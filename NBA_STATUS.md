# NBA + college basketball DFS — status (NBA built 2026-10-15, opens 2026-10-20; college opens ~11/3)

60-second orientation. Read before changing the NBA model.

## What exists

| piece | file | what it does |
|---|---|---|
| app | `pages/12_🏀_NBA_DFS.py` | cash + GPP lineups, Board with a **Promoted / changed only** filter (`?promoted=1`), confirmed/expected starters per team, OUT list, preseason warning |
| pipeline | `edge/dfs_run_nba.py` `build_slate` | DK salaries → NBA.com starters/inactives → minutes → points → sim → lineups; same call as `scripts/dfs_lineups_nba.py` and the autolog |
| minutes | `edge/nba_minutes.py` | recent role, re-shared for tonight's news (scratches, promoted starters); 240 shared by role^1.15 when roles overflow |
| points | `edge/nba_project.py` | minutes × DK points/minute (decay 0.98) × opponent^0.5 × implied team total |
| sim / theory | `edge/dfs_nba_theory.py` | independent gamma draws, sd = 5.91 + 0.202 × proj; cash 30th pct, GPP 90th; ownership PRIOR |
| optimiser | `edge/dfs_opt_nba.py` on `edge/dfs_opt_climb.py` | PG SG SF PF C G F UTIL as a slot matching, 2+ teams / games; latest tips in G/F/UTIL; `locked=` for late swap; `portfolio()` |
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

## Opening week (2026-10-20)

Season-opening minutes come from last season's role (`SEASON_START_WINDOW`), which
the backtest never covered: its histories start empty each season. Replaying
2025-26's opening week (150 team-games, each team's first 5; roles from 2024-25 only;
NBA.com actives and starters as known at the time), minutes mean |error|:

| role window | before lineups | starters confirmed |
|---|---|---|
| 25 (was) | 6.76 | 6.31 |
| 40 | 6.55 | 6.21 |
| **82 (shipped 10/20)** | **6.42** | **6.10** |

`nba_history_publish.py` now keeps 82 games a player (the rate still reads its
40). Known bias left in: on opening week, starters run about 3 min low before lineups post
(-1.9 once confirmed) and the bench +1.5. After the offseason the 13 kept roles sum
to 267-321 against 240 (PHI 321 on 10/20), so stars are squeezed: Maxey
projects 26, Embiid 22. Tested and rejected on the same replay: protecting the
starting five in the overflow (6.45 / 6.16, worse), a start-rate weight, a
rotation cap (<= 0.04 better, in-sample) and a higher excess power (worse
overall). One opening week is all the cache allows, so none of this is held out.
**Build after lineups confirm (late swap): that removes about a third of the bias.**

## Buzz: YouTube + podcasts in GPP ownership (2026-10-21)

`scripts/buzz_nba.py` (timer `buzz-nba-publish`, 10:30 / 13:00 / 15:30 / 17:30 / 18:20 ET) collects the
day's NBA DFS shows (date-keyed YouTube search plus the Stokastic and Ship It Nation podcast feeds) and
counts player mentions with edge/buzz.py's matcher, words cut at lock. A show only counts if its words
were READABLE before lock: an upload, a live stream that had ended (+45 min for YouTube's transcript),
or a podcast episode published before lock. Podcasts are mostly reposts of the YouTube streams posted
after lock, so they add little (5 ready episodes over 10 nights).

`scripts/buzz_fit_nba.py`: 10 GPP nights rebuilt as they stood, buzz backfilled, leave one night out:

| buzz | ownership MAE held out | vs prior 5.355 | nights better | rank corr |
|---|---|---|---|---|
| YouTube, readable before lock | 5.116 | -0.238 [-0.453, -0.026] | 7/10 | +0.730 |
| **YouTube + podcasts, readable (shipped)** | **5.080** | **-0.275 [-0.481, -0.079]** | 7/10 | +0.735 |
| all pre-lock talk, read afterwards | 4.981 | -0.374 [-0.617, -0.154] | 8/10 | +0.739 |

`BUZZ_BETA = 0.4` (folds: 0.4 x6, 0.3 x4). When the talk happens (mentions, ET): night-before previews
1,501, mornings 1,384, early afternoon 2,079 -- all readable; late afternoon 2,900 readable + 6,690
only after lock (Live Before Lock streams). Every show's mentions lean the right way (rank corr with
the prior's miss +0.06..+0.19, Tory Langley -0.02); too few nights to drop one. First live night
10/21: 19 YouTube + 2 podcasts by 6 PM, 219 players named. Page 12 says when buzz is in.

## Not done / not verified

- **Props** (`dfs_nba`): DK posts points/rebounds/assists (verified 10/15 for 10/20 games); not blended into projections. Next: log props beside projections, grade both after a week.
- **Ownership: the prior was tested against real fields on 2026-10-20 and stands.** `scripts/nba_ownership_fit.py`
  rebuilds each 2025-26 GPP night as it stood: DK salaries for the contest's own draft group, history before
  that date (`nba_history_publish.build(before=)`), and that day's NBA.com lineups (the day's final file, so
  late-game starters carry mild hindsight). No odds. It then scores `add_ownership` against the export's
  %Drafted. 10 contests / 10 nights, leave one night out:
  prior (value 1.0, salary 0.4, promoted 0.6) MAE **5.39**, rank **+0.71**. A refit of those weights scored
  5.43-5.49 held out (better 0-5/10); adding DK's FPPG value or tonight's minutes jump scored 5.47 (1/10).
  The misses are news chalk the rebuild can't see (Kel'el Ware 60% vs 7%, Tre Johnson 45% vs 2%) and
  stars the field skips (LeBron and Luka 3% vs 22%). The leverage map / contest EV on page 12 uses this
  prior, so read its ORDER, not its dollars. Next lever: a source of the field's own projections (the NFL
  needed Dan's sheet / buzz), not more weights.
- **Late swap on the phone (built 2026-10-20):** page 12's 🔁 Swap tab (`R.late_swap`) takes
  your entered 8, locks players whose game has tipped, replaces an OUT player whose game
  has not, and holds the slot of one who is OUT but already locked. "Also re-optimise"
  re-climbs the unlocked slots. Same day: board rows now carry `start`. Without it,
  `assign_slots` never put the latest tips in G/F/UTIL (DK's late-swap convention) for NBA.
- `edge/dfs_leverage_sports.hoops` builds the leverage map for NBA results, but page 12
  doesn't show it yet, and there is no field-aware cash.
- First real slate: **10/20, DK draft group 154575** (3 games). Watch: starters flipping Expected → Confirmed (lineup log `data/nba_lineup_log.csv` records when), the first urgent push, and whether projections for stars look sane once inactives post.

## Merged 2026-10-16: the web session's parallel basketball build

A cloud session (branch `claude/nba-ncaa-dfs-edge-search-3upryu`, 10/13-10/16)
built NBA + college on a different model -- props inverted to stat means, DK
FPPG fallback, a correlated whole-game simulator -- with every constant a prior
and nothing run live. This file's NBA model was kept because it is the one
measured held out. What came across:

- **Shared ingest fixes** (`tests/test_hoops_ingest.py`): "Pts + Reb" / "Pts + Ast" /
  "Reb + Ast" / "Stl + Blk" / "Threes" / turnovers get their own market keys (they
  were being filed as `player_points` in `data/odds.db` since NBA props started
  collecting); DK basketball prop tabs are also selected by NAME; an unknown DK
  FPPG stat id is detected from the payload; the draftables snapshot keeps DK's
  injury status, so the cloud sees OUT players.
- **The optimiser** became the shared `edge/dfs_opt_climb.py`. Same algorithm as
  the old NBA one; on the logged 10/15 slate + 20 perturbed copies (scored on a
  fresh 20,000-draw sim): cash identical on 21/21, GPP equal on 18 and better on
  the real slate (+1.1 at the 90th pct), worse on 2 synthetic (-4.8, -1.2); 5.6x
  faster (94 s vs 526 s for 42 builds). At 200 restarts (now the default; it runs in less time than the old
  80): GPP equal on 20/21 and better on the real slate, never worse; 272 s vs 610 s.
- **College basketball** (below), on that engine.
- **Teammate-out splits** (`edge/nba_onoff.py`) -- built, and tested against this
  model: **not shipped**. `scripts/nba_onoff_backtest.py` (fit 2024-25,
  report 2025-26, 29,311 player-games / 155 dates): model alone 7.575; + whole-points
  ratio 7.694 (the web build's way: on this model it counts the minutes change
  twice); + per-minute ratio 7.593 (CI [+0.007, +0.026], worse); + per-minute shrunk
  to no change 7.576 at the fitted K=160 (CI [-0.001, +0.002], nothing). The minutes
  model already carries a scratch; the publisher and its timer were dropped.

## College basketball (DK CBB Classic) — built 2026-10-13, NOT verified live

| piece | file |
|---|---|
| app | `pages/13_🏀_NCAAB_DFS.py` (cash, GPP + leverage map, board with each row's source) |
| pipeline | `edge/dfs_run_ncaab.py` `build_slate` (same call as `scripts/dfs_lineups_ncaab.py`) |
| teams / roster | `edge/ncaab.py` — G G G F F F UTIL UTIL; book names matched to DK codes by prop-subject votes, then name similarity inside one tip window |
| simulator | `edge/ncaab_sim.py` — whole games: shared pace, team efficiency, blowout minutes cut, usage renormalised within team, Poisson box scores (DD/TD bonuses priced) |
| theory | `edge/dfs_ncaab_theory.py` — cash 25th pct, GPP 90th; ownership prior per G/F group |
| optimiser | `edge/dfs_opt_ncaab.py` on `edge/dfs_opt_climb.py` |
| odds | profile `dfs_ncaab`; `deploy/odds-{collect,publish}-dfs-ncaab.timer` — install in November |

Projections, in order, every row says which: **props** (two-sided lines inverted
to means, combos fill missing stats) → **fppg** (DK's, scaled half-way to tonight's
implied points) → **salary** (4.6 DK pts per $1K, PRIOR). Every simulator and
ownership constant is a PRIOR.

**Go-live (once DK lists a CBB slate, ~late October):**
1. `python3 -m harness run hoops-verify -- --sport CBB` — checked 2026-10-16: DK's
   CBB lobby listed nothing yet, so the lobby code, Classic id, roster, FPPG id and
   position strings are all still assumed. It exits 3 until there is a slate to read.
2. `python3 -m harness run odds-dfs_ncaab`, then
   `python3 -m harness run lineups-ncaab -- --no-log --board 30`: books > 0, game lines
   matched (the page names any game with none), no implausible values.
3. Install the two `dfs-ncaab` odds timers.
4. After the first contest: `harness run import-contests`, then
   `harness run calibrate-nba -- data/contest-standings-N.csv --sport ncaab`
   (per source; `--fit-ownership` reads, never ships, below ~5 slates).
