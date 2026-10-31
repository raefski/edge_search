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

## Cycle 3: the live build had no inactive list

**Critic #3 (fresh general-purpose agent, ~26 min, read-only).** "NBA.com's
daily-lineups feed no longer marks anyone inactive. Both history builders rely on that
flag, so live histories record every injured, G-League or two-way player as a
0-minute game, and tonight's roster falls back to DK's sparse OUT flags plus the
13-man cut -- a path the lab has never scored."

- **Verified:**
  - the 2025-26 lineup file for 12/26 flags 78 inactives; 2026-10-20's flags 0 of 103;
  - stats.nba.com's InactivePlayers table is empty for 10/20 too;
  - the box score (v3) comments name 6 inactives in game 1 alone, 18 across 10/20.
- **One correction:** the critic's example, Taurean Prince, was a genuine coach's
  DNP on 10/20 per the box score.
- **Replay of the live state on 2025-26** (shipped model): RMSE 6.586 with a full
  inactive list and clean history, against 6.716-6.954 in the broken live state.
  That's bigger than cycles 1 and 2 combined.
- **Runners-up:**
  - dropped starters whose last game was 15-25 minutes (9.8 actual vs 3.9
    projected, n ≈ 90 a season);
  - April (MSE 57.7 vs about 42);
  - NBA.com's afternoon *Expected* five changes in 6 of 28 team-games, but live
    applies the split and the dropped-starter cut to it.

**Fix (shipped).**
- **`edge/nba_availability.py`:**
  - after the game, stats.nba.com `boxscoretraditionalv3` comments: "Inactive - …"
    → skipped, "DNP - Coach's Decision" → a 0 kept, absent from the box → didn't
    dress; cached in `data/nba_availability/`;
  - before the game, ESPN's public injuries JSON: every player listed Out is out,
    with the source and timestamp shown on page 12 and used by the scratch monitor.
- `NM.load_history` and the lab apply the box-score availability.
- `nba_history_publish.refresh` caches every finished game.
- The pre-inactives cut is now 15 (it runs all day without NBA.com's list). On the
  live-state replay, 2024-25 fit: 13 / 14 / 15 / none = 6.478 / 6.467 / 6.465 /
  6.477.
- `scripts/nba_minutes_lab.py live` replays any information state; `cycles` scores
  every version.

**Result.**
- **Live state on 2025-26** (the error the app actually had vs has):
  - starters confirmed: RMSE 6.954 → **6.634**, MAE 5.062 → 4.942, bias −0.80 → −0.51;
  - inactives only: 8.204 → **7.435**.
- **Lab protocol:** unchanged (6.586 / 7.396). The fix restores the inputs the lab
  always assumed.
- **2026-27, re-scored on the 85 players who actually dressed** (18 of the 103
  "actives" never did): confirmed RMSE 7.12, inactives only 6.31.

| mode | season | n | RMSE all | all | opening (team games 1-5) | rookie / no NBA history | lottery rookie | new team (first 5) | back from 5+ missed | teammate of a scratch | promoted starter | high role (base 28+) | roles/240 >1.15, starter |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| confirmed | 2024-25 | 32,566 | 6.427 | 4.77 (-0.1) | 4.45 (-0.1) | 3.66 (+0.1) | 4.71 (-1.1) | 5.24 (-0.4) | 5.57 (-0.3) | 4.79 (-0.1) | 5.19 (+0.6) | 4.29 (-0.2) | 4.35 (-0.2) |
| confirmed | 2025-26 | 32,224 | 6.586 | 4.93 (-0.1) | 5.01 (-0.2) | 5.09 (-0.8) | 4.89 (-1.0) | 5.91 (-0.3) | 5.97 (-0.9) | 5.00 (-0.1) | 5.12 (+0.9) | 4.33 (-0.0) | 4.27 (+0.2) |
| confirmed | 2026-27 | 85 | 7.123 | 5.72 (-0.0) | 5.72 (-0.0) | 4.70 (+4.2) | 2.07 (-2.1) | 9.76 (+2.1) | - | 5.61 (-0.0) | 6.47 (+6.5) | 4.91 (-4.2) | 4.89 (-3.8) |
| inactives | 2024-25 | 32,566 | 7.460 | 5.37 (-0.1) | 4.37 (-0.1) | 3.79 (+0.0) | 4.88 (-1.5) | 5.58 (-0.2) | 6.14 (+0.9) | 5.39 (-0.1) | 8.20 (-6.9) | 4.89 (+0.1) | 5.02 (-2.3) |
| inactives | 2025-26 | 32,224 | 7.396 | 5.41 (-0.1) | 5.05 (-0.2) | 5.51 (-1.4) | 5.35 (-2.4) | 5.95 (+0.2) | 6.32 (+0.6) | 5.52 (-0.1) | 7.77 (-6.4) | 4.73 (+0.1) | 4.76 (-1.9) |
| inactives | 2026-27 | 85 | 6.305 | 4.71 (+0.0) | 4.71 (+0.0) | 4.16 (+3.3) | 3.63 (-3.6) | 8.28 (+1.2) | - | 5.13 (-0.0) | 6.17 (-6.2) | 2.99 (-2.3) | 3.56 (-3.0) |

legacy protocol 2025-26, confirmed: all 4.845, teammates of a scratch 4.928, promoted starters 5.224 (+1.14), n 29,311

legacy protocol 2025-26, inactives: all 5.332, teammates of a scratch 5.424, promoted starters 7.427 (-5.80), n 29,311

## Cycle 4: the Expected five was used as if confirmed

First cycle run through the `dfs-critique` skill. **Baseline** (`report`, unchanged since
cycle 3): 2025-26 RMSE 6.586 confirmed, 7.396 inactives only; live state 6.634 / 7.435.

**Critic #4 (fresh `dfs-model-critic` agent, ~17 min, read-only).** "Before the confirmed
lineups, the app treats NBA.com's *Expected* five as certain -- the start/bench split and
the dropped-starter cut -- yet it was wrong in 7 of 28 regular-season team-games, and the
lab never scores that state (only true fives or none)." Cycle 3 listed this as a runner-up;
the critic measured it as bigger than cycle 1 or 2.

- **Error rate:** last Expected snapshot vs the final Confirmed five in
  `data/nba_lineup_log.csv`, 2026-10-20/21: 7 of 28 team-games, 8 of 140 starters
  (preseason 50%). Misses were coaches' choices that still played: Jalen Williams 30
  minutes, Grayson Allen 29 (both left out of the confirmed five).
- **Cost, simulated on 2025-26:** at 25% wrong, Expected-as-confirmed RMSE 7.148
  (8.49 in the wrong team-games) vs 6.586 with true fives and 7.396 with none.
- **Cross-check:** a five guessed from last game's starters, treated as confirmed,
  scores 8.81 in team-games with one wrong starter, against 7.78 for no five.
  Confidently wrong costs more than not knowing.

**Fix (shipped, `edge/nba_minutes.py` EXPECTED_W).**
- **Model:** while any announced starter is unconfirmed, minutes =
  w · project(Expected five) + (1 − w) · project(no five). Confirmed or absent fives
  are unchanged.
- **Wiring:** `build_board` and the scratch monitor (`harness/nba_scratch.py`) pass
  NBA.com's `confirmed` flag. Page 12's "not confirmed" banner says the five counts 70%.
- **Lab:** `nba_minutes_lab.py cycle4` scores the Expected state exactly. Every
  team-game is projected three ways: five right; one miss (the lowest-start-rate
  starter swapped for the highest-start-rate active non-starter, the critic's
  process); no five. Error at miss rate ρ is the mixture, and the optimal w has a
  closed form.
- **Chosen on 2024-25 only:** w = 0.70, by minimax regret over ρ = 10–30% (regret
  ≤ 0.018 RMSE; the same w in the live state). Closed-form optimum 0.82 at ρ = 10%,
  0.66 at 25%; the critic's pick was 0.65 at 25%.
- **Judgment call:** the ρ range. 25% comes from two opening nights (CI about
  11–45%); midseason rotations are steadier, so the range was centred lower. The
  critic's 2025-26 numbers at w = 0.65–0.80 had been seen before choosing it.
- **Parity:** production equals 0.7 × hard + 0.3 × no-five to rounding (max 0.05
  minutes, 7,870 player-games). Legacy protocol through production, every five marked
  Expected and right: MAE 4.891 vs 4.845 confirmed (lab: +0.042).
- **Live builds:** 10/22 (no five posted) is identical (0.0). 10/21 with the five
  forced to Expected: e.g. Herbert Jones, dropped from the confirmed five, 5.0 → 8.5
  projected (he played 17).

**Result** (2025-26 held out, 32,224 player-games; RMSE / MAE (bias)):

| Expected five wrong in | lab: as confirmed (before) | lab: blend w 0.70 (after) | live state: before | live state: after | no five (lab / live) |
|---|---|---|---|---|---|
| 0% | 6.586 / 4.929 (−0.09) | 6.599 / 4.971 (−0.09) | 6.634 / 4.942 | 6.637 / 4.985 | 7.396 / 7.435 |
| 10% | 6.800 / 5.040 | **6.744 / 5.050** | 6.847 / 5.052 | **6.782 / 5.064** | |
| 15% | 6.904 / 5.095 | **6.815 / 5.089** | 6.952 / 5.108 | **6.853 / 5.103** | |
| 20% | 7.007 / 5.151 | **6.885 / 5.128** | 7.054 / 5.163 | **6.924 / 5.143** | |
| 25% (measured) | 7.108 / 5.206 | **6.955 / 5.168** | 7.155 / 5.218 | **6.994 / 5.182** | |
| 30% | 7.208 / 5.262 | **7.024 / 5.207** | 7.255 / 5.274 | **7.063 / 5.222** | |

- **Wrong team-games:** RMSE 8.484 → 7.928.
- **The price, while the five is Expected and right:** RMSE 6.586 → 6.599, and
  promoted starters' MAE 5.12 → 5.21 with bias +0.9 → −1.3 (n 1,940). Part of the
  late-news edge waits for the Confirmed five.
- **Confirmed and inactives-only states:** unchanged (6.586 / 7.396). Below about 10%
  wrong, MAE (not the fitting criterion) is flat to slightly worse.

**Runners-up (critic #4):**
- **The starting role's memory is too short.** Start-role decay 0.93 and κ 3 (best on
  the 2024-25 grid, gap refitted) take confirmed 6.586 → 6.550 and MAE 4.929 → 4.891.
- **Diffuse headroom.** A residual GBM (34 features, fit on 2024-25) reaches
  6.407 / 7.272, and no single feature is worth more than 0.04.
- **No shrinkage toward rank.** Starters' actual minutes rise only 0.84–0.89 per
  projected minute (0.76 for rank 1); a per-rank linear recalibration gains 0.013.
- **Not tried:** a per-player Expected weight (by agreement with start rate). It would
  be fitted to the assumed one-swap process. Re-measure ρ from the lineup log after a
  few weeks.

## Cycle 5: the starting role forgot too fast

Adam, 2026-10-22: "use the best idea for next cycle". No new critic: this is critic
#4's first runner-up, re-decided on 2024-25.

**Hole.** One memory (DECAY 0.8, last season worth κ = 1 game) served the bench role,
the pre-lineup role and the starting role. Critic #4: starters' residuals rise 0.20
minutes per minute their 10-game median sits above their role. A starter's minutes
are stable, so a short memory reads one hot or cold week as a new role. A bench role
really moves (bench decay 0.7 or 0.9 are both worse).

**Fix (shipped, `edge/nba_minutes.py` MS_DECAY / MS_KAPPA).**
- `role_v2` recomputes `ms` (the starting role) with its own weights. Bench and
  pre-lineup roles are unchanged, so the inactives-only state is untouched.
- **Chosen on 2024-25 only:** `nba_minutes_lab.py cycle5`, grid decay 0.8–0.97 ×
  κ 1–8, rank-gap coefficients refitted for every cell. Best was decay 0.9 / κ 3
  (2024-25 RMSE 6.9245 both modes, 6.3886 confirmed); 0.93 / 3 ties, and the
  critic's pick was 0.93 / 3. The surface is flat: 0.85–0.95 × 2–5 all within 0.005.
- Rank-gap coefficients regenerated: confirmed cells only; inactives identical.
- **Parity:** production `role_v2` equals the lab's roles exactly (ms, mb, base;
  max difference 0.0000 over 4,919 rows). Legacy protocol through production:
  confirmed 4.845 → **4.814**, inactives 5.332 unchanged.
- EXPECTED_W (cycle 4) kept at 0.70. With the new roles, the Expected state at 25%
  wrong goes 6.955 → 6.939.

**Result** (2025-26 held out, starters confirmed; inactives-only rows are unchanged):

| | before (cycle 4) | after (cycle 5) |
|---|---|---|
| RMSE, all (32,224) | 6.586 | **6.550** |
| MAE (bias) | 4.93 (−0.1) | **4.89 (−0.1)** |
| opening week | 5.01 | **4.84** |
| high role 28+ | 4.33 (−0.0) | **4.20 (+0.1)** |
| starters, roles > 1.15 × 240 | 4.27 (+0.2) | **4.02 (+0.5)** |
| promoted starters | 5.12 (+0.9) | **5.10 (+0.5)** |
| back from 5+ missed | 5.97 (−0.9) | **5.87 (−0.7)** |
| lottery rookies | 4.89 (−1.0) | 5.00 (−1.2) |
| live state (injury-list Out, cut 15), confirmed | 6.634 | **6.598** |
| Expected five, 25% wrong (lab / live) | 6.955 / 6.994 | **6.939 / 6.978** |
| 2026-27 so far, confirmed (387, 2 nights) | 7.582, high role 4.92 (−1.8) | **7.290**, high role **3.73 (−0.3)** |

**The 10/21 live board, rebuilt.**
- **Leak:** a naive rebuild disagreed: everyone who dressed went 4.56 → 4.67. But
  today's `nba_player_history.json` already holds 10/21's games, so the short memory
  put half its weight on the game being scored (Curry's last entry: 21 minutes).
  **Any rebuild of a past slate from the current history file leaks.**
- **With 10/21 stripped from history** (DNPs scored as 0, n 281):
  - everyone who dressed: RMSE 8.30 → **8.05**, MAE 6.73 → 6.39;
  - starters (110): 7.17 → **6.24**, bias −2.48 → −1.28;
  - bench (171): 8.95 → 9.02.
  - One night, anecdotal.

**Open.**
- Lottery rookies are slightly worse (4.89 → 5.00, n ≈ 75).
- Bench bias moves down a little, since the closing step takes starters' gains from
  the bench.
- Critic #4's other runners-up still stand:
  - diffuse residual headroom (a GBM reaches 6.41);
  - shrinkage toward rank, which gains only 0.013.

## Cycle 6: the Out list misses scratches, and the replay assumed it didn't

**Baseline** (cycle 5): lab 6.550 / 7.396; live state 6.598 / 7.435; Expected five at
25% wrong 6.939.

**Critic #6 (fresh `dfs-model-critic` agent, ~19 min, read-only).** "The live state
assumes the Out list catches every meaningful scratch, and the model has no notion of
availability risk. A player who won't dress keeps his full role, takes minutes from
teammates who do play, and earns 0 DFS points."

- **Claimed evidence:** 11 of 47 role-10+ scratches of 10/20-21 were still projected
  by the live board.
- **Simulated on 2025-26:** at 23% missed, the replay's 6.598 / 7.435 becomes
  6.700 / 7.682.
- **Proposed fix:** soft availability for unlisted players who didn't dress in their
  team's last game ("stale"). Fitted on 2024-25, it reaches 6.588 / 7.523 at 23%
  missed and 6.561 / 7.412 at 0%.

**Corrections to the evidence.**
- **The 23% measured the old pipeline.** Cycle 3 (ESPN's Out list, cut 15) was
  committed 10/21 22:54, after that night's games, so both logged boards ran on DK's
  OUT flags, a 13-man cut and phantom history.
- **The hole holds up on the current list anyway.** ESPN's 10/21 pre-game list
  wasn't saved. As a proxy, ESPN's 10/22 feed lists the 47 as: 18 **Out**, 16
  **Day-To-Day** (ignored by the app), 11 **not at all** (Julian Reese, Malachi Smith,
  Javon Small and other two-ways, plus Brandon Ingram).
- **So the realistic miss rate is at least about 23%.** That's the unlisted share
  alone, before any Day-To-Day player who sat.

**Fix (shipped).**
- **Lab, `nba_minutes_lab.py cycle6` (`avail_preds`, `avail_score`, `avail_fit`):**
  - each role-10+ scratch is missed with probability m (a fixed per-row draw), then
    the cut keeps 15;
  - every team-game is projected with its stale players in and out;
  - **board metric (new):** every player the board projects plus everyone who
    dressed, with a non-dresser scored as 0. The standard metric never charges for a
    phantom, and DFS does.
- **Model:**
  - teammates get w · (stale in) + (1 − w) · (stale out, cut re-run);
  - a stale player gets his expected minutes, p × his minutes if he plays, with p
    split by role < 10 and 10+ (the critic's suggestion to treat big roles apart);
  - announced starters are never stale (with no five, starters are unknown, so they
    can be).
- **Chosen on 2024-25 only**, per information state: a grid w 0–1 × p_lo 0.15–0.4 ×
  p_hi 0.3–1.0, by minimax regret of the board RMSE over m = 0 / 25 / 50%.
  - **starters announced:** w 0.4, p 0.20 / 0.50 (regret 0.052);
  - **no five:** w 0.4, p 0.25 / 0.70 (regret 0.137).
  - **Why minimax:** fitted at a single m, w runs 0.05–0.80 and p_hi 0.28–0.94, so a
    point fit would be a bet on the unknown miss rate. For stale players under role
    10 the fitted p is 0.22–0.27 at any m.
- **Production:**
  - `scripts/nba_history_publish.py` writes `missed_last` (his last active game was
    with this team, before the team's latest game);
  - `NM.project_available` holds the blend, and `build_board` uses it;
  - page 12 shows "sat last game: x% to dress".
- **ESPN logging:** `edge/nba_availability.tonight_out` now logs each distinct ESPN
  snapshot, all statuses, to `data/nba_espn_injuries/<ET date>.jsonl` (git-ignored,
  not re-fetchable), so the miss rate can be measured.
- **Parity:**
  - stale definition, production `missed_last` (rebuilt as of 4 mornings of 2025-26)
    vs the lab's: 247 agree, 16 lab-only (recent trades), 0 production-only;
  - blend: `project_available` uses the lab's formula, tested;
  - live 10/22 board: two stale players, Sam Merrill 22.4 → 11.2 (p 0.5) and
    Thomas Bryant 5.8 → 1.2 (p 0.2); Cleveland teammates gain; other teams unchanged.

**Result** (2025-26 held out, live state):

| Out list misses | starters announced, board (n; phantoms) | starters announced, active | no five, board | no five, active |
|---|---|---|---|---|
| 0% | 6.385 → **6.319** (36,191; 3,967) | 6.598 → **6.560** | 7.168 → 7.206 | 7.435 → 7.460 |
| 25% | 6.766 → **6.459** (37,297; 5,073) | 6.711 → **6.598** | 8.334 → **7.889** | 7.704 → **7.529** |
| 50% | 7.078 → **6.578** (38,257; 6,033) | 6.845 → **6.650** | 9.219 → **8.442** | 8.008 → **7.626** |

- "Active" scores minutes if he plays. With his expected minutes on the same rows,
  starters announced at 0% is 6.652, worse by construction: a stale player who does
  dress is under-projected. The board is the metric that charges both ways.
- **Starters announced:** better in every cell.
- **No five:** costs 0.04 (board) at 0% missed and gains 0.45–0.78 at 25–50%. Shipped
  on the measured lower bound of about 23%.
- The lab protocol (full inactive list) and the Expected-five weight are unchanged.

**Judgment calls.**
- The board metric replaces "active players only" for this decision.
- The miss process is random; real misses are habitual (two-ways) or fresh
  (Day-To-Day).
- The 0–50% range: 23% unlisted is a floor, and today's feed is only a proxy for the
  10/21 pre-game list.

**Runners-up (critic #6):**
- **The no-five role ignores last game's start.** Using
  `p_start = 0.3·last_start + 0.7·start_rate` and role `= p·ms + (1−p)·mb` takes
  inactives-only 7.396 → 7.366 (2024-25 grid, gap refitted).
- **Diffuse residual in recent-minutes features.** A residual GBM reaches 6.42 /
  7.26; a linear stack recovers only 0.01–0.035.
- **Postponed games in the lab.** 8 games (99 rows, e.g. LAL–CHA 2025-01-09) are
  dated by their make-up date: a tiny leak.
- **Not tried:** ESPN Day-To-Day as an input (no history to fit; it's now being
  logged).

**Null results (critic #6):**
- a position-aware gap: 6.548;
- with/without-teammate history: 6.547;
- prior-game blowout or overtime: bias shifts under 0.7 minutes.

## Cycle 7: the NBA's official injury report

**Baseline** (cycle 6):
- lab 6.550 / 7.396;
- live 6.598 / 7.435;
- board at 25% missed: 6.459 / 7.889.

**Critic #7 (fresh `dfs-model-critic` agent, ~15 min, read-only).** "Before the official
inactive list exists, the build decides who dresses from ESPN's 'Out' list, which
leaves out two-way and G-League players and treats Questionable or Doubtful players as
playing. The NBA's official injury report covers both groups, has public archives
going back years, and is not used."

- **Out list vs modelling:** an Out list worth up to 0.72 board RMSE (no five) and
  0.14 (five announced), against every modelling lever it tested:
  - online residual corrections: 0.004 / 0.015;
  - a five guessed from last game's starters: 0.044 (no five);
  - a residual GBM: 0.14–0.16 of headroom, spread thin.
- **Runners-up:**
  - starters in lopsided games run high (+0.4 to +1.4), but the proxy is worth
    0.01 or less;
  - veterans on a new team in opening week (RMSE 8.4–8.7, n about 100 a season);
  - the guessed-five blend for the no-five state (0.044).

**Found on the way, shipped first (5:15 PM, `0246b174`).** Cycle 6's stale flag read
across seasons, so before a team's opener it flagged whoever sat last April's finale.
On CLE's opener, Sam Merrill and Thomas Bryant were projected at 50% / 20% to dress,
and the 5 PM official report listed neither.
- Players who sat the previous finale dressed in **77%** of 2025-26 openers (n 95)
  and **91%** of 2026-27's (n 92), against 16–22% for stale players in season. The
  2024-25 fit never saw an opener.
- Only a game of this season counts now. Lab, 2025-26 board: no five 7.206 → 7.179;
  five announced 6.319 → 6.315.
- The history was republished before Adam's 6:30 build.

**Building the data.**
- **`edge/nba_injury_report.py`:** fetches a report by date and time (hourly
  `_06PM.pdf` before 2026, 15-minute `_06_00PM.pdf` after), caches it in
  `data/nba_injury_reports/` (a missing one is cached too) and parses it with pypdf's
  layout text.
  - **Row regex:** "Last, First  Status  Reason". The parsed row count equals the
    count of status words on all 8 PDFs checked, 45–169 rows each.
- **Archive:** the report about 60 minutes before every 2024-25 / 2025-26 tip,
  floored to the hour (tips from stats.nba.com `scheduleleaguev2`, `fetch_tips`).
  1,182 slots, all found, 0 errors, 100 MB, about 25 minutes at 1.2 s a request.

**Lab (`cycle7`: `report_status`, `report_preds`, `report_score`, `report_fit`).**
- **Out list:** the report's Out → not dressing; the cut keeps 15.
- **Uncertain** (not announced starters): Doubtful, Questionable, Probable/Available,
  or not listed but stale. Each dresses with p by (status, role ≥ 10).
- **Expected minutes:** `m_i = A_i + Σ_k (1 − p_k)(B_k,i − A_i)`, × p_i for an
  uncertain player himself. No fitted teammate weight; the p's are the 2024-25 dress
  shares.
- **Harness check:** fed the true inactive flags as the "report", it reproduces the
  lab's 6.5505 exactly.

**An evaluation hole found on the way.** In 2025-26, **1,014 player-games listed Out
on the report were flagged active by NBA.com's lineups and played 0 minutes, every
one** (average role 15.5).
- The lab's truth counts them as active, so the lab protocol (6.550 / 7.396) has
  projected them at their role all along. Every earlier table is pessimistic by that
  much.
- It's also why "official report" first looked better than "full inactive list". The
  report knows something the flags don't.

**The fair baseline.** Cycle 6's random-miss replay drew from the same flags, so it
missed these injured players, which ESPN would list. The baseline is therefore
replayed on the same reports, "ESPN-like":
- Out for an injury only (an empty, wrapped reason counts as an injury, which
  flatters the baseline);
- two-ways not listed;
- Doubtful / Questionable / Probable ignored;
- cycle 6's stale p.

**Metric.** The board's squared error per active player-game (`fixed`). Board RMSE
over each method's own rows rewards keeping many cheap phantoms: on it, ESPN-like
looked better, 6.222 vs 6.257.

**Result** (P fitted on 2024-25):

| season | state | ESPN-like Out + cycle-6 hedge (shipped) | official Out only | **official + P by status** | active RMSE: shipped → new |
|---|---|---|---|---|---|
| 2024-25 | five announced | 6.323 (4,302 phantoms) | 6.238 | **6.160** (1,386) | 6.031 → 6.005 |
| 2025-26 | five announced | 6.624 (4,297) | 6.563 | **6.438** (1,894) | 6.298 → 6.261 |
| 2024-25 | no five | 6.967 (4,314) | 6.803 | **6.736** (1,395) | 6.478 → 6.452 |
| 2025-26 | no five | 7.357 (4,321) | 7.304 | **7.077** (1,905) | 6.721 → 6.674 |

**P(dress), 2024-25** (starters known; no five):
- Doubtful 0.20 / 0.36 (n 15 / 14); 0.20 / 0.40 with no five;
- Questionable 0.72 / 0.80 (n 36 / 113); 0.72 / 0.88;
- Probable 0.95 / 0.99; 0.95 / 1.00;
- stale, not listed, 0.45 / 0.51 (n 1,255 / 427); 0.45 / 0.68.

**Decision: ship** (after the 7 PM lock, so Adam's build and the autolog ran on the
previous code). Production:
- `NM.project_uncertain` + `REPORT_P`;
- `build_board` reads `nba_injury_report.latest()` and unions its Out with ESPN and DK;
- the scratch monitor uses the official Out;
- page 12 shows each uncertain player's status and chance;
- `pypdf` added to requirements;
- ESPN alone is the visible fallback.

**Judgment calls and open items.**
- **Lead time:** about 60 minutes before tip. The build is about 30 minutes before the
  first lock, so later games' reports are older.
- **Doubtful:** n is thin.
- **Reasons:** about a quarter of Out rows have an empty reason (wrapped); only the
  baseline uses it.
- **No new model holes:** none of the critic's modelling levers exceeds 0.05.

## Summary of cycles 1-3

Every version scored the same way (`python3 scripts/nba_minutes_lab.py cycles`),
fitted on 2024-25 and scored on 2025-26. The lab protocol assumes NBA.com's inactive
list; the second table is the information state the app actually had.

| version | mode | RMSE | MAE (bias) | opening | rookies | lottery | new team | 1st game back | scratch teammates | promoted | high role 28+ | 2026-27 RMSE / MAE (n) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1 (10/15) | confirmed | 7.758 | 5.74 (-0.1) | 6.21 (-0.2) | 6.30 (-0.2) | 7.32 (-3.7) | 7.11 (+0.5) | 6.59 (+3.0) | 5.80 (-0.1) | 5.71 (+0.4) | 5.22 (+0.2) | 7.39 / 6.14 (85) |
| v1 (10/15) | inactives | 8.146 | 5.97 (-0.1) | 6.54 (-0.2) | 6.75 (-0.3) | 8.61 (-4.8) | 7.50 (+0.3) | 6.96 (+3.3) | 6.05 (-0.1) | 8.74 (-7.2) | 5.31 (+0.9) | 7.27 / 5.93 (85) |
| cycle 0: v2 | confirmed | 6.810 | 5.26 (-0.1) | 5.30 (-0.2) | 5.46 (-0.6) | 5.17 (-1.4) | 6.21 (+0.1) | 5.99 (-0.6) | 5.34 (-0.1) | 5.20 (+1.3) | 4.63 (-0.5) | 7.63 / 6.45 (85) |
| cycle 0: v2 | inactives | 7.649 | 5.78 (-0.1) | 5.41 (-0.2) | 5.99 (-0.7) | 6.10 (-3.0) | 6.27 (+0.6) | 6.34 (+1.0) | 5.88 (-0.1) | 8.31 (-7.2) | 5.10 (-0.9) | 7.25 / 6.04 (85) |
| cycle 1: + rank-gap | confirmed | 6.692 | 5.03 (-0.1) | 5.06 (-0.2) | 5.15 (-0.9) | 4.90 (-1.0) | 6.00 (-0.2) | 5.98 (-0.9) | 5.11 (-0.1) | 5.13 (+0.9) | 4.44 (+0.2) | 7.09 / 5.64 (85) |
| cycle 1: + rank-gap | inactives | 7.474 | 5.49 (-0.1) | 5.11 (-0.2) | 5.52 (-1.5) | 5.36 (-2.6) | 6.10 (+0.3) | 6.27 (+0.6) | 5.59 (-0.1) | 8.03 (-6.8) | 4.73 (+0.1) | 6.26 / 4.69 (85) |
| cycles 2-3: + recency / dropped starter | confirmed | 6.586 | 4.93 (-0.1) | 5.01 (-0.2) | 5.09 (-0.8) | 4.89 (-1.0) | 5.91 (-0.3) | 5.97 (-0.9) | 5.00 (-0.1) | 5.12 (+0.9) | 4.33 (-0.0) | 7.12 / 5.72 (85) |
| cycles 2-3: + recency / dropped starter | inactives | 7.396 | 5.41 (-0.1) | 5.05 (-0.2) | 5.51 (-1.4) | 5.35 (-2.4) | 5.95 (+0.2) | 6.32 (+0.6) | 5.52 (-0.1) | 7.77 (-6.4) | 4.73 (+0.1) | 6.31 / 4.71 (85) |

| version | live state on 2025-26 | mode | RMSE | MAE | bias |
|---|---|---|---|---|---|
| v1 (10/15) | broken feed: phantom history, DK-like Out, cut 13 | confirmed | 8.307 | 6.123 | -1.10 |
| v1 (10/15) | broken feed: phantom history, DK-like Out, cut 13 | inactives | 9.113 | 6.638 | -1.23 |
| cycle 0: v2 | broken feed: phantom history, DK-like Out, cut 13 | confirmed | 7.195 | 5.290 | -0.83 |
| cycle 0: v2 | broken feed: phantom history, DK-like Out, cut 13 | inactives | 8.361 | 6.135 | -1.41 |
| cycle 1: + rank-gap | broken feed: phantom history, DK-like Out, cut 13 | confirmed | 7.074 | 5.193 | -0.89 |
| cycle 1: + rank-gap | broken feed: phantom history, DK-like Out, cut 13 | inactives | 8.317 | 6.138 | -1.41 |
| cycles 2-3: + recency / dropped starter | broken feed: phantom history, DK-like Out, cut 13 | confirmed | 6.954 | 5.062 | -0.80 |
| cycles 2-3: + recency / dropped starter | broken feed: phantom history, DK-like Out, cut 13 | inactives | 8.204 | 6.023 | -1.33 |
| cycle 3: availability fixed | box-score history, injury-list Out, cut 15 | confirmed | 6.634 | 4.942 | -0.51 |
| cycle 3: availability fixed | box-score history, injury-list Out, cut 15 | inactives | 7.435 | 5.437 | -0.55 |

## Cycle 8: report-Out phantoms counted as 0-minute games in history

**The hole** (from a fresh critic). Cycle 7 removed the phantoms (players listed Out
on the official report but flagged active by NBA.com, with 0 minutes) from the board
and from scoring, but not from the role history.
- **Size:** 1,200 such games in 2024-25 and 1,018 in 2025-26 still counted as
  0-minute games in both the lab's `load()` and production's `NM.load_history`.
  Production skipped 2,201.
- **Effect:** each one decayed the player's role while he was hurt and hid the
  5-game gap the return ramp needs. The 2024-26 history also feeds this season's
  last-season prior.

**The fix.** It is a data rule with no new parameters.
- `edge.nba_injury_report.out_at_tip(game_id)` returns who the report listed Out
  60 minutes before tip, from the cache only.
- History skips a game when the player is on that list and played 0 minutes:
  `load(report_skip=True)` in the lab, and the same rule in `NM.load_history`.
- The tonight rows and the scoring are unchanged.

**Result** (scored as cycle 7 does: P(dress) refit on 2024-25, scored on 2025-26,
32,224 active player-games, bias −0.10 before and after):

| state | train 2024-25: fixed (active) | **test 2025-26: fixed** | test 2025-26: active |
|---|---|---|---|
| five announced, before | 6.160 (6.005) | 6.438 | 6.261 |
| five announced, after | 6.111 (5.957) | **6.383** | 6.214 |
| no five, before | 6.736 (6.452) | 7.077 | 6.674 |
| no five, after | 6.577 (6.297) | **6.953** | 6.568 |

**Decision: ship.** It improves held out in both states. Checks:
- `harness verify` passes;
- page 12 runs under AppTest with no errors;
- production skips 2,201 games, the lab 2,218.

**Open items.**
- The history-fitted constants (kappa, `RAMP`, `RECENCY_LAM`, `DROP_Q`, `GAP_COEF`)
  have not been refit on the clean history. Refitting them is the next cheap cycle.
- 2023-24 has no report archive, so its history stays as it was.

**Runner-ups** (2025-26 residuals):
- **Questionable / Doubtful / Probable players who dress:** n 2,536, RMSE 7.19 / 8.37,
  bias −0.4 / −0.7. A minutes-restriction multiplier is untested.
- **Players who dress and play 0 minutes:** n 5,625, bias +3.9 / +4.6. A per-role
  chance of not playing might help; the gain is likely small.
- **No five:** actual starters run −2.3 while bench players run +0.66, but most of
  that is conditioning on who turned out to start. Likely under 0.05.

---

# Target: promoted starters / vacated production -- DK points (from 2026-10-27)

Adam (10/26): a starter out -> a cheap promoted player is a "free square" the field
piles on. 10/26 OKC lost Jalen Williams, Holmgren, Wallace; Ajay Mitchell ($4,600)
scored 42 on our 26.6 projection, 77% owned in the field. Harness:
`python3 scripts/nba_projection_backtest.py` (DK points = minutes x rate, leak-free,
fit 2024-25, test 2025-26), now sliced by **vacated production**: last-10 mean DK
points of teammates inactive tonight who played the team's previous game.

## Cycle P0 -- baseline, shipped model (2026-10-27), test 2025-26

| slice | role | n | MAE | RMSE | bias | rate x ACTUAL min: MAE | bias |
|---|---|---|---|---|---|---|---|
| a. none new out | all | 16194 | 6.771 | 9.152 | -0.02 | 4.451 | +0.04 |
| a. none new out | starter | 6189 | 8.383 | 10.714 | -0.05 | 6.712 | -0.17 |
| b. new out <20 | all | 5875 | 6.958 | 9.242 | -0.08 | 4.506 | -0.02 |
| c. new out 20+ | all | 7242 | 7.532 | 9.863 | -0.37 | 4.745 | -0.40 |
| c. new out 20+ | bench | 4308 | 6.975 | 9.232 | -0.37 | 3.419 | -0.09 |
| c. new out 20+ | starter | 2934 | 8.350 | 10.723 | -0.36 | 6.693 | -0.87 |

Overall: MAE 6.997, corr 0.8057, bias -0.11 (n 29,311).

## Cycle P1 -- the harness ignored the injury report the live build reads (2026-10-27) -- HARNESS FIX, no production change

**Hole (critic):** nba_projection_backtest.py did not apply the official report
(`nba_injury_report.out_at_tip`): 840-1,060 player-games a season are listed Out
but NBA.com-"active" at 0 minutes, and the harness gave them minutes their
teammates should have had -- under-projecting exactly the vacated-production
slice. Production (build_board) already applies the report, so P0 measured a
less-informed model than the one shipped. Ported the live rule (non-starter
listed Out = inactive; his 0-min games out of history). No parameters.

| 2025-26, like-for-like rows (critic) | n | MAE before -> after (paired se) | bias |
|---|---|---|---|
| c. new out 20+ | 8720 | 7.529 -> 7.463 (-0.066, 0.018) | -0.88 -> -0.40 |
| c, bench | 4998 | 6.873 -> 6.770 (-0.103, 0.031) | -1.08 -> -0.45 |
| c, starter | 3722 | 8.410 -> 8.394 (-0.016, 0.013) | -0.61 -> -0.34 |
| overall | 28283 | 6.994 -> 6.946 (-0.047, 0.007) | -0.34 -> -0.13 |

2024-25 (unfitted) agrees: slice c -0.073 (0.025). New baseline (harness output):
overall MAE 6.958, corr 0.8067; slice c starter MAE 8.397, bias -0.33, at ACTUAL
minutes -0.85.

**The usage bump (the hypothesis) is small once measured faithfully:** actual/
predicted points per minute on slice c = 1.03 both seasons; a fitted
1 + k*vacated-share rate multiplier (k 0.20 starters) gains -0.002 MAE with
projected minutes, -0.04 with actual minutes. Not shipped. Minutes on slice c are
calibrated on average (slope ~1.02). So 10/26 Ajay Mitchell (26.6 -> 42) was not
a systematic rate miss the history can see; ownership (77% vs our 4.4%) is the
bigger lever for him.

**Runner-ups:** (1) the rate bump above, -0.04 at actual minutes; (2) slice-c bench
projections too spread (-1.0 below 8 min, +1.2 above 24) -- shrink toward role
mean, 0.02-0.04; (3) bench who dress and play 0 min, ~22% of slice-c bench error.
Fidelity still open: build() lacks since_return, cur_n, cross-season priors and
nba_availability -- rebuild the harness on nba_minutes_lab.load() + points.

---

## Cycle 9 (target: substitutions) -- no-five roles ignore who is out (2026-10-27) -- NOT SHIPPED

Metric: minutes for actives on teams with a scratched regular ("teammate of a
scratch"), promoted starters secondary; fit 2024-25, test 2025-26, both states.

**Baseline (shipped, `report`), 2025-26 MAE (bias):** confirmed: all 4.97 (-0.1),
scratch-teammates 5.04 (-0.1), promoted starters 5.14 (+0.3); inactives: all 5.53,
scratch-teammates 5.64 (-0.1), promoted starters 7.93 (**-6.6**). RMSE all 6.707 / 7.753,
n 32,224.

**Hole (critic):** without a five, a player's role blends starter/bench by season
start rate alone, so whoever inherits a scratched starter's spot keeps his bench
role. Promoted starters are 6% of the slice and 10% of its squared error; the
five-known state shows no redistribution bias. 2024-25 shows the same thing (-6.83).

**Fix tested:** logistic P(start | start rate, started last game, position-group vacated
start rate x (1-s), open starts x (1-s), role/36), fit by Newton on 2024-25
(w = [-5.54, 4.04, 1.77, 1.19, 1.18, 2.94]). The role is rescaled by a P-weighted
starter/bench mix and `GAP_COEF["inactives"]` is refit. Script:
scripts/critic/nba_subs_pstart_fix.py.

| 2025-26, inactives | n | RMSE | MAE | bias |
|---|---|---|---|---|
| teammate of a scratch | 29,228 | 7.874 -> 7.829 (dMSE -0.72, se 0.18) | 5.637 -> 5.689 | = |
| promoted starter | 1,857 | 9.98 -> 9.83 (dMSE -2.9, se 0.95) | 7.93 -> 7.74 | -6.58 -> -5.95 |
| all | 32,224 | 7.753 -> 7.710 | 5.530 -> 5.589 | |

**Decision: not shipped.**
- RMSE improves significantly, but MAE gets worse: the probability blend pulls
  everyone toward the middle.
- It was not scored in the official-report state (`report_preds`), the path
  production runs.
- Production would see only about 30% of the gain, because the Expected-five blend
  weights the no-five model at 0.3.
- Most of the -6 bias is still there.

**Next (the likely real fix):** carry the start probability into the 240-minute
step (`_rank_gap`) as a per-cell starting term, refit on 2024-25, and score both
states plus `report_preds`.

**Runner-ups:**
- Bench ranks 7-11 under-projected by 0.3-1.1 in both states, about 30% of the
  slice's squared error (estimated 0.02-0.04 RMSE).
- Top-3 roles over by 0.4-0.7 when a teammate is out (about 0.01).

## Cycle 10 (target: substitutions) -- P(start) inside the 240 step (2026-10-27) -- NOT SHIPPED

Follows up cycle 9. When there is no announced five, the rank-gap step gains a
per-cell third coefficient on u = (P(start) - start rate) x (starting role - bench
role). P(start) is cycle 9's logistic, computed from tonight's inactive list. The
official-report replay changes who is out, so u moves with it. Everything is fitted
on 2024-25:
- P(start) w = [-5.54, 4.04, 1.77, 1.19, 1.18, 2.94], log loss 0.239;
- the coefficients on u come out at about 0.3-0.7 in most cells.

Code: `python3 scripts/nba_minutes_lab.py cycle10` (`pstart_feats`, `pstart_game`,
`fit_pstart`, prm `pstart_w`; off unless set). A control refits the inactives gap on
2024-25 with shipped roles, to separate the refit from the new term.

| 2025-26 held out | shipped | gap refit only | + P(start) term |
|---|---|---|---|
| five known: RMSE all / MAE | 6.707 / 4.97 | = | = (by construction) |
| no five: RMSE all | 7.753 | 7.719 | 7.709 |
| no five: MAE all | 5.53 | 5.60 | 5.59 |
| no five: teammates of a scratch MAE (bias) | 5.64 (-0.1) | 5.70 (-0.1) | 5.69 (-0.1) |
| no five: promoted starters MAE (bias) | 7.93 (-6.6) | 7.92 (-6.6) | 7.80 (-6.3) |
| official report, no five: active RMSE (board) | 6.568 (6.953) | not run | **6.615 (7.002)** |
| official report, five known | 6.214 (6.383) | -- | = |

**Decision: not shipped.**
- The new term buys only 0.010 RMSE over the plain refit, and MAE is worse than the
  shipped model.
- In the official-report state, the one production runs, it is worse by 0.047.
- The promoted-starter shortfall barely moves (-6.6 -> -6.3).

The rank step cannot express "this bench player is starting tonight": the fitted
coefficient is about 0.5 of the uplift, and the variance it adds costs more than the
mean shift recovers.

**What is left:**
- The −6 bias on promoted starters before the five is announced is a classification
  problem: who starts. It isn't an allocation problem.
- Production already blends in NBA.com's Expected five at weight 0.7, and the Expected
  five is right about 75% of the time. That is a better signal than this logistic.
- So the lever is the Expected-five weight when a regular starter is out (a promoted
  name in the Expected five), not the no-five model.
- Separately, the stale shipped inactives gap coefficients: a plain refit gives RMSE
  -0.034 but MAE +0.07. That is the same trade-off, and also not shipped.

## Cycle 11 (target: blowout potential) -- rank x |expected margin| correction (2026-10-27) -- NOT SHIPPED

The metric was minutes RMSE, MAE and bias on 2025-26, held out. It was sliced by
pre-game |expected margin| and by projected-minutes rank (top 5 / 6-9 / 10+), in both
information states: 32,224 player-games, 1,230 games.

**Hole (critic):** the model does mis-handle blowouts, but almost none of the error
can be known before lock.
- In games that end 21+ points apart, the top 5 run +3.2 min high and ranks 10+ run
  −3.3 min low.
- Bias by pre-game margin is small. In 9+ point expected games the top 5 run +1.1,
  ranks 6-9 −0.4 and ranks 10+ −1.2 (five known); with no five, +1.2 / −0.6 / −1.1.
  Within 3 points the same figures are +0.2 / −0.1 / −0.4.
- The size of the expected margin tracks the size of the final margin at only
  0.12-0.145. A perfect spread would reach about 0.20 (simulated).

**Fix tested:** `pred += a_k + b_k·|exp_margin|` after the rank-gap step, with k = the
projected rank (13+ pooled), least squares on 2024-25. The slopes come out at about
+0.09 for rank 1 down to −0.07 for rank 13+. It is scored against a rank-only control.
Scripts are in the session scratchpad (critic-nba-11, not kept).

| 2025-26 held out | shipped | rank-only control | + blowout term | final margin known (ceiling) |
|---|---|---|---|---|
| five known RMSE | 6.7075 | 6.6983 | 6.6945 (−0.0038, se 0.0019) | about −0.15 |
| no five RMSE | 7.7527 | 7.7271 | 7.7239 (−0.0032, se 0.0020) | about −0.14 |
| five known MAE | 4.97 | 5.003 | 4.996 | |

**Decision: not shipped.**
- The gain is about 0.004 RMSE, below resolution (the same bar cycle 6 used).
- Even a perfect market spread caps it near −0.009.
- `data/odds.db` has NBA spreads only from 2026-10-20 on, so a spread version cannot be
  tested on 2025-26 yet.
- This confirms the earlier "blowout adjustment not kept" with the reason: the pre-game
  information is too weak; the model isn't missing a term.

**Runner-ups:**
- A rank-only bias correction: no five −0.026 RMSE, but MAE 5.53 → 5.60. That is the
  same trade-off as cycles 9-10.
- No `exp_margin` in each team's first ~5 games (about 2,100 player-games a season).
- The mid-game blowout error (about 0.14 RMSE) belongs in the simulator's spread of
  outcomes for GPP (top 5 −3 min / deep bench +3 min in 21+ point games), not the mean.

**Revisit:** with logged 2026-27 spreads once they cover about 500 games.

## Cycle 12 (target: promoted starters, no five posted) -- the slice is selection, not model (2026-10-28) -- NOT SHIPPED

Baseline (`report`, inactives): promoted starter MAE 8.25 / bias -7.0 (2024-25), 7.93 / -6.6
(2025-26), 9.41 / -8.4 (2026-27, n 55); overall RMSE 7.746 / 7.753 / 6.952. Confirmed: +0.3.

**Critic:** the slice is chosen by the OUTCOME (he started, start rate < 0.5). A perfectly
calibrated P(start) from pre-five information still shows -6.98 (2024-25) / -6.62 (2025-26)
on it, vs shipped -6.80 / -6.30 on the same rows: the bias is the floor, not a hole. Sliced by
what is known before the five the model is near unbiased (start rate < 0.5 and started last
game: n 1,514, 49% start again, bias -0.75; P >= 0.5: n 909, bias -0.23). 60% of the slice did
not start his last game (bias -7.76, mean P 0.18): surprise starts history can't see.
A hard predicted five (top 5 by P, then confirmed mode): five right 88%, but 2025-26 RMSE
7.753 -> 8.067, promoted MAE 7.93 -> 8.41.

**Fix tried:** replacement memory (`mem` = share of earlier team games with the same regular
starter out in which he started) into a calibrated P, pred += 0.309 (P - start rate)(ms - mb),
zero-sum to 240; fitted on 2024-25. Held out 2025-26: RMSE 7.753 -> 7.748, MAE 5.530 -> 5.522,
promoted MAE 7.93 -> 7.80; 2026-27 RMSE 6.952 -> 6.974 (worse). Below resolution (production
weights the no-five state 0.3) -> **not shipped.**

**What would move it:** news between the injury report and the posted five (NBA.com's
Expected five, beat reports). The data branch's `data/nba_lineups_snapshot.json` commit
history IS a pre-lock log of Expected fives since the 2026-27 opener -- the next harness
should replay it (as nba_ownership_lab.py does). Runner-up: starters overall -1.95 in
inactives; recheck on pre-five slices.

## Cycle 13 (target: the Expected five, replayed) -- a parse bug wiped posted fives (2026-10-28) -- SHIPPED (bug fix); EXPECTED_W unchanged

New harness `scripts/nba_expected_five_lab.py`: replays every commit of
`data/nba_lineups_snapshot.json` on the data branch (the five as it stood at any moment)
against the day's final file, by hours before tip. Regular season 10/20-10/27, train
before 10/25 (72 team-games) / test (64):

| hours before tip | five exactly right train / test | wrong starters per 5, train / test |
|---|---|---|
| 6h+ | 78% / 89% | 5.3% / 2.8% |
| 3-6h | 76% / 81% | 5.2% / 4.4% |
| 1.5-3h | 73% / 82% | 5.9% / 4.1% |
| 45-90m | 81% / 82% | 4.0% / 4.0% |
| <45m | 99% / 100% (confirmed) | 0.3% / 0.0% |

A five misses a starter on ~11-24% of team-games at any lead -- inside the 10-30% range
EXPECTED_W = 0.70 was chosen over by minimax regret (cycle 4): **no change**. Early
listings are as good as late ones (Paul Reed was Expected 14.0 h before tip on 10/27; Ajay
Mitchell was not listed until confirmed 0.5 h before, 10/26).

**Bug found:** ~1 h before tip NBA.com adds the active roster and each starter appears
TWICE (starter row with position; roster row without). `nba_lineups.parse` kept both and
`tonight_status` kept the LAST, so the five vanished in the final hour: **39 of 154
team-boards (25%) on logged builds since 10/20 had a posted five but projected as if none**
(10/27 Main: DET, NYK, SAS, SAC). Fixed: one row per player, the starter row wins, inactive
if either says so. Day files used by the backtests have no duplicates (0 of 497), so no
held-out number moves. Replay of the 10/27 Main board (7:07 PM snapshot): Paul Reed 20.6 ->
24.7 min, 24.5 -> 31.6 pts, own 13 -> 75% (field 60-83%, actual 43.2).

## Cycle 14 -- "Musk algorithm" review: question, delete, simplify, accelerate, automate (2026-10-28)

**Delete** (each part switched back to v1, held out 2025-26, RMSE change confirmed / no five):
start/bench split +0.55 / 0; EWMA role +0.37 / +0.21; last-season prior (kappa 0) +0.22 / +0.09;
rank-gap 240 +0.15 / +0.05; drop cut +0.14 / 0; return ramp +0.07 / +0.13; recency boost
+0.07 / +0.07; start/bench means +0.03 / 0; rookie prior +0.005 / +0.01; **starter tilt and
v2's power 240 share: 0.000 / 0.000 -- unreachable under rank-gap (a full team returns from
it first). DELETED** with the OFF salary nudge and the ALLOC switch; production output
identical on 3,000 team-games (1,500 real 2025-26 + short-handed variants). Redistribution
(ROLE_POW_V2 / POS_W_V2) also scored 0.000 but is the only allocation for a team with < 8
active, so it stays. project_v1 stays (the lab's v1 baseline, nba_minutes_backtest.py).

**Simplify:** the rank-gap cells were last fitted at cycle 5. `refit-gap` (new) refits them on
2024-25 against today's roles. Held out 2025-26: RMSE confirmed 6.708 -> 6.706, no five 7.753 ->
7.719, but MAE 4.970 -> 4.998 / 5.530 -> 5.596 and the live replay confirmed 6.751 -> 6.756.
**Not written** (both states must improve). Coarser cells (8 ranks, 36 vs 56) matched on test
but lost on train (6.541 vs 6.532); 13 stays. prior_kappa: train keeps 1.0.

**Accelerate:** `load()` now caches D in data/cache/nba_minutes_lab/ keyed on every input file
(box scores, lineups, injury reports) and the lab + model source: 325 s -> 1 s, so a lab
cycle is ~10 s, not ~5.5 min.

**Automate:** after any change to the roles, run `nba_minutes_lab.py refit-gap` (it prints
both states and the live replay) -- the stale cells went unnoticed for 8 cycles.

## Cycle 15 -- the lab scored ~1,000 "phantoms" a season as actives; rank-gap refit on the clean protocol (2026-10-28) -- SHIPPED

**Hole (critic):** production has dropped a NON-starter listed Out on the official report 60
min before tip since cycle 7, but the lab's `run()` / `fit_gap()` still scored those rows as
actives (NBA.com flags them active; 1,178 in 2024-25, 1,006 in 2025-26; 5 played). On the
shipped model they were RMSE 21.7 / bias +19.8 with no five and 24.7% of 2025-26's no-five
squared error (29.5% in 2024-25); their teammates ran -0.83. The rank-gap cells were fitted
on that mix, which is why cycle 14's refit lost MAE.

**Fix:** (1) protocol: `load_fresh()` marks them inactive (D["phantoms_out"]) -- the
production rule. New baseline (shipped cells): 2025-26 confirmed 6.279 / 4.705, no five
6.612 / 4.918. Every earlier "not shipped" verdict (cycles 9, 10, 12) was scored with phantoms
in -- re-score before treating them as settled. (2) `refit-gap --write` on 2024-25.

| RMSE / MAE | train 2024-25 | **test 2025-26** | 2026-27 (n 1,538) |
|---|---|---|---|
| confirmed | 6.056/4.516 -> 6.048/4.516 | **6.279/4.705 -> 6.273/4.703** | 6.446/4.946 -> 6.446/4.934 |
| no five | 6.390/4.747 -> 6.333/4.670 | **6.612/4.918 -> 6.579/4.870** | 6.952/5.132 -> 6.972/5.122 |
| live replay (role10 Out, cut 15) confirmed / no five | | 6.341 -> 6.336 / 6.644 -> 6.634 | |

Promoted starters, no five: MAE 7.00 -> 6.86, bias -5.14 -> -4.92. 2026-27 no-five RMSE +0.020
is not like-for-like (no report archive for 2026-27 yet: its phantoms are still in).
Parity: shipped module reproduces the refit exactly (6.2733 / 6.5788).

**Runner-ups:** (1) Questionable / Doubtful bench players who DO dress run high (2025-26 no
five n 270, bias +10.0; confirmed +4.4) -- they're on a minutes cap; test a role multiplier
by status. (2) "Available" (back from injury) run low (n 1,821, bias -0.56 / -0.82) -- the
return ramp may cut too much for players cleared to play.

## Re-scored on the clean protocol (cycle 15's phantoms-out), 2026-10-28

2025-26 held out, no five, RMSE / MAE (shipped = cycle 15: 6.579 / 4.870):
- **Cycle 9** (P(start) role mix + gap refit): 6.584 / 4.869, promoted MAE 6.86 -> 6.81 -- worse RMSE; stands.
- **Cycle 10** (P(start) term in the 240 step): lab 6.572 / 4.86; official-report active
  6.641 -> 6.638, board 7.050 -> 7.048; 2026-27 6.972 -> 7.001 -- noise-level; stands.
- **Cycle 12** (replacement memory, zero-sum; c refit 0.285): 6.575 / 4.859, promoted MAE
  6.86 -> 6.76 -- consistent but ~half the MSE se (~0.1); stands.
The phantoms did not flip any verdict.

## Cycle 16 -- Doubtful / Questionable players who dress are on a minutes limit (2026-10-28) -- SHIPPED

From cycle 15's runner-up. Cycle 7 prices only WHETHER an uncertain player dresses
(P(dress) x minutes); given he dresses the model gave full minutes. Official-report replay
(`report_preds`, phantoms out), held out 2025-26 dressed players: no five Doubtful n 39 bias
+21.0, Questionable n 378 +4.2; confirmed +11.7 / +4.0.

**Fix:** `NM.REPORT_K` -- minutes given he dresses x K[category], fitted on 2024-25 (OLS through
0, dressed players, pooled over role size): confirmed Doubtful 0.0 (n 8), Questionable 0.688
(n 116); no five Doubtful 0.202 (n 9), Questionable 0.766 (n 191). Probable / stale and an
8-cell per-role version were also fitted: Probable / stale ~1.0 with no gain (cycle 15's runner-up
"Available run low" -- not supported, -0.11 -> -0.75 on small-role Probables), the 8-cell
version had cells on n 2-6; the pooled 4 shipped. Lab: `report_score(P, p, K)`.

| 2025-26 held out, official-report replay | shipped | + REPORT_K |
|---|---|---|
| confirmed: active RMSE / MAE | 6.301 / 4.724 | **6.271 / 4.707** |
| confirmed: board RMSE | 6.291 | 6.269 |
| no five: active RMSE / MAE | 6.641 / 4.919 | **6.575 / 4.909** |
| no five: board RMSE | 6.844 | 6.795 |
| no five Doubtful dressers (n 39) bias / RMSE | +21.0 / 22.0 | +4.3 / 4.4 |
| no five Questionable dressers (n 378) bias / RMSE | +4.2 / 13.1 | -1.6 / 12.1 |

Train 2024-25: confirmed 6.063 -> 6.052, no five 6.369 -> 6.346. Production == lab
(`project_uncertain` x REPORT_K; parity 6.2713 / 6.5748). Doubtful K rests on n 8-9 train
(39 held out agree); minutes K removes are not re-shared to teammates (as in the lab).
Next: re-share them through the 240 step and test.

## Cycle 17 -- re-share a limited player's minutes through the 240 step (2026-10-28) -- SHIPPED

Cycle 16 multiplied a dressed Doubtful / Questionable player's minutes by K and let the rest
vanish (team below 240, bias -0.24 / -0.34 held out). Now a third scenario per such player:
L_k = he dresses with role x K, and the rank-gap step re-shares the rest. Expected minutes
m_i = A_i + sum_k (1-p_k)(B_k,i - A_i) + sum_{k limited} p_k (L_k,i - A_i); his own:
p_k (L_k,k + sits terms). Production: `project(..)` honours a row's `role_scale`;
`project_uncertain` builds L_k. Script: scripts/critic/nba_reshare_fix.py.
K refit under re-share on 2024-25 (Questionable x0.8-1.2): confirmed x1.1 -> 0.757
(6.0524 best), no five x1.0 (0.766). Doubtful kept.

| 2025-26 held out, official-report replay | cycle 16 | cycle 17 |
|---|---|---|
| confirmed active RMSE / MAE | 6.2713 / 4.7070 | **6.2704 / 4.7068** |
| confirmed board / bias | 6.2685 / -0.235 | 6.2670 / -0.196 |
| no five active RMSE / MAE | 6.5748 / 4.9089 | **6.5672 / 4.9033** |
| no five board / bias | 6.7948 / -0.337 | 6.7860 / -0.254 |

Judgment call: train confirmed is 0.0008 worse than cycle 16 (6.0516 -> 6.0524); held out both
states improve on RMSE and MAE, and the change removes a structural loss (minutes leaving the
team), so it shipped. The confirmed gain is at resolution; the no-five gain is ~0.008.

## Cycle 18 (2026-10-28): the role trails the last 3 games — SHIPPED

**Critic's hole.** After the full shipped pipeline (report replay, rank-gap 240
step, REPORT_K re-share), the remaining error still tracks how far a player's
last 3 games this season sit from his projection (DNP zeros counted):
- the slope of actual minus projection on that gap is 0.13 in both 2024-25 and
  2025-26;
- last-1, back-to-back and April add nothing beyond it.

**What I changed from the critic's spec**
- **Starter flag before the five posts.** The critic split by the actual start in
  both states, which leaks in the no-five state. That state now uses the pre-lock
  `start_rate ≥ 0.5`. Train-fit c moved 0.042/0.093 → 0.144/0.051.
- **Zero-sum group.** The critic zero-summed over rows that were known to have
  dressed. Production can't know that, so the shipped version zero-sums over every
  kept player with m > 0, and an uncertain player is corrected on m/p.

**Variants tried, all fitted on 2024-25**
- **DNP zeros left out of the last-3:** confirmed train MAE got worse
  (4.5228 → 4.5348). That's the reason for counting zeros; the decision was made
  on train.
- **Skipping uncertain players or capping |x| at 12:** smaller gains on train.

**Coefficients shipped:** `RECENT_C` confirmed (0.080, 0.168), no five
(0.144, 0.051), as (starter, bench), least squares through zero on 2024-25.

**Results:** report replay, production-faithful
(`scripts/critic/nba_recency_fix/score.py`), RMSE / MAE / bias.

| | 2024-25 (fit) before → after | 2025-26 (held out) before → after |
|---|---|---|
| confirmed | 6.0524/4.5228/−0.148 → 6.0091/4.4942/−0.154 | 6.2704/4.7069/−0.196 → **6.2142/4.6673/−0.220** |
| no five | 6.3433/4.6975/−0.135 → 6.3339/4.6863/−0.134 | 6.5672/4.9033/−0.254 → **6.5599/4.8916/−0.257** |

- Both states improve on RMSE and MAE, train and test.
- The confirmed gain (−0.056 RMSE) is larger than cycles 15–17 combined.

**Parity.** Production `recency` is unit-tested on its formula. It reads the last 3
from the board history plus `cur_n`, while the lab reads dressed rows this season.
Both drop the report-Out phantoms, but they weren't compared row by row on real
boards.

**Runner-ups (critic)**
1. **No-five starters run low** (−1.37): selection; test through P(start).
2. **April:** RMSE ~7 against ~6.2 the rest of the season. An April dummy adds
   nothing after recency; a tank/clinch flag is untested.
3. **30+ minute projections drifted high in 2025-26** (+0.39): watch it in 2026-27.

**Cycle 18 follow-up: trades and season boundaries (Adam, 2026-10-28)**
- **Season boundaries already hold.** The last-3 window is keyed on the NBA season,
  not the calendar year: the lab uses `season`, production `cur_n`. October games
  never reach back to the previous spring.
- **Trades.** Restricting the last 3 to games with his current team changes 272–290
  rows a season (the first games after a trade). Results, RMSE/MAE, any team → same
  team only:

  | | 2024-25 (train) | 2025-26 (held out) |
  |---|---|---|
  | confirmed | 6.0091/4.4942 → 6.0091/4.4936 | 6.2142/4.6673 → 6.2145/4.6666 |
  | no five | 6.3339/4.6863 → 6.3331/4.6856 | 6.5599/4.8916 → 6.5593/4.8911 |

  Not shipped: held-out confirmed RMSE is 0.0003 worse, and every move is at
  resolution. A traded player's new role is handled by the existing new-team
  handling (`nba_minutes.py`, "new team, first 5 games").

## Cycle 19 (2026-10-28, target: DK points when a star sits) — NOT SHIPPED

**Question (Adam).** When a superstar is a fresh scratch (Dončić, Wembanyama), the
minutes model re-shares his minutes at the teammates' own DK points per minute. Is
part of his excess production (his rate over theirs) made up on top of that?

**Test.** `scripts/critic/nba_star_out.py`, one pooled parameter:
- E = Σ over fresh outs (played the team's previous game) whose last-10 mean DK
  points ≥ T of m_s × max(0, rate_s − tonight's team rate).
- Bump: α × E × share of base points, given to all actives or to starters only.
- α by least squares; T and the recipients chosen by MAE on 2024-25.
- Test season 2025-26; dates are the bootstrap unit.

**Results**
- **Train (2024-25):** best fit T = 45 pts, starters only, α = +0.551. MAE
  6.8530 → 6.8504; every T fits α at +0.42 to +0.65.
- **Held out (2025-26), n 28,317 player-games / 155 dates:**

  | | n | MAE before → after | bias before → after |
  |---|---|---|---|
  | all rows | 28,317 | 6.9605 → 6.9606 (CI −0.003…+0.003) | |
  | star-out rows | 415 | 9.066 → 9.068 (CI ±0.21) | −0.86 → **+1.40** |

**Verdict: the effect is real, but its size can't be pinned down yet. Not shipped.**
- Starters are under-projected by 0.86 pts when a 45+ star sits.
- 2025-26's own α is about +0.30 at every T, roughly half of the 2024-25 fit, so the
  trained value overshoots (bias +1.40).
- Only 415 star-out starter rows a season (about 80 team-games), and MAE doesn't
  move.
- Choosing a smaller α by looking at 2025-26 would be fitting on the test season.

**Next.**
- Refit pooled on 2024-25 + 2025-26 and judge on 2026-27 star-outs once about
  30 team-games exist.
- Use the on/off table's per-star shrinkage as a prior for α.

## Cycle 20 (2026-10-29, target: DK-points bias by salary tier) — NOT SHIPPED

**Question.** scripts/pool_coverage.py (10/29) reported $8.5k+ under-projected by
2.51 pts (n 52, 10 slates) and sub-$4k over by 2.07. Is there a tier bias to fix?

**Harness.** `scripts/critic/nba_tier_bias.py`. In the lab (leak-free, train
2024-25, held out 2025-26) the tier is the projection, because past seasons have no
salaries. Live 2026-27 (last pre-lock row per draft group vs box score, 9 dates) is
tiered by real DK salary. Bias = proj − actual.

**Baseline**

| tier | lab 2024-25 | lab 2025-26 | live 2026-27 (salary) |
|---|---|---|---|
| top (40+ / $8.5k+) | −0.78 (2,090) | −0.19 (1,694) | +0.26 (64) |
| 30-40 / $6.5-8.5k | −0.19 | −0.10 | −0.48 (223) |
| 20-30 / $5-6.5k | +0.33 | +0.31 | +0.81 (298) |
| 15-20 / $4-5k | +0.12 | +0.31 | +0.47 (346) |
| <15 / <$4k | −0.30 | −0.54 | −0.08 (505) |
| all MAE | 6.8525 | 6.9605 | 7.4254 (1,436) |

**The 2.5-pt finding was selection.** pool_coverage looked only at players in
winning lineups, which conditions on the outcome. In the lab, 40+ projected players
whose actual was at or above the median show bias −9.7 / −9.0 (train / held out),
against −0.78 / −0.19 for all of them.

**Critic's hole (small).** Projections are pulled too far from recent DK output:
proj − L10 DK mean is −2.55 for L10 40+ and +1.10 for L10 <15. Held-out bias by L10
tier: 40+ −0.39 (n 1,977), 30-40 −0.40 (3,776), <15 −0.16 (11,960). A linear
recalibration of the projection (slope 0.996) makes MAE worse (+0.022 held out).

**Fix tested.** proj' = proj + w·(L10_DK − proj), with w fitted on 2024-25 = 0.10
(train MAE 6.8525 → 6.8478).

| held out 2025-26 | before → after |
|---|---|
| MAE (n 28,317 / 155 dates) | 6.9605 → 6.9540, Δ −0.0065, CI −0.0127…+0.00002 |
| bias, L10 40+ | −0.39 → −0.13 |
| bias, L10 30-40 | −0.40 → −0.27 |
| bias, L10 <15 | −0.16 → −0.27 |

**Decision: not shipped.**
- The CI touches zero.
- RMSE was not checked.
- Whether L10 includes zero-minute entries (injury returns) is unverified.
- It patches over the shrinkage in `NP.rate` / `NM.project` instead of tuning that
  shrinkage directly.

Next: tune the rate shrink (`RATE_MIN_MINUTES`) on train, with both MAE and RMSE as
guardrails; check on 2026-27 at around 30 dates.

**Runner-ups.**
1. Errors are right-skewed, so MAE penalises any upward shift. Use RMSE as a second
   guardrail for bias fixes.
2. <15 tier −0.54 held out: rare bench blowup minutes.
3. Diagnostics of the "winners' players" kind should report bias over the whole
   board by salary.

## Cycle 21 (2026-10-29, target: AVAILABILITY -- out vs playing, starter/role at build) — FIX NOT SHIPPED, LOGGING SHIPPED

First cycle of the availability critic (.claude/agents/dfs-availability-critic.md).

**Cycle 0 availability baseline** (shipped board, data/dfs_proj_log_nba.csv vs NBA.com gamelog;
10-15/16 preseason excluded):

| | TRAIN 10-20..24 (5 slates / 936 players) | TEST 10-25..28 (4 / 842) |
|---|---|---|
| false active, all (proj > 0, 0 min) | 188 / 1,193 proj pts / 7 lineup slots | 153 / 562 pts / 0 slots |
| false active, proj 15+ min | 26 (24 on 10-21: deep-bench DNP-CD, e.g. Reese 22 min "Confirmed", 4 slots) | **0** |
| false active already inactive in our lineup log | 5 | 10 |
| missed role (10+ min over proj, 5x+ value, 10+ pts over) | 23 / 415 pts | 19 / 381 pts (15 at <= $4k: 7.8x realised vs 2.2x projected) |
| ...from a teammate scratch known before lock | -- | ~7 / ~120 pts (~30 per slate); the rest in-game injuries / blowouts / OT |
| starter flag vs last confirmed five | not logged | P 0.97, R 0.98 (107 TP / 3 FP / 2 FN) |
| lead time | not enough data (news-lead log from 10-29; lineup log stopped ~7:12 PM ET on 10-21) | |

**Critic's hole:** a known scratch's minutes don't go to the same-position BENCH backup
(`promoted` only fires for announced starters); 10-28 Capela 10.6 proj -> 22 min with Sengun +
Adams out, Coulibaly 9.8 -> 27, Jackson-Davis 6.3 -> 20, Clarkson 4.7 -> 17.

**Test (scripts/nba_availability_pos_test.py):** on top of rank_gap, bench players (outside the top
5 roles) whose position overlaps a scratched regular get k x his minutes (shared by role among
equals), taken back from the others by minutes. k fitted on 2024-25 ONLY -> **k = 0**; every k > 0
is worse in both information states and every season (RMSE, all active player-games):

| k | confirmed 24-25 / 25-26 / 26-27 | inactives 24-25 / 25-26 / 26-27 |
|---|---|---|
| 0 (shipped) | 6.048 / 6.273 / 6.382 | 6.333 / 6.579 / 6.903 |
| 0.05 | 6.081 / 6.291 / 6.374 | 6.446 / 6.652 / 6.908 |
| 0.1 | 6.195 / 6.397 / 6.413 | 6.845 / 6.989 / 7.018 |
| 0.2 | 6.650 / 6.851 / 6.625 | 8.226 / 8.253 / 7.518 |

Teammates of a scratch already run slightly OVER (bias -0.10, n ~28k/season): rank_gap's bench
slopes absorb the minutes on average, and the 10-28 cases are the nights the backup was the
one who got them (selection on outcome, 4 slates / ~7 events vs 56k player-games). Not shipped.
A per-team "who backs up whom" (minutes in prior games with that starter out) is the untested
refinement; it needs the with/without history, not a position match.

**Shipped (the critic's logging hole):** the board log kept only a draft group's latest build and a
rebuild after lock could replace it, so the at-lock state and lead time were unrecoverable.
edge/dfs_forward_log.write now also appends every build, stamped `built_at` (UTC), to
data/dfs_proj_history_<sport>.csv (every sport; published on the data branch). Next availability
cycle: grade the last build before lock and measure lead time against nba_news_lead_log.csv.

Runner-ups: deep-bench / two-way players projected 15-22 min as "Confirmed" (10-21 only, 4 lineup
slots; cap for < 2 games played); lineup-log gaps (poll every 2-5 min until the last lock).

## Cycle 22 (2026-10-29, Adam: use CourtIQ for projections) — NOT SHIPPED

Adam's point: when a C sits, the backup C may not get his place -- the PF slides over or a
PF-type takes the minutes -- so use what THIS team did (pages/14 CourtIQ), not position labels.
scripts/nba_courtiq_backtest.py: for each teammate Y of a regular X out tonight, from this team's
earlier games only (no leak; a day enters history after the whole day), delta = Y's mean
without X - with X, shrunk n_wo / (n_wo + K), summed over tonight's scratches; projection =
shipped + alpha x delta, team put back to its shipped total. 50,421 teammate rows have a history.
Fitted on 2024-25 only; RMSE all active player-games (teammates of a scratch in brackets).

| variant | state | train pick | 2024-25 (fit) | 2025-26 held out | 2026-27 (n 1,658) |
|---|---|---|---|---|---|
| shipped | confirmed | -- | 6.048 (6.086) | 6.273 (6.327) | 6.382 (6.407) |
| raw minutes delta | confirmed | alpha 0 (0.25/K10: 6.112) | worse at every alpha | 6.345 at 0.25/K10 | 6.418 |
| residual delta (Y's past model error without X - with X) | confirmed | alpha 0 (0.25/K10: 6.055) | worse at every alpha | 6.274 | 6.388 |
| shipped | inactives | -- | 6.333 (6.409) | 6.579 (6.663) | 6.903 (6.877) |
| raw minutes delta | inactives | alpha 0 (0.25/K10: 6.369) | worse | 6.615 | 6.912 |
| residual delta | inactives | **alpha 0.15, K 10** | 6.320 (6.395) | **6.563 (6.645)** | 6.903 (6.877) |

Raw with/without minutes double count what the role-rank re-share already moves (the model
already gives a scratch's minutes away). Using the model's own past errors fixes that, and
then the team-specific pattern adds ~nothing once starters are confirmed (the announced five
already says who slid where) and -0.016 RMSE (0.25%) before they are, flat on 2026-27.
Not shipped: production would need a nightly table of the model's own past errors per
(team, X, Y) for a 0.25% gain that only applies until the five is confirmed (~30 min before
tip). CourtIQ stays a research view: for a lineup decision, the confirmed five is the signal.

## Cycle 21-22 follow-ups (2026-10-29) — NOTHING SHIPPED

**Opening-night deep bench (cycle 21 runner-up).** 10-21's 15-22 min false actives (Reese 22.4,
Slawson 19.8, Small 16.8; 4 lineup slots) were not two-way flukes: their roles came from LAST
season's final games (Reese 24-39 min over his last 8 -- the April shutdown stretch) while this
preseason he played 9 / 6 / 3; preseason only counts for a player with < 5 NBA games.
scripts/nba_preseason_blend_test.py: while season_games < N, role = (1-W) role + W x preseason
minutes x 0.66, for players with 2+ preseason games. Fitted on 2024-25 -> **W = 0**: worse at every
(W, N) in both states, including the season-games-0 slice it targets (confirmed, shipped -> best
W 0.25 N 1: 2024-25 6.750 -> 6.947 (n 576), held out 2025-26 8.165 -> 8.404 (n 589), 2026-27
7.233 -> 7.293 (n 448)). Last season's minutes beat preseason on average; Reese is the exception.
A min-salary rule (DK priced all three at ~$3,000) cannot be fitted held out: cached DK salaries
exist for only a few nights and the only opening night among them is 10-21 itself. Revisit at the
2027-28 opener with this season's logged salaries (dfs_proj_history_nba.csv now keeps them).

**Lineup-log "gap" (cycle 21 runner-up 2) was a misread.** data/nba_lineup_log.csv runs to 10:28 PM
ET on 10-21 (2,834 rows) and 10:29 PM on 10-28; it logs on CHANGE, and the scratch timer already
polls every 2 min from 4 h before the first tip to 10 min after the last. No change needed.

## Cycle 23 (2026-10-29, target: CourtIQ as a projection input) — NOT SHIPPED

Metric: minutes RMSE, teammates of a scratched regular (+ all active), both states; fit 2024-25.
**Critic's hole:** CourtIQ was used as a minutes shift; what the no-five state lacks is WHO TAKES
THE START. 2025-26 inactives, teammates of a scratch: actual starters bias -1.27 (n 11,435), bench
+0.67 (n 17,260), vs +0.24 / -0.33 confirmed; error is within-team (6.64), not team-level (0.53).
**Fix tested (scripts/nba_courtiq_pstart_test.py):** cycle 10's P(start) logistic + feature
ciq = sum over scratched starters X of n_wo/(n_wo+K) x (Y's start rate without X - with X), this
team's earlier games only; logistic, K and inactives gap coefficients fitted on 2024-25 (K 3,
ciq weight 0.73). Inactives state:

| | 2024-25 all / slice | 2025-26 held out all / slice | 2026-27 all / slice | 2025-26 starter / bench bias |
|---|---|---|---|---|
| shipped | 6.3325 / 6.4091 | 6.5788 / 6.6628 | 6.9030 / 6.8769 | -1.27 / +0.67 |
| cycle 10 P(start) | 6.3034 / 6.3789 | 6.5721 / 6.6549 | 6.9267 / 6.8991 | -1.20 / +0.63 |
| + CourtIQ start feature | 6.3011 / 6.3763 | **6.5705 / 6.6532** | 6.9253 / 6.8982 | -1.20 / +0.62 |

Official-report replay 2025-26 (production's state): board 7.050 / 7.048 / 7.047, active 6.641 /
6.638 / 6.637 (n 31,218) -- noise. 2026-27 replay not available (no report archive this season).
CourtIQ adds 0.0016 over cycle 10 (itself not shipped), and both are worse than shipped on 2026-27.
The confirmed five settles who starts ~30 min before tip; before that the team's slot history
carries ~nothing the position/start-rate features don't. **Verdict across cycles 21-23: CourtIQ is
a research view; no form of it (position transfer, minutes delta, residual delta, P(start)) earns
a place in the projection.** Runner-ups (untested): 28+ min scratch under-shared (slice 6.98);
a narrower slice for CourtIQ-relevant rows; multi-scratch double counting in the cycle-22 delta.

## Overnight 2026-10-30: gradient-boosted minutes correction — SHIPPED

Adam asked for an all-night ML review; seven parallel studies (minutes ML, DK-points rate,
ceilings, GPP ownership, lineup construction, phone/desktop mismatch, code review). This one won.

`edge/nba_minutes_ml.py` + `edge/nba_minutes_ml_trees.npz`: two HistGradientBoosting models
(five known / not), applied at the end of `nba_minutes.project`, features computed by project()
from its own internals (shipped minutes, adjusted role, role_v2 pieces, recent minutes, team
scratch load, ranks, position) -- training (`scripts/nba_minutes_ml_fit.py`, rebuilding each
lab night in the LIVE input format and calling project() itself) and live share one code path.
Prediction clipped 0-48, team rescaled to the shipped total; trees walked with numpy (no sklearn
at run time). Fitted on 2024-25 only; hyperparameters from a time split inside 2024-25.

| RMSE / MAE (bias unchanged) | 2025-26 held out (n 31,218) | 2026-27 (n 1,936) |
|---|---|---|
| five known: shipped -> ML | 6.273 / 4.70 -> **6.102 / 4.57** | 6.335 / 4.79 -> **6.250 / 4.65** |
| five not known: shipped -> ML | 6.579 / 4.87 -> **6.498 / 4.80** | 6.859 / 5.01 -> **6.784 / 4.92** |

Baselines reproduce the lab exactly (production path == harness); parity: production project()
with the exported trees == the fit script's prediction on 200 team-games (0 differ > 0.1 min).
A leak caught in the study: its `promoted` flag used the TRUE starters in the no-five state
(its 6.31 there was fake); here starter/promoted are NaN without a five (tested).
Not run: the official-report replay (report_preds) -- the model sits inside project(), which
that path wraps, so it inherits it; re-check at the next critique cycle. Build time +5 s.
Off switch: nba_minutes_ml.ENABLED = False (or params {"ML": False}); a missing tree file
falls back to the shipped minutes and the page says so ("minutes: ..." in the summary).
Refit: python3 scripts/nba_minutes_ml_fit.py --write (refit on 2024-25 + 2025-26 once the
season has ~6 weeks, holding out 2026-27).

**The other studies (overnight, held out):**
- Ceilings: empirical error quantiles per projection bucket SHIPPED (NBA_STATUS.md).
- Points per minute (cash): EWMA rate -0.011 MAE (0.16%), GBM rate noise -- nothing shipped;
  live 10/20-29: minutes run -1.1 low while the rate runs +1.4 high (grows with salary) -- recheck
  at 25+ nights before fitting a correction.
- GPP ownership: 0.75 shipped + 0.25 GBM blend 3.34 -> 3.26 MAE on 5 clean nights -- directional
  (9 nights), not shipped; contest size has no effect on concentration.
- Lineup construction (10 cash / 12 GPP slates): nothing beat shipped (team caps, minute floors,
  95/99th, fades, stacks); shipped cash missed the line 7/10 by 25-78 pts -- projections, not shape.
- Phone vs desktop: pool order drove different near-tied lineups -- fixed (sorted pool); the
  10/30 LeBron -> Isaac Jones entry NOT reproducible from any Cloud input.
- Code review: late swap could silently drop a held player -- fixed.
