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

**Team-share fix, re-tested ON TOP of the last game (same folds, LAST_GAME refit per fold on each
model's own projection; scratch combined.py):** priced MAE CURR+LG -> TEAM+LG 8.543->8.492 (11/2,
no previous box), 7.892->7.732 (11/3), 7.225->7.195, 7.461->7.456, 7.020->6.975; rho .627->.634,
.618->.610, .658->.657; forward 7.621->7.601, 6.998->6.956. Where a last game exists the gain is
0.005-0.045 and rank is flat or worse: the last game already carries most of what the team split
did. **Not shipped.** Retest at 10 slates.

## Cycle 2 — baseline (2026-11-07, evening)

New harness: `harness run ncaab-lab -- report` (scripts/ncaab_lab.py) replays the shipped board
(as-run log, then LAST_GAME through the production last_game_lines / apply_last_game) on every
graded slate; its LOSO table reproduces cycle 1's held-out numbers exactly (parity). Same 5 slates
as cycle 1: 156344 (11/7) was not final at 5:49 PM. Held-out baseline (LAST_GAME refit per fold):

| slate | all MAE | all bias | priced n | priced MAE | priced bias | rho | bench MAE | bench bias |
|---|---|---|---|---|---|---|---|---|
| 11/2 155565 | 6.56 | +0.78 | 152 | 9.56 | +3.24 | .476 | 3.48 | -1.75 |
| 11/3 156039 | 7.64 | -0.74 | 113 | 9.01 | +3.37 | .475 | 6.45 | -4.31 |
| 11/4 156123 | 5.80 | +0.68 | 89 | 7.80 | +1.99 | .562 | 3.27 | -0.99 |
| 11/5 156240 | 5.88 | -0.18 | 114 | 7.48 | +0.60 | .616 | 3.84 | -1.17 |
| 11/6 156342 | 5.51 | -0.35 | 123 | 6.98 | -0.64 | .653 | 3.84 | -0.01 |
| pooled | 6.35 | | 591 | 8.25 | +1.76 | | 4.29 | |

### Cycle 2 — hole, fix, decision: LAST_GAME's minutes follow tonight's implied points, SHIPPED

**Hole (dfs-model-critic):** LAST_GAME shrinks the board's projection to ~0.36, and that projection was
the only carrier of tonight's game environment (ENV_POWER), so its biggest term, 0.374 x last
minutes, paid a 32-minute player at a 54.5-point underdog (Trey Perry, C-C, 11/5) the same as one at a
102-point favourite (ARIZ). Moved rows by implied points, LOSO: under 66 (n=102) bias +2.25, 80+ (n=184)
-1.45. Examples: Krivas (ARIZ, 102) 28.3 -> 33.1 vs 46.0 actual; Perry 22.0 -> 17.8 vs 17.75; worse:
Mandaquit (ARIZ) 21.4 -> 27.9 vs 7.0.

**Fix:** minutes term x (implied / LEAGUE.team_points)^1; LAST_GAME refit on the same 491 rows ->
(0.029, 0.298, 0.128, 0.449); `implied` added to the log; lab reads it or the backfill.

**Judgment call re-decided on train:** the critic picked power 1 from a table that showed held-out
slates. Re-chosen inside each fold by inner leave-one-slate-out over the training slates: k=1 in 4 of
5 folds (1.5 for 11/6: all 5.42, still better than 5.51). Forward with k chosen on 11/2-11/4 only (two
usable inner folds) picks 2: 11/5 all 5.96 -> 5.63, 11/6 all 5.47 -> 5.45 but priced 7.01 -> 7.07, rho
.628 -> .625: the one held-out cell that got worse.

**Held out (lab LOSO, LAST_GAME refit per fold; harness == critic to the 2nd decimal):**

| slate | rows moved | all MAE | priced MAE | priced rho | bench MAE |
|---|---|---|---|---|---|
| 11/2 155565 | 0 | 6.56 -> 6.56 | 9.56 -> 9.56 | .476 -> .476 | 3.48 -> 3.48 |
| 11/3 156039 | 16 | 7.64 -> 7.62 | 9.01 -> 8.96 | .475 -> .477 | 6.45 -> 6.46 |
| 11/4 156123 | 96 | 5.80 -> 5.69 | 7.80 -> 7.57 | .562 -> .580 | 3.27 -> 3.31 |
| 11/5 156240 | 150 | 5.88 -> 5.61 | 7.48 -> 7.08 | .616 -> .645 | 3.84 -> 3.75 |
| 11/6 156342 | 229 | 5.51 -> 5.35 | 6.98 -> 6.84 | .653 -> .664 | 3.84 -> 3.66 |
| pooled | | 6.349 -> 6.249 | 8.252 -> 8.100 | | 4.29 -> 4.25 |

Forward (k=1, fit 11/2-4): all 5.96 -> 5.63, 5.47 -> 5.27; priced 7.65 -> 7.11, 7.01 -> 6.79. Moved rows
$7K+ (n=51) MAE 8.02 -> 7.67, bias -3.13 -> -2.38 (eases cycle 1's star worry). Team-games 27 of 35
better. Bench on 11/4 slightly worse. **3 real slates: not enough data.** Shipped: wins the target
metric on every applicable held-out slate. 156346 (Sun 11/8 1 PM) rebuild vs the cycle-1 constants:
mean |change| 0.56; CAL (64.25 implied) down ~2-3, UH/USC/PITT (78-82) up ~1.5-2; cash out Long,
Indrusaitis, Phipps, in Heard, Foster, Rice; GPP Frison -> Long. The NCAAB projection history file
rotates once (new column), by design.

**Runner-ups:** (1) last game's started flag as a 5th term: LOSO priced 7.57/7.08/6.84 -> 7.47/6.88/6.69
but bench worse on 11/5-11/6; retest at 6+ slates. (2) The lab grades 11/2-11/4 on the pre-11/5
blend/salary model (priced bias +2..+3.4); replay them with fold-refit constants before judging any
priced-row change. (3) Spread / blowout terms null or worse; several previous games untestable yet
(0 teams had 2 in the window).

### Cycle 2 runner-ups, tested 2026-11-07 evening (on top of the shipped cycle-2 board; nothing shipped)

**Q_PLAY split by price** (availability runner-up: 9 of 14 tagged at $4K+ played, 5 of 28 below). Logged
x0.7 undone, (q under $4K, q $4K+) grid-fit on training slates' tagged rows, then LAST_GAME. Fits want
(0.0, 0.75-1.0). LOSO all MAE: 11/2 6.558 -> 6.589, 11/3 7.613 -> 7.456, 11/4 5.654 -> 5.667, 11/5 5.629 ->
5.612, 11/6 5.317 -> 5.286 (tagged n = 12/15/3/4/8). Worse on 2 of 5: **not shipped**; 42 tagged rows, retest
at 10 slates.

**RotoWire role offsets** (cycle-1 critic runner-up: listed non-starters +2.7..+5.8 high before
LAST_GAME). Mean residual per group on training slates, added to blend/salary rows. Pooled offsets:
LOSO all 6.558 -> 6.398, 7.613 -> 7.550, but 5.654 -> 5.733, 5.629 -> 5.714, 5.317 -> 5.423; forward worse on
both. Split by has-a-last-game: better only on 11/2 (no previous games). LAST_GAME absorbed it: **not
shipped**; it only helps on opening nights.

**Lab fidelity (cycle-2 critic runner-up 2): 11/2-11/4 replayed under the current model** (blend/salary
from the cycle-1 critic's replay inputs, critic-ncaab-1/rows.json; props and bench as logged), LAST_GAME
(env) refit per fold on top. Current-model all MAE is lower than the as-run lab shows on the old slates
(11/2 5.92 vs 6.56, 11/3 7.09 vs 7.62, 11/4 5.42 vs 5.69): the lab's as-run baseline overstates them.
(a) Refitting BLEND_RESULT / SALARY_TIER_LINE per fold vs the shipped ones, LAST_GAME on top: 11/4-11/6
differ by <= 0.015 all MAE (5.418/5.627/5.387 shipped vs 5.409/5.618/5.393), forward 5.653 -> 5.650, 5.281
-> 5.282: **no gain, constants kept** (all-5 refit would be -10.20 + 1.331 x blend, 4.42 + 1.685/$1K).
(b) LAST_GAME refit on current-model projections (all 5): (0.092, 0.315, 0.124, 0.444) vs shipped (0.029,
0.298, 0.128, 0.449); held out the shipped constants are as good or better on every slate: the cycle-2
fit is not distorted by the pre-11/5 logs.

**Not run:** last game's started flag (critic: retest at 6+ slates; 5 graded), transfers in a team's
first game (every team has now played: no such slate left this season).


## Cycle 3 — the lab applied LAST_GAME twice from 11/7 on: harness fixed, no model change (2026-11-08, unattended)

**Hole (found recording the baseline, before any critic).** 11/7 (156344) went final overnight and was
graded into the worktree for this cycle (`ncaab-box-grade 156344`; the box is NOT committed, see below).
It was built 14:49 ET, after cycle 1 shipped LAST_GAME (14:27), so its logged `proj` already has it --
and the lab applies LAST_GAME to the logged `proj`. Evidence, logged proj by the team's last game:
bench rows with 20+ last minutes 4.0 on 11/6 (n=10) vs **15.5 on 11/7 (n=6)**; bench with 0 last minutes
2.37 vs 1.02. The lab was grading 11/7 on a board that never ran (all MAE 5.21 doubled vs 5.13 as run),
and every slate from now on would have gone the same way, including through the critique gate.

**Fix (harness + one log column; the model is unchanged).**
- `scripts/ncaab_lab.py`: `base_projection` recovers the pre-LAST_GAME projection by inverting the step
  with the constants that ran: 156344 = cycle 1 (0.048, 0.359, 0.190, 0.374, no implied factor), any
  row with the cycle-2 `implied` column = cycle 2 (0.029, 0.298, 0.128, 0.449, x implied / 72.5), a logged
  `base_proj` = exact. Parity: re-applying cycle 1's constants to the recovered base reproduces 156344's
  log to 4e-15; 0 negative bases; recovered means bench 2.69 / blend 17.03 / salary 10.67 (n 70/54/5),
  the pre-11/7 boards' levels.
- `edge/dfs_run_ncaab.py`: the forward-test log gets `base_proj` (fp after Q_PLAY and the team budget,
  before LAST_GAME). Old rows read blank; the history file rotates once (new column).
- `data/ncaab_implied_backfill.json`: 156344's implied points, rebuilt by `game_lines` on dfs_ncaab
  scan 20564 (finished 1 s before the logged build). The same method on 156346 reproduces its logged
  implied on 12 of 12 teams.
- Test: `test_lab_takes_last_game_back_out_of_a_logged_projection` (+ base_proj in the log test).

**Held out, lab LOSO (LAST_GAME refit per fold), double-applied -> fixed.** 11/2-11/6 change only
through the refit including 11/7's correct rows:

| slate | all MAE | priced MAE | priced rho | bench MAE | bench bias |
|---|---|---|---|---|---|
| 11/2 155565 | 6.56 -> 6.56 | 9.56 -> 9.56 | .476 -> .476 | 3.48 -> 3.48 | -1.75 -> -1.75 |
| 11/3 156039 | 7.62 -> 7.62 | 8.96 -> 8.96 | .477 -> .477 | 6.46 -> 6.46 | -4.34 -> -4.36 |
| 11/4 156123 | 5.70 -> 5.68 | 7.57 -> 7.58 | .575 -> .577 | 3.33 -> 3.26 | -0.91 -> -1.04 |
| 11/5 156240 | 5.59 -> 5.58 | 7.04 -> 7.03 | .648 -> .649 | 3.76 -> 3.74 | -0.97 -> -1.15 |
| 11/6 156342 | 5.31 -> 5.27 | 6.81 -> 6.82 | .666 -> .669 | 3.59 -> 3.51 | -0.09 -> -0.33 |
| 11/7 156344 | 5.21 -> 5.06 | 7.08 -> 6.96 | .574 -> .610 | 3.21 -> 3.02 | +1.51 -> +1.29 |
| pooled | 6.122 -> 6.094 | 7.973 -> 7.961 | | 4.121 -> 4.074 | |

**Cycle 2 on its first real forward slate (11/7, one slate: not enough data).** As run (cycle 1's
constants live) vs cycle 2's shipped constants on the same base: all MAE 5.13 -> 5.06, priced 7.16 ->
6.96, rho .573 -> .610; bench 2.96 -> 3.02 (bias +1.14 -> +1.29: 11/7's bench ran high, unlike 11/2-11/6).
LAST_GAME refit on all 6 slates (-0.197, 0.335, 0.095, 0.451) vs shipped (0.029, 0.298, 0.128, 0.449):
the minutes term is stable; not refit (one more slate, and this cycle changes no model).

**Decision: SHIP the harness fix** (it is the judge: `scripts/ncaab_lab.py` is the gate's file, so this
branch goes to Adam, not the auto-merge). No model critic this cycle: the skill puts a faithful harness
before any critic, and a critic on the double-applied lab would have chased a phantom on 11/7.
**Conservative calls (unattended):** 156344's box is not committed (the gate compares main vs branch on
the same keys; grade it on main after this merges: `harness run ncaab-box-grade -- 156344` -- before the
merge main's lab would double-apply it). 11/8 (156346) is cycle 2's code, handled by `CYCLE2` once graded.

**Next critic:** 6 slates with a box; 11/7 is the first slate both built and graded under LAST_GAME. Open
leads from earlier cycles: last game's started flag (retest at 6+ slates: now), team-share at 10 slates,
GPP ownership refit at 5 GPP slates (11/7 has no GPP export).

Unattended gate (20261108-0621): **fail**

| split | state | n | clusters | main MAE | candidate MAE | loss diff [90%] | verdict |
|---|---|---|---|---|---|---|---|
| primary | all | 1,137 | 5 | 6.2493 | 6.2493 | +0.0000 [+0.0000, +0.0000] | unchanged |
- no primary state better beyond noise
