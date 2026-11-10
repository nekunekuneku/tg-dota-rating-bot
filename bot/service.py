"""Glue between OpenDota, the friend list and the scorer."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .db import DB
from .opendota import OpenDota, OpenDotaError
from .scoring import MatchRating, PlayerRating, rate_match

STEAM64_BASE = 76561197960265728
REFRESH_EVERY = 5 * 60


@dataclass(frozen=True)
class Friend:
    account_id: int
    name: str


def load_roster(path: str | Path) -> list[Friend]:
    """friends.json: [{"id": 55775392, "name": "Nick"}, ...]"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Friend(int(f["id"]), str(f["name"])) for f in data]


def find_friend(roster: list[Friend], query: str) -> Friend | list[Friend] | None:
    """Exact name, then unique prefix, then unique substring. A list means ambiguous."""
    q = query.strip().lstrip("@").lower()
    if not q:
        return None
    if q.isdigit():
        for f in roster:
            if f.account_id == int(q):
                return f
    for match in (
        lambda n: n == q,
        lambda n: n.startswith(q),
        lambda n: q in n,
    ):
        hits = [f for f in roster if match(f.name.lower())]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return hits
    return None


def parse_account_id(text: str) -> int | None:
    """Accept a Friend ID, a SteamID64, or a Dotabuff/OpenDota/STRATZ/Steam profile URL."""
    t = text.strip()
    m = re.search(r"(?:dotabuff\.com|opendota\.com|stratz\.com)/players/(\d+)", t)
    if not m:
        m = re.search(r"steamcommunity\.com/profiles/(\d+)", t)
    if m:
        n = int(m.group(1))
    elif t.isdigit():
        n = int(t)
    else:
        return None
    if n >= STEAM64_BASE:
        n -= STEAM64_BASE
    return n if 0 < n < 2**32 else None


def parse_match_id(text: str) -> int | None:
    m = re.search(r"(\d{6,12})", text or "")
    return int(m.group(1)) if m else None


@dataclass
class GroupMatch:
    match_id: int
    start_time: int
    friends: list[Friend]


class Service:
    def __init__(self, db: DB, od: OpenDota, roster: list[Friend] | None = None):
        self.db = db
        self.od = od
        self.roster = roster or []
        self._nudged: dict[int, float] = {}

    def nudge(self, account_ids) -> None:
        """Ask OpenDota (in the background) to pull these players' newest games from Valve.

        OpenDota only auto-fetches players who use its site, so without this a
        fresh game can take hours to appear. The refresh takes OpenDota a minute
        or so, so it helps the *next* lookup. At most once per player per 5 min.
        """
        now = time.time()
        for aid in account_ids:
            if now - self._nudged.get(aid, 0) > REFRESH_EVERY:
                self._nudged[aid] = now
                asyncio.create_task(self.od.refresh_player(aid))

    # -- basics --------------------------------------------------------------

    async def rate(self, match_id: int, force: bool = False) -> MatchRating | None:
        m = await self.od.match(match_id, force=force)
        return rate_match(m) if m else None

    def friends_map(self) -> dict[int, str]:
        """{account_id: name} for everyone on the friend list."""
        return {f.account_id: f.name for f in self.roster}

    # -- one friend ----------------------------------------------------------

    async def last_match_id(self, account_id: int) -> int | None:
        self.nudge([account_id])
        recent = await self.od.recent_matches(account_id, force=True)
        if not recent:
            return None
        return max(recent, key=lambda r: r.get("start_time") or 0)["match_id"]

    async def player_games(self, account_id: int, limit: int, force: bool = False) -> list[tuple[MatchRating, PlayerRating]]:
        recent = await self.od.recent_matches(account_id, force=force)
        recent = sorted(recent, key=lambda r: -(r.get("start_time") or 0))[:limit]
        return await self._rate_for(account_id, [r["match_id"] for r in recent])

    # -- the group -----------------------------------------------------------

    async def group_matches(self, n: int, min_friends: int = 2, force: bool = False) -> list[GroupMatch]:
        """Newest matches where at least `min_friends` people from the list played.

        Looks through each friend's 20 most recent matches (OpenDota's
        recentMatches - the fast endpoint), so a game shows up as long as it's
        in the recent 20 of at least two of the people in it.
        """
        if force:
            self.nudge(f.account_id for f in self.roster)
        results = await asyncio.gather(
            *(self.od.recent_matches(f.account_id, force=force) for f in self.roster), return_exceptions=True
        )
        if results and all(isinstance(r, BaseException) for r in results):
            raise OpenDotaError("OpenDota is not responding, try again in a minute")
        seen: dict[int, GroupMatch] = {}
        for f, recent in zip(self.roster, results):
            if isinstance(recent, BaseException):
                continue
            for r in recent:
                g = seen.setdefault(r["match_id"], GroupMatch(r["match_id"], r.get("start_time") or 0, []))
                if f not in g.friends:
                    g.friends.append(f)
        together = [g for g in seen.values() if len(g.friends) >= min_friends]
        together.sort(key=lambda g: -g.start_time)
        return together[:n]

    async def group_history(self, n: int, force: bool = False) -> list[tuple[GroupMatch, MatchRating | None]]:
        groups = await self.group_matches(n, force=force)
        rated = await asyncio.gather(*(self.rate(g.match_id) for g in groups), return_exceptions=True)
        return [(g, r if isinstance(r, MatchRating) else None) for g, r in zip(groups, rated)]

    async def games_since(self, account_id: int, days: int, cap: int = 25) -> list[tuple[MatchRating, PlayerRating]]:
        data = await self.od._request("GET", f"/players/{account_id}/matches", date=days, limit=cap) or []
        cutoff = time.time() - days * 86400
        ids = [r["match_id"] for r in data if (r.get("start_time") or 0) >= cutoff][:cap]
        return await self._rate_for(account_id, ids)

    async def period(self, days: int) -> list[dict]:
        results = await asyncio.gather(*(self.games_since(f.account_id, days) for f in self.roster), return_exceptions=True)
        return [{"name": f.name, "games": g if isinstance(g, list) else []} for f, g in zip(self.roster, results)]

    async def _rate_for(self, account_id: int, match_ids: list[int]) -> list[tuple[MatchRating, PlayerRating]]:
        rated = await asyncio.gather(*(self.rate(mid) for mid in match_ids), return_exceptions=True)
        out = []
        for mr in rated:
            if isinstance(mr, MatchRating):
                p = mr.player_by_account(account_id)
                if p:
                    out.append((mr, p))
        return out
