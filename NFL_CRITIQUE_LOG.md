# NFL critique log

Target metric (cycle 1, picked by `scripts/dfs_target_scout.py` + the `dfs-target-scout`
agent): **the lineup we enter vs the real DK field** -- mean field percentile of the
replayed production lineup per contest, cash rate, and $ at the rank taken. Cash and GPP
never pooled. Harness: `scripts/nfl_lineup_lab.py` (`parity`, `report`, `lodo`, `gate OUT`).
Split: leave-one-slate-out over the slate dates with a cash export (7 slates, 9/13-11/01).
The slate is the unit; 7 slates is thin -- say so with every number.

**Read every number as a mean over 8 optimizer seeds.** The cash search does not converge
at 700 iterations: one seed is one random draw (cycle 1).

## Cycle 0 -- baseline (2026-11-09), shipped model, z_cash 0.75

Parity (replay == logged lineup, iters 700): cash 32/35 boards, GPP 34/35. The 3 cash
misses are the first three Sundays (151307, 153428, 153769), logged before the 9/27 and
9/30 ownership changes that reorder the pool. At the page's 350 iterations: 29/35, 26/35.
Exports with no logged board: 9/27 dg 153774, 10/04 dg 154078 (excluded). Unscored lineup
players: none. 11/08 exports sit in data/ unimported (unattended runs never import).

| lineup | contests | slates | cashed | mean field pctile | mean points | ROI ($ at rank) |
|---|---|---|---|---|---|---|
| cash, seed 0 (what production built) | 11 | 7 | 36% | 0.507 | 130.1 | -47% |
| cash, mean of 8 seeds | 11 | 7 | 62% | 0.648 | 133.9 | +12% |
| cash, Adam's real entry | 11 | 7 | 55% | 0.652 | 138.4 | +33% |
| GPP, seed 0 | 16 | 7 | 69% | 0.778 | 147.6 | +287% |
| GPP, mean of 8 seeds | 16 | 7 | 68% | 0.774 | 146.5 | +230% |
| GPP, Adam's real entry | 16 | 7 | 69% | 0.829 | 146.8 | +248% |

GPP $ is dominated by 11/01 (one 49-entry $5 contest: +$64 at rank 1); not enough data.

## Cycle 1 (auto: cash lineups vs the field) -- 2026-11-09, unattended

Scout:
```json
{"sport": "nfl", "target": "build lineup harness (cash first)",
 "metric": "Share of replayed production cash lineups that clear each real DK cash contest's cash line, pooled per slate (mean across slates, 90% bootstrap over slates). Secondary, reported separately and never pooled with cash: GPP $ returned per $1 entered on each real GPP's payout table.",
 "harness": "build: scripts/nfl_lineup_lab.py ...", "split": "Leave-one-slate-out over Sunday draft groups 9/13-11/08",
 "why": "No NFL harness scores what we enter. Model cash +29% [-33%,+92%] on 13 entries / 8 slates; GPP +251% (23e/9s) mostly from 11/01.",
 "money_at_stake": "Cash: model +$5 on 13e/8s. GPP: +$116 on 23e/9s, no held-out test.",
 "runner_ups": ["RB/TE projected low by +1.4 pts (TD imputation)", "player_rush_attempts unread",
                "12 top-salary players without props on one board: check that date's prop join"]}
```
Name suspects (5) checked: all different players (Brian Robinson Jr./Bijan Robinson etc.); nothing to fix.

**Work: built `scripts/nfl_lineup_lab.py`** (above), the NFL harness that scores what we enter.

**First pass (single seed):** z_cash chosen leave-one-slate-out from (0..1.5) picked 1.5 on
every fold, +0.232 [+0.038, +0.402] field pctile -- and the in-sample curve was ragged
(0.5: 0.482, 0.75: 0.507, 1.0: 0.637, 1.5: 0.739, 2.0: 0.649, 3.0: 0.503).

**Critic's hole (dfs-model-critic, fresh):** the cash lineup is a random search that never
settles: seeds 0..7 give 3-7 different lineups per board within 1-2.5 projected points,
and field pctile per seed for shipped z=0.75 runs 0.507 (seed 0, the lowest of 8) to 0.822,
mean 0.648 -- Adam's real entries (0.652). Single contests swing 0.17-0.96 on the seed.
The optimizer's own objective does not pick the better draw (best-objective-of-8 = 0.589).
So the single-seed baseline, Adam's apparent +0.145 lead and the +0.232 z gain were mostly
seed noise. Critic's fix: `cash_restarts=8` (keep the best objective of seeds 0..7), score
every construction on the seed mean, extend the z grid; z=1.5 seed-averaged +0.070
[-0.044, +0.173] (computed on all slates).

**What I did:** the harness now averages every comparison over 8 seeds (`SEEDS`,
`rows_avg`; `gate` writes seed-averaged rows), and the z grid is (0, .25, .5, .75, 1, 1.25,
1.5, 2, 2.5) -- extended after seeing 1.5 at the old edge, a judgment call on all slates;
lodo still chooses inside the grid without the held-out slate.

Held out, z_cash chosen leave-one-slate-out on the 8-seed mean:

| cash lineup | contests | slates | cash rate | mean field pctile | ROI | pctile diff vs shipped [90%, by slate] |
|---|---|---|---|---|---|---|
| shipped z 0.75 (8-seed mean) | 11 | 7 | 62% | 0.648 | +12% | |
| z chosen leave-one-slate-out | 11 | 7 | 55% | 0.601 | +0% | **-0.047 [-0.171, +0.054]** |

Folds chose 2.0, 1.5, 2.0, 0.0, 1.0, 1.5, 2.0 -- an unstable choice, the noise signature.
In-sample (not held out): z 1.0 0.708, 1.5 0.719, 2.0 0.720, 0.0 0.665, 0.5 0.660.
Restarts (best objective of 8): 0.589 vs the 0.648 seed mean (critic's numbers) -- no
gain in expectation, so not shipped either; they would only make the lineup reproducible.

**Decision: nothing shipped to the model.** Z_CASH stays 0.75; the 2020-21 backtest
contradiction (DFS_STATUS.md open question 3) is still unsettled on 7 real slates.
Harness + seed-averaged gate committed; adding `nfl_lineup_lab.py gate` to
harness/critique_gate is Adam's call.

Runner-ups (critic):
1. Do not tilt cash toward projected ownership: among near-tied candidate lineups the
   most-owned scored 0.504, the least-owned 0.705 (11 contests, not held out).
2. Zero-point players in shipped cash lineups: Ted Hurst (x2), Cade Otton (x2), Zay
   Flowers, Oronde Gadsden -- 6 slots on 5 boards. Check whether any were ruled out
   before the last pre-lock build (availability target).
3. Two TEs (TE in FLEX) in 5 of 11 shipped cash lineups; cheap-TE projections may run
   hot. Untested on lineups.

Unattended gate (20261109-0817): **not shipped (log / harness only, verified)**

## Cycle 2 (auto: RB DK points; scout: rush-TD imputation from the rush-attempts prop) -- 2026-11-10, unattended

Scout:
```json
{"sport": "nfl", "target": "RB rushing-TD imputation: use the player_rush_attempts props we collect and never read",
 "metric": "Held-out DK points error for RBs (then RB+TE) on the graded 2026 boards, matched by draft group: bias and SSE per slate vs the shipped projection, 90% bootstrap over slates; must not lower the 8-seed cash field pctile on replayed lineups. GPP $ separately.",
 "harness": "build: rebuild each board's RB projection from the pre-lock props (OddsStore history + F: archive), TD = f(yds, att) fitted on nflverse 2023-25; lineup check via nfl_lineup_lab with the rebuilt proj",
 "split": "fit nflverse 2023-25, test the 2026 live boards 9/13-11/08; lineup check leave-one-slate-out, 7 cash slates",
 "why": "RBs +1.3 pts under-projected (440 player-slates); rushing TDs scaled off yards alone while attempt lines sit unread",
 "money_at_stake": "cash +29% [-33%,+92%] 13e/8s (+$5); GPP +212% [+95%,+329%] 26e/9s (+$121, mostly 11/01)",
 "runner_ups": ["cash ownership tie-break (least-owned near-ties 0.705 vs 0.504, not held out)",
                "late-scratch check for cheap chalk (Hurst 10/25, Otton/Godwin 11/01)",
                "GPP lineups in the lab, scored in $"]}
```
Name suspects (2): different players (Brian Robinson Jr. / Bijan Robinson, Mike Washington Jr. /
Malik Washington); nothing to fix.

**Harness built: `scripts/nfl_rb_lab.py`** (`extract`, `parity`, `fit`, `skew`, `report`,
`gate OUT`). Per logged board: the last dfs_nfl scan >= 40 min before the board's first
kickoff (closer in, books have pulled the starting games' props: 52 of 160 priced 1 min
out on 9/13), DraftKings' book, each player's markets from his own game's event only.
Cached in data/harness/nfl_rb_lab_boards.json (gitignored; ~40 min cold, mostly the F:
archive). Parity, rebuilt vs logged proj: mean |diff| 0.03-0.21 pts per board. Truth = DK
FPTS by draft group; 33 projected RBs with no export score dropped and counted.

Baseline (model before this cycle), held out, 2026 boards 9/13-11/08:

| pos | n | slates | bias | MAE | RMSE |
|---|---|---|---|---|---|
| RB | 437 | 8 | +1.22 | 5.243 | 7.231 |
| TE | 355 | 8 | +1.29 | 5.103 | 6.748 |
| WR | 727 | 8 | +0.83 | 5.416 | 7.322 |
| QB | 235 | 8 | +0.17 | 6.293 | 7.993 |

**The scout's hypothesis: noise.** Rush TDs = a*yds + b*att, fitted on nflverse 2023-25 (2,687
RB-weeks; in-sample SSE 981.3 -> 981.0): RB RMSE 7.231 -> 7.229, SSE -16 [-72, +37] (314 of
437 backs had an attempts line). Not shipped.

**Critic's hole (dfs-model-critic, fresh): a back's yardage line is read as his mean, but it
is his median.** `implied_mean` solves a symmetric normal, so it returns the middle
outcome; RB yardage is right-skewed. Evidence: actual - projected is positive in every
scoring part (rush yds, rec, rec yds, rush TD, rec TD, +0.1 to +0.25 pts each; 2026 weeks
1-5, n 168, diagnostic only); bias by salary quintile +1.8, +1.9, +1.1, +1.6, -0.4 (none for
bell-cows, as skew predicts). Fix fitted on nflverse 2023-25 only: per RB/FB player-season
with >= 8 games, (mean - median) on the median: rush yds 7.38 - 0.0855m (n 176, medians
15-120), rec yds 4.79 - 0.0950m (n 177, 3-50), floored at 0, added before TDs are imputed.
Scale 1.0 as fitted (a scale chosen on the 9/13-10/11 slates is flat 0.75-1.0). Judgment
call recorded: the critic picked the hole type after looking at 2026 bias; the constants
never saw 2026.

**Shipped (to the branch):** `dfs_sport.NFL_RB_YDS_SKEW` + `_nfl_mean_shift` through a new
`Sport.mean_shift` hook applied in `dfs_project.project` after `implied_mean`. NFL backs only;
QB/WR/TE and NCAAF unchanged (tested). The lab's refit reproduces the constants exactly
(test).

| RBs, held out | n | slates | bias | MAE | RMSE | SSE change [90% over slates] |
|---|---|---|---|---|---|---|
| before, 9/13-11/08 | 437 | 8 | +1.22 | 5.243 | 7.231 | |
| after, 9/13-11/08 | 437 | 8 | +0.35 | 5.270 | 7.129 | **-641 [-1353, -3]** |
| before, 10/18-11/08 | 268 | 4 | +1.40 | 5.349 | 7.391 | |
| after, 10/18-11/08 | 268 | 4 | +0.53 | 5.303 | 7.250 | -553 [-1158, +72] |

Per slate SSE: -179, +91, -11, +10, +31, -280, -317, +14 -- 10/25 and 11/01 carry most of
it; 8 slates is thin. MAE rises slightly on all 8 (MAE rewards the median; the target is
the mean).

Lineups (nfl_lineup_lab, RB proj shifted by the rebuilt delta, everything else logged,
8-seed mean; not enough data either way):

| lineup | contests | slates | cashed | field pctile | ROI | pctile diff [90%] |
|---|---|---|---|---|---|---|
| cash before | 11 | 7 | 62% | 0.648 | +12% | |
| cash after | 11 | 7 | 59% | 0.606 | -7% | -0.042 [-0.112, +0.012] |
| GPP before | 22 | 8 | 63% | 0.795 | +174% | |
| GPP after | 22 | 8 | 77% | 0.870 | +272% | +0.075 [-0.013, +0.160] |

Unattended decision: NFL has no gate in harness/critique_gate yet, so this model change goes
to Adam with these numbers. The cash lean (-0.042, interval spans 0) is the reason to look before
merging. `nfl_rb_lab.py gate OUT` writes the rows (437, pred = production on the checkout)
for adding it to the gate.

Runner-ups (critic):
1. Backs with a rush prop and no receiving prop get no receiving points: 90 rows, bias
   +1.86 (+1.07 after the shift); 12 receiving-only backs +4.15. Impute rec / rec yds
   from rush yds, fitted on nflverse.
2. Receptions read off a normal instead of a Poisson: SSE -151 [-331, +15] on 8 slates,
   -111 on 10/18-11/08; -677 combined with the shift.
3. Stale pre-lock prices are not the hole: 24 late-game RBs priced early vs late, RMSE
   6.946 vs 6.966.
WR/TE probably carry the same skew (+0.83 / +1.29 bias). Untested; fit their own constants.

Unattended gate (20261110-1028): **review** -- waiting for Adam: harness critique-loop --merge / --drop


- no gate for nfl yet (a lab gate() + critique_gate.SPECS entry): the agent's own numbers are in the log
- touches the loop's own judge (skill / critic / hooks / gate): Adam decides

**Adam, 2026-11-10: merged GPP ONLY.** Cash leaned worse (-0.042 [-0.112, +0.012], 11 contests /
7 slates) while NFL cash is profitable, so cash keeps the unshifted projection (`proj`) and every
GPP build reads `proj_gpp` (`edge/dfs_run_nfl.gpp_pool`: GPP lineup, page QB stacks, leverage
chart, contest EV). The forward log carries both columns; re-test cash on the shift when the
cash slates reach ~12.
