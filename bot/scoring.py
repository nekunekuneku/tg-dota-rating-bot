"""Match rating engine.

Pure functions only: takes an OpenDota /matches/{id} JSON blob, returns a
rating for each of the 10 players. No network, no Telegram. Read
docs/SCORING.md for the plain-language version.

Every player gets a 0-100 score where 50 means "normal for this hero in
this position". It's a weighted average of a few components, and the
weights depend on position (a pos 5 isn't judged on farm, a pos 1 isn't
judged on wards).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Tunables. Everything that decides a verdict lives here.
# ---------------------------------------------------------------------------

# Component weights per position (each row sums to 100).
WEIGHTS: dict[int, dict[str, int]] = {
    #     farm dmg  kp  deaths bld impact vision
    1: dict(farm=30, damage=20, kp=10, deaths=20, buildings=15, impact=5, vision=0),
    2: dict(farm=25, damage=25, kp=15, deaths=20, buildings=10, impact=5, vision=0),
    3: dict(farm=15, damage=15, kp=20, deaths=20, buildings=10, impact=20, vision=0),
    4: dict(farm=5, damage=15, kp=25, deaths=15, buildings=5, impact=20, vision=15),
    5: dict(farm=0, damage=10, kp=25, deaths=15, buildings=5, impact=20, vision=25),
}

# Expected kill participation, (kills + assists) / team kills.
KP_NORM = {1: 0.55, 2: 0.60, 3: 0.55, 4: 0.60, 5: 0.60}
KP_SPREAD = 0.15

# Expected deaths per 10 minutes.
DEATHS_NORM = {1: 1.4, 2: 1.7, 3: 2.0, 4: 2.4, 5: 2.4}
DEATHS_SPREAD = 0.9

# Expected wards placed (obs + sentries) per 10 minutes. Dewards count double.
VISION_NORM = {1: 0.3, 2: 0.3, 3: 0.6, 4: 3.5, 5: 6.0}

# Expected stun duration, seconds of disable per minute of game.
STUN_NORM = 1.0
STUN_SPREAD = 1.0

# A component this low with at least this much weight is a "clear failure".
FAILURE_SCORE = 20
FAILURE_MIN_WEIGHT = 10

# "Threw?" needs: lost, overall score under this, and this many clear failures.
THREW_SCORE = 30
THREW_FAILURES = 2

# Blame on the losing team is shared by how far each player is under this.
BLAME_BASELINE = 55

COMPONENT_LABELS = {
    "farm": "Farm",
    "damage": "Hero damage",
    "kp": "Kill participation",
    "deaths": "Deaths",
    "buildings": "Buildings",
    "impact": "Stuns & healing",
    "vision": "Vision",
}

LANE_NAMES = {1: "safe", 2: "mid", 3: "off", 4: "jungle"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Component:
    key: str
    score: float  # 0..100, 50 = normal
    weight: int  # from WEIGHTS for this player's position
    detail: str  # human-readable "why"
    stat: str = ""  # short version for the match card, e.g. "14 deaths, 3.5 per 10 min"

    @property
    def label(self) -> str:
        return COMPONENT_LABELS[self.key]


@dataclass
class PlayerRating:
    slot: int  # player_slot from OpenDota (0-4 radiant, 128-132 dire)
    account_id: int | None
    hero_id: int
    is_radiant: bool
    won: bool
    position: int  # 1..5, inferred
    position_source: str  # "lanes" or "net worth"
    kills: int
    deaths: int
    assists: int
    score: float
    components: list[Component]
    skipped: list[str]  # component keys that had weight but no data
    failures: list[str]  # component keys that were clear failures
    verdict: str = "OK"
    blame: float | None = None  # share of the losing team's blame, 0..1
    name: str | None = None  # in-game persona name if public

    @property
    def ruin_meter(self) -> float:
        """Score mapped to -1..+1, 0 = normal for the position."""
        return round((self.score - 50) / 50, 2)

    @property
    def kda(self) -> str:
        return f"{self.kills}/{self.deaths}/{self.assists}"

    def best_stat(self) -> Component | None:
        """The component that helped this score most, given the role weights."""
        if not self.components:
            return None
        return max(self.components, key=lambda c: (c.score - 50) * c.weight)

    def worst_stat(self) -> Component | None:
        """The component that hurt this score most, given the role weights."""
        if not self.components:
            return None
        return min(self.components, key=lambda c: (c.score - 50) * c.weight)


@dataclass
class MatchRating:
    match_id: int
    radiant_win: bool
    duration: int  # seconds
    radiant_score: int
    dire_score: int
    start_time: int
    game_mode: int
    lobby_type: int
    parsed: bool
    players: list[PlayerRating]
    mvp: PlayerRating | None = None
    best_loser: PlayerRating | None = None
    threw: list[PlayerRating] = field(default_factory=list)
    confidence: float = 1.0  # share of weight that had data, averaged

    def player_by_account(self, account_id: int) -> PlayerRating | None:
        for p in self.players:
            if p.account_id == account_id:
                return p
        return None

    def player_by_slot(self, slot: int) -> PlayerRating | None:
        for p in self.players:
            if p.slot == slot:
                return p
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def around_norm(value: float, norm: float, spread: float, higher_is_better: bool = True) -> float:
    """Map a raw stat to 0..100 where `norm` -> 50.

    One `spread` above the norm is about 88, one below is about 12.
    """
    if spread <= 0:
        return 50.0
    z = (value - norm) / spread
    if not higher_is_better:
        z = -z
    return clamp(50 + 50 * math.tanh(z))


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _bench_pct(player: dict, key: str) -> float | None:
    """OpenDota benchmark percentile (0..1) of this stat vs. the same hero."""
    b = (player.get("benchmarks") or {}).get(key)
    if not b or b.get("pct") is None:
        return None
    return float(b["pct"])


def _bench_raw(player: dict, key: str) -> float | None:
    b = (player.get("benchmarks") or {}).get(key)
    if not b or b.get("raw") is None:
        return None
    return float(b["raw"])


def is_parsed(match: dict) -> bool:
    if match.get("version") is not None:
        return True
    return bool((match.get("od_data") or {}).get("has_parsed"))


def is_radiant(player: dict) -> bool:
    if "isRadiant" in player:
        return bool(player["isRadiant"])
    return player.get("player_slot", 0) < 128


def _net_worth(player: dict, minutes: float) -> float:
    for key in ("net_worth", "total_gold"):
        if player.get(key):
            return float(player[key])
    return float(player.get("gold_per_min") or 0) * minutes


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


def _core_signal(p: dict, minutes: float) -> tuple[float, float]:
    """How much this player farmed creeps - the best tell for core vs support.

    Last hits at 10 min (parsed replays) beat total last hits, and both beat
    net worth: kill gold inflates net worth, so a support who gets lots of
    kills looks "richer" than the core in their lane. Net worth only breaks ties.
    """
    lh_t = p.get("lh_t") or []
    lh = lh_t[10] if len(lh_t) > 10 else (p.get("last_hits") or 0)
    return (float(lh), _net_worth(p, minutes))


def infer_positions(team: list[dict], minutes: float) -> dict[int, tuple[int, str]]:
    """Return {player_slot: (position, source)} for one team.

    With a parsed replay we know lanes: mid -> 2; in the safe lane the player
    with more last hits is pos 1, the other pos 5; in the off lane, more last
    hits is pos 3, the other pos 4.
    If lanes are missing or weird (trilanes, jungle), order the team by last
    hits: most = pos 1, fewest = pos 5. That can swap pos 1 and 2, but those
    weights are close enough that it rarely changes the verdict.
    """
    by_farm = sorted(team, key=lambda p: _core_signal(p, minutes), reverse=True)
    lanes = [p.get("lane_role") for p in team]

    if len(team) == 5 and all(lane in (1, 2, 3) for lane in lanes):
        mid = [p for p in by_farm if p["lane_role"] == 2]
        safe = [p for p in by_farm if p["lane_role"] == 1]
        off = [p for p in by_farm if p["lane_role"] == 3]
        if len(mid) == 1 and len(safe) == 2 and len(off) == 2:
            return {
                mid[0]["player_slot"]: (2, "lanes"),
                safe[0]["player_slot"]: (1, "lanes"),
                safe[1]["player_slot"]: (5, "lanes"),
                off[0]["player_slot"]: (3, "lanes"),
                off[1]["player_slot"]: (4, "lanes"),
            }

    return {p["player_slot"]: (min(i + 1, 5), "last hits") for i, p in enumerate(by_farm)}


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def _pct_text(pct: float) -> str:
    return f"better than {round(pct * 100)}% of games on this hero"


def _rank(pct: float) -> str:
    """0.92 -> 'top 8% for the hero', 0.1 -> 'bottom 10% for the hero'."""
    if pct >= 0.5:
        return f"top {max(1, round((1 - pct) * 100))}% for the hero"
    return f"bottom {max(1, round(pct * 100))}% for the hero"


def _k(x: float) -> str:
    return f"{x / 1000:.1f}k" if x >= 1000 else f"{round(x)}"


def _farm(p: dict, pos: int) -> Component | None:
    keys = ["gold_per_min", "xp_per_min"] + (["last_hits_per_min"] if pos <= 3 else [])
    pcts = [_bench_pct(p, k) for k in keys]
    pcts = [x for x in pcts if x is not None]
    if not pcts:
        return None
    avg = sum(pcts) / len(pcts)
    detail = f"GPM {p.get('gold_per_min', 0)}, XPM {p.get('xp_per_min', 0)}"
    if pos <= 3:
        detail += f", {p.get('last_hits', 0)} LH"
    detail += f" - {_pct_text(avg)}"
    stat = f"{p.get('gold_per_min', 0)} GPM, farm in the {_rank(avg)}"
    return Component("farm", avg * 100, 0, detail, stat)


def _damage(p: dict, pos: int) -> Component | None:
    pct = _bench_pct(p, "hero_damage_per_min")
    if pct is None:
        return None
    raw = _bench_raw(p, "hero_damage_per_min")
    dmg = p.get("hero_damage") or 0
    detail = f"{dmg:,} hero damage"
    if raw is not None:
        detail += f" ({round(raw)}/min)"
    detail += f" - {_pct_text(pct)}"
    return Component("damage", pct * 100, 0, detail, f"{_k(dmg)} hero damage, {_rank(pct)}")


def _buildings(p: dict, pos: int) -> Component | None:
    pct = _bench_pct(p, "tower_damage")
    if pct is None:
        return None
    td = p.get("tower_damage") or 0
    return Component(
        "buildings", pct * 100, 0, f"{td:,} building damage - {_pct_text(pct)}", f"{_k(td)} building damage, {_rank(pct)}"
    )


def _kp(p: dict, pos: int, team_kills: int) -> Component | None:
    if team_kills <= 0:
        return None
    kp = ((p.get("kills") or 0) + (p.get("assists") or 0)) / team_kills
    kp = min(kp, 1.0)
    norm = KP_NORM[pos]
    score = around_norm(kp, norm, KP_SPREAD)
    return Component(
        "kp",
        score,
        0,
        f"in {round(kp * 100)}% of team kills (normal for pos {pos}: {round(norm * 100)}%)",
        f"in {round(kp * 100)}% of team kills (pos {pos} normal: {round(norm * 100)}%)",
    )


def _deaths(p: dict, pos: int, minutes: float) -> Component | None:
    if minutes <= 0:
        return None
    d = p.get("deaths") or 0
    per10 = d / minutes * 10
    norm = DEATHS_NORM[pos]
    score = around_norm(per10, norm, DEATHS_SPREAD, higher_is_better=False)
    return Component(
        "deaths",
        score,
        0,
        f"{d} deaths = {per10:.1f} per 10 min (normal for pos {pos}: {norm})",
        f"{d} deaths, {per10:.1f} per 10 min (pos {pos} normal: {norm})",
    )


def _impact(p: dict, pos: int, minutes: float) -> Component | None:
    """Stuns (parsed replays only) and healing (only for heroes that heal)."""
    parts: list[tuple[float, str, str]] = []

    stuns = p.get("stuns")
    if stuns is not None and minutes > 0:
        per_min = float(stuns) / minutes
        parts.append(
            (
                around_norm(per_min, STUN_NORM, STUN_SPREAD),
                f"{float(stuns):.0f}s of stuns ({per_min:.1f}s/min)",
                f"{float(stuns):.0f}s of stuns",
            )
        )

    heal_raw = _bench_raw(p, "hero_healing_per_min")
    heal_pct = _bench_pct(p, "hero_healing_per_min")
    # Healing only counts for heroes that actually heal - otherwise a
    # 0-healing Sniper would get a random percentile.
    if heal_pct is not None and heal_raw is not None and heal_raw >= 30:
        heal = p.get("hero_healing") or 0
        parts.append((heal_pct * 100, f"{heal:,} healing - {_pct_text(heal_pct)}", f"{_k(heal)} healing, {_rank(heal_pct)}"))

    if not parts:
        return None
    score = sum(s for s, _, _ in parts) / len(parts)
    return Component("impact", score, 0, "; ".join(d for _, d, _ in parts), ", ".join(st for _, _, st in parts))


def _vision(p: dict, pos: int, minutes: float) -> Component | None:
    obs, sen = p.get("obs_placed"), p.get("sen_placed")
    if obs is None and sen is None:
        return None  # not parsed
    if minutes <= 0:
        return None
    obs, sen = obs or 0, sen or 0
    dewards = (p.get("observer_kills") or 0) + (p.get("sentry_kills") or 0)
    per10 = (obs + sen + 2 * dewards) / minutes * 10
    norm = VISION_NORM[pos]
    score = around_norm(per10, norm, max(norm * 0.6, 0.5))
    return Component(
        "vision",
        score,
        0,
        f"{obs} obs, {sen} sentries, {dewards} dewards = {per10:.1f}/10 min (normal for pos {pos}: {norm})",
        f"{obs + sen} wards, {dewards} dewards",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def rate_player(p: dict, pos: int, minutes: float, team_kills: int) -> tuple[float, list[Component], list[str], float]:
    """Return (score, components, skipped_keys, coverage)."""
    weights = WEIGHTS[pos]
    builders = {
        "farm": lambda: _farm(p, pos),
        "damage": lambda: _damage(p, pos),
        "kp": lambda: _kp(p, pos, team_kills),
        "deaths": lambda: _deaths(p, pos, minutes),
        "buildings": lambda: _buildings(p, pos),
        "impact": lambda: _impact(p, pos, minutes),
        "vision": lambda: _vision(p, pos, minutes),
    }
    comps: list[Component] = []
    skipped: list[str] = []
    for key, build in builders.items():
        w = weights[key]
        if w == 0:
            continue
        c = build()
        if c is None:
            skipped.append(key)
            continue
        c.weight = w
        c.score = round(clamp(c.score), 1)
        comps.append(c)

    total_w = sum(c.weight for c in comps)
    if total_w == 0:
        return 50.0, comps, skipped, 0.0
    score = sum(c.score * c.weight for c in comps) / total_w
    return round(score, 1), comps, skipped, total_w / 100


def verdict_for(r: PlayerRating) -> str:
    s = r.score
    if not r.won and s < THREW_SCORE and len(r.failures) >= THREW_FAILURES:
        return "Threw?"
    if s >= 75:
        return "Carried" if r.won else "Tried hard"
    if s >= 60:
        return "Good"
    if s >= 40:
        return "OK"
    if r.won and s < 35:
        return "Got carried"
    if s >= 25:
        return "Weak"
    return "Bad"


def rate_match(match: dict) -> MatchRating:
    duration = int(match.get("duration") or 0)
    minutes = duration / 60 if duration else 0.0
    radiant_win = bool(match.get("radiant_win"))
    players_raw: list[dict] = match.get("players") or []

    radiant = [p for p in players_raw if is_radiant(p)]
    dire = [p for p in players_raw if not is_radiant(p)]
    positions = {**infer_positions(radiant, minutes), **infer_positions(dire, minutes)}

    team_kills = {
        True: int(match.get("radiant_score") or sum(p.get("kills") or 0 for p in radiant)),
        False: int(match.get("dire_score") or sum(p.get("kills") or 0 for p in dire)),
    }

    ratings: list[PlayerRating] = []
    coverages: list[float] = []
    for p in players_raw:
        rad = is_radiant(p)
        pos, src = positions[p["player_slot"]]
        score, comps, skipped, coverage = rate_player(p, pos, minutes, team_kills[rad])
        coverages.append(coverage)
        failures = [c.key for c in comps if c.score < FAILURE_SCORE and c.weight >= FAILURE_MIN_WEIGHT]
        r = PlayerRating(
            slot=p["player_slot"],
            account_id=p.get("account_id"),
            hero_id=int(p.get("hero_id") or 0),
            is_radiant=rad,
            won=(rad == radiant_win),
            position=pos,
            position_source=src,
            kills=int(p.get("kills") or 0),
            deaths=int(p.get("deaths") or 0),
            assists=int(p.get("assists") or 0),
            score=score,
            components=comps,
            skipped=skipped,
            failures=failures,
            name=p.get("personaname"),
        )
        r.verdict = verdict_for(r)
        ratings.append(r)

    # Blame split on the losing team.
    losers = [r for r in ratings if not r.won]
    deficits = {r.slot: max(0.0, BLAME_BASELINE - r.score) for r in losers}
    total_def = sum(deficits.values())
    if total_def > 0:
        for r in losers:
            r.blame = round(deficits[r.slot] / total_def, 2)

    winners = [r for r in ratings if r.won]
    mr = MatchRating(
        match_id=int(match.get("match_id") or 0),
        radiant_win=radiant_win,
        duration=duration,
        radiant_score=team_kills[True],
        dire_score=team_kills[False],
        start_time=int(match.get("start_time") or 0),
        game_mode=int(match.get("game_mode") or 0),
        lobby_type=int(match.get("lobby_type") or 0),
        parsed=is_parsed(match),
        players=ratings,
        confidence=round(sum(coverages) / len(coverages), 2) if coverages else 0.0,
    )
    mr.mvp = max(winners, key=lambda r: r.score) if winners else None
    mr.best_loser = max(losers, key=lambda r: r.score) if losers else None
    mr.threw = sorted([r for r in losers if r.verdict == "Threw?"], key=lambda r: r.score)
    return mr
