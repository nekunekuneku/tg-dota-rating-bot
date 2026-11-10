# How the score works

Every player in a match gets a **score from 0 to 100**.
**50 means "normal for this hero in this position."** Above 50 is better than usual, below is worse.

The **ruin-meter** is the same number on a −1…+1 scale: `(score − 50) / 50`.
So +0.40 is a score of 70 and −0.60 is a score of 20.

All the code is in [`bot/scoring.py`](../bot/scoring.py). Every number below is a constant at the top of that file, so you can tune them.

## 1. Work out each player's position

- **If the replay is parsed**, OpenDota tells us which lane each player was in. The mid is pos 2. In the safe lane, the player with more **last hits at 10 minutes** is pos 1 and the other is pos 5. In the off lane, more last hits is pos 3, the other is pos 4.
- **Otherwise** (or with trilanes or junglers), we sort the team by total last hits: most is pos 1, fewest is pos 5.

Last hits, not net worth: kill gold inflates net worth, so a support who gets lots of kills (a 15/2/18 Invoker) can end up richer than the core in their lane. Last hits only count creeps, which is what cores take and supports leave.

The fallback can still swap pos 1 and pos 2, since a mid and a carry both farm a lot. The weights for pos 1 and 2 are close, so this rarely changes the verdict. The *Why?* screen tells you which method was used.

## 2. Score each component (0–100)

| Component | What's measured | Compared to |
|---|---|---|
| **Farm** | GPM, XPM, last hits (cores only) | Other games **on the same hero** (OpenDota benchmarks) |
| **Hero damage** | damage per minute | Same hero |
| **Buildings** | tower/building damage | Same hero |
| **Kill participation** | (kills + assists) / team kills | Normal for the **position** |
| **Deaths** | deaths per 10 min | Normal for the position |
| **Stuns & healing** | stun seconds per min (parsed only), healing (only for heroes that heal) | Fixed norm / same hero |
| **Vision** | (obs + sentries + 2 × dewards) per 10 min (parsed only) | Normal for the position |

"Compared to same hero" uses OpenDota's percentile. For example, if your GPM beat 70% of recent public Anti-Mage games, your farm is 70.

"Compared to the position" uses a smooth curve centred on the norm:

```
component = 50 + 50 · tanh((value − norm) / spread)
```

At the norm you get 50. One `spread` better gives about 88, and one `spread` worse gives about 12. Here are the norms:

| | pos 1 | pos 2 | pos 3 | pos 4 | pos 5 | spread |
|---|---|---|---|---|---|---|
| Kill participation | 55% | 60% | 55% | 60% | 60% | 15% |
| Deaths / 10 min | 1.4 | 1.7 | 2.0 | 2.4 | 2.4 | 0.9 |
| Wards / 10 min | 0.3 | 0.3 | 0.6 | 3.5 | 6.0 | 60% of norm |
| Stuns s/min | 1.0 for everyone | | | | | 1.0 |

## 3. Combine them using position weights

Each row adds up to 100:

|      | farm | dmg | kp | deaths | bld | stuns/heal | vision |
|------|-----:|----:|---:|-------:|----:|-----------:|-------:|
| pos1 | 30 | 20 | 10 | 20 | 15 | 5 | 0 |
| pos2 | 25 | 25 | 15 | 20 | 10 | 5 | 0 |
| pos3 | 15 | 15 | 20 | 20 | 10 | 20 | 0 |
| pos4 | 5 | 15 | 25 | 15 | 5 | 20 | 15 |
| pos5 | 0 | 10 | 25 | 15 | 5 | 20 | 25 |

```
score = Σ(component × weight) / Σ(weight of components we have data for)
```

When the replay isn't parsed yet, vision and stuns are missing. They're **dropped, and the other weights are scaled up**. Missing data is never treated as a zero. The card says "Preliminary" until the replay is parsed. Tap 🔄 to re-check.

## 4. Verdicts

| Score | Won | Lost |
|---|---|---|
| 75+ | 🔥 Carried | 🦸 Tried hard |
| 60–75 | 👍 Good | 👍 Good |
| 40–60 | ▫️ OK | ▫️ OK |
| 25–40 | 🎒 Got carried (under 35) / 👎 Weak | 👎 Weak |
| under 25 | 🎒 Got carried | 🫠 Bad |

**💀 Threw?** overrides the table. You only get it if **all three** are true:
1. you lost,
2. your score is under 30,
3. you had **2 or more "clear failures"**: components under 20 that carry at least 10% weight.

A bad KDA alone isn't enough. A support who went 3/9/9 with a normal kill participation stays "OK".

**Blame %** on the losing team: each loser's deficit is `max(0, 55 − score)`. Each player's share of the total deficit is their blame.

**MVP** is the highest score on the winning team. **Best on losing side** is the highest score on the losing team.

## Known limits

- OpenDota benchmarks cover all skill brackets, not your rank. A Herald and an Immortal are compared against the same curve.
- Saves (Glimmer, Force on an ally) aren't counted. OpenDota doesn't expose them in a usable form. Healing partly covers this.
- Turbo games are scored like normal games. Benchmarks don't separate them, so Turbo GPM looks inflated.
- Position guessing without a parsed replay is a heuristic (see step 1). Tap 🔄 once the replay is parsed to get real lanes.
