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


MIN_HERO_POINTS = 8  # fewer cached games than this on a hero: use the all-heroes curve


def curves_from_matches(matches: list[dict]) -> dict[int, dict]:
    """Benchmark curves rebuilt from matches OpenDota already gave us.

    Every OpenDota match has, per player, {stat: {raw, pct}}: their number and
    where it ranks for that hero. Enough of those per hero trace the same curve
    /benchmarks returns. Used when OpenDota is down and a match came from
    STRATZ without benchmarks. Returns {hero_id: {"result": {stat: points}}},
    with key 0 = all heroes together (rougher, but better than nothing).
    """
    pts: dict[int, dict[str, list[tuple[float, float]]]] = {}
    for m in matches:
        if m.get("source") == "stratz":
            continue
        for p in m.get("players") or []:
            for key, b in (p.get("benchmarks") or {}).items():
                if key in BENCH_KEYS and b and b.get("pct") is not None and b.get("raw") is not None:
                    pair = (float(b["pct"]), float(b["raw"]))
                    hero = int(p.get("hero_id") or 0)
                    if hero:
                        pts.setdefault(hero, {}).setdefault(key, []).append(pair)
                    pts.setdefault(0, {}).setdefault(key, []).append(pair)

    def curve(pairs: list[tuple[float, float]], bucketed: bool) -> list[dict]:
        pairs = sorted(pairs)
        if bucketed:  # many heroes mixed: the median value around each 5% step
            out = []
            for i in range(21):
                q = i / 20
                near = sorted(v for p, v in pairs if abs(p - q) <= 0.05)
                if near:
                    out.append((q, near[len(near) // 2]))
            pairs = out
        points, top = [], 0.0
        for p, v in pairs:
            top = max(top, v)  # a higher percentile never has a lower value
            points.append({"percentile": p, "value": top})
        return points

    out = {}
    for hero, stats in pts.items():
        result = {k: curve(v, bucketed=hero == 0) for k, v in stats.items() if hero == 0 or len(v) >= MIN_HERO_POINTS}
        if result:
            out[hero] = {"result": result}
    return out
