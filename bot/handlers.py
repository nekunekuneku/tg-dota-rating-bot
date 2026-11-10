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

MAX_N = 20  # OpenDota's recentMatches returns 20 per player

HELP = (
    "<b>Who threw?</b> Match verdicts for the squad. I only post when you ask.\n\n"
    "/last <code>[name] [N]</code> – someone's last game, or last N games\n"
    "/ourlast <code>[N]</code> – the squad's last game (2+ of you), or last N\n"
    "/match <code>&lt;id&gt;</code> – rate any match\n"
    "/week, /month – leaderboard\n"
    "/scoring – how the score is calculated\n\n"
    "In lists: R ranked · N normal · T turbo · AD ability draft.\n"
    "Examples: <code>/last nick</code>, <code>/last nick 5</code>, <code>/ourlast 5</code>. "
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
        rows.append([btn("🔄 Refresh", f"m:{m.match_id}:r")])
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


def kb_pick(roster: list[Friend], n: int) -> InlineKeyboardMarkup:
    """Name buttons for /last without a name; n = how many games."""
    buttons = [btn(f.name, f"p:{i}:{n}") for i, f in enumerate(roster)]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i : i + 3] for i in range(0, len(buttons), 3)])


def kb_open(match_ids: list[int]) -> InlineKeyboardMarkup:
    """Numbered buttons that open each game's card."""
    buttons = [btn(str(i), f"o:{mid}") for i, mid in enumerate(match_ids, 1)]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i : i + 5] for i in range(0, len(buttons), 5)])


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
    return render.summary(m, heroes, friends, await abilities_for(svc, m)), kb_summary(m, heroes, friends)


async def abilities_for(svc: Service, m: MatchRating) -> dict[int, str] | None:
    """Ability names, only fetched for Ability Draft games."""
    return await svc.od.ability_names() if m.game_mode == render.ABILITY_DRAFT else None


async def friend_card(svc: Service, f: Friend, n: int) -> tuple[str, InlineKeyboardMarkup | None]:
    """n == 1: that game's card. n > 1: a one-line-per-game list."""
    if n == 1:
        match_id = await svc.last_match_id(f.account_id)
        if not match_id:
            return f"No recent matches for {render.escape(f.name)}. Is Expose Public Match Data on?", None
        return await match_card(svc, match_id)
    games = await svc.player_games(f.account_id, n, force=True)
    heroes = await svc.od.heroes()
    kb = kb_open([m.match_id for m, _ in games]) if games else None
    return render.history(f.name, games, heroes), kb


async def group_card(svc: Service, n: int) -> tuple[str, InlineKeyboardMarkup | None]:
    if n == 1:
        groups = await svc.group_matches(1, force=True)
        if not groups:
            return "No game with 2+ of you together in everyone's last 20 matches.", None
        return await match_card(svc, groups[0].match_id)
    items = await svc.group_history(n, force=True)
    heroes = await svc.od.heroes()
    title = "Last game together" if len(items) == 1 else f"Last {len(items)} games together"
    kb = kb_open([g.match_id for g, _ in items]) if items else None
    return render.our_history(items, heroes, svc.friends_map(), title), kb


def parse_args(args: str | None) -> tuple[str, int]:
    """'nick 5' / '5 nick' / '5' / 'nick' -> (name, count). Small numbers are counts."""
    name, n = [], 1
    for word in (args or "").split():
        if word.isdigit() and int(word) <= 100:
            n = int(word)
        else:
            name.append(word)
    return " ".join(name), max(1, min(MAX_N, n))


def resolve(svc: Service, name: str) -> Friend | str | None:
    """Friend, an error message, or None if no name was given."""
    if not name.strip():
        return None
    hit = find_friend(svc.roster, name)
    if isinstance(hit, Friend):
        return hit
    if isinstance(hit, list):
        return "Did you mean " + " or ".join(render.escape(f.name) for f in hit) + "?"
    return f"No one called “{render.escape(name.strip())}” on the list."


async def typing(message: Message) -> None:
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    except TelegramBadRequest:
        pass


async def reply_with(message: Message, builder, *args) -> None:
    await typing(message)
    try:
        text, kb = await builder(*args)
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
    name, n = parse_args(command.args)
    target = resolve(svc, name)
    if target is None or isinstance(target, str):
        await message.reply(target or "Whose?", reply_markup=kb_pick(svc.roster, n))
        return
    await reply_with(message, friend_card, svc, target, n)


@router.message(Command("ourlast"))
async def cmd_ourlast(message: Message, command: CommandObject, svc: Service) -> None:
    _, n = parse_args(command.args)
    await reply_with(message, group_card, svc, n)


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
        _, idx, n = cb.data.split(":")
        friend, n = svc.roster[int(idx)], max(1, min(MAX_N, int(n)))
    except (ValueError, IndexError):
        await cb.answer()
        return
    await cb.answer(f"Loading {friend.name}…")
    try:
        text, kb = await friend_card(svc, friend, n)
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
        text, kb = render.summary(m, heroes, friends, await abilities_for(svc, m)), kb_summary(m, heroes, friends)

    await edit(cb, text, kb)
    await cb.answer(note or None, show_alert=bool(note))
