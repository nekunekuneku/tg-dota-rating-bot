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
                for t in (roasts.GOOD if good else roasts.BAD)[key]:
                    missing = fields(t) - set(c.values) - {"stat"}
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
