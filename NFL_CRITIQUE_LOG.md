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
