import copy

import pytest


def bench(gpm, xpm, lh, dmg, heal, td):
    """Benchmarks block in the shape OpenDota returns: {stat: {raw, pct}}."""
    return {
        "gold_per_min": {"raw": 0, "pct": gpm},
        "xp_per_min": {"raw": 0, "pct": xpm},
        "last_hits_per_min": {"raw": 0, "pct": lh},
        "hero_damage_per_min": {"raw": 500, "pct": dmg},
        "hero_healing_per_min": {"raw": heal[0], "pct": heal[1]},
        "tower_damage": {"raw": 0, "pct": td},
    }


def player(slot, hero, k, d, a, nw, b, lane=None, **extra):
    p = {
        "player_slot": slot,
        "account_id": 1000 + slot,
        "hero_id": hero,
        "kills": k,
        "deaths": d,
        "assists": a,
        "net_worth": nw,
        "gold_per_min": nw // 40,
        "xp_per_min": 500,
        "last_hits": 200,
        "hero_damage": 20000,
        "tower_damage": 2000,
        "hero_healing": 0,
        "benchmarks": b,
    }
    if lane is not None:
        p["lane_role"] = lane
    p.update(extra)
    return p


BASE_MATCH = {
    "match_id": 8000000001,
    "duration": 40 * 60,
    "radiant_win": False,
    "radiant_score": 21,
    "dire_score": 48,
    "start_time": 1_760_000_000,
    "game_mode": 22,
    "lobby_type": 7,
    "version": None,
    "players": [
        # Radiant (lost). Slot 0 is a mid who fed hard.
        player(0, 11, 2, 14, 3, 9000, bench(0.10, 0.10, 0.15, 0.12, (0, 0.5), 0.10)),
        player(1, 1, 8, 4, 6, 22000, bench(0.70, 0.65, 0.70, 0.60, (0, 0.5), 0.70)),
        player(2, 2, 5, 7, 8, 15000, bench(0.45, 0.50, 0.40, 0.50, (0, 0.5), 0.40)),
        player(3, 26, 3, 8, 10, 8000, bench(0.50, 0.50, 0.50, 0.55, (0, 0.5), 0.40)),
        player(4, 5, 3, 9, 9, 7000, bench(0.50, 0.40, 0.50, 0.40, (120, 0.60), 0.30)),
        # Dire (won)
        player(128, 42, 15, 2, 14, 28000, bench(0.92, 0.90, 0.90, 0.88, (0, 0.5), 0.95)),
        player(129, 86, 12, 3, 20, 20000, bench(0.80, 0.82, 0.75, 0.85, (0, 0.5), 0.60)),
        player(130, 49, 8, 5, 22, 17000, bench(0.60, 0.60, 0.55, 0.60, (0, 0.5), 0.70)),
        player(131, 14, 6, 6, 25, 11000, bench(0.55, 0.55, 0.50, 0.50, (0, 0.5), 0.40)),
        player(132, 50, 2, 5, 28, 9500, bench(0.50, 0.50, 0.50, 0.40, (300, 0.70), 0.30)),
    ],
}


@pytest.fixture
def match():
    return copy.deepcopy(BASE_MATCH)
