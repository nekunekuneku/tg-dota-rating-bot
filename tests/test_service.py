import asyncio
import copy

import httpx

from bot.db import DB
from bot.opendota import BASE, OpenDota
from bot.service import Friend, Service, find_friend
from tests.conftest import BASE_MATCH


def run(coro):
    return asyncio.run(coro)


def make_service(tmp_path, handler):
    async def build():
        db = DB(str(tmp_path / "t.sqlite3"))
        await db.open()
        od = OpenDota(db)
        od.client = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler))
        return db, od, Service(db, od)

    return build()


def test_last_match_flow_and_cache(tmp_path):
    calls = []
    raw = copy.deepcopy(BASE_MATCH)
    raw["players"][0]["huge_log"] = ["x"] * 1000  # should be stripped before caching

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.path.endswith("/recentMatches"):
            return httpx.Response(200, json=[{"match_id": 1, "start_time": 1}, {"match_id": 8000000001, "start_time": 2}])
        if req.url.path.endswith("/matches/8000000001"):
            return httpx.Response(200, json=raw)
        return httpx.Response(404)

    async def go():
        db, od, svc = await make_service(tmp_path, handler)
        mid = await svc.last_match_id(1000)
        assert mid == 8000000001
        m = await svc.rate(mid)
        assert m.player_by_account(1000).verdict == "Threw?"

        cached = await db.cache_get(f"match4:{mid}", None)
        assert "huge_log" not in cached["players"][0]

        await svc.rate(mid)  # second call served from cache
        assert calls.count("/api/matches/8000000001") == 1
        await od.close()
        await db.close()

    run(go())


def test_rate_limit_retry(tmp_path):
    n = {"hits": 0}

    def handler(req):
        n["hits"] += 1
        if n["hits"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=[{"id": 1, "localized_name": "Anti-Mage"}])

    async def go():
        db, od, _ = await make_service(tmp_path, handler)
        import bot.opendota as mod

        real_sleep = mod.asyncio.sleep
        mod.asyncio.sleep = lambda s: real_sleep(0)
        try:
            assert await od.hero_name(1) == "Anti-Mage"
        finally:
            mod.asyncio.sleep = real_sleep
        await od.close()
        await db.close()

    run(go())


def test_stub_match_is_not_rated_or_cached(tmp_path):
    stub = {"match_id": 5, "duration": 0, "players": [{"player_slot": s, "hero_id": 0} for s in (0, 1, 2, 3, 4, 128, 129, 130, 131, 132)]}

    async def go():
        db, od, svc = await make_service(tmp_path, lambda req: httpx.Response(200, json=stub))
        assert await svc.rate(5) is None
        assert await db.cache_get("match4:5", None) is None
        await od.close()
        await db.close()

    run(go())


ROSTER = [Friend(55775392, "Nick"), Friend(129229916, "GS Smurf"), Friend(103826341, "GS"), Friend(72621204, "QG")]


def test_find_friend():
    assert find_friend(ROSTER, "nick").name == "Nick"
    assert find_friend(ROSTER, "GS").name == "GS"  # exact beats prefix
    assert find_friend(ROSTER, "smurf").name == "GS Smurf"
    assert find_friend(ROSTER, "g") == [ROSTER[1], ROSTER[2]]  # ambiguous
    assert find_friend(ROSTER, "72621204").name == "QG"
    assert find_friend(ROSTER, "zzz") is None


def test_group_matches_needs_two_friends(tmp_path):
    recent = {
        55775392: [{"match_id": 3, "start_time": 300}, {"match_id": 2, "start_time": 200}, {"match_id": 1, "start_time": 100}],
        129229916: [{"match_id": 2, "start_time": 200}],
        103826341: [{"match_id": 1, "start_time": 100}, {"match_id": 9, "start_time": 900}],
        72621204: [{"match_id": 1, "start_time": 100}],
    }

    def handler(req):
        acc = int(req.url.path.split("/")[3])
        return httpx.Response(200, json=recent[acc])

    async def go():
        db, od, svc = await make_service(tmp_path, handler)
        svc.roster = ROSTER
        groups = await svc.group_matches(5)
        assert [g.match_id for g in groups] == [2, 1]  # 3 and 9 were solo games
        assert {f.name for f in groups[1].friends} == {"Nick", "GS", "QG"}
        await od.close()
        await db.close()

    run(go())


def test_parse_last_args():
    from bot.handlers import parse_args

    assert parse_args(None) == ("", 1)
    assert parse_args("nick") == ("nick", 1)
    assert parse_args("nick 5") == ("nick", 5)
    assert parse_args("5 gs smurf") == ("gs smurf", 5)
    assert parse_args("99") == ("", 20)  # capped at 20
    assert parse_args("55775392") == ("55775392", 1)  # big numbers are Friend IDs


def test_period_fetches_shared_games_once_and_reports_progress(tmp_path):
    import time

    calls = []
    raw = copy.deepcopy(BASE_MATCH)
    now = int(time.time())

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.path.endswith("/matches") and "/players/" in req.url.path:
            return httpx.Response(200, json=[{"match_id": 8000000001, "start_time": now - 3600}])
        if req.url.path.endswith("/matches/8000000001"):
            return httpx.Response(200, json=raw)
        return httpx.Response(404)

    async def go():
        db, od, svc = await make_service(tmp_path, handler)
        svc.roster = [Friend(1000, "Danil"), Friend(1129, "Ruslan")]  # same game, opposite teams
        seen = []

        async def progress(done, total):
            seen.append((done, total))

        rows = await svc.period(7, progress)
        await od.close()
        await db.close()
        return rows, seen

    rows, seen = run(go())
    assert calls.count("/api/matches/8000000001") == 1
    assert [len(r["games"]) for r in rows] == [1, 1]
    assert seen == [(1, 1)]


def test_old_unparsed_match_stays_cached(tmp_path):
    import time

    calls = []
    raw = copy.deepcopy(BASE_MATCH)
    raw["start_time"] = int(time.time()) - 2 * 86400  # ended two days ago, never parsed

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        return httpx.Response(200, json=raw)

    async def go():
        db, od, svc = await make_service(tmp_path, handler)
        await od.match(8000000001)
        # pretend it was cached an hour ago: past the 10-minute window for fresh games
        await db.conn.execute("UPDATE cache SET fetched_at = fetched_at - 3600")
        await db.conn.commit()
        await od.match(8000000001)
        await od.close()
        await db.close()

    run(go())
    assert calls.count("/api/matches/8000000001") == 1
