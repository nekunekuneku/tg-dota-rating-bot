"""Feed fake Telegram updates through the real dispatcher; capture what the bot would send."""

import asyncio
import copy
import datetime as dt

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendChatAction, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.db import DB
from bot.handlers import router
from bot.opendota import BASE, OpenDota
from bot.service import Service
from tests.conftest import BASE_MATCH

GROUP = Chat(id=-100, type="supergroup", title="squad")
DANIL = User(id=42, is_bot=False, first_name="Danil", username="danil")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=len(self.sent), date=dt.datetime.now(), chat=GROUP, text=method.text,
                from_user=User(id=1, is_bot=True, first_name="bot"),
            ).as_(bot)  # fmt: skip
        if isinstance(method, (SendChatAction, AnswerCallbackQuery)):
            return True
        raise AssertionError(f"unexpected {type(method).__name__}")

    async def close(self):
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        raise NotImplementedError


def od_handler(req: httpx.Request):
    p = req.url.path
    if p.endswith("/recentMatches"):
        return httpx.Response(200, json=[{"match_id": 8000000001, "start_time": 2}])
    if p.endswith("/matches/8000000001"):
        return httpx.Response(200, json=copy.deepcopy(BASE_MATCH))
    if p == "/api/players/1000":
        return httpx.Response(200, json={"profile": {"personaname": "EGOIST"}})
    if p == "/api/heroes":
        return httpx.Response(200, json=[{"id": 11, "localized_name": "Shadow Fiend"}])
    return httpx.Response(200, json={})


def msg(text, mid=1):
    return Update(
        update_id=mid,
        message=Message(message_id=mid, date=dt.datetime.now(), chat=GROUP, from_user=DANIL, text=text),
    )


def test_link_last_and_buttons(tmp_path):
    async def go():
        db = DB(str(tmp_path / "h.sqlite3"))
        await db.open()
        od = OpenDota(db)
        od.client = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(od_handler))
        session = FakeSession()
        bot = Bot("42:TEST", session=session, default=DefaultBotProperties(parse_mode="HTML"))
        dp = Dispatcher(svc=Service(db, od))
        dp.include_router(router)

        await dp.feed_update(bot, msg("/link 1000"))
        replies = [m.text for m in session.sent if isinstance(m, SendMessage)]
        assert "EGOIST" in replies[-1]

        await dp.feed_update(bot, msg("/last", 2))
        card = [m for m in session.sent if isinstance(m, SendMessage)][-1]
        assert "Danil" in card.text and "Threw?" in card.text
        buttons = [b for row in card.reply_markup.inline_keyboard for b in row]
        data = {b.callback_data for b in buttons}
        assert "m:8000000001:a" in data and "m:8000000001:w0" in data

        bot_msg = Message(message_id=99, date=dt.datetime.now(), chat=GROUP, text="card")
        for cb_data, expect in [("m:8000000001:w0", "weight"), ("m:8000000001:a", "<pre>"), ("m:8000000001:r", "MVP")]:
            cb = CallbackQuery(id="1", from_user=DANIL, chat_instance="x", message=bot_msg, data=cb_data)
            await dp.feed_update(bot, Update(update_id=10, callback_query=cb))
            edit = [m for m in session.sent if isinstance(m, EditMessageText)][-1]
            assert expect in edit.text, cb_data

        await dp.feed_update(bot, msg("/week", 3))
        edit = [m for m in session.sent if isinstance(m, EditMessageText)][-1]
        assert "Danil" in edit.text or "Nobody" in edit.text

        await od.close()
        await db.close()

    asyncio.run(go())
