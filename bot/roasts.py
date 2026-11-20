"""Funny one-liners for stats, built from the real numbers.

Each stat has "bad" and "good" lines. One is picked per (match, player,
stat) with a stable hash, so the same card always says the same thing
(refreshing doesn't reshuffle the jokes). Edit freely.

What a line can use:
- every stat: {kills} {deaths} {assists} {kda} {lh} {dn} {gpm} {xpm} {nw}
  {mins} (game length) {pos} {hero}
- deaths: {dead_min} {dead_pct}   (every bad deaths line should show one)
- kp: {kp} {missed} {team_kills}
- farm, damage, buildings: {rank}; damage: {dmg}; buildings: {td}
- impact_stun: {stuns}; impact_heal: {heal} {heal_rank}; impact: {stat}
- vision: {wards} {dewards}

A line can also be a (condition, text) pair, e.g.
    (lambda v: v["kills"] == 0, "{kda}. Not one kill")
and is only used when the condition is true. Lines whose values are missing
for a player are skipped.

PERSONAL holds lines for specific friends (by Dota friend ID). When one
fits, it's used instead of the stat joke about half the time. An optional
"name" overrides what {name} says in that friend's personal lines. Personal
lines can also use {name}, {stat_key} (deaths, farm, ...), {won}, {mvp}
and {ad} (Ability Draft game).
"""

from __future__ import annotations

import zlib
from string import Formatter

from .scoring import Component

BAD: dict[str, list] = {
    "deaths": [
        "wasn't playing for {dead_min} minutes ({deaths} deaths)",
        "spent {dead_min} minutes staring at a grey screen ({deaths} deaths)",
        "dead for {dead_pct} of the game ({deaths} deaths). Basically a spectator",
        "{deaths} deaths, {dead_min} minutes dead. The enemy's favourite ATM",
        "{deaths} deaths, {dead_min} minutes dead. Could've cooked dinner in that time",
        "dead for {dead_pct} of a {mins}-minute game. Respawn timer had more screen time than the hero",
        "{deaths} deaths ({dead_min} minutes dead). The enemy carry owes them a thank-you card",
        "{deaths} deaths, {dead_min} minutes dead. Should start paying rent in the fountain",
        "{kda}. That middle number isn't a high score. {dead_min} minutes dead",
        "{deaths} deaths. Gave away more gold than a charity, and spent {dead_min} minutes dead doing it",
        "dead for {dead_pct} of the game. Watched more Dota than they played",
        (lambda v: v["kills"] == 0, "{kda}. Zero kills, {deaths} deaths, {dead_min} minutes dead. Pure donation"),
        (lambda v: v["deaths"] >= 15, "{deaths} deaths, {dead_min} minutes dead. That's not feeding, that's catering"),
        (lambda v: "hero" in v, "{deaths} deaths on {hero}, {dead_min} minutes dead. {hero} deserved better"),
    ],
    "kp": [
        "in {kp}% of team kills. Were they even in this game?",
        "missed {missed}% of the fights. Farming? AFK? Both?",
        "{kp}% kill participation. The team fought alone",
        "{kp}% kill participation. The team played 4v5 and nobody noticed",
        "missed {missed}% of the fights. Heard about them in voice chat afterwards",
        "{kills} kills, {assists} assists out of {team_kills} team kills. A ghost",
        "in {kp}% of the fights. Thought this was a single-player game",
        "{kp}% kill participation. Probably tabbed out watching YouTube",
        "missed {missed}% of the fights. Has a restraining order against teamfights",
        (lambda v: v["kills"] + v["assists"] <= 3, "{kills} kills, {assists} assists in {mins} minutes. Was the account shared?"),
    ],
    "farm": [
        "{gpm} GPM. The creeps lived long, happy lives",
        "{gpm} GPM, {rank}. Farming like it's their first game",
        "{gpm} GPM. Somehow poorer than the courier",
        "{gpm} GPM, {rank}. Bots on easy farm better than this",
        "{lh} last hits in {mins} minutes. Creeps died of old age",
        "{lh} last hits, {dn} denies. Missed more creeps than they hit",
        "{nw} net worth after {mins} minutes. Broke and proud",
        "{gpm} GPM. Professionally poor",
        "{xpm} XPM. Level 6 was a late-game item",
        "{gpm} GPM. Every wave they touched was a crime scene",
        (lambda v: v["pos"] <= 3 and v["lh"] < 4 * v["mins"], "{lh} last hits in {mins} minutes as a core. A support could've done that"),
        (lambda v: "hero" in v, "{gpm} GPM on {hero}. That's not how {hero} works"),
    ],
    "damage": [
        "{dmg} hero damage. The enemy barely noticed",
        "{dmg} hero damage, {rank}. Hits like a wet noodle",
        "{dmg} hero damage. Were the spells on cooldown all game?",
        "{dmg} hero damage in {mins} minutes. Hits like a pillow",
        "{dmg} hero damage, {rank}. Pacifist run",
        "{dmg} hero damage. Enemy regen undid it all on the walk to lane",
        "{dmg} hero damage. Right-clicked a tree for {mins} minutes?",
        "{dmg} hero damage. The enemy's health bars thank them for their service",
        (lambda v: "hero" in v, "{dmg} hero damage on {hero}. Forgot {hero} has buttons"),
    ],
    "buildings": [
        "{td} building damage. Towers felt completely safe",
        "{td} building damage. Buildings? Never heard of them",
        "{td} building damage. Hit a tower once, by accident",
        "{td} building damage. Thinks the Ancient is decoration",
        "{td} building damage in {mins} minutes. Objectives are for nerds, apparently",
        "{td} building damage. The towers didn't know they were there",
    ],
    "impact_stun": [
        "{stuns}s of stuns. The enemy walked around freely",
        "{stuns}s of stuns. Control? Never heard of it",
        "{stuns}s of stuns in {mins} minutes. Every spell went into the trees",
        "{stuns}s of stuns. The enemy didn't feel a thing",
    ],
    "impact_heal": [
        "{heal} healing, {heal_rank}. The team was on their own",
        "{heal} healing. Forgot they had a heal",
        "{heal} healing, {heal_rank}. Watched the team die like it was Netflix",
        "{heal} healing. Healing is free and still went unused",
    ],
    "impact": [
        "{stat}. Zero control, zero sustain",
        "{stat}. Did nothing, and did it with confidence",
    ],
    "vision": [
        "{wards} wards. Playing in the dark",
        "{wards} wards placed. The map stayed a mystery",
        "{wards} wards in {mins} minutes. Kept them in the backpack as a souvenir",
        "{wards} wards. The team found the enemy by dying to them",
        "{wards} wards, {dewards} dewards. The map was a black hole",
        "{wards} wards. Vision diff, and it was them",
        (lambda v: v["pos"] >= 4, "{wards} wards as a pos {pos}. That's literally the job"),
    ],
}

GOOD: dict[str, list] = {
    "deaths": [
        "only {deaths} deaths. Basically immortal",
        "dead for just {dead_min} minutes. Unkillable",
        "{deaths} deaths in {mins} minutes. The enemy ran out of ideas",
        (lambda v: v["deaths"] == 0, "{kda}. Zero deaths. Built different"),
    ],
    "kp": [
        "in {kp}% of team kills. Everywhere at once",
        "{kp}% kill participation. Never missed a fight",
        "{kills} kills + {assists} assists out of {team_kills}. Main character",
    ],
    "farm": [
        "{gpm} GPM, {rank}. Ate the whole map",
        "{gpm} GPM. The jungle is empty now",
        "{lh} last hits. The creeps were scared",
        "{nw} net worth. Disgustingly rich",
        (lambda v: "hero" in v, "{gpm} GPM on {hero}. Textbook"),
    ],
    "damage": [
        "{dmg} hero damage, {rank}. The enemy felt every hit",
        "{dmg} hero damage. Pure violence",
        "{dmg} hero damage in {mins} minutes. The enemy team needs therapy",
    ],
    "buildings": [
        "{td} building damage. Towers fell like dominoes",
        "{td} building damage. Hated buildings personally",
        "{td} building damage. Demolition company",
    ],
    "impact_stun": [
        "{stuns}s of stuns. Nobody moved",
        "{stuns}s of stuns. The enemy spent the game standing still",
        "{stuns}s of stuns. The enemy team played in handcuffs",
    ],
    "impact_heal": [
        "{heal} healing, {heal_rank}. The team's personal fountain",
        "{heal} healing. Kept everyone alive",
        "{heal} healing. A walking fountain",
    ],
    "impact": [
        "{stat}. Carried the fights",
    ],
    "vision": [
        "{wards} wards, {dewards} dewards. The map was an open book",
        "{wards} wards. Saw everything",
        "{wards} wards. Legal map hack",
    ],
}


# For friends who played fine (score 50+) but still had a weakest area.
# Lighter than BAD. {label} is the stat's name, {c_score} its 0-100 score.
MEH: dict[str, list] = {
    "deaths": [
        "{deaths} deaths, {dead_min} minutes dead. The one thing to tidy up",
        "solid game, apart from {dead_min} minutes as a spectator",
        "died {deaths} times. Not a disaster, not a flex either",
        "{kda}. Fine, but the respawn timer got to know them",
    ],
    "kp": [
        "only in {kp}% of the fights. Show up more and it's a great game",
        "missed {missed}% of the fights. Good when there, just wasn't always there",
        "{kp}% kill participation. Fashionably late to every fight",
    ],
    "farm": [
        "{gpm} GPM. Fine, but the map had more gold on it",
        "{lh} last hits in {mins} minutes. Respectable, not scary",
        "farm was a bit light ({gpm} GPM). A few more camps and it's a great game",
    ],
    "damage": [
        "{dmg} hero damage, {rank}. Could've hit things harder",
        "damage was the soft spot: {dmg}. Present, not threatening",
        "{dmg} hero damage. The enemy barely noticed",
    ],
    "buildings": [
        "{td} building damage. The towers were never worried",
        "{td} tower damage. Buildings are allowed to be hit, you know",
    ],
    "impact_stun": [
        "{stuns}s of stuns. Could've locked down a bit more",
        "only {stuns}s of disable. The enemy mostly walked free",
    ],
    "impact_heal": [
        "{heal} healing. The team could've used a bit more love",
    ],
    "impact": [
        "impact was the soft spot: {stat}",
    ],
    "vision": [
        "{wards} wards. The map stayed a bit dark",
        "vision was light: {wards} wards, {dewards} dewards",
    ],
    # their worst stat was still above normal: nothing to roast
    "_fine": [
        "weakest area was {label}, and even that was fine ({stat})",
        "hard to nitpick. {label} was the 'worst' part: {stat}",
        "clean game. If you squint, {label} could be better ({stat})",
    ],
}


def meh(c: Component, seed: str, ctx: dict | None = None) -> str:
    """A light jab at the weakest stat of someone who played fine."""
    from .scoring import COMPONENT_LABELS

    key = c.key
    if key == "impact" and not ("stuns" in c.values and "heal" in c.values):
        key = "impact_stun" if "stuns" in c.values else "impact_heal"
    if c.score >= 50:
        key = "_fine"
    label = COMPONENT_LABELS.get(c.key, c.key).lower()
    short = c.stat.split(" (")[0]  # "7 deaths, 1.7 per 10 min (pos 2 normal: 1.7)" -> before the note
    values = {**(ctx or {}), **c.values, "stat": short, "label": label, "c_score": round(c.score)}
    options = usable(MEH.get(key, []), values)
    if not options:
        return f"weakest area: {label} ({c.stat})"
    return options[zlib.crc32(f"{seed}:{c.key}:meh".encode()) % len(options)].format(**values)


def _stat(*keys):
    return lambda v: v["stat_key"] in keys


ANDREW = {
    "bad": [
        (lambda v: not v["ad"], "{kda}. Go back to Ability Draft"),
        (lambda v: not v["ad"], "Go back to Ability Draft. Normal heroes are clearly too hard"),
        (lambda v: not v["ad"], "{hero} with its own spells was too much for {name}. Back to Ability Draft"),
        (lambda v: not v["ad"], "{name} queued normal Dota by mistake. Go back to Ability Draft"),
        (lambda v: v["ad"], "Bad even in Ability Draft. Nowhere left to go back to"),
        (lambda v: v["ad"], "Ability Draft, the one mode {name} is meant to be good at. Yikes"),
    ],
    "good": [
        (lambda v: v["ad"], "Ability Draft king. Never leave"),
        (lambda v: not v["ad"], "Played like it was Ability Draft. Finally"),
    ],
}

GS = {
    "bad": [
        "No MVP today. {name} is already typing why it's the team's fault",
        "{kda}. No MVP screenshot for the group chat this time",
        "Can't farm MVP from the grey screen, {name}",
        "{name} played for the MVP badge and got neither",
        (lambda v: not v["won"], "Lost, and no MVP to cry into. Rough day for {name}"),
    ],
    "good": [
        (lambda v: v["mvp"], "MVP again. {name} is already screenshotting it"),
        (lambda v: v["mvp"], "Another MVP for the trophy cabinet. The other four did the work"),
        (lambda v: v["mvp"], "MVP. Brace for {name} bringing it up for a week"),
        (lambda v: v["mvp"], "Played for the MVP, not the team. Worked though"),
        (lambda v: not v["won"], "Best on a losing team. {name} will still call it an MVP"),
        (lambda v: not v["mvp"], "Good game, but no MVP. {name} is devastated"),
    ],
}
GS_SMURF = {
    "name": "GS",  # same person: the jokes call the smurf GS too
    "bad": GS["bad"] + ["Logged into the smurf to farm MVPs. Still couldn't"],
    "good": GS["good"] + [(lambda v: v["mvp"], "Smurfing for MVPs. Classy")],
}

MIC = {
    "bad": [
        (_stat("farm"), "{gpm} GPM. Spent minute 30 clearing the small camp again"),
        (_stat("farm"), "{lh} last hits in {mins} minutes. Every small camp on the map was cleared, the game wasn't"),
        (_stat("kp"), "missed {missed}% of the fights. The small camp needed {name} more"),
        (_stat("kp", "damage"), "The team was fighting at the T3s. {name} was at the small camp"),
        (lambda v: not v["won"], "We lost while {name} farmed the small camp for the 40th time"),
        (_stat("buildings"), "{td} building damage. Towers aren't small camps, so {name} wasn't interested"),
    ],
    "good": [
        (_stat("farm"), "{gpm} GPM. All those small camps finally paid off"),
        "{name} left the small camp and won the game",
    ],
}

NICK = {
    "bad": [
        "Built a bot to flame the squad. The bot chose violence against its creator",
        "Ultron moment: {name}'s own bot is roasting {name}",
        "{kda}. {name} wrote the scoring code and still couldn't rig it",
        "No strings on this bot. {name} can't patch out the truth",
        "{name} made the bot to judge us. It judged {name} first",
    ],
    "good": [
        "The bot's creator gets a top score. Totally unbiased algorithm, trust",
        "{name} made the bot and the bot says {name} is great. Nothing suspicious here",
        "The bot spares its creator today. For now",
    ],
}

KAWCH = {
    "bad": [
        "{kda}. {name}'s head is still in Maple",
        "{name} was mentally grinding mobs in Henesys the whole game",
        (_stat("farm"), "{gpm} GPM. {name} farms better in Maple"),
        (_stat("kp"), "missed {missed}% of the fights. {name} was off doing a Maple boss run"),
        (lambda v: not v["won"], "Lost. {name} is already back on Maple"),
        "Spent the game scrolling gear in Maple. Both failed",
    ],
    "good": [
        (lambda v: v["mvp"], "Logged off Maple long enough to MVP"),
        "Imagine how good {name} would be without Maple",
    ],
}

PERSONAL: dict[int, dict[str, list]] = {
    100648413: ANDREW,
    103826341: GS,
    129229916: GS_SMURF,
    55636689: MIC,
    55775392: NICK,
    191871359: KAWCH,
}


def fields(text: str) -> set[str]:
    return {f for _, f, _, _ in Formatter().parse(text) if f}


def usable(options: list, values: dict) -> list[str]:
    """The lines whose condition holds and whose values are all present."""
    out = []
    for o in options:
        cond, text = o if isinstance(o, tuple) else (None, o)
        if not fields(text) <= values.keys():
            continue
        try:
            if cond and not cond(values):
                continue
        except (KeyError, TypeError):
            continue
        out.append(text)
    return out


def line(c: Component, good: bool, seed: str, ctx: dict | None = None) -> str:
    """A joke for this stat; falls back to the plain stat text if none fits.

    ctx: what else is known about the player (hero, name, account_id, won,
    mvp, ad). With personal=False in ctx, personal lines are skipped.
    """
    ctx = ctx or {}
    key = c.key
    if key == "impact" and not ("stuns" in c.values and "heal" in c.values):
        key = "impact_stun" if "stuns" in c.values else "impact_heal"
    values = {"won": False, "mvp": False, "ad": False, **ctx, **c.values, "stat": c.stat, "stat_key": c.key}
    pick = zlib.crc32(f"{seed}:{c.key}:{good}".encode())

    mine = PERSONAL.get(ctx.get("account_id")) if ctx.get("personal", True) else None
    if mine:
        values["name"] = mine.get("name", values.get("name"))
        options = usable(mine["good" if good else "bad"], values)
        if options and pick % 2 == 0:
            return options[(pick // 2) % len(options)].format(**values)

    options = usable((GOOD if good else BAD).get(key, []), values)
    if not options:
        return c.stat
    return options[pick % len(options)].format(**values)
