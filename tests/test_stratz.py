"""STRATZ as a full backup: converting its matches, and falling back to it."""

import asyncio
import copy
import json
import time

import httpx

from bot import render
from bot.db import DB
from bot.opendota import BASE, STRATZ_MATCH_TTL, OpenDota
from bot.scoring import rate_match
from bot.stratz import Stratz, to_opendota
from tests.conftest import BASE_MATCH

LANE_NAMES = {1: "SAFE_LANE", 2: "MID_LANE", 3: "OFF_LANE", 4: "JUNGLE"}


def as_stratz(od: dict, parsed: bool = True) -> dict:
    """The test match the way STRATZ's GraphQL would return it."""
    players = []
    for p in od["players"]:
        q = {
            "playerSlot": p["player_slot"],
            "isRadiant": p["player_slot"] < 128,
            "steamAccountId": p["account_id"],
            "steamAccount": {"name": f"p{p['player_slot']}"},
            "heroId": p["hero_id"],
            "kills": p["kills"],
            "deaths": p["deaths"],
            "assists": p["assists"],
            "numLastHits": p["last_hits"],
            "numDenies": 3,
            "goldPerMinute": p["gold_per_min"],
            "experiencePerMinute": p["xp_per_min"],
            "networth": p["net_worth"],
            "heroDamage": p["hero_damage"],
            "towerDamage": p["tower_damage"],
            "heroHealing": p["hero_healing"],
            "lane": LANE_NAMES.get(p.get("lane_role"), "ROAMING"),
            "abilities": [{"abilityId": 5003, "time": 200}, {"abilityId": 5001, "time": 100}],
            "stats": None,
        }
        if parsed:
            q["stats"] = {
                "lastHitsPerMinute": [3] * 40,
                "campStack": [0, 1, 1, 2],
                "deathEvents": [{"timeDead": 30} for _ in range(p["deaths"])],
                "wards": [{"type": 0}, {"type": 0}, {"type": 1}],
                "wardDestruction": [{"isWard": True}, {"isWard": False}],
            }
        players.append(q)
    return {
        "id": od["match_id"],
        "didRadiantWin": od["radiant_win"],
        "durationSeconds": od["duration"],
        "startDateTime": od["start_time"],
        "gameMode": "ALL_PICK_RANKED",
        "lobbyType": "RANKED",
        "parsedDateTime": 1 if parsed else None,
        "radiantKills": [od["radiant_score"]],
        "direKills": [od["dire_score"]],
        "players": players,
    }


def test_to_opendota_maps_every_field():
    m = to_opendota(as_stratz(BASE_MATCH))
    assert m["source"] == "stratz" and m["od_data"]["has_parsed"]
    assert (m["game_mode"], m["lobby_type"]) == (22, 7)
    assert (m["radiant_score"], m["dire_score"], m["radiant_win"]) == (21, 48, False)
    p = m["players"][0]
    assert p["account_id"] == 1000 and p["hero_id"] == 11 and p["kills"] == 2 and p["deaths"] == 14
    assert p["obs_placed"] == 2 and p["sen_placed"] == 1
    assert p["observer_kills"] + p["sentry_kills"] == 2
    assert p["life_state_dead"] == 14 * 30
    assert "stuns" not in p  # STRATZ's stun numbers don't match OpenDota's
    assert p["lh_t"][:3] == [0, 3, 6] and len(p["lh_t"]) == 11  # running total, first 10 minutes
    assert p["camps_stacked"] == 2
    assert p["ability_upgrades_arr"] == [5001, 5003]  # in skill order


def test_unparsed_stratz_match_has_no_replay_stats():
    m = to_opendota(as_stratz(BASE_MATCH, parsed=False))
    assert not m["od_data"]["has_parsed"]
    p = m["players"][0]
    assert "obs_placed" not in p and "stuns" not in p and "lane_role" not in p and "life_state_dead" not in p


def test_stratz_match_scores_like_the_opendota_one():
    """Same game from both sources -> same players, same verdicts."""
    od = rate_match(copy.deepcopy(BASE_MATCH))
    sm = to_opendota(as_stratz(BASE_MATCH, parsed=False))
    for a, b in zip(sm["players"], BASE_MATCH["players"]):
        a["benchmarks"] = b["benchmarks"]  # filled from OpenDota's cached benchmarks in real use
    st = rate_match(sm)
    assert st.source == "stratz"
    assert [(p.account_id, p.verdict) for p in st.players] == [(p.account_id, p.verdict) for p in od.players]
    assert "came from STRATZ" in render.summary(st, {}, {1000: "Danil"})


# -- falling back ------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


async def setup(tmp_path, od_handler, stratz_match):
    db = DB(str(tmp_path / "t.sqlite3"))
    await db.open()
    stratz_calls = []

    def stratz_handler(req):
        body = json.loads(req.content)
        stratz_calls.append(body["variables"])
        return httpx.Response(200, json={"data": {"match": stratz_match}})

    backup = Stratz("test-token")
    backup.client = httpx.AsyncClient(transport=httpx.MockTransport(stratz_handler))
    od = OpenDota(db, backup=backup)
    od.client = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(od_handler))
    return db, od, stratz_calls


def test_opendota_down_uses_stratz_and_skips_opendota_for_a_while(tmp_path):
    od_calls = []

    def od_handler(req):
        od_calls.append(req.url.path)
        raise httpx.ConnectTimeout("no answer", request=req)

    async def go():
        db, od, stratz_calls = await setup(tmp_path, od_handler, as_stratz(BASE_MATCH))
        m = await od.match(8000000001)
        assert m and m["source"] == "stratz"
        assert len(od_calls) == 1  # one timeout, no retries
        # The next fresh fetch doesn't wait on OpenDota again
        await od.match(8000000001, force=True)
        assert len(od_calls) == 1 and len(stratz_calls) == 2
        # Cached, but only briefly: OpenDota's copy should replace it later
        entry = await db.cache_entry("match4:8000000001")
        assert entry is not None and entry[2] is False
        await od.close()
        await db.close()

    run(go())


def test_stratz_copy_expires_so_opendota_replaces_it(tmp_path):
    def od_handler(req):
        return httpx.Response(200, json=BASE_MATCH)  # OpenDota is back

    async def go():
        db, od, _ = await setup(tmp_path, od_handler, as_stratz(BASE_MATCH))
        old = to_opendota(as_stratz(BASE_MATCH))
        await db.cache_put("match4:8000000001", old, permanent=False)
        await db.conn.execute("UPDATE cache SET fetched_at = ?", (int(time.time()) - STRATZ_MATCH_TTL - 1,))
        await db.conn.commit()
        m = await od.match(8000000001)
        assert m.get("source") != "stratz"
        await od.close()
        await db.close()

    run(go())


def test_brand_new_match_comes_from_stratz_when_opendota_has_a_stub(tmp_path):
    stub = {"match_id": 8000000001, "duration": 0, "players": []}

    def od_handler(req):
        return httpx.Response(200, json=stub)

    async def go():
        db, od, _ = await setup(tmp_path, od_handler, as_stratz(BASE_MATCH))
        m = await od.match(8000000001)
        await od.close()
        await db.close()
        return m

    assert run(go())["source"] == "stratz"


def test_old_cached_benchmarks_and_heroes_are_used_when_opendota_is_down(tmp_path):
    def od_handler(req):
        raise httpx.ConnectTimeout("no answer", request=req)

    async def go():
        db, od, _ = await setup(tmp_path, od_handler, None)
        await db.cache_put("bench:11", {"result": {}}, permanent=False)
        await db.cache_put("heroes", [{"id": 11, "localized_name": "Shadow Fiend"}], permanent=False)
        await db.conn.execute("UPDATE cache SET fetched_at = 0")  # very old
        await db.conn.commit()
        assert await od.hero_benchmarks(11) == {"result": {}}
        assert await od.hero_name(11) == "Shadow Fiend"
        await od.close()
        await db.close()

    run(go())
