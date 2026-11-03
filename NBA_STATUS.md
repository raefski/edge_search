# NBA + college basketball DFS — status (NBA built 2026-10-15, opens 2026-10-20; college live 2026-11-02)

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

## Late swap after lock (2026-11-02)

11/1 Night 2g (156049) locked 8:30; Poeltl first showed out 8:44 (not on the 8:30 official report; NBA.com had a confirmed start at 8:42); TOR@LAL tipped 9:00. The build was not wrong, but the 14 minutes were lost: Adam got the "high" push and did not see it, and the swap could not have been done on the phone anyway. n = 1 night, a replay of one incident, not a measurement. All of it had to change together:

| link | was | now |
|---|---|---|
| alert | "high" unless a cheap teammate was value; no deadline | a player in a logged lineup ruled out before their tip is urgent: `🚨 SWAP BY 9:00 PM`, plus a last call 5 min before tip. Fires for non-regulars too |
| reaching the slate | the picker read only the lobby, which drops a slate at its first tip | `data/draftables_snapshot/slates.json`; listed as 🔒 until the last tip; the alert's link (`?gid=&swap=1&mode=&at=`) works even without it |
| salaries on Cloud | snapshot pruned once out of the lobby | kept until last start + 1 h |
| stale build | a build cached up to 15 min could still list the player | the link's `at` rebuilds once |
| lineup loaded | the current build, which has already dropped the player: always "no swap needed" | the logged lineup |
| suggestion | could offer a player whose game had tipped (Horford at 8:47; legal answer Jackson-Davis) | only games not started |
| phone layout | swap ~1,400 px down at 390 px | on the link, the answer is on the first screen |
| log | a rebuild after lock replaced the logged lineups (done by accident 11/1, restored from the `data` branch) | NBA refuses to log at or after the first tip |

Not done: the alert and Swap know the logged lineup, not the real DK entry. `late_swap` takes names, not DK slots, so a replacement that does not fit the open slot would be refused by DK. The after-lock log guard is NBA only. The NCAAB page has no Swap tab at all. Cloud's path has only been driven with AppTest and locally: first real proof is a live scratch.

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

## Cash = GPP is right in NBA (2026-10-22)

Adam saw identical cash and GPP lineups. Two checks:

1. **The simulator was drawing everyone independently.** That rested on residual correlation AFTER
   the minutes actually played. On PRE-GAME errors (z = error / sd_for, pre-game projection with
   starters confirmed, players projected >= 12; 2025-26 / 2024-25), same-team starter pairs run
   +0.067 / +0.043, starter-bench -0.090 / -0.088, opposing starters +0.019 / +0.010 (blowouts and
   game script move whole rotations). `simulate` now draws a game's players jointly (Gaussian copula,
   ROLE_CORR; marginals unchanged, tested). On 10/21's main, cash and GPP still share 6 of 8: the
   correlation is real but too small to split them.
2. **Should GPP fade?** `scripts/nba_gpp_exante.py`: the 11 NBA GPP nights rebuilt, every entry
   scored on our pre-lock projection and its real ownership (59k entries). At a FIXED projection,
   more ownership paid: ROI +0.32 per SD [+0.16, +0.48], cash +10.6 pp, top-1% +0.49 pp. The contrarian
   fifth minus the chalk fifth was **-0.59 ROI [-0.93, -0.21], 10/11 nights**, despite 15.8 vs 1.6
   duplicates. The field's ownership carries news our projection lacks (NHL showed the same, smaller).
   So: GPP ownership fade stays 0, cash and GPP sharing most players is correct, and page 12
   warns that the map's EV ⭐ (which treats our projection as truth) leans the wrong way in NBA.
   Next lever: use predicted ownership + buzz as a projection signal.

## Critique loop, seven cycles (2026-10-21/22) -- NBA_MINUTES_CRITIQUE_LOG.md

Independent critic agents (`.claude/agents/nba-minutes-critic.md`), each fixed and re-measured:
1. the 240 step moved stars like bench players -> rank-gap allocation (2025-26 RMSE 6.810 -> 6.692; salary nudge off);
2. the bench role ignored the last game and dropped starters -> recency 0.25 / drop 0.2 (6.692 -> 6.586);
3. **NBA.com's lineups feed stopped flagging inactives in 2026-27** -> box-score availability for history
   (edge/nba_availability.py, stats.nba.com v3 comments) and ESPN's Out list tonight, cut 13 -> 15.
   Live-state replay: 6.954 -> 6.634 (confirmed), 8.204 -> 7.435 (inactives only).
4. **NBA.com's afternoon *Expected* five was used as if confirmed** (wrong in 7 of 28 team-games, 10/20-21)
   -> minutes = 0.70 x project(Expected five) + 0.30 x project(no five) until the team confirms; w chosen on
   2024-25 by minimax regret over 10-30% miss rates. Expected state, 2025-26 held out at 25% wrong:
   7.108 -> 6.955 (lab), 7.155 -> 6.994 (live state); 10% wrong 6.800 -> 6.744.
5. the starting role forgot too fast -> its own memory, decay 0.9 / last season worth 3 games (bench and
   pre-lineup roles keep 0.8 / 1): 2025-26 confirmed 6.586 -> 6.550, live state 6.634 -> 6.598; 2026-27 so far
   7.58 -> 7.29, stars' bias -1.8 -> -0.3.
6. ESPN's Out list misses two-ways and Day-To-Day sits (29 of 47 role-10+ scratches of 10/20-21 not 'Out')
   -> a player who sat his team's last game gets P(dress) x minutes, teammates a blend (NM.project_available);
   board RMSE incl. phantoms, five announced, 2025-26: 6.766 -> 6.459 at 25% missed (6.385 -> 6.319 at 0%).
   ESPN snapshots now logged (data/nba_espn_injuries/) to measure the miss rate.
7. the NBA's official injury report replaces ESPN as the Out list (two-ways, Doubtful/Questionable/Probable);
   uncertain players get P(dress) x minutes by status, teammates the expected share. Replayed on 1,182 archived
   reports, 2025-26 held out: five announced 6.624 -> 6.438, no five 7.357 -> 7.077 (board per active game).
   The lab's NBA.com inactive flags miss ~1,000 injured 0-minute 'actives' a season: lab tables run pessimistic.
v1 -> now, lab protocol: 7.758 -> 6.550 confirmed, 8.146 -> 7.396 inactives only.

## Revised: chalk vs contrarian under the v2 projections (2026-10-21)

`scripts/nba_gpp_exante.py` re-run with v2 projections (same 11 nights, 60k entries): projection now
predicts ROI, **+0.33 per SD [+0.07, +0.60]** (cash +8.1 pp); ownership at a fixed projection is no
longer significant for ROI (+0.10 [-0.11, +0.34]; cash still +5.8 pp); contrarian minus chalk quintile
-0.15 [-0.58, +0.34] (5+/6-). The 10/22 "chalk wins, -0.59" was largely the field knowing the minutes v1
got wrong. Rule now, as in every sport: maximize projection, ownership breaks ties; the fade stays 0.
Page 12's note says so. BUZZ_BETA refit 0.4 -> 0.2 under v2 (prior 4.913 -> 4.713 with buzz, 9/10).

## Minutes model v2 (2026-10-21)

`edge/nba_minutes.project` was rebuilt on a live-faithful backtest (`scripts/nba_minutes_lab.py`:
histories across seasons from 2023-24, every active player scored, fit 2024-25 / test 2025-26,
squared error). Full method and tables: NBA_MINUTES_METHOD.md. Held out on 2025-26, starters
confirmed: RMSE 7.76 -> **6.81**, MAE 5.74 -> **5.26**; opening week 6.21 -> 5.30, lottery rookies 7.32 -> 5.17,
first game back 6.59 (+3.0) -> 5.99 (-0.6); legacy protocol 5.601 -> 5.123. Changes: weighted role
(decay 0.8) against a one-game prior of last season as a whole; start/bench split replaces the promotion
lift; 240 shared by power 1.0 + starter tilt 0.05 both ways; return ramp 0.8/0.9; rookie role from the
draft pick; DK salary nudge (11 nights, LOO MAE 5.14 -> 4.97). The history file gained cur_n / n_pre /
pick / since_return (republish it with the code). Not fixed: 2026-27 opening-week stars still about
8 min low on night one (3 games); recheck on more nights.

## Not done / not verified

- **Props** (`dfs_nba`): DK posts points/rebounds/assists (verified 10/15 for 10/20 games); not blended into projections. Next: log props beside projections, grade both after a week.
- **Ownership: whole-slate model shipped 2026-10-28 (NBA_OWNERSHIP_CRITIQUE_LOG.md cycle 1).** One softmax
  over the slate (not per position) on our value, salary, promoted, buzz AND each player's RECENT DK points
  per $1K (`dfs_nba_theory.SLATE_OWN`, `recent_dk`). Live replay harness `scripts/nba_ownership_lab.py`:
  held out 10/25-10/27 (5 slates / 10 GPPs) MAE 4.95 -> 3.96, better 5/5 slates; cheap chalk still
  -19 pts low. Cycle 2 (same day): + cheap pre-lock STARTER bonus (`CHEAP_STARTER`): test MAE 3.96 -> 3.65,
  cheap chalk -19 -> -14 (Reed, Ajay remain: the field knows the five before NBA.com posts it). The paragraph below is the 2025-26 history.
- **Ownership (2025-26 history): the prior was tested against real fields on 2026-10-20 and stands.** `scripts/nba_ownership_fit.py`
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

## College basketball (DK CBB Classic) — first live slate 2026-11-02 (draft group 155565)

| piece | file |
|---|---|
| app | `pages/13_🏀_NCAAB_DFS.py` (cash, GPP + leverage map, board with each row's source, name check) |
| pipeline | `edge/dfs_run_ncaab.py` `build_slate` (same call as `scripts/dfs_lineups_ncaab.py`) |
| names | `edge/ncaab_names.py`: every outside name -> one DK player, inside a team or game; audit on page + CLI |
| teams / roster | `edge/ncaab.py` — G G G F F F UTIL UTIL; DK code <-> book/Torvik/RotoWire spellings |
| starters + injuries | `edge/ncaab_rotowire.py` (reads the link title: the visible text is "D. Aberdeen") |
| last season | `edge/ncaab_torvik.py`, `data/ncaab_torvik_2026.json` (Barttorvik; on `main`, frozen) |
| ladders | `edge/dfs_ladder.mean_gapped` + `HOOPS_OVERROUND` |
| simulator | `edge/ncaab_sim.py` — whole games: shared pace, team efficiency, blowout minutes cut, usage renormalised within team, Poisson box scores (DD/TD bonuses priced) |
| theory | `edge/dfs_ncaab_theory.py` — cash 25th pct, GPP 90th; ownership prior per G/F group |
| optimiser | `edge/dfs_opt_ncaab.py` on `edge/dfs_opt_climb.py` |
| odds | profile `dfs_ncaab` (full ladders); `deploy/odds-{collect,publish}-dfs-ncaab.timer`, installed 11/2 |

**Verified live 11/2** (`harness run hoops-verify -- --sport CBB`): lobby code CBB, Classic game
type 98, slots, $50,000, min 2 games, late swap on, FPPG stat id 279 (pinned), positions F / G / G/F.

**What the first board taught, each of which the old build got wrong:**
- DK's sportsbook league is now "College Basketball (M)": the catalog pattern matched nothing and
  no DK price was collected. FanDuel's women's games were also being read as men's (a second
  Maryland game).
- DK posts college props only as one-sided milestones with gaps (8+, 10+, 12+, 15+...): a main-line
  client got one rung each and projected nobody from props. Overround fitted on NBA boards where DK
  posts both forms (points .067, stable by date); estimator graded on NBA outcomes: 11 slates, 689
  player-games, bias +0.87, MAE 8.24. No O/U exists in college to check it directly.
- DK's FPPG is LAST season's, at the old school: Thomas Haugh 32.3 vs Torvik's 31.9. On transfers
  it misleads: Dennis Parker Jr. 31.5 from Radford, $3,500 at Kansas.
- The market's salary line is flat: 9.21 + 2.13 per $1K (r .73, n=33), not 4.6 per $1K. The old
  fallback put a $10,300 freshman at 47.
- Same name, two players: Trent Perry, UCLA $7,600 and Kansas $7,500. Props and history are keyed
  by the salary-list key, never the name.

**Projections, in order, every row says which:**
1. **props** ladders inverted, averaged across books; stats a book does not post come from combos,
   then the player's own last-season shape, then the position profile.
2. **blend** half carryover of last season (moved for same school / up / sideways / down a conference
   tier, and for tonight's implied points), half the salary line.
3. **salary** no verifiable history and no prop, at $4,000+ or a listed starter. Its own line from
   results (1.86 + 1.45 per $1K), not the market's: see the first grade below.
4. **bench** under $4,000 and not a listed starter, on a team RotoWire lists. Fitted to what the
   team's implied points leave (2.04 DK points per team point, measured, 365 teams).

**Evidence.** Carryover, fit 2024-25 -> 2025-26, held out 2023-24 -> 2024-25, MAE vs carrying raw FPPG:
same school 4.60 / 4.84 (n=1,554), up 6.00 / 8.89 (315), sideways 5.67 / 6.41 (511), down 6.12 / 9.81
(221). Against tonight's market, each game's props hidden and predicted (`harness run ncaab-prior-fit
-- 155565`, 33 players, 4 games): shipped MAE 2.87, bias -0.17; salary line alone 3.32; history alone
3.73; raw FPPG 4.18; 4.6 x salary 8.23. **One night against the market, not results: not enough data
to call any of that proven.** The salary line, blend weight, bench rule, Q_PLAY and every simulator
and ownership constant are still priors.

**First grade, 2026-11-02** (gid 155565, ONE slate, 300 logged players, ESPN box scores including the
undrafted: `harness run ncaab-box-grade -- 155565`; DK's FPTS agreed on 172 of 173):

| tier | n | proj | actual | bias | MAE | played |
|---|---|---|---|---|---|---|
| props | 37 | 25.3 | 24.3 | +1.04 | 8.91 | 100% |
| blend | 97 | 19.3 | 16.8 | +2.50 | 9.29 | 94% |
| salary | 18 | 23.1 | 11.2 | +11.80 | 12.31 | 100% |
| bench | 148 | 1.7 | 3.4 | -1.75 | 3.48 | 43% |

- **Salary tier was the miss.** 16 of 18 fell short of `SALARY_LINE`, which came from players a book chose
  to price; the five at $9,700+ averaged 16.0. The same night's 4 no-history players WITH props averaged
  33.8, so no prop at lock is itself news. Now `SALARY_TIER_LINE` (1.86 + 1.45 per $1K), held out by game:
  MAE 5.50, bias +0.07, vs 12.31 / +11.80 (n=18, 7 games). It also removes most of the +11.7 DK points per
  team the board was over. One slate: tonight's is its first real test. Refit: `ncaab-box-grade --fit`.
- **The bench rule held on average.** The export said -5.13 only because it lists drafted players (43 of
  148). Its hole is specific: Darius Bivins $3,000, 1.09 -> 24.00 at 23.6% field; Hassan Koureissi $3,000,
  1.03 -> 24.75. We cannot see those two before lock yet. Once a team has played, its last box can (below).
- **Starters:** RotoWire listed 13 of 20 teams, 60 of its 65 right. The other 7 teams are correctly not
  benched (`team_listed`). Tagged Q: 3 of 12 played (3 of 6 at $4,100+), vs `Q_PLAY` .7; not enough data.
- **Out list worked:** the 8 box rows not on our board all logged 0 minutes.
- **Baselines**, the 152 priced rows: shipped MAE 9.56 / rho .476, salary line 9.97 / .332; on the 131 with a
  DK FPPG, shipped 9.12 / .507, raw FPPG 9.55 / .385. Field ownership ranks them better than we do, .589.
- **Ownership** (GPP 197428763, field 740; cash not pooled): MAE 2.69, rho .73, but far too flat. The field's
  top 10 averaged 30.0% and we had them at 7.4%; Joshua Dent 37.7% vs 6.3%. Nothing tried beat that held out
  by game (value, salary, FPPG; top-10 MAE 22.6-28). `own^2` was 18.3 in sample only. Not shipped. Top 1% of
  lineups summed 134% ownership, the field 139%: no answer yet on whether it matters, one contest.
- **Two players, one name:** `parse_contest_file` gave both Trent Perrys the last row. Now left out and said
  on stderr, in every sport (3 boards of 237; `contests.db` still holds the merged name, 3 boards).

**After each contest:**
1. `harness run import-contests`, then `harness run ncaab-box-grade -- <gid>`, then `--fit`.
2. `harness run calibrate-nba -- data/contest-standings-N.csv --sport ncaab` for floor/ceiling coverage;
   `--fit-ownership` reads, never ships, below ~5 slates.
3. Refit `SALARY_LINE` each night until it settles: `harness run ncaab-prior-fit -- <gid>`.

**Known gaps.** DK FPPG will start mixing this season in as games are played, and neither Torvik's 2027
file nor last game's box (minutes, who started) is read yet: on a later slate a player's "last season" goes
stale, and a Bivins stays invisible. Next build target; of 11/3's 16 teams, only Lipscomb's game is on 11/2's board. `harness/late_swap.py` does not cover
college. Ownership is the untouched prior and `own_weight` is 0, Adam's call to adjust per night
until an export says otherwise (GPP_META: ownership had no ROI effect at fixed projection in MLB/NHL;
never measured for college).

## CourtIQ: position shuffle when a player sits (2026-10-29)

`pages/14_🔀_NBA_CourtIQ.py` + `edge/nba_courtiq.py`: pick a team, mark up to three players
OUT, and see what the team did in the games they all missed -- who started at each NBA.com
slot (PG/SG/SF/PF/C: did the backup C start, or did the PF slide to C and a wing start at
PF?) and every teammate's minutes, DK points, DK/min, start rate and usual slot, with vs.
without, with n (under 3 games flagged). Game level, not on-court stints. Built from the
lineup cache (starters with slots, 2,602 team-games over 2025-26 + 2026-27) and the box
scores by scripts/nba_history_publish.py (06:00 / 14:00), published as data/nba_courtiq.json
on the data branch. `?team=HOU&out=<nba id>` opens a split directly. Research view only:
a same-position transfer on top of the minutes model lost held out (cycle 21), so nothing
here feeds projections yet; a per-team with/without minutes transfer is the open test.

## Ceilings: empirical tails (2026-10-30, overnight review) — SHIPPED

`edge/dfs_nba_theory.simulate` draws each player from his projection bucket's measured error
quantiles (`edge/nba_ceiling_quantiles.py`, z = (actual - proj) / sd_for(proj), fitted on 2024-25,
27,407 player-games, de-meaned so the mean stays the projection) instead of one gamma shape for
every tier. Held out 2025-26 (27,575): 90th coverage proj<10 0.829 -> 0.891, 30-45 0.938 -> 0.906,
45+ 0.957 -> 0.916 (target 0.90); random 8-man lineups P(actual > 90th) 0.067 -> 0.094, pinball
5.480 -> 5.367. A 50-pt star's 90th 70.5 -> 65.2, a 5-pt player's 13.5 -> 14.1: GPP builds stop
overpaying for star ceilings (155966 on 10/31: GPP dropped Mitchell $8.7k; cash unchanged).
Fitted teammate/opponent correlations matched ROLE_CORR within noise (lineup 90th moves 0.4 pt):
unchanged. `EMPIRICAL_TAILS = False` restores the gamma. College (ncaab_sim) is untouched.
