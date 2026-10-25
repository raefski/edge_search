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
