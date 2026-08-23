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
| *Baseline: always pick the favorite (2023–24)* | — | 54.2% |
| *Baseline: always pick the home team* | — | 51.2% |

> **⚠ That 54.2% favorite baseline is an anomaly of the holdout era, and reading it as "the
> model barely beats chalk" is wrong.** Always taking the frozen-line favorite scores **48.2%
> (train), 48.3% (validate), 54.2% (holdout), 49.4% (all 2,878 games)**. 2023–24 was an unusually
> good era for favorites. The model's margin *over chalk* — which is what actually matters,
> because ~17 opponents mostly play chalk — is:
>
> | | model | chalk | **margin** |
> |---|---|---|---|
> | train 2014–19 | 54.9% | 48.2% | **+6.72pp** |
> | validate 2020–22 | 56.0% | 48.3% | **+7.70pp** |
> | holdout 2023–24 | 55.9% | 54.2% | **+1.69pp** |
>
> In a typical era the model is **6–8 points better than the field**, roughly one extra win a
> week against a chalk-picking opponent. In the holdout era it was 1.7. Both numbers are honest;
> quoting only the second understates the edge, and quoting only the first overstates it.
> Found 2026-08-23 (5j round 6) — the original table had gone unchallenged for three rounds.

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

### 5g. Two claims checked against the data (2026-08-22)

**"Around 75% of home teams cover the spread."** Widely repeated; **false**. Across all
2,878 games in the file:

| Measure | Result |
|---|---|
| Home teams **cover** the closing spread | **48.7%** (1359-1431-88) |
| Home teams cover the opening spread | 48.9% |
| Home favorites cover | 48.1% |
| Home underdogs cover | 49.6% |
| Home teams **win outright** (no spread) | **54.8%** |

The 54.8% straight-up figure is probably the source of the confusion, and it is a completely
different question. Home-field advantage is real — and the oddsmaker has already priced it
into the number, which is exactly why covering sits at a coin flip. Any strategy of the form
"always take home / always take away" is dead on arrival; the spread exists to kill it.
*(The one 75% in the table is home pick-em games, at n=4. Coincidence, not a signal.)*

**"If CBS says 3.5 and the market closed at 3, we hold the better side of the 3."** This one
is **true and already in the model** — it is the key-number effect from 5d, and the intuition
is sound: a 3-point margin is **14.4%** of all NFL games (7 is 8.5%), so holding +3.5 where
the market holds +3 wins outright every game that lands exactly on 3.

Tested a broader formulation than 5d's — "capture", defined as *our* number beating the
market's across a key (`side_line > k >= market_line`), which catches 542 dev games against
the old crossing rule's 185, and every crossing game is also a capture:

| | Train | Validate | Combined |
|---|---|---|---|
| Capture, all moves ≥0.5 | 57.5% | 61.8% | **59.2%** (z=+4.26, 7/9 seasons) |
| No capture | 53.2% | 55.7% | — |
| **Capture on 0.5-pt moves only** | 53.4% | **49.1%** | sign flip — dead |

So the effect is real in aggregate, but note the last row: **the exact scenario people
describe (a single half-point, 3.5 vs 3) does not survive validation.** The aggregate is
carried by moves of 1.0+ points. And since 5d already showed this family's dev magnitude is
roughly double what the holdout delivers, nothing here changes the shipped model.

**Two structural reasons this can't do what people hope it will:**

1. **It cannot add wins.** Capture is a property of a pick already made — it never changes
   *which side* you take, only how confident you should be. In a pool where you must pick
   every game, confidence pays nothing. (Same trap as 5e; see that section's lesson.)
2. **It cannot reach the close games.** Lines move in half-point steps, so a coin flip means
   the CBS number and the market number are *identical* — there is no gap to hold. Measured:
   **0 of 461** dev coin-flip games can capture a key number. It is blocked by definition,
   not by sample size.

### 5h. Can the pool's frozen line be scraped automatically? (2026-08-22)

Asked directly, tested properly, and the answer has two halves.

**The pool page itself: no.** `picks.cbssports.com/.../pools/<id>` fetched anonymously with
a real JS-rendering browser **redirects to `/join`** and serves pool settings plus a login
prompt — no team names, no spreads. That is authentication, not a scraping-technique
problem, and no amount of cleverness gets around it.

**CBS's public odds page is close, and using it would be a disaster.** `cbssports.com/nfl/odds`
is fully open, needs no key, and carries every game with open line, current line and total.
On 2026 Week 1 it agreed with the pool's frozen line on **12 of 16 games** — and disagreed on
exactly the four the market had moved since the freeze:

| Game | Pool (frozen) | CBS public (live) |
|---|---|---|
| Bills @ Texans | HOU −1.5 | **BUF −1.5** (favorite flipped) |
| Saints @ Lions | DET −7.5 | DET −7.0 |
| Falcons @ Steelers | PIT −3.5 | PIT −3.0 |
| Broncos @ Chiefs | KC −2.5 | KC −3.0 open |

**Those four disagreements are the entire edge.** The public page tracks the live market;
the pool freezes. Substituting one for the other would delete the signal and quietly return
the model to ~50% while still looking like it worked. This is the most dangerous available
shortcut, which is why it is written down.

**What automation is actually possible.** Two real wins, both shipped:

1. **A free market feed.** `edge/pickem_cbs.fetch_public_odds()` scrapes the public page for
   all 16 games — open line, current line, total — at **zero Odds-API credits and no key**.
   Note it also supplies an *opening* line, which the free Odds-API tier cannot (historical
   endpoints are paid-plan only).
2. **No more transcription.** `scripts/pickem_pool_import.py` turns text copied from the
   logged-in Picks page into `pickem_current_week.csv`, community percentages included. The
   fetch still needs a human session; the tedious part no longer needs a human.

Nothing in this repo stores, requests, or transmits CBS credentials, and nothing logs in on
your behalf — the importer parses a page you already opened.

### 5i. Spread regimes — "should there be several models, one per spread size?" (2026-08-23)

Asked in two stages, and worth keeping as one entry because the second stage is the more
interesting failure. Reproducible: `scripts/pickem_feature_lab.py`, "ROUND 3".

**Stage 1 — does accuracy vary by spread size or moneyline?** Stratified the shipped model's
own win rate. It varies, but not *consistently*, which is the whole question:

| Frozen spread | Train 2014–19 | Validate 2020–22 |
|---|---|---|
| 0–3 pts | 50.6% | **60.4%** |
| 3–6 pts | 56.1% | 55.4% |
| 6–9 pts | 56.8% | 57.1% |
| 9–13 pts | 54.6% | 51.6% |
| 13+ pts | **58.5%** | 42.4% |

**The extremes invert.** The best train bucket (13+) is the worst on validate; the worst
train bucket (0–3) is the best on validate. Only 3–9 holds steady — at roughly the model's
overall rate, i.e. no stratified edge at all, just the average showing up in a subgroup.

Moneyline (de-vigged implied probability of the closing favorite) does the same thing: the
best train bucket (0.60–0.65, 59.0%) ranks 4th of 5 on validate, and the best validate
bucket (0.50–0.55, 64.3%) ranks 4th of 5 on train. No monotonic relationship in either era,
and no agreement between eras.

**Stage 2 — should the RULE change per regime?** This is a genuinely different proposal from
everything else in this graveyard, and it deserved a real test: a per-bucket rule change alters
*which side* we take, so unlike 5d/5e/5g it is capable of adding wins.

The motivating intuition (Adam's, stated plainly): books must be less accurate setting a
14-point number than a 3-point number, and on a two-touchdown spread it feels wise to take the
points.

**The premise is directly falsifiable, and it's false.** The book's error is the same size at
every spread:

| Frozen spread | σ of (actual margin − line) |
|---|---|
| 0–3 pts | 13.14 |
| 3–6 pts | 12.99 |
| 6–9 pts | 12.91 |
| 9–13 pts | 13.38 |
| **13+ pts** | **12.95** |

A 14-point line is as accurate as a 3-point line. (This also retroactively justifies the model's
flat σ = 13.45 — no per-bucket σ is warranted.) With no accuracy gradient, there is no mechanism
for a big-spread regime to exploit.

**"Take the points" tested at rising thresholds** — it never clears 50% on validate, and it gets
*worse* as the spread grows, the opposite of the hypothesis:

| Threshold | Train | Validate |
|---|---|---|
| 7+ | 52.6% | 47.9% |
| 9+ | 51.0% | 45.3% |
| 11+ | 47.8% | 44.0% |
| 13+ | 47.7% | 42.4% |
| 14+ | 50.0% | 42.9% |

**Why the anecdote feels true anyway** — this is the part worth remembering, because the
intuition is tracking something real, just not the thing it thinks:

| | \|line\| ≥ 13 | \|line\| 3–9 |
|---|---|---|
| dog covers | **45.9%** | 53.2% |
| median outcome | **−1.00** (favorite side) | +1.00 (dog side) |
| when the dog covers, by | **+10.74** | +9.82 |
| when it doesn't, by | −10.01 | −10.77 |

Big-dog covers are **rare but enormous**. The backdoor cover, the garbage-time touchdown, the
31–14 final that "wasn't as close as it looked" — those are the memorable games, and they cover
by double digits. The modal outcome is the favorite quietly covering, which is forgettable. The
mean and the median point *opposite ways*, which is exactly the signature of an availability-bias
intuition. Note also that the folklore has the direction backwards: mid-sized dogs (3–9) cover
more often than huge ones.

Six rules × five buckets were tested (shipped, inverted, always-dog, always-favorite,
always-home, dog-on-coin-flips). One cell beat shipped in both eras — underdog on coin flips
within 9–13 point spreads, +0.7pp train / +1.1pp validate — on **n=27 and n=18**, which is
**0.30 games per week**. It is 1 of 30 cells tested. See the control below for why that is
exactly what you'd expect from nothing.

**The exposure ceiling, worth knowing before anyone proposes this again:** spreads of 9+ are
**2.3 games per 16-game week**. Even a genuine 5pp edge confined to that regime is worth 0.1
wins per week. The upside was small before the testing started.

#### The selection-bias control — the reusable part of this entry

Fitting a rule choice per bucket is a **best-of-N search**, and best-of-N looks good on pure
noise. So the harness runs the *identical* procedure (pick the best of 6 rules in each of 5
buckets on train, apply to validate) against 400 sets of coin-flip outcomes, where by
construction there is nothing to find:

| | Validate full-slate rate |
|---|---|
| Shipped one-size-fits-all | **56.0%** |
| Best-per-bucket fitted on train | **52.8%** (−3.2pp) |
| *Null procedure, mean* | −0.2pp |
| *Null procedure, 95th percentile* | **+3.6pp** |
| *Null procedure, max* | +9.8pp |

Empirical p = **0.887** — the real result was matched or beaten by 355 of 400 coin-flip runs.
The regime model didn't just fail to help, it did **worse than the single rule** it was meant
to improve on.

> **The methodological lesson, and the reason this control is now permanent:** the 95th
> percentile is the bar. With 5 buckets and 6 rules, a **"+3pp regime improvement" would have
> been an ordinary draw from pure noise** — and it would have looked like a real finding in a
> table. Any future proposal that slices the slate into subgroups and fits something per
> subgroup must be scored against this null, not against 50%. This is the first tool in the
> project that puts a number on how good a multi-bucket result has to look before it means
> anything.

**Holdout note:** the go/no-go here was made entirely on dev. A descriptive look at the holdout
was taken during the session (the same category as the existing `calibration_by_move_size` table
in `pickem_backtest.py`, which already reports holdout cover rates by bucket) and showed the same
era-to-era inconsistency — but **nothing was selected, fitted, or tuned on it**, and the shipped
model is unchanged, so the 55.9% headline remains an honest once-evaluated number.

**Why it failed, in one line:** the size of the spread is information the bookmaker *has* when
setting the line, so it is priced. Same wall as 5a and 5b, reached from a third direction. The
edge is still time, not football.

### 5j. The situational search — 10 rounds of outside ideas (2026-08-23, ongoing)

**The setup.** A domain-expert agent (sharp NFL/betting/pick'em knowledge, deliberately kept
naive to this model's internals) reads this document fresh each round, proposes 3+ testable
hypotheses, they get tested on dev, and the results are written back here. Ten rounds, hunting
for a genuinely novel angle worth an extra win or two per week.

**What made this possible:** `scripts/pickem_situational_collect.py` pulls nflverse's
`games.csv` and joins it onto the odds history — **2878/2878 games, zero misses**. That adds
15 columns the project had never used: rest days, weather (temp/wind), roof, surface, division
flag, weekday/kickoff time, neutral site, **starting QB**, referee. Section 5f's "blocked for
lack of data" list got materially shorter.

**The protocol, which matters more than any single round:**
- **The holdout is not touched.** Nothing in this search reads 2023–24. Survivors become
  candidates; the holdout is spent later, once, on a finished model.
- **Rules are judged only on the games they FLIP.** A rule agreeing with the shipped model 95%
  of the time can't be judged on overall win rate — that number is dominated by picks it didn't
  make. Its entire effect lives in the disagreements (5e).
- **The significance bar rises with the ledger.** `data/pickem_idea_ledger.json` counts every
  hypothesis ever tested; the bar is Šidák-adjusted for the cumulative count. Test 1 needs
  z=1.96; test 30 needs ≈z=3.0. Across 30 tests, ~1.5 will clear conventional significance by
  luck, and the ledger is what stops one being written up as a discovery.
- **A live demonstration of why:** a deliberate placebo rule (parity of the home team's
  abbreviation length, applied to coin flips) hit **z = −2.06 on train** before flipping sign on
  validate. Meaningless by construction, "significant" in one era.

#### Round 1 — six ideas, all dead

| # | Hypothesis | Flips | Train | Validate | Verdict |
|---|---|---|---|---|---|
| 1.1 | Fade the spread move when the total didn't corroborate it | 509 | 44.5% | 43.5% | negative both |
| 1.2 | Scale the frozen line by scoring environment (total) | 0 | — | — | killed by regression |
| 1.3 | Outdoor wind ≥13 (and ≥15) → take the underdog | 134 / 89 | 46.4% | 46.0% | negative both |
| 1.4 | Fade small primetime moves toward the favorite | 134 | 43.7% | 48.9% | negative both |
| 1.4c | *Control: same rule on 1pm Sunday games* | 495 | 49.5% | 42.6% | control also fired |
| 1.5 | Back the team whose QB changed when the line moved ≥2 against it | 120 | 39.3% | 42.4% | negative both |
| 1.6 | Divisional rematch → back the meeting-1 blowout loser | 82 | 35.4% | 32.4% | negative both |
| 1.6i | *Inverse: back the blowout winner instead* | 67 | 37.3% | 50.0% | also negative |

Percentages are on flipped games only — i.e. how often the new rule was right where it
overrode the shipped model. Anything under 50% means the override cost wins.

**Three diagnostics worth keeping even though every rule failed:**

**(a) The frozen line is an unbiased predictor of margin, and there is no scoring-environment
scaling.** Regressing actual margin on the frozen line gives a coefficient of **−1.048 (train)
and −1.042 (validate)** — essentially exactly 1.0 in the sign convention used here. The line is
not systematically too steep or too shallow at any level. The interaction with the projected
total, which would show margins scaling with the scoring environment, came out **+0.0026 on
train and −0.0192 on validate** — a sign flip, and both an order of magnitude below the
+0.015–0.03 the hypothesis needed. Killed by one regression before any pick logic was built.

**(b) Wind: the mechanism is real, the consequence isn't.** This is the most interesting failure
of the round, because the premise checked out exactly as predicted:

| Check | Result | Reading |
|---|---|---|
| corr(wind, total move) | **−0.138** | the market *does* cut the total for wind in-week |
| corr(wind, spread move) | **−0.028** | the market does *not* move the spread for it |

So wind really is post-freeze information that the market prices into one number and not the
other. But the payoff never appears — underdog cover rate by wind is **non-monotone** (49.3% at
0–9mph, 54.4% at 9–13, 53.1% at 13–18, **48.6% at 18+**), with the windiest bucket the *worst*.
And the **dome placebo covers 52.9%** — higher than the calm-weather outdoor bucket, which means
the mid-range "signal" isn't a wind effect at all. Wind compresses scoring without
systematically helping the side getting points.

*(Honesty note: `wind` is the observed value at kickoff, not a Tuesday forecast, so this test was
already an upper bound on what's achievable live. It failed as an upper bound.)*

**(c) A directionally correct finding that pays nothing.** The total-corroboration idea predicted
that spread moves confirmed by a falling total are information, and moves with a rising total are
just money. The gap is **real and in the predicted direction**: the shipped model goes **59.9%
when the total fell ≥1** vs **55.8% when it rose ≥1**. But both are comfortably above 50%, so
fading the "money" bucket loses badly (44.5%/43.5%). The signal is a *confidence* distinction,
and confidence pays nothing in pick'em (5e). A textbook case of a correct insight with no
available action.

**Two controls that did their job.** The primetime idea's 1pm-Sunday control fired *harder* than
the primetime bucket itself (42.6% vs 48.9% on validate), proving any effect belonged to move
size rather than primetime shading. And on divisional rematches, backing the blowout loser *and*
backing the blowout winner both lost — meaning the market's in-week movement already prices the
rematch dynamic, and any override is worse than following it.

#### Round 2 — the half-point zone, the deadline, and the tiebreak's holes

The round-2 scout opened by **killing two idea families before proposing anything**, which is
worth recording because both are perennial re-proposals:

- **Every variance/σ-based angle is dead by construction.** At an unbiased line,
  P(cover) = Φ(edge/σ), which is exactly 0.5 at edge = 0 *regardless of σ*. σ scales the payoff
  of staleness; it cannot create an edge where none exists. Combined with 5j(a)'s finding that
  the line is unbiased at every level, every "low-total games compress margins" or "volatile
  team" idea is a confidence distinction before it is tested.
- **Week 1 lines are not contaminated.** The worry: a Week 1 "opener" is a spring number
  containing months of pre-Tuesday movement. Measured: Week 1 mean |open−close| is **1.05 vs
  1.25 for Weeks 2+**. Week 1 moves are *smaller*. Not a problem.

**The reframing that shaped the round:** **47.6% of the slate has ≤0.5 points of signal** —
650 dev games (4.45/wk) move exactly a half point, 461 (3.16/wk) don't move at all. Any
remaining upside has to live there.

| # | Hypothesis | Flips | Train | Validate | Verdict |
|---|---|---|---|---|---|
| 2.1 | Treat \|move\|=0.5 as no signal → route to totals tiebreak | 319 | 47.9% | 43.0% | negative both |
| 2.1b | Same, only flat-zone halves (no key number in span) | 167 | 52.6% | 37.7% | sign flip |
| 2.1c | *Control: route \|move\|=1.0 to tiebreak* | 173 | 44.0% | 50.0% | negative both |
| 2.2 | Fade the toFAV_totUP cell (max-recreational signature) | 388 | 46.5% | 46.3% | negative both |
| 2.3 | Raise threshold to 1.5 on Sunday-late games | 157 | 45.9% | 39.1% | negative both |
| 2.4 | Fade small move toward team off a ≥14 / ≥17 / ≥21 win | 191/138/93 | 51.1/48.5/47.0% | 48.1/39.0/37.0% | sign flip, then negative |
| 2.4a | *Control: toward team off a ≥14 LOSS* | 149 | 49.5% | 39.5% | negative both |
| 2.4b | *Control: same rule on \|move\|≥2* | 118 | 38.9% | 37.0% | negative both |
| 2.5a | Tiebreak fall-through → take the underdog | 69 | 35.3% | 54.3% | sign flip |
| 2.5b | Referee crew home-lean on coin flips | 104 | 46.2% | 51.3% | sign flip |

**(d) The deadline worry is not supported — and this one changes a priority.** Section 6 ranks
"time the picks better" as the **#1 remaining upside**, on the reasoning that the backtest
approximates Adam's lock with the *closing* line, so late-window games (4:25pm, 8:20pm) should
carry post-deadline information he'll never have. If true, the 55.9% headline would be inflated.
Measured, shipped-model win rate by kickoff window and move size:

| Window | coin flips *(control)* | 0.5–1.5 pts | 2.0+ pts |
|---|---|---|---|
| Sunday early (≤13:00) | 53.2% (n=235) | 54.2% (n=655) | **60.7%** (n=323) |
| Sunday late (≥16:00) | 56.0% (n=125) | 54.4% (n=395) | **54.1%** (n=183) |
| Non-Sunday | 55.4% (n=83) | 53.3% (n=195) | 58.6% (n=87) |

**⚠ RETRACTED — this analysis was wrong. See the correction immediately below.**

The original reading was: "the prediction was that late games would outperform; they
underperform by 6.6pp, so there is no evidence that post-deadline movement is where the edge
lives," and section 6's ranking was flagged as unsupported on that basis.

**The error: those numbers are POOLED across train and validate.** This document's own standard
— stated in section 3 and applied to every other result here — is that a difference means
nothing until it holds in both eras. That check was skipped. Round 3 ran it:

| Window, 2+ pt moves | Train 2014–19 | Validate 2020–22 |
|---|---|---|
| Sunday early | 62.4% (n=205) | 57.6% (n=118) |
| Sunday late | **50.0%** (n=104) | **59.5%** (n=79) |

**The gap sign-flips.** Late games are worse in train and *better* in validate. Pooled, the
early-vs-late difference is z = 1.44 — under this project's noise floor. There is no late-kickoff
anomaly. The thread is closed, and section 6's ranking is restored to what it was.

*Kept rather than deleted because the failure mode is the instructive part: pooling two eras
manufactured a clean-looking 6.6pp effect out of a sign flip, and it was reported as
decision-relevant before the era split was run. That is the exact mistake sections 5a and 5b
exist to warn about, committed while writing the document that warns about it.*

**(e) The "fade the uninformative move" family is now closed permanently.** Round 1 tested it
one way (total direction alone); round 2 split it into the full 2×2 of spread direction ×
total direction, hunting a cell below 50% that could be flipped:

| Cell | Train | Validate |
|---|---|---|
| toFAV_totUP *(max recreational)* | 53.5% | 53.7% |
| toFAV_totDN | 53.8% | 60.2% |
| toDOG_totUP | 55.6% | 60.0% |
| toDOG_totDN | 56.3% | 54.8% |

**Every cell is above 53% in both eras.** The money-vs-news distinction is real and it is
*entirely* a confidence distinction — there is no cell where the market's move is bad enough to
fade. Do not propose a variant of this again.

**(f) MOVE_FLOOR = 0.5 is validated, from the opposite direction.** Following the move wins
**53.0% on half-point moves, 52.6% on 1.0-point moves, 58.7% on 1.5+**. So small moves genuinely
carry less signal — but routing them to the totals tiebreak is *worse* (47.9%/43.0%), because the
tiebreak is the weaker rule. A 53% signal beats the best available alternative, so the threshold
stays where it is. The 1.0-point control confirms it: routing those to the tiebreak also loses.

Both of round 2's designed controls fired correctly again — the symmetry placebo (blowout
*losses*) and the magnitude placebo (large moves) both went negative, confirming there was no
recency mechanism to find.

#### Round 3 — a shipped-code bug, and a real threat to the headline number

Round 3 produced no working feature either, but it is the most consequential round so far: it
found a **sign error in shipped code** and a **calibration problem that probably means the live
edge is smaller than 55.9%**. Both outrank any pick rule this exercise could have found.

**(a) Drift is front-loaded — and that lands before CBS freezes.** The backtest's "frozen" line
is a sportsbook **opener** (posted Sunday night / Monday). CBS freezes **Tuesday**. Those are
different numbers, and the difference only matters if meaningful movement happens in that first
day or two. Test: if drift accumulated like a random walk, games further from the opener should
move more, by √time.

| Kickoff | Days from opener | mean \|move\| | observed / Sunday | random-walk expectation |
|---|---|---|---|---|
| Thursday | ~3 | 1.234 | **0.994** | 0.707 |
| Sunday | ~6 | 1.242 | 1.000 | 1.000 |
| Monday | ~7 | 1.214 | **0.977** | 1.080 |

**A Thursday game has half the time and moves the same amount. A Monday game has an extra day
and moves slightly less.** Movement is essentially independent of elapsed time, which means it is
concentrated in the first ~2 days after the opener — *before* CBS posts.

**Why this matters more than any feature in this document:** the backtest credits the model with
the full opener→close gap. Adam only ever gets the Tuesday→lock gap. If the early correction is
the large and high-quality part, the live edge is materially smaller than the backtested one.

**What this does NOT establish, stated plainly:** the follow-up test — is a point of *short-window*
movement worth more than a point of *long-window* movement — is underpowered and inconclusive.

| Kickoff | flat | 0.5–1.5 pts | 2.0+ pts |
|---|---|---|---|
| Thursday | 63.6% (n=33) | 53.8% (n=80) | 63.9% (n=36) |
| Sunday | 54.2% (n=360) | 54.3% (n=1050) | 58.3% (n=506) |
| Monday | 52.9% (n=34) | 52.4% (n=84) | 51.3% (n=39) |

Thursday looks better at 2+ points (63.9% vs 58.3%) — but n=36, and Thursday's *flat* games come
in at 63.6% on n=33, which is nonsense (flat games have no signal and must sit near 50%). That
tells you the Thursday column is noise-dominated. Monday is worse everywhere, which points the
opposite way. **So: the amount of movement is clearly front-loaded (D1 is solid, n=2,275); whether
the early points are worth more per point is unresolved.**

**This is the single highest-value thing the weekly capture log can settle**, and it raises the
stakes on item 0 in section 6 considerably. One season of `market_at_post` readings answers it
directly. Until then, treat 55.9% as an **upper bound on live performance**, not a forecast.

**(b) A sign error in `edge/pickem_log.py::cbs_bias` — found, fixed, regression-tested.** The
function returned `cbs_line − market_at_post`, and the prescribed formula was
`true_edge = (market_now − cbs_line) − cbs_bias`. Substituting:

```
(market_now − cbs) − (cbs − at_post)  =  market_now + at_post − 2·cbs      ← what it computed
                                intended:  market_now − at_post             ← pure drift
```

It **doubles the offset instead of removing it**, and is wrong in exactly the case it exists for:

| Case | As documented | Intended |
|---|---|---|
| CBS matches market, no drift | +0.0 | +0.0 ✓ |
| **CBS off by 1, no drift** | **−2.0** | **+0.0** ✗ |
| CBS matches, market drifts 1 | −1.0 | −1.0 ✓ |
| **CBS off by 1 and 1 pt drift** | **−3.0** | **−1.0** ✗ |

Nothing consumed it (5f deliberately left it unwired), so no pick was ever affected — but it
would have silently corrupted the first season of captures. Renamed to **`cbs_offset`**, returning
`market_at_post − cbs_line`, so the two components simply **add**:
`total_edge = cbs_offset + drift`. Guarded by `test_cbs_offset_and_drift_add_to_the_total_edge`.

**(c) …and the correction it was built for is itself conceptually wrong.** 5f framed the CBS
offset as house methodology to be *netted out*. That is backwards. **The pool grades against
CBS's number**, so a point of offset is worth exactly as much as a point of drift — both measure
how far the number you are scored on sits from the market's best estimate. Subtracting the offset
would **delete real value**, not isolate noise. It would only be correct if CBS's number were a
*better* predictor than the market's at the same instant, which is not credible for a media
company setting lines "at its own discretion."

The decomposition is still worth logging — but as a **measurement**, not a subtraction. The null
worth testing after a season of captures is **β_offset = β_drift**, not β_offset = 0. This is
recorded in the function's docstring so the trap is not re-entered.

**(d) Pick rules tested, all dead:**

| # | Hypothesis | Flips | Train | Validate | Verdict |
|---|---|---|---|---|---|
| 3.4 | Frozen line 3.5/7.5 + small move → take the points | 90 | 53.2% | 57.1% | consistent but z=+0.84 vs bar 3.06 |
| 3.4a | *Mirror control: 2.5/6.5 → take the favorite* | 89 | 61.9% | 34.6% | sign flip |
| 3.4b | *Placebo: non-key 4.5/8.5 → take the points* | 30 | 38.9% | 41.7% | negative both |
| 3.5 | Divisional → take the underdog | 389 | 44.0% | 43.3% | negative both |
| 3.5b | Divisional → take the road team | 376 | 45.6% | 38.9% | negative both |

3.4 is the closest thing to a survivor in 27 tests — era-consistent and positive — and it still
fails, for the right reason: **the mirror control sign-flips and the placebo goes negative.** A
genuine key-number-lumpiness effect must be symmetric; this one isn't, so the positive result is
noise wearing a good mechanism. The scout that proposed it predicted this outcome and recommended
skipping it, which is worth more than the test.

**(e) The divisional effect: the most stable subgroup in the project, worth exactly nothing.**
The shipped model's divisional win rate, every dev season: **57.4, 58.3, 56.4, 59.6, 59.1, 58.5,
59.8, 57.3, 59.1** — a 3.4-point range across nine years, vs 52.9%/54.4% on non-divisional. Nothing
else in this document is that stable. And every side-changing version of it loses badly (take the
dog: 44.0%/43.3%; take the road team: 45.6%/38.9%). It is a pure confidence distinction — the
fifth one this exercise has produced, and the cleanest illustration of 5e in the document.

#### Round 4 — a survivor, later substantially downgraded by round 5

> **⚠ RETIRED 2026-08-23. Do not ship this.** Round 4 optimised **P(top 4)**, which is not the
> payoff function. Re-run with the *correct pool size* (20 players, not 18), the *actual prize
> ladder*, and the round-4 confound removed, the tournament strategy is worth **−$32 to +$13 a
> season — indistinguishable from zero, and negative more often than not.** See "Season
> simulation" below. The section is kept because the reasoning is sound and the failure is
> instructive, but the recommendation is withdrawn: **keep the shipped totals tiebreak.**


**Not agent-scouted.** The round-4 scout was cut off by an API spend limit, so this pursued the
one fully-specified thread round 3's scout left behind rather than substituting a less
independent idea generator. Worth flagging: rounds 1–3 drew their value from the scout being
*naive* to this model, and that property is absent here.

**The reframe: this pool is a tournament, not a prediction task.** The payout is
$1,200/$450/$300/$150 for the **top four of ~18**. Adam does not get paid for wins; he gets paid
for **rank**. Those are different objectives, and 27 failed hypotheses have all been attacks on
the first one.

**(a) The ~3 coin flips per week are genuinely free.** Every candidate rule for the flat zone
sign-flips between eras:

| Flat-zone rule | Train 2014–19 | Validate 2020–22 |
|---|---|---|
| Shipped totals-drift tiebreak | 57.3% | **49.0%** |
| Always favorite | 51.0% | **43.3%** |
| Always underdog | **49.0%** | 56.7% |

Pooled, the tiebreak is 54.4% with a 95% CI of **[49.7%, 59.1%]** — it includes 50%. Three
rounds have now failed to find a football rule here, and the honest conclusion is that **flat-zone
EV is 50%**. Which means those picks cost nothing to spend on something else.

**(b) A confound that had to be removed first — and it looked like a result.** Simulating with the
*real* flat-zone outcomes credits each policy with whatever it happened to score on dev. Since the
shipped tiebreak pooled to 54.4% (that era sign flip), the simulation handed it a spurious
win-rate advantage, and shadowing appeared to *reverse* and lose by up to −14pp at reduced edge.
Randomising flat-zone outcomes to true coin flips gives all policies identical expected wins,
leaving correlation with the field as the only difference. **That reversal was an artifact of
noise being fed back in as if it were skill.**

**(c) The pure tournament effect.** P(top 4 of 18), flat-zone outcomes randomised, 20k sims/season:

| Field quality | Edge surviving | current | **shadow** | diverge | shadow − current |
|---|---|---|---|---|---|
| weak (lean .55–.90) | full | 83.3% | **85.7%** | 81.7% | **+2.33pp** |
| weak | 60% | 65.6% | **66.6%** | 64.7% | +1.03pp |
| weak | 30% | 49.9% | 49.7% | 50.0% | −0.13pp |
| realistic (.70–.95) | full | 88.1% | **92.9%** | 85.3% | **+4.73pp** |
| realistic | 60% | 72.0% | **74.4%** | 70.3% | +2.49pp |
| realistic | 30% | 57.5% | **58.3%** | 56.9% | +0.86pp |
| sharp (.85–1.00) | full | 93.3% | **99.4%** | 89.7% | **+6.06pp** |
| sharp | 60% | 80.3% | **84.0%** | 77.5% | +3.74pp |
| sharp | 30% | 67.2% | **69.8%** | 65.6% | +2.58pp |

**Shadowing the field on free picks wins in every cell where the model has a real edge, and the
advantage grows as the field gets sharper** — which is the signature of a correlation effect, not
an artifact. It also survives edge shrinkage, which matters given round 3(a).

**The mechanism, plainly:** when you pick the same side as the field on a game nobody can
predict, you and your opponents win or lose it *together*. That noise cancels out of the
comparison, so your finishing position is decided by the games where you actually differ — the
ones where you have an edge. Diverging does the opposite: it injects coin-flip noise into the
exact comparison you want decided by skill.

**(d) It does not flip for a winner-take-all prize, and that is correct.**

| Prize shape | current | shadow | diverge |
|---|---|---|---|
| 1st only (weekly prize) | 63.1% | **64.0%** | 62.3% |
| top 2 | 76.8% | **79.3%** | 74.8% |
| top 4 (season money) | 88.1% | **92.9%** | 85.3% |
| top 8 | 96.5% | **99.7%** | 93.8% |

The advantage shrinks as the target narrows (+4.7pp → +0.9pp) but never reverses, because
variance-seeking is for **underdogs** and this model is the field's *favorite*. Section 7's
`chase` mode already covers the genuinely-behind case; this is about the default policy.

#### What this does and does not justify

**It does NOT add a single expected win.** Adam's stated goal is 10 wins/week; this contributes
nothing to that. It converts picks that were already worthless into rank protection.

**Three caveats that keep this from being shipped on the spot:**
1. **It is a simulation, not a backtest.** Every other number in this document comes from real
   games. This one comes from an invented opponent model — 17 players who take the frozen
   favorite with probability drawn from a range. Real pool opponents are not that.
2. ~~**The "field" signal available live is CBS's *national* community percentage, not the 15–20
   people Adam is actually playing.**~~ **RESOLVED 2026-08-23 — this caveat is much weaker than
   it looked.** Adam confirmed he cannot see his pool's picks until after lock, so the strategy
   has to run on the national number. Tested directly by comparing two policies: *oracle*
   (shadow the realised 18-person majority — impossible in practice) vs *prior* (shadow the
   a-priori popular side — what he can actually do):

   | Field | Edge | current | shadow (prior) | shadow (oracle) | prior captures |
   |---|---|---|---|---|---|
   | weak (.55–.90) | full | 83.5% | 85.6% | 85.6% | **99%** |
   | weak | 60% | 65.4% | 66.8% | 66.8% | 96% |
   | realistic (.70–.95) | full | 88.1% | 92.9% | 92.9% | **100%** |
   | realistic | 60% | 72.1% | 74.6% | 74.6% | 100% |
   | sharp (.85–1.00) | full | 93.3% | 99.4% | 99.4% | **100%** |

   **Shadowing the predictable crowd captures 96–100% of the benefit of shadowing the actual
   one.** The mechanism is obvious once seen: with 18 opponents who all lean the same way, the
   realised majority *is* the popular side almost every time, so observing their picks tells you
   something you could already predict. Not being able to see the pool costs essentially nothing.

   *The residual risk this does not cover:* if Adam's specific 18 are systematically unlike the
   national population — a cluster of fans of one team, say — the national number could point at
   the wrong crowd. Unmeasurable until he logs a season of post-lock pool picks, and cheap to
   check once he does.
3. **It contradicts the current gating.** `edge/pickem_strategy.py` restricts conformity to Week
   14+. This result says the default on free games should be shadow **from Week 1** — a change to
   shipped behaviour that deserves an explicit decision, not a quiet edit.

**Recommended, not shipped.** The cheap version: on COIN FLIP games only, take the side CBS's
community percentage favours, and log it. One season of real pool pick distributions turns this
from a simulation into a measurement — and that is the same capture habit section 6 item 0 already
asks for.

#### Round 5 — the objective was wrong, and it costs the only survivor most of its value

Round 5's scout attacked round 4 rather than proposing more football, and it was right to.

**(a) Two families killed before proposing, both worth keeping.**

- **Weekly outcomes are not overdispersed.** Tested whether favorite-cover counts per week
  cluster more than binomial across 155 dev weeks: **χ² = 115.8 on df = 154, ratio 0.752,
  z = −2.18.** If anything *under*-dispersed. There is no "chalk week" or "weather week" to buy
  variance from, and observed weekly-score SD (2.07) matches independent binomial (≈2.0). This
  kills any "correlate your divergences with each other" idea at the premise — and also kills
  "use Sunday-early results to update Sunday-late picks," because there is nothing to learn.
- **Round 4's national-vs-pool caveat is analytically small.** If 18 players are a draw from the
  national population, pool% has SE = √(.8×.2/18) = **9.4pp**, so a national 80/20 landing at
  55/45 in the pool is 2.7 SD away (~0.4%). The national number identifies the majority side
  essentially always — confirming the simulation in round 4's caveat #2 from a second direction.
  **The real risk is selection, not sampling:** a $150 self-selected pool may carry a local-team
  bias. That is what a season of post-lock captures should check.

**(b) The measurement that recalibrates round 4.** The shipped model agrees with "take the
frozen favorite" on only **48.0%** of dev games. It already diverges from chalk on **7.66 games
per week (SD 2.05, range 2–12)**. Round 4 was tuning **3** coin flips while nearly **8** games
swung uncontrolled. Adam's weekly score is already close to uncorrelated with a chalky field —
he is not a conformist who should shadow more; he is at high differentiation *by accident*.

**(c) Scored in dollars, the picture changes.** The payout is **$1,200/$450/$300/$150** for the
season *plus* **18 × $50 = $900** in weekly prizes — nearly 30% of all prize money, and
winner-take-all in a single week, which is the regime that *rewards* variance. Simulation with
flat-zone outcomes randomised (so all policies have identical expected wins):

| Field | β | policy | season $ | weekly $ | **total $** | vs current |
|---|---|---|---|---|---|---|
| chalky | 0.0 | current | 939 | 238 | 1177 | — |
| chalky | 0.0 | **shadow** | 980 | 222 | **1202** | **+25** |
| chalky | 0.0 | diverge | 909 | 251 | 1160 | −17 |
| chalky | 0.2 | current | 1038 | 328 | 1366 | — |
| chalky | 0.2 | **shadow** | 1116 | 318 | **1434** | **+68** |
| chalky | 0.2 | diverge | 997 | 334 | 1331 | −35 |
| mixed | 0.0 | current | 746 | 124 | 870 | — |
| mixed | 0.0 | shadow | 758 | 110 | 869 | **−1** |
| mixed | 0.0 | diverge | 739 | 136 | 876 | +6 |
| mixed | 0.2 | shadow | 911 | 208 | 1119 | +5 |

**The scout's mechanism is confirmed: shadowing loses money on the weekly pot in every single
cell, and diverging gains it.** But the scout's arithmetic understated the season side — it
assumed shadowing only buys marginal 4th-place slots worth $150, when protecting an edge lifts
the *whole* ladder including the $1,200 top rung. Net, shadow still wins in chalky fields and is
a wash in mixed ones.

**(d) The row that decides it.** Round 3(a) says the live edge is probably below the backtested
one. Under that assumption the advantage evaporates:

| Edge surviving | current | shadow | diverge |
|---|---|---|---|
| full | $1366 | **$1434 (+68)** | $1331 (−35) |
| 60% | $1152 | **$1165 (+14)** | $1134 (−18) |
| 30% | $965 | $955 (**−10**) | $964 (−1) |

**Shadowing helps only when the model's edge is close to its backtested strength.** At 30% edge
survival it is slightly *negative*. Combined with the mixed-field column, the honest summary is
that round 4's finding lives in one corner of the assumption space — chalky field **and** strong
edge — and is worth ~$0 outside it. *(Control: with Adam forced to a pure coin flip, all three
policies converge to exactly $759, so the separation is not a simulator artifact.)*

**(e) β cut the other way, which is a point in round 4's favour.** The scout predicted that
letting opponents favour chalk more steeply on big spreads (β>0) would shrink the shadow
advantage to under +1pp, because the field would be split on the small-spread games where flat-zone
picks live. It did the opposite: **+$25 at β=0 rose to +$68 at β=0.2.** A sharper field is a more
*coherent* field, so there is more correlation available to buy. Flat-zone games turn out to have
median |spread| 3.5 (quartiles 3/3.5/7), not the near-pick'em lines the objection assumed.

**Where this leaves the project.** The scout's closing judgement is worth recording verbatim in
substance: every effect in the tournament frame is worth roughly **$10–70 per season against a
$150 entry** — real but small — while **section 6 item 0, the weekly capture habit, still
dominates all of it**, because it is the only thing that converts round 3(a) from a threat into a
measurement. Knowing whether 55.9% is real is worth more than every strategy refinement found here.

**(f) The tiebreaker question is closed: THERE IS NO TIEBREAKER.** Adam confirmed (2026-08-23)
that **the weekly $50 is split evenly among everyone tied on wins.** The scout's proposal 5 — solve
the total-points tiebreak as a bidding game, projected at $10–25/season — is therefore void, and a
solved version of it was deleted from this document rather than left to mislead. Record the rule
so it is never re-derived:

> **Weekly prize: $50, split equally among all players tied for the most wins. No total-points
> tiebreaker exists.** ~40% of dev weeks end with a shared top score, so splitting is common.

Two consequences worth keeping:
- **Round 5's dollar simulation was already correct on this point** — it modelled the weekly pot
  as `$50 / n_tied` for ties rather than winner-take-all, so the numbers in (c) and (d) stand
  unchanged.
- **Splitting slightly weakens the case for divergence.** Under true winner-take-all you must
  beat everyone outright, which rewards variance; when ties split, finishing level still pays,
  so the variance premium in the weekly pot is smaller than a pure winner-take-all analysis
  would suggest. That nudges the round 5(c) trade-off marginally toward shadowing.

#### Round 6 — the front-loading threat becomes a number, and it is survivable

The best round of the exercise. It corrected the document's own headline and turned round 3(a)
from an open fear into a measured curve with an actionable calibration path.

**(a) The chalk baseline was an era artifact.** See the callout in section 2. Always-take-chalk
is 48.2%/48.3%/54.2% by era; the model's margin over it is +6.72/+7.70/**+1.69**pp. The
holdout happened to be a strong era for favorites, which made the model look barely better than
the field when in a normal era it is 6–8 points better. This also recalibrates rounds 4–5, whose
opponent model is parameterised on chalk-taking.

**(b) The transferability curve.** Model the freeze as a variance split: the observed move
`M = ε₁ + ε₂`, where `ε₁` lands before CBS posts and `ε₂` after. Adam only gets `ε₂`. Sweep
**w = share of move variance landing after Tuesday**. Critically, the *grading* line moves too —
he is scored against CBS's number, not the opener — so `cbs_line = close − ε₂`, rounded to the
half-point lines actually trade on. 40 seeds per point, shipped `make_pick`, dev only:

| w | train model | chalk | margin | validate model | chalk | margin | **retained** |
|---|---|---|---|---|---|---|---|
| **1.0** (backtest) | 54.9% | 48.2% | **+6.72pp** | 56.0% | 48.3% | **+7.70pp** | 100% |
| 0.8 | 54.7% | 48.8% | +5.85pp | 54.7% | 47.8% | +6.89pp | 87 / 89% |
| 0.6 | 54.3% | 48.9% | +5.37pp | 53.7% | 47.6% | +6.07pp | 80 / 79% |
| **0.5** | 53.9% | 48.8% | +5.06pp | 53.1% | 47.6% | +5.48pp | **75 / 71%** |
| 0.3 | 53.1% | 48.8% | +4.23pp | 52.2% | 47.5% | +4.74pp | 63 / 62% |
| 0.1 | 51.7% | 48.7% | +3.05pp | 50.7% | 47.1% | +3.65pp | 45 / 47% |
| **0.0** | 48.3% | 48.4% | **−0.13pp** | 46.7% | 46.7% | **+0.00pp** | 0% |

**The damage function is concave and era-consistent. Losing HALF the movement costs only about a
quarter of the edge.** The mechanism: big moves keep their sign through partial erosion, so only
marginal ones flip. Round 3(a) is a real threat but a **graceful** one, not a cliff — and the
floor as w→0 is *chalk*, not 50%, because a dead signal routes to the favorite default.

*Controls both pass, and they are what make the curve trustworthy: at w=1.0 it reproduces the
backtest exactly (54.9%/56.0%), and at w=0.0 the margin over chalk collapses to −0.13/+0.00pp
with all 16 games/week becoming coin flips.*

**(c) w can be calibrated in FOUR WEEKS, with no game outcomes at all.** This is the round's most
useful practical insight, and it overturns "blocked until a season of captures exists." The
reasoning: round 3(c) established the pool grades against CBS's number, so the live edge is
exactly `market_at_lock − cbs_frozen` *regardless of where CBS anchors*. Its composition is
irrelevant to its size — and size is all that sets w. So only the **magnitude** of the live gap
is needed, which requires no results and no waiting:

```
w  =  E[(market_at_lock − cbs_frozen)²]  /  E[(open − close)²]
   =  E[gap²] / 3.3296                              # dev second moment
```

**Use second moments, not mean absolute gaps.** The obvious form —
`(E|gap| / 1.235)²` — is wrong, and testing caught it: on synthetic captures with a known w it
**overestimated by ~30%** (true 0.50 recovered as 0.66). It is only valid when the live gap has
the same distributional *shape* as the historical move, and it does not: **19.7% of historical
games do not move at all**, and that spike at zero drags E|M| (1.235) down relative to SD(M)
(1.815). Since w is defined as a variance share, second moments are exact regardless of shape.
The corrected estimator recovers a known w to within 0.013 across w ∈ [0.1, 1.0]; both the bug
and the fix are guarded by `test_transferability_recovers_a_known_w_from_second_moments`.

| If the measured E[gap²] is… | implied w | margin retained |
|---|---|---|
| 3.33 | 1.00 | 100% |
| 2.66 | 0.80 | ~87% |
| 2.00 | 0.60 | ~80% |
| 1.66 | 0.50 | ~75% |
| 1.00 | 0.30 | ~63% |
| 0.33 | 0.10 | ~45% |

~4 weeks (≈64 games) gives a usable SE. **`scripts/pickem_transferability.py` computes all of
this from the capture log and prints the answer** — no analysis required, run it any time.

**(d) One capture-spec change worth making before Week 1.** Add a **third mid-week snapshot**
(Thursday or Friday) alongside `post` and `lock`. No historical data can ever recover the *time
profile* of post-freeze drift — the history has only two snapshots per game (5f) — and one extra
call a week (2 credits, ~36/season) buys the single thing the archive structurally cannot. **A
week not captured is permanently uncapturable.**

**(e) The tiebreaker thread is void.** Round 6's scout proposed solving the total-points tiebreak
as a bidding game (its item 4, projected ~$115/season). Adam confirmed the pool **splits the $50
among everyone tied on wins, with no tiebreaker at all** — see 5j round 5(f). Recorded so it is
not proposed a third time.

**(f) MEASURED 2026-08-23: the pool's frozen line IS an opener, not a Tuesday consensus.**
Round 3(a)'s threat rests on CBS freezing *after* the front-loaded correction. That is directly
checkable today, because CBS's public odds page publishes an **open** column (5h) and the pool's
Week 1 frozen lines are already transcribed. Comparing them:

| | mean \|diff\| | exact matches |
|---|---|---|
| pool frozen line vs **CBS published opener** | **0.281** | **12 / 16** |
| pool frozen line vs CBS current line | 0.438 | 12 / 16 |

**CBS publishes the opener as its pool line.** That is the optimistic branch: Adam's frozen number
sits at the same point in the timeline the backtest assumes, so w may be close to 1 and little of
the front-loaded correction is lost. Two games already showed live staleness — BUF@HOU differs by
3.0 points *with a favorite flip*, and GB@MIN has moved 3.0 points off its opener since posting.

**Do not over-read this.** It is n=16, one week, on lines the tracker still marks *provisional*
(captured ~3 weeks before kickoff, to be re-verified Tuesday Sep 8). It shows CBS's **anchoring
behaviour**, not the size of the live gap at lock — that still needs real in-season captures, which
is exactly what (c) measures. Treat it as evidence that the pessimistic branch is unlikely, not as
a value for w.

**Where round 6 leaves the project.** The scout's summary is the right one: *the analytical well
is dry and the measurement well is full.* Twenty-seven football hypotheses, zero survivors; the
tournament frame yields $10–70/season against a $150 entry; and a single unmeasured parameter
spans roughly **$400/season of decision uncertainty**. The next useful action is not another
hypothesis — it is four weeks of captures with a third snapshot.

#### Season simulation — the tournament strategy, settled in dollars (2026-08-23)

Adam asked the right question: forget win rates, simulate his **actual** pool and report profit.
`scripts/pickem_season_sim.py` — 19 opponents + Adam = **20 players**, 1,000 simulated seasons,
dev slates, real ladder, weekly pot split among ties.

**A structural check that confirms the setup:** 20 × $150 = **$3,000** = season ladder
($1,200+$450+$300+$150 = $2,100) + weekly ($50 × 18 = $900). **The pool balances exactly at 20
players.** Earlier rounds assumed 18 and were mis-specified.

**Answering the question — vs 19 coin flippers:**

| Edge assumption | finish 1st | **top 4** | mean profit | median | P(profit > 0) |
|---|---|---|---|---|---|
| Full backtested edge | 44.5% | **74.4%** | **+$570** | +$417 | 72.3% |
| Half the move lost (w=0.5) | 26.4% | **64.4%** | **+$342** | +$192 | 61.4% |
| Most of it lost (w=0.3) | 20.4% | **51.6%** | **+$238** | $0 | 49.4% |

Mean exceeds median because first place ($1,200) is a fat right tail — the typical season is
worth less than the average one.

**Coin flippers are a HARDER field than real opponents,** which is counter-intuitive and worth
knowing: a coin flipper hits 50% ATS, while someone who always takes the favorite hits ~48%
(5j round 6a). Against 19 chalk-leaning opponents the same model finishes top 4 **86.6%** of the
time for **+$827**. Adam's real pool sits somewhere between these two, closer to the chalk end.

**The tournament strategy, with the confound removed.** Flat-zone outcomes randomised so every
policy has identical expected wins, leaving only correlation with the field:

| Field | w | current | shadow | diverge |
|---|---|---|---|---|
| coin flippers | 1.0 | $460 | $428 (**−$32**) | $462 (+$2) |
| coin flippers | 0.5 | $352 | $352 (**$0**) | $355 (+$3) |
| chalk-leaning | 1.0 | $687 | $673 (**−$14**) | $698 (+$12) |
| chalk-leaning | 0.5 | $542 | $535 (**−$8**) | $556 (+$13) |

**Worth nothing.** And the mechanism of its failure is now clear: against chalk at w=1 shadowing
*does* raise top-4 (85.8% vs 83.2%) — exactly what round 4 measured — but it *lowers* first place
(52.2% vs 53.1%), and **first pays 8× fourth**. Buying top-4 security with first-place equity is a
losing trade in this ladder. Round 5's scout was right about the objective; this is the confirmation
with the correct field size and real dollars.

**Against coin flippers specifically, shadowing cannot work even in principle** — the strategy
correlates your score with the field's so coin-flip noise cancels, but coin flippers share no
lean, so there is no crowd to join.

**The decision:** keep the shipped totals-drift tiebreak. `edge/pickem_strategy.py` stays gated to
its late-season chase role and is not promoted to a default policy.

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

`edge/pickem_log.py::cbs_offset` records the decomposition and returns `None` until there is
data. **The formula below replaced a buggy one on 2026-08-23 — see 5j round 3(b)/(c) before
touching it:**

```
total_edge = cbs_offset + drift
           = (market_at_post − cbs_line) + (market_at_lock − market_at_post)
```

**Do NOT subtract `cbs_offset` from the model's edge.** The pool grades against CBS's number, so
a point of offset is worth as much as a point of drift; netting it out deletes real value. The
paragraph above originally framed it as noise to remove — that was wrong. Log both components and
test **β_offset = β_drift** after a season.

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
   Capturing the line as late as legally possible directly buys more staleness. (Also why
   live results may land slightly *below* 55.7% — see `PICKEM_STATUS.md` caveat #2.)
   *A round-2 result appeared to undercut this ranking; it was pooled across eras, sign-flips
   when split, and has been retracted — see 5j round 2(d). The ranking stands.*
   **But see 5j round 3(a), which is a much more serious problem for this project's headline
   number than a ranking question: drift appears to be front-loaded into the first ~2 days
   after the opener, which is BEFORE CBS freezes.**
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
4. **Pool-standings strategy — a real but small and conditional lever** (2026-08-22 framework;
   upgraded by 5j round 4, then substantially downgraded by round 5). Shadowing the field on the
   ~3 free coin flips per week is worth **+$14 to +$68 a season in a chalky field with a
   full-strength edge, and ≈$0 or slightly negative in a mixed field or at reduced edge.** It
   costs money on the weekly pot and makes it back on the season ladder. Unvalidated against a
   real field.
   ~~framework built~~ — see section 7.
   Unvalidated by construction and gated to Week 14+. The real upgrade available to it is
   feeding it the pool's ACTUAL pick distribution instead of CBS's national percentages.

Genuinely dead ends, do not revisit: better team-strength ratings of any kind, coaching
records, moneyline-derived signals, **separate models per spread size or moneyline range
(section 5i)**, anything else the bookmaker already sees at line-set time, and any approach
evaluated on a *filtered subset* rather than the full slate.

A third round of feature work (5i) added a *methodological* result rather than a modelling one:
any future idea that slices the slate into buckets and fits something per bucket has to clear
the selection-bias null in 5i, which sits at **+3.6pp** — not 50%.

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
# What is still blocked, and how close am I to unblocking it? (free, instant)
python3 scripts/pickem_blocked.py

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
| `edge/pickem_log.py` | Append-only snapshot log + `cbs_offset` decomposition (5f, 5j r3) |
| `edge/pickem_strategy.py` | Late-season standings play (section 7). **Unvalidated.** |
| `edge/pickem_cbs.py` | CBS scrapers: free public odds feed + pool-page parser |
| `scripts/pickem_pool_import.py` | Saved pool page → `pickem_current_week.csv`, no typing |
| `scripts/pickem_capture.py` | Weekly capture CLI, dry-run by default |
| `scripts/pickem_blocked.py` | Progress toward each blocked experiment — run it any time |
| `scripts/pickem_vs_random.py` | Holdout replay vs a coin-flipping field (seeded) |
| `scripts/pickem_feature_lab.py` | Experiment harness (all three rounds, incl. the 5i selection-bias null). Refuses to read holdout seasons. |
| `scripts/pickem_backtest.py` | Out-of-sample backtest + the staleness negative control |
| `scripts/pickem_pbp_collect.py` | nflverse play-by-play → per-game efficiency table |
| `pages/4_🎯_Pickem.py` | The weekly picks screen |

**Note on `edge/pickem_features.py`:** it is deliberately kept in the repo despite shipping
nothing. It's the evidence behind section 5, it re-runs on demand, and if someone ever
wants to test a new efficiency idea the as-of-week plumbing (the hard, easy-to-get-wrong
part) already exists and is already leak-proof.
