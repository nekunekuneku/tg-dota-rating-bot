import re
from string import Formatter

from bot import roasts
from bot.scoring import rate_match


def fields(template):
    return {f for _, f, _, _ in Formatter().parse(template) if f}


def test_every_template_has_its_values(match):
    """Every {name} in a joke must exist on the component it's used for."""
    match["version"] = 21  # parsed: stuns, wards and time dead available
    for p in match["players"]:
        p.update(obs_placed=3, sen_placed=4, observer_kills=1, stuns=40.0, life_state_dead=300)
    m = rate_match(match)
    seen = set()
    for r in m.players:
        for c in r.components:
            for good in (True, False):
                key = c.key
                if key == "impact" and not ("stuns" in c.values and "heal" in c.values):
                    key = "impact_stun" if "stuns" in c.values else "impact_heal"
                for o in (roasts.GOOD if good else roasts.BAD)[key]:
                    t = o[1] if isinstance(o, tuple) else o
                    missing = fields(t) - set(c.values) - {"stat", "hero"}
                    assert not missing, (key, t, missing)
                    seen.add(key)
    assert {"deaths", "kp", "farm", "damage", "buildings", "vision", "impact_stun"} <= seen


def test_deaths_joke_uses_time_dead(match):
    match["version"] = 21
    match["players"][0]["life_state_dead"] = 863  # 14m 23s
    c = next(c for c in rate_match(match).player_by_slot(0).components if c.key == "deaths")
    text = roasts.line(c, False, "seed")
    assert re.search(r"\b14 minutes\b|\b36%", text), text


def test_unparsed_time_dead_is_marked_as_estimate(match):
    c = next(c for c in rate_match(match).player_by_slot(0).components if c.key == "deaths")
    assert c.values["dead_min"].startswith("~")


def test_same_seed_same_joke(match):
    c = rate_match(match).player_by_slot(0).components[0]
    assert roasts.line(c, False, "9:0") == roasts.line(c, False, "9:0")


def test_every_bad_deaths_line_says_how_long_they_were_dead():
    for o in roasts.BAD["deaths"]:
        t = o[1] if isinstance(o, tuple) else o
        assert {"dead_min", "dead_pct"} & fields(t), t


def test_conditions_and_hero_lines(match):
    c = next(c for c in rate_match(match).player_by_slot(0).components if c.key == "deaths")
    v = {**c.values, "stat": c.stat}
    texts = roasts.usable(roasts.BAD["deaths"], v)
    assert not any("{hero}" in t for t in texts)  # no hero passed
    assert any("{hero}" in t for t in roasts.usable(roasts.BAD["deaths"], {**v, "hero": "Pudge"}))
    zero_kill = "Zero kills"
    assert any(zero_kill in t for t in texts) == (c.values["kills"] == 0)
    # every seed gives a fully formatted line, never a raw {placeholder}
    for seed in range(50):
        out = roasts.line(c, False, str(seed), "Pudge")
        assert "{" not in out, out
