"""SQLite storage: linked accounts, chat membership and an OpenDota cache."""

from __future__ import annotations

import json
import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id        INTEGER PRIMARY KEY,
    account_id   INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    username     TEXT
);
CREATE TABLE IF NOT EXISTS chat_members (
    chat_id INTEGER NOT NULL,
    tg_id   INTEGER NOT NULL,
    PRIMARY KEY (chat_id, tg_id)
);
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    permanent  INTEGER NOT NULL DEFAULT 0
);
"""


class User:
    __slots__ = ("tg_id", "account_id", "display_name", "username")

    def __init__(self, tg_id: int, account_id: int, display_name: str, username: str | None):
        self.tg_id = tg_id
        self.account_id = account_id
        self.display_name = display_name
        self.username = username


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # -- users ---------------------------------------------------------------

    async def link(self, tg_id: int, account_id: int, display_name: str, username: str | None) -> None:
        await self.conn.execute(
            "INSERT INTO users (tg_id, account_id, display_name, username) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET account_id=excluded.account_id, "
            "display_name=excluded.display_name, username=excluded.username",
            (tg_id, account_id, display_name, username),
        )
        await self.conn.commit()

    async def unlink(self, tg_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM users WHERE tg_id = ?", (tg_id,))
        await self.conn.execute("DELETE FROM chat_members WHERE tg_id = ?", (tg_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def touch_user(self, tg_id: int, display_name: str, username: str | None) -> None:
        """Keep names fresh if someone renames themselves in Telegram."""
        await self.conn.execute(
            "UPDATE users SET display_name = ?, username = ? WHERE tg_id = ?", (display_name, username, tg_id)
        )
        await self.conn.commit()

    async def get_user(self, tg_id: int) -> User | None:
        async with self.conn.execute(
            "SELECT tg_id, account_id, display_name, username FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        return User(*row) if row else None

    async def get_user_by_username(self, username: str) -> User | None:
        async with self.conn.execute(
            "SELECT tg_id, account_id, display_name, username FROM users WHERE lower(username) = lower(?)",
            (username.lstrip("@"),),
        ) as cur:
            row = await cur.fetchone()
        return User(*row) if row else None

    # -- chats ---------------------------------------------------------------

    async def add_member(self, chat_id: int, tg_id: int) -> None:
        await self.conn.execute("INSERT OR IGNORE INTO chat_members (chat_id, tg_id) VALUES (?, ?)", (chat_id, tg_id))
        await self.conn.commit()

    async def remove_member(self, chat_id: int, tg_id: int) -> None:
        await self.conn.execute("DELETE FROM chat_members WHERE chat_id = ? AND tg_id = ?", (chat_id, tg_id))
        await self.conn.commit()

    async def chat_users(self, chat_id: int) -> list[User]:
        async with self.conn.execute(
            "SELECT u.tg_id, u.account_id, u.display_name, u.username FROM users u "
            "JOIN chat_members m ON m.tg_id = u.tg_id WHERE m.chat_id = ? ORDER BY u.display_name",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [User(*r) for r in rows]

    # -- cache ---------------------------------------------------------------

    async def cache_get(self, key: str, max_age: int | None) -> dict | list | None:
        async with self.conn.execute("SELECT data, fetched_at, permanent FROM cache WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        data, fetched_at, permanent = row
        if not permanent and max_age is not None and time.time() - fetched_at > max_age:
            return None
        return json.loads(data)

    async def cache_put(self, key: str, data: dict | list, permanent: bool = False) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO cache (key, data, fetched_at, permanent) VALUES (?, ?, ?, ?)",
            (key, json.dumps(data, separators=(",", ":")), int(time.time()), int(permanent)),
        )
        await self.conn.commit()
