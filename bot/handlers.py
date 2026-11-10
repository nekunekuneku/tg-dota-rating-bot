"""Telegram commands. The bot only ever speaks when someone asks it to."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ChatAction, ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import render
from .db import User
from .opendota import OpenDotaError
from .scoring import MatchRating
from .service import Service, parse_account_id, parse_match_id

log = logging.getLogger(__name__)
router = Router()

HELP = (
    "<b>Who threw?</b> Match verdicts for your friend group. I only post when you ask.\n\n"
    "/link <code>&lt;Friend ID or profile link&gt;</code> – link your Dota account\n"
    "/last – rate your last match (reply to someone or add @name for theirs)\n"
    "/match <code>&lt;id&gt;</code> – rate any match\n"
    "/me – your last 10 games (/me 20 for more)\n"
    "/week, /month – chat leaderboard\n"
    "/scoring – how the score is calculated\n"
    "/unlink – forget your account\n\n"
    "In a group, run /link (or /join if you linked in DMs) so you show up in the group's stats.\n"
    "Your match data must be public: Dota 2 → Settings → Social → Expose Public Match Data."
)


def tg_name(msg_user) -> str:
    return msg_user.first_name or msg_user.username or str(msg_user.id)


def is_group(message: Message) -> bool:
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def remember(message: Message, svc: Service) -> User | None:
    """Return the sender's linked account and register them in this group."""
    u = await svc.db.get_user(message.from_user.id)
    if u:
        await svc.db.touch_user(u.tg_id, tg_name(message.from_user), message.from_user.username)
        if is_group(message):
            await svc.db.add_member(message.chat.id, u.tg_id)
    return u


# -- keyboards ---------------------------------------------------------------


def kb_summary(m: MatchRating, heroes: dict[int, str], friends: dict[int, str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="👥 All players", callback_data=f"m:{m.match_id}:a")]]
    targets = [r for r in m.players if r.account_id and r.account_id in friends][:4]
    if not targets:
        targets = [r for r in (m.mvp, *m.threw[:1]) if r]
    why = [
        InlineKeyboardButton(
            text=f"🔍 {render.short(friends.get(r.account_id) or heroes.get(r.hero_id, '?'), 14)}",
            callback_data=f"m:{m.match_id}:w{r.slot}",
        )
        for r in targets
    ]
    for i in range(0, len(why), 2):
        rows.append(why[i : i + 2])
    if not m.parsed:
        rows.append([InlineKeyboardButton(text="🔄 Refresh (replay parsed?)", callback_data=f"m:{m.match_id}:r")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_all(m: MatchRating, heroes: dict[int, str]) -> InlineKeyboardMarkup:
    rows = []
    for radiant in (True, False):
        team = sorted((r for r in m.players if r.is_radiant == radiant), key=lambda r: r.position)
        rows.append(
            [
                InlineKeyboardButton(
                    text=render.short(heroes.get(r.hero_id, "?"), 8), callback_data=f"m:{m.match_id}:w{r.slot}"
                )
                for r in team
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data=f"m:{m.match_id}:s")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back(m: MatchRating, to: str = "s") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data=f"m:{m.match_id}:{to}")]]
    )


async def send_match(message: Message, svc: Service, match_id: int) -> None:
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    m = await svc.rate(match_id)
    if not m:
        asyncio.create_task(svc.od.request_parse(match_id))  # nudges OpenDota to fetch it
        await message.reply(
            "OpenDota doesn't have this match yet. New games usually show up a few minutes "
            "after they end. Try again soon, or check the match ID."
        )
        return
    if not m.parsed:
        asyncio.create_task(svc.od.request_parse(match_id))  # so Refresh has something to find later
    heroes = await svc.od.heroes()
    friends = await friends_for(message, svc)
    await message.reply(render.summary(m, heroes, friends), reply_markup=kb_summary(m, heroes, friends))


async def friends_for(message: Message, svc: Service) -> dict[int, str]:
    friends = await svc.friends_map(message.chat.id)
    if not is_group(message):  # in DMs, highlight yourself
        u = await svc.db.get_user(message.chat.id)
        if u:
            friends[u.account_id] = u.display_name
    return friends


# -- commands ----------------------------------------------------------------


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message, svc: Service) -> None:
    await remember(message, svc)
    await message.reply(HELP)


@router.message(Command("scoring"))
async def cmd_scoring(message: Message) -> None:
    await message.reply(render.scoring_help())


@router.message(Command("link"))
async def cmd_link(message: Message, command: CommandObject, svc: Service) -> None:
    account_id = parse_account_id(command.args or "")
    if not account_id:
        await message.reply(
            "Send your Dota Friend ID, e.g. <code>/link 123456789</code>\n"
            "Also works with a Dotabuff/OpenDota/STRATZ link or steamcommunity.com/profiles/… link."
        )
        return
    try:
        prof = await svc.od.profile(account_id)
    except OpenDotaError as e:
        await message.reply(str(e))
        return
    await svc.db.link(message.from_user.id, account_id, tg_name(message.from_user), message.from_user.username)
    if is_group(message):
        await svc.db.add_member(message.chat.id, message.from_user.id)
    if prof:
        p = prof["profile"]
        await message.reply(f"Linked to <b>{render.escape(p.get('personaname') or str(account_id))}</b> ✅")
    else:
        await message.reply(
            f"Linked to <code>{account_id}</code>, but OpenDota has no public profile for it yet. "
            "Make sure Expose Public Match Data is on, play a game, and try /last."
        )


@router.message(Command("unlink"))
async def cmd_unlink(message: Message, svc: Service) -> None:
    ok = await svc.db.unlink(message.from_user.id)
    await message.reply("Unlinked 👋" if ok else "You weren't linked.")


@router.message(Command("join"))
async def cmd_join(message: Message, svc: Service) -> None:
    if not is_group(message):
        await message.reply("Run /join in your group chat.")
        return
    u = await remember(message, svc)
    await message.reply("You're in this chat's stats ✅" if u else "Link first: /link <Friend ID>")


@router.message(Command("leave"))
async def cmd_leave(message: Message, svc: Service) -> None:
    if is_group(message):
        await svc.db.remove_member(message.chat.id, message.from_user.id)
        await message.reply("Removed you from this chat's stats.")


async def resolve_target(message: Message, command: CommandObject, svc: Service) -> User | None:
    """Reply-to user, @username in args, or the sender."""
    if message.reply_to_message and message.reply_to_message.from_user and not message.reply_to_message.from_user.is_bot:
        return await svc.db.get_user(message.reply_to_message.from_user.id)
    for word in (command.args or "").split():
        if word.startswith("@"):
            return await svc.db.get_user_by_username(word)
    return await svc.db.get_user(message.from_user.id)


@router.message(Command("last"))
async def cmd_last(message: Message, command: CommandObject, svc: Service) -> None:
    await remember(message, svc)
    target = await resolve_target(message, command, svc)
    if not target:
        await message.reply("Not linked. Use /link <Friend ID> first.")
        return
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        match_id = await svc.last_match_id(target.account_id)
        if not match_id:
            await message.reply("No recent matches found. Is Expose Public Match Data on?")
            return
        await send_match(message, svc, match_id)
    except OpenDotaError as e:
        await message.reply(str(e))


@router.message(Command("match"))
async def cmd_match(message: Message, command: CommandObject, svc: Service) -> None:
    await remember(message, svc)
    match_id = parse_match_id(command.args or "")
    if not match_id:
        await message.reply("Usage: <code>/match 8012345678</code>")
        return
    try:
        await send_match(message, svc, match_id)
    except OpenDotaError as e:
        await message.reply(str(e))


@router.message(Command("me"))
async def cmd_me(message: Message, command: CommandObject, svc: Service) -> None:
    await remember(message, svc)
    target = await resolve_target(message, command, svc)
    if not target:
        await message.reply("Not linked. Use /link <Friend ID> first.")
        return
    limit = 10
    for word in (command.args or "").split():
        if word.isdigit():
            limit = max(1, min(20, int(word)))
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        games = await svc.player_games(target.account_id, limit)
        heroes = await svc.od.heroes()
        await message.reply(render.profile(target.display_name, games, heroes))
    except OpenDotaError as e:
        await message.reply(str(e))


async def period(message: Message, svc: Service, days: int) -> None:
    await remember(message, svc)
    if not is_group(message):
        u = await svc.db.get_user(message.from_user.id)
        users = [u] if u else []
    else:
        users = await svc.db.chat_users(message.chat.id)
    if not users:
        await message.reply("Nobody's linked here yet. /link <Friend ID>")
        return
    status = await message.reply(f"⏳ Crunching the last {days} days for {len(users)} players…")
    try:
        rows = await svc.chat_period(users, days)
        heroes = await svc.od.heroes()
        await status.edit_text(render.week(rows, heroes, days))
    except OpenDotaError as e:
        await status.edit_text(str(e))


@router.message(Command("week"))
async def cmd_week(message: Message, svc: Service) -> None:
    await period(message, svc, 7)


@router.message(Command("month"))
async def cmd_month(message: Message, svc: Service) -> None:
    await period(message, svc, 30)


# -- buttons -----------------------------------------------------------------


@router.callback_query(F.data.startswith("m:"))
async def on_match_button(cb: CallbackQuery, svc: Service) -> None:
    try:
        _, mid, view = cb.data.split(":", 2)
        match_id = int(mid)
    except ValueError:
        await cb.answer()
        return

    note = None
    try:
        if view == "r":
            await svc.od.request_parse(match_id)
            m = await svc.rate(match_id, force=True)
            if m and not m.parsed:
                note = "Not parsed yet. Asked OpenDota to parse it, try again in a few minutes."
            view = "s"
        else:
            m = await svc.rate(match_id)
    except OpenDotaError as e:
        await cb.answer(str(e), show_alert=True)
        return
    if not m:
        await cb.answer("Match not found", show_alert=True)
        return

    heroes = await svc.od.heroes()
    friends = await svc.friends_map(cb.message.chat.id)
    if cb.message.chat.type == ChatType.PRIVATE:
        u = await svc.db.get_user(cb.from_user.id)
        if u:
            friends[u.account_id] = u.display_name

    if view == "a":
        text, kb = render.all_players(m, heroes, friends), kb_all(m, heroes)
    elif view.startswith("w"):
        r = m.player_by_slot(int(view[1:]))
        if not r:
            await cb.answer()
            return
        text, kb = render.why(m, r, heroes, friends), kb_back(m)
    else:
        text, kb = render.summary(m, heroes, friends), kb_summary(m, heroes, friends)

    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise
    await cb.answer(note or None, show_alert=bool(note))
