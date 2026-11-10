"""Telegram HTML formatting. Pure functions: rating in, (text) out.

Goal: the default card fits on one phone screen. Details live behind
buttons ("All players", "Why?") instead of being dumped up front.
"""

from __future__ import annotations

import time
from html import escape as _escape

from .scoring import COMPONENT_LABELS, MatchRating, PlayerRating

def escape(text: str) -> str:
    """Telegram HTML only needs <, > and & escaped; leave quotes readable."""
    return _escape(text, quote=False)


VERDICT_EMOJI = {
    "Carried": "🔥",
    "Tried hard": "🦸",
    "Good": "👍",
    "OK": "▫️",
    "Got carried": "🎒",
    "Weak": "👎",
    "Bad": "🫠",
    "Threw?": "💀",
}

GAME_MODES = {1: "All Pick", 2: "Captains Mode", 3: "Random Draft", 4: "Single Draft", 5: "All Random",
              16: "Captains Draft", 18: "Ability Draft", 22: "All Pick", 23: "Turbo"}  # fmt: skip
LOBBIES = {0: "Normal", 1: "Practice", 2: "Tournament", 7: "Ranked"}


def duration(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def mode_name(m: MatchRating) -> str:
    mode = GAME_MODES.get(m.game_mode, f"mode {m.game_mode}")
    lobby = LOBBIES.get(m.lobby_type)
    return f"{lobby} {mode}" if lobby and mode != "Turbo" else mode


def signed(x: float) -> str:
    return f"{x:+.2f}".replace("-", "−")


def short(name: str, n: int) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


def verdict_tag(r: PlayerRating) -> str:
    return f"{VERDICT_EMOJI.get(r.verdict, '')} {r.verdict}".strip()


def who(r: PlayerRating, heroes: dict[int, str], friends: dict[int, str]) -> str:
    """'Ruslan (Juggernaut)' for friends, just 'Juggernaut' for randoms."""
    hero = escape(heroes.get(r.hero_id, f"Hero #{r.hero_id}"))
    friend = friends.get(r.account_id) if r.account_id else None
    return f"<b>{escape(friend)}</b> ({hero})" if friend else hero


def header(m: MatchRating) -> str:
    side = "Radiant" if m.radiant_win else "Dire"
    return (
        f"🏆 <b>{side} victory</b> · {m.radiant_score}–{m.dire_score} · {duration(m.duration)}\n"
        f"{mode_name(m)} · <a href=\"https://www.opendota.com/matches/{m.match_id}\">#{m.match_id}</a>"
    )


ABILITY_DRAFT = 18


def drafted(r: PlayerRating, abilities: dict[int, str]) -> str:
    """'Thunder Clap · Vengeance Aura · Nether Swap · Gust' (talents already filtered out)."""
    return " · ".join(escape(abilities[a]) for a in r.abilities if a in abilities)


def squad_lines(
    side: list[PlayerRating],
    won: bool,
    mvp_slot: int | None,
    heroes: dict[int, str],
    friends: dict[int, str],
    abilities: dict[int, str] | None = None,
) -> list[str]:
    """Comments for the friends on one team.

    Win:  best friend = MVP (if they're the match MVP) or "best of us", with
          their best stat. Worst friend = "got carried", plus their worst stat
          if they were below normal.
    Loss: everyone shows their worst stat, except the best friend if they
          were above normal (they show their best stat). Worst = "griefer".
    """
    side = sorted(side, key=lambda r: -r.score)
    best, worst = side[0], side[-1]
    solo = len(side) == 1
    notes: dict[int, tuple[str, str, object]] = {}  # slot -> (icon, tag, Component | None)

    if won:
        for r in side:
            notes[r.slot] = ("▫️", "", None)
        if not solo:
            notes[worst.slot] = ("🎒", "got carried", worst.worst_stat() if worst.ruin_meter < 0 else None)
        good = best.best_stat()
        notes[best.slot] = (
            ("⭐", "MVP") if best.slot == mvp_slot else ("💪", "best of us")
        ) + ((good if good and good.score > 50 else None),)
    else:
        for r in side:
            notes[r.slot] = ("👎", "", r.worst_stat())
        best_ok = best.ruin_meter > 0
        if best_ok:
            notes[best.slot] = ("👍", "best of us", best.best_stat())
        if not (solo and best_ok):
            notes[worst.slot] = ("🗑", "griefer", worst.worst_stat())

    # Match stats first, one line per friend (plus drafted skills in Ability Draft)...
    lines = []
    for r in side:
        lines.append(
            f"{who(r, heroes, friends)} · pos {r.position} · {r.kda} · <b>{round(r.score)}</b> ({signed(r.ruin_meter)})"
        )
        skills = drafted(r, abilities) if abilities else ""
        if skills:
            lines.append(f"      🎲 {skills}")
    # ...then the comments underneath.
    comments = []
    for r in side:
        icon, tag, stat = notes[r.slot]
        if not tag and not stat:
            continue
        name = escape(friends.get(r.account_id, "?"))
        text = f"{icon} <b>{tag[0].upper() + tag[1:]}</b>: {name}" if tag else f"{icon} {name}"
        if stat:
            text += f" — {escape(stat.stat)}"
        comments.append(text)
    if comments:
        lines += [""] + comments
    return lines


def summary(
    m: MatchRating, heroes: dict[int, str], friends: dict[int, str], abilities: dict[int, str] | None = None
) -> str:
    """abilities: pass OpenDota.ability_names() for Ability Draft games to list drafted skills."""
    if m.game_mode != ABILITY_DRAFT:
        abilities = None
    ours = [r for r in m.players if r.account_id and r.account_id in friends]
    mvp_slot = m.mvp.slot if m.mvp else None
    link = f"{mode_name(m)} · <a href=\"https://www.opendota.com/matches/{m.match_id}\">#{m.match_id}</a>"
    score = f"{m.radiant_score}–{m.dire_score} · {duration(m.duration)}"
    sides = {r.is_radiant for r in ours}

    if len(sides) == 1:
        we_radiant = ours[0].is_radiant
        won = ours[0].won
        team = "Radiant" if we_radiant else "Dire"
        lines = [f"{'✅ <b>We won</b>' if won else '❌ <b>We lost</b>'} as {team} · {score}", link, ""]
        lines += squad_lines(ours, won, mvp_slot, heroes, friends, abilities)
    elif len(sides) == 2:
        lines = [f"⚔️ <b>Friends on both sides</b> · {score}", link]
        for radiant in (True, False):
            side = [r for r in ours if r.is_radiant == radiant]
            won = side[0].won
            lines += ["", f"<b>{'Radiant' if radiant else 'Dire'}</b> {'✅' if won else '❌'}"]
            lines += squad_lines(side, won, mvp_slot, heroes, friends, abilities)
    else:
        # Nobody from the squad: plain summary of the match.
        lines = [header(m), ""]
        if m.mvp:
            lines.append(f"⭐ MVP: {who(m.mvp, heroes, friends)} — {round(m.mvp.score)}")
        if m.best_loser:
            lines.append(f"🛡 Best on losing side: {who(m.best_loser, heroes, friends)} — {round(m.best_loser.score)}")
        for r in m.threw[:2]:
            blame = f" · {round(r.blame * 100)}% of the blame" if r.blame else ""
            lines.append(f"💀 Threw?: {who(r, heroes, friends)} — {round(r.score)}{blame}")

    if not m.parsed:
        lines.append(
            "\n⚠️ <i>Replay not parsed yet, so roles and some stats may be wrong. "
            "Give it a few minutes, then tap 🔄 Refresh.</i>"
        )
    return "\n".join(lines)


def all_players(m: MatchRating, heroes: dict[int, str], friends: dict[int, str]) -> str:
    """Two compact monospace tables, one per team."""
    lines = [header(m), ""]
    for radiant in (True, False):
        team = sorted((r for r in m.players if r.is_radiant == radiant), key=lambda r: r.position)
        won = team[0].won if team else False
        lines.append(f"<b>{'Radiant' if radiant else 'Dire'}</b> {'✅' if won else '❌'}")
        rows = []
        for r in team:
            hero = short(heroes.get(r.hero_id, f"#{r.hero_id}"), 11)
            mark = "▸" if r.account_id and r.account_id in friends else " "
            rows.append(f"{mark}{r.position} {hero:<11} {r.kda:>8} {round(r.score):>3} {VERDICT_EMOJI.get(r.verdict, '')}")
        lines.append("<pre>" + escape("\n".join(rows)) + "</pre>")
    lines.append("<i>▸ = friend · number = score (50 is normal)</i>")
    return "\n".join(lines)


def why(m: MatchRating, r: PlayerRating, heroes: dict[int, str], friends: dict[int, str]) -> str:
    """Full breakdown of one player's score."""
    lines = [
        f"🔍 {who(r, heroes, friends)} · pos {r.position} · {r.kda} · {'won' if r.won else 'lost'}",
        f"Score <b>{round(r.score)}</b>/100 · {verdict_tag(r)} · ruin-meter {signed(r.ruin_meter)}",
        "",
    ]
    for c in sorted(r.components, key=lambda c: -c.weight):
        flag = " ⚠️" if c.key in r.failures else ""
        lines.append(f"<b>{c.label}</b> {round(c.score)} <i>(weight {c.weight}%)</i>{flag}")
        lines.append(f"  {escape(c.detail)}")

    if r.skipped:
        names = ", ".join(COMPONENT_LABELS[k] for k in r.skipped)
        lines.append(f"\n<i>Not counted (no data yet): {names}. Weights rescaled.</i>")

    pos_note = "from lanes in the replay" if r.position_source == "lanes" else "guessed from last hits (no replay yet)"
    lines.append(f"<i>Position {pos_note}.</i>")

    if r.verdict == "Threw?":
        lines.append(
            f"<i>“Threw?” = lost, score under 30 and {len(r.failures)} clear failures (⚠️ = component under 20).</i>"
        )
    elif not r.won and r.blame is not None:
        lines.append(f"<i>Share of the loss: {round(r.blame * 100)}%.</i>")
    return "\n".join(lines)


def scoring_help() -> str:
    return (
        "<b>How scores work</b>\n"
        "Each player gets 0–100. <b>50 = normal for that hero in that position.</b> "
        "Ruin-meter is the same thing on a −1…+1 scale.\n\n"
        "Score = weighted average of:\n"
        "• <b>Farm</b> – GPM/XPM/LH vs. other games on that hero\n"
        "• <b>Hero damage</b> – vs. other games on that hero\n"
        "• <b>Kill participation</b> – vs. normal for the position\n"
        "• <b>Deaths</b> per 10 min – vs. normal for the position\n"
        "• <b>Buildings</b> – tower damage vs. that hero\n"
        "• <b>Stuns & healing</b> – stuns need a parsed replay\n"
        "• <b>Vision</b> – wards + dewards, parsed replay only\n\n"
        "<pre>"
        "     farm dmg kp  dth bld imp vis\n"
        "pos1  30  20  10  20  15   5   0\n"
        "pos2  25  25  15  20  10   5   0\n"
        "pos3  15  15  20  20  10  20   0\n"
        "pos4   5  15  25  15   5  20  15\n"
        "pos5   0  10  25  15   5  20  25"
        "</pre>\n"
        "<b>Threw?</b> only if you lost, scored under 30 <i>and</i> had 2+ components under 20. "
        "A bad KDA alone isn't enough.\n"
        "Tap <b>Why?</b> on any match to see the exact numbers."
    )


def profile(name: str, ratings: list[tuple[MatchRating, PlayerRating]], heroes: dict[int, str]) -> str:
    if not ratings:
        return f"<b>{escape(name)}</b>\nNo recent matches found."
    n = len(ratings)
    wins = sum(1 for _, r in ratings if r.won)
    avg = sum(r.score for _, r in ratings) / n
    meter = (avg - 50) / 50
    best_m, best = max(ratings, key=lambda t: t[1].score)
    worst_m, worst = min(ratings, key=lambda t: t[1].score)
    # newest on the right, like a chart
    strip = "".join("🟩" if r.score >= 60 else "🟥" if r.score < 40 else "⬜" for _, r in reversed(ratings))

    lines = [
        f"<b>{escape(name)}</b> · last {n} games",
        f"Ruin-meter <b>{signed(meter)}</b> · avg score {round(avg)} · {wins}–{n - wins}",
        strip,
        "",
        f"Best: {escape(heroes.get(best.hero_id, '?'))} {best.kda} — {round(best.score)} {verdict_tag(best)} "
        f"(<code>{best_m.match_id}</code>)",
        f"Worst: {escape(heroes.get(worst.hero_id, '?'))} {worst.kda} — {round(worst.score)} {verdict_tag(worst)} "
        f"(<code>{worst_m.match_id}</code>)",
    ]
    by_hero: dict[int, list[PlayerRating]] = {}
    for _, r in ratings:
        by_hero.setdefault(r.hero_id, []).append(r)
    top = sorted(by_hero.items(), key=lambda kv: (-len(kv[1]), -sum(x.score for x in kv[1])))[:3]
    if top:
        parts = []
        for hid, rs in top:
            w = sum(1 for x in rs if x.won)
            parts.append(f"{escape(heroes.get(hid, '?'))} {w}–{len(rs) - w} ({round(sum(x.score for x in rs) / len(rs))})")
        lines.append("Heroes: " + " · ".join(parts))
    lines.append("<i>🟩 60+ · ⬜ 40–60 · 🟥 under 40, newest on the right</i>")
    return "\n".join(lines)


def week(rows: list[dict], heroes: dict[int, str], days: int) -> str:
    """rows: [{name, games:[(MatchRating, PlayerRating)]}] for chat members with games."""
    rows = [r for r in rows if r["games"]]
    if not rows:
        return f"Nobody here played in the last {days} days. Or nobody ran /link yet."

    table = []
    for r in rows:
        games = r["games"]
        n = len(games)
        w = sum(1 for _, p in games if p.won)
        avg = sum(p.score for _, p in games) / n
        table.append({"name": r["name"], "n": n, "w": w, "avg": avg, "games": games})
    table.sort(key=lambda t: -t["avg"])

    match_ids = {m.match_id for t in table for m, _ in t["games"]}
    wins = {m.match_id for t in table for m, p in t["games"] if p.won}
    lines = [
        f"📅 <b>Last {days} days</b> · {len(match_ids)} matches · {len(wins)} won",
        "",
    ]
    rank_icons = ["🥇", "🥈", "🥉"]
    for i, t in enumerate(table):
        icon = rank_icons[i] if i < 3 else f"{i + 1}."
        meter = signed((t["avg"] - 50) / 50)
        lines.append(f"{icon} <b>{escape(t['name'])}</b> {meter} · {t['w']}–{t['n'] - t['w']}")

    eligible = [t for t in table if t["n"] >= 2] or table
    if len(eligible) >= 2:
        lines.append("")
        lines.append(f"🔥 Carry of the week: <b>{escape(eligible[0]['name'])}</b>")
        lines.append(f"💀 Ruiner of the week: <b>{escape(eligible[-1]['name'])}</b>")

    every = [(t["name"], m, p) for t in table for m, p in t["games"]]
    if every:
        name, m, p = max(every, key=lambda x: x[2].score)
        lines.append(f"🏆 Game of the week: {escape(name)} · {escape(heroes.get(p.hero_id, '?'))} {p.kda} — {round(p.score)}")
        name, m, p = min(every, key=lambda x: x[2].score)
        lines.append(f"🤡 Shame of the week: {escape(name)} · {escape(heroes.get(p.hero_id, '?'))} {p.kda} — {round(p.score)}")
    return "\n".join(lines)


def ago(ts: int, now: float | None = None) -> str:
    sec = max(0, int((now or time.time()) - ts))
    if sec < 3600:
        return f"{max(1, sec // 60)}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    days = sec // 86400
    return "yesterday" if days == 1 else f"{days}d ago"


def history(name: str, games: list[tuple[MatchRating, PlayerRating]], heroes: dict[int, str]) -> str:
    """One line per game. Numbered so the buttons below can open each one."""
    if not games:
        return f"<b>{escape(name)}</b>\nNo recent matches found. Is Expose Public Match Data on?"
    n = len(games)
    wins = sum(1 for _, r in games if r.won)
    avg = sum(r.score for _, r in games) / n
    lines = [f"📜 <b>{escape(name)}</b> · last {n} games · {wins}–{n - wins} · avg {round(avg)}", ""]
    for i, (m, r) in enumerate(games, 1):
        hero = escape(heroes.get(r.hero_id, f"Hero #{r.hero_id}"))
        lines.append(
            f"{i}. {'✅' if r.won else '❌'} {hero} {r.kda} · <b>{round(r.score)}</b> {verdict_tag(r)} · {ago(m.start_time)}"
        )
    return "\n".join(lines)


def our_history(items: list, heroes: dict[int, str], friends: dict[int, str], title: str) -> str:
    """items: [(GroupMatch, MatchRating | None)]. One block per match."""
    if not items:
        return "No games with 2+ of you together in everyone's last 20 matches."
    lines = [f"👥 <b>{title}</b>", ""]
    for i, (g, m) in enumerate(items, 1):
        if m is None:
            names = ", ".join(escape(f.name) for f in g.friends)
            lines.append(f"{i}. ⏳ {names} · {ago(g.start_time)} · not on OpenDota yet")
            continue
        ours = [r for r in m.players if r.account_id in friends]
        sides = {r.won for r in ours}
        result = "⚔️" if len(sides) > 1 else ("✅" if ours and ours[0].won else "❌")
        lines.append(f"{i}. {result} {duration(m.duration)} · {ago(m.start_time)}")
        parts = [f"{escape(friends[r.account_id])} {round(r.score)}{VERDICT_EMOJI.get(r.verdict, '')}" for r in sorted(ours, key=lambda r: -r.score)]
        lines.append("    " + " · ".join(parts))
    return "\n".join(lines)
