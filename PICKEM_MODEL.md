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
- **|edge| < 0.5** → no movement signal. Break the tie on **totals drift**: if the game
  total has fallen by 0.5+ points take the underdog, if it has risen take the favorite,
  otherwise take the market favorite. (Lower-scoring games compress margins, which helps
  whoever is getting points.) Added 2026-08-22 — see section 4.
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
| **The model, out-of-sample** | 298-235-10 | **55.9%** |
| Games where the line moved (83% of slate) | 253-193 | 56.7% |
| Games where it didn't (the coin flips) | 45-42 | 51.7% |
| *Baseline: always pick the favorite* | — | 54.2% |
| *Baseline: always pick the home team* | — | 51.2% |

≈ **8.9 wins per 16-game week.** 18 of 36 test weeks hit a 9+/16 pace, 13 hit 10+/16.

*(Was 55.7% / 297-236-10 before the totals tiebreak. That change bought exactly one extra
win across 543 games — see section 4's honesty note.)*

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
| **Market movement off the frozen line** | 54.1% on dev, 55.9% out-of-sample (z=+3.92) | **Shipped.** The entire model. |
| **Favorite-flip → auto STRONG** | Best single pattern found | **Shipped.** |
| **Bigger move = more confidence** | 3+ pt moves hit 65.3% out-of-sample | **Shipped** (drives tiering). |
| **Totals-drift coin-flip tiebreak** | Beat "take the favorite" in 8 of 9 dev seasons; +1.2pp on dev | **Shipped, provisional.** |

### Honesty note on the totals tiebreak (added 2026-08-22)

It is shipped, but it is **not proven**. Dev promised +1.2pp on the full slate (54.1% →
55.3%); the holdout delivered **+0.2pp** (55.7% → 55.9%) — literally one extra win in 543
games, well inside noise. It is kept because it beat the old default in 8 of 9 dev seasons,
it has a real mechanism, it never made things worse, and the old default was measurably
*bad* (see below). Treat it as a small, unconfirmed edge, not a win.

The thing it replaced deserves naming: **"take the market favorite" on no-movement games was
the single worst rule in the model** — 48.3% across the dev split, i.e. actively worse than a
coin flip. Several different replacements beat it. That's the real finding here; totals drift
just happened to be the best of them.

**Operational cost:** this needs the game total *at the moment CBS froze its spread*, which
nothing currently captures. Add it to the Tuesday capture (the Odds API can return spreads
and totals in one call — 2 credits instead of 1). Without it the model silently falls back
to the old favorite rule, which is safe but forfeits the feature.

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

### 5d. The key-number bonus — a real effect we still can't price

**This one is different from the rest of the graveyard: the effect is real.** It's the
*magnitude* that failed, and the story is worth keeping because it is the clearest example
in the project of how a validated finding can still be unshippable.

**The idea:** NFL margins cluster hard on 3 and 7. If the market moves *through* one of
those numbers — say from −2.5 to −3.5 — the frozen CBS line hands us the 3 for free. That
should be worth far more than the raw 1-point move suggests.

**It replicated everywhere we looked:**

| | Crossed 3 or 7 | Didn't cross | Gap |
|---|---|---|---|
| Train (2014–19) | 68.0% (n=97) | 53.2% | +14.8pp |
| Validate (2020–22) | 72.4% (n=87) | 55.3% | +17.1pp |
| **Every dev season** | **beat not-crossing 9 times out of 9** | | |
| **Holdout (2023–24)** | **62.9% (n=62)** | **55.7%** | **+7.2pp** |

Nine out of nine seasons, plus the holdout. This is not noise — and unlike the DVOA work,
it *strengthened* under scrutiny rather than dissolving.

**So why is `KEY_BONUS = 0.0` in the shipped code?** Because knowing an effect is real is
not the same as knowing how big it is. Fitting on train gave a bonus of **3.6 points**,
which validated beautifully (it cut the probability error on validate from +12.5pp to
+2.8pp). Then the holdout showed the dev gap had been roughly **twice** the true one:

| | Predicted | Actual | Error |
|---|---|---|---|
| With the 3.6 bonus | 69.7% | 62.9% | **−6.8pp** (overconfident) |
| With no bonus at all | 59.9% | 62.9% | **+3.0pp** (slightly under) |

The "improvement" made the model's probabilities **worse than leaving it alone**. A value
near 1.8 would calibrate nicely — but that number is only visible *by looking at the
holdout*, which would make it a parameter fitted on the test set and destroy the honesty of
every number in this document.

So it ships at zero. `Pick.key_number` still reports the flag, so the signal stays visible
and nobody has to rediscover it. **Re-fit and re-validate it once 2025+ seasons provide
fresh data** — that's the clean path, and it's a genuinely promising one.

> **Lesson:** a replicated effect measured on data you *selected it from* is systematically
> inflated. Direction survives out-of-sample far more reliably than magnitude. Estimate
> effect sizes on data you didn't use to find the effect — or shrink them hard.

### 5e. Market-based features that didn't add anything

| Idea | Train | Validate | Verdict |
|---|---|---|---|
| Moneyline drift as coin-flip tiebreak | 48.9–51.5% | 35.7–51.0% | Sign flip. Dead. |
| Key-number *proximity* on coin flips | 44.0% | 56.1% | Sign flip. Dead. |
| Filtering picks by moneyline confirming the spread | 55.0% | 56.7% | No better than the base signal rate (54.4/57.7). Pointless. |
| Extended key numbers (3/4/6/7/10) | weaker than 3/7 alone | | Dead — 3 and 7 carry it. |

**Moneyline drift deserves a note**, because on its own it looks fantastic: filtered to
large drifts it hits 57.6% train / 64.2% validate, and it strengthens monotonically with the
threshold in *both* eras — every hallmark of a real signal. It is real. It's also
**redundant**: it's measuring the same market move the spread already tells us about. When
used where it could add something new — the coin flips, where the spread is silent — it
collapses to noise. Same trap as DVOA in section 5a, reached from a different direction.

> **Lesson specific to pick'em: selectivity is worthless here.** You must pick every game,
> so a filter that finds a 64% subset adds nothing — you still have to pick the other games
> too. A feature only helps if it changes *which side* you take on games you're currently
> getting wrong. Always evaluate full-slate win rate, never the win rate of a filtered subset.

### 5f. Experiments that couldn't be run at all (data doesn't exist yet)

Not failures — genuinely blocked. Listed so nobody re-plans them without first solving the
data problem.

| Experiment | What's missing |
|---|---|
| **CBS post-offset isolation** (`CBS_bias = CBS_line − market_at_post`) | There are **no historical CBS lines**. The backtest's "pool line" is a *sportsbook opening line* used as a proxy. `live_line_at_post_home` exists in the tracker but holds 16 rows, all 2026 Week 1, none with results — and all currently equal to the CBS line, so the measured bias is zero. **Runnable only after a season of real captures.** |
| **Line-movement velocity** (Δline in the last 24h) | The history has exactly two snapshots per game, open and close. No intermediate timestamps. Needs a timestamped feed — the Odds API's historical endpoints cost 10× credits, which the free tier can't fund. |
| **Sharp-book directional agreement** | The history is a **single book**. No cross-book disagreement to measure. |
| **Public pick-percentage fading** | CBS community percentages exist for 16 games of 2026 Week 1 and nowhere else. No historical pick distributions. |

**All four become testable by logging weekly**, which costs nothing but discipline: record
CBS's line at post, a market line at that same moment, the total at both points, and the
community pick percentages.

**The plumbing for that now exists** (built 2026-08-22). Two commands a week:

```bash
# Tuesday, immediately after transcribing the CBS screenshot into
# data/pickem_current_week.csv. Order matters -- a "market at the moment CBS
# posted" reading is only that if you run it within a few minutes.
python3 scripts/pickem_capture.py --snapshot post --week 3 --confirm

# Before the first game of each day, once inactives are out.
python3 scripts/pickem_capture.py --snapshot lock --week 3 --confirm
```

Each run costs **2 Odds-API credits** (spreads + totals, one call covering the whole slate)
— about 72 credits for a full season, inside the 500/month free tier shared with the MLB
work. Rows land in `data/pickem_line_log.csv`, which is **committed** (all of it is public
market data, and it needs to survive a Streamlit rebuild and accumulate across a season).
Adam's own picks and standings stay in the gitignored `data/pickem/`.

`edge/pickem_log.py::cbs_bias` already implements the 5f formula and returns `None` until
there is data:

```
true_edge = (market_now − cbs_line) − cbs_bias        # cbs_bias = cbs_line − market_at_post
```

Nothing in the model consumes CBS bias yet, deliberately — it gets tested like everything
else here, on train/validate, once a season of captures exists. **A missed week is a
permanently missing row.** That is the entire cost of these experiments.

### 5c. Previously killed (from earlier sessions)

| Idea | Why it died |
|---|---|
| Direction of line move (toward favorite vs. underdog) | 60.7% in test, opposite sign in train. Textbook noise. Excluded on purpose. |
| Picking on the *closing* line instead of the frozen one | 48.9% — the negative control. Confirms the edge is staleness. |

---

## 6. Where the remaining upside actually is

We're at ~8.9 wins/week; the target is 10. Two full rounds of feature work now point the
same way: the gains are **not** in better modeling of any kind — football *or* market. Every
market feature tested was either redundant with the line movement we already use, or real
but unpriceable. Ranked by realistic promise:

0. **Run the weekly capture** (`scripts/pickem_capture.py`, 2 commands, 2 credits each).
   The plumbing is built as of 2026-08-22; all that remains is the habit. It is the only
   thing that unblocks four separate experiments — including CBS-bias isolation, the one
   with a genuine mechanism nobody else in the pool can exploit. A missed week is a
   permanently missing row.

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
3. ~~Multi-book consensus instead of one book's line~~ — **done** (2026-08-22).
   `edge/pickem_live.py` now averages every available book with sharp books upweighted.
   Note it cannot be backtested: the historical file is single-book, so the weighting is a
   reasonable prior, not a validated result. Circa is not distributed through The Odds API
   at all, and Pinnacle sits in the `eu` region (doubling per-call cost) — the default
   stays `us`-only.
4. ~~Pool-standings strategy~~ — **framework built** (2026-08-22), see section 7.
   Unvalidated by construction and gated to Week 14+. The real upgrade available to it is
   feeding it the pool's ACTUAL pick distribution instead of CBS's national percentages.

Genuinely dead ends, do not revisit: better team-strength ratings of any kind, coaching
records, moneyline-derived signals, anything else the bookmaker already sees at line-set
time, and any approach evaluated on a *filtered subset* rather than the full slate.

**Open and promising:** re-fitting the key-number bonus (5d) once 2025+ data exists. It is
the only tested feature whose effect survived the holdout — it just needs a magnitude
estimated on data that didn't select it.

---

## 7. Playing the standings (late season)

**Status: built, reasoned, and NOT validated.** Everything else in this document is backed
by a leak-free backtest. This is not, and cannot be yet — it needs historical pool standings
and opponents' weekly picks, which nobody recorded. It is standard tournament game theory
with deliberately simple arithmetic, and it is a heuristic. `edge/pickem_strategy.py`.

**Why it exists.** Maximising expected wins is the right goal only until about Week 14.
After that you are not trying to win the most games — you are trying to *finish in a paying
place*, and those differ. Your finish depends on your score **relative** to the field, so
what matters is the variance of (your score − theirs). Pick what everyone else picks and
that variance is near zero: a lead is preserved, and a deficit is frozen solid. Pick against
them and you manufacture the swings a deficit needs.

| Situation | Mode | Behaviour |
|---|---|---|
| Before Week 14 | `neutral` | Pure expected wins. Don't touch anything. |
| Leading, or already at target rank | `protect` | Shadow the field on coin flips so nobody gains ground cheaply. **Never** surrenders a real edge to follow the crowd. |
| Behind | `chase` | Diverge from the field, spending the cheapest games first. |

**The cost of divergence is expected wins — and the cheapest divergences are coin flips.**
Flipping a 50/50 game costs nothing (there was no edge to give up) but still separates you
from however much of the field was on the other side. That is close to free variance, and
it is why the module spends coin flips first and protects genuine edges last.

**How much to diverge.** Making up a *G*-win deficit needs swing; *n* divergent picks give a
relative-score standard deviation of roughly √*n*; wanting that on the order of *G* gives
*n* ≈ *G*², spread over the weeks left:

```
divergences_per_week ≈ gap² / weeks_remaining
```

Three back with six weeks left is a gentle 1–2 flips a week. Six back with two weeks left
demands ~18 — essentially the whole slate — which is correct: a hole that deep that late is
nearly hopeless, and the only guaranteed loss is playing it safe. Even then the module
refuses to flip a game whose edge exceeds 12%, because that is spending real advantage to
buy noise.

**The weakness, stated plainly:** `field_pct` comes from CBS's **national** community
percentages, not the 15–20 people Adam is actually playing. A national 80/20 can easily be
55/45 in a small pool, and the leverage maths is only as good as that number. The pool's own
picks are visible in the CBS app after each lock — feeding those in would upgrade this from
plausible to sound, and is the single highest-value improvement available to it.

## 8. Running it yourself

```bash
# Weekly capture -- the habit that unblocks section 5f (2 credits per run)
python3 scripts/pickem_capture.py --snapshot post --week N            # dry run
python3 scripts/pickem_capture.py --snapshot post --week N --confirm  # writes

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
| `edge/pickem_live.py` | Live lines + totals, multi-book weighted consensus (2 credits/week) |
| `edge/pickem_log.py` | Append-only snapshot log + the `cbs_bias` formula (section 5f) |
| `edge/pickem_strategy.py` | Late-season standings play (section 7). **Unvalidated.** |
| `scripts/pickem_capture.py` | Weekly capture CLI, dry-run by default |
| `scripts/pickem_feature_lab.py` | Experiment harness (both rounds). Refuses to read holdout seasons. |
| `scripts/pickem_backtest.py` | Out-of-sample backtest + the staleness negative control |
| `scripts/pickem_pbp_collect.py` | nflverse play-by-play → per-game efficiency table |
| `pages/4_🎯_Pickem.py` | The weekly picks screen |

**Note on `edge/pickem_features.py`:** it is deliberately kept in the repo despite shipping
nothing. It's the evidence behind section 5, it re-runs on demand, and if someone ever
wants to test a new efficiency idea the as-of-week plumbing (the hard, easy-to-get-wrong
part) already exists and is already leak-proof.
