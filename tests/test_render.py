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
    assert "Replay not parsed yet" in text


def stats_then_comments(text):
    """Split the card body into the stats block and the comments block."""
    blocks = [b.splitlines() for b in text.split("\n\n")]
    return blocks[1], blocks[2]


def test_squad_loss_rules(match):
    # Radiant lost. 1000 = feeder (7), 1001 = Anti-Mage (69), 1003 = Lion (58)
    friends = {1000: "Danil", 1001: "Ruslan", 1003: "Mic"}
    text = visible(render.summary(rate_match(match), HEROES, friends))
    print("\n" + text)
    assert text.startswith("❌ We lost as Radiant")
    stats, comments = stats_then_comments(text)
    assert [l.split(" (")[0] for l in stats] == ["Ruslan", "Mic", "Danil"]  # best first
    assert all("/" in l for l in stats)  # KDA on every stats line
    assert comments[0].startswith("👍 Best of us: Ruslan — ")
    assert comments[1].startswith("👎 Mic — ")
    assert comments[2].startswith("🗑 Griefer: Danil — ")
    assert "MVP" not in text and "Best on losing side" not in text  # no comments on randoms


def test_squad_win_rules(match):
    # Dire won. 1128 = Wraith King (match MVP, 88), 1129 = Rubick (79), 1132 = Dazzle (64)
    friends = {1128: "Nick", 1129: "Andrew", 1132: "QG"}
    text = visible(render.summary(rate_match(match), HEROES, friends))
    print("\n" + text)
    assert text.startswith("✅ We won as Dire")
    stats, comments = stats_then_comments(text)
    assert len(stats) == 3
    assert comments[0].startswith("⭐ MVP: Nick — ")
    assert comments[1] == "🎒 Got carried: QG"  # 64 is above normal: no bad stat
    assert len(comments) == 2  # Andrew, in the middle, gets no comment


def test_win_best_not_mvp_and_carried_with_bad_stat(match):
    for p in match["players"]:
        if p["player_slot"] == 132:
            p.update(deaths=20, assists=2)  # push QG below normal
    friends = {1129: "Andrew", 1132: "QG"}
    text = visible(render.summary(rate_match(match), HEROES, friends))
    print("\n" + text)
    _, comments = stats_then_comments(text)
    assert comments[0].startswith("💪 Best of us: Andrew — ")
    assert comments[1].startswith("🎒 Got carried: QG — ")


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


def test_ability_draft_lists_drafted_skills(match):
    names = {5427: "Thunder Clap", 5428: "Vengeance Aura", 5429: "Nether Swap", 5609: "Gust"}
    for p in match["players"]:
        if p["player_slot"] == 128:
            # repeats and talents (6565 isn't in the names map) are dropped
            p["ability_upgrades_arr"] = [5427, 5609, 5427, 5428, 5429, 6565, 5429]
    friends = {1128: "Nick"}

    match["game_mode"] = 18
    text = visible(render.summary(rate_match(match), HEROES, friends, names))
    print("\n" + text)
    assert "🎲 Thunder Clap · Gust · Vengeance Aura · Nether Swap" in text

    match["game_mode"] = 22  # not AD: no skills line even if names are passed
    assert "🎲" not in render.summary(rate_match(match), HEROES, friends, names)


def test_lists_tag_game_mode(match):
    rated = []
    for mode, lobby in [(18, 0), (22, 7), (22, 0), (23, 0), (2, 7), (2, 0)]:
        match["game_mode"], match["lobby_type"] = mode, lobby
        m = rate_match(match)
        rated.append((m, m.player_by_slot(0)))
    text = visible(render.history("Danil", rated, HEROES))
    print("\n" + text)
    tags = [l.split("(")[1].split(")")[0] for l in text.splitlines() if l[:1].isdigit()]
    assert tags == ["AD", "R", "N", "T", "R", "CM"]


def test_week_links_game_and_shame(match):
    from bot.handlers import kb_highlights

    m = rate_match(match)
    rows = [
        {"name": "Danil", "games": [(m, m.player_by_slot(0)), (m, m.player_by_slot(1))]},
        {"name": "Ruslan", "games": [(m, m.player_by_slot(129)), (m, m.player_by_slot(128))]},
    ]
    week = render.week(rows, HEROES, 7)
    assert week.count('href="https://www.opendota.com/matches/8000000001"') == 2
    month = visible(render.week(rows, HEROES, 30))
    assert "Shame of the month" in month and "of the week" not in month
    # same match for both here, so only one button
    assert [b.callback_data for b in kb_highlights(rows, 7).inline_keyboard[0]] == ["o:8000000001"]
