"""Funny one-liners for stats, built from the real numbers.

Each component has a few "bad" and "good" lines. One is picked per
(match, player, stat) with a stable hash, so the same card always says the
same thing (refreshing doesn't reshuffle the jokes). Edit freely: every
{name} must be a key in that component's `values` (see scoring.py).
"""

from __future__ import annotations

import zlib

from .scoring import Component

BAD: dict[str, list[str]] = {
    "deaths": [
        "wasn't playing for {dead_min} minutes ({deaths} deaths)",
        "spent {dead_min} minutes staring at a grey screen ({deaths} deaths)",
        "dead for {dead_pct} of the game ({deaths} deaths)",
        "{deaths} deaths, {dead_min} minutes dead. The enemy's favourite ATM",
    ],
    "kp": [
        "in {kp}% of team kills. Were they even in this game?",
        "missed {missed}% of the fights. Farming? AFK? Both?",
        "{kp}% kill participation. The team fought alone",
    ],
    "farm": [
        "{gpm} GPM. The creeps lived long, happy lives",
        "{gpm} GPM, {rank}. Farming like it's their first game",
        "{gpm} GPM. Somehow poorer than the courier",
    ],
    "damage": [
        "{dmg} hero damage. The enemy barely noticed",
        "{dmg} hero damage, {rank}. Hits like a wet noodle",
        "{dmg} hero damage. Were the spells on cooldown all game?",
    ],
    "buildings": [
        "{td} building damage. Towers felt completely safe",
        "{td} building damage. Buildings? Never heard of them",
    ],
    "impact_stun": [
        "{stuns}s of stuns. The enemy walked around freely",
        "{stuns}s of stuns. Control? Never heard of it",
    ],
    "impact_heal": [
        "{heal} healing, {heal_rank}. The team was on their own",
        "{heal} healing. Forgot they had a heal",
    ],
    "impact": [
        "{stat}. Zero control, zero sustain",
    ],
    "vision": [
        "{wards} wards. Playing in the dark",
        "{wards} wards placed. The map stayed a mystery",
    ],
}

GOOD: dict[str, list[str]] = {
    "deaths": [
        "only {deaths} deaths. Basically immortal",
        "dead for just {dead_min} minutes. Unkillable",
    ],
    "kp": [
        "in {kp}% of team kills. Everywhere at once",
        "{kp}% kill participation. Never missed a fight",
    ],
    "farm": [
        "{gpm} GPM, {rank}. Ate the whole map",
        "{gpm} GPM. The jungle is empty now",
    ],
    "damage": [
        "{dmg} hero damage, {rank}. The enemy felt every hit",
        "{dmg} hero damage. Pure violence",
    ],
    "buildings": [
        "{td} building damage. Towers fell like dominoes",
        "{td} building damage. Hated buildings personally",
    ],
    "impact_stun": [
        "{stuns}s of stuns. Nobody moved",
        "{stuns}s of stuns. The enemy spent the game standing still",
    ],
    "impact_heal": [
        "{heal} healing, {heal_rank}. The team's personal fountain",
        "{heal} healing. Kept everyone alive",
    ],
    "impact": [
        "{stat}. Carried the fights",
    ],
    "vision": [
        "{wards} wards, {dewards} dewards. The map was an open book",
        "{wards} wards. Saw everything",
    ],
}


def _pick(options: list[str], seed: str) -> str:
    return options[zlib.crc32(seed.encode()) % len(options)]


def line(c: Component, good: bool, seed: str) -> str:
    """A joke for this stat; falls back to the plain stat text if a value is missing."""
    key = c.key
    if key == "impact" and not ("stuns" in c.values and "heal" in c.values):
        key = "impact_stun" if "stuns" in c.values else "impact_heal"
    options = (GOOD if good else BAD).get(key)
    if not options:
        return c.stat
    try:
        return _pick(options, f"{seed}:{c.key}:{good}").format(stat=c.stat, **c.values)
    except (KeyError, IndexError):
        return c.stat
