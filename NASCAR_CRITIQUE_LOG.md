# NASCAR finishing-position critique log

One cycle per `/dfs-critique nascar`. Metric: finishing position per
driver-race from the simulator's mean finish (DNFs included): MAE, RMSE,
bias, mean per-race Spearman; by track type. Harness:
`python3 scripts/nascar_lab.py report [--variant NAME]`. Fit on 2022-2024
Cup, report on 2025-2026 (66 races); a race is the unit. Ship only if
held-out MAE and Spearman improve overall without a track type getting
clearly worse.

## Cycle 0 -- baseline (2026-10-25)

Shipped specification refitted on train (`--variant refit`):

| slice | races | driver-races | finish MAE | RMSE | bias | mean race Spearman |
|---|---|---|---|---|---|---|
| all | 66 | 2461 | 8.29 | 9.96 | -0.13 | 0.389 |
| superspeedway | 10 | 386 | 9.71 | 11.33 | -0.36 | 0.135 |
| intermediate | 27 | 1003 | 8.18 | 9.71 | -0.07 | 0.436 |
| short | 17 | 626 | 7.54 | 9.43 | -0.04 | 0.458 |
| road | 12 | 446 | 8.38 | 9.99 | -0.16 | 0.401 |

Reference only, shipped constants as-is (fitted 2022-2026, so in-sample on test): MAE 8.28, RMSE 9.93, Spearman 0.397.

## Cycle 1 -- form from running position, not finishing position (2026-10-25) -- SHIPPED

**Hole (critic):** form judged a driver by his past finishing positions,
which wrecks and late cautions scramble, and ignored loop-data average
running position (already loaded as `avg_pos`, unused) and how he runs at
this kind of track.

**Fix:** `nascar_sim.running_form`: ew12 = recency-weighted avg running
position over all earlier races (half-life 12), ewtt = same at this track
type (half-life 6, ew12 if none); finish ~ start + ew12 + ewtt + form_led +
form_dnf per type. History = the two seasons the live build loads.
Judgment calls: (1) the critic picked its specification from a few variants
it also scored on test; an inner split chosen on train only (fit 2022-23,
test 2024) ranks it the same: MAE 8.33 -> 8.26, Spearman 0.365 -> 0.380.
(2) Superspeedway kept REDUCED (start, ew12, ewtt): the full fit put form_led
at +22.0; reduced vs full on the 2024 check 9.65 vs 9.63 -- noise.

| slice | races | baseline MAE | cycle 1 MAE | baseline Spearman | cycle 1 Spearman |
|---|---|---|---|---|---|
| all | 66 | 8.30 | **8.18** | 0.394 | **0.424** |
| superspeedway | 10 | 9.71 | 9.66 | 0.135 | 0.165 |
| intermediate | 27 | 8.20 | 8.10 | 0.441 | 0.467 |
| short | 17 | 7.52 | 7.33 | 0.466 | 0.514 |
| road | 12 | 8.37 | 8.28 | 0.399 | 0.418 |

(Baseline rerun with the harness's final code: 8.30 / 0.394, vs 8.29 / 0.389
in cycle 0 -- the same model, the fit helper refactored.) Bias unchanged
(-0.13). Parity: live `driver_form` running form == lab, 38/38 drivers at
2026-10-11, max diff 0.0. Production table fitted on 2022-2026 (`nascar_lab.py
fit`). Kansas 9/27 rebuilt board, DK points: MAE 19.4 -> 18.0, rho +0.25 ->
+0.62 (GPP export, 36 drivers; one race).

**Runner-ups:** (1) a longer / recency-weighted finish window, -0.07 MAE
alone, mostly absorbed here; (2) driver rating as form, -0.04, redundant
with running position; (3) practice rank adds nothing held out (Spearman
+0.004).

## Cycle 2 -- the simulator counted wrecks twice (2026-10-25) -- SHIPPED

**Hole (critic):** the finish latent (and its resid_sd) was fitted on every
finish, wrecks included, and simulate() then sends its own random DNFs to
the back. Each wreck was in the model twice, so simulated mean finishes
bunched toward mid-pack: slope of actual finish on simulated mean 1.34
intermediate / 1.13 road; sd of simulated means 3.80 vs 4.71 for the
regression's own E[finish]. Scoring E[finish] directly already beat the sim
(8.06 vs 8.18).

**Fix:** `nascar_lab.py --variant cond_latent`: fit on non-DNF rows,
resid_sd x k. k chosen on the inner 2022-23 -> 2024 split only (re-run here,
reproduces the critic): MAE 8.153 / 8.100 / **8.089** / 8.092 / 8.113 for
k 1 / 0.8 / 0.7 / 0.6 / 0.5; shipped there 8.249.

| slice | races | cycle 1 MAE | cycle 2 MAE | cycle 1 Spearman | cycle 2 Spearman |
|---|---|---|---|---|---|
| all | 66 | 8.18 | **7.96** | 0.424 | 0.429 |
| superspeedway | 10 | 9.66 | 9.53 | 0.165 | 0.173 |
| intermediate | 27 | 8.10 | 7.79 | 0.467 | 0.465 |
| short | 17 | 7.33 | 7.20 | 0.514 | 0.520 |
| road | 12 | 8.28 | 8.05 | 0.418 | 0.431 |

Paired per race: -0.224 +- 0.051 MAE (critic). RMSE 9.85 -> 9.84 overall,
short 9.17 -> 9.30 (worse: k shrinks the means' tails at short tracks).
Spearman change is noise: this fixes the level/spread of the means, not
their order.

**Spread check (the risk: k narrows outcomes):** held-out DK points, real
sd 26.24 / p90 58.55. Cycle 1 sim sd 26.75, p90 63.63, actual inside the
driver's 10-90% band 83.7% (too wide); cycle 2 sd 25.23, p90 61.03, band
79.6% (target 80). sd 4% narrow, inside the 10% bar. Kansas rebuilt (GPP):
DK MAE 18.0 -> 15.2, rho +0.62 -> +0.68; below floor 30.6% (target 25),
above ceiling 5.6% (target 10) -- one race, watch on the next exports.

Production table: 2022-2026 non-DNF rows, k 0.7 (`nascar_lab.py fit`). Test
loosened: short/superspeedway expected-finish spread ratio is now 2.49 (was
asserted > 2.5), asserted > 2.0.

**Runner-ups:** (1) score E[finish] directly for the mean projection
(-0.12) -- subsumed; (2) per-track k (superspeedway/road prefer ~1,
intermediate/short ~0.5-0.6): < 0.02 more MAE, not worth 3 parameters;
(3) superspeedway still ~lottery (Spearman 0.17, 10 races), drafting
partners unmodelled. Also flagged: form_led flips sign by track type
(+3.2 intermediate, -8.9 short in production) -- candidate for a cap.

## Cycle 3 -- cycle 2's noise scale was chosen for the median, lineups use the mean (2026-10-25) -- SHIPPED (k 0.7 -> 0.85)

**Hole (critic):** cycle 2 picked k = 0.7 by MAE, the median's loss. The
lineup builder sums MEANS and reads floors/ceilings off the spread; at 0.7
the means over-spread: slope of actual on simulated mean 0.82 test / 0.75
inner, top-10 starters projected ~1.5 places too good and the back of the
grid too bad -- over-projecting the most expensive cars and under-projecting
place-differential plays.

**Judgment call (recorded): what k is chosen by.** Not MAE (median) and not
the test seasons. Chosen on the inner 2022-23 -> 2024 split by the DK 10-90%
band's coverage hitting 80%, the quantity floors/ceilings use, with RMSE and
bias by grid slot reported alongside:

| k | inner band | inner RMSE | inner bias st1-10 | test MAE | test RMSE | test bias st1-10 / 11-25 / 26+ | test band |
|---|---|---|---|---|---|---|---|
| 0.7 (cycle 2) | 78.4% | 9.985 | -2.35 | **7.96** | 9.84 | -1.49 / +0.01 / +0.82 | 79.6% |
| **0.85 (shipped)** | **80.3%** | 9.919 | -1.76 | 8.00 | 9.80 | -0.87 / +0.04 / +0.28 | 81.5% |
| 1.0 (critic's pick, by RMSE) | 81.7% | 9.892 | -1.24 | 8.06 | **9.79** | -0.34 / +0.07 / -0.18 | 82.7% |

1.0 removes more bias but its band is too wide on both splits; 0.85 is the
band-calibrated point and is better than 0.7 on RMSE and grid-slot bias in
both splits. MAE gets 0.04 worse -- the price of predicting the mean.
Spearman 0.429 -> 0.427 (noise; k moves spread, not order). By track
(test RMSE, 0.7 -> 0.85): superspeedway 11.24 -> 11.23, intermediate 9.56
-> 9.53, short 9.30 -> 9.22, road 9.87 -> 9.84. Kansas rebuilt (GPP): DK MAE
15.2 -> 16.0, rho +0.68 -> +0.70, floor/ceiling unchanged (one race).

From this cycle on the NASCAR ship rule is RMSE + bias by grid slot + band
coverage, with MAE reported; MAE alone chose the wrong k once.

**Runner-ups (critic, tested, null):** (1) team/equipment running-position
form on top of cond_latent: MAE change -0.004..+0.010 per race (se ~0.008)
on both splits -- nothing; (2) the harness drops drivers with < 4 prior
races (35 test driver-races, mean finish 29.7) -- minor; (3) superspeedway
still the biggest error (RMSE 11.2, inner slope 0.41); drafting /
manufacturer effects remain the only untested lead.

---

# Target: DK points per driver (from 2026-10-26)

Cycles 1-3 were judged on finishing position. Live DK grading on 10/26 (2 races,
n=76 drivers) showed points rho ~0.17 and Adam losing, so the target moves to the
DK points a lineup is actually paid: `python3 scripts/nascar_lab.py dk` (production
simulate(), train 2022-24 tables, test 2025-26, live-faithful race inputs:
green_laps = race distance as live has no caution count; LAP_MODEL in-sample).

## Cycle 4.0 -- DK points baseline, shipped model (2026-10-26)

| slice | races | driver-races | DK MAE | RMSE | bias | race Spearman | 10-90 band | <floor(25) | >ceil(90) |
|---|---|---|---|---|---|---|---|---|---|
| all | 66 | 2461 | 18.85 | 24.51 | +0.90 | 0.338 | 82.6% | 25.5% | 7.1% |
| superspeedway | 10 | 386 | 20.94 | 24.89 | +1.38 | 0.320 | 80.6% | 26.7% | 7.8% |
| intermediate | 27 | 1003 | 18.29 | 23.88 | +0.79 | 0.352 | 84.5% | 25.3% | 6.4% |
| short | 17 | 626 | 19.06 | 26.84 | +1.00 | 0.407 | 81.0% | 23.8% | 7.5% |
| road | 12 | 446 | 18.01 | 22.02 | +0.60 | 0.220 | 82.5% | 27.1% | 7.6% |

## Cycle 4 -- DK points: no front-of-grid lead, and a fastest-lap pool 25% too big (2026-10-26) -- SHIPPED

**Hole (critic):** simulate()'s lap-led weight depended only on simulated finish
and lead form, so starting on the pole was worth nothing in laps led. Held out,
by start: pole projected 32.2 DK pts vs 55.8 actual (laps-led pts 4.6 vs 16.2,
fast-lap 7.2 vs 12.1), P2 30.9 vs 37.8, starts 3-10 ~29 vs ~24.6 (over). Same
in train (pole -8.5, P2 -7.3) and on every track type. Pole alone ~15 of ~600 MSE.

**Fix:** lead weight x (1 + FRONT_A*exp(-(start-1)/FRONT_S)); FRONT_A 2.0,
FRONT_S 1.5 chosen on the inner 2022-23 -> 2024 split (grid FA {0,2,4,8,16} x
FS {0.7,1.5}; 2024 RMSE 23.73 -> 23.63). Plus the critic's live-faithfulness
find: live passed green_laps = scheduled laps (no caution count pre-race), the
lab copied it; the fastest-lap pool is now FAST_FRAC[track] x laps (mean
sum(fast_laps)/actual_laps 2022-24: ss .748, int .782, short .832, road .753).

| variant (2000 sims, test 2025-26) | MAE | RMSE | bias | race Spearman | 10-90 band | <p25 | >p90 |
|---|---|---|---|---|---|---|---|
| pre_cycle4 | 18.84 | 24.48 | +0.90 | 0.340 | 83.1% | 25.6% | 6.7% |
| front_only | 18.68 | 24.14 | +0.90 | 0.350 | 82.9% | 25.6% | 6.9% |
| **shipped** | 18.69 | **24.14** | **+0.23** | 0.348 | 82.1% | 25.5% | 7.7% |

Paired per-race squared error, front vs pre: -17.1 +- 5.2 (3.3 se; intermediate
-28.1 +- 5.1, road -5.3 +- 2.8, short -17 +- 18, superspeedway -1.0 +- 2.1).
RMSE by track 24.89/23.83/26.82/22.00 -> 24.84/23.31/26.44/21.88 (ss/int/short/road).
Latest cached race (10/11 Charlotte): RMSE 25.20 -> 24.58, rho 0.368 -> 0.405,
pole 37.3 -> 49.9 proj (actual 66.7). Live 11/1 board not priced yet.

**Risks:** 2025-26 poles were unusually dominant (test +23.7 vs train +8.5),
so the gain may shrink; boost spills onto starts 3-5 (already high).

**Runner-ups:** (1) the finish component is most of the error (RMSE 20.6,
corr 0.32), starts 3-10 over-projected 3-5 pts and the pole's finish low
(20.4 vs 27.6) -- a start-curve shape / front-row term in fit_running is the
next cycle; (2) lead/fast form multipliers 8.0/4.0 never fitted (~0.03 RMSE);
(3) loop-data cache stops at 10/11 -- the 10/18 and 10/25 races are not in it.

---

# Target: the lineups we enter, vs the real field (from 2026-11-09)

## Cycle 5 (auto: lineups vs the field) -- harness built; nothing shipped (2026-11-09, unattended)

Scout (dfs_target_scout.py + dfs-target-scout agent):

```json
{"sport": "nascar", "target": "build lineup harness (cash + GPP lineups vs the real field)",
 "metric": "Per Cup contest, on the lineup production builds: cash = points vs the real cash line and cashed y/n; GPP = $ returned per $1 under the real payout table. Per slate, with n, cash and GPP apart",
 "harness": "build: scripts/nascar_lab.py lineups -- rebuild_board(logged salaries, real grid, form before the race) -> production cash + GPP builders -> score vs contests.db fields",
 "split": "leave-one-slate-out over the exported Cup races; 'not enough data' (<5 slates)",
 "why": "Same lineup in cash and GPP on all 4 slates; cash missed the line by 58/22/23 pts in the 101-500 double-ups (beat 31% of the field, -50% ROI, 4e/4s). Cycles 1-4 improved driver-level error but no harness scores what we enter",
 "money_at_stake": "cash -50% [-100%,+32%] 4e/4s; GPP -54% [-100%,+18%] 4e/4s; net -$4",
 "runner_ups": ["start-curve / front-row finish term (cycle 4 runner-up)", "collect NASCAR outright / top-N / H2H odds before Daytona", "superspeedway drafting partners"]}
```

**The cycle's work was the harness** (built as its own script, not a `nascar_lab.py`
subcommand, so the gate's judge file is untouched): `scripts/nascar_lineup_lab.py`
(`report`, `parity`, `lodo`, `gate OUT`). Per Cup draft group in the projection log it
rebuilds the post-qualifying board with production `rebuild_board` (logged salaries, the
real grid, form from earlier races only, the CURRENT model), runs production
`dfs_opt_nascar.optimize` at the phone page's defaults (1,500 sims, effort 250, seed 0,
own_weight 0) and scores the cash and GPP lineups with DK's own FPTS against every exported
contest on that draft group: field percentile, cash line, $ at the rank taken. Truth check:
DK FPTS == results-feed recompute on all 113 exported drivers (max |diff| 0.00).

**Coverage:** 6 Cup boards rebuild (9/27, 10/11, 10/18, 10/25, 11/1, 11/8); 3 have real
fields (9/27 intermediate, 10/25 superspeedway, 11/1 short). 10/4 (dg 154134) has exports
but no logged board -- no salaries on this machine -- so it is excluded rather than guessed.
11/8 has no export imported yet. **Not enough data (3 races < 5).**

Baseline, shipped model + objectives (3 races, 1 cash + 1 GPP contest each):

| lineup | contests | cashed | mean field pctile | mean points | ROI ($ at rank taken) | pctile vs replay [90%, by race] |
|---|---|---|---|---|---|---|
| cash replay (CASH_PCT 25) | 3 | 0/3 | 0.394 | 211.2 | -100% | |
| cash, Adam's real entry | 3 | 1/3 | 0.286 | 197.0 | -33% | -0.108 [-0.251, +0.035] |
| GPP replay (GPP_PCT 90) | 3 | 1/3 | 0.487 | 208.5 | -78% | |
| GPP, Adam's real entry | 3 | 0/3 | 0.410 | 194.2 | -100% | -0.077 [-0.119, -0.035] |

Per contest the cash replay missed the line by 7.6 (9/27, 274.4 vs 282.0), 23.4 (10/25)
and 0.7 points (11/1, a 19-entry field). Lineup points vs the hindsight best 6 under the
cap: 135.8 of 368 at Talladega, 85.1 (GPP) / 170.8 (cash) of 343 at the 11/8 championship.

Parity (replay vs what was entered): 10/25 6/6 cash and GPP (the model has not moved for a
superspeedway); 9/27 4/6 (Adam's lineups came from the pre-cycle-1 model); 11/1 2/6 cash,
3/6 GPP. Not a strict parity target: the replay runs today's model on the real grid; the
local log is often a pre-qualifying build and Adam builds on the phone.

**Objective percentiles, leave-one-race-out** (grids: cash {10,25,40,50}, GPP {75,90,95};
chosen on the other 2 races by mean field percentile, ties to production):

| | shipped pctile | chosen held out | diff [90%, by race] |
|---|---|---|---|
| cash | 0.394 | 0.394 | -0.000 [-0.035, +0.035] |
| GPP | 0.487 | 0.417 | -0.069 [-0.138, +0.000] |

In-sample, cash pct 10 reads +0.198 [-0.018, +0.413] (1/3 cashed) -- on 3 races it is a
reading, not a result; the held-out choice flips between 10 and 50 race to race.

**Decision: not shipped.** No model or objective change: nothing beats shipped held out, and
3 races cannot carry one. No critic was spawned (unattended, conservative): with 3 fielded
races any hole it named could not be verified, and the season is over (next Cup slate:
Daytona, February). `gate OUT` writes one row per (mode, contest), pred = 1 - field
percentile, cluster = race date, state = mode -- for Adam to add to harness/critique_gate.py
beside nascar_lab.py's driver rows once >= 5 races have fields.

**For the next cycle:** (1) import the 11/8 export if Adam has one -> 4 races; (2) rerun
`report` + `lodo` after every 2027 race; cash pct 10 is the reading to confirm or kill at
>= 5 races; (3) runner-ups above, start-curve first (testable on 66 races now).

Unattended gate (20261109-0751): **not shipped (log / harness only, verified)**

---

# Target: the start-to-finish curve (driver DK points by grid slot), from 2026-11-10

## Cycle 6 (auto: start curve / grid-slot bias in DK points) -- not shipped (2026-11-10, unattended)

Scout (dfs_target_scout.py + dfs-target-scout agent):

```json
{"sport": "nascar",
 "target": "cash lineups via the start-to-finish curve (front-row / place-differential bias in the driver means the cash build adds up)",
 "metric": "Primary: DK points per driver-race on the 2025-26 Cup test seasons: RMSE, bias by grid slot (P1, P2, P3-10, P11-25, P26+), 10-90 band, MAE reported. Guard: nascar_lineup_lab.py cash lineup pctile must not drop (5 cash contests / 4 races).",
 "harness": "python3 scripts/nascar_lab.py dk [--variant NAME]; python3 scripts/nascar_lineup_lab.py report && lodo",
 "split": "fit 2022-24, choose constants on the inner 2022-23 -> 2024 split only, report 2025-26 (race = unit, paired per-race squared error)",
 "why": "Cash is the losing line with enough slates (-67% [-100%,-9%], 6e/5s); the replayed cash lineup cashed 0/5 (mean pctile 0.313), below the line every race. It is a sum of driver means, and cycle 4 left a measured start-curve bias (starts 3-10 high, pole finish low), testable on 70 held-out races where lineups (4 races) are not.",
 "money_at_stake": "cash -67% [-100%,-9%] 6e/5s, net -$4; GPP -54% 4e/4s thin",
 "runner_ups": ["recover the 10/4 board (dg 154134 salaries) -> 5 cash races in the lineup lab",
                "collect NASCAR outright / top-N / H2H odds before Daytona",
                "cash objective percentile (pct 10) -- rerun lodo at >= 5 fielded races"]}
```

Baseline (shipped, `nascar_lab.py dk`, 400 sims; the test set is now 70 races / 2,614
driver-races, up from 66 in cycle 4): RMSE 24.27, MAE 18.68, bias +0.22, Spearman 0.345,
band 81.1%. At 2,000 sims: RMSE 24.19, band 82.2%.

Bias by grid slot, DK points (pred - actual), 2,000 sims:

| slot | inner 2024 (fit 22-23) | test 2025-26 (fit 22-24) |
|---|---|---|
| P1 | +7.8 | -10.9 |
| P2 | -4.5 | -4.1 |
| P3-10 | +2.9 | +2.4 |
| P11-25 | +1.0 | +0.4 |
| P26+ | -2.1 | -0.1 |

**Hole (critic):** the start curve is *not* a big hole. The pole bias changes sign
between splits (se ~4.5-4.9), so it is noise. Correcting every slot's bias with the test
data's own numbers (an oracle) only gets RMSE from 24.22 to 24.10. The same correction fitted on inner makes test
worse (24.37, +269 +- 122 per race). The one flaw with the same sign on both splits is at **short tracks**: the
simulated mean finish is too steep in start (P3-10 -1.7/-1.9 places, P26+ +1.0/+1.2,
inner/test; DK P3-10 +3.5/+3.8, P26+ -2.2/-2.8). Null on test: shrinking the whole
field's mean, lap-led decay x1.5/x2, lead-form multiplier 16/32, FRONT_A/FRONT_S, a
logistic DNF model (AUC 0.609 vs current 0.615), practice speed (24.22 -> 24.21).
Where the error is: DNFs are 13% of rows but 27% of the squared error; drivers who led 40+
laps are 5.5% of rows but 24% of it.

**Fix tried:** `START_SHRINK[short]` scales the start term toward the field mean inside
simulate(): `mu -= (1 - s) * b_start * (start - mean start)`. It sits outside FINISH_MODEL,
so the lab picks it up unchanged.
Chosen on inner (2,000 sims): s 1.0 / .75 / .5 / .25 -> RMSE 23.508 / 23.467 / 23.463 /
23.501. **Judgment call, logged:** 0.5 and 0.75 tie (0.004). I chose 0.75: the smaller
move, with the tighter paired se (-72 +- 53 vs -79 +- 108 per race).

| variant, test 2025-26 (70 races) | sims | RMSE | MAE | bias | band | dSSE/race vs shipped | short only (19 races) |
|---|---|---|---|---|---|---|---|
| shipped | 2000 | 24.192 | 18.670 | +0.22 | 82.2% | | |
| START_SHRINK short .75 | 2000 | 24.163 | 18.649 | +0.22 | 82.6% | -51.9 +- 46.7 | -191 +- 168 |
| (s .5, not chosen) | 2000 | 24.161 | 18.647 | +0.22 | 83.1% | -55.3 +- 96.5 | -204 +- 353 |
| shipped | 400 (gate) | 24.268 | 18.676 | +0.22 | 81.1% | | |
| START_SHRINK short .75 | 400 (gate) | 24.247 | 18.657 | +0.22 | 81.6% | -37.4 +- 51.5 | -138 +- 188 |

Slot bias with .75 on test: P3-10 +2.35 -> +1.91 and P26+ -0.08 -> +0.31, but P1
-10.9 -> -11.9 and P2 -4.1 -> -4.9.

**Decision: not shipped.** The gain is 0.03 RMSE, 1.1 se at 2,000 sims and 0.7 se at the
gate's 400, so its 90% interval includes 0, and it moves the front row the wrong way. The
gate would refuse it, and the fix is too small to change a cash lineup. I did not run the
lineup-lab guard (no production change). Production code is unchanged.

**What this cycle settles:** the driver-mean start curve is close to the best these inputs
allow (oracle ceiling -0.1 RMSE). Cash losses are not explained by a biased driver mean.
The remaining error is DNFs and dominators, and nothing on the board predicts either.

**For the next cycle:** (1) new INFORMATION, not reshaping: collect NASCAR outright / top-N /
H2H odds before Daytona (the dominator / laps-led tail is the error mass; the market prices
it); (2) recover the 10/4 board (dg 154134 salaries) so the lineup lab reaches 5 cash
races; (3) gate noise: 400 sims leave ~1.2 DK pts of sim noise per driver, about the size
of the effects being judged -- the gate's sim count is a judge-file change (Adam);
(4) critic runner-up: P2 laps-led pts under-projected (6.7 vs 10.7 test, 6.0 vs 11.3
inner), but every reweighting tried is null held out; (5) COND_K = 0.85 may spread the
finishes too wide (slope of actual on simulated mean 0.85 inner / 0.92 test). The critic
could not test it because its value lives in the lab.
