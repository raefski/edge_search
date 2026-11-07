# NCAAB critique log

One cycle = a fresh critic finds the single biggest hole, it is fixed, re-measured held
out by slate, shipped only if better (`/dfs-critique ncaab`). Target: DK points vs the
logged pre-lock projection, graded on ESPN box scores including undrafted players
(`harness run ncaab-box-grade -- <gid>`, rows matched by draft group and `edge.names.norm`).

## Cycle 0 — baseline (2026-11-07)

As-run logs (`data/dfs_proj_log_ncaab.csv`), 5 slates, 1,137 player rows. 11/2-11/4 ran
before the 11/5 blend/salary-tier refit; 11/5-11/6 are the only slates the current model
built live. 11/5 and 11/6 were ungraded until six DK codes got aliases (CPS, C-C, DPU, USF,
JMU, MTSU in `edge/ncaab.py`).

| slate | gid | n | all MAE | all bias | priced n | priced MAE | priced bias | priced rho |
|---|---|---|---|---|---|---|---|---|
| 11/2 | 155565 | 300 | 6.56 | +0.78 | 152 | 9.56 | +3.24 | .476 |
| 11/3 | 156039 | 243 | 7.74 | -0.83 | 113 | 9.12 | +3.37 | .462 |
| 11/4 | 156123 | 159 | 6.77 | +0.37 | 89 | 8.84 | +2.62 | .368 |
| 11/5 | 156240 | 204 | 6.18 | -0.88 | 114 | 7.69 | -0.10 | .551 |
| 11/6 | 156342 | 231 | 6.54 | -1.43 | 123 | 8.11 | -0.82 | .483 |

By tier, all 5: props n=67 bias +0.44 MAE 8.54; blend 455 +2.12 / 8.66; salary 69 +0.01 / 9.16;
bench 546 -2.59 / 4.68 (59% played). bias = proj - actual.
