# How the Pick'em Model Works — and What We've Tried

**Audience: someone who has never seen this project.** No stats background assumed.
This is the living record of *how the sausage is made*: what the model does, what made it
better, and — mostly — what didn't, so nobody burns a weekend rediscovering a dead end.

Companion docs: `PICKEM_STATUS.md` (current state, deployment, open questions),
`DFS_STATUS.md` (the separate DFS side of this repo).

**Keep this current.** Every time a feature is tested, add it to the tables below —
*especially* the failures. A killed idea is worth more here than a shipped one, because
killed ideas are the ones people re-propose.

---

## 1. The game we're playing

Adam is in a season-long NFL pick'em pool on CBS Sportsline. Each week you pick every
game **against the spread** — not just who wins, but who beats the point handicap. One
point per correct pick, ~16 games a week, real money on the line ($150 entry;
$1,200/$450/$300/$150 for the top four at season's end).

**The one exploitable quirk:** CBS posts its spread on Tuesday and **freezes it for the
week**. The real betting market does not freeze — it moves all week on injuries, weather,
and money. By Sunday, CBS's Tuesday number can be stale.

That gap is the entire edge. Not football knowledge. Not picking winners. Just: *the pool
is using an out-of-date number, and we can see the current one.*

---

## 2. The model, in one page

```
edge = live_market_line − frozen_CBS_line       (home-team spread, − = home favored)
```

- **|edge| ≥ 0.5 points** → pick whichever side the market moved *toward*. That side is
  getting a better number from CBS than the market now thinks it deserves.
- **|edge| < 0.5** → no validated signal. Fall back to the current market favorite.
  We deliberately claim *no* edge here rather than inventing one.
- **Favorite flips sides entirely** (CBS says Team A favored, market now says Team B) →
  automatic STRONG pick. The single strongest pattern in the data.
- **Win probability**: `P(cover) = Φ(|edge| / 13.45)`. Roughly **+3% win probability per
  point of stale value.** (13.45 is the standard deviation of NFL game margins around the
  spread — Stern 1991, re-checked against our own 2014–2024 data.)

Tiers shown in the app: **STRONG** (3+ pts or a flip) · **SOLID** (1.5–3) · **LEAN**
(0.5–1.5) · **COIN FLIP** (<0.5).

Code: `edge/pickem.py` (~130 lines, no dependencies). That's the whole model.

### How well it works

Backtested on 2,878 real NFL games (2014–2024). Trained on 2014–2022, then evaluated
**once** on 2023–2024 — seasons the model had never seen:

| | Record | Rate |
|---|---|---|
| **The model, out-of-sample** | 297-236-10 | **55.7%** |
| Games where the line moved (83% of slate) | 253-193 | 56.7% |
| Games where it didn't (the coin flips) | 44-43 | 50.6% |
| *Baseline: always pick the favorite* | — | 54.2% |
| *Baseline: always pick the home team* | — | 51.2% |

≈ **8.9 wins per 16-game week.** 21 of 36 test weeks hit a 9+/16 pace, 10 hit 10+/16.

### The result that proves it's real

Re-run the identical strategy pretending the frozen line was never stale (set the pool
line = the closing line): **48.9% — a dead coin flip.** The edge vanishes exactly when the
staleness does. That check is permanently wired into `scripts/pickem_backtest.py`; if it
ever drifts far from 50%, something has broken.

---

## 3. How we test things (the part that keeps us honest)

Three rules, enforced in code rather than by good intentions:

**1. Never use information from the future.** Obvious in principle, easy to violate by
accident. If we're predicting a Week 5 game, the model may only see Weeks 1–4. Full-season
stats are forbidden — a team's final-season rating already "knows" how the Week 5 game
turned out. `edge/pickem_features.py` enforces this structurally: each week's ratings are
snapshotted from an accumulator *before* that week's games are added to it. Leaking would
require rewriting the loop, not just passing a wrong argument.

**2. Split by time, never randomly.** A random split lets a 2023 game leak information
into a 2019 prediction through shared team strength. We split chronologically:

| Split | Seasons | Purpose |
|---|---|---|
| Train | 2014–2019 | Fit anything that needs fitting |
| Validate | 2020–2022 | Test ideas here, as often as we like |
| **Holdout** | **2023–2024** | **Touched once. That's the honest number.** |

**3. The holdout is spent when it's used.** `scripts/pickem_feature_lab.py` *refuses* to
read holdout seasons — it hard-filters them at load. New ideas must survive train **and**
validate before they're allowed anywhere near 2023–24. Anything that only works in one of
the two is noise, and we treat it as noise.

**The tell we look for:** a real signal points the same direction in both train and
validate. A fake one flips sign. Almost everything below flips sign.

---

## 4. What WORKS

| Feature | Effect | Status |
|---|---|---|
| **Market movement off the frozen line** | 54.1% on dev, 55.7% out-of-sample (z=+3.92) | **Shipped.** The entire model. |
| **Favorite-flip → auto STRONG** | Best single pattern found | **Shipped.** |
| **Bigger move = more confidence** | 3+ pt moves hit 65.3% out-of-sample | **Shipped** (drives tiering). |

That's the complete list. One idea, and it's the one the project started with.

---

## 5. What DIDN'T work — the graveyard

> Everything here was tested properly and failed. **Do not re-propose without genuinely
> new evidence** — and "I have a feeling about it" is not new evidence.

### 5a. DVOA-style efficiency ratings — *the big one*

**The idea:** DVOA measures how efficient a team is per play, versus league average,
adjusted for opponent. If we know Team A is far better than Team B, and CBS's line
under-rates that gap, we should pounce.

**What we built:** Real DVOA is proprietary — FTN (formerly Football Outsiders) keeps the
historical archive behind a paywall with no free API, so it can't go in a reproducible
pipeline. We built a **DVOA-analog** from free nflverse play-by-play: opponent-adjusted
EPA (Expected Points Added) per play, offense and defense separately, with early-season
shrinkage and a prior-season carryover so Week 1 isn't blank. That's DVOA's two core ideas
(efficiency vs. baseline, adjusted for opponent) even if it isn't DVOA's actual formula.
~6,300 team-games, 2013–2024. Code: `edge/pickem_features.py`.

**Result: it did nothing.**

| Variant | Train | Validate | Combined |
|---|---|---|---|
| Ratings vs. the frozen line | 50.2% | 51.0% | 50.5% (z=+0.44) |
| Only when ratings disagree by 3+ pts | 49.2% | 50.7% | — |
| Only on coin-flip games | 50.3% | 49.0% | — |
| Garbage-time filtered | 48.7% | 49.9% | 49.1% (z=−0.86) |
| Early season only (wk 1–4) | 52.6% | **47.3%** | — |

Note the confidence test: filtering to games where the model disagrees *most strongly*
made it **worse** (49.8% → 48.3% as the threshold rose). A real signal gets stronger when
you demand more of it. This one got weaker — the fingerprint of noise.

**Why it failed — and this is the important part.** We checked whether our rating was
simply broken. It isn't:

| Check | Value | Meaning |
|---|---|---|
| corr(our rating, actual margin) | **+0.338** | Our rating genuinely predicts games |
| corr(opening line, actual margin) | **+0.413** | The bookmaker predicts them *better* |
| corr(our rating, opening line) | **+0.817** | They already largely agree |
| Straight-up winners picked | 61.2% vs. line's 65.5% | We're real, but worse |
| **corr(rating *beyond* the line, margin *beyond* the line)** | **+0.002** | **Nothing left over** |

That last row is the whole story. Take everything our rating knows, subtract the part the
betting line already knew, and ask whether the leftover predicts anything the line got
wrong. Answer: **+0.002. Zero.**

The rating works fine. It's *redundant*. Oddsmakers are professionals who already price
team efficiency — and price it better than we do. Our stale-line edge comes from **time**
(information arriving after CBS froze its number), not from **knowing more football** than
the bookmaker at the moment the line was set. Building a better football model doesn't
attack the thing that actually makes money here.

> **The generalizable lesson:** before adding any feature, ask *"is this information the
> bookmaker didn't already have when they set the line?"* If they had it, it's priced,
> and it's worth nothing to us — no matter how well it predicts football.

### 5b. Head coach / coaching-system features

**The idea:** coaches run systems. Some consistently beat the spread, especially as
underdogs; some are systematically over- or under-valued by the market.

**What we built:** as-of-that-week career records for every head coach back to 1999 (never
including the game being predicted), with heavy shrinkage so a 6-3 coach isn't treated as
a 67% lock. Tested four separate angles:

| Feature | Train | Validate | Combined | Verdict |
|---|---|---|---|---|
| Coach career ATS edge | **47.1%** | **52.9%** | 49.1% (z=−0.72) | Sign flip. Dead. |
| Fade first-year head coach | 48.6% | 52.7% | 50.2% (z=+0.08) | Sign flip. Dead. |
| Back the more experienced coach | 50.2% | 51.7% | 50.7% (z=+0.48) | Nothing. Dead. |
| Back proven underdog-covering coach | 48.8% | 53.0% | 50.7% (z=+0.36) | Sign flip. Dead. |

**Every single one flips sign between the two eras.** The coach-career-ATS result is the
best cautionary tale in the whole project: on train it was "significant" at z=−1.96 —
significantly *bad*, a coach's record predicting the **opposite** of the next game — and
then flipped positive on validate. Had we looked at only one era and squinted, we'd have
shipped a coin flip dressed up as an edge.

**Why it failed:** same reason as DVOA. Coaching quality is not a secret. It's in the
line. A coach with a reputation for beating spreads gets that reputation priced *into* the
next spread — which mechanically destroys the edge that created the reputation.

**On the "system fits the roster" idea specifically** (e.g., a coach whose scheme
elevates a certain kind of RB or TE): that's a *player-level* effect. It's plausibly real
and it matters for **DFS**, where you pick individual players — see `DFS_MULTISPORT_PLAN.md`.
But this pool scores **team vs. spread**. A scheme boosting one player shows up here only
as a slightly better team, which is exactly what the line already prices. Wrong tool for
this game. It has not been tested for DFS and might well work there.

### 5c. Previously killed (from earlier sessions)

| Idea | Why it died |
|---|---|
| Direction of line move (toward favorite vs. underdog) | 60.7% in test, opposite sign in train. Textbook noise. Excluded on purpose. |
| Picking on the *closing* line instead of the frozen one | 48.9% — the negative control. Confirms the edge is staleness. |

---

## 6. Where the remaining upside actually is

We're at ~8.9 wins/week; the target is 10. Based on everything above, the gains are **not**
in better football modeling. Ranked by realistic promise:

1. **Time the picks better.** The backtest approximates "at lock" with the *closing* line,
   but Adam's real deadline is each day's first kickoff — hours earlier for most games.
   Capturing the line as late as legally possible is worth more than any new feature,
   because it directly buys more staleness. (Also why live results may land slightly
   *below* 55.7% — see `PICKEM_STATUS.md` caveat #2.)
2. **Net out CBS's house offset.** CBS sets spreads "at its own discretion," so part of
   the observed gap may be CBS's own methodology rather than drift. Recording a market
   line *at the moment CBS posts* would isolate true movement. The
   `live_line_at_post_home` column in `data/pickem/tracker.csv` exists for this and is
   still unused.
3. **Multi-book consensus** instead of one book's line — less noise per reading.
4. **Pool-standings strategy.** Late in the season, maximizing *expected wins* stops being
   the right goal — you play to win the pool. Copy the leader when ahead, deliberately
   diverge when behind. Pure game theory, zero football modeling, and completely untouched.

Genuinely dead ends, do not revisit: better team-strength ratings of any kind, coaching
records, anything else the bookmaker already sees at line-set time.

---

## 7. Running it yourself

```bash
# Rebuild the efficiency data from nflverse (free, ~45s)
python3 scripts/pickem_pbp_collect.py 2013 2024      # -> data/pbp_team_game.csv

# Re-run every feature experiment in section 5 (dev split only, holdout-safe)
python3 scripts/pickem_feature_lab.py

# The honest out-of-sample backtest (touches the holdout — the shipped model only)
python3 scripts/pickem_backtest.py

# Unit tests
python3 -m pytest tests/test_pickem.py -q
```

| File | Role |
|---|---|
| `edge/pickem.py` | The shipped model. Small on purpose. |
| `edge/pickem_features.py` | As-of-week ratings + coach history. **Built, tested, not shipped** — kept so nobody rebuilds it to rediscover section 5. |
| `edge/pickem_live.py` | Live market lines (The Odds API; ~1 credit for a whole week) |
| `scripts/pickem_feature_lab.py` | Experiment harness. Refuses to read holdout seasons. |
| `scripts/pickem_backtest.py` | Out-of-sample backtest + the staleness negative control |
| `scripts/pickem_pbp_collect.py` | nflverse play-by-play → per-game efficiency table |
| `pages/4_🎯_Pickem.py` | The weekly picks screen |

**Note on `edge/pickem_features.py`:** it is deliberately kept in the repo despite shipping
nothing. It's the evidence behind section 5, it re-runs on demand, and if someone ever
wants to test a new efficiency idea the as-of-week plumbing (the hard, easy-to-get-wrong
part) already exists and is already leak-proof.
