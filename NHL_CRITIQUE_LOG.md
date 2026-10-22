# NHL skater projection: critique loop

Started 2026-10-22 (`/dfs-critique nhl`). Each cycle a fresh, independent critic agent
(`.claude/agents/dfs-model-critic.md`) finds the single biggest hole in the skater
projection. The hole is fixed as a variant in `scripts/nhl_skater_lab.py`, fitted on
the TRAIN nights only, and shipped to `edge/dfs_run_nhl.py` / `edge/nhl_sim.py` only if
the TEST nights improve in both information states. Every cycle is recorded here with
the standard table (`python3 scripts/nhl_skater_lab.py report`).

**Target:** skater DK points (box score: goals, assists, shots, blocks + bonuses) against
the pre-lock simulated mean. Goalies are reported on one line: their points barely beat
a constant (NHL_STATUS.md 6e).

**Protocol:** every logged slate (data/dfs_proj_log_nhl.csv) is rebuilt from pinned
inputs (scripts/nhl_rebuild.py: draftables + DailyFaceoff snapshot from git, odds from
the last dfs_nhl scan before the cut) in two states, `lock` (inputs at lock) and `early`
(lock − 3 h, the afternoon phone build). Each (date, team, player) is graded once, from
the latest-locking slate that holds him; scratched board players are counted, not
graded as 0. TRAIN = nights before 2026-10-13 (11), TEST = 10/13 on (8). 10/15 has no
draftables snapshot and is missing. Parity: the lab's re-simulated shipped projection
matches build_board to 0.005 pts (the log's rounding).

## Cycle 0: the starting point (as shipped 2026-10-22, `d6c126a4`)

| state | split | nights | skater-games | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|---|---|
| lock | train | 11 | 2,187 | 4.592 | 6.107 | +0.151 | +0.355 |
| lock | test | 8 | 1,805 | 4.509 | 5.991 | +0.139 | +0.353 |
| early | train | 11 | 2,167 | 4.597 | 6.119 | +0.136 | +0.359 |
| early | test | 8 | 1,783 | 4.510 | 6.003 | +0.121 | +0.342 |

Scratched board skaters (team played, not in the box): 70 at lock, 108 early.
Goalies, test: MAE 7.62 vs a train-mean constant 7.68.

Test slices at lock, MAE (bias): C 4.89 (+0.12) · W 4.83 (+0.18) · D 3.84 (+0.11) ·
props 5.68 (+0.04) · no props 3.64 (+0.21) · F1 6.45 (−0.12) · F2 5.12 (+0.24) ·
F3 4.23 (+0.16) · F4 3.50 (+0.42) · D1 4.45 (+0.06) · D2 3.89 (−0.37) · D3 3.16 (+0.63) ·
PP1 5.79 (+0.21) · salary 3.5-5k 4.52 (+0.57) · 5-6.5k 5.33 (+0.40) · 6.5k+ 7.67 (−0.41).

## Cycle 1: nothing from this season reached the skater means

**Critic #1 (fresh `dfs-model-critic`, ~13 min, read-only).** "Every mean not set by a
market -- all four means of the 57% of skaters without props, and blocks for every
skater -- is last season's per-game rate, so a skater whose ice time has fallen or
risen this season keeps last year's output."

- Test error binned by log(last-5 TOI / last-season TOI) runs −1.2 to +1.2 pts in
  order for no-prop skaters, the same direction on TRAIN.
- Most of the gain is the no-prop goals / assists / shots scaling (alone: lock test
  RMSE 5.968); blocks alone 5.987.
- Of everything it tried on TRAIN, the largest held-out gain: form −0.006 RMSE, DK FPPG
  −0.004, salary −0.002, line/PP dummies +0.010 (worse), prop devig recalibration
  +0.020 (worse), assist-draw fix −0.002.
- Observed test MSE (35.9) is below the simulator's own mean variance (37.9): the mean
  error is close to the noise floor, so gains will be small from here.

**Fix.** r = (K·toi_last + n·toi_cur) / (K + n) / toi_last, toi_cur = mean of the last
LAST games this season (NHL stats API game-level ice-time report, games dated before
the slate), n = games this season; no-prop goals / assists / shots × r^B, every
skater's blocks × r^B_B; rookies and skaters yet to play unchanged. Production:
`edge/dfs_run_nhl.role_ratio` / `scale_role` / `group_toi`, `edge/nhl.skater_game_toi`;
the lab variant `toi_role` calls the same functions.

**Re-decided on TRAIN** (lock, RMSE): the critic's grid stopped at power 1.5 (its edge).
Grid K {3,5,10,20} × B {0.5..2.5} × B_B {0.5..2.5} × LAST {5,10}, then B_B {3,3.5,4}:
K 5, LAST 5, **B 2.0, B_B 2.5** (interior), train RMSE 6.107 -> 6.093. Judgment call
recorded: the surface is flat (most cells 6.093-6.100), so the powers are loosely
determined; the argmin was taken as the rule says. Joined by name + D/F (the critic's
team + initial join lost traded players' games).

**Decision: SHIPPED.** Better held out in both states on MAE, RMSE and rank.

| state | split | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|
| lock | train, before -> after | 4.592 -> 4.580 | 6.107 -> 6.093 | +0.151 -> +0.131 | +0.355 -> +0.365 |
| lock | **test**, before -> after | 4.509 -> **4.470** | 5.991 -> **5.962** | +0.139 -> +0.137 | +0.353 -> **+0.375** |
| early | train, before -> after | 4.597 -> 4.586 | 6.119 -> 6.102 | +0.136 -> +0.129 | +0.359 -> +0.372 |
| early | **test**, before -> after | 4.510 -> **4.478** | 6.003 -> **5.981** | +0.121 -> +0.125 | +0.342 -> **+0.368** |

Test squared error per night (night bootstrap): lock −0.60 [−1.24, −0.05], better 6/8;
early −0.57 [−1.11, −0.10], 5/8. Mean |change| 0.40 pts, max 3.7. Slices (lock test
MAE, bias): F4 3.50 -> 3.35 (+0.42 -> +0.28), D3 3.16 -> 3.04 (+0.63 -> +0.26), no props
3.64 -> 3.57; props 5.68 -> 5.68; 6.5k+ 7.67 -> 7.67 (−0.41 -> −0.38). Parity: production
build_board == lab variant to 0.005 pts on 3 rebuilt slates (85-87% of each board scaled);
the lab cache re-built through the shipped code reproduces the "after" rows exactly.

**Runners-up (critic):**
- MAE can be gamed: ×0.8 on every projection cuts test MAE 4.509 -> 4.324 while RMSE
  worsens (6.126). Judge cycles on RMSE + bias + rank, MAE only alongside.
- The assist draw under-feeds stars: 7.5k+ skaters get 0.72-0.74 assist means in and
  0.65-0.68 out (two draws treated as independent, sampled without replacement);
  7.5k+ under-projected by 1.25 (train, n 69) / 1.53 (test, n 72). Fixing the draw
  alone did not move test RMSE.
- Silent name-join misses ("Tommy" / "Thomas" Novak, "Jack" / "John (Jack)" Roslovic)
  drop players to default rates; 310 graded rows have no last-season rates.

## Cycle 2: last season's rates silently dropped 34 skaters' stats

**Critic #2 (fresh `dfs-model-critic`, ~17 min, read-only).** "Last season's per-game
rates come from two NHL stats-API reports fetched 100 rows at a time with no sort key,
so the pages overlap and drop players: 43 board skaters carry wrong rates, many of them
expensive."

- The cached 2025-26 reports: 940 rows, 929 (summary) / 917 (realtime) distinct players;
  the API returns 940 distinct in one `limit=-1` call (checked).
- `_season_rates` read a missing field as `or 0`: 27 board skaters had blocks 0.0
  (Dahlin, Robertson, Chiarot, Schmidt, Buium), 10 had goals/assists/shots 0.0 (Fox,
  Coyle, Teravainen, Chatfield), 6 none at all. The key existed, so the board default
  and the `imputed` flag never fired. The live app read the same cache.
- Affected rows under-projected by 1.31 (train, n 103) / 0.90 (test, n 84) pts.

**Fix (no parameters).** `edge/nhl.season_skater_stats`: one `limit=-1` request per
report sorted by playerId; refused unless one row per player and the API's total; a
cache with repeated players refetches itself (Cloud too, after a reboot).
`edge/dfs_run_nhl._season_rates`: a field a report lacks stays missing. Lab cache rebuilt
through the fixed code (before = the post-cycle-1 cache). Tests: duplicate cache refetch,
short fetch refused, missing field stays missing.

**Decision: SHIPPED** (a correctness fix): RMSE and rank better on test in both states,
at 1 seed and 4; MAE flat; bias +0.04 worse (see below).

| state | split | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|
| lock (4 seeds) | train, before -> after | 4.581 -> 4.581 | 6.099 -> 6.090 | +0.124 -> +0.164 | +0.366 -> +0.367 |
| lock (4 seeds) | **test**, before -> after | 4.469 -> 4.469 | 5.959 -> **5.946** | +0.135 -> +0.174 | +0.376 -> **+0.385** |
| early (4 seeds) | train, before -> after | 4.589 -> 4.590 | 6.110 -> 6.103 | +0.115 -> +0.154 | +0.370 -> +0.371 |
| early (4 seeds) | **test**, before -> after | 4.474 -> 4.475 | 5.975 -> **5.961** | +0.124 -> +0.160 | +0.370 -> **+0.381** |
| lock (1 seed, production) | test | 4.470 -> 4.477 | 5.962 -> 5.955 | +0.137 -> +0.186 | +0.375 -> +0.381 |

Test squared error per night (4 seeds): lock −0.34 [−0.86, +0.04], 6/8 nights; early
−0.32 [−0.85, +0.07], 5/8. Skaters moved > 0.25 pts: test n 113, bias −0.73 -> −0.08,
MSE 53.3 -> 50.7 (lock). Their under-projection was offsetting a general
over-projection, now about +0.17 per skater: a candidate for the next cycle.

**Evaluation change (critic's runner-up, adopted).** One seed's Monte Carlo noise moves
train RMSE by ~0.01 (seeds 0-3: 6.093-6.104), as large as cycle 1's whole grid
surface. `report --seeds 4` averages seeds and prints per-night paired differences
(`paired`); decisions from here are taken at 4 seeds. **Cycle 1 re-checked** at 4 seeds
on the corrected rates (scaling undone by 1/r): test squared error −0.58 [−1.13, −0.09]
per night at lock (6/8), −0.56 [−1.08, −0.10] early (5/8): it stands.

**Runners-up (critic):**
- The 7-D blocks scale doesn't hold this season: D on flagged 7-D teams projected
  1.08-1.18 blocks, blocked 1.38-1.57 (n 129, both splits).
- Points props are compressed: actual points rise ~1.3x as fast as the market's
  (s.e. ~0.13), all four split/state cells, mostly assists (1.35-1.45x); stretching it
  on TRAIN moved nothing yet.
- The box score writes "J.Kulich" without a space, so his games count as scratches,
  not graded (lab `box_key`).

## Runner-ups of cycles 1-2, implemented (2026-10-22, at Adam's request)

Fresh 4-seed baseline after fixing the lab's grading (`box_key`: 'J.Kulich' /
'D.Jiricek', written without a space, were counted as scratches; scratches at lock
70 -> 59): lock test RMSE 5.938, early 5.954. Each candidate fitted on TRAIN lock at
4 seeds (`scratchpad ru/ru.py`), then TEST in both states.

| candidate | TRAIN fit | lock test RMSE | early test RMSE | test per night (lock) | decision |
|---|---|---|---|---|---|
| 7-D blocks factor | 0.855 -> 0.95 (train RMSE −0.0002, flat 0.855-1.05) | 5.938 -> 5.938 | 5.954 -> 5.953 | +0.047 [−0.018, +0.141], 3/8 | **not shipped** |
| points stretch alone | power 1.3 (6.0857 -> 6.0816) | 5.938 -> 5.927 | 5.954 -> 5.941 | −0.04 [−0.63, +0.46], 3/8; train per night worse 7/11 | superseded |
| exact assist draw | no parameter (6.0857 -> 6.0816) | 5.938 -> 5.936 | 5.954 -> 5.953 | +0.004, 5/8 | **shipped** (correctness) |
| exact draw + stretch | power 1.1 given the draw (6.0803) | 5.938 -> 5.929 | 5.954 -> 5.946 | −0.036 [−0.38, +0.26], 5/8 | **shipped** |
| nickname name joins | none (5 skaters, 21 games) | in the row below | | | **shipped** (correctness) |

- **The assist draw** was confirmed on a 12-game slate at 20,000 sims: $7.5k+ given 0.706
  simulated 0.656 under the old solve, 0.690 under the exact one. The 0.5-4k tier was
  0.218 -> 0.221 before, 0.216 after. A uniform ~4.5% shortfall remains, because a
  twice-clashing second assist is dropped. It is left alone: fixing it adds ~0.06 pts per
  skater to a model already +0.18 high.
- **The 7-D blocks gap** (flagged D proj 1.17 vs 1.38 actual on TRAIN, against 1.34 vs
  1.44 unflagged) is half the size on TRAIN that it is on TEST. Choosing the factor on
  TEST would be fitting on held-out data.
- **Nickname joins** go through this season's NHL game rows: a unique (team, first
  initial, last name), and the NHL name must have last-season rates. Of 66 board
  skaters without rates, 5 were spellings (Nicholas/Nick Robertson, Zack/Zachary
  Bolduc, Will/William Borgen, Joe/Joseph Veleno, Jeffrey/Jeff Viel). The rest are
  rookies, or Barkov, who has no 2025-26 season. The Novak / Roslovic misses the cycle-1
  critic named were the paging bug fixed in cycle 2.

**Shipped together** (lab re-cached through the shipped code; before = the post-cycle-2
cache with the old solve), 4 seeds:

| state | split | MAE | RMSE | bias | rank corr | $7.5k+ bias |
|---|---|---|---|---|---|---|
| lock | train, before -> after | 4.578 -> 4.571 | 6.086 -> 6.078 | +0.164 -> +0.162 | +0.367 -> +0.366 | −1.27 -> −0.67 |
| lock | **test**, before -> after | 4.461 -> **4.449** | 5.938 -> **5.929** | +0.181 -> +0.181 | +0.386 -> +0.387 | −1.51 -> **−0.91** |
| early | train, before -> after | 4.587 -> 4.581 | 6.098 -> 6.092 | +0.155 -> +0.154 | +0.371 -> +0.370 | −1.36 -> −0.75 |
| early | **test**, before -> after | 4.467 -> **4.455** | 5.954 -> **5.945** | +0.167 -> +0.168 | +0.381 -> +0.382 | −1.47 -> **−0.89** |

Pooled test squared error −0.118 [−0.273, +0.101] at lock, −0.103 [−0.253, +0.113]
early; per night better on 5/8 nights in both states. The gain concentrates on the
stars rather than spreading across nights. Nickname re-joins: bias −2.64 -> −1.96,
MSE 55.3 -> 51.9 (n 21). Parity: production == lab variants to 0.005 on 3 slates (the
lab's reference level rounded to production's 0.556).

## The dropped second assist, fixed (2026-10-22, at Adam's request)

Left open after the runner-ups: simulate() redrew a clashing second assister once and
dropped the assist on a second clash. Fix (`edge/nhl_sim.simulate` + `_assist_matrix`):
the second assister is drawn from the scorer's OTHER teammates, renormalised, and the
weight solve uses P(j second | i scored) = q_ij · Σ_{k≠j} q_ik / (1 − q_ik). No
parameters.

**Measured on the real simulator** (10/20, 12 games, 20,000 sims): assists per goal 1.666
-> **1.692** (fitted 1.692). Simulated / target by salary: <4k 0.986 -> 1.003, 4-6.5k
0.984 -> 0.998, 6.5-7.5k 0.988 -> 1.005, 7.5k+ 0.975 -> 0.996. Toy team (test): the
original solve gave 1.598 per goal with the star at 82% of target, this morning's
1.575 / 93%, now 1.692 / 100%. The new test runs simulate() end to end and fails on both.

**Held out** (same cached means, 4 seeds; before = the simulator as of `83a1a7ba`):

| state | split | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|
| lock | train, before -> after | 4.571 -> 4.576 | 6.078 -> 6.080 | +0.162 -> +0.185 | +0.366 -> +0.365 |
| lock | **test**, before -> after | 4.449 -> 4.453 | 5.929 -> **5.928** | +0.181 -> +0.203 | +0.387 -> +0.388 |
| early | train, before -> after | 4.581 -> 4.582 | 6.092 -> 6.087 | +0.154 -> +0.178 | +0.370 -> +0.370 |
| early | **test**, before -> after | 4.455 -> 4.458 | 5.945 -> **5.943** | +0.168 -> +0.192 | +0.382 -> +0.382 |

Per night: lock test +0.081 [−0.013, +0.185] (better 2/8), early test +0.015 (6/8);
pooled test dMSE −0.006 [−0.050, +0.105] lock, −0.027 [−0.074, +0.045] early.
Projections rise +0.023 per skater on average. Stars' bias −0.91 -> −0.86, PP1 +0.44 ->
+0.47.

Parity: lab re-cached through the shipped code, lab == production to 0.005 on all 5,366 board rows.

**Decision: SHIPPED** as a correctness fix. The simulator now produces the assist rate
it was fitted to; RMSE is marginally better in both states, MAE marginally worse. It
adds ~0.02 to an over-projection that is now **+0.18 to +0.20 per skater**. That bias
is the next cycle's natural target: it is spread across PP1 (+0.44 test) and mid-salary
skaters (3.5-6.5k +0.4-0.6 in Cycle 0), and does not live in the assist draw.
