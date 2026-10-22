# NBA minutes model: critique loop

Started 2026-10-21 at Adam's request. Each cycle an independent critic agent
(`.claude/agents/nba-minutes-critic.md`), with no knowledge of this project beyond the
repo, finds the single biggest hole in the minutes model. The hole is then fixed in
`scripts/nba_minutes_lab.py` (fit on 2024-25, scored on 2025-26), shipped to
`edge/nba_minutes.py` only if it improves the held-out season, and recorded here with
the standard table (`python3 scripts/nba_minutes_lab.py report`). MAE (bias) in minutes
by slice; RMSE over all active player-games.

## Cycle 0: the starting point (v2, shipped 2026-10-21, `f6a3104f`)

| mode | season | n | RMSE all | all | opening (team games 1-5) | rookie / no NBA history | lottery rookie | new team (first 5) | back from 5+ missed | teammate of a scratch | promoted starter | high role (base 28+) | roles/240 >1.15, starter |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| confirmed | 2024-25 | 32,566 | 6.732 | 5.15 (-0.1) | 4.76 (-0.1) | 3.87 (+0.5) | 4.65 (-1.0) | 5.61 (-0.3) | 5.63 (-0.1) | 5.16 (-0.1) | 5.20 (+0.9) | 4.72 (-0.6) | 5.08 (-2.6) |
| confirmed | 2025-26 | 32,224 | 6.810 | 5.26 (-0.1) | 5.30 (-0.2) | 5.46 (-0.6) | 5.17 (-1.4) | 6.21 (+0.1) | 5.99 (-0.6) | 5.34 (-0.1) | 5.20 (+1.3) | 4.63 (-0.5) | 4.68 (-2.2) |
| confirmed | 2026-27 | 103 | 7.840 | 6.74 (+0.0) | 6.74 (+0.0) | 5.35 (+5.1) | 2.43 (-2.4) | 9.95 (+2.6) | - | - | 6.18 (+3.5) | 8.06 (-8.0) | 7.60 (-6.4) |
| inactives | 2024-25 | 32,566 | 7.751 | 5.77 (-0.1) | 4.77 (-0.1) | 4.26 (+0.7) | 4.95 (-1.4) | 6.05 (-0.0) | 6.29 (+1.4) | 5.78 (-0.1) | 8.88 (-7.9) | 5.28 (-0.8) | 6.39 (-5.0) |
| inactives | 2025-26 | 32,224 | 7.649 | 5.78 (-0.1) | 5.41 (-0.2) | 5.99 (-0.7) | 6.10 (-3.0) | 6.27 (+0.6) | 6.34 (+1.0) | 5.88 (-0.1) | 8.31 (-7.2) | 5.10 (-0.9) | 5.70 (-4.4) |
| inactives | 2026-27 | 103 | 7.836 | 6.56 (-0.0) | 6.56 (-0.0) | 5.00 (+4.7) | 2.67 (-2.7) | 10.43 (+2.5) | - | - | 6.63 (-6.6) | 7.75 (-7.7) | 8.21 (-8.2) |

legacy protocol 2025-26, confirmed: all 5.123, teammates of a scratch 5.216, promoted starters 5.346 (+1.49), n 29,311

legacy protocol 2025-26, inactives: all 5.624, teammates of a scratch 5.722, promoted starters 7.974 (-6.59), n 29,311

## Cycle 1: the 240 step moved stars like bench players

**Critic #1 (fresh general-purpose agent, ~17 min, read-only).** "Steps 3-4 close the
team's gap to 240 backwards: they move every player's minutes roughly in proportion to
his role, but in the data stars barely move and the players ranked 8-12 by role absorb
almost all of any surplus or shortfall."

- **Phantom surplus:** active roles already sum to about 240 (median 242), yet step 3
  adds 49-56 "missing" minutes per team-game. 77% of that comes from players already
  out at least one game, whose absence is already inside teammates' decayed roles.
  Step 4 then squeezes about 18% back out, mostly from starters.
- **Slopes of (actual − role) on G = 240 − Σ active roles, by rank:** ranks 1-3
  0.00-0.04 actual vs 0.095-0.108 in v2; ranks 8-12 0.09-0.18 actual vs 0.045-0.075
  in v2. Both seasons agree.
- **The salary nudge was compensating for this hole.**
- **Runners-up:**
  - regular starters who are active but out of tonight's five (bias +6 to +8, about
    430 a season);
  - bench recency after a DNP or a big game;
  - a gradient-boosted residual model reaches RMSE 6.43, so headroom remains.

**Fix (shipped).**
- Step 3-4 → `rank_gap`: G = 240 − Σ active roles; the player at rank k moves
  a(k, G > 0) + b(k, G > 0)·G; the team is closed to 240 by the slopes and clipped
  to 0-48.
- 2 × 14 cells × 2 information states, least squares on 2024-25 (32,516 player-games
  per state), written to `edge/nba_minutes_gap.py` by `nba_minutes_lab.py cycle1 --write`.
- Teams with fewer than 8 active keep the old path.
- `SALARY_NUDGE` off: on top of the fix, salary moves MAE 4.830 → 4.834.
- Production matches the lab to rounding (mean 0.025 min, 64,448 player-games).
- Scratch-monitor fixture rebuilt as a real 13-man roster (the old 10-man fixture
  summed to 208).

**Result** (2025-26 held out, starters confirmed):
- RMSE 6.810 → **6.692**, MAE 5.26 → **5.03**
- inactives only: RMSE 7.649 → **7.474**, MAE 5.78 → **5.49**
- starters with roles > 1.15 × 240: bias −2.2 → **−0.0**
- 2026-27 (3 games): MAE 6.74 → 5.75, stars −8.0 → −5.1
- legacy protocol: 5.123 → **4.929** confirmed, 5.624 → **5.394** inactives only

| mode | season | n | RMSE all | all | opening (team games 1-5) | rookie / no NBA history | lottery rookie | new team (first 5) | back from 5+ missed | teammate of a scratch | promoted starter | high role (base 28+) | roles/240 >1.15, starter |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| confirmed | 2024-25 | 32,566 | 6.585 | 4.88 (-0.1) | 4.49 (-0.1) | 3.67 (+0.0) | 4.70 (-1.1) | 5.34 (-0.4) | 5.55 (-0.3) | 4.91 (-0.1) | 5.18 (+0.6) | 4.51 (+0.1) | 4.35 (-0.4) |
| confirmed | 2025-26 | 32,224 | 6.692 | 5.03 (-0.1) | 5.06 (-0.2) | 5.15 (-0.9) | 4.90 (-1.0) | 6.00 (-0.2) | 5.98 (-0.9) | 5.11 (-0.1) | 5.13 (+0.9) | 4.44 (+0.2) | 4.26 (-0.0) |
| confirmed | 2026-27 | 103 | 7.001 | 5.75 (+0.0) | 5.75 (+0.0) | 4.46 (+3.8) | 5.26 (-5.3) | 8.98 (+2.6) | - | - | 6.01 (+6.0) | 5.66 (-5.1) | 5.80 (-3.6) |
| inactives | 2024-25 | 32,566 | 7.539 | 5.43 (-0.1) | 4.41 (-0.1) | 3.78 (-0.1) | 4.97 (-1.7) | 5.73 (-0.2) | 6.11 (+0.9) | 5.46 (-0.1) | 8.55 (-7.4) | 4.88 (+0.1) | 4.99 (-2.4) |
| inactives | 2025-26 | 32,224 | 7.474 | 5.49 (-0.1) | 5.11 (-0.2) | 5.52 (-1.5) | 5.36 (-2.6) | 6.10 (+0.3) | 6.27 (+0.6) | 5.59 (-0.1) | 8.03 (-6.8) | 4.73 (+0.1) | 4.80 (-2.0) |
| inactives | 2026-27 | 103 | 6.970 | 5.29 (+0.0) | 5.29 (+0.0) | 3.82 (+3.1) | 6.47 (-6.5) | 9.03 (+1.9) | - | - | 8.49 (-8.5) | 3.64 (-3.0) | 4.86 (-4.5) |

legacy protocol 2025-26, confirmed: all 4.929, teammates of a scratch 5.026, promoted starters 5.227 (+1.10), n 29,311

legacy protocol 2025-26, inactives: all 5.394, teammates of a scratch 5.485, promoted starters 7.688 (-6.21), n 29,311

## Cycle 2: a bench role that ignores whether the coach is playing him right now

**Critic #2 (fresh general-purpose agent, ~33 min, read-only).** "Every player outside
tonight's five gets a slow average of his bench minutes, ignoring the two strongest
signals the model already has: his last game, and -- for a regular starter -- being
left out of the announced five." Critic #1 listed these as two runners-up; tested
against everything else, they are one hole and nothing else is close.

- **Last-game slope:** bench error rises 0.18 minutes per minute his last game was
  above his role (+0.175 on 2024-25, n 18,893; +0.181 on 2025-26, n 18,514).
  Starters are flat.
- **Dropped regular starters** (start rate ≥ 0.8, active, not in the five):
  - play 0 minutes 67% / 60% of the time, bias +8.3 / +5.6;
  - 1.3% of rows but 6.7% / 5.5% of all squared error;
  - examples: Jaylen Brown 2026-02-04, Trae Young 2025-12-05.
- **Residual GBM ranking of signals** (fit 2024-25, scored 2025-26, MSE gains):
  start status × start rate −1.7, last game −0.9, days since last game −0.28,
  standings −0.18, everything else smaller.

**Fix (shipped, `edge/nba_minutes.py` RECENCY_* / DROP_*).**
- **Recency:** non-starters with start rate < 0.8 move λ of the way to their last
  game's minutes (a DNP counts 0).
- **Drop:** regular starters left out of the five get q × role.
- **Guards:** at least 1 game this season (3 for the drop), not back from an absence.
- Rank-gap coefficients refitted under the new roles (`edge/nba_minutes_gap.py`,
  `nba_minutes_lab.py cycle2 --write`).
- **Chosen on 2024-25 only:** λ 0.25, q 0.2 (grid λ 0.1-0.3 × q 0-0.5, coefficients
  refitted per cell). The critic's own picks were λ 0.2, q 0.3.
- **Judgment call on the guards:** 2024-25 alone prefers no guards (6.9355 vs
  6.9439). Without them, 2025-26 opening week goes 5.01 → 5.18, first game back
  5.97 → 6.27, and 2026-27 night one 6.98 → 8.54: rotation players get cut on last
  season's finale. Kept on mechanism, and recorded here because the held-out
  numbers were seen before deciding.
- Production matches the lab to rounding (RMSE 6.586 / 7.396 both).

**Result** (2025-26 held out, starters confirmed):
- RMSE 6.692 → **6.586**, MAE 5.03 → **4.93**
- inactives only: RMSE 7.474 → **7.396**, MAE 5.49 → **5.41**
- high role 28+: 4.44 (+0.2) → **4.33 (−0.0)**
- legacy protocol: 4.929 → **4.845** confirmed, 5.394 → **5.332** inactives only
- 2026-27 (3 games): RMSE 7.001 → 6.984, MAE 5.75 → 5.83

**Runners-up:**
- days since the last game;
- the lab doesn't replay the pre-inactives live state (expected starters, DK OUT,
  the 13-man cut);
- end-of-season effects (April MSE 60 vs 45).

| mode | season | n | RMSE all | all | opening (team games 1-5) | rookie / no NBA history | lottery rookie | new team (first 5) | back from 5+ missed | teammate of a scratch | promoted starter | high role (base 28+) | roles/240 >1.15, starter |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| confirmed | 2024-25 | 32,566 | 6.427 | 4.77 (-0.1) | 4.45 (-0.1) | 3.66 (+0.1) | 4.71 (-1.1) | 5.24 (-0.4) | 5.57 (-0.3) | 4.79 (-0.1) | 5.19 (+0.6) | 4.29 (-0.2) | 4.35 (-0.2) |
| confirmed | 2025-26 | 32,224 | 6.586 | 4.93 (-0.1) | 5.01 (-0.2) | 5.09 (-0.8) | 4.89 (-1.0) | 5.91 (-0.3) | 5.97 (-0.9) | 5.00 (-0.1) | 5.12 (+0.9) | 4.33 (-0.0) | 4.27 (+0.2) |
| confirmed | 2026-27 | 103 | 6.984 | 5.83 (-0.0) | 5.83 (-0.0) | 4.93 (+4.2) | 6.04 (-6.0) | 8.70 (+2.2) | - | - | 5.84 (+5.8) | 5.31 (-4.7) | 5.44 (-3.2) |
| inactives | 2024-25 | 32,566 | 7.460 | 5.37 (-0.1) | 4.37 (-0.1) | 3.79 (+0.0) | 4.88 (-1.5) | 5.58 (-0.2) | 6.14 (+0.9) | 5.39 (-0.1) | 8.20 (-6.9) | 4.89 (+0.1) | 5.02 (-2.3) |
| inactives | 2025-26 | 32,224 | 7.396 | 5.41 (-0.1) | 5.05 (-0.2) | 5.51 (-1.4) | 5.35 (-2.4) | 5.95 (+0.2) | 6.32 (+0.6) | 5.52 (-0.1) | 7.77 (-6.4) | 4.73 (+0.1) | 4.76 (-1.9) |
| inactives | 2026-27 | 103 | 6.999 | 5.36 (-0.0) | 5.36 (-0.0) | 4.07 (+3.2) | 6.99 (-7.0) | 9.11 (+2.1) | - | - | 9.15 (-9.2) | 3.47 (-2.9) | 4.84 (-4.5) |

legacy protocol 2025-26, confirmed: all 4.845, teammates of a scratch 4.928, promoted starters 5.224 (+1.14), n 29,311

legacy protocol 2025-26, inactives: all 5.332, teammates of a scratch 5.424, promoted starters 7.427 (-5.80), n 29,311
