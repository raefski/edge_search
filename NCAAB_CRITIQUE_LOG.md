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

Unattended gate (20261108-0621): **review** -- waiting for Adam: harness critique-loop --merge / --drop
(re-labelled 11/8 by hand under the new rule: a change that edits the lab scoring it goes to Adam, not
fail. The gate saw only the 5 slates main can grade -- 11/7's box is not committed -- where the fix
changes nothing by design, hence "unchanged"; the slate it corrects, 11/7, is in the agent's table above.)

| split | state | n | clusters | main MAE | candidate MAE | loss diff [90%] | verdict |
|---|---|---|---|---|---|---|---|
| primary | all | 1,137 | 5 | 6.2493 | 6.2493 | +0.0000 [+0.0000, +0.0000] | unchanged |
- no primary state better beyond noise

## Cycle 4 (target: cash lineups -- build the lineup harness) — 2026-11-10, unattended

Scout (dfs-target-scout, `scripts/dfs_target_scout.py ncaab --rebuild` first):
```json
{"sport": "ncaab", "target": "build cash lineup harness",
 "metric": "Cash lineups replayed from the as-run board on each graded slate, scored on DK points against every real NCAAB Double Up field on that slate: share at or above the cash line and $ ROI per entry; GPP percentile secondary; never pooled.",
 "harness": "build: scripts/ncaab_lineup_lab.py, ported from scripts/nba_lineup_lab.py",
 "split": "Leave one slate out over the 4 slates with cash fields (11/2 155565, 11/4 156123, 11/5 156240, 11/7 156344).",
 "why": "Cash lost -67% [-100%,-6%] (6 entries/4 slates) while three cycles improved player MAE (pooled 6.35 -> 6.09); nothing scores the lineups we enter.",
 "money_at_stake": "NCAAB cash -67%, 6 entries / 4 slates in 17-23 entrant Double Ups; GPP -44% [-100%,+40%] 6 entries / 4 slates.",
 "runner_ups": ["last game's started flag as a fifth LAST_GAME term (retest at 6+ slates)",
                "GPP ownership whole-slate refit (waits for a 5th GPP slate)",
                "grade 11/8 + 11/9 and commit 156344's box on main (prerequisite)"]}
```

**This cycle is the harness (skill section 1); no critic, no model change.** New
`scripts/ncaab_lineup_lab.py` (`parity | report | rows | gate`): each logged board with a contest
field, in log order (= production's pool order), turned into production's pool (means_from_total on
the position profile, rescaled once so each player's 2,000-sim mean = the variant's projection),
`ncaab_sim.simulate` with the logged / backfilled implied points, `add_ownership`,
`dfs_opt_ncaab.optimize` (150 iters, own_weight 0). Scored with DK's own points (2,752 of 2,752
lineup-player lookups) in every contest of the lineup's type on its draft group: field percentile,
cashed, $ back per $1 from the payout table. Variants: `shipped` = ncaab_lab's base + LAST_GAME refit
leave-one-slate-out (today's production, held out); `as_logged` = the board as it ran. Single lineup
(seed 0) and 20 independent seeds. ~3.5 min on all cores.

**Parity** (seed-0 replay of the logged board vs the logged lineup, players shared of 8):
155565 cash 6 / gpp 5, 156039 6/5, 156123 6/5, 156240 5/6, 156342 6/6, 156344 8/5, 156346 8/7,
156355 8/8. Not exact: props players' stat split, Torvik splits and each game's home/away side are not
logged (the sim's RNG order and the props' shape follow them). Logging the matchup + means would make
it exact -- a cheap next step if a lineup cycle needs to separate optimizer effects of < 1 player.

**Baseline (4 slates, 6 cash / 6 GPP contests: NOT ENOUGH DATA):**

| variant | mode | field pctile [90%, by slate] | cashed | mean pts | $ back per $1 |
|---|---|---|---|---|---|
| shipped / set20 | cash | .417 [.300, .674] | 41% | 216.8 | 0.82 |
| shipped / single | cash | .412 [.259, .731] | 33% | 218.0 | 0.67 |
| as_logged / set20 | cash | .299 [.205, .489] | 32% | 201.9 | 0.63 |
| as_run (logged lineups) | cash | .334 [.190, .611] | 33% | 208.5 | 0.67 |
| Adam's entries | cash | .285 [.076, .611] | 33% | 199.8 | 0.67 |
| shipped / set20 | gpp | .576 [.523, .631] | 22% | 210.8 | 0.44 |
| as_logged / set20 | gpp | .523 [.455, .597] | 23% | 204.9 | 0.49 |
| Adam's entries | gpp | .482 [.260, .703] | 33% | 198.5 | 0.74 |

Per cash slate (shipped set20): 11/2 pctile .35, cashed 5% (line 209.5); 11/4 .66, 95%; 11/5 three
Double Ups .18-.33, 15% (lines 242.75-249.75: the night the field ran far above every board);
11/7 .71, 100%. What it says, as a hypothesis: today's production (LAST_GAME on) builds better cash
lineups than the boards that ran (+.12 pctile, +15 pts) -- 11/4 carries most of it (as run 0.25 ->
0.66 by set). 11/5 is the loss either way: Adam's entry (196.2) scored 20 under the replay and 50
under the line, so construction on that night is the first thing to look at.

**Gate:** `python3 scripts/ncaab_lineup_lab.py gate OUT` writes 12 rows (one per mode x contest, cluster
= slate, pred = 1 - field pctile of the shipped 20-lineup set, actual 0, loss "abs"), deterministic.
Not added to harness/critique_gate.py (conservative, unattended: Adam approves; under 5 slates it would
refuse anyway). Test: `test_lineup_lab_replays_a_logged_board_and_scores_it_against_the_field`.

**Conservative calls (unattended):** 11/8 / 11/9 not box-graded (no contest field on either; grading
writes data/ncaab_box, done on main). No critic this cycle: 4 slates cannot separate a lineup change
from noise, and the skill puts the harness first.

**Next critic (lineups):** cash target = the shipped set20 pctile / $ per $1 by slate; start with 11/5
(cash line 250, every lineup 214-232). Re-run `report` as each new Double Up lands; at 5+ cash slates
propose the gate wiring.

Unattended gate (20261110-0658): **not shipped (log / harness only, verified)**


## Cycle 5 (target: cash lineups -- the optimizer selects our errors; T08, from NHL L05) — 2026-11-13, unattended

Scout (dfs-target-scout, `scripts/dfs_target_scout.py ncaab --rebuild` first):
```json
{"sport": "ncaab", "target": "cash lineups: the optimizer picks our projection errors (selection gap)",
 "metric": "The shipped 20-lineup cash sets, replayed per slate and scored against the real Double Up fields: field percentile and $ back per $1 (8 Double Ups / 6 slates). Paired second measure: actual DK points per cash lineup, variant minus shipped, per slate, on all 11 logged boards. Guards: GPP set20 percentile and ncaab_lab all-player MAE no worse beyond noise. Diagnostic: lineup actual minus projection, ours vs the field's.",
 "harness": "python3 -m harness run ncaab-box-grade -- <gid> for 156346 156355 156569 156549 156551 (the lab's report crashes now: KeyError, no box for 156355/156569); then python3 scripts/ncaab_lineup_lab.py report --variant <new> and rows OUT.json; python3 scripts/ncaab_lab.py report as the guard",
 "split": "Leave one slate out over 11/2-11/12 (11 boards; Double Up fields on 155565, 156123, 156240 x3, 156344, 156355, 156569), any constant refit inside each fold; forward check fit 11/2-11/7, test 11/8-11/12",
 "why": "Our entered lineups ran 36.8 pts per lineup [-45.2, -28.4] worse against our own projection than the field's lineups did, over 6 slates. On 11/9-11/10 they were projected 243-253 and scored 167-207, while the field's sat -12 to -21. The board's top 15 props players ran 2-8 pts high while props across the whole board ran only about +1.",
 "money_at_stake": "NCAAB cash -57% [-100%, +4%], 8 entries / 6 slates, net -$8; GPP -63% [-100%, -6%], 8 entries / 6 slates, net -$3.65",
 "transfer": "T08",
 "runner_ups": ["props rows are raw ladder means with no calibration fitted on results (NBA's PROPS_BLEND shrinks its market to 0.76 + 4.4)",
                "GPP lineups in GPP mode on the same harness (-63%, 6 slates); not ownership (graveyard, and NBA's T20 was null)",
                "T07 availability starters: RotoWire's starter flag now only feeds the under-$4K rule, and LAST_GAME carries roles"]}
```
(The 02:00 run of this cycle hit the usage limit with nothing committed; this is the 08:43 re-run.)

### Before the critic: 13 team codes matched no school -- SHIPPED (mechanical, the name-suspect rule)

Grading the five new slates failed on three: CINCY/MINN (11/9), ARK/RMU, NEV/UCSB, STC/UNT (11/11), OKST/TA&M,
RICH/SIU (11/12) "not found on ESPN", all final. DK's codes matched no school: `match_team` gave 0 for CINCY,
MRSHL, NFLA, RMU, STC, STJN, UCSB, JACST, MONTS, SIU, TA&M, TNTC (and UNT only 0.8). The same function gates every
team-keyed join in production, so those **13 team-slates on 11/9-11/12 ran with: no last game** (logged proj ==
base_proj up to sim noise: LAST_GAME never moved them), **no RotoWire five** (0 starters listed: their $3,000
rows stayed "blend" at 7-12 points instead of "bench", e.g. UCSB John Simcoe $3,000 7.4 after 3 minutes), **no
conference tier**, and **no line** for ARK/RMU, NEV/UCSB, RICH/SIU (one side matched 1.0 + 0 < the 1.2 event
threshold: league-average implied). Our 11/12 lineups held three players from those teams (Sanders TNTC,
Haggerty and Mgbako TA&M); 11/12 has no contest export.

Audit of every code on the 11 boards (107), true school = most roster-name overlap with Torvik's player table:
- **GWB is Gardner-Webb**, not George Washington (11/5 @ Texas; 3 DK names are Gardner-Webb's last season). The
  11/7 alias sent its last-game search to the wrong school, which the roster check then refused.
- Codes that also claimed another school (team_tiers votes over every Torvik row a code matches): UNC -> UNC
  Asheville / Greensboro / Wilmington (3 low-major rosters vs 1 ACC: UNC's tier vote went to the low majors), TEX
  -> all six Texas schools, PUR -> Purdue Fort Wayne, NEB -> Nebraska Omaha, TENN -> Tennessee Martin, IU -> IU
  Indy, IOWA -> Iowa St., COL -> Colgate / Columbia / Colorado St., CAL -> five Cal schools, JAC -> Jackson St. +
  Jacksonville St., UCSB -> Cal St. Bakersfield (initials), KU -> Kansas City, and ESPN's non-D1 namesakes.
- Torvik spellings that nothing matched: "Cal St. Northridge" (CS-NR), "Nebraska Omaha" (OMAHA).

**Fix** (`edge/ncaab.py`): 24 new aliases (incl. TEX "texas", GWB "gardner webb"), two added spellings (CS-NR
"cal st northridge"; MISS "ole miss", ESPN's name, before MISS reaches a board) + 30
`_OTHER_SCHOOL` words (asheville, fort, omaha, martin, city, edwardsville, poly, st, college, valley, ...). Now
every code on the 11 boards resolves to exactly one Torvik school and one ESPN team (BSU / SDSU / USF are two
schools by DK's design; the roster / opponent check decides). Last-game coverage, 11 slates: missing team-slates
54 -> 40, every one of them a season opener (11/2-11/5); 0 missing from 11/6 on. Tests:
`test_cycle5_codes_find_their_school`, `test_cycle5_namesakes_do_not_match`,
`test_every_aliased_code_names_one_torvik_school`.

**Held out, player lab LOSO (LAST_GAME refit per fold), same 11 graded slates, main's matcher -> fixed:**

| slate | all MAE | priced MAE | bench MAE |
|---|---|---|---|
| 11/3 156039 | 7.61 -> 7.61 | 8.95 -> 8.94 | 6.44 -> 6.44 |
| 11/4 156123 | 5.65 -> 5.63 | 7.62 -> 7.61 | 3.14 -> 3.11 |
| 11/5 156240 (GWB) | 5.56 -> 5.49 | 6.99 -> 6.93 | 3.74 -> 3.67 |
| 11/6 156342 | 5.26 -> 5.24 | 6.86 -> 6.85 | 3.44 -> 3.40 |
| 11/7 156344 | 4.88 -> 4.84 | 6.93 -> 6.89 | 2.70 -> 2.64 |
| 11/8 156346 | 5.68 -> 5.63 | 8.66 -> 8.65 | 2.69 -> 2.61 |
| 11/9 156355 (3 teams) | 5.62 -> 5.42 | 7.24 -> 6.97 | 2.73 -> 2.64 |
| 11/10 156569 | 5.81 -> 5.75 | 7.50 -> 7.46 | 3.49 -> 3.39 |
| 11/11 156549 (6 teams) | 6.21 -> 5.55 | 7.58 -> 6.76 | 2.99 -> 2.71 |
| 11/12 156551 (4 teams) | 5.70 -> 5.08 | 7.31 -> 6.57 | 1.53 -> 1.22 |
| pooled (11/2 unchanged) | 5.954 -> 5.815 | 7.788 -> 7.596 | 3.580 -> 3.510 |

Only the last-game part is replayable (the log holds base_proj); starters, tier and lines are not, so this
understates the fix. Gate preview (critique_gate.compare on `ncaab_lab.py gate` rows, main's 6 slates): branch
vs main all MAE 6.0935 -> 6.0391, -0.054 [-0.101, -0.022]; of which the matcher alone (6 boxes) -0.012 [-0.025,
-0.003] and the 5 extra training slates -0.043 [-0.084, -0.016] -- each better beyond noise on its own.

**Lineups (paired, 20 seeds x 11 boards, the critic's dump harness):** main's matcher -> fixed, cash points per
lineup -2.5 [-4.3, -0.7] (3 of 11 better), GPP -0.8 [-4.0, +2.7]; Double Up pctile -0.020 [-0.056, +0.005] (6
slates). The swaps on the four slates the fix touched are idiosyncratic (11/11: UNT's Chavis 31.0 and Ndour 22.5
in, Lockett JACST 6.5 in, and a knock-on Nigel James Jr. 33.8 proj -> -0.5; 11/12: SIU's Haynes 35.5 in, Robert
Wright III 44.0 out), and 11/10, where no code changed (only the LAST_GAME refit moved a little), swung -11 by
itself. **Placebo** (the fixed-matcher board x (1 + 0.01 N(0,1)) per player, nothing else; three seeds): cash
+4.1 [-0.3, +8.5], -0.6 [-4.3, +3.3], +2.2 [-0.7, +5.1]; GPP +4.3 [+1.3, +7.4], -0.4 [-3.5, +2.1], +2.4 [-1.2,
+6.1]; forward boards (11/8-11/12) cash +7.9 [+1.5, +14.6] on placebo 1; single slates swing +23 / -15. A 1% nudge moves replayed lineups as much as this fix: the slate bootstrap on paired
lineup points does not carry the optimizer's own discontinuity and must not be read as "beyond noise" for
changes this small. The fix ships on the player metric and on correctness.

### Cycle 5 -- hole, fix, decision: per-tier recalibration of the cash board, NOT SHIPPED

**Hole (dfs-model-critic):** the cash optimizer ranks players on projections the selection itself inflates:
it picks the rows whose projection most beats their salary, which are the most over-projected, and nothing on
the board maps (projection, salary) to the expected actual. Shipped cash sets on all 11 boards: projected 220.4,
scored 196.3 (-24/lineup); worst on props-heavy boards (11/10 244 -> 183, 11/9 248 -> 196, 11/11 210 -> 168).
Props players inside the cash lineups ran +4.41 each (904 lineup slots, 10 slates) vs +1.14 for all props rows
(218); blend players in lineups +1.81.

**Fix tested** (critic's variant, my re-scoring of its dumps): after LAST_GAME, per tier (props; blend + salary)
actual = a + b proj + c salary/$1K by OLS on the training slates, refit inside each fold (bench as is, floor 0).
Fits stable: props b .40-.59, c 0.96-1.52; blend/salary b .85-.93, c .41-.69.

| measure (held out) | shipped | recalibrated |
|---|---|---|
| cash Double Up pctile, set20 (8 contests / 6 slates) | .396 [.294, .556] | .483 [.398, .596] |
| cashed / $ back per $1 | 30% / $0.60 | 53% / $1.06 |
| paired Double Up pctile, by slate | | +0.064 [-0.022, +0.140] |
| paired $ per $1, by slate | | +0.18 [-0.32, +0.65] |
| paired cash points / lineup, 11 boards | | -0.4 [-5.7, +5.2], 5 of 11 better |
| forward fit (11/2-11/7 only), cash points, 11 / 5 fwd boards | | -1.5 [-7.2, +4.3] / -2.7 [-11.1, +4.5] |
| GPP set20 pctile (guard) | .559 | .517; paired -0.043 [-0.093, +0.010] |
| forward fit, GPP points on 11/8-11/12 (guard) | | -6.8 [-11.6, -2.3], 0 of 5 better |
| lineup projection vs actual, cash | 220 vs 196 | 198 vs 196 |
| player MAE LOSO (guard) | 5.815 | 5.732 (8 of 11 slates better) |
| props-only shrink: cash points / pctile | | +0.1 / -0.016 |

**Decision: NOT SHIPPED.** The Double Up gain rides on three slates (11/2 +19 pts, replayed on the pre-11/5
blend model; 11/5 +11; 11/10 +14) and its paired interval spans 0; lineup points are flat (-0.4) and inside the
placebo range above; the forward fit is worse on GPP. The recalibration makes the cash board honest (-24 -> -2
pts/lineup) without picking better players: at this level the projections cannot separate the candidates. The
critic's own reading: ship it, if at all, as the page's projected-total / cash-line comparison (the page
overstates a cash lineup by ~24 points), not as the optimizer's input -- left for Adam (display, no money test).
T08 set to null (scripts/dfs_transfer.py).

**Runner-ups (critic):** (1) the field knows something at lock we don't: cash-field-owned players beat our proj +
salary by 11.2 pts per 100% owned (780 player rows, 6 slates, in sample) -- news / minutes, not calibration;
(2) blend recalibrated on its own last game (proj, salary, last points, last minutes): blend/salary MAE 7.38 ->
7.14 held out (1,051 rows) vs 7.26 salary-only, not tested on lineups; (3) the lab replays 11/2-11/4 on the old
blend model, so gains there are inflated -- flag or replay those slates before any lineup gate.

**Also this cycle:** `scripts/ncaab_lineup_lab.py` leaves out (and names on stderr) a contest slate with no box
instead of crashing. Five slates graded and committed (156346, 156355, 156549, 156551, 156569: 11 with boxes).
**Conservative calls (unattended):** the boxes are committed on the branch this time -- cycles 3-4 left grading to
main and it never happened, and the lineup lab crashed without them; the gate preview above separates the boxes'
effect from the matcher's. No mirror (NCAAB has none). Cash-only recalibration and the display-only total were
not built (no money evidence; Adam's call). Verified: `harness verify` (112 tests), DFS suite 577 passed, NCAAB
page AppTest no exception (05:45: no fresh prices, nothing to build -- the build path is covered by the tests).

**Next critic:** the lineup lab's noise floor is the binding constraint: before another lineup cycle, average
each variant over several perturbation seeds (or more sim seeds per lineup) so a paired difference can be told
from optimizer chaos. Information at lock (runner-up 1) is the lead for cash.

Unattended gate (20261113-0843): **pass** -- re-gated after 7 forward days, then merged unless vetoed

| split | state | n | clusters | main MAE | candidate MAE | loss diff [90%] | verdict |
|---|---|---|---|---|---|---|---|
| primary | all | 1,282 | 6 | 6.0935 | 6.0391 | -0.0544 [-0.1013, -0.0221] | better |

Unattended gate (20261121-0658): **dropped at re-gate (fail)**


- rebase onto main failed: git rebase -q origin/main: error: cannot rebase: You have unstaged changes.
error: Please commit or stash them.

## Cycle 6 (availability: projected starters / late scratches; T07, from NBA) — 2026-11-22, unattended

Scout: `{"target": "availability at lock: projected starters / late scratches (T07), scored on lineups",
"metric": "held-out set20 field pctile + $ per $1, cash first, GPP guard, player MAE LOSO guard",
"harness": "scripts/ncaab_lineup_lab.py report --n 20 (+VARIANT), scripts/ncaab_lab.py",
"split": "LOSO over boards with box + contest; 11/2-11/4 reported apart (old blend)",
"why": "cycle 5 runner-up: cash-owned players beat proj+salary by 11.2 pts per 100% owned (780 rows, 6 slates)",
"money_at_stake": "cash +48% [-28,+124] (15 entries/10 slates); GPP -37% (12/10, net -$3)"}`

**Hole (dfs-availability-critic):** a cheap (<$5K) RotoWire-listed starter on the salary/bench path (no Torvik
history, no props) gets no credit for starting. 13 rows / 8 boxed slates: proj 11.8, scored 17.7 (+5.9; Stillwell
11/10 $3,000 2.3 -> 31.75, Goosby 11/5 7.7 -> 30.5 at 76% cash-owned, L'Etang 11/14 7.6 -> 39.25).
Other availability measures (8 slates, 1,689 rows): false actives are bench rows projected 0-3 (LAST_GAME already
zeroes last-game DNPs; ~3 of 128 replayed lineup slots); starter flag precision .904 (283/313), recall .497
(RotoWire covers ~half the teams; 4 late builds 11/17-11/20 logged no starters); false outs unmeasurable (OUT
rows aren't logged); lead time unmeasurable (one build per slate, 14-72 min before lock, no news timestamps).

**Fix tested** (`starter_bump`): eligible rows + STARTER_BUMP after LAST_GAME, the bump = mean train residual of
eligible rows (floor 0, >= 5 rows), refit per LOSO fold (3.7-8.4 on 10-13 rows).

| held out (6 slates / 8 contests per mode, set20) | shipped | starter_bump |
|---|---|---|
| cash pctile | .368 [.252, .544] | .441 [.306, .578] |
| cash cashed / $ per $1 | 31% / 0.62 | 41% / 0.81 |
| GPP pctile (guard) | .529 | .538 |
| cash pts/lineup by slate | | 11/5 +17.1, 11/7 +15.5, 11/10 +1.2, 11/16 0, 11/2 -5.0, 11/4 -4.0 (old blend) |

**Decision: NOT SHIPPED (conservative, unattended).** 13 qualifying rows, the gain is two slates, and cycle 5's
placebo showed a 1% nudge swings single slates +23 / -15; no perturbation-seed average was run. Risks: RotoWire
starters who come off the bench (Morillo, Landeira) get inflated; the variant silently does nothing on builds
where RotoWire returned no starters. Re-test once more boxes are graded (9 of 24 logged boards have no box here).

**Runner-ups:** (1) grading/logging gaps: commit the missing boxes, log OUT players at proj 0, add a rebuild ~20
min before lock (makes scratches and lead time measurable); (2) no visible fallback when RotoWire returns no
starters (4 late builds): last game's 20+ minute players as starters hit precision .80 / recall .77 on unlisted
teams; (3) **bug:** two "Christian Jones" on LBS (11/16) share one key -> `KeyError: 'last_min'` in
scripts/ncaab_lab.py and the lineup lab; key by DK player id. Not fixed here: the labs are the gate's judge
files -- left for Adam.

Unattended gate (20261122-0658): **not shipped (log / harness only, verified)**


## Cycle 7 (auto: cash lineups -- re-test the cheap-listed-starter credit; T07, from NBA) — 2026-11-25, unattended

Scout: `{"target": "cash lineups: re-test the starter credit for cheap RotoWire-listed starters (T07) on the newly graded slates, with each variant averaged over several seeds",
"metric": "held-out cash set20 field pctile + $ per $1, paired by slate; guards GPP pctile, player DK-pts MAE LOSO",
"harness": "ncaab_box_grade.py for missing slates, then ncaab_lineup_lab.py report --n 20 (shipped vs starter_bump), ncaab_lab.py MAE guard",
"split": "LOSO over boards with box + contest; 11/2-11/4 apart; post-11/22 as forward subset",
"money_at_stake": "cash +49% [-24,+122] (16 entries / 11 slates, net +$27); GPP -37% (12 / 10, net -$3)",
"runner_ups": ["fallback when RotoWire lists no starters (last game's 20+ min players)", "NHL c17 GPP-median transfer", "T26 non-props rows in priced games"]}`

**Grading first:** 16 more slates boxed from ESPN (8 -> 24): 156346 156355 156549 156551 156653 156658 156662
156663 157013 157015 157017 157019 157032 157033 157138 157139. Not final on ESPN, nothing saved: 156656 (UTAH/UW),
157021, 157024, 157026.

**Lab bug fixed (judge file, goes to Adam):** `scripts/ncaab_lab.load` iterated the name|team dict, so the second
Christian Jones (LBS, 11/16, 156660) never got `last_min` and every LOSO run crashed (cycle 6 runner-up 3). It now
iterates the rows; the shadowed twin takes the no-history path. No other slate's rows change.

**Finding: the hole has closed by itself -- no critic spawned, no variant built.** Cycle 6's rows (cheap <$5K
listed starter on the bench/salary path) exist only on 11/2-11/10: 17 rows on 7 slates, 0 on the 15 slates since
11/11 (by then every college player has a DK season FPPG, so cheap listed starters go to `blend`: 58 blend +
40 props logged 11/12-11/24, 0 bench/salary). Measured LOSO on ncaab_lab (LAST_GAME refit per fold, bump = mean
train residual of eligible rows, refit per fold):

| held out | n rows | slates | shipped MAE | + starter bump |
|---|---|---|---|---|
| all players | 4,142 | 24 | 5.5468 | 5.5414 (all of it on 11/2-11/10; 0 eligible rows after) |
| cheap listed starters on blend, 11/12-11/22 (residual act - proj) | 36 | 10 | +0.78 (4 of 10 slates > 0) | -- |
| cheap listed starters on props, same window | 26 | 6 | -3.07 (1 of 6 > 0) | -- |

**Decision: NOT SHIPPED.** A bump that fires on no current build cannot move tonight's lineups; the blend
already credits these starters (+0.78, slates split 4/6). The lineup replay was not run for that reason (the
variant is a no-op on every slate since 11/11). T07 set to null (scripts/dfs_transfer.py).
**Conservative calls (unattended):** skipped the critic (the scout's target was a measured re-test with its answer
in hand); fixed the lab crash although the lab is a judge file -- the branch goes to Adam.

**Next critic:** the boxes now cover 24 slates; the lineup lab can finally run on 15+ forward slates -- the
scout's runner-ups (no-starters fallback; GPP median build from NHL c17) are the leads. Cheap props starters ran
-3.07 (26 rows) -- small, watch.

Unattended gate (20261125-0707): **crashed** in main's baseline gate (scripts/ncaab_lab.py KeyError 'last_min': two LBS Christian Jones on 156660). Nothing shipped by the agent, so nothing to gate; entry, boxes and T07 status carried by hand 2026-11-25 after the lab fix (3ec3b7290). The branch's own lab patch was superseded.


## Cycle 7 (availability: role of RotoWire-listed teams; T07, from NBA) — 2026-11-25, unattended

Scout: `{"target": "availability: cheap RotoWire-listed starters with no history get no starter credit (cycle-6
starter_bump rerun on more graded slates, seed-averaged)", "metric": "held-out cash set20 pctile + $ per $1,
seed-averaged; GPP pctile + player MAE (LOSO) guards", "harness": "scripts/ncaab_lineup_lab.py report --n 20;
scripts/ncaab_lab.py report", "split": "LOSO over boards with box + contest; 11/2-11/4 apart (old blend)",
"money_at_stake": "cash +37% [-42,+116] (17 entries/12 slates, +$22); GPP -37% (12/10, -$3)"}`

**Graded first:** boxes for 156355 (11/9), 156551 (11/12), 156662 (11/17), 157015 (11/20, cash only) — committed.
156656 (UTAH/UW) and 157024 not final/found on ESPN.

**Hole (dfs-availability-critic):** on teams with a RotoWire starting five, players NOT in the five keep their full
projection and score under it. Held out, 12 boxed boards, 2,545 rows: not-listed blend 163 rows proj 15.6 / scored
12.1 (-3.5, low on 9 of 11 slates); not-listed salary-path 32 rows -3.4; listed starters blend +0.8 (188), salary
path +3.8 (31); unlisted teams blend -1.2 (698). E.g. Davis-Fleming 11/9 $4,200 proj 18.7 DNP; Clary 11/16 24.3 -> 6.5.

**Fix tested** (`role`, scratch /tmp/critic-ncaab-7/role_variant.py): after LAST_GAME, listed starters +S, listed
team non-starters -B (floor 0), S/B = mean train-fold residual (S +0.9..+1.6, B -2.2..-3.9), refit per LOSO fold.

| held out (set20, 2 seed sets x 20 averaged) | shipped | role |
|---|---|---|
| cash pctile (8 slates / 13 contests) | .334 | .349 (+.015 [-.028, +.058], better 4/8) |
| cash cashed / $ per $1 | 25% / 0.51 | 21% / 0.42 |
| GPP pctile (guard, 7 / 9) | .449 | .461 (+.012 [-.050, +.068]) |
| player MAE pooled | 5.774 | 5.746 (better 6/12 slates) |
| 11/2-11/4 (old blend) cash pctile | .476 | .667 (discounted) |
| cash pts/lineup by slate | | 11/5 +3.9, 11/7 +3.9, 11/9 +7.8, 11/12 +6.1, 11/10 +1.7, 11/16 -10.2, 11/17 -6.0, 11/20 0 |

Seed noise between the two seed sets ~±.005 cash pctile, so the flat result is not optimizer chaos.

**Decision: NOT SHIPPED.** Player-level hole is real and consistent, but cash $ per $1 falls (0.51 -> 0.42, mostly
11/17) and the percentile gain is inside its interval; the optimizer already avoids most of these players in cash.
Cycle 6's starter_bump and this role fix both fail at the money level -> T07 stays null for lineups.
Process note: the critic's prompt omitted the `critique-metrics exemplars` bar (prompt truncated) — next cycle include it.

**Runner-ups:** (1) unlisted teams (698 rows, -1.2) have no role info; 4 builds logged no starters — last game's 20+
min players as a stand-in five, tested together with this fix; (2) two "Christian Jones" (LBS 156660) still crash
ncaab_lab / lineup lab (`KeyError: 'last_min'`) — key by DK id (judge files: Adam); (3) one build per slate, OUT not
logged: a ~20-min-before-lock rebuild logging OUT at 0 would make false outs / lead time measurable.
Possible non-money use: show "not in RotoWire's five" on the page / feed ownership (untested).

Unattended gate (20261125-1250): **crashed** the same way (main's lab, fixed in 3ec3b7290). Nothing shipped by the agent; entry carried by hand 2026-11-25.

### 11/21 cycle 6 (last-game starter step, T07) -- re-gated by hand 2026-11-25: NOT MERGED

The 11/21 run crashed in main's gate (two LBS Christian Joneses; fixed 3ec3b7290), so the
candidate was never judged. Cherry-picked onto main (branch `critique/ncaab-c6-regate`) and
judged with the loop's own `judge()`:

| slates | main MAE | candidate MAE | diff [90%] |
|---|---|---|---|
| 24 (boxes to 11/22) | 5.5466 | 5.5229 | -0.0237 [-0.0342, -0.0129] -> gate **pass** |
| 27 (+3 forward) | 5.5068 | 5.4984 | better on 18 of 26 slates that move |
| **forward only, after 11/21** | 157021 (11/23) 5.063 -> 5.279; 157024 (11/24) 4.737 -> 4.867; 157026 (11/24) 6.020 -> 6.048 | | **0 of 3 better** |

Lineups (ncaab_lineup_lab, 10 contest slates 11/2-11/20, all before the cycle): cash single
pctile .383 -> .470, $ .80 -> 1.07; cash set20 .384 -> .418, $ .74 -> .79; GPP set20 .520 -> .532,
$ .95 -> 1.11 -- but single-cash $ is one slate each way (11/5 +, 11/4 -).

Verdict: held-out over the season it helps; on the only forward nights it hurts, worst on
the two Thanksgiving-tournament slates (neutral sites; "last game" may be a different
event type). 3 forward slates is not enough data either way -> not merged; re-gate the
branch at 5+ forward slates (boxes: ncaab-box.timer fetches them nightly since 11/25).

Also fixed today: boxes for 157021/157024/157026 failed because DK codes MISS, OU, CSU were
not in data/college_teams.json (built 11/21, before those codes appeared): `harness run
college-teams` (279 DK teams, was 269). 156656 (11/14, UTAH/UW) still fails to fetch.


## Cycle 8 (auto: DK season FPPG unused) — 2026-11-25, hand-run

Scout: `{"target": "DK season FPPG unused: no-props projection ignores this season's DK average whenever a Torvik row exists",
"metric": "held-out cash set20 field pctile + $ per $1 on the Double Ups from 11/12 (5 slates / 8 contests), 2+ seed sets; paired cash pts/lineup on every boxed board from 11/12; guards GPP set20 pctile, ncaab_lab all-player MAE LOSO (the gate)",
"harness": "ncaab_lineup_lab VARIANT act ~ a + b*shipped + c*dk_fppg on non-props rows, refit per fold; ncaab_lab gate",
"split": "LOSO over boards from 11/12; forward fit 11/12-11/18, test 11/19-11/24; 11/2-11/10 apart (DK FPPG was last season's)",
"money_at_stake": "cash +37% [-42,+116] (17e/12s, +$22, mostly 11/17); cash 5-7 game slates -69% (7e/5s); GPP -37% (12e/10s, -$3)",
"runner_ups": ["re-land cycle 5's team-code fix (passed the gate 11/13, lost in a rebase)", "GPP build pct p90 -> p50/p70 (NHL c17)", "re-gate critique/ncaab-c6-regate at 5+ forward slates"]}`

Baseline (27 boxed slates, 4,689 rows): LOSO pooled all MAE **5.507**, priced bias +1.15.

**Hole (dfs-model-critic):** from 11/9 DK's FPPG on every board is THIS season's average, but no-props
rows still projected from last season (Torvik carryover + salary line) and one LAST_GAME box: a player
with a Torvik row never read DK's average; one without read it through the "side" carryover as if it
were last season's. Residuals rise with (dk_fppg - proj): -3.6 at -5..-2 (204 rows), +2.95 at +10 or more
(107). Juzenas 11/20: DK 40.7, base 15.7, proj 28.7; field 50% vs our 5%. Switch: the share of
Torvik-joined players whose DK FPPG equals Torvik's last-season DK points (within 0.5) is .84-.96 on
11/2-11/8 and .03-.29 on every slate from 11/9 (all 58 early-seen players changed on 11/9).

**Fix (shipped):** `SEASON_FPPG` in edge/dfs_run_ncaab.py, after LAST_GAME on every non-props row with a DK
FPPG: fp' = max(-1.833 + 0.623 fp + 0.357 dk_fppg, 0), least squares on 2,178 rows / 20 fresh slates
(11/9-11/24; train-only 11/9-11/18 fit (-1.552, 0.711, 0.261)), only when `season_fppg_state` says the
FPPG is this season's (share < .5 among 20+ joined players); `info["season_fppg"]` on the page, with a
warning when off. scripts/ncaab_lab.py (judge file, hand-run cycle) replays it: `shipped(..., season)`,
`fit_season`, `held_out` (LAST_GAME + SEASON_FPPG refit leave-one-slate-out) used by report, gate and
the lineup lab's `shipped`. Parity: the lab's LOSO reproduces the critic's 5.3802 and its full fit
reproduces the shipped constants to 4 decimals.

| held out | n | shipped | + SEASON_FPPG |
|---|---|---|---|
| all-player MAE, LOSO (the gate) | 4,689 rows / 27 slates | 5.507 | **5.380** (19 of 20 fresh slates better; 11/2-11/8 unchanged) |
| 11/12 on | 2,562 / 17 | 5.265 | 5.050 (17 / 17) |
| forward: fit <= 11/18, test 11/19-11/24 | 1,406 / 9 | 5.446 | 5.216 (9 / 9) |
| 11/9-11/11 (1-3 games in DK's average) | 669 / 3 | 5.472 | 5.406 (2 / 3) |
| priced bias, 11/12 on | | +1.26 | +0.84 |
| cash pts / lineup, 17 boxed boards 11/12+ (20 x 2 seed sets) | | 200.1 | **205.1** (+5.0 [+1.8, +8.4]; seed sets +5.6 / +4.5) |
| cash pts / lineup, forward 11/19+ (9 boards) | | 184.5 | 191.0 (+6.5 [+2.3, +10.7]) |
| cash Double Up pctile, 11/12+ (8 contests / 5 slates) | | .288 | .379 (+.091 [-.034, +.227], 3 of 5) |
| cash $ per $1 | | 0.50 | 0.58 (+0.08 [-0.40, +0.64]) |
| GPP set20 pctile, guard (3 slates) | | .336 | .460 (+.124 [+.041, +.207]) |
| 11/9-11/11 cash (2 contests / 3 boards) | | .274 / 183.7 pts | .231 / 180.6 pts (worse) |

Per cash slate pctile: 11/12 +.28, 11/16 +.31, 11/17 -.11, 11/20 -.03, 11/24 +.01. A salary-only
recalibration (cycle-5 style comparator) reached only 5.148 on 11/12+, so the gain is DK's average,
not a level shift. $5K+ rows whose DK average is under half the board (6 held out): MAE 8.26 -> 6.91.

**Decision: SHIPPED.** Player error better on every forward slate and the lineup points interval clear
of 0 on 17 boards; the money percentile is positive but its interval includes 0 (5 cash slates), and
cycle 5's placebo moved cash points up to +4.1 [-0.3, +8.5] -- both seed sets and the forward window agree,
both GPP guards improved. Live build 11/25 (157295, 5 games): fresh (share .065 of 46), 99 rows moved.
Risks: next season the switch fires with 1-3 games in DK's average, where the replay was worse
(11/9-11/11) -- weight by games played before then; c grows with games (.26 on the early fit, .36 full):
refit weekly.

**Runner-ups:** (1) props rows run high at $5K+ (bias +2.10, 424 rows / 17 slates); a per-fold shrink
(~0.84 p + 1.76) cuts props MAE 8.16 -> 7.88 (14 of 17) -- cycle 5's props-only lineup test was null;
(2) since 11/9 the Torvik fingerprint join compares last-season Torvik with this-season FPPG, so transfers
fail it and fall to the no-Torvik path -- match on last season's FPPG; (3) weight DK's average by games
played (above).


## Cycle 9 (auto: optimizer build percentile, cash p25 / GPP p90; L16 from NHL c17) — 2026-11-25, hand-run

Scout: `{"target": "optimizer objective percentile (cash p25 / GPP p90 are unfitted 10/13 priors): fit the build percentile per contest type on replayed lineups",
"metric": "paired by slate, shipped vs arm: actual DK pts per lineup, cash set20 and GPP set20, all 27 boxed boards; money: field pctile, cashed, $ per $1 vs real fields (cash 11 slates / 16 contests, GPP 9 / 11), never pooled; arms cash {25, 50, 70}, GPP {50, 70, 90}; 2+ seed sets x 20; guard: the other mode",
"harness": "build: per-mode pct override for scripts/ncaab_lineup_lab.py (shipped projection = LAST_GAME + SEASON_FPPG refit per fold)",
"split": "arm picked LOSO over 11/2-11/24; forward pick on 11/2-11/14, test 11/15-11/24; 11/2-11/4 apart",
"money_at_stake": "cash +37% [-42,+116] 17e/12s (+$22; no-overlay -47% 11e/11s); GPP -37% 12e/10s (-$3)",
"runner_ups": ["props rows at $5K+ run high (+2.10, 424 rows / 17 slates): retest the per-fold shrink on 27 boards", "Torvik fingerprint join vs this season's FPPG (transfers)", "re-gate critique/ncaab-c6-regate at 5+ forward boxes"]}`

Baseline: cycle 8's model (LOSO all MAE 5.380, 27 slates); player MAE unchanged by construction here.

**Finding (dfs-model-critic): not a hole.** Replayed all 27 boxed boards x 2 seed sets (0-19, 100-119) x 20
lineups per arm (4,320 lineups; sim shared across arms per board and seed, so paired). In college the
build percentile barely changes the lineup: p90 and p25 builds from the same sim share 6.49 of 8 players
(identical 33% of the time), sim means differ -0.05 pts, sim sd +0.53. NHL's p95 cost ~6 proj pts; here
there is nothing to give back (weak correlation: lineup sd 26.1-26.7 whatever the objective).

| cash (shipped p25) | pts/lineup | paired diff [90%, by slate] | better/worse | seed sets A / B | Double Up pctile (16 / 11 slates) | cashed | $ per $1 |
|---|---|---|---|---|---|---|---|
| p25 | 202.3 | -- | -- | -- | .414 | 34% | 0.69 |
| p50 | 202.9 | +0.53 [-0.52, +1.63] | 12 / 12 | +0.37 / +0.69 | .418 | 36% | 0.72 |
| p70 | 202.0 | -0.29 [-2.08, +1.40] | 11 / 13 | -0.32 / -0.26 | .395 | 30% | 0.60 |

| GPP (shipped p90) | paired diff [90%] | better/worse | seed sets A / B | GPP pctile (11 / 9 slates) | cashed | $ per $1 |
|---|---|---|---|---|---|---|
| p50 | +0.16 [-2.03, +2.26] | 15 / 10 | +0.46 / -0.14 | .518 | 19% | 0.47 |
| p70 | -0.66 [-2.05, +0.69] | 12 / 13 | -0.23 / -1.09 | .496 | 18% | 0.47 |
| p90 | -- | -- | -- | .518 | 22% | 0.62 |

LOSO arm choice: cash p50 in 27/27 folds, held out +0.53 [-0.52, +1.63]; GPP p50 22 / p90 5, held out
-1.57 [-3.25, -0.09] (worse than shipped). Forward (pick on 11/2-11/14, test 15 boards 11/15-11/24):
cash p50 trained +1.35, tested -0.13 [-1.38, +1.15] (5 of 15 better); GPP p50 trained +0.50, tested
-0.12. Most of cash p50's edge is 11/2-11/4 (old model, +3.6); from 11/5 +0.14. Noise floor: 20 seeds
give 5.3 distinct p25 lineups per board; the same arm on seed sets A vs B differs by sd 2.9 pts/board.

**Decision: NOT SHIPPED -- CASH_PCT 25 / GPP_PCT 90 kept**, provenance added beside them
(edge/dfs_ncaab_theory.py). Transfer: lesson L16 (NHL c17) added, T34 ncaab null; T35 NBA / T36 NASCAR
to measure (same 90 prior; check the p90-vs-p50 projection gap first).

**Runner-ups:** (1) the props tier: every arm's lineups are simulated ~225 and score 202-203; props players
fill 71% of lineup slots (6,132 / 8,640) and run +3.31 high when picked (board-wide $4K+ props +1.96,
picked blend +1.67; same-salary same-projection look-alikes +2.30 vs +3.03 picked: mostly tier bias, not
selection) -- retest the per-fold props shrink on 27 boards x 2 seed sets (cycle 5's null was 11 boards,
old model); (2) lineup-lab noise: require 2+ seed sets and report the A/B split, or any change under ~2
pts/lineup reads as signal; (3) GPP and cash builds are nearly the same lineup: GPP differentiation must
come from ownership or game correlation in the sim, not the percentile.



## Cycle 10 (availability: players the field rosters that we zero; queued) — 2026-11-27, unattended

Queued target (Adam): "players the field rosters that we zero". No dfs_transfer row matches it exactly
(T15, "DNP games counted as 0", is the nearest). Metric: for each held-out slate (draft group), players
who played but whom the board zeroed (proj < 3, 10+ min) or dropped (on DK's list, not on the board),
with field %Drafted (cash and GPP never pooled) and DK points scored.

**Baseline (26 boxed slates with a log):** 11/2-11/8: zeroed 109 rows / 1,398 pts, field own sums cash
46.6 / GPP 71.4. From 11/9 (17 slates): zeroed 37 rows / 379 pts, field cash 13.6 / GPP 19.4 (Stillwell
11/10 is 9.1 of the cash sum); dropped 15 rows, field 0%.

**Critic (dfs-availability-critic):** the target has closed since DK's FPPG rollover. On the LOSO board,
rows projected under 3 come out at 0.61 projected vs 0.79 scored (1,321 rows), so they are calibrated. A
perfect pick of the zeroed rows worth 4+ pts/$1K was worth 4.4 pts per slate from 11/9 (42.6 before), and
nothing known before lock picks them out. Of the 15 "dropped" rows: 6 were name misses in my baseline
script, 3 were Isaiah Brown (DK never priced him), and 2 were real OUT drops (Robinson 11/10, Shinholster
11/20, field 0%). The biggest money hole it found runs the other way, false actives after the one build:
- 7 of our lineup slots (4 cash, 3 GPP) went to players who then played 0 minutes.
- 5 of those 7 were in games that tipped 0.8-5.5 h after lock. NCAAB has no late swap.
- Fland 11/19 and Gaskins 11/26 had their props pulled before tip while teammates stayed quoted. A perfect
  late swap would have recovered 52 cash / 38.5 GPP pts over 24 slates.
- From 11/13, among $4K+ rows, players whose props were pulled played 0 minutes 5 of 24 times, against
  4 of 677 when props stayed up.

**Shipped (the critic's runner-up 1, which is on target):** the name join behind LAST_GAME and the grader.
`edge/ncaab_names._same_person` refused ESPN's one-letter slips:

| ESPN box | DK | effect |
|---|---|---|
| Emil Skytt | Emil Skytta | read as a last-game DNP |
| Gantas Krianauskas | Gantas Krizanauskas | ž dropped, not folded |
| Amal L'Etang | Amael L'Etang | read as a last-game DNP |

`last_game_lines` gives an unresolved player 0 minutes, so LAST_GAME cut him. On 11/14 (156656) L'Etang,
a $7,300 starter owned by 26.1% of the cash field and 23.2% of the GPP field, was projected 7.57 from a
base of 25.0. He scored 39.25. The grader also scored him 0 on 11/20 and 11/22, when he played 22 and 28
minutes. DK lists "Q Williams IV" (C-C) by an initial, and the bare-initial rule refused even DK's own text.

The fix is a last tier, "typo": exactly one name part differs, by one edit, the slipped part has 4+
letters and keeps its first letter, and only one teammate fits. Exact, suffix, nickname and partial still
win. A source name equal to DK's own initial name joins as exact; "Q. Williams" is still refused. The
partial/nickname checks no longer return early before this tier. Tests are in tests/test_ncaab_names.py
(twins one slip apart are refused, and so are Ian/Ivan and Lee/Lea).

Audit, old resolver vs new, nothing chosen on held-out data:

| check | n | result |
|---|---|---|
| lab load (last games + grader), all slates | 31 slates | 4 new joins, all right (above + Q Williams IV) |
| live last-game lines, every logged board | 32 boards | 5 player-slates change: L'Etang x3, Skytta, Krizanauskas |
| book prop subjects (odds.db) vs every logged team | 474 names | 1 new join: "Efeosa Oliigu-Elabor" -> DK "Oliogu-Elabor" (GMU), right |
| Torvik join on logged rows | 32 boards | unchanged |
| box rows with minutes that look like a logged player graded 0 | | 5 -> 0 |

The board replay: L'Etang 11/14 goes 7.57 -> 18.55. The other 16 Dayton rows replay within 0.1 of the
log, which checks the replay against production. Skytta 11/16 goes 6.45 -> 11.45 in the lab (he scored 14.5).
The live board had left him at 13.4 because PORST's box wasn't up yet. L'Etang 11/20 and 11/22 were props
rows: projection unchanged, truth corrected.

| gate rows (ncaab_lab gate, LOSO) | rows / slates | main | branch |
|---|---|---|---|
| all-player MAE, main truth vs branch truth | 5,530 / 31 | 5.3809 | 5.3774 |
| board only (both on corrected truth) | 5,530 / 31 | 5.3788 | 5.3774 |

**Decision: SHIPPED as a correctness fix** (the name-suspect rule). It is not a money claim. The one money
case, L'Etang 11/14, has no box (156656 still fails to fetch), so the lineup lab cannot replay it.
**Note for the gate:** the grader uses the same resolver, so main and the branch grade 4 rows differently
(0 -> 7.5 / 20.5 / 14.5 / 8.75). The branch's MAE is judged partly on its own corrected truth.
**Conservative calls (unattended):**
- The props-pulled tag and the late-swap alert were not built. They are a new feed plus a monitor, with 5
  true scratches and under 5 test slates with a hit.
- No lineup replay: the fix moves one replayable row.

**Runner-ups:**
1. NCAAB late-scratch path (critic's hole above). Tag a player whose props were pulled while a teammate
   stays quoted, alert after lock until his game tips, and log OUT-dropped players at 0 with a timestamp
   on each poll. Fit the gap G and the multiplier P on slates up to 11/18.
2. A Q tag plus a last-game DNP sat again 15 of 29 times, but props rows keep full projection (Robert
   Wright III 11/23, $10.3K, 32.7, 0 min). None has landed in our lineups yet.
3. 156656's box (11/14, UTAH/UW) still fails to fetch, and it is the one slate that would price this fix.

Unattended gate (20261127-1511): **review** -- waiting for Adam: harness critique-loop --merge / --drop

| split | state | n | clusters | main MAE | candidate MAE | loss diff [90%] | verdict |
|---|---|---|---|---|---|---|---|
| primary | all | 5,530 | 31 | 5.3809 | 5.3827 | +0.0017 [+0.0008, +0.0035] | worse |
- 5 rows changed truth / cluster / split / state
- primary all: worse, point estimate worse (+0.0017 [+0.0008, +0.0035], 31 clusters)
- no primary state better beyond noise
- the cycle aimed at 'availability: players the field rosters that we zero', which this gate does not measure (dk points, minutes, projection); gate said fail: Adam decides on the cycle's held-out numbers
