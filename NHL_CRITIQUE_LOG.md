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
