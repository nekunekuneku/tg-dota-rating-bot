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


def line(c: Component, good: bool, seed: str, hero: str | None = None) -> str:
    """A joke for this stat; falls back to the plain stat text if none fits."""
    key = c.key
    if key == "impact" and not ("stuns" in c.values and "heal" in c.values):
        key = "impact_stun" if "stuns" in c.values else "impact_heal"
    values = {**c.values, "stat": c.stat}
    if hero:
        values["hero"] = hero
    options = usable((GOOD if good else BAD).get(key, []), values)
    if not options:
        return c.stat
    text = options[zlib.crc32(f"{seed}:{c.key}:{good}".encode()) % len(options)]
    return text.format(**values)
