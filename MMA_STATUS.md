# DK MMA DFS — status, methodology, and what is not validated

Start here for anything MMA. Built 2026-09-26. Everything below was measured on
this machine on that date unless it says otherwise, and the things that were
*not* measured are called out rather than left to be discovered.

---

## 1. What the contest actually is

Read off `api.draftkings.com/lineups/v1/gametypes/168/rules`.

| | |
|---|---|
| Roster | **F, F, F, F, F, F** — six fighters, no positions |
| Cap | $50,000 |
| **Late swap** | **NOT ALLOWED.** Lock is the **first fight's** opening bell — the main event is 3–4 hours later |
| DK lobby sport code | **`MMA`** (`UFC` returns every sport in the lobby) |
| Classic game type id | **168**. 169 is Captain Mode (1.5× CPT, a separate late-card slate — not built), 373 is Snake (no cap) |
| FPPF stat id | **635** (`draftStatAttributes`) — a different id from every other sport |
| Other board fields | `playerGameAttributes` 111 = opponent, **115 = fight number (1 = main event)**, **150 = weigh-in weight** |

A scratched fighter stays on the board with **no competition and
`isDisabled=true`** (Mickey Gall, 2026-09-26) — dropped and listed on the page.

## 2. Scoring — confirmed against DraftKings' own FPPF

```
0.2 × every landed strike  +  0.2 × every landed SIGNIFICANT strike (so 0.4 each)
+ 0.03 × control seconds  +  5 × takedown  +  5 × reversal  +  10 × knockdown
+ win bonus: R1 90 · R2 70 · R3 45 · R4 40 · R5 40 · decision 30
+ 25 more for a win inside the first 60 seconds of round 1
```

Three strategy sites print this table (RotoWire and FantasyLabs date it to the
January 2021 change). It was not taken on trust. `scripts/mma_fit.py --what
scoring` recomputes every fighter's career average from UFCStats under
competing readings and compares it with the FPPF DraftKings prints on its board
(18 fighters on the 2026-09-26 card with both):

| reading | MAE | bias | exact to 0.1 |
|---|---|---|---|
| **shipped, no-contests included** | **1.054** | −0.708 | **15 / 18** |
| shipped, no-contests excluded | 1.962 | +0.203 | 13 / 18 |
| significant strike counted once (0.2) | 8.574 | −8.574 | 0 / 18 |
| no quick-win bonus | 1.357 | −1.013 | 13 / 18 |
| no reversals | 1.518 | −1.178 | 8 / 18 |
| no control time | 6.227 | −6.227 | 1 / 18 |

**Fifteen of eighteen reproduce DraftKings' number to the decimal**, and every
rule is confirmed individually: remove any one and exact matches are lost. The
three that miss (Nakamura 87.5 vs 74.5, Simon 75.9 vs 73.2, Hiestand 87.1 vs
90.1) look like DK's stat provider disagreeing on one fight each, not a rule.
DK's FPPF is the plain **career UFC average, no-contests included**.

**A data bug this caught.** UFCStats renamed two events after the fact
("UFC Fight Night: Grasso vs. Shevchenko 2" → "Noche UFC: …") and the public
mirror carries both copies. Counted twice, Raul Rosas Jr.'s 132.9-point KO moved
his average 4.4 points off DK's. `edge/mma.py::fights` now deduplicates on the
fight URL — every fighter history in the build ran through that bug until the
FPPF check exposed it.

## 3. Where the numbers come from

| input | source | cost |
|---|---|---|
| win / method / round | **DraftKings Sportsbook** UFC league 9034: moneyline, Method of Victory (six prices), Round-and-Method grid, distance, alt total rounds, sig-strike and takedown O/U for every fighter | free, 8 requests, **desktop only** (Akamai) |
| fighter stats history | **UFCStats** via the `Greco1899/scrape_ufc_stats` GitHub mirror (UFCStats itself sits behind a JS proof-of-work page). Per-ROUND stats for 8,638 fights since 1994, refreshed daily | free, any IP |
| historical prices (fitting only) | **`ufc-master.csv`** (`shortlikeafox/ultimate_ufc_dataset`, the Kaggle "Ultimate UFC" set): moneylines 2010–2026-03 and per-fighter KO/SUB/DEC prices for ~5,400 fights — the only public method odds at scale, and the reason this build could be **backtested end to end**, which NCAAF and NASCAR could not | free |
| salaries, FPPF, fight order, weigh-in | DK draftables | desktop, snapshot for Cloud |

Streamlit Cloud cannot reach the sportsbook, so the desktop pushes
`data/odds_snapshot_dfs_mma.json` (`scripts/mma_odds_capture.py --push`, on
`deploy/mma-odds-publish.timer`). Every capture is also archived to
`data/mma_odds_history/` (gitignored — scraped prices, public repo) so the
market calibration can later be re-fitted on DK's own pre-lock lines.

## 4. The market, de-biased — two findings, both out of sample

Measured on 6,821 priced fights joined to UFCStats (`--what market`):

**1. The method market underprices DECISIONS, in every era.** Power-devigged,
the six method prices imply 45.7% of fights go the distance; 49.5% do.

| era | market P(decision) | actual |
|---|---|---|
| 2013–16 | 0.443 | 0.484 |
| 2017–19 | 0.458 | 0.499 |
| 2020–22 | 0.455 | 0.487 |
| 2023–26 | 0.477 | 0.510 |

Same in three- and five-round fights. The public bets knockouts. **For DFS this
is the expensive direction**: a finish pays 45–90 and a decision 30, so reading
the market at face value inflates every fighter's win bonus. Correction
`logit p = a + b·logit p_mkt`, fitted on pre-2021 fights, tested on 2021–26:
mean 0.469 → **0.508 vs actual 0.501**, log loss 0.67580 → 0.67143. Shipped
fitted on all data (a = +0.128, b = 0.780).

**2. Favourites win more than the moneyline says.** 80–90% bucket: 83.7%
implied, 88.2% actual; 10–20%: 16.3% vs 12.2%. Power devig beats multiplicative
(log loss 0.6055 vs 0.6066) and a logit slope removes a little more — 1.024
fitted on either pre-2019 or pre-2021 data; 1.058 on all (shipped).

**The shipped table**: six method prices power-devigged into winner × method,
then iterative proportional fitting re-targets the decision column to the
calibrated distance probability and the rows to the calibrated moneyline.
Six-way log loss on 2,181 held-out fights: raw 1.5467 → **1.5325**.

**No market (a replacement opponent is the usual case)**: the win probability
comes from **DK's own salaries** — `logit p = 0.576 × Δsalary/$1k`. On the
eleven priced bouts of 2026-09-26 that reproduced the market to a mean absolute
**0.023** in probability: DK prices MMA off the line. ONE card — refit as the
forward log grows. The method split then comes from the division's history
(strawweight women go the distance 65.6% of the time, heavyweights 37.2%).

## 5. The simulator (`edge/mma_sim.py`)

A fight is one draw: exactly one winner, one method, one moment — and the length
of the fight decides how long **both** corners accumulate stats. So whole fights
are sampled.

**When** — competing-risk hazards on 30-second bins; the market fixes each finish
cell's total probability, a fitted shape fixes when it lands.

| | round 1 | 2 | 3 | 4–5 | first-minute factor |
|---|---|---|---|---|---|
| KO | 1.0 | 0.794 | 0.483 | 0.475 | 0.698 |
| SUB | 1.0 | 0.878 | 0.572 | 0.421 | 0.266 |

Validated pre-2022 → 2022–26 on 1,548 three-round fights: finish share by round
predicted [0.247, 0.156, 0.086], actual [0.256, 0.165, 0.076]. **The first
minute drifted** — 4.6% of fights ended inside 60s in 2012–16, 4.0% in
2017–21, 3.2% in 2022–26 — so the shipped shape is fitted on 2019–26 only
(predicts 3.39% vs 3.54% actual). That minute is the +25 quick-win bonus. A KO is
2.5× as likely as a submission to land in it.

**How much** — a corner's stat points =
`(burst + slope × minutes) × off^0.8 × def_opp^0.8 × dominance × gamma noise`

* burst/slope by the corner's **own outcome**: a KO winner banks ~11 at the
  finish (the knockdown alone is 10) and 3.2/min before it; a loser ~2.3/min
  whatever the method. Fitted on **2019–26** — stat volume ran ~7% lower every
  year through 2019.
* **style indices** off/def: actual stat points over what an average fighter
  would have scored **with the same outcomes and durations**, shrunk toward 1.
  Relative to outcome on purpose — raw per-minute rates would count a fighter's
  wins twice. Held out 2022–26, outcome known: MAE 10.51 → **9.82**, corr 0.777
  → 0.809. UFC debuts measured at 0.986 of average (n = 1,245), so debuts get 1.0.
* **dominance**: given the result, a favourite still out-produces and an
  underdog under-produces — sharpest in DEFEAT (a heavy favourite who loses:
  1.23× his expected stats; a heavy underdog who loses: 0.86×). Fitted 2016–21.
* noise: gamma, shape 4.49. The two corners' residuals correlate **−0.12**
  (one out-landing is the other being out-landed); drawn independently, which is
  conservative for a lineup holding both.

A DK MMA score is, on average, 26.9 points of win bonus and 32.0 of stats — of
which significant strikes are 16.9, takedowns 5.2, control 3.8, other strikes
3.4, knockdowns 2.2, reversals 0.6.

## 6. The backtest — the whole chain on held-out fights

`scripts/mma_fit.py --what backtest --strict`: 1,727 priced fights since
2022-01-01 (3,454 fighter-fights). The stats baseline and the timing shape are
refitted on 2019–21 only; style indices are walk-forward. Projection = the exact
expected DK score from the de-biased market and the simulator.

| projection | MAE | RMSE | bias | corr |
|---|---|---|---|---|
| career average — **what DK's FPPF shows** | 36.52 | 43.59 | +7.13 | 0.185 |
| market + an average fighter's stats | 30.57 | 36.55 | −1.31 | 0.464 |
| **market + style (shipped)** | **30.04** | **36.18** | −0.89 | **0.479** |

By projected decile it is calibrated at both ends (28.0 → 25.6 at the bottom,
91.9 → 92.5 at the top). **The simulated distribution is calibrated too**, which
is what the objectives actually read: of 864 held-out fighter-fights, 11.6% of
real scores fell below the fighter's simulated 10th percentile, 22.9% below the
25th, 47.6% below the median, 74.3% below the 75th, 91.0% below the 90th.

## 7. The objectives — scored against a simulated FIELD

NASCAR ranks lineups on percentiles of their *own* total. MMA cannot: when the
chalk favourite is knocked out, your score drops **and so does the cash line**,
because half the field had him. So, on the same simulated cards:

```
cash = P(lineup beats the double-up line)    line = the field's 56th percentile
gpp  = P(lineup finishes in the top 1% of the field)
```

GPP leverage then comes out of the simulation, not a hand-set penalty. Both are
scored over **every legal lineup** (101,701 on the 12-fight 2026-09-26 card),
screened on 3,000 simulated cards and re-scored on 20,000 for the best 400 — no
heuristic search.

**Both corners of one fight are allowed.** The field sampler never pairs them,
but the optimiser may: on 2026-09-26 the cash lineup held both sides of the
five-round main event (a guaranteed winner, and 25 minutes of stats for each).
The page flags it.

## 8. The field model — A PRIOR

Ownership is **how often a fighter appears in the lineups the field builds**,
with every legal pair-free lineup weighted `exp((public projection − best)/τ)`.
The public projection is 85% this model's and 15% DK's lobby FPPF. τ = 8 for
GPP, 5 for cash (sharper).

This structure replaced a per-fighter softmax that produced **impossible
boards**: an implied average lineup over the $50,000 cap, then 70% on BOTH
corners of the main event. A lineup-level model cannot do either. It is the "Opt
Rate" idea from the NFL ownership research (Dan's Projections), and its three
constants are one grid search away from fitted.

## 9. What is NOT validated

| thing | status |
|---|---|
| **Ownership / the field** | a **PRIOR** (τ 8/5, FPPF weight 0.15). Never fitted to an MMA contest. **The first DK contest export is the most valuable file this build can receive** — `scripts/mma_calibration.py --fit-ownership`, cash and GPP separately |
| **Lineup head-to-head** | the objectives are argued from a calibrated simulation, not from a backtest against real contest results. No historical DK MMA salaries were found. |
| **Market constants on DK's own lines** | fitted on `ufc-master.csv` prices (a consensus source), applied to DraftKings'. DK's own pre-lock lines are now archived every capture for a refit. |
| **Salary-implied fallback** | one card, eleven bouts |
| **Sig-strike / takedown props** | parsed and captured but NOT used: their calibration is unmeasured. Displayed for reference in the log only |
| **Round-and-Method grid** | parsed, not used for timing: the fitted hazard is validated, the grid's pricing is not |
| **Captain Mode (169)** | not built |
| **Weight-miss / short-notice effects** | not modelled beyond what the market prices |

## 10. Weekly checklist

```bash
# any time -- the timer does this on a schedule (deploy/mma-odds-publish.timer)
python3 scripts/mma_odds_capture.py --push

# fight day, BEFORE THE FIRST BELL (no late swap)
python3 scripts/dfs_lineups_mma.py --capture          # fresh prices, then cash + gpp
python3 scripts/dfs_lineups_mma.py --board
python3 scripts/dfs_lineups_mma.py --mode gpp -n 3

# the day after: grade the forward log against UFCStats (no export needed)
python3 scripts/mma_calibration.py --grade --refresh

# after exporting contest standings from DK into data/ and tagging each
# cash/gpp in data/contest_meta.json:
python3 scripts/mma_calibration.py --fit-ownership

# re-fit everything
python3 scripts/mma_fit.py --refresh
python3 scripts/mma_fit.py --what timing --train-from 2019-01-01 --test-from 2100-01-01
```

## 11. Layout

| path | role |
|---|---|
| `edge/mma.py` | DK constants, scoring (validated), UFCStats loader, historical prices, name matching |
| `edge/mma_market.py` | DK Sportsbook parser, the de-biased outcome table, salary fallback |
| `edge/mma_sim.py` | **the fight simulator** — timing hazards, stats model, exact expectations |
| `edge/dfs_mma_theory.py` | the field model and the two field-relative objectives |
| `edge/dfs_opt_mma.py` | exhaustive optimiser, portfolio |
| `edge/dfs_run_mma.py` | slate → board → lineups, forward log; one entry point |
| `pages/7_🥊_MMA_DFS.py` | the phone app |
| `scripts/dfs_lineups_mma.py` | the CLI |
| `scripts/mma_odds_capture.py` | DK Sportsbook capture + push |
| `scripts/mma_fit.py` | every constant, plus the end-to-end backtest |
| `scripts/mma_calibration.py` | forward-log grading and ownership fitting |
| `tests/test_dfs_mma.py` | scoring, names, market, simulator invariants, field, optimiser |
| `deploy/mma-odds-publish.{service,timer}` | the capture schedule |

## 12. First card

2026-09-26, UFC Apex, 12 fights (Rosas Jr. vs Barcelos). Built after DFS lock
from sportsbook prices captured at 5:10 PM ET, before the first bell — logged to
`data/dfs_proj_log_mma.csv` as the first forward test. Nothing was entered.
Grade it with `--grade 2026-09-26 --refresh` once the UFCStats mirror has the
card (about a day).
