from bot import scoring
from bot.benchmarks import fill_benchmarks, percentile_of
from bot.scoring import WEIGHTS, around_norm, infer_positions, rate_match
from bot.service import parse_account_id, parse_match_id


def test_weights_sum_to_100():
    for pos, w in WEIGHTS.items():
        assert sum(w.values()) == 100, pos


def test_around_norm_is_centered():
    assert around_norm(1.0, 1.0, 0.5) == 50
    assert around_norm(1.5, 1.0, 0.5) > 85
    assert around_norm(0.5, 1.0, 0.5) < 15
    assert around_norm(3, 1.0, 0.5, higher_is_better=False) < 5


def test_positions_tie_on_last_hits_falls_back_to_net_worth(match):
    radiant = [p for p in match["players"] if p["player_slot"] < 128]  # all 200 LH in the fixture
    pos = infer_positions(radiant, 40)
    assert pos[1] == (1, "last hits")  # richest
    assert pos[4] == (5, "last hits")  # poorest


# Radiant of match 9072914350: GS Smurf's Invoker was richer from kills,
# but QG's Spirit Breaker took more creeps and was the real pos 3.
REAL_TEAM = [
    {"player_slot": 0, "net_worth": 20109, "last_hits": 294, "lane_role": 2},  # Nick, mid
    {"player_slot": 1, "net_worth": 13054, "last_hits": 49, "lane_role": 3},  # GS Smurf
    {"player_slot": 2, "net_worth": 12330, "last_hits": 78, "lane_role": 3},  # QG
    {"player_slot": 3, "net_worth": 21306, "last_hits": 290, "lane_role": 1},
    {"player_slot": 4, "net_worth": 10092, "last_hits": 19, "lane_role": 1},
]


def test_offlane_core_is_who_farmed_not_who_got_kills():
    pos = {s: p for s, (p, _) in infer_positions(REAL_TEAM, 33).items()}
    assert pos[2] == 3 and pos[1] == 4


def test_offlane_core_without_replay():
    team = [{k: v for k, v in p.items() if k != "lane_role"} for p in REAL_TEAM]
    pos = {s: p for s, (p, _) in infer_positions(team, 33).items()}
    assert pos[2] == 3 and pos[1] == 4


def test_last_hits_at_10_min_win_over_totals():
    team = [dict(p) for p in REAL_TEAM]
    team[1]["lh_t"] = [0] * 10 + [40]  # GS out-farmed QG in lane...
    team[2]["lh_t"] = [0] * 10 + [15]  # ...even though QG ended with more
    pos = {s: p for s, (p, _) in infer_positions(team, 33).items()}
    assert pos[1] == 3 and pos[2] == 4


def test_positions_by_lane():
    team = [
        {"player_slot": 0, "net_worth": 15000, "lane_role": 2},
        {"player_slot": 1, "net_worth": 20000, "lane_role": 1},
        {"player_slot": 2, "net_worth": 6000, "lane_role": 1},
        {"player_slot": 3, "net_worth": 14000, "lane_role": 3},
        {"player_slot": 4, "net_worth": 7000, "lane_role": 3},
    ]
    pos = {s: p for s, (p, _) in infer_positions(team, 40).items()}
    assert pos == {0: 2, 1: 1, 2: 5, 3: 3, 4: 4}


def test_feeder_gets_threw_and_most_blame(match):
    m = rate_match(match)
    feeder = m.player_by_slot(0)
    assert not feeder.won
    assert feeder.score < scoring.THREW_SCORE
    assert len(feeder.failures) >= 2
    assert feeder.verdict == "Threw?"
    assert m.threw[0] is feeder
    losers = [p for p in m.players if not p.won]
    assert feeder.blame == max(p.blame for p in losers)
    assert abs(sum(p.blame for p in losers) - 1) < 0.02


def test_bad_kda_alone_is_not_threw(match):
    """A support with many deaths but fine everything else is not 'Threw?'."""
    m = rate_match(match)
    sup = m.player_by_slot(4)  # 3/9/9 pos 5
    assert sup.verdict != "Threw?"


def test_mvp_and_best_loser(match):
    m = rate_match(match)
    assert m.mvp.slot == 128
    assert m.mvp.verdict == "Carried"
    assert m.best_loser.slot == 1


def test_unparsed_skips_vision_and_rescales(match):
    m = rate_match(match)
    pos5 = next(p for p in m.players if p.position == 5 and p.won)
    assert "vision" in pos5.skipped
    assert not m.parsed
    assert 0 < m.confidence < 1


def test_parsed_counts_vision(match):
    match["version"] = 21
    for p in match["players"]:
        p.update(obs_placed=2, sen_placed=3, stuns=30.0)
    sup = next(p for p in match["players"] if p["player_slot"] == 132)
    sup.update(obs_placed=14, sen_placed=16, observer_kills=4, stuns=60.0)
    m = rate_match(match)
    assert m.parsed
    r = m.player_by_slot(132)
    vision = next(c for c in r.components if c.key == "vision")
    assert vision.score > 70
    assert not r.skipped


def test_missing_benchmarks_still_scores(match):
    for p in match["players"]:
        p.pop("benchmarks")
    m = rate_match(match)
    for p in m.players:
        assert 0 <= p.score <= 100
        assert {"kp", "deaths"} <= {c.key for c in p.components}


def test_benchmark_fallback():
    points = [{"percentile": x / 10, "value": 300 + 50 * x} for x in range(1, 10)]
    assert percentile_of(300 + 50 * 5, points) == 0.5
    assert 0.5 < percentile_of(560, points) < 0.6
    assert percentile_of(10_000, points) == 1.0
    assert percentile_of(0, points) == 0.0

    m = {"duration": 2400, "players": [{"hero_id": 1, "gold_per_min": 550, "xp_per_min": 0}]}
    fill_benchmarks(m, {1: {"result": {"gold_per_min": points}}})
    assert m["players"][0]["benchmarks"]["gold_per_min"]["pct"] == 0.5


def test_parse_ids():
    assert parse_account_id("123456789") == 123456789
    assert parse_account_id("76561198083722517") == 123456789
    assert parse_account_id("https://www.dotabuff.com/players/123456789") == 123456789
    assert parse_account_id("https://steamcommunity.com/profiles/76561198083722517/") == 123456789
    assert parse_account_id("https://steamcommunity.com/id/vanity") is None
    assert parse_account_id("hello") is None
    assert parse_match_id("https://www.opendota.com/matches/8012345678") == 8012345678
