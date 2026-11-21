"""STRATZ as a backup for players' match lists.

OpenDota's per-player match lists (recentMatches, matches) sometimes go down
while single matches still load. STRATZ has the same lists, so when OpenDota
fails there, the bot asks STRATZ for the match IDs and keeps going. Match
details still come from OpenDota.

Needs STRATZ_API_KEY (free at stratz.com/api, log in with Steam). One key can
only be used from 2 IP addresses per 15 minutes, so don't share it.
"""

from __future__ import annotations

import logging

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


class StratzError(Exception):
    pass


class Stratz:
    def __init__(self, token: str):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            # STRATZ asks API users to send this exact User-Agent.
            headers={"Authorization": f"Bearer {token}", "User-Agent": "STRATZ_API"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def player_matches(self, account_id: int, take: int = 20, since: int | None = None) -> list[dict]:
        """Newest first, as OpenDota-style rows: [{"match_id", "start_time"}]."""
        variables = {"id": account_id, "take": take, "since": since}
        try:
            r = await self.client.post(URL, json={"query": MATCHES_QUERY, "variables": variables})
        except httpx.HTTPError as e:
            raise StratzError(f"STRATZ is not responding: {e}") from e
        if r.status_code != 200:
            raise StratzError(f"STRATZ returned {r.status_code}: {r.text[:200]}")
        try:
            body = r.json()
        except ValueError:
            raise StratzError(f"STRATZ returned non-JSON: {r.text[:200]}") from None
        if body.get("errors"):
            raise StratzError(f"STRATZ error: {body['errors'][0].get('message', body['errors'][0])}")
        player = (body.get("data") or {}).get("player") or {}
        rows = [{"match_id": m["id"], "start_time": m.get("startDateTime") or 0} for m in player.get("matches") or []]
        return sorted(rows, key=lambda m: -m["start_time"])
