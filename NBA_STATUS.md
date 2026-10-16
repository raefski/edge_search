# DK NBA + College Basketball DFS — status, methodology, what is not validated

Start here for anything basketball. Built 2026-10-13, a week before NBA opening
night (~10/20) and three before college (~11/3), **in a cloud container that
could not reach DraftKings, the NBA stats API, ESPN or any odds source.** So
nothing below has met a live payload. Everything is exercised end to end on a
synthetic slate (fake DK draftables, fake odds client) by
`tests/test_dfs_nba.py` and `tests/test_nba_pages.py`. **Before the first real
slate, run the go-live checklist (§7).**

## 1. The contests

| | NBA Classic | College (CBB) Classic |
|---|---|---|
| Roster | PG SG SF PF C G F UTIL | G G G F F F UTIL UTIL |
| Cap | $50,000 | $50,000 |
| Minimums | 2 teams, 2 games | 2 games |
| Lobby code | `NBA` | `CBB` |
| Classic id | 70 (assumed; resolved by the lobby's GameTypes **name** first) | not assumed — by name only |
| Late swap | yes | assumed yes |

Scoring is one table for both (`edge/nba.py::actual_points`, vectorised in
`edge/nba_sim.dk_points`, held equal by a test): pts 1, 3PM +0.5, reb 1.25,
ast 1.5, stl 2, blk 2, tov −0.5, double-double +1.5, triple-double +3 (not
stacked), across pts/reb/ast/blk/stl.

## 2. Code map

| role | file |
|---|---|
| league shape, team aliases | `edge/nba.py` (GS/NO/NY/SA/PHO → GSW/NOP/NYK/SAS/PHX), `edge/ncaab.py` (fuzzy book-name matching) |
| simulator | `edge/nba_sim.py` (both leagues, `League` constants) |
| theories + ownership | `edge/dfs_nba_theory.py` |
| optimiser | `edge/dfs_opt_nba.py` (slot eligibility = bipartite matching; `locked=` for late swap) |
| pipeline | `edge/dfs_run_nba.py` (`Spec`, `build_slate_for`), `edge/dfs_run_ncaab.py` (college `Spec`) |
| pages | `pages/12_🏀_NBA_DFS.py`, `pages/13_🏀_NCAAB_DFS.py`, body in `edge/dfs_hoops_ui.py` |
| CLI | `scripts/dfs_lineups_nba.py [--sport ncaab] [--lock NAME]`, `scripts/dfs_lineups_ncaab.py` |
| leverage | `edge/dfs_leverage_sports.hoops` |
| evaluate | `scripts/nba_calibration.py` (by draft group), `scripts/nba_fit.py` (sim priors vs box scores) |
| verify live | `scripts/hoops_dk_discover.py` = `harness run hoops-verify` |
| odds | profiles `dfs_nba`, `dfs_ncaab`; timers `odds-{collect,publish}-dfs-{nba,ncaab}` |

## 3. Where projections come from (in order, every row says which)

1. **props** — every book's two-sided points / rebounds / assists / threes /
   steals / blocks / turnovers line, inverted to a mean under the simulator's
   own variance model and averaged across books. Missing singles come from
   combos (Pts+Reb − Pts …), then from the position profile scaled by points.
2. **fppg** — DK's FPPG, scaled half-way to tonight's implied team points,
   split by position profile. Does **not** move on a teammate's scratch.
3. **salary** — college only, 4.6 DK pts per $1K (PRIOR), for players priced
   above min × 1.15. The NBA drops a priced player with no prop and no FPPG
   (a DNP risk the optimiser would otherwise love at $3,000).

DK `OUT/O/IR` are dropped; `Q/GTD` are kept and flagged ⚠️.

## 4. The simulator (edge/nba_sim.py)

Pace per game (shared), efficiency per team, realised margin → blowout minutes
cut for each team's top-5 (beyond 15 points, 1.2%/pt, floor 72%, bench gains),
per-player minutes and usage noise with usage renormalised inside the team,
then points = 2×twos + 3×threes + FT (Poisson each), rebounds scaled by the
opponent's misses, assists by efficiency. Every factor is renormalised to mean
1 per player, so **simulated means equal market means** (tested) and the
factors only shape spread and correlation.

| constant | NBA | NCAAB | provenance |
|---|---|---|---|
| pace sd / eff sd | 0.050 / 0.088 | 0.075 / 0.105 | PRIOR: market residuals (NBA total sd ~18.6, margin ~14.2) |
| minutes / usage sd | 0.10 / 0.08 | 0.11 / 0.09 | PRIOR |
| usage renormalisation | 0.6 | 0.6 | PRIOR |
| FT share of points | 0.155 | 0.18 | league box-score share, PRIOR |
| blowout start / slope / floor | 15 / 0.012 / 0.72 | same | PRIOR |

On the synthetic board: a 50-pt star sd ≈ 12; teammates r ≈ +0.04, same-game
opponents ≈ +0.05, different games ≈ 0. **`harness run nba-fit`** compares these
to 2024-25 box scores on the desktop (data/nba_ground_truth/, gitignored).

## 5. Theories

- **cash** = 25th percentile of the lineup's simulated total. No stack rule.
- **gpp** = 90th percentile (weak correlation → the 95th would just chase one
  volatile player). No stack rule; the search walks into high-pace games.
- **Ownership** = softmax on value (1.1) and salary (0.45) z-scores per primary
  position, normalised to slot shares. **UNFITTED PRIOR.** Fit cash and GPP
  separately on exports: `harness run calibrate-nba -- <export> --fit-ownership`.

## 5a. Teammate out: our own with/without splits (edge/nba_onoff.py, 2026-10-16)

Built instead of paying for RotoGrinders' CourtIQ ($74.99/mo NBA tier, no export).
Splits are **game-level**: what a player scores in games a key teammate (20+ min,
8+ games) *missed*, against games he played. That is the DFS case, unlike CourtIQ's
on-court splits, whose "off" minutes are mostly bench-vs-bench. Source: the
`leaguegamelog` box scores `scripts/nba_ground_truth_collect.py` already pulls (one
call a season); last season merged in at half weight.

**Applied only where the market has not already done it:** to FPPG-projected players
on a team with a DK-OUT player; and to props players while the props are **stale**
(the OUT player still has a points line, so the books have not re-posted). Each
split is shrunk by n_wo / (n_wo + K); the combined factor is bounded to [0.75, 1.6]
and relative to the player's all-games level, so a long-injured star (already in
FPPG) moves nothing. The board marks adjusted players ↑1.12 / ↓0.93 and says when
the table is missing.

| | status |
|---|---|
| K = `SHRINK_GAMES` = 10 | **PRIOR.** `harness run nba-onoff-backtest` fits it held out (first half of dates fit, second reported, date = unit, bootstrap CI by date) |
| backtest on real data | **not run** — 2024-25 box scores are on the desktop only. On a synthetic league (24k player-games, one planted +6 effect): MAE 4.84 vs 4.93, CI [−0.06, +0.03], not significant, so the shrinkage must be fitted, not assumed |
| vs the books' re-posted lines | not possible yet: no NBA scans in data/odds.db. Once a few weeks exist, the books' teammate re-pricing after a scratch is the second, cleaner test |
| publish | `harness run nba-onoff-publish` (`--push`), timer `nba-onoff-publish` 09:20 / 15:20 |

## 6. What is NOT validated

| thing | status |
|---|---|
| DK Classic ids, roster, FPPG stat id, position strings | assumed — §7 |
| DK sportsbook NBA/CBB category ids | selected by NAME (`PROP_CATEGORY_NAME`) so a wrong id cannot silently empty the board; pin ids after §7 |
| FanDuel NBA tab names | unverified (edge/arb/config.py) — DK alone is enough to build |
| every simulator constant | PRIOR (§4) |
| ownership | PRIOR (§5) |
| college team-name matching | heuristic; an unmatched game is simulated at league average and named on the page |
| college props as ladders | only two-sided lines are read; if DK posts college points only as milestone ladders (it does for football) those players fall to FPPG |
| late swap in `harness late-swap` | NHL only; NBA uses `dfs_lineups_nba.py --lock` |
| mirrors | none generated for basketball |

## 7. Go-live checklist (desktop, ~2 minutes)

1. `python3 -m harness run hoops-verify` — exits non-zero on any disagreement
   with §1; pin the detected FPPG id in `edge/dfs.FPPG_STAT_IDS` and the prop
   category ids in `edge/arb/draftkings_league.PROP_CATEGORIES`.
2. `python3 -m harness run odds-dfs_nba`, then
   `python3 -m harness run lineups-nba -- --no-log --board 30` and check the
   board: books > 0, few `fppg` rows among starters, no implausible values.
3. Install timers: `odds-collect-dfs-nba`, `odds-publish-dfs-nba` (and the
   ncaab pair in November); draftables-publish already covers NBA/CBB.
4. `python3 -m harness run nba-onoff-publish` (then enable its timer), and
   `python3 -m harness run nba-onoff-backtest` on 2024-25: set `SHRINK_GAMES`
   from it only if the held-out CI says it helps.
5. After the first contest: `harness run import-contests`, then
   `harness run calibrate-nba -- data/contest-standings-N.csv`.
