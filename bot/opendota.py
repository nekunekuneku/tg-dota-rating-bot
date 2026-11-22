"""Thin async OpenDota client with a SQLite cache.

Match data comes from OpenDota: the basic data is available as soon as Valve
publishes the match, so the bot can answer right away. Stats that need the
replay (wards, stuns, lanes) show up after OpenDota parses it; the "Refresh"
button re-checks.

With a STRATZ key, STRATZ is the backup: for players' match lists when
OpenDota's fail, and for whole matches when OpenDota is down. After a
timeout or connection error, OpenDota is skipped for DOWN_FOR seconds so
every request doesn't wait out the timeout first. See stratz.py.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .benchmarks import curves_from_matches, fill_benchmarks, needs_benchmarks
from .db import DB
from .scoring import is_parsed
from .stratz import Stratz, StratzError

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
    "stuns", "camps_stacked", "benchmarks", "rank_tier", "ability_upgrades_arr", "life_state_dead",
)  # fmt: skip

UNPARSED_TTL = 10 * 60  # re-fetch fresh unparsed matches after 10 min (the parse may land)
OLD_UNPARSED_TTL = 24 * 3600  # ...but a game that ended hours ago and still isn't parsed won't change soon
OLD_AFTER = 6 * 3600
RECENT_TTL = 3 * 60
PROFILE_TTL = 6 * 3600
HEROES_TTL = 7 * 86400
BENCH_TTL = 7 * 86400
STRATZ_MATCH_TTL = 30 * 60  # STRATZ copies are temporary: OpenDota's replaces them when it's back
DOWN_FOR = 2 * 60  # after OpenDota times out, go straight to STRATZ for this long


class OpenDotaError(Exception):
    pass


def slim_match(m: dict) -> dict:
    """Parsed matches are ~1 MB of logs we don't need. Keep the bits we use."""
    out = {k: m.get(k) for k in MATCH_KEYS}
    out["players"] = []
    for p in m.get("players") or []:
        slim = {k: p.get(k) for k in PLAYER_KEYS if k in p}
        if p.get("lh_t"):
            slim["lh_t"] = p["lh_t"][:11]  # last hits per minute; only the first 10 min matter
        out["players"].append(slim)
    return out


def unparsed_ttl(m: dict) -> int:
    if m.get("source") == "stratz":
        return STRATZ_MATCH_TTL
    ended = (m.get("start_time") or 0) + (m.get("duration") or 0)
    return OLD_UNPARSED_TTL if time.time() - ended > OLD_AFTER else UNPARSED_TTL


def has_real_data(m: dict) -> bool:
    players = m.get("players") or []
    return bool(m.get("duration")) and len(players) >= 2 and any(p.get("hero_id") for p in players)


class OpenDota:
    def __init__(self, db: DB, api_key: str | None = None, concurrency: int = 6, backup: Stratz | None = None):
        self.db = db
        self.api_key = api_key
        self.backup = backup  # STRATZ, for when OpenDota fails
        self._down_until = 0.0  # monotonic time; while in the future, skip OpenDota (only with a backup)
        self._curves: dict | None = None  # benchmark curves rebuilt from cached matches
        self._curves_at = 0.0
        self.sem = asyncio.Semaphore(concurrency)
        self.client = httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(25.0, connect=10.0), headers={"User-Agent": "dota-verdict-bot"})
        self._heroes: dict[int, str] = {}
        self._abilities: dict[int, str] = {}
        self._inflight: dict[tuple[int, bool], asyncio.Future] = {}

    async def close(self) -> None:
        await self.client.aclose()
        if self.backup:
            await self.backup.close()

    def opendota_down(self) -> bool:
        return bool(self.backup) and time.monotonic() < self._down_until

    async def _request(self, method: str, path: str, tries: int = 3, **params) -> dict | list | None:
        if self.opendota_down():
            raise OpenDotaError("OpenDota is down")
        if self.api_key:
            params["api_key"] = self.api_key
        for attempt in range(tries):
            async with self.sem:
                try:
                    r = await self.client.request(method, path, params=params)
                except httpx.HTTPError as e:
                    log.warning("OpenDota %s %s failed: %s", method, path, e)
                    if self.backup:
                        # Not answering at all: don't retry, and skip it for a while.
                        self._down_until = time.monotonic() + DOWN_FOR
                        raise OpenDotaError("OpenDota is not responding") from e
                    if attempt == tries - 1:
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
        """One fetch per match at a time: friends in the same game share it."""
        k = (match_id, force)
        fut = self._inflight.get(k)
        if fut is None:
            fut = asyncio.ensure_future(self._match(match_id, force))
            self._inflight[k] = fut
            fut.add_done_callback(lambda _: self._inflight.pop(k, None))
        return await asyncio.shield(fut)

    async def _match(self, match_id: int, force: bool) -> dict | None:
        key = f"match4:{match_id}"
        if not force:
            entry = await self.db.cache_entry(key)
            if entry is not None:
                data, fetched_at, permanent = entry
                if permanent or time.time() - fetched_at < unparsed_ttl(data):
                    return data
        try:
            raw = await self._request("GET", f"/matches/{match_id}")
        except OpenDotaError as e:
            if not self.backup:
                raise
            return await self._match_from_stratz(match_id, key, e)
        if not raw or not has_real_data(raw):
            # Brand-new matches come back as a stub (no heroes, 0 duration) until
            # OpenDota pulls them from Valve. Don't cache or rate those - but
            # STRATZ may already have it.
            return await self._match_from_stratz(match_id, key, None) if self.backup else None
        m = slim_match(raw)
        await self._ensure_benchmarks(m)
        parsed = is_parsed(m)
        await self.db.cache_put(key, m, permanent=parsed)
        return m

    async def _match_from_stratz(self, match_id: int, key: str, error: OpenDotaError | None) -> dict | None:
        if error:
            log.warning("OpenDota match %s failed (%s), using STRATZ", match_id, error)
        try:
            m = await self.backup.match(match_id)
        except StratzError as e:
            log.warning("STRATZ backup failed too: %s", e)
            if error:
                raise error from e
            return None
        if m is None:
            return None
        await self._ensure_benchmarks(m)
        await self.db.cache_put(key, m, permanent=False)
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
        still = missing - by_hero.keys()
        if still:
            # OpenDota is down (STRATZ matches have no benchmarks): use curves
            # rebuilt from the matches already in the cache.
            curves = await self._cached_curves()
            base = (curves.get(0) or {}).get("result") or {}
            for hero_id in still:
                result = {**base, **((curves.get(hero_id) or {}).get("result") or {})}
                if result:
                    by_hero[hero_id] = {"result": result}
        fill_benchmarks(m, by_hero)

    async def _cached_curves(self) -> dict:
        if self._curves is None or time.monotonic() - self._curves_at > 3600:
            self._curves = curves_from_matches(await self.db.cache_values("match4:"))
            self._curves_at = time.monotonic()
        return self._curves

    async def hero_benchmarks(self, hero_id: int) -> dict | None:
        key = f"bench:{hero_id}"
        cached = await self.db.cache_get(key, BENCH_TTL)
        if cached is not None:
            return cached
        try:
            data = await self._request("GET", "/benchmarks", hero_id=hero_id)
        except OpenDotaError:
            return await self.db.cache_get(key, None)  # an old copy beats none
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
        data = await self._player_list(
            account_id, f"/players/{account_id}/recentMatches", {}, lambda: self.backup.player_matches(account_id, 20)
        )
        await self.db.cache_put(key, data)
        return data

    async def matches_since(self, account_id: int, days: int, limit: int = 25) -> list[dict]:
        """This player's matches from the last `days` days, newest first."""
        since = int(time.time()) - days * 86400
        data = await self._player_list(
            account_id,
            f"/players/{account_id}/matches",
            {"date": days, "limit": limit},
            lambda: self.backup.player_matches(account_id, limit, since),
        )
        return [r for r in data if (r.get("start_time") or 0) >= since][:limit]

    async def _player_list(self, account_id: int, path: str, params: dict, from_backup) -> list[dict]:
        """OpenDota's list, or STRATZ's when OpenDota fails and a STRATZ key is set."""
        try:
            # With a backup, don't spend long retrying OpenDota.
            return await self._request("GET", path, tries=2 if self.backup else 3, **params) or []
        except OpenDotaError as e:
            if not self.backup:
                raise
            log.warning("OpenDota %s failed (%s), using STRATZ", path, e)
            try:
                return await from_backup()
            except StratzError as e2:
                log.warning("STRATZ backup failed too: %s", e2)
                raise e from e2

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
                data = await self.db.cache_get("heroes", None)  # an old copy beats none
            else:
                if data:
                    await self.db.cache_put("heroes", data)
        if data:
            self._heroes = {int(h["id"]): h["localized_name"] for h in data}
        return self._heroes

    async def ability_names(self) -> dict[int, str]:
        """{ability id: display name}, without talents and AD gold bonuses.

        Built from two OpenDota constants files (~1.5 MB together), so it's
        only fetched the first time an Ability Draft game is shown, then cached.
        """
        if self._abilities:
            return self._abilities
        data = await self.db.cache_get("ability_names", HEROES_TTL)
        if data is None:
            try:
                ids = await self._request("GET", "/constants/ability_ids") or {}
                info = await self._request("GET", "/constants/abilities") or {}
            except OpenDotaError:
                old = await self.db.cache_get("ability_names", None)  # an old copy beats none
                return {int(k): v for k, v in old.items()} if old else {}
            data = {}
            for aid, name in ids.items():
                if "special_bonus" in name or not aid.isdigit():
                    continue
                dname = (info.get(name) or {}).get("dname")
                if dname:
                    data[aid] = dname
            if data:
                await self.db.cache_put("ability_names", data)
        self._abilities = {int(k): v for k, v in data.items()}
        return self._abilities

    async def hero_name(self, hero_id: int) -> str:
        return (await self.heroes()).get(hero_id, f"Hero #{hero_id}")
