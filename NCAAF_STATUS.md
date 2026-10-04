# DK College Football DFS — status, methodology, and what is not validated

Start here for anything NCAAF. Built 2026-09-24. Everything below was measured
on this machine on that date unless it says otherwise, and the things that were
*not* measured are called out rather than left to be discovered.

---

## 1. What the contest actually is

Read off `api.draftkings.com/lineups/v1/gametypes/94/rules` — DraftKings' own
published roster rules — not transcribed from an article.

| | |
|---|---|
| Roster | **QB, RB, RB, WR, WR, WR, FLEX (RB/WR), S-FLEX (QB/RB/WR)** — 8 players |
| Cap | $50,000 |
| Minimum games | 2 |
| Late swap | allowed |
| DK lobby sport code | **`CFB`** — *not* `NCAAF`, which returns every sport in the lobby and looks like it worked |
| Classic game type id | **94** (95 is Showdown, 377 is a Snake draft with no cap) |
| Main slate | the Classic group whose `ContestStartTimeSuffix` is **empty** |

**No defence slot and no tight-end slot.** DraftKings lists college tight ends
as WR. So the entire DST half of the NFL build — points-allowed tiers, the
opponent's implied team total, the "never roster a defence against your own
stack" rule — has no counterpart here, and a missing game line costs nothing.

## 2. Scoring — validated against DraftKings, not cross-referenced

`edge/nfl.py` is candid that its scoring constants were "cross-referenced across
multiple independent sources" but never confirmed against DraftKings directly,
because DK's rules pages blocked every automated fetch. They still do: the rules
page is a single-page app that serves 576KB of shell and no scoring text, and no
`api.draftkings.com` scoring endpoint answers.

So this was confirmed a different way. DK's draftables payload publishes each
player's season **fantasy points per game** (`draftStatAttributes` id **174**
for CFB). Recomputing that quantity from cfbfastR play data under competing
scoring tables and keeping the one that reproduces DK's own number is a direct
test against DraftKings — just not against its prose. Over the **393 players on
the 2026-09-26 main slate** who matched:

| hypothesis | MAE | bias | verdict |
|---|---|---|---|
| passing TD = 4 | 0.900 | +0.42 | **kept** (QB only) |
| passing TD = 6 | 2.356 | +2.18 | rejected |
| 0.04 per passing yard | 1.144 | +0.90 | **kept** |
| 0.05 per passing yard | 1.270 | +1.07 | rejected |
| 0.1 per rushing yard | 1.144 | +0.90 | **kept** |
| 0.05 per rushing yard | 1.500 | +0.20 | rejected |
| threshold bonuses ON | 1.144 | +0.90 | **kept** |
| threshold bonuses OFF | 1.231 | +0.70 | rejected |
| full PPR | 0.678 | −0.34 | **kept** (WR only) |
| half PPR | 1.425 | −1.20 | rejected |
| no PPR | 2.255 | −2.07 | rejected |

**The PPR row came out backwards the first time, and the reason is a trap.**
Averaged over the games a player *recorded a stat in*, full PPR looks biased
high for receivers (+1.24) and half PPR looks unbiased. That is an artifact of
the denominator: a receiver who played and caught nothing has no row in a
play-derived box score, so he is dropped from the average instead of counted as
the zero DK counts him as — and that hits receivers hardest, backs less,
quarterbacks least, which is exactly the shape of the spurious bias. Dividing by
the player's **team's** games reverses the conclusion at every position. Anyone
re-running this must use team games, not observed rows.

**Conclusion: DK College Football is DK NFL scoring minus everything defensive.**

## 3. The price problem, and why it turned out to be an advantage

**DraftKings posts NCAAF player props ONLY as one-sided milestone ladders.**

```
marketType "Receiving Yards Milestones"
selections  "15+" 1.03   "25+" 1.30   "40+" 1.95   "50+" 2.55 ...
```

There is no Under on any of them. `edge/dfs_project.project` needs an
Over/Under pair to devig, finds none, and returns `proj=None` for every player
on the board. That is not a degraded projection — it is no projection, and it is
why `edge/dfs_ladder.py` exists.

A two-sided line is **one point** on a distribution; turning it into a mean
requires assuming the whole shape around it (that is what the sigma is, and
`edge/dfs_sport.py` is candid that the NFL's fitted sigmas turned out nearly
inert). **A ladder is the distribution.** So the quantity the NFL path has to
assume, this path largely measures:

```
E[X] = ∫ P(X ≥ t) dt          (yardage)
E[N] = Σ_{k≥1} P(N ≥ k)       (counts)
```

Both identities are exact, and the rungs supply most of the terms directly.
College football's uglier market data yields a **better-founded** projection.

### Coverage, measured on a live board

| | events | ladders | per covered game |
|---|---|---|---|
| receiving yards | 32 | 189 | ~6 WR |
| rushing yards | 31 | 107 | ~3.5 RB |
| passing yards | 33 | 62 | ~2 QB |
| receptions | 18 | 102 | thinner, and 1 pt each in PPR |
| passing TDs | 33 | 64 | |

On the 12-game main slate, **all 12 games** were covered and **106 of 857**
slate players carried a ladder. That is enough to fill QB/RB/RB/WR/WR/WR/FLEX/
S-FLEX comfortably — **and it is also the build's biggest structural
limitation**: DraftKings prices the players books take action on, so a cheap
backup getting a full workload is a real play this model cannot see at all. The
app says so on the page rather than in this file.

Collection is cheap: DraftKings serves these at **league level**, so five
requests cover every college game on the board rather than five per game.

### What the estimator had to get right

Graded end-to-end against real cfbfastR distributions with the market removed
entirely (`scripts/ncaaf_ladder_fit.py`):

| | lognormal | Weibull | shipped |
|---|---|---|---|
| passing yards | +5.5% | +0.8% | **+3.2%** |
| rushing yards | +15.8% | +8.9% | **+1.5%** |
| receiving yards | +20.0% | +12.4% | **+3.0%** |
| receptions | — | — | **−4.1%** |
| passing TDs | — | — | **0.0%** |

Fitting a parametric family to the rungs and integrating it over the unpriced
head **does not work** for rushing and receiving: those distributions have a
mass at and near zero (a receiver targeted once for four yards, or not at all)
that no smooth right-skewed density reproduces. Swapping families moved the bias
without removing it.

So the shipped version drops the family and **measures the two unpriced pieces
directly**, each as one bounded number per market:

* `HEAD_FILL` — where the head sits inside the bracket monotonicity already
  guarantees. `c=0` is the rectangle `t_min·S(t_min)` (strict lower bound),
  `c=1` is the full box (strict upper bound). Fitted 0.53 / 0.40 / 0.38 for
  passing / rushing / receiving.
* `TAIL_EXCESS` — mean excess above the top rung, **in units of the rung gap**.
  Against `t_max` the ratio is 0.017–0.067 with a wide spread; against the gap
  it is 0.40–0.50 with a tight one.

For one player's own sample both quantities have exact identities
(`mean(min(x, t_min))` and `mean(max(0, x − t_max))`), so every player-season
with 8+ games gives one exact observation and the constant is the median over
thousands.

### Two bugs worth remembering, both of which produced plausible numbers

1. **The count tail ran away.** The tail decayed geometrically at the rate the
   last two rungs implied, clamped at 0.9. A book posts two rungs at the same
   price (and the isotonic step produces one whenever it pools an inversion),
   so the ratio came out 1.0, clamped to 0.9, and compounded:
   `0.083 × 0.9 / (1−0.9) = 0.75` of a touchdown out of a flat pair. Graded
   +33.7% on passing TDs. The measured mean excess is **0.000**.
2. **A count market is not interpolable.** FanDuel's "Over 1.5" passing TDs IS
   DraftKings' "2+" rung — the same event for an integer stat. Interpolating
   linearly between "1+" and "2+" instead reported a **50%** overround on
   passing TDs and 23% on receptions, against 7–10% on the yardage markets.
   Numbers that are not a plausible hold were the tell.

Both are locked in by regression tests in `tests/test_dfs_ladder.py`.

## 4. The two game theories, and four places college contradicts the NFL

Same shape as the NFL: a lineup is a sum of eight correlated random variables,
`cash = mean − 0.75·sd`, `gpp = mean + 1.25·sd`. Every constant is measured on
cfbfastR 2024+2025 (66,499 player-games → 4,380 player-seasons).

**1. The S-FLEX is a second quarterback, and it is not close.**

| position | mean DK | median | p90 | p99 |
|---|---|---|---|---|
| QB | **17.22** | 15.6 | 33.4 | 51.9 |
| RB | 10.45 | 7.8 | 23.5 | 39.9 |
| WR | 8.89 | 6.8 | 18.9 | 35.1 |

31 of the 40 highest-scoring player-seasons in the sample are quarterbacks.

**2. But never two from the same team.** `R_QB_OWN_QB = −0.098` — they compete
for snaps. Enforced as a hard refusal in the optimiser, the structural analogue
of the NFL's "a DST must not face your own stack".

**3. Team-mate receivers help each other in college and hurt each other in the
NFL.** `WR↔own WR` is **+0.048** here and **−0.029** in the NFL. `dfs_opt_nfl`
reasons correctly from its negative number that a second pass-catcher is worth
less than the first, and stacks 2. The sign flips here, so **`STACK_N = 3`**.
This is the single most consequential difference for roster construction and the
first thing to re-check if GPP lineups ever look wrong.

**4. Do not pay up at quarterback for the floor.** The NFL logic inverts. At a
20-point projection:

| | sd | CV |
|---|---|---|
| QB | 11.60 | **0.580** ← most volatile |
| RB | 10.60 | 0.530 |
| WR | 10.14 | **0.507** ← least |

A college QB's intercept is 8.48 points — he runs, throws picks, and gets pulled
in blowouts. Cash still wants quarterbacks for the **mean**; it should not pay up
for the most expensive one expecting safety.

Full correlation matrix with sample sizes: `edge/dfs_ncaaf_theory.py`.

## 5. What is NOT validated — read before trusting a number

| thing | status |
|---|---|
| **Ownership** | fitted on **one** slate (2026-09-26 Night, a 594-entry GPP); see §8. Sharpness 1.1 confirmed; the S-FLEX split is now **measured** (QB 1.9 / RB 2.3 / WR 3.8 — a QB in the S-FLEX ~90% of the time, not the guessed 60%), and 12% of the field's slots go to players with no DK ladder (`MODELED_SHARE` 0.88). Fitted to GPP only: the cash field is far sharper and cash lineups do not read ownership. |
| **Ladder overrounds** | fitted against **FanDuel's** two-sided lines — a cross-book anchor, an assumption about a second book, not ground truth. n is 22–81 per market. The first graded slate (§8) had projections **3.9 points LOW** on 63 players (RB −5.5, QB −3.4, WR −3.3) — the direction too-high overrounds would produce, and the check this row was waiting for. Seven games that move together is not enough to refit on. |
| **`SD_FIT` spreads** | regressed against a leave-one-out **season mean**, not a real projection, because no projection log existed. A season mean knows nothing about the opponent, so these are if anything biased **high**. |
| **Lineup head-to-head** | the NFL build has `scripts/nfl_lineup_backtest.py` testing cash-vs-GPP objectives against real outcomes. **College has no equivalent yet** — the objectives are argued from measured correlations and spreads, not from a backtested edge. |
| **Fumbles / 2-pt conversions** | not in the cfbfastR feed; left at zero. Worth about −0.1 and +0.05 points per player-game. |

All five are checkable from contest exports. `data/dfs_proj_log_ncaaf.csv` is
written on **every** build and starts that clock — it does nothing for a week
already gone, which is why it shipped with the first build.

## 6. Weekly checklist

```bash
# Friday night / Saturday morning — DK keeps ADDING ladders through Friday,
# so an old scan is a THIN pool, not a wrong one.
python3 scripts/odds_collect.py --profile dfs_ncaaf --push

# lineups
python3 scripts/dfs_lineups_ncaaf.py                  # cash + gpp, main slate
python3 scripts/dfs_lineups_ncaaf.py --board --top 40 # the projected pool
python3 scripts/dfs_lineups_ncaaf.py --mode gpp -n 3  # a diversified portfolio

# after the slate: export the contest standings from DK, drop in data/, then
python3 scripts/ncaaf_calibration.py --fit-ownership
```

Re-fitting, when there is more data:

```bash
python3 scripts/ncaaf_overround_fit.py            # accumulates pairs across runs
python3 scripts/ncaaf_ladder_fit.py               # head/tail constants + grade
python3 scripts/ncaaf_fit.py                      # TD rates, spreads, correlations
```

## 7. Layout

| path | role |
|---|---|
| `edge/ncaaf.py` | DK CFB scoring (validated) + cfbfastR ground truth |
| `edge/dfs_ladder.py` | **the milestone-ladder engine** — the core of this build |
| `edge/dfs_ncaaf_theory.py` | spreads, correlations, the two objectives, ownership |
| `edge/dfs_opt_ncaaf.py` | the optimiser (S-FLEX, the two-QB rule) |
| `edge/dfs_run_ncaaf.py` | slate → pool → lineups, one entry point |
| `pages/3_🏈_NCAAF_DFS.py` | the phone app |
| `scripts/dfs_lineups_ncaaf.py` | the CLI |
| `scripts/ncaaf_fit.py` | TD rates, spreads, correlations |
| `scripts/ncaaf_ladder_fit.py` | head/tail constants, and grades the estimator |
| `scripts/ncaaf_overround_fit.py` | ladder overround vs FanDuel |
| `scripts/ncaaf_calibration.py` | predicted vs actual, from a contest export |
| `tests/test_dfs_ladder.py` | the ladder engine, incl. both regressions above |
| `tests/test_dfs_ncaaf.py` | scoring, roster, theory, optimiser rules |

## 8. First graded slate — 2026-09-26 Night (7 games, draft group 153951)

Both model lineups were entered. **Cash 171.9, 21st of 23** (the line was 185).
**GPP 119.0, 548th of 594.**

**The cause was a bug, not the model.** DK's salary list keeps name suffixes
("Michael Hawkins **Jr.**") and a college prop subject drops them
("Michael Hawkins (WVU)"), so every suffixed player fell out of the pool:
**10 of the 75 players with ladders**, including the two players the cash
field built around — Michael Hawkins Jr. (QB, **91%** in cash, scored 35.6) and
Anthony Evans III (74%) — and JC French IV (74%). DK had posted their ladders
all day. Fixed in `edge/dfs_run_ncaaf.py::salary_index`. NFL is unaffected:
its prop subjects keep the suffix.

Replayed from the 6:31 PM odds snapshot, the fixed build's cash lineup puts
Hawkins Jr. at S-FLEX and scores **186.0 against the 185 line** (the buggy
build's replay: 165.0). One replay, cleared by a point: direction, not proof.

| check | result |
|---|---|
| projection (63 players) | MAE 7.16, r 0.51, **bias −3.88** (see §5, overrounds) |
| ladder `band` as a risk measure | high-band half MAE 7.38 vs low-band 6.94 — worse, as it should be |
| ownership, shipped prior on the fixed pool | 5.00 pts/player; with the measured slots + modelled share **4.58** |
| field ownership on players with no DK ladder | 12% GPP, 8% cash (after the fix; 25% / 36% before it) |
| cash field sharpness | gamma 3+ with a 90% cap — recorded, not shipped (cash does not read ownership) |

## 9. Second graded slate — 2026-10-03 Main (4 games, draft group 154161)

Both model lineups were entered. **GPP 183.5, 304th of ~1,900.** **Cash 147.5, 103rd of ~200** (cashed ~50%).

`scripts/ncaaf_calibration.py --fit-ownership`:

| | n | bias | MAE | rank corr | below floor (25) | above ceiling (5) |
|---|---|---|---|---|---|---|
| skaters | 130 | −1.55 | 8.19 | +0.546 | 26% | 11% |
| ownership | — | −0.54 | 3.52 | +0.749 | — | — |

Shipped: OWNERSHIP_GAMMA 1.1 → MAE 3.47. **Best fit: gamma 1.50 → MAE 3.29** (−0.18 improvement).

**Buzz test:** Tested on 3-slate aggregate (γ=0.6, β=1.0). Titans DST example: predicted 22% without buzz → 45% with buzz (YouTube mentions). Pattern is real but single outlier—hold collection running, defer integration to 4–6 slates.
