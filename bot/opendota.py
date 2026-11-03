"""Thin async OpenDota client with a SQLite cache.

Only OpenDota is used (no STRATZ): the basic match data is available as soon
as Valve publishes the match, so the bot can answer right away. Stats that
need the replay (wards, stuns, lanes) show up after OpenDota parses it; the
"Refresh" button re-checks.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .benchmarks import fill_benchmarks, needs_benchmarks
from .db import DB
from .scoring import is_parsed

log = logging.getLogger(__name__)

BASE = "https://api.opendota.com/api"

MATCH_KEYS = (
    "match_id", "duration", "radiant_win", "radiant_score", "dire_score",
    "start_time", "game_mode", "lobby_type", "version", "od_data",
)  # fmt: skip
PLAYER_KEYS = (
    "player_slot", "isRadiant", "account_id", "personaname", "hero_id",
    "kills", "deaths", "assists", "last_hits", "denies", "gold_per_min", "xp_per_min",
    "net_worth", "total_gold", "hero_damage", "tower_damage", "hero_healing",
    "lane_role", "is_roaming", "obs_placed", "sen_placed", "observer_kills", "sentry_kills",
    "stuns", "camps_stacked", "benchmarks", "rank_tier",
)  # fmt: skip

UNPARSED_TTL = 10 * 60  # re-fetch unparsed matches after 10 min
RECENT_TTL = 3 * 60
PROFILE_TTL = 6 * 3600
HEROES_TTL = 7 * 86400
BENCH_TTL = 7 * 86400


class OpenDotaError(Exception):
    pass


def slim_match(m: dict) -> dict:
    """Parsed matches are ~1 MB of logs we don't need. Keep the bits we use."""
    out = {k: m.get(k) for k in MATCH_KEYS}
    out["players"] = [{k: p.get(k) for k in PLAYER_KEYS if k in p} for p in m.get("players") or []]
    return out


class OpenDota:
    def __init__(self, db: DB, api_key: str | None = None, concurrency: int = 4):
        self.db = db
        self.api_key = api_key
        self.sem = asyncio.Semaphore(concurrency)
        self.client = httpx.AsyncClient(base_url=BASE, timeout=25.0, headers={"User-Agent": "dota-verdict-bot"})
        self._heroes: dict[int, str] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **params) -> dict | list | None:
        if self.api_key:
            params["api_key"] = self.api_key
        for attempt in range(3):
            async with self.sem:
                try:
                    r = await self.client.request(method, path, params=params)
                except httpx.HTTPError as e:
                    log.warning("OpenDota %s %s failed: %s", method, path, e)
                    if attempt == 2:
                        raise OpenDotaError("OpenDota is not responding, try again in a minute") from e
                    await asyncio.sleep(1 + attempt)
                    continue
            if r.status_code == 429:
                await asyncio.sleep(2 + 3 * attempt)
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                await asyncio.sleep(1 + attempt)
                continue
            if r.status_code >= 400:
                raise OpenDotaError(f"OpenDota returned {r.status_code}")
            return r.json()
        raise OpenDotaError("OpenDota is rate-limiting or down, try again in a minute")

    # -- matches -------------------------------------------------------------

    async def match(self, match_id: int, force: bool = False) -> dict | None:
        key = f"match:{match_id}"
        if not force:
            cached = await self.db.cache_get(key, UNPARSED_TTL)
            if cached is not None:
                return cached
        raw = await self._request("GET", f"/matches/{match_id}")
        if not raw or not raw.get("players"):
            return None
        m = slim_match(raw)
        await self._ensure_benchmarks(m)
        parsed = is_parsed(m)
        await self.db.cache_put(key, m, permanent=parsed)
        return m

    async def request_parse(self, match_id: int) -> None:
        try:
            await self._request("POST", f"/request/{match_id}")
        except OpenDotaError as e:
            log.info("parse request for %s failed: %s", match_id, e)

    async def _ensure_benchmarks(self, m: dict) -> None:
        missing = needs_benchmarks(m)
        if not missing:
            return
        by_hero = {}
        for hero_id in missing:
            b = await self.hero_benchmarks(hero_id)
            if b:
                by_hero[hero_id] = b
        fill_benchmarks(m, by_hero)

    async def hero_benchmarks(self, hero_id: int) -> dict | None:
        key = f"bench:{hero_id}"
        cached = await self.db.cache_get(key, BENCH_TTL)
        if cached is not None:
            return cached
        try:
            data = await self._request("GET", "/benchmarks", hero_id=hero_id)
        except OpenDotaError:
            return None
        if data:
            await self.db.cache_put(key, data)
        return data

    # -- players -------------------------------------------------------------

    async def recent_matches(self, account_id: int, force: bool = False) -> list[dict]:
        """Last ~20 matches, newest first. Cheap: one request."""
        key = f"recent:{account_id}"
        if not force:
            cached = await self.db.cache_get(key, RECENT_TTL)
            if cached is not None:
                return cached
        data = await self._request("GET", f"/players/{account_id}/recentMatches") or []
        await self.db.cache_put(key, data)
        return data

    async def profile(self, account_id: int) -> dict | None:
        key = f"profile:{account_id}"
        cached = await self.db.cache_get(key, PROFILE_TTL)
        if cached is not None:
            return cached
        data = await self._request("GET", f"/players/{account_id}")
        if data and data.get("profile"):
            await self.db.cache_put(key, data)
            return data
        return None

    async def refresh_player(self, account_id: int) -> None:
        """Ask OpenDota to pull a player's newest matches from Valve."""
        try:
            await self._request("POST", f"/players/{account_id}/refresh")
        except OpenDotaError:
            pass

    # -- heroes --------------------------------------------------------------

    async def heroes(self) -> dict[int, str]:
        if self._heroes:
            return self._heroes
        data = await self.db.cache_get("heroes", HEROES_TTL)
        if data is None:
            try:
                data = await self._request("GET", "/heroes")
            except OpenDotaError:
                data = None
            if data:
                await self.db.cache_put("heroes", data)
        if data:
            self._heroes = {int(h["id"]): h["localized_name"] for h in data}
        return self._heroes

    async def hero_name(self, hero_id: int) -> str:
        return (await self.heroes()).get(hero_id, f"Hero #{hero_id}")
