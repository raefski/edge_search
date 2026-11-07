# NCAAB expert and data sources: what is trustworthy, and what it adds to DFS

**Date:** 2026-11-07. **Status:** two sources tested held out on our 5 graded slates
(155565 11/2, 156039 11/3, 156123 11/4, 156240 11/5, 156342 11/6); the rest is research
and marked *(unverified)* where the record was not re-read today.

**What counts as trustworthy:** a public, graded, out-of-sample record against final scores
or closing lines, or a test on our own slates. "Most accurate" banners are claims.

**Bottom line first.** Like football (NCAAF_EXPERT_SOURCES.md), the best published team
rating loses to the betting line: Torvik's own day-before snapshot was worse than the
pre-tip market on 203 D1 games 11/2-11/6 (margin MAE 10.51 vs 9.26, total 13.68 vs 12.99),
and its disagreement with the market did not predict the market's error. The money is not
in team ratings. It is in **this season's box scores**, which our board does not read at
all (DK's FPPG on the board is still LAST season's on 11/6: corr with this season's
per-game DK points 0.39-0.50) and which the books only price for the ~6% of players with
props.

## 1. Ranked by what we could verify

| # | source | verified track record | free / fetchable | turns into | tested here |
|---|---|---|---|---|---|
| 1 | **ESPN box scores, this season** (site.api.espn.com summary) | the truth itself; our grader already uses it | yes, 1 call per game | per-player prior-game DK points / minutes / started, DNP = 0 | **yes: wins** (§2) |
| 2 | **Barttorvik T-Rank time machine** `barttorvik.com/timemachine/team_results/YYYYMMDD_team_results.json.gz` | point-in-time daily snapshot (Last-Modified 10:30 UTC the next morning, before every slate lock); vs market below | yes | adjOE/adjDE/adjT -> team points | **yes: loses to the market** (§2) |
| 3 | **Barttorvik player table 2027** `getadvstats.php?year=2027&csv=1` | same boxes as #1, aggregated | yes | season-to-date per player | **not point-in-time**: `start=/end=` params are ignored (Duke rows show gp=2 incl. 11/5 with end=20261102). Use only from a snapshot we save before lock, or rebuild from #1 |
| 4 | Barttorvik 2026 player table (last season) | already in board (`edge/ncaab_torvik.py`, carryover) | yes | prior | in board |
| 5 | Sportsbook player props (DK/FD) | market; props tier MAE 8.54 on n=67 | via odds client | means | in board; #1 does NOT add on this tier (n=23) |
| 6 | Market game totals/spreads | beat Torvik on 203 games | in board | implied team points | in board |
| 7 | KenPom (paid) | widely cited; Prediction Tracker grades it *(unverified today)* | no (subscription) | team ratings | not tested; expect the same as #2 |
| 8 | EvanMiya (paid tier for player BPR) | lineup/player BPR, no public graded record found *(unverified)* | partly | player impact, minutes | not tested |
| 9 | Haslametrics | team ratings, graded on Prediction Tracker *(unverified)* | yes | team ratings | not tested; same expectation as #2 |
| 10 | ESPN BPI | team ratings *(unverified record)* | yes | team ratings | not tested; same expectation as #2 |
| 11 | Hoop-Math (shot location, assisted rates) | descriptive, no predictive record | yes | shot mix -> 3PM | not tested; second-order for DK points |
| 12 | Returning-production / transfer portal data (Torvik, EvanMiya, On3) | descriptive *(unverified as predictive)* | partly | minutes prior for transfers/freshmen | already partly in board (Torvik carryover + team tier) |
| 13 | RotoWire lineups / conference availability notes | RotoWire logged by the board (`edge/ncaab_rotowire.py`) | yes | who is out / starting | availability agent's area |
| 14 | CBB DFS touts (ETR, RotoGrinders, Awesemo, YouTube) | no public graded record; repo's tout tests in other sports = coin flips (GPP_META.md) | mostly paid | ownership signal more than projection | not tested |

The Prediction Tracker (thepredictiontracker.com, basketball results page) is the
natural yardstick for #7, #9 and #10 as it is for NCAAF; not re-read today *(unverified)*.

## 2. Held-out tests on our slates (2026-11-07)

### 2a. Torvik T-Rank vs the market, game level (all D1 games with an ESPN final, a
pre-tip line in odds.db and a Torvik snapshot dated the day before; n=203 games, 5 days,
97 of them on 11/2)

| | market (last scan before tip, main line, median of books) | Torvik (eff x tempo, HCA 1.4%) |
|---|---|---|
| total MAE | **12.99** | 13.68 |
| margin MAE | **9.26** | 10.51 |
| slope of (actual-market) on (Torvik-market), total | +0.10, corr +0.03 | |
| slope, margin | -0.37, corr -0.13 (wrong sign) | |
| leave-one-day-out total MAE, market -> market + Torvik | 12.99 -> 13.14 (worse) | |

On the 32 slate games alone: team-points MAE market 7.23 vs Torvik 7.99 (n=64 team-games).
At the player level, proj x (Torvik team points / market team points - 1) as a residual
feature: priced 8.69 -> 8.53 LOSO but the fitted sign is negative (Torvik's disagreement
points the wrong way), +intercept alone 8.56; bench gets worse (4.46 -> 5.11); forward
priced 7.77 -> 8.16. **Not useful.** Early-season Torvik ratings are mostly preseason
priors that the market already has.

### 2b. Current-season ESPN boxes (the winner) -- see report.md §2b.

## 3. How each would become a projection input
- **ESPN prior games** -> `prior_fp` = this-season DK points per TEAM game (a team game he
  missed counts 0), blended into non-prop projections (report.md §3).
- **Torvik time machine** -> only as a fallback team total when no market line posts
  (board now uses league average there: "no total: league average"); untested in that role.
- **Torvik 2027 player table** -> snapshot it before lock each day if wanted; never
  read the live file for past slates.
- **Touts** -> ownership features only, and only after a held-out test.

**Update 2026-11-07, same day:** #1 shipped as `LAST_GAME` (edge/dfs_run_ncaab.py, read by
edge/ncaab_espn.last_games), the availability critic's minutes+points regression on the last
game, which held out slightly better than the per-game weight above. NCAAB_CRITIQUE_LOG.md cycle 1.
