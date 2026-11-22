"""STRATZ as a backup for when OpenDota is down.

Two things can come from STRATZ: players' match lists (the IDs behind /last,
/ourlast, /week) and whole matches. A STRATZ match is converted into the same
shape OpenDota returns (see to_opendota), so the scorer doesn't care where it
came from. It's marked source="stratz" and only cached briefly, so the
OpenDota version replaces it once OpenDota is back.

Needs STRATZ_API_KEY (free at stratz.com/api, log in with Steam). One key can
only be used from 2 IP addresses per 15 minutes, so don't share it.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

log = logging.getLogger(__name__)

URL = "https://api.stratz.com/graphql"

MATCHES_QUERY = """
query($id: Long!, $take: Int!, $since: Long) {
  player(steamAccountId: $id) {
    matches(request: {take: $take, startDateTime: $since}) { id startDateTime }
  }
}
"""


MATCH_QUERY = """
query($id: Long!) {
  match(id: $id) {
    id didRadiantWin durationSeconds startDateTime gameMode lobbyType parsedDateTime radiantKills direKills
    players {
      playerSlot isRadiant steamAccountId steamAccount { name } heroId
      kills deaths assists numLastHits numDenies goldPerMinute experiencePerMinute networth
      heroDamage towerDamage heroHealing lane
      abilities { abilityId time }
      stats {
        lastHitsPerMinute campStack deathEvents { timeDead } wards { type } wardDestruction { isWard }
      }
    }
  }
}
"""

# STRATZ enums list Dota's values in id order, so the index is OpenDota's number.
GAME_MODES = [
    "NONE", "ALL_PICK", "CAPTAINS_MODE", "RANDOM_DRAFT", "SINGLE_DRAFT", "ALL_RANDOM", "INTRO", "THE_DIRETIDE",
    "REVERSE_CAPTAINS_MODE", "THE_GREEVILING", "TUTORIAL", "MID_ONLY", "LEAST_PLAYED", "NEW_PLAYER_POOL",
    "COMPENDIUM_MATCHMAKING", "CUSTOM", "CAPTAINS_DRAFT", "BALANCED_DRAFT", "ABILITY_DRAFT", "EVENT",
    "ALL_RANDOM_DEATH_MATCH", "SOLO_MID", "ALL_PICK_RANKED", "TURBO", "MUTATION",
]  # fmt: skip
LOBBY_TYPES = ["UNRANKED", "PRACTICE", "TOURNAMENT", "TUTORIAL", "COOP_VS_BOTS", "TEAM_MATCH", "SOLO_QUEUE", "RANKED"]
LANES = {"SAFE_LANE": 1, "MID_LANE": 2, "OFF_LANE": 3, "JUNGLE": 4}


def _index(names: list[str], value: str | None) -> int | None:
    return names.index(value) if value in names else None


def to_opendota(m: dict) -> dict:
    """A STRATZ match in the shape of OpenDota's /matches (as slimmed by slim_match)."""
    parsed = bool(m.get("parsedDateTime"))
    out = {
        "match_id": m["id"],
        "duration": m.get("durationSeconds") or 0,
        "radiant_win": m.get("didRadiantWin"),
        "radiant_score": sum(m.get("radiantKills") or []),
        "dire_score": sum(m.get("direKills") or []),
        "start_time": m.get("startDateTime") or 0,
        "game_mode": _index(GAME_MODES, m.get("gameMode")),
        "lobby_type": _index(LOBBY_TYPES, m.get("lobbyType")),
        "version": None,
        "od_data": {"has_parsed": parsed},
        "source": "stratz",
        "players": [],
    }
    for p in m.get("players") or []:
        q = {
            "player_slot": p.get("playerSlot"),
            "isRadiant": p.get("isRadiant"),
            "account_id": p.get("steamAccountId"),
            "personaname": (p.get("steamAccount") or {}).get("name"),
            "hero_id": p.get("heroId"),
            "kills": p.get("kills"),
            "deaths": p.get("deaths"),
            "assists": p.get("assists"),
            "last_hits": p.get("numLastHits"),
            "denies": p.get("numDenies"),
            "gold_per_min": p.get("goldPerMinute"),
            "xp_per_min": p.get("experiencePerMinute"),
            "net_worth": p.get("networth"),
            "hero_damage": p.get("heroDamage"),
            "tower_damage": p.get("towerDamage"),
            "hero_healing": p.get("heroHealing"),
        }
        abilities = sorted(p.get("abilities") or [], key=lambda a: a.get("time") or 0)
        if abilities:
            q["ability_upgrades_arr"] = [a["abilityId"] for a in abilities]
        st = p.get("stats") or {}
        if parsed and st:
            lane = p.get("lane")
            q["lane_role"] = LANES.get(lane)
            q["is_roaming"] = lane == "ROAMING"
            # STRATZ gives last hits per minute; OpenDota's lh_t is the running total
            lh_t, total = [0], 0
            for n in (st.get("lastHitsPerMinute") or [])[:10]:
                total += n or 0
                lh_t.append(total)
            if len(lh_t) > 1:
                q["lh_t"] = lh_t
            wards = st.get("wards") or []
            q["obs_placed"] = sum(1 for w in wards if w.get("type") == 0)
            q["sen_placed"] = sum(1 for w in wards if w.get("type") == 1)
            killed = st.get("wardDestruction") or []
            q["observer_kills"] = sum(1 for w in killed if w.get("isWard"))
            q["sentry_kills"] = sum(1 for w in killed if not w.get("isWard"))
            q["life_state_dead"] = sum((d.get("timeDead") or 0) for d in st.get("deathEvents") or [])
            # No "stuns": STRATZ's stunDuration doesn't match OpenDota's stuns
            # (off by 1.5-3x, sometimes 0 for a stunner), so the stun part of
            # the score is skipped for STRATZ matches rather than scored wrong.
            stacks = st.get("campStack") or []
            if stacks:
                q["camps_stacked"] = stacks[-1]
        out["players"].append(q)
    return out


class StratzError(Exception):
    pass


class Stratz:
    def __init__(self, token: str):
        # Always connect over IPv4: on a dual-stack server, IPv4 + IPv6 would
        # count as 2 addresses and use up STRATZ's 2-IP limit on their own.
        # (Behind an HTTPS proxy the proxy decides, so leave it alone.)
        proxied = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
        self.sem = asyncio.Semaphore(4)
        self.client = httpx.AsyncClient(
            transport=None if proxied else httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
            timeout=httpx.Timeout(20.0, connect=10.0),
            # STRATZ asks API users to send this exact User-Agent.
            headers={"Authorization": f"Bearer {token}", "User-Agent": "STRATZ_API"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def player_matches(self, account_id: int, take: int = 20, since: int | None = None) -> list[dict]:
        """Newest first, as OpenDota-style rows: [{"match_id", "start_time"}]."""
        data = await self._query(MATCHES_QUERY, {"id": account_id, "take": take, "since": since})
        player = data.get("player") or {}
        rows = [{"match_id": m["id"], "start_time": m.get("startDateTime") or 0} for m in player.get("matches") or []]
        return sorted(rows, key=lambda m: -m["start_time"])

    async def match(self, match_id: int) -> dict | None:
        """The match in OpenDota's shape, or None if STRATZ doesn't have it (yet)."""
        m = (await self._query(MATCH_QUERY, {"id": match_id})).get("match")
        if not m or not m.get("durationSeconds") or not m.get("players"):
            return None
        return to_opendota(m)

    async def _query(self, query: str, variables: dict) -> dict:
        try:
            async with self.sem:
                r = await self.client.post(URL, json={"query": query, "variables": variables})
        except httpx.HTTPError as e:
            raise StratzError(f"STRATZ is not responding: {e}") from e
        if r.status_code != 200:
            raise StratzError(f"STRATZ returned {r.status_code}: {r.text[:200]}")
        try:
            body = r.json()
        except ValueError:
            raise StratzError(f"STRATZ returned non-JSON: {r.text[:200]}") from None
        data = body.get("data") or {}
        if body.get("errors") and not any(data.values()):
            raise StratzError(f"STRATZ error: {body['errors'][0].get('message', body['errors'][0])}")
        return data
