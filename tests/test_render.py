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
    assert "Friends on both sides" in text  # Danil is Radiant, Ruslan is Dire
    assert "Preliminary" in text


def test_squad_loss_rules(match):
    # Radiant lost. 1000 = feeder (7), 1001 = Anti-Mage (69), 1003 = Lion (58)
    friends = {1000: "Danil", 1001: "Ruslan", 1003: "Mic"}
    text = visible(render.summary(rate_match(match), HEROES, friends))
    print("\n" + text)
    lines = text.splitlines()
    assert lines[0].startswith("❌ We lost as Radiant")
    ruslan = next(i for i, l in enumerate(lines) if "Ruslan" in l)
    assert "👍" in lines[ruslan] and "best of us" in lines[ruslan] and "📈" in lines[ruslan + 1]
    danil = next(i for i, l in enumerate(lines) if "Danil" in l)
    assert "griefer" in lines[danil] and "📉" in lines[danil + 1]
    mic = next(i for i, l in enumerate(lines) if "Mic" in l)
    assert "👎" in lines[mic] and "📉" in lines[mic + 1]
    assert "MVP" not in text and "Best on losing side" not in text  # no comments on randoms


def test_squad_win_rules(match):
    # Dire won. 1128 = Wraith King (match MVP, 88), 1129 = Rubick (79), 1132 = Dazzle (64)
    friends = {1128: "Nick", 1129: "Andrew", 1132: "QG"}
    text = visible(render.summary(rate_match(match), HEROES, friends))
    print("\n" + text)
    lines = text.splitlines()
    assert lines[0].startswith("✅ We won as Dire")
    nick = next(i for i, l in enumerate(lines) if "Nick" in l)
    assert "⭐" in lines[nick] and "MVP" in lines[nick] and "📈" in lines[nick + 1]
    qg = next(i for i, l in enumerate(lines) if "QG" in l)
    assert "got carried" in lines[qg]
    assert qg + 1 == len(lines) or "📉" not in lines[qg + 1]  # 64 is above normal: no bad stat
    andrew = next(i for i, l in enumerate(lines) if "Andrew" in l)
    assert "▫️" in lines[andrew]


def test_win_best_not_mvp_and_carried_with_bad_stat(match):
    # 1129 Rubick is best of these two but not match MVP; 1132's score pushed below normal
    for p in match["players"]:
        if p["player_slot"] == 132:
            p.update(deaths=20, assists=2)
    friends = {1129: "Andrew", 1132: "QG"}
    text = visible(render.summary(rate_match(match), HEROES, friends))
    print("\n" + text)
    assert "💪" in text and "best of us" in text and "MVP" not in text
    lines = text.splitlines()
    qg = next(i for i, l in enumerate(lines) if "QG" in l)
    assert "got carried" in lines[qg] and "📉" in lines[qg + 1]


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
