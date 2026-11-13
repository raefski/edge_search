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

## Cycle 3: no mean hole the test nights can see -- nothing shipped

Baseline (4 seeds, after the assist fix): lock test MAE 4.453, RMSE 5.928, bias +0.203,
rank +0.388; early 4.458 / 5.943 / +0.192 / +0.382.

**Critic #3 (fresh `dfs-model-critic`, ~18 min, read-only).** "After cycles 0-2, no
information we hold locally moves held-out skater error by more than ~0.006 RMSE. The
real hole is that 8 test nights resolve only ~±0.02 RMSE, so further mean tuning on
them chooses among noise."

- Realised error matches the simulator's own spread: test MSE 35.2 vs mean simulated
  variance 38.3 (0.92), train 37.0 vs 38.0 (0.97); every slice 0.78-1.03 except
  $6.5-7.5k 1.20 (n 190). 2025-26 within-player variance is 37.7 pts², so even each
  skater's true season mean would leave more error than the model has now.
- No persistent player / team miss: a player's TRAIN mean miss predicts nothing on TEST
  (corr −0.02, 566 players; team −0.22). Only blocks carry over (player +0.14, team
  +0.22), the one component with no market.
- The books are calibrated: shots-line over rates within 0.01-0.04 of the devigged
  price at 1.5 / 2.5 / 3.5; points 0.5 line 0.446 / 0.473 hit vs 0.471 / 0.475 priced.
- Best TEST gains of every probe it fitted on TRAIN: linear recalibration −0.074 pts²
  (changes no lineup), salary-tier bias ≤ −0.07, this season's no-prop shots −0.017,
  this season's blocks + rink −0.016, shots power on implied goals ~−0.005, scratches
  as 0 with a dress probability −0.004 to −0.008 RMSE.

**Its best variant, `blocks_now`** (this season's blocks blended at K = 10 games, rink
scorer power E = 1, TRAIN grid 6.076-6.084 across all cells), re-run here:

| state | split | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|
| lock | train, shipped -> blocks_now | 4.576 -> 4.575 | 6.080 -> 6.077 | +0.185 -> +0.187 | +0.365 -> +0.368 |
| lock | test, shipped -> blocks_now | 4.453 -> 4.463 | 5.928 -> **5.936** | +0.203 -> +0.219 | +0.388 -> +0.393 |
| early | test, shipped -> blocks_now | 4.458 -> | 5.943 -> **5.951** | | |

Per night: train −0.133 [−0.266, −0.002] (8/11), test lock −0.046 [−0.270, +0.203]
(5/8), test early +0.010 [−0.22, +0.28]. **Not shipped**: pooled test RMSE worse in
both states.

**Production Monte Carlo (critic's runner-up), tested here: not shipped.** One seed of
8,000 sims vs 2,000 (as production): lock test RMSE 5.9350 -> 5.9330 (−0.149 per night
[−0.298, +0.001], 5/8), but early test 5.9404 -> 5.9437 (−0.054, 3/8 nights) and early
train 6.0850 -> 6.0906. Mixed held out, and the three lineups on a 448-player board take
184 s instead of 80 s (simulate 0.6 -> 1.6 s; the optimizers scale with sims).

**Rule adopted for NHL mean changes from here (the critic's fix).** The 8 test nights
cannot certify a change under ~0.02 RMSE. A change to a component no market prices
(blocks; no-prop goals / assists / shots; role scaling; rink) should first be validated
on the 2025-26 box scores (~40k skater-games, every input rebuildable as of each game
date: prior-season rates, rolling ice time, rink factors from other games). Fit on the
first half, check on the second, ship only if that interval excludes zero and the lab's
TEST is not worse. That harness is not built yet: it is the next cycle's work.

**Runners-up (critic):**
- Each team's assists are renormalised to implied goals × 1.69; scaling the input assist
  means to that total instead is better in all four split/state cells, by 0.014-0.040
  pts². It needs a per-team assists-per-goal change inside simulate().
- Mid-salary skaters ($3.5-6.5k, mostly with props) are over-projected by +0.35 to +0.93,
  and $7.5k+ under by −0.58 to −0.83, in both splits. A correction is worth ≤ 0.07 pts²
  on TEST.
- The simulator's spread runs 3-8% wider than realised errors, assuming the mean is
  right. That feeds the cash 25th / GPP 90th percentiles directly, and is the critic's
  suggested next focus (a distribution metric, not the mean).

## Cycle 4: the 2025-26 season harness (built first), then a critic

**Harness: `scripts/nhl_season_lab.py`** (cycle 3's rule needed it). Each 2025-26 game is
replayed as of the morning of its date through production's own recipe and simulator:
- the box score's dressed skaters (7+ D → the 7-D scale);
- 2024-25 per-game rates by NHL id (`edge/dfs_run_nhl.rate_row`, as `_season_rates`);
- this season's ice time from earlier box scores (the cycle-1 role scaling);
- `edge/dfs_run_nhl.skater_means` with no props, the refactored recipe build_board now
  calls, unchanged: means, flags and projections identical on 3 rebuilt slates;
- `edge/nhl_sim.simulate`, one call per date.

No odds exist before 2026-09-28, so team goals are a form estimate: goals for × the
opponent's goals against so far, shrunk to the league's to-date rate (TEAM_K 10,
LEAGUE_K 50, prior 3.04 from 2024-25). Every skater runs the no-prop path, so this lab
judges that path (57% of a 2026-27 board) and blocks (everyone); stars' goals and shots
come from props in production. H1 = dates before 2026-01-01 (fit), H2 = after (check);
the date is the bootstrap unit.

Baseline, 4 seeds:

| half | dates | skater-games | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|---|
| H1 | 81 | 22,716 | 4.545 | 6.0957 | +0.187 | +0.3827 |
| H2 | 86 | 24,515 | 4.621 | 6.2622 | +0.300 | +0.3933 |

**Cycle 1 checked out of season** (1 seed): role scaling off (`no_role`) vs shipped,
squared error H1 +0.092 [−0.102, +0.290], H2 +0.183 [−0.139, +0.507]; rank corr
+0.366 / +0.364 without it, +0.382 / +0.393 with it. Same direction as the 2026-27 slates.

**Critic #4 (fresh `dfs-model-critic`, ~18 min, read-only).** "Beyond ice time, the skater
projection never uses what a player has produced this season: every mean no market sets
is last season's rate (a rookie runs on flat defaults all year)." Shipped blocks ran
0.853 a game vs 0.765 blocked in H2; skaters with no 2024-25 rates −0.83 bias.

**Fix.** `edge/dfs_run_nhl.blend_season`, inside `skater_means` after the role scaling
and before the 7-D scale. m = w·m + (1−w)·this season's per-game rate, w = K / (K + games
this season); goals, assists and shots are × scale and blend only where last season set
them, blocks always and unscaled. Live: `edge/nhl.skater_season_to_date`, the stats
API's summary + realtime reports aggregated to games before the date (two requests;
matched the box scores for 676 of 677 skaters). Joined like the ice time; the page warns
if it is unreachable. K **points 30, shots 7, blocks 20**, fitted on season-lab H1 (grid
re-run here: argmin 6.0317, flat 6.032-6.035 near it).

**Decision: SHIPPED.** Season lab (4 seeds), recipe before vs after:

| half | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|
| H1 (fit) | 4.545 -> 4.481 | 6.0957 -> 6.0300 | +0.187 -> +0.119 | +0.383 -> +0.402 |
| **H2 (check)** | 4.621 -> **4.508** | 6.2622 -> **6.1190** | +0.300 -> **+0.150** | +0.393 -> **+0.423** |

H2 squared error −1.77 [−2.01, −1.53] per skater-game, better on 78/86 dates. The gain
grows with games played (critic: −0.12 in a player's first 10 games, −1.2 past 40).

2026-27 lab (re-cached through the shipped code, 4 seeds), the rule's "not worse":

| state | split | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|
| lock | test, before -> after | 4.453 -> 4.449 | 5.928 -> 5.926 | +0.203 -> +0.183 | +0.388 -> +0.396 |
| early | test, before -> after | 4.458 -> 4.451 | 5.943 -> 5.939 | +0.192 -> +0.173 | +0.382 -> +0.387 |

Per night lock −0.087 [−0.42, +0.26] (4/8), early +0.011 [−0.32, +0.46] (5/8): flat this
early in the season (test nights fall at games 4-12; stars' goals and shots are props).

**Risks / runners-up (critic):**
- The season lab's team goals are a form estimate, so part of the gain may be
  this-season rates standing in for team strength the market already gives production.
  Goals and assists are renormalised within each team; shots and blocks are not.
- K_shots = 7 is aggressive; recheck on the 2026-27 lab at ~30 nights.
- Shots scale one-for-one with implied goals; ~0.25 of that fits H1 better. It interacts
  with the market, so only the 2026-27 lab can judge it.

## Cycle 5: stars' props dropped when the book spells them differently

**The hole** (from a fresh critic). `own_game_markets` keyed props by the book's
subject, and `SalaryLookup` resolved neither tags nor nicknames. So DK-book props for
"Mitchell Marner", "Sebastian Aho (CAR)", "Elias Pettersson (F)", "Christopher Tanev",
"Cameron York" and "Alexander Wennberg" never reached `build_board`
(`props.get(norm(DK name))`). They were 23% of the $5k+ no-prop skaters. The critic
found nothing larger in the no-prop path: the season lab's components match the box
scores within 0.06 a game.

**The fix.** A join correction with no parameters.
- `SalaryLookup._alias`: a (TEAM) tag must match the player's team, and an (F)/(D)
  tag his position.
- `_nick`: same surname and one first name a prefix of the other (3+ letters), with
  exactly one candidate. Mike / Malik Williams is refused.
- `own_game_markets(key_of=lookup.key_of)` keys props by the salaries key, and NHL
  `build_board` reads `props[salaries key]`.
- Tests: `tests/test_dfs_slate_guard.py`.
- Every sport that uses `SalaryLookup` gets the alias rules: NFL, MLB, NCAAB.

**Result** (2026-27 lab re-cached through production code, nights as defined in the
lab):

| state | split | before RMSE (n) | **after RMSE (n)** | MAE | bias |
|---|---|---|---|---|---|
| lock | train | 6.077 (2,192) | 6.079 (2,196) | 4.570 → 4.572 | +0.18 |
| lock | test | 5.926 (1,811) | **5.919** (1,814) | 4.449 → 4.446 | +0.19 |
| early | train | 6.088 (2,172) | 6.090 (2,176) | 4.577 → 4.578 | +0.17 |
| early | test | 5.939 (1,789) | **5.932** (1,792) | 4.451 → 4.446 | +0.17 |

- 41 lock skater-slates gained their props (42 early).
- Namesakes landed correctly: Aho on CAR C, Pettersson on VAN C.
- A few skaters newly enter the scored rows (+3/+4): players with no line but with
  props were dropped before.
- The season lab has no props, so H1/H2 are unchanged at 6.0300 / 6.1190.

**Decision: ship.** It is a correctness fix on the highest-salary players. Test
improves a little in both states and train is flat. The gain is inside the 8-night
resolution (±0.02), and I say so.

**Runner-ups:**
- **Games with no props in the lock scan** (10/06 VGK-SEA, 10/14 COL-DAL) run on
  rates. Use the latest earlier scan that has props: about 20 star skater-games so
  far, untested.
- **Blocks drift down across the season** (0.822 a game in H1 vs 0.755 in H2): a
  drift term is worth about 0.01 pts².
- **Test bias for PP1 (+0.50) and $3.5-6.5k (+0.5-0.6):** worth 0.07 pts² or less
  (cycle 3).

## Cycle 6 (target: confirmed lines + PP units, late scratches/promotions) -- nothing shipped

Run 2026-10-27. Lab re-cached through 10/27 and 2026-27 box scores fetched through
10/26, so TEST is now 13 nights (10/13-10/26). Metric: skater DK points vs the pre-lock
mean, both states. The targeted slices are skaters whose line or PP unit changed,
teammates of late scratches, and promotions.

**Baseline** (shipped, 4 seeds):

| state | split | nights | skater-games | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|---|---|
| lock | train | 11 | 2,196 | 4.568 | 6.076 | +0.172 | +0.367 |
| lock | test | 13 | 2,962 | 4.478 | 5.970 | +0.173 | +0.404 |
| early | train | 11 | 2,176 | 4.577 | 6.094 | +0.158 | +0.371 |
| early | test | 13 | 2,923 | 4.490 | 5.995 | +0.164 | +0.397 |

Scratched board skaters, not graded: 82 at lock, 133 early. PP1 test bias at lock is +0.38.

**The critic's finding.** Tonight's DailyFaceoff line and PP unit reach the simulation
only through `LINE_AFFINITY` / `PP_AFFINITY` in `_assist_matrix`, which is renormalised
to each skater's targets. So a slot change before lock barely moves the means: early to
lock moves them by -0.09 to +0.09 pts. The data shows:

- **Teammates of late scratches** are not under-projected (bias +0.14 to +0.42 with 0-2
  scratches on the team, no trend that could be fitted).
- **Line promotions between early and lock** are under-projected: -3.2 on train (n=23)
  and -1.4 on test (n=36). Not enough data, and demotions flip sign between the halves.
- **Skaters dropped a PP unit since their last board** run hot: actual/projected was
  0.945 on train (n=40) and 0.775 on test (n=120). The no-prop PP2->none subset fits the
  wrong sign on train (1.10), and fitting it makes test worse (5.970 -> 5.975).
- **Line x PP x props multipliers fitted on train** overfit: lock test RMSE 6.045
  unshrunk, 5.973 with shrinkage.

**Variant tried: `pp_demote`.** It multiplies goals, assists and shots by 0.945 (fitted
on train lock as sum(actual)/sum(proj), n=40) and was applied post hoc without
re-simulating. Lock test RMSE went 5.9699 -> 5.9669 and early 5.9945 -> 5.9918, better
on 9 of 12 nights. Train was flat.

**Decision: NOT shipped.** The gain is a seventh of the 13-night resolution (about ±0.02).
The fit rests on 40 games and a subset flips sign. Even a fit on the test nights caps
this whole area at about -0.01 RMSE: confirmed lines are not where the skater error is.

**Runner-ups:**
- Recheck `pp_demote` at about 40 nights. A parameter-free form subtracts DFO
  `last10.powerplayPoints / gamesPlayed` when tonight's `pp` is None.
- Book props on promoted skaters (6i: -1.66, books over-price promotions) against
  early->lock promotions here (-1.4 under). Retest the two together.
- DFO carries confirmation for goalies only, not for line combinations. Logging each
  team's update time would make "stale morning lines" measurable. The early-only and
  lock-only board rows (66 / 62) are never scored in the other state.

## Cycle 7 (target: goalies -- DK points MAE + within-slate rank corr) -- nothing shipped

Harness: `python3 scripts/nhl_skater_lab.py goalies --seeds 4` (new): starters in the box,
same rebuilt slates and TRAIN/TEST split, vs a train-mean constant; rank corr within slate.

| state | split | slates | goalie-games | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|---|---|
| lock | train | 11 | 123 | 8.071 | 10.033 | +0.853 | +0.075 |
| lock | test | 13 | 168 | 7.761 | 9.615 | +0.242 | -0.060 |
| lock | constant 9.76, test | 13 | 168 | 7.679 | 9.544 | -0.397 | -- |
| early | train | 11 | 116 | 8.242 | 10.232 | +1.162 | +0.021 |
| early | test | 13 | 165 | 7.633 | 9.485 | +0.036 | -0.006 |
| early | constant 9.39, test | 13 | 165 | 7.653 | 9.514 | -0.970 | -- |

**Critic #7: no fixable hole at this n.** Each input predicts its component on both splits
(goal edge vs win +0.32 train / +0.16 test; opp xG vs GA +0.26/+0.22; saves prop vs saves
+0.40/+0.30), but they cancel in DK points (saves prop vs goal edge -0.78): projection vs
win +0.04; saves prop vs points +0.07 train / +0.24 test (sign unstable). Projection sd 1.1
vs actual 9.8 caps the attainable rho at ~0.11; slate-averaged rho se ~0.08 with ~13
starters a slate. Train least-squares refits (saves prop, projection, both): test MAE
7.69-7.76, rho -0.05 to -0.09. Shipped minus train median, test MAE, slate bootstrap 90%:
lock +0.092 [-0.04, +0.22], early +0.013 [-0.13, +0.16].

Variant checked: shrink to train median, proj' = o + k(proj - mean), grid on train (lock
o 10.2 k 0.8, early o 9.7 k 0.7): test MAE lock 7.761 -> 7.732, early 7.633 -> 7.657.
A wash: NOT shipped. Lineups use the simulated distribution (win/shutout tail, calibrated
per NHL_STATUS 6e), not the mean. Re-run at ~40 slates.

Runner-ups: (1) opponent xG 3.16-3.20 vs actual GA 2.94-2.99 on both splits/states, ~0.7
pts a goalie -- check whether empty-netters sit in the goalie's goals against (calibration,
not ranking); (2) saves-prop bias +1.0 train / +0.07 test, too unstable to offset;
(3) starters with no saves prop: 21 in test, rho +0.68, MAE 6.16 -- too few.

## Cycle 8: the simulator over-projects every skater by ~0.17 pts -- SHIPPED (scale 0.976)

Baseline (4 seeds), as cycle 7's shipped model:

| state | split | nights | skater-games | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|---|---|
| lock | train | 11 | 2,196 | 4.568 | 6.076 | +0.172 | +0.367 |
| lock | test | 13 | 2,962 | 4.478 | 5.970 | +0.173 | +0.404 |
| early | train | 11 | 2,176 | 4.577 | 6.094 | +0.158 | +0.371 |
| early | test | 13 | 2,923 | 4.490 | 5.995 | +0.164 | +0.397 |

**Critic #8: no mean hole that ranks; a uniform +0.17 over-projection.** Night-level bias
train +0.21 (se 0.12, 7/11 nights positive), test +0.30 (se 0.13, 10/13); early the same.
The per-player input means land on the box (test linear points 6.404 vs 6.366), so the
excess is the simulator's, spread over components per skater-game on test: blocks +0.04,
bonuses +0.06 (5+ shots 4.6% simulated vs 4.1%), assists +0.03, goals/shots +0.02 each.
Train-fit refits on salary/FPPG/TOI add nothing (best test RMSE 5.961, rho +0.406).
The 2025-26 season lab shows the same sign out of season (H1 +0.187, H2 +0.300).

**Fix.** `edge/nhl_sim.SKATER_SCALE` multiplies every skater's draws (goalies untouched,
percentiles stay consistent). Fit on TRAIN as sum(actual)/sum(proj): 0.9751 lock, 0.9772
early; shipped 0.976 for both. The lab calls the production simulator, so lab == production.

| state | split | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|
| lock | test, before | 4.478 | 5.970 | +0.173 | +0.404 |
| lock | test, shipped | 4.445 | 5.965 | +0.007 | +0.404 |
| early | test, before | 4.490 | 5.995 | +0.164 | +0.397 |
| early | test, shipped | 4.456 | 5.989 | -0.003 | +0.397 |

RMSE gain is below what 13 nights resolve; the bias removal is ~2.3 se. Shipped because
held out improves in both states and the sign holds on both splits and both season halves.
Cross-position effect: skaters now lose ~2.4% against goalies in the optimizer.

Runner-ups: (1) DK's +2 shorthanded-point and +1.5 shootout-goal bonuses are missing from
both grader and simulator (vs DK FPTS on 18 nights / 3,674 rows: 3.4% of rows differ,
mean +0.04) -- add both; refit the scale after; (2) the over-wide 5+ shot / 3+ block tails:
refit SHOT_K/BLOCK_K in the season lab (H1) rather than a scale; (3) report a
"scratches = 0" RMSE beside the current one (82 at lock, 133 early are excluded).
The $6.5k+ slice flips sign between splits (train -0.68, test +0.55): don't fit tiers.

## Cycle 9: no-prop skaters' means spread too wide -- not shipped

Baseline (4 seeds, cycle 8 shipped): lock test 13 nights / 2,962 MAE 4.445 RMSE 5.965 bias
+0.007 rho +0.404 (train 4.530 / 6.074); early test 2,923 MAE 4.456 RMSE 5.989 bias -0.002
rho +0.397 (train 4.538 / 6.091).

**Critic #9: no hole 13 nights resolve; the most consistent is spread.** Slope of actual
on projection, no-prop skaters (57% of graded games, rates-only path): lock train 0.81
(n 1,246) / test 0.71 (n 1,685), early 0.80 / 0.66. Prop skaters flip (1.18 / 0.95): don't
fit. Fitting both groups: test RMSE 5.965 -> 5.972, better on 4/13 nights.

**Variant** `noprop_shrink(k)`: no-prop means shrunk toward their slate x position (C/W/D)
mean, mu + k(m - mu), k = train slope.

| check | k (fit on) | RMSE | MAE | rho |
|---|---|---|---|---|
| lock test, 13 nights | 0.811 (train) | 5.9648 -> 5.9568 | 4.4452 -> 4.4493 | +0.404 -> +0.405 |
| early test, 13 nights | 0.802 (train) | 5.9894 -> 5.9780 | 4.4560 -> 4.4584 | +0.397 -> +0.398 |
| season lab H2, 86 dates / 24,515 (1 seed) | 0.870 (H1) | 6.1158 -> 6.1102 | 4.4745 -> 4.4987 | -- |

Season-lab slopes 0.870 H1 / 0.907 H2: the over-spread is real but smaller than the 13
nights say, and k drifts (0.81 train, 0.71 test, 0.87-0.91 season). Per-night squared error
-0.20 (se 0.09) lock. **Not shipped:** RMSE improves everywhere, MAE worsens everywhere
(+0.002-0.004 test, +0.024 season); the target does not improve on both, and k is unstable.
Revisit with ~40 nights or with the mechanism (blend_season / small-sample rates
under-regressed) fixed in the season lab rather than a post-hoc shrink.

Runner-ups: prop skaters' slope flips (recheck ~40 nights); D2/D3/F4 slot biases swing by
split (noise, as cycle 6); cycle 8's open items (shorthanded/shootout bonuses; a
"scratches = 0" RMSE line).

## Cycle 10 (target: salary-tier bias) -- SHIPPED (points knot 1.1, top stretch 1.7)

Metric: skater DK-points bias + MAE by salary tier, held out, both states; overall MAE /
RMSE as guardrail. Lab cache as of 10/29 (today's refresh timed out): 11 train / 13 test
nights, 2,196 / 2,962 skater-games at lock, 2,176 / 2,923 early.

**Critic #10: $8.5k+ skaters under-projected, all of it goals + assists.** The only tier
whose sign holds on both splits and states (bias = proj - actual, lock, night-clustered se):

| tier | train n / bias | test n / bias |
|---|---|---|
| <3.5k | 1227 / -0.09 +- 0.18 | 1875 / -0.08 +- 0.11 |
| 3.5-5k | 479 / +0.15 +- 0.23 | 480 / +0.47 +- 0.14 |
| 5-6.5k | 317 / +0.73 +- 0.31 | 349 / -0.32 +- 0.46 |
| 6.5-7.5k | 104 / -0.94 +- 1.09 | 141 / +0.42 +- 0.88 |
| 7.5-8.5k | 54 / -0.16 +- 1.26 | 80 / +0.86 +- 0.88 |
| 8.5k+ | 15 / -4.33 +- 2.19 | 37 / -1.77 +- 1.95 |

$8.5k+ components (lock, train / test): goals -1.36 / -0.53, assists -2.07 / -1.11, shots
+0.19 / +0.02, blocks -0.15 / -0.01, bonuses -0.94 / -0.15. A negative-binomial inversion of
the points market (K 8/4/2) barely moves it (-4.9 -> -4.6 train): the curve's steepness, not
the distribution's shape. The critic's fix, POINTS_STRETCH 1.1 -> 1.3 for everyone: test RMSE
5.966 -> 5.971 lock, 5.990 -> 5.996 early (guardrail fails), and 7.5-8.5k over-fed on train too
(-0.16 -> +0.61).

**Fix.** Steepen only above a knot: points mean m > POINTS_KNOT gets an extra factor
(m / knot) ** (POINTS_TOP_STRETCH - POINTS_STRETCH) on goals + assists. Grid knot 0.9 / 1.1
/ 1.3 x top 1.4 / 1.7 / 2.0, chosen by TRAIN RMSE summed over lock + early (2 seeds):
1.1 / 1.7 (12.1523; next 0.9 / 1.4 at 12.1544). Confirmed at 4 seeds:

| state | split | MAE | RMSE | 8.5k+ bias | 7.5-8.5k bias |
|---|---|---|---|---|---|
| lock | train | 4.5317 -> 4.5242 | 6.0745 -> 6.0688 | -4.30 -> -2.65 | -0.16 -> +0.08 |
| lock | test | 4.4452 -> 4.4443 | 5.9648 -> 5.9623 | -1.77 -> -0.51 | +0.86 -> +1.07 |
| early | train | 4.5379 -> 4.5293 | 6.0913 -> 6.0845 | -4.24 -> -2.68 | -0.33 -> -0.16 |
| early | test | 4.4560 -> 4.4520 | 5.9894 -> 5.9833 | -1.83 -> -0.60 | +0.90 -> +1.09 |

(tier biases from the 2-seed grid). Small gains, 52 top-tier rows: sign stable, size not
resolved; recheck at ~40 nights. Parity: one slate (154332, 461 skaters) rebuilt through
build_board == the lab variant to 2e-16.

Runner-ups: (1) bonuses (5+ shots, 3+ blocks) over-simulated below $7.5k on both splits
(+0.07 to +0.35 / skater-game; 3.5-5k 5+ shot rate 0.051 sim vs 0.027 test); (2) 3.5-5k
over-projected both splits (+0.15 / +0.47), slope unstable (0.98 / 0.69) -- no tier fit.

Gap map (same cache, train-fit linear recalibration per slice, test SSE saved per night,
lock / early): no-prop skaters 22 / 32 (slope 0.81 / 0.71 lock); no-PP 18 / 20; F4 9 / 0;
3.5-5k 6 / 5; every PP / line / mid-salary slice NEGATIVE (sign flips = noise). Board
players who did not play: 82 at lock (359 proj pts), 133 early (567). Goalies MAE 7.90 vs
constant 7.85 (n 291).

## Cycle 11 (the gap map's four fixes, 2026-10-31)

Four fixes from cycle 10's gap map, run in parallel (availability critic + two worktree
agents). Lab cache predates cycle 10 (its "shipped" = pre-knot, lock test 4.4452 / 5.9648);
deltas below are variant vs that cache.

**(a) $8.5k+ stars -- cycle 10's knot, shipped `0222f007`.**

**(b) Availability -- SHIPPED `4e2a3e8f` (no fit).** Scratches cost nothing: the 82 lock /
133 early DNP board skaters were in 0 of 675 logged lineup player-nights (DFO drops them
70-90 min after lock; only 5 of 14 test GTD flags sat). The hole: DK <-> DailyFaceoff names
joined by exact norm(); a nickname (DK Freddy / Nick / Jake / Max vs DFO Frederick /
Nicholas / Jacob / Maxwell) left the skater lineless, and no-prop + no line = dropped.
41 train / 60 test player-games that PLAYED (235 / 297 DK pts) were off the board, avg
5.9 pts at $2,660 on test, 13 test games at 3x+. Gabe Perreault (F1 PP2) and Max Shabanov
(PP1) stayed on via props with line = pp = None. Fix: (team, initial, last) fallback when
unique on DFO's team list and DK's board; goalies too; listed on the page. Lab rebuild of
155421: 7 aliases. Held-out effect: re-cache pending.
Runner-ups: "Projected" goalies at lock wrong 4 / 25 vs 1 / 231 Confirmed -- fit a start
probability; props skaters with no DFO line still boarded (8 lock DNPs) -- require a line or
a recent game. Logged lineups != entered lineups (10/24 Kuemper): log what is entered.

**(c) No-prop spread -- SHIPPED (RATE_K 30).** Mechanism: last season's per-game rates
used raw; 2025-26 replay slope 0.54 for 1-20 games of rates, 0.78 for 21-40, 0.85 for 60+.
Each rate shrinks toward a * toi ** b per position / component (fitted on the rates table)
with RATE_K games of prior weight. A linear-in-TOI prior failed (lock test MAE 4.445 ->
4.452, F4 bias +0.02 -> +0.27). K grid on season H1 (1 seed): none 4.4487 / 6.0248, 15
4.4443 / 6.0178, **30 4.4432 / 6.0146**, 60 4.4463 / 6.0168.

| check | MAE | RMSE | no-prop slope |
|---|---|---|---|
| season H1 (fit), 22,716 | 4.4471 -> 4.4417 | 6.0234 -> 6.0130 | 0.872 -> 0.901 |
| season H2, 24,515 | 4.4728 -> **4.4740** | 6.1134 -> 6.1095 | 0.912 -> 0.929 |
| lock train, 2,196 | 4.5317 -> 4.5274 | 6.0745 -> 6.0703 | 0.809 -> 0.848 |
| lock test, 2,962 | 4.4452 -> 4.4411 | 5.9648 -> 5.9572 | 0.712 -> 0.758 |
| early train, 2,176 | 4.5379 -> 4.5333 | 6.0913 -> 6.0863 | 0.802 -> 0.842 |
| early test, 2,923 | 4.4560 -> 4.4532 | 5.9894 -> 5.9820 | 0.662 -> 0.701 |

Paired dSE per skater-game: H2 -0.047 [-0.080, -0.014] (53/86 dates); test lock -0.119
[-0.251, +0.003], early -0.160 [-0.313, -0.032] (10/13 nights each). Judgment call: shipped
on the target (this season, both states, both metrics) despite H2 MAE +0.0012; that MAE
cost sits on late-season many-game skaters where this season's blend already dominates.
Parity 155435: production == variant to 4.4e-16. SKATER_SCALE would refit 0.9728 lock /
0.9755 early (0.9749 / 0.9772 without): left at 0.976 until the re-cache.

**(d) Bonus tails -- NOT shipped.** The tails are calibrated conditional on the mean:
season H1 likelihood peaks at the shipped SHOT_K 19.4 / BLOCK_K 11.4 (shots ~10, wider),
H2 likelihood too. Tail-rate fit 40 / 20 + scale 0.977: test RMSE 5.9648 -> 5.9647 lock,
5.9894 -> 5.9866 early, 3.5-5k bias +0.46 -> +0.45 -- noise, and narrower than the
likelihood wants (GPP ceilings). The 5+ shot excess at 3.5-5k is a MEAN gap (shots 1.82
sim vs 1.67 actual, test lock; season H2 blocks 0.813 vs 0.765).

Next: re-cache the lab with (a)-(c) in production, re-baseline, refit SKATER_SCALE once;
then blocks run ~6% high (season H2) -- the block means, not their tail.


## Cycle 12 (unattended, 2026-11-08): imputed shots' goal elasticity -- not shipped

**Re-cache first** (cycle 11's open item). The lab cache was rebuilt from today's code
(cycles 10-11 in production) for every logged slate: 57 per state (154747 / 154752 have no
draftables snapshot). Box scores fetched through 11/7 (10/30 still missing: 3 games, ~107
rows ungraded). New baseline, 4 seeds, TRAIN < 10/13 <= TEST:

| state | split | nights | skater-games | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|---|---|---|
| lock | train | 11 | 2,223 | 4.511 | 6.062 | -0.009 | +0.363 |
| lock | test | 24 | 5,952 | 4.483 | 6.017 | -0.078 | +0.416 |
| early | train | 11 | 2,202 | 4.520 | 6.078 | -0.029 | +0.368 |
| early | test | 24 | 5,880 | 4.497 | 6.038 | -0.079 | +0.411 |

Parity lab == build_board: max 0.0050 (log rounding). Scratched board skaters 128 lock (567
proj pts) / 212 early. Goalies test MAE 7.59 vs constant 7.62 (n 331). SKATER_SCALE: bias is
now -0.08 on test (-0.01 train), so no refit (the 0.976 still sits on the right side).

**Critic #12: imputed shots spread ~1.6x too wide.** Skaters with no shots prop (4,700 of
8,175 graded rows, incl. ~40% of F2/D1/D2 with a points prop but no shots line) get last
season's / this season's shot rate x scale (implied team goals / 3.128), an elasticity of 1
never fitted. Slope of actual on projected shots, no-shots-prop skaters: lock 0.69 train /
0.66 test, early 0.73 / 0.65 (prop skaters 1.00-1.08). The critic also found the means at the
noise floor: test MSE 36.24 vs the simulator's own variance 36.12 (train 36.74 / 35.66), and
every other lever flat (season-blend K 36 cells, role scaling 24 cells, a TOI prior for the
727 rateless rows, train-fit linear recalibration: |test dRMSE| <= 0.004). Proposed: (a) shots
x scale ** 0.5, (b) shrink imputed shots toward the slate x position mean, k ~0.62, (c) refit
SKATER_SCALE. Critic's own estimate -0.003 to -0.004 RMSE, below one seed's noise.

**Variant** `shots_power` (scripts/nhl_skater_lab.py; e, optional k via
`shrink_imputed_shots`): imputed shots x scale ** (e - 1) on the cached board, == a
production knob to 9e-16 on all 20,152 cached skater rows. Fit on TRAIN, lock + early RMSE
summed, 4 seeds: e 1.0 (shipped) 12.1397, 0.5 **12.1350**, 0.25 12.1402, 0 12.1362; k 0.75
on top 12.1327 (-0.002, noise; slate-relative, cycle 9's shrink was unstable) -> **e 0.5, no
shrink** (judgment call: 0 and 0.5 within noise; 0.5 is the smaller move).

Season lab (2025-26 no-prop path, team goals = a form estimate, not odds), squared error per
skater-game vs e = 1:

| e | H1 (fit) RMSE | H2 RMSE | H1 dSE [95%] | H2 dSE [95%] | shots slope H2 |
|---|---|---|---|---|---|
| 1.0 | 6.0146 | 6.1112 | -- | -- | 0.863 |
| 0.5 | 5.9993 | 6.1018 | -0.185 [-0.241, -0.127] | -0.116 [-0.174, -0.059] | 0.922 |
| 0.0 | 5.9914 | 6.0960 | -0.279 [-0.386, -0.178] | -0.186 [-0.286, -0.088] | 0.947 |

**2026-27 TEST (the target), 4 seeds, e 0.5:**

| state | split | MAE | RMSE | bias | rank | dSE per night [95%], nights better |
|---|---|---|---|---|---|---|
| lock | train | 4.511 -> 4.508 | 6.062 -> 6.060 | -0.009 -> -0.013 | +0.363 -> +0.364 | -0.040 [-0.077, -0.002], 8/11 |
| lock | test | 4.483 -> 4.479 | 6.017 -> 6.016 | -0.078 -> -0.087 | +0.416 -> +0.415 | -0.004 [-0.051, +0.051], 13/24 |
| early | train | 4.520 -> 4.518 | 6.078 -> 6.075 | -0.029 -> -0.029 | +0.368 -> +0.369 | -0.055 [-0.155, +0.034], 7/11 |
| early | test | 4.497 -> 4.496 | 6.038 -> 6.039 | -0.079 -> -0.089 | +0.411 -> +0.410 | +0.033 [-0.004, +0.072], 10/24 |

**Not shipped.** The season lab says the elasticity is real and well below 1 (beyond noise in
both halves), but on this season's real-odds boards the test gain is nil at lock and early
RMSE is worse in point estimate: the rule (target improves in both states) fails, and the
gate would refuse it. Likely why: with market implied goals, the scale's spread is already
small, and the season lab's form-based team goals are noisier, so damping them helps more there.
Revisit at ~45 test nights (`report --variant shots_power`), or fit e on the season lab with
odds-based team goals if a 2025-26 odds history is found.

Runner-ups: (1) goal / assist split from props: the outcome weight on the market's goal share
flips by split (+1.13 test / -0.63 train vs the 3.5 the model applies), ~0.001 RMSE; (2)
simulated spread vs realised error: prop skaters 55.2 vs 54.1, no-prop 21.6 vs 22.7 (GPP
percentiles, not this metric); (3) data: A.J. Greer runs on default rates (18 board rows), as
the NHL lists him "Anthony-John (AJ) Greer" and both the exact and the initial + last joins miss.

Unattended gate (20261108-0638): **not shipped (log / harness only, verified)** -- the night's
"fail / does not compile" was the loop compiling this .md (fixed 11/8); the lab variant + tests
landed by hand after harness verify + the NHL suite passed.

Unattended gate (20261110-0714): **not shipped (log / harness only, verified)**


## Cycle 14 (availability: goalies -- queued by Adam; transfer T01 from nba L01) -- SHIPPED on the branch (Adam reviews)

Unattended, 2026-11-12. (Cycle 13, 11/10, built scripts/nhl_lineup_lab.py; its log entry never
landed here -- the commit message `0ade87d1` holds its baseline.) Target, as queued: who the board
had in the G slot, and how sure, at build time vs who started. Metric per held-out night: starter
precision by DailyFaceoff status and minutes to puck drop (Brier / log loss of the start belief),
false actives (board goalie who did not start: count, projected points, our G slots), lead time.
The T01 probe numbers were kept from the critic.

**Harness built first** (none existed): `scripts/nhl_goalie_lab.py`. Every committed version of
data/nhl_lines_snapshot.json (`git log --all`: main 9/28-10/16, data branch since; 1,075 versions,
`generated_at` stamps) gives DFO's goalie + status for every team-game at T-240 .. T-15 before ITS
puck drop; truth = the box-score starter (data/nhl_cache/box). 594 team-games 9/29-11/11. TRAIN <
2026-10-21 <= TEST (the middle of the history, fixed before any test row was read): 302 / 292
team-games, 22 / 22 nights. Also scored: our logged lineups' G slots (build time from
dfs_proj_history_nhl.csv since 10/29, else lock - 15 min) and Adam's entered goalies (contests.db).

**Cycle 0 for this target (shipped = every DFO goalie a certain starter):**

| split | lead | Confirmed n / wrong | Likely | Projected |
|---|---|---|---|---|
| train | T-180 | 165 / 0 | 71 / 0 | 66 / 14 (21%) |
| train | T-60 | 191 / 0 | 66 / 0 | 45 / 12 (27%) |
| test | T-180 | 168 / 0 | 51 / 0 | 73 / 14 (19%) |
| test | T-60 | 201 / 0 | 46 / 0 | 45 / 9 (20%) |

The real starter was named for good a median T-15 / T-16 (train / test; 10th pct T-0 / T-1): the
switch comes out at warmups, after every build. Our logged lineups, test: 117 G slots, 8 did not
start (Kuemper 10/24 x3 at T-165, Saros 11/5 x5 at T-163 / T-103), 97.2 projected points on zeros;
train 0 / 48. Adam's entries: 1 of 63 entered goalies did not start (Merilainen 10/17, one $1 GPP);
he swapped Kuemper and Saros by hand.

**Critic #14 (dfs-availability-critic): "Projected" is three different claims.** DFO's no-news
default for a team on rest is the goalie who started its last game, and that is the one that
fails: he started 58% train / 61% test (17/28 at T-180). Its pick of the OTHER goalie on the second
night of a back-to-back is its rotation call and held 14/14 (train 18/18). Everything else
"Projected" ran 83% / 90%. All 29 misses were dressed as the backup in the box (not a name join).
The monitor's live poll had the flips at T-21..T-28; the 30-minute snapshot timer at T-16..T-1.
Fix specified: p_start per goalie from (status, started last game, back-to-back), fitted on TRAIN;
the simulator zeroes the goalie in 1 - p of the sims so cash floors and GPP ceilings price it.

**Fix (no judgment on test):** the critic's cells re-fitted on TRAIN in the lab (leads pooled with
weight 1/7 so n = team-games, 2 pseudo-games toward the status rate): Confirmed 1.0, Likely 1.0,
Projected/started_last **0.586** (train n 19.9), Projected/b2b_backup **0.969** (13.1),
Projected/other **0.785** (17.4); unknown last start -> the status rate 0.758. Production:
`GOALIE_START_P` + `goalie_cell` / `goalie_start_prob` (edge/dfs_run_nhl.py), `nhl.last_starts`
(schedule + cached box scores, looking back two weeks), `row["p_start"]`, and `nhl_sim.simulate`
zeroing the goalie's column from its OWN RNG (`[seed, 7919, i]`), so skater draws and every
confirmed-goalie board are bit-identical to before (tested). Parity: production's cell == the
lab's on 594 / 594 team-games (`nhl_goalie_lab.py parity`). The page lists unconfirmed goalies
with their start chance.

**Held out, TEST (292 team-games, 22 nights), start belief:**

| lead | wrong | Brier ship / status-only / cells | log loss ship / status / cells | goalie pts RMSE ship / cells | dSE cells [90%, by night] |
|---|---|---|---|---|---|
| T-180 | 14 | 0.0479 / 0.0394 / **0.0337** | 0.331 / 0.124 / **0.105** | 9.635 / 9.585 | -0.97 [-3.25, +1.81] |
| T-60 | 9 | 0.0308 / 0.0249 / **0.0222** | 0.213 / 0.078 / **0.068** | 9.574 / 9.497 | -1.47 [-3.32, +0.50] |
| T-15 | 5 | 0.0171 / 0.0137 / **0.0119** | 0.118 / 0.042 / **0.037** | 9.557 / 9.508 | -0.94 [-2.40, +0.72] |

(pts = train starter mean 10.10 x p vs what DFO's goalie scored, 0 if he sat: the availability
part only; goalie points themselves barely beat a constant, cycle 7.)

**Lineups replayed** (`nhl_goalie_lab.py lineups`: nhl_lineup_lab's production sim + optimizers
on the skater lab's cached boards, scored with DK FPTS against every real contest field of the
type; a goalie who sat scores 0, late swap not modelled). Only slates where some goalie has p < 1
are rebuilt (elsewhere both arms are identical). TEST = slates on/after 10/21.

| state (seeds) | mode | test slates | dead G slots full -> p_start | mean pts full -> p_start [90% by slate] | pctile | $ per $1 |
|---|---|---|---|---|---|---|
| lock (20) | cash | 7 | 17/140 -> 0 | 95.58 -> 97.04, +1.46 [-3.09, +6.12] | .598 -> .607 | 1.136 -> 1.145 |
| lock (20) | cash_floor | 7 | 18/140 -> 0 | +0.23 [-3.68, +4.68] | .603 -> .610 | 1.164 -> 1.173 |
| lock (20) | gpp | 10 | 37/200 -> 10 | 85.94 -> 84.85, -1.08 [-3.95, +2.70] | .433 -> .428 | 1.187 -> 0.546 |
| early (10) | cash | 9 | 13/90 -> 0 | 90.60 -> 90.39, -0.22 [-4.86, +4.23] | .466 -> .476 | 0.785 -> 0.738 |
| early (10) | cash_floor | 9 | 15/90 -> 0 | -0.65 [-2.72, +1.79] | .475 -> .465 | 0.831 -> 0.723 |
| early (10) | gpp | 14 | 19/140 -> 5 | 89.17 -> 87.54, -1.64 [-4.09, +0.96] | .461 -> .445 | 1.154 -> 1.341 |

Train (slates < 10/21): lock cash -0.21, floor -0.50, gpp -0.62; early cash -0.18, floor +2.72,
gpp -1.44 -- all inside noise. Early-state decomposition (test, 14 moved slates): the G slot gains
**+1.21** pts / lineup in cash and +1.29 in cash_floor (the avoided zeros, ~13 x 9 / 90), skaters
+0.14 / +0.21; GPP G slot +0.34, skaters **-1.62** (the goalie change re-picks the stacks). On train
the cash G slot ran -0.94 (full-credit Projected goalies who did start scored well): goalie points
(sd ~9.8) swamp a 30-slate replay either way. The lock-state GPP $ drop is one top-heavy slate.

**Decision: SHIPPED on the branch, for Adam's review** (the NHL gate scores skaters, whose rows
are bit-identical; a goalie change goes to Adam on these numbers). The target improves in both
information states and at every lead (Brier -30%, log loss -68%, dead G slots in replayed cash
lineups 30 -> 0 across the two states), and the expected-value case is direct: a 59% goalie is
worth ~0.59 of his projection, and projections barely separate starters (sd 1.1 vs 9.8 realised).
NOT shown: a lineup-dollar gain. Realised lineup points are within noise in every cell, and GPP ran
-1.1 / -1.6 per lineup in both test states (4 of 4 GPP cells negative, none significant) -- watch it
forward; if GPP keeps losing at ~20 more slates, give GPP the full-credit sim (the 95th percentile
may want the cheap risky goalie). Conservative choices (unattended): no change to the monitor or
the snapshot cadence; mirrors not regenerated (no pushes) -- `harness run mirror-nhl` after merge.

Morning builds (outside the fitted leads; DFO's state at T-900 .. T-600, checked after the fit with
the lab's LEADS extended): started_last started 94/183 train, 102/184 test (51-55%), other 73/83 and
77/88, b2b_backup 9/9 and 4/4 -- the cells hold before any news (started_last slightly optimistic),
so an early phone build is not over-penalised. AppTest of the page at 04:47 (one lobby listing, no
exception): every goalie on the 11/12 slate was still Projected and listed with its start chance.

Side effect to know: a Projected goalie's proj (and so his predicted ownership and the simulated
cash field's use of him) drops by 1 - p; the real field's discount for unconfirmed goalies is
unmeasured (the field had Kuemper 6-8% on 10/24 while we had him in 100%).

Runner-ups: (1) the 30-minute DFO snapshot after 8 PM publishes warmup flips at T-1 while the live
poll had them at T-21: poll every 5 min from T-35 to each puck drop (cloud fallback + the lab's
lead times); (2) the build-time goalie state is not logged (45 of 117 test slots have an assumed
build time): log starter_status + p_start in the projection history; (3) logged != entered (11/5:
log Saros x5, Adam entered Hellebuyck) -- T17. Refit the cells at ~40 nights (`nhl_goalie_lab.py
report`); lead time did not move P(start) enough to fit (Projected 0.71-0.79 across leads, train).

## Gate fix (2026-11-12, hand-made at Adam's OK): the lab cache outran the gate

The 11/12 night's cycle 14 (goalies) finished its work, then the gate crashed: `nhl_skater_lab.py
cache` on main's side ran past the gate's 3-hour limit. Cause: scans older than 14 days are read
from the F: archive (WSL /mnt/f, ~20 s a cold query) and one board asks the store 29 times -- an
October slate took 323-536 s, and the cache rebuilds 69 slates x 2 states in each checkout. Fix:
each distinct store query of a pinned scan is kept in data/nhl_cache/lab/scans/<id>.json.gz (rows
in the store's own order: a filtered full read reordered the h2h/totals rows and moved a skater
10.34 -> 10.58), and `cache` builds across processes (all cores but two; `--workers`). Parity on
10/1 (gid 154311): real store == cache fill == from cache, 536 s -> 11 s. The gate's worktrees copy
data/nhl_cache, so they start warm once main's cache is filled. Separately found: a slate whose
games are live while it is cached does not rebuild identically (two real-store builds of 11/12
differed in blocks / role); past slates are deterministic. Cycle 14 itself was merged by Adam.
