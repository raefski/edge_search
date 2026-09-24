# DK NASCAR DFS — status, methodology, and what is not validated

Start here for anything NASCAR. Built 2026-09-24. Everything below was measured
on this machine on that date unless it says otherwise, and the things that were
*not* measured are called out rather than left to be discovered.

---

## 1. What the contest actually is

Read off `api.draftkings.com/lineups/v1/gametypes/173/rules`.

| | |
|---|---|
| Roster | **D, D, D, D, D, D** — six drivers, no positions |
| Cap | $50,000 |
| **Late swap** | **NOT ALLOWED.** What you enter at the green flag is what runs. |
| DK lobby sport code | **`NAS`** — *not* `NASCAR`, which returns every sport in the lobby |
| Classic game type id | **173** (376 is a Snake draft with no cap) |
| Series | the draft-group suffix carries it: `(Cup)`, `(Trucks)`, `(Xfinity)` |

## 2. Scoring — pinned down, and the published version is wrong

```
finish points  +  1.0 × (start − finish)  +  0.25 × laps led  +  0.45 × fastest laps
```

The finishing table is **not** a flat −1 per place. Every strategy article says
"45 for the win, 42 for second, then one less per place"; that is right for the
top ten and wrong after it. There is an **extra −1 at each decade boundary**:

| | |
|---|---|
| P = 1 | 45 |
| P = 2–10 | 44 − P → 42, 41, 40, 39, 38, 37, 36, 35, 34 |
| P = 11–20 | 43 − P → 32 … 23 |
| P = 21–30 | 42 − P → 21 … 12 |
| P = 31–40 | 41 − P → 10 … 1 |

Flat overprices a 37th-place finish by 3 points and a 40th by 3 — on exactly
the deep finishers a place-differential play is built around, which is most of
a NASCAR lineup.

**Validation 1.** Reproduces all sixteen per-position values in a third-party
per-race points table, including the 30th → 31st step (12 → 10) that a flat
rule cannot produce.

**Validation 2, against DraftKings itself.** Recomputing each driver's 2026
season fantasy-points-per-race from raw NASCAR results and comparing to DK's
own published FPPG (`draftStatAttributes` id **653**) across the 36 drivers on
its board:

| table | MAE | bias |
|---|---|---|
| flat −1 per place | 1.233 | +0.948 |
| **decade-step (shipped)** | **0.996** | **−0.423** |

…and that is with only 29 of DK's ~32 scored races available.

**The other three constants** are anchored by a worked example rather than
assumed: Joey Logano, North Wilkesboro 2026 — started 11, won, led 323 laps,
100 fastest laps, published total **180.75** = 45 + 10 + 80.75 + 45.

## 3. Why this sport gets a simulator

**No sportsbook prices three of the four scoring components.** Place
differential, laps led and fastest laps have no market at any book, at any
price. A book prices who wins, who finishes top-5, and driver matchups — none
of which is what a DFS lineup is paid for. So unlike every other build in this
repo, NASCAR is modelled from **results**, and costs nothing, and works from
any IP.

**And its components are constrained sums over the whole field:**

* finishing position is a **permutation** — exactly one driver finishes 1st
* place differential **sums to zero** across the field
* laps led sums to exactly `actual_laps`
* fastest laps sum to exactly `green_laps` (*not* actual laps — a fastest lap
  is only awarded under green, verified by summing the loop data)

Six drivers cannot all dominate. A mean-plus-correlation-matrix model can only
approximate that, and only by fitting a matrix whose every entry is a function
of the same constraint. Sampling whole races enforces all four exactly and
produces the correlation structure for free.

It also gives the optimiser something better than two numbers: the full joint
distribution of a *lineup's* total, which is left-skewed by wrecks and
right-skewed by dominators.

### Simulator validation

Fitted on 2022–2026 Cup (6,088 scored driver-races with leak-free prior-12-race
form), simulated against held-out 2026 races:

| | sim | real |
|---|---|---|
| mean DK points | 28.05 | 27.82 |
| sd | 26.62 | 25.98 |
| p90 | 63.21 | 58.52 |

The bar is a mean within a point or two and an sd within 10%. A simulated sd
that is too **narrow** is the dangerous direction — it makes every GPP lineup
look safer than it is — and this errs wide.

## 4. The four track types are four different games

Every number below is measured, and each one changes what a correct lineup
looks like.

| | superspeedway | intermediate | short | road |
|---|---|---|---|---|
| finish model R² | **0.029** | 0.181 | **0.287** | 0.182 |
| corr(start, finish) | **0.076** | 0.370 | 0.458 | 0.402 |
| leader's lap share | **26.6%** | 42.3% | 47.5% | 48.2% |
| corr(laps led, fastest laps) | **0.01** | 0.77 | 0.77 | 0.76 |
| DNF, starting P1–10 | **28.2%** | 12.3% | 6.6% | 7.8% |
| DNF, starting P31+ | 23.3% | 18.2% | 15.3% | 13.2% |

Three things fall out that the strategy literature asserts qualitatively and
this quantifies:

1. **A superspeedway is very nearly a lottery.** The finish model explains 3%
   of variance. Place differential from the back of the grid is close to free
   there and nowhere else.
2. **There is no such thing as a superspeedway dominator.** The leader takes
   only 26.6% of the laps *and* fastest laps are uncorrelated with leading
   (r = 0.01) — the fastest lap goes to whoever is in clean air when it is set.
3. **At a superspeedway the front of the grid is the most dangerous place to
   be** — 28.2% DNF from the first ten against 23.3% from the back. Nowhere
   else does the table run that way. The wreck happens in the pack and the
   pack is at the front.

**The superspeedway model is deliberately reduced.** Fitted on all four
predictors its `form_led` coefficient reads +11.35; refitted without 2026 it
reads +3.41. A term that moves 3× between overlapping samples is noise, and it
was only ever explaining noise — `form_finish` alone gets R² 0.0269 of the
total 0.0287. The other three terms are zeroed rather than shipped at an
unstable point estimate.

## 5. The two theories are percentiles, not mean ± k·sd

```
cash = the 25th percentile of the lineup's own simulated total
gpp  = the 90th percentile, minus an ownership tilt
```

A NASCAR driver-race, measured over 6,088 starts: mean 28.5, sd 25.5, p10
−2.6, p50 27.6, p90 59.5, p99 96.0, **minimum −36.1**. Neither tail is
reachable from a mean and a standard deviation. The left tail is a wreck — an
expensive car that starts near the front and is collected on lap 12 loses the
finishing points *and* the entire place differential, which is why the minimum
is strongly negative rather than zero.

`GPP_PCT` is 90 rather than 99 because a field is 36–40 cars and a lineup is
six: the extreme upper tail is dominated by which single driver happened to
win, and maximising it just picks the six highest-variance cars.

**Optimiser.** A randomised fill plus a vectorised hill climb. Measured on the
live 2026-09-27 Kansas board at 1,500 simulated races:

| | time | cash floor | gpp ceil |
|---|---|---|---|
| `optimize(iters=250)` | **16s** | 159.6 | 283.6 |
| `optimize_exhaustive(top_n=24)` | 70s | 159.6 | 283.6 |

Identical lineups, four times faster. Scoring candidates one at a time instead
of vectorising the swap took **85s**, which is too slow to sit through before
a green flag that does not allow late swap.

## 6. The two states of a slate

DraftKings prices a NASCAR field days before qualifying. Until the grid is set,
NASCAR's feed reports every entrant with `starting_position = 0` — and **place
differential does not exist yet**, because it is start minus finish.

* **Before qualifying:** starting positions are estimated from practice rank
  if a session has run, recent average finish otherwise, then *ranked and
  renumbered 1..N* so the provisional grid is a real permutation. The app
  flags this as **PROVISIONAL** in the first thing you see.
* **After qualifying:** the real grid.

Because there is **no late swap**, the post-qualifying rebuild is the single
most important action in the weekly loop.

## 7. What is NOT validated

| thing | status |
|---|---|
| **Ownership** | a **PRIOR**, never fitted against any NASCAR contest. The first export is the most valuable file this build will receive — the whole field picks six of ~37, so one file pins the curve across the entire pool. |
| **Drafting partners** | at a superspeedway, teammates' and manufacturer allies' finishes are genuinely correlated beyond what the permutation implies. **Not modelled.** A known gap, not an oversight. |
| **Lineup head-to-head** | no backtest of cash-vs-GPP objectives against real contest outcomes yet. The objectives are argued from the measured distribution, not from a demonstrated edge. |
| **Pit strategy, fuel windows, caution timing** | absorbed into the residual spread rather than simulated. |
| **Stage points** | correctly absent — DK NASCAR Classic does not score them. |

`data/dfs_proj_log_nascar.csv` is written on every build and records whether
the grid was provisional, so the calibration script can split on it.

## 8. Weekly checklist

```bash
# any time — no odds, no snapshot, no staleness
python3 scripts/dfs_lineups_nascar.py --board

# AFTER QUALIFYING (Saturday for a Sunday race). There is no late swap.
python3 scripts/dfs_lineups_nascar.py
python3 scripts/dfs_lineups_nascar.py --mode gpp -n 3

# after the race: export the contest standings from DK into data/, then
python3 scripts/nascar_calibration.py --fit-ownership
```

Re-fitting:

```bash
python3 scripts/nascar_fit.py --validate      # all constants + a held-out check
```

## 9. Data sources (all free, public, no key)

| feed | carries |
|---|---|
| `cf.nascar.com/cacher/{yr}/{series}/race_list_basic.json` | schedule, track, caution laps, restrictor-plate flag |
| `cf.nascar.com/cacher/{yr}/{series}/{race}/weekend-feed.json` | start, finish, laps led, status — **and the entry list before the race, and practice + qualifying sessions** |
| `cf.nascar.com/loopstats/prod/{yr}/{series}/{race}.json` | **fastest laps**, lead laps, average running position, passes, rating |

Loop data goes back to **2020**; the results feed to 2018.

**The join is exact.** DraftKings' NASCAR `playerId` *is* NASCAR's own
`driver_id` — Larson 4030, Hamlin 1361, Reddick 4065 in both. No name
normalisation, no alias table, nothing to rot. No other sport in this repo can
say that.

## 10. Layout

| path | role |
|---|---|
| `edge/nascar.py` | scoring (validated), track types, the feeds |
| `edge/nascar_sim.py` | **the race simulator** — the core of this build |
| `edge/dfs_nascar_theory.py` | the two percentile objectives, the field model |
| `edge/dfs_opt_nascar.py` | the optimiser (vectorised hill climb + exhaustive) |
| `edge/dfs_run_nascar.py` | slate → board → lineups, one entry point |
| `pages/6_🏁_NASCAR_DFS.py` | the phone app |
| `scripts/dfs_lineups_nascar.py` | the CLI |
| `scripts/nascar_fit.py` | every constant, plus a held-out simulator check |
| `scripts/nascar_calibration.py` | predicted vs actual, from a contest export |
| `tests/test_dfs_nascar.py` | scoring, track types, simulator invariants, optimiser |
