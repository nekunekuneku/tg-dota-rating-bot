"""Entry point: `python -m bot`."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .db import DB
from .handlers import router
from .opendota import OpenDota
from .service import Service


def load_dotenv(path: str = ".env") -> None:
    """Tiny .env reader so there's no extra dependency."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


COMMANDS = [
    BotCommand(command="last", description="Rate your last match"),
    BotCommand(command="match", description="Rate a match by ID"),
    BotCommand(command="me", description="Your recent form"),
    BotCommand(command="week", description="Chat leaderboard, 7 days"),
    BotCommand(command="month", description="Chat leaderboard, 30 days"),
    BotCommand(command="link", description="Link your Dota account"),
    BotCommand(command="join", description="Show up in this group's stats"),
    BotCommand(command="scoring", description="How scores work"),
    BotCommand(command="help", description="Help"),
]


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is not set (see .env.example)")

    db = DB(os.getenv("DB_PATH", "bot.sqlite3"))
    await db.open()
    od = OpenDota(db, api_key=os.getenv("OPENDOTA_API_KEY") or None)
    svc = Service(db, od)

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher(svc=svc)
    dp.include_router(router)

    await bot.set_my_commands(COMMANDS)
    asyncio.create_task(od.heroes())  # warm the hero-name cache
    try:
        await dp.start_polling(bot)
    finally:
        await od.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
