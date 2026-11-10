# Who threw? (Dota 2 verdict bot for Telegram)

A small Telegram bot for a friend group. Ask it about a match and it tells you who carried and who threw.

- **Only speaks when asked.** No auto-posts, no weekly spam.
- **No sign-up.** The squad is a fixed list in `friends.json`.
- **Short cards.** One screen per match. Full tables and breakdowns are behind buttons.
- **Fast.** Uses OpenDota only and answers right away from the basic match data. The parsed replay adds wards and stuns later (🔄 button).
- **Transparent scoring.** Tap *Why?* on any player to see each component, its weight and the raw numbers. See [docs/SCORING.md](docs/SCORING.md).

## Commands

| Command | What it does |
|---|---|
| `/last [name] [N]` | Someone's last game. With N: a list of their last N games (max 20); tap a number to open one. No name → tap a name button |
| `/ourlast [N]` | The squad's last game (2+ friends in it), or a list of the last N |
| `/match <id>` | Rate any match |
| `/week`, `/month` | Leaderboard and awards |
| `/scoring` | Short explanation of the formula |

Examples: `/last`, `/last nick`, `/last nick 5`, `/last 5` (then tap a name), `/ourlast 10`.
Names match loosely: `/last smurf` finds GS Smurf. An exact name wins, so `/last gs` means GS, not GS Smurf.

## Friend list

Edit `friends.json` and restart the bot:

```json
[
  {"id": 55775392, "name": "Nick"},
  {"id": 100648413, "name": "Andrew"}
]
```

`id` is the Dota Friend ID. Group commands look through each friend's 20 most recent matches.

Example card:

```
🏆 Dire victory · 21–48 · 40:00
Ranked All Pick · #8000000001

🟢 Ruslan (Rubick) · pos 2 · 12/3/20
     79 🔥 Carried (+0.59)
🔴 Danil (Shadow Fiend) · pos 3 · 2/14/3
     7 💀 Threw? (−0.86)

⭐ MVP: Wraith King — 88
🛡 Best on losing side: Anti-Mage — 69
💀 Threw?: Danil (Shadow Fiend) — 7 · 78% of the blame
[👥 All players] [🔍 Ruslan] [🔍 Danil] [🔄 Refresh]
```

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token. You can leave privacy mode on, because the bot only reacts to commands.
2. Put your squad in `friends.json`, and everyone turns on **Dota 2 → Settings → Social → Expose Public Match Data**.
3. Run it:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # put BOT_TOKEN in it
python -m bot
```

The bot uses long polling, so you don't need a public URL. Any small VPS, a Raspberry Pi or your PC works. Match data is cached in `bot.sqlite3` so repeat lookups are instant.

The OpenDota free tier (no key) is enough for a friend group. The first `/month` call for a big group can take a minute because it fetches every match once. After that, matches come from the cache.

## Tests

```bash
pip install pytest
python -m pytest -q
```

## Layout

```
bot/scoring.py     rating formula (pure, no I/O) – the part to tweak
bot/benchmarks.py  fallback hero percentiles when OpenDota omits them
bot/opendota.py    API client + SQLite cache
bot/service.py     glue: "last match of X", "games this week", ...
bot/render.py      message text
bot/handlers.py    Telegram commands and buttons
bot/db.py          SQLite
```

Match data comes from [OpenDota](https://www.opendota.com).
