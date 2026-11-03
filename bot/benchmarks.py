"""Fallback for matches where OpenDota didn't attach `benchmarks` to players.

OpenDota's /benchmarks?hero_id=X returns, per stat, a list of
{percentile, value} points across recent public games on that hero. We
compute the same {raw, pct} shape that /matches puts on each player so the
scorer doesn't care where the numbers came from.
"""

from __future__ import annotations

BENCH_KEYS = (
    "gold_per_min",
    "xp_per_min",
    "last_hits_per_min",
    "hero_damage_per_min",
    "hero_healing_per_min",
    "tower_damage",
)


def raw_stats(player: dict, minutes: float) -> dict[str, float]:
    m = minutes or 1.0
    return {
        "gold_per_min": float(player.get("gold_per_min") or 0),
        "xp_per_min": float(player.get("xp_per_min") or 0),
        "last_hits_per_min": float(player.get("last_hits") or 0) / m,
        "hero_damage_per_min": float(player.get("hero_damage") or 0) / m,
        "hero_healing_per_min": float(player.get("hero_healing") or 0) / m,
        "tower_damage": float(player.get("tower_damage") or 0),
    }


def percentile_of(value: float, points: list[dict]) -> float | None:
    """Linear interpolation over [{percentile, value}, ...] sorted by percentile."""
    pts = sorted(
        ((float(p["percentile"]), float(p["value"])) for p in points if p.get("value") is not None),
        key=lambda t: t[0],
    )
    if not pts:
        return None
    first_p, first_v = pts[0]
    if value <= first_v:
        # Below the lowest listed point: scale linearly down towards 0.
        return first_p * value / first_v if first_v > 0 else first_p
    for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
        if value <= v1:
            if v1 == v0:
                return p1
            return p0 + (p1 - p0) * (value - v0) / (v1 - v0)
    return 1.0


def needs_benchmarks(match: dict) -> set[int]:
    """Hero ids whose players are missing benchmarks."""
    return {int(p.get("hero_id") or 0) for p in match.get("players") or [] if not p.get("benchmarks")}


def fill_benchmarks(match: dict, by_hero: dict[int, dict]) -> None:
    """Mutates match: adds players[i].benchmarks where missing."""
    minutes = (match.get("duration") or 0) / 60
    for p in match.get("players") or []:
        if p.get("benchmarks"):
            continue
        result = (by_hero.get(int(p.get("hero_id") or 0)) or {}).get("result") or {}
        if not result:
            continue
        raws = raw_stats(p, minutes)
        bench = {}
        for key in BENCH_KEYS:
            pct = percentile_of(raws[key], result.get(key) or [])
            if pct is not None:
                bench[key] = {"raw": raws[key], "pct": round(pct, 3)}
        if bench:
            p["benchmarks"] = bench
