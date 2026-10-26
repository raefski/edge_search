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
