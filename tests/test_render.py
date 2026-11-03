import re

from bot import render
from bot.handlers import kb_summary
from bot.scoring import rate_match

HEROES = {11: "Shadow Fiend", 1: "Anti-Mage", 2: "Axe", 26: "Lion", 5: "Crystal Maiden",
          42: "Wraith King", 86: "Rubick", 49: "Dragon Knight", 14: "Pudge", 50: "Dazzle"}  # fmt: skip
FRIENDS = {1000: "Danil", 1129: "Ruslan"}


def visible(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def test_summary_is_compact(match):
    m = rate_match(match)
    text = render.summary(m, HEROES, FRIENDS)
    print("\n" + visible(text))
    assert len(text.splitlines()) <= 14
    assert "Danil" in text and "Ruslan" in text
    assert "Threw?" in text
    assert "Preliminary" in text


def test_all_players_and_why(match):
    m = rate_match(match)
    table = render.all_players(m, HEROES, FRIENDS)
    print("\n" + visible(table))
    for pre in re.findall(r"<pre>(.*?)</pre>", table, re.S):
        for row in pre.splitlines():
            assert len(row) <= 34, row  # fits a phone screen in monospace
    why = render.why(m, m.player_by_slot(0), HEROES, FRIENDS)
    print("\n" + visible(why))
    assert "weight" in why and "⚠️" in why


def test_keyboard_callback_data_fits(match):
    m = rate_match(match)
    kb = kb_summary(m, HEROES, FRIENDS)
    for row in kb.inline_keyboard:
        for b in row:
            assert len(b.callback_data.encode()) <= 64


def test_profile_and_week(match):
    m = rate_match(match)
    games = [(m, m.player_by_slot(0)), (m, m.player_by_slot(129))]
    print("\n" + visible(render.profile("Danil", games, HEROES)))
    rows = [
        {"name": "Danil", "games": [(m, m.player_by_slot(0)), (m, m.player_by_slot(1))]},
        {"name": "Ruslan", "games": [(m, m.player_by_slot(129)), (m, m.player_by_slot(128))]},
        {"name": "Ghost", "games": []},
    ]
    week = render.week(rows, HEROES, 7)
    print("\n" + visible(week))
    assert "Carry of the week: Ruslan" in visible(week)
    assert "Ghost" not in week
