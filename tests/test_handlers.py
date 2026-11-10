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
from bot.service import Friend, Service
from tests.conftest import BASE_MATCH

GROUP = Chat(id=-100, type="supergroup", title="squad")
NICK = User(id=42, is_bot=False, first_name="Nick")
ROSTER = [Friend(1000, "Danil"), Friend(1129, "Ruslan"), Friend(5, "Solo")]
MID = 8000000001


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
    if p == "/api/players/5/recentMatches":
        return httpx.Response(200, json=[{"match_id": 7, "start_time": 9}])
    if p.endswith("/recentMatches"):
        return httpx.Response(200, json=[{"match_id": MID, "start_time": 2}])
    if p.endswith(f"/matches/{MID}"):
        return httpx.Response(200, json=copy.deepcopy(BASE_MATCH))
    if p == "/api/heroes":
        return httpx.Response(200, json=[{"id": 11, "localized_name": "Shadow Fiend"}])
    return httpx.Response(200, json={})


def test_commands_and_buttons(tmp_path):
    async def go():
        db = DB(str(tmp_path / "h.sqlite3"))
        await db.open()
        od = OpenDota(db)
        od.client = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(od_handler))
        session = FakeSession()
        bot = Bot("42:TEST", session=session, default=DefaultBotProperties(parse_mode="HTML"))
        dp = Dispatcher(svc=Service(db, od, ROSTER))
        dp.include_router(router)
        try:
            await scenario(dp, bot, session)
        finally:
            await od.close()
            await db.close()

    async def scenario(dp, bot, session):
        uid = iter(range(1, 1000))

        async def say(text):
            m = Message(message_id=next(uid), date=dt.datetime.now(), chat=GROUP, from_user=NICK, text=text)
            await dp.feed_update(bot, Update(update_id=next(uid), message=m))
            return [x for x in session.sent if isinstance(x, SendMessage)][-1]

        async def tap(data):
            bot_msg = Message(message_id=99, date=dt.datetime.now(), chat=GROUP, text="card")
            cb = CallbackQuery(id="1", from_user=NICK, chat_instance="x", message=bot_msg, data=data)
            await dp.feed_update(bot, Update(update_id=next(uid), callback_query=cb))
            return session.sent[-2] if isinstance(session.sent[-1], AnswerCallbackQuery) else session.sent[-1]

        # /last with no name -> name picker; tapping a name -> card
        r = await say("/last")
        assert r.text == "Whose?"
        assert [b.text for b in r.reply_markup.inline_keyboard[0]] == ["Danil", "Ruslan", "Solo"]
        assert r.reply_markup.inline_keyboard[0][0].callback_data == "p:0:1"
        card = await tap("p:0:1")
        assert isinstance(card, EditMessageText) and "Friends on both sides" in card.text

        # /last with a name
        r = await say("/last danil")
        assert "Danil</b> (" in r.text and "Ruslan</b> (" in r.text
        assert any(b.callback_data == f"m:{MID}:w0" for row in r.reply_markup.inline_keyboard for b in row)
        assert "No one" in (await say("/last bob")).text

        # buttons on the card
        for data, expect in [(f"m:{MID}:w0", "weight"), (f"m:{MID}:a", "<pre>"), (f"m:{MID}:r", "Friends on both sides")]:
            assert expect in (await tap(data)).text, data

        # /last N: one line per game + number buttons that open the card
        r = await say("/last ruslan 5")
        assert "Ruslan" in r.text and "1. " in r.text
        assert r.reply_markup.inline_keyboard[0][0].callback_data == f"o:{MID}"
        assert "Friends on both sides" in (await tap(f"o:{MID}")).text

        # picker remembers the count: "/last 3" -> tap a name -> list, not a card
        r = await say("/last 3")
        assert r.reply_markup.inline_keyboard[0][1].callback_data == "p:1:3"
        assert "📜" in (await tap("p:1:3")).text

        # group: match 8000000001 has Danil + Ruslan; Solo's game is ignored
        r = await say("/ourlast")
        assert "Danil</b> (" in r.text and "Ruslan</b> (" in r.text
        r = await say("/ourlast 5")
        assert "Last game together" in r.text and "Danil" in r.text and "Solo" not in r.text

    asyncio.run(go())
