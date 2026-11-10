"""Telegram commands. The bot only ever speaks when someone asks it to."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import render
from .opendota import OpenDotaError
from .scoring import MatchRating
from .service import Friend, Service, find_friend, parse_match_id

log = logging.getLogger(__name__)
router = Router()

HISTORY_N = 5

HELP = (
    "<b>Who threw?</b> Match verdicts for the squad. I only post when you ask.\n\n"
    "/last <code>[name]</code> – someone's last game\n"
    "/ourlast – the squad's last game (2+ of you)\n"
    "/history <code>[name]</code> – someone's last 5 games\n"
    "/ourhistory – the squad's last 5 games together\n"
    "/match <code>&lt;id&gt;</code> – rate any match\n"
    "/week, /month – leaderboard\n"
    "/scoring – how the score is calculated\n\n"
    "Leave the name out and I'll show buttons to pick who."
)


# -- keyboards ---------------------------------------------------------------


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def kb_summary(m: MatchRating, heroes: dict[int, str], friends: dict[int, str]) -> InlineKeyboardMarkup:
    rows = [[btn("👥 All players", f"m:{m.match_id}:a")]]
    targets = sorted((r for r in m.players if r.account_id in friends), key=lambda r: -r.score)[:6]
    if not targets:
        targets = [r for r in (m.mvp, *m.threw[:1]) if r]
    why = [
        btn(f"🔍 {render.short(friends.get(r.account_id) or heroes.get(r.hero_id, '?'), 14)}", f"m:{m.match_id}:w{r.slot}")
        for r in targets
    ]
    for i in range(0, len(why), 3):
        rows.append(why[i : i + 3])
    if not m.parsed:
        rows.append([btn("🔄 Refresh (replay parsed?)", f"m:{m.match_id}:r")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_all(m: MatchRating, heroes: dict[int, str]) -> InlineKeyboardMarkup:
    rows = []
    for radiant in (True, False):
        team = sorted((r for r in m.players if r.is_radiant == radiant), key=lambda r: r.position)
        rows.append([btn(render.short(heroes.get(r.hero_id, "?"), 8), f"m:{m.match_id}:w{r.slot}") for r in team])
    rows.append([btn("⬅️ Back", f"m:{m.match_id}:s")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back(m: MatchRating) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn("⬅️ Back", f"m:{m.match_id}:s")]])


def kb_pick(roster: list[Friend], action: str) -> InlineKeyboardMarkup:
    """Name buttons. action: 'l' = last game, 'h' = history."""
    buttons = [btn(f.name, f"p:{action}:{i}") for i, f in enumerate(roster)]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i : i + 3] for i in range(0, len(buttons), 3)])


def kb_open(match_ids: list[int]) -> InlineKeyboardMarkup:
    """Numbered buttons that open each game's card."""
    buttons = [btn(str(i), f"o:{mid}") for i, mid in enumerate(match_ids, 1)]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


# -- shared bits -------------------------------------------------------------


async def match_card(svc: Service, match_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    m = await svc.rate(match_id)
    if not m:
        asyncio.create_task(svc.od.request_parse(match_id))  # nudges OpenDota to fetch it
        return (
            "OpenDota doesn't have this match yet. New games usually show up a few minutes "
            "after they end. Try again soon, or check the match ID.",
            None,
        )
    if not m.parsed:
        asyncio.create_task(svc.od.request_parse(match_id))  # so Refresh has something to find later
    heroes = await svc.od.heroes()
    friends = svc.friends_map()
    return render.summary(m, heroes, friends), kb_summary(m, heroes, friends)


async def last_card(svc: Service, f: Friend) -> tuple[str, InlineKeyboardMarkup | None]:
    match_id = await svc.last_match_id(f.account_id)
    if not match_id:
        return f"No recent matches for {render.escape(f.name)}. Is Expose Public Match Data on?", None
    return await match_card(svc, match_id)


async def history_card(svc: Service, f: Friend) -> tuple[str, InlineKeyboardMarkup | None]:
    games = await svc.player_games(f.account_id, HISTORY_N, force=True)
    heroes = await svc.od.heroes()
    kb = kb_open([m.match_id for m, _ in games]) if games else None
    return render.history(f.name, games, heroes), kb


def resolve(svc: Service, args: str | None) -> Friend | str | None:
    """Friend, an error message, or None if no name was given."""
    if not args or not args.strip():
        return None
    hit = find_friend(svc.roster, args)
    if isinstance(hit, Friend):
        return hit
    if isinstance(hit, list):
        return "Did you mean " + " or ".join(render.escape(f.name) for f in hit) + "?"
    return f"No one called “{render.escape(args.strip())}” on the list."


async def typing(message: Message) -> None:
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    except TelegramBadRequest:
        pass


async def for_friend(message: Message, command: CommandObject, svc: Service, action: str, builder) -> None:
    target = resolve(svc, command.args)
    if target is None:
        await message.reply("Whose?", reply_markup=kb_pick(svc.roster, action))
        return
    if isinstance(target, str):
        await message.reply(target, reply_markup=kb_pick(svc.roster, action))
        return
    await typing(message)
    try:
        text, kb = await builder(svc, target)
    except OpenDotaError as e:
        text, kb = str(e), None
    await message.reply(text, reply_markup=kb)


# -- commands ----------------------------------------------------------------


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.reply(HELP)


@router.message(Command("scoring"))
async def cmd_scoring(message: Message) -> None:
    await message.reply(render.scoring_help())


@router.message(Command("last"))
async def cmd_last(message: Message, command: CommandObject, svc: Service) -> None:
    await for_friend(message, command, svc, "l", last_card)


@router.message(Command("history"))
async def cmd_history(message: Message, command: CommandObject, svc: Service) -> None:
    await for_friend(message, command, svc, "h", history_card)


@router.message(Command("ourlast"))
async def cmd_ourlast(message: Message, svc: Service) -> None:
    await typing(message)
    try:
        groups = await svc.group_matches(1, force=True)
        if not groups:
            await message.reply("No game with 2+ of you together in everyone's last 20 matches.")
            return
        text, kb = await match_card(svc, groups[0].match_id)
    except OpenDotaError as e:
        text, kb = str(e), None
    await message.reply(text, reply_markup=kb)


@router.message(Command("ourhistory"))
async def cmd_ourhistory(message: Message, svc: Service) -> None:
    await typing(message)
    try:
        items = await svc.group_history(HISTORY_N, force=True)
        heroes = await svc.od.heroes()
        text = render.our_history(items, heroes, svc.friends_map(), "Last game together" if len(items) == 1 else f"Last {len(items)} games together")
        kb = kb_open([g.match_id for g, _ in items]) if items else None
    except OpenDotaError as e:
        text, kb = str(e), None
    await message.reply(text, reply_markup=kb)


@router.message(Command("match"))
async def cmd_match(message: Message, command: CommandObject, svc: Service) -> None:
    match_id = parse_match_id(command.args or "")
    if not match_id:
        await message.reply("Usage: <code>/match 8012345678</code>")
        return
    await typing(message)
    try:
        text, kb = await match_card(svc, match_id)
    except OpenDotaError as e:
        text, kb = str(e), None
    await message.reply(text, reply_markup=kb)


async def period(message: Message, svc: Service, days: int) -> None:
    status = await message.reply(f"⏳ Crunching the last {days} days for {len(svc.roster)} players…")
    try:
        rows = await svc.period(days)
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


async def edit(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise


@router.callback_query(F.data.startswith("p:"))
async def on_pick(cb: CallbackQuery, svc: Service) -> None:
    """A name was tapped under 'Whose?'."""
    try:
        _, action, idx = cb.data.split(":")
        friend = svc.roster[int(idx)]
    except (ValueError, IndexError):
        await cb.answer()
        return
    await cb.answer(f"Loading {friend.name}…")
    builder = last_card if action == "l" else history_card
    try:
        text, kb = await builder(svc, friend)
    except OpenDotaError as e:
        text, kb = str(e), None
    await edit(cb, text, kb)


@router.callback_query(F.data.startswith("o:"))
async def on_open(cb: CallbackQuery, svc: Service) -> None:
    """Number tapped under a history list: post that game's card as a reply."""
    try:
        match_id = int(cb.data.split(":")[1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    await cb.answer()
    try:
        text, kb = await match_card(svc, match_id)
    except OpenDotaError as e:
        text, kb = str(e), None
    await cb.message.reply(text, reply_markup=kb)


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
    friends = svc.friends_map()
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

    await edit(cb, text, kb)
    await cb.answer(note or None, show_alert=bool(note))
