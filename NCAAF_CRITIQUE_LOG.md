# NCAAF critique log

Target metric: **DK points vs the pre-lock projection**, laddered players that
were logged (`data/dfs_proj_log_ncaaf.csv`) and drafted in a DK export on the
same draft group. Harness: `scripts/ncaaf_lab.py report [--cutoff]`.
Split: train = slates before 2026-10-11 (9/26 Night, 10/3 Main), test = every
slate since. The slate is the unit; 2 train slates is thin, say so with every fit.

Caveat on train: the 9/26 and 10/3 rows were logged by older code (pre suffix
fix, pre wrong-game fix). The log is what the app showed, so it is
live-faithful, but a correction fitted on train partly fits since-fixed bugs.

## Cycle 0 — baseline (2026-10-22), shipped ladder model

```
cutoff 2026-10-11: train 2 slates / 193 players, test 5 slates / 257 players
coverage (logged -> graded): 153951 68->63, 154161 136->130, 155010 128->127, 155105 17->16, 155109 30->28, 155112 27->27, 155122 74->59

TRAIN
  slice                     n     MAE    RMSE  bias(a-p)
  all                     193    7.85   10.50      +2.31
  QB                       35   10.72   12.62      +3.23
  RB                       48    8.37   11.75      +2.48
  WR                      110    6.72    9.08      +1.95
  $0k-5                    78    5.93    7.95      +1.48
  $5k-7                    81    8.10   10.97      +2.09
  $7k-+                    34   11.66   13.93      +4.76
  2026-09-26 153951        63    7.16   10.15      +3.88
  2026-10-03 154161       130    8.19   10.66      +1.55

TEST (held out)
  slice                     n     MAE    RMSE  bias(a-p)
  all                     257    6.17    8.15      +0.11
  QB                       49    6.60    8.48      -0.55
  RB                       61    7.14    9.51      +1.42
  WR                      147    5.63    7.39      -0.21
  $0k-5                    98    4.73    6.19      -0.14
  $5k-7                   108    6.67    8.56      +0.07
  $7k-+                    51    7.90   10.29      +0.67
  2026-10-17 155010       127    5.34    6.88      -0.57
  2026-10-14 155105        16    7.42    9.06      -2.36
  2026-10-15 155109        28    7.42   10.40      +2.27
  2026-10-16 155112        27    7.52    9.96      +2.62
  2026-10-17 155122        59    6.42    8.28      +0.07
```

## Cycle 1 — 2026-10-22: QB/RB projections too compressed — NOT SHIPPED

**Hole (critic):** for QB and RB the ladder pulls projections toward the
middle. The top third of RBs by projection beat it by +3.26 (train, n=16) and
+3.53 (test, n=21). Regressing actual on projection gives slopes above 1 in
both periods: QB 1.60 / 1.35, RB 1.00 / 1.30. WR is inconsistent (1.35 / 0.70).
Suspected cause: rush/rec TDs are imputed as a flat rate per yard
(`_ncaaf_impute`), which under-credits the highest-volume players.

**Fix tested:** `ncaaf_lab.py report --stretch` applies
p' = pivot + k(p − pivot) to QB and RB. The pivot is the train mean projection;
k is the slate-demeaned train OLS slope. Fitted values: QB (18.32, 1.62),
RB (12.02, 1.07).

| held out (5 slates) | n | MAE | RMSE | bias |
|---|---|---|---|---|
| all, shipped | 257 | 6.17 | 8.15 | +0.11 |
| all, stretch | 257 | 6.15 | 8.12 | +0.18 |
| $7k+, shipped | 51 | 7.90 | 10.29 | +0.67 |
| $7k+, stretch | 51 | 7.75 | 10.01 | −0.02 |

**Decision: not shipped.** The overall gain (−0.02 MAE) is inside the noise.
Two of the 5 test slates got worse (155105: 7.42→7.60, 155112: 7.52→7.65).
QB k = 1.62 rests on 35 train QBs logged by older code. A stronger hinge
version overshot on test (QB bias −1.68). The $7k+ result is a lead to recheck
once more slates are logged. The real fix belongs in the model: a TD rate that
rises with volume, refitted on cfbfastR.

**Runners-up:**
- Blending in DK FPPG (0.8 ladder + 0.2 FPPG): 5.68→5.65 leave-one-slate-out
  (n=186), but `dk_fppg` is missing on every train row, so it can't be fitted
  on train yet.
- Grading covers drafted players only (155122: 74 logged, 59 graded). Grade
  the undrafted from cfbfastR.
- Rush-attempt, pass+rush-yard and completion markets are collected in odds.db
  but not used. RB carries could shape the TD imputation. Needs a replay.
