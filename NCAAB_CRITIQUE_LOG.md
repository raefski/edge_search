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

## Cycle 1 — four agents in parallel (2026-11-07): the team's LAST GAME, shipped

**Hole (dfs-availability-critic):** the board never read this season's box scores. A cheap
player who had just played 20+ minutes was still a 1-3 point "bench" row (Darius Bivins, STMRY
$3,000: 22 min / 24.0 on 11/2, projected 1.08 on 11/4, scored 26.75; Zavier Zens, Sebastian
Wilkins, Braydon Hawthorne, Tarris Bouie the same), and a blend player who did not dress was
still 12-16 (Salih Altuntas 16.6, Lee Dort 19.6, Kevin de Kovachich 14.3: none played). By the
previous game, n=514 rows with one: bench 10-19 min -> proj 2.9 / actual 10.0 (n=51); bench 20+
-> 3.7 / 12.9 (18); blend 1-9 min -> 13.2 / 3.0 (21). The box was final ~20 h before lock.
The expert-signals agent found the same thing independently (per-game prior points: priced
non-prop LOSO 8.11 -> 7.38, bench 4.51 -> 3.89; WORSE on props, n=23: the books price it).

**Fix:** `LAST_GAME`: proj' = 0.048 + 0.359 proj + 0.190 last_fp + 0.374 min(last_min, 35) on
every non-props row of a team whose last final game (ESPN, a day before the slate) resolves to
the DK roster (>= 50% of names); missing from the box = 0 / 0. Applied after Q_PLAY and the team
budget (it was fitted on the logged final proj). New `edge/ncaab_espn.py` (the grader's ESPN
code moved there); page shows "last game: N teams from espn" and warns on any team without one.

**Held out** (regression refit each fold; as-run proj as the baseline):

| slate | rows moved | all MAE | bench MAE | rho < $4K |
|---|---|---|---|---|
| 11/3 156039 | 16 | 7.74 -> 7.64 | 6.54 -> 6.45 | .30 -> .33 |
| 11/4 156123 | 96 | 6.77 -> 5.80 | 4.14 -> 3.27 | .42 -> .62 |
| 11/5 156240 | 150 | 6.18 -> 5.88 | 4.28 -> 3.84 | .43 -> .55 |
| 11/6 156342 | 229 | 6.54 -> 5.51 | 4.76 -> 3.84 | .37 -> .68 |
| forward, fit 11/2-4 | 11/5, 11/6 | 6.18 -> 5.96, 6.54 -> 5.47 | 4.28 -> 3.83, 4.76 -> 3.72 | .55, .69 |

By salary band, non-props, LOSO (11/4, 11/5, 11/6): $7K+ MAE 6.94->6.06, 8.46->7.61, 9.24->8.53
(n=21/13/27); $4-7K 9.87->8.69, 8.37->8.12, 7.36->6.38; under $4K 5.23->4.35, 4.82->4.49,
5.55->4.41. Better everywhere, but $7K+ bias moves under on 11/5-11/6 (-3.66 -> -6.54, -1.73 ->
-3.04): stars with a short last game are pulled down hard (Diop 27.0 -> 18.1 on 11/7). Watch it.
**Not enough data:** 3 slates (plus 16 rows on 11/3) have a previous box; fold coefficients
move (b .30-.41, c .16-.28, d .26-.44). Shipped because it wins on every held-out slate and
split; refit as slates come in. 11/7 3:00 slate (156344) diff: 91 rows moved; cash swaps
Almodovar, Sandfort, Neely for Nyk Lewis, Torey Alston, Djokovic.

**Model critic (projection level, not shipped yet):** (a) the across-the-board bias is rejected:
current constants refit LOSO, priced bias -0.07 (n=591, 5 slates); all-player -1.28 is the bench.
(b) team totals: blend/salary rows x (2.04 implied / team's priced sum)^0.625, renormalised per
slate. LOSO priced MAE 8.06 -> 7.94, rho .511 -> .539, all 6.44 -> 6.37, 5 of 5 slates, k .625 in
every fold. Measured WITHOUT the last-game layer, which moves the same rows: needs a combined
held-out test before it ships.

**Ownership (GPP only, 3 slates: not enough data, not shipped):** whole-slate softmax on
value, salary and DK-FPPG value, 800%: MAE 2.69 -> 2.08, 5.23 -> 3.38, 3.26 -> 2.73, top-10 MAE
and rho better on all 3 held out. 37 of 39 players the field owned at 20%+ started; RotoWire had
19 of them. Refit at 5 GPP slates (scratch lab: own-ncaab-1).

**Expert sources:** Torvik's day-before team ratings lose to the market on 203 D1 games
(margin MAE 10.51 vs 9.26, total 13.68 vs 12.99) and hurt as a player feature. Torvik's 2027
player table ignores its date filter (leaks). NCAAB_EXPERT_SOURCES.md.

**Runner-ups:** transfers in a team's first game on teams RotoWire did not list (11/3: 17 false
actives, 224 projected points); Q_PLAY flat 0.7 vs 9 of 14 at $4K+ and 5 of 28 below played;
RotoWire non-starters in blend run +2.7..+5.8 high (n=62).
