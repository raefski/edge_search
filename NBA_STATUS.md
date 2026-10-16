# NBA DFS — status (built 2026-10-15, regular season opens 2026-10-20)

60-second orientation. Read before changing the NBA model.

## What exists

| piece | file | what it does |
|---|---|---|
| app | `pages/12_🏀_NBA_DFS.py` | cash + GPP lineups, Board with a **Promoted / changed only** filter (`?promoted=1`), confirmed/expected starters per team, OUT list, preseason warning |
| pipeline | `edge/dfs_run_nba.py` `build_slate` | DK salaries → NBA.com starters/inactives → minutes → points → sim → lineups; same call as `scripts/dfs_lineups_nba.py` and the autolog |
| minutes | `edge/nba_minutes.py` | recent role, re-shared for tonight's news (scratches, promoted starters); 240 shared by role^1.15 when roles overflow |
| points | `edge/nba_project.py` | minutes × DK points/minute (decay 0.98) × opponent^0.5 × implied team total |
| sim / theory | `edge/dfs_nba_theory.py` | independent gamma draws, sd = 5.91 + 0.202 × proj; cash 30th pct, GPP 90th; ownership PRIOR |
| optimiser | `edge/dfs_opt_nba.py` | PG SG SF PF C G F UTIL with multi-position matching, 2+ teams / games |
| lineups feed | `edge/nba_lineups.py` | NBA.com daily lineups JSON (Expected/Confirmed starters, inactive list); snapshot for the cloud |
| scratch alerts | `harness/nba_scratch.py` + `edge-nba-scratch.timer` (2 min, interval) | 🔴 OUT / 🟢 STARTS pushes, urgent when ≤$5k reaches 6x; tap opens the app filtered |
| history | `scripts/nba_history_publish.py` + `nba-history-publish.timer` (06:00, 14:00) | `data/nba_player_history.json` for desktop + cloud |
| odds | `odds-collect-dfs-nba.timer`, `odds-publish-dfs-nba.timer` | game lines (implied totals); props collected, NOT yet used |

## Measured (held out on 2025-26, fit on 2024-25)

- **Points**: 7.65 DK pts mean |error|, corr 0.765 (29,311 player-games) vs last-10 average 7.88 / 0.743, season average 8.19 / 0.725. With the minutes he actually played: 4.54 / 0.909 → **minutes are most of the error**.
- **Minutes**: all 5.60 min; promoted starters 5.48 (bias +0.1) vs 9.58 (bias −8.6) for recent minutes. A bench player who starts plays **~8.6 more minutes** than his recent average; the confirmed five is the signal, inactives alone barely help (8.37).
- **Residual correlation** after minutes: teammates −0.037, opponents +0.001 → no stacking rule.
- **Preseason → regular season** minutes for newcomers: ×0.66 (n=112).
- A game-night roster is 13 active, roles summing ~241 min; before NBA.com's inactive list posts, the app keeps each team's 13 most-used players.

## Not done / not verified

- **Props** (`dfs_nba`): DK posts points/rebounds/assists (verified 10/15 for 10/20 games); not blended into projections. Next: log props beside projections, grade both after a week.
- **Ownership** is an unfitted prior. Twelve NBA GPP exports sit in `data/contests.db` (GPP_META: NBA fields rewarded chalk on all 10 slates measured) — rebuild those slates' projections from history and fit it.
- No late-swap, no leverage map / contest picker, no field-aware cash (NHL has all three).
- First real slate: **10/20, DK draft group 154575** (3 games). Watch: starters flipping Expected → Confirmed (lineup log `data/nba_lineup_log.csv` records when), the first urgent push, and whether projections for stars look sane once inactives post.
