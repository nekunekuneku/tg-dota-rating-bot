"""Glue between OpenDota, the DB and the scorer."""

from __future__ import annotations

import asyncio
import re
import time

from .db import DB, User
from .opendota import OpenDota
from .scoring import MatchRating, PlayerRating, rate_match

STEAM64_BASE = 76561197960265728


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


class Service:
    def __init__(self, db: DB, od: OpenDota):
        self.db = db
        self.od = od

    async def rate(self, match_id: int, force: bool = False) -> MatchRating | None:
        m = await self.od.match(match_id, force=force)
        return rate_match(m) if m else None

    async def friends_map(self, chat_id: int) -> dict[int, str]:
        """{account_id: display name} for everyone linked in this chat."""
        return {u.account_id: u.display_name for u in await self.db.chat_users(chat_id)}

    async def last_match_id(self, account_id: int) -> int | None:
        recent = await self.od.recent_matches(account_id, force=True)
        if not recent:
            return None
        return max(recent, key=lambda r: r.get("start_time") or 0)["match_id"]

    async def player_games(self, account_id: int, limit: int) -> list[tuple[MatchRating, PlayerRating]]:
        recent = await self.od.recent_matches(account_id)
        recent = sorted(recent, key=lambda r: -(r.get("start_time") or 0))[:limit]
        return await self._rate_for(account_id, [r["match_id"] for r in recent])

    async def games_since(self, account_id: int, days: int, cap: int = 25) -> list[tuple[MatchRating, PlayerRating]]:
        data = await self.od._request("GET", f"/players/{account_id}/matches", date=days, limit=cap) or []
        cutoff = time.time() - days * 86400
        ids = [r["match_id"] for r in data if (r.get("start_time") or 0) >= cutoff][:cap]
        return await self._rate_for(account_id, ids)

    async def _rate_for(self, account_id: int, match_ids: list[int]) -> list[tuple[MatchRating, PlayerRating]]:
        rated = await asyncio.gather(*(self.rate(mid) for mid in match_ids), return_exceptions=True)
        out = []
        for mr in rated:
            if isinstance(mr, MatchRating):
                p = mr.player_by_account(account_id)
                if p:
                    out.append((mr, p))
        return out

    async def chat_period(self, users: list[User], days: int) -> list[dict]:
        results = await asyncio.gather(*(self.games_since(u.account_id, days) for u in users), return_exceptions=True)
        rows = []
        for u, games in zip(users, results):
            rows.append({"name": u.display_name, "games": games if isinstance(games, list) else []})
        return rows
