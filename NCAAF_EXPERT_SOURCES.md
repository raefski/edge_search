# NCAAF expert and data sources: what is trustworthy, and what it could add to DFS

**Date:** 2026-11-07. **Status:** research only. Nothing here has been tested on our slates yet.

## Method

1. I read CLAUDE.md, NCAAF_STATUS.md (§3 ladders, §5 not validated, §10 five-slate
   replay) and NCAAF_CRITIQUE_LOG.md first, so each source is judged against what the
   model already has:
   - DK milestone ladders give player means (MAE 7.38 on 377 laddered players, 5 slates).
   - cfbfastR box-score usage was tested and lost to the ladder (8.16 vs 7.38). Ladder + usage
     improved MAE by only 0.07, which is noise.
   - Ownership is a value softmax plus a DK FPPG term. It still misplaces about half of the
     field's ownership.
   - 4-12% of GPP ownership goes to players with no ladder.
   - 13 game-slots had no DK ladder at all.
2. I searched the web on 2026-11-07 and read primary pages where they would load:
   - Prediction Tracker results tables for 2025 (808 games) and 2026 to date (500 games).
   - The Big 12 availability policy page.
   - CFBD's data-availability and API-tier pages.
   - ETR's CFB product FAQ.
   Secondary news reports were used for the conference availability rules and are flagged as such.
3. **What counts as trustworthy:** a public, graded, out-of-sample record. That means results
   against final scores or closing lines, a documented method, or academic or market evidence.
   Self-reported records and "most accurate" banners count as claims, not evidence.
4. **Not verified:** anything marked *(unverified)* came from a secondary source or from prior
   knowledge and was not re-read today.

**Bottom line first.** Every published team-strength system loses to the betting line on
error, in both 2025 and 2026. A model built on the market should not expect points from SP+,
FPI, Sagarin or FEI. The money that is not already in the props sits in four places:
- **Availability:** who is out, and who replaces him.
- **Players the books did not price:** depth chart and usage.
- **Ownership:** what the field reads.
- **News between the last ladder scan and lock.**

The top 5 below target those four.

---

## 1. Team-strength and game-projection systems

### The yardstick: The Prediction Tracker (Todd Beck)

[thepredictiontracker.com/ncaaresults.php](https://www.thepredictiontracker.com/ncaaresults.php)
grades about 50 computer systems against every FBS game. It reports straight-up %,
mean absolute error (MAE) of the margin, bias, MSE and ATS %, with the opening, midweek and
updated (near-closing) lines in the same table. It is free and scrapeable. Past seasons load with
`?type=1&year=YY` (2025 checked). This is the right independent judge for every system below.
I could not find its totals-results page: `ncaatotals.php` returns 404.

**2025 full season (808 games, through 2026-01-20), margin MAE:**

| system | MAE | ATS % | note |
|---|---|---|---|
| Computer Adjusted Line | 11.84 | 48.4 | the line plus a small computer nudge |
| **Line (updated)** | **11.85** | 47.4 | close to the closing line |
| Line (opening) | 11.89 | 51.8 | |
| System median / average | 11.97 / 11.98 | 51.8 / 52.8 | consensus of all computers |
| Sagarin Ratings | 12.08 | 52.8 | best named single system |
| TeamRankings.com | 12.13 | 51.1 | |
| ESPN FPI | 12.25 | 48.2 | |
| FEI Projections (Fremeau) | 12.31 | 50.7 | |
| Massey Ratings | 12.39 | 49.9 | |
| Massey Consensus | 12.71 | 50.1 | |

**2026 to date (500 games, through 2026-11-01):** the updated line has an MAE of 11.48 and
ranks first. Sagarin Points comes in at 11.82, FPI at 11.91 (492 games), FEI at 12.27, and Massey
Consensus at 13.10.

**Verdict:**
- No published system beats the closing line on error.
- The "ATS %" column measures a system against the line, and none of them clears the roughly
  52.4% needed to cover the vig.
- Sagarin's 52.8% on 808 games is within noise: the standard error is about ±1.8 points.

### Each system

| system | what it is | trust evidence | access | translation for us |
|---|---|---|---|---|
| **SP+** (Bill Connelly, ESPN+) | Opponent-adjusted efficiency and explosiveness with a returning-production and recruiting prior | Not on Prediction Tracker. Older self-reports: about 55% ATS mid-2018 ([Study Hall](https://www.footballstudyhall.com/2018/11/2/18055394/college-football-projections-sp-plus)); preseason win projections beat Caesars totals 60% (74% at a gap of 1+ win), 2021, per Connelly. **No graded 2024-26 ATS record found.** CFBD republishes SP ratings (`/ratings/sp`) | ESPN+ paywall; ratings mirrored by CFBD | Low value for points. It only helps a game with no line, and every DK-slate game has one. Possibly useful early season (weeks 0-3), when lines lean on priors, but the market prices SP+ anyway |
| **ESPN FPI** | Expected-margin model | PT 2025: 12.25 MAE, worse than the line (11.85) | free on ESPN; CFBD `/ratings/fpi` | Same as SP+. Skip |
| **FEI** (Brian Fremeau, [bcftoys.com](https://www.bcftoys.com)) | Possession-efficiency rating | PT 2025: 12.31 MAE, 50.7% ATS | free | Possession and drive efficiency is conceptually useful (drives × points per drive = team total), but the market's team total already does this. Skip |
| **Sagarin** | Elo and points-based ratings | PT 2025: best named system at 12.08 / 52.8% ATS, still behind the line | free ([sagarin.com](https://sagarin.com/sports/cfsend.htm)) | Skip for points. A fallback team total for an unpriced game only |
| **Massey** ([masseyratings.com](https://masseyratings.com/cf/aboutcomp.htm)) | Ratings plus a comparison of about 100 systems | PT 2025: 12.39; the composite is worse at 12.71. Massey himself says the ratings "measure past performance, not necessarily predict" | free | Skip |
| **Phil Steele** | Preseason magazine, depth charts, experience charts | "Most accurate preseason magazine" per Chris Stassen's [football.stassen.com](https://football.stassen.com) tracking since 1993; Athlon has ranked ahead in some windows ([OU 2009](https://soonersports.com/news/2009/6/18/208394843), [Steele PDF](https://plus.philsteele.com/Blogs/Blog_PDFs/Most_Accurate.pdf)). That grades preseason rankings, not weekly games | magazine; philsteele.com paid | Not weekly. Skip |
| **collegefootballdata.com (CFBD)** | The data layer: plays, drives, PPA (EPA), advanced box, player usage, returning production, transfer portal, depth-free rosters, **historical betting lines (2013+)**, **weather (2001+)** ([availability](https://apinext.collegefootballdata.com/data-availability)) | A data source, not a predictor. PPA is its own EPA implementation, and the method is only partly documented | **Free key: 1,000 calls/month. Weather and live data need Patreon Tier 1 at $1/month (5k calls). Tier 2 is $5/month for 30k** ([tiers](https://collegefootballdata.com/api-tiers)) | Inputs for the top-5 tests (player usage, weather, historical lines for backtests). We already use cfbfastR, which shares its lineage |
| **cfbfastR / Game On Paper** | Open play-by-play with EPA and success rate (sportsdataverse); gameonpaper.com is the game-summary front end (Akshay Easwaran, *unverified*: the site returned 403 to the fetch) | Open code, reproducible | free GitHub release (already in `edge/ncaaf.py`) | Already used for truth and fitting. Box-score usage lost to the ladder (STATUS §10) |
| **PFF college** | Charted grades, snaps, routes, targets | Grades are subjective. No public out-of-sample test of grades against fantasy points was found. Data is ready by 8am ET the day after the game | PFF+ $99.99/yr includes NCAA Premium Stats; Pro $199.99/yr adds an API ([pricing](https://www.pff.com/news/a-new-chapter-of-pff-pricing)) | **Snap and route share** is the useful part (see #3). Grades: skip |
| **Sports-Reference (CFB)** | Box-score archive | Reference only | free, scrape-limited | Skip; cfbfastR covers it |
| **Academic and market evidence** | | Mixed: the CFB spread market is mostly efficient in Power-5 games. Reported pockets: non-P5 vs non-P5 games (54% with a rule, 2020); favourites overpriced 1985-2003 ([Sinkey & Logan](https://aeaweb.org/conference/2010/retrieve.php?pdfid=406)); **totals biased by censoring at zero, so team totals plus spread beat 55% for two decades** ([Arscott 2023, J. Sports Econ.](https://econpapers.repec.org/RePEc:sae:jospec:v:24:y:2023:i:5:p:664-689)) | | The Arscott result matters most here: low team totals are skewed. A team implied at 14 has a floor at 0 and a long right tail. Our ladders measure the player distribution directly, so this already lives in the player means. Check it only if we start deriving team totals |

**What the market does not price that these sources describe:**
- pace (plays per game)
- pass/rush split
- red-zone efficiency

For a **laddered** player, all three are priced into his ladder. STATUS §10 measured that
re-estimating them from box scores is worse than the ladder (8.16 vs 7.38 MAE). Where they could
matter is an **unladdered** player's volume, through the team's play count. Even there, usage plus
salary only reached MAE 4.07 against 4.21 for predicting zero. So pace and efficiency feeds rank low.

---

## 2. Player-level availability and usage

### Conference availability reports (the biggest change since 2024)

| conference | first season | schedule (Saturday game) | designations | source |
|---|---|---|---|---|
| **Big Ten** | 2023 (one report, 2h before kickoff) → **2026: four per week** | Wed / Thu / Fri by 8pm ET, then a game-day report **2h before kickoff**. Conference games only. Unlisted = available. The visitor reports its travel party plus regular two-deep players | probable / questionable / doubtful / out / out first half; game day: GTD / out / out first half | [Altoona Mirror, 8/2026](https://www.altoonamirror.com/sports/psu/2026/08/cfb-bigten-upping-availability-reports/), [bigten.org](https://bigten.org/fb/article/60284/) (*secondary; the official policy text was not read*) |
| **SEC** | 2024 | Wed by 8pm ET (7 CT), Thu and Fri updates, final report **≥90 min before kickoff** | available / probable / questionable / doubtful / out; game day: available / GTD / out | [SI](https://www.si.com/college/oklahoma/football/sec-announces-policies-and-procedures-for-new-availability-report-01j6ytq381jt), [CBS](https://www.cbssports.com/college-football/news/sec-institutes-player-availability-injury-reports-for-all-teams-during-2024-college-football-season/) (*secondary*) |
| **ACC** | 2025 | Initial report two nights before, by 8pm, then an update the next night, final **2h before kickoff**. Posted on theacc.com | same family | [AJC](https://www.ajc.com/sports/2025/09/haynes-king-status-included-in-first-availability-report-for-tech-clemson), [ACC weekly notes](https://clemsontigers.com/wp-content/uploads/2025/10/2025_ACC_Football_Weekly_Release_Week08.pdf) (*secondary; the "two days" vs "two nights" wording conflicts*) |
| **Big 12** | 2025-26 | Daily for three days at **9pm CT**, final **90 min before kickoff** | available 100% / probable 75% / questionable 50% / doubtful 25% / out 0%; game day: available / GTD / out | [big12sports.com policy page](https://big12sports.com/sports/2025/8/21/availability-reporting.aspx) (**primary, read**) |
| **CFP** | 2025-26 | 3 days out, daily, final 90 min | | [CBS](https://www.cbssports.com/college-football/news/college-football-playoff-will-require-teams-to-provide-player-availability-reports-beginning-with-2025-season) |
| **G5 / independents / non-conference games** | **none found** | — | — | No NCAA-wide football mandate was found. The NCAA's own reports cover the 2026 basketball tournaments only |

**Timing against a Saturday noon ET lock:**
- The Wed, Thu and Fri reports all land before lock.
- The game-day report lands about 10:00-10:30 ET for noon kickoffs, so it is before lock.
- For 3:30 and 7:30 kickoffs it lands after lock, but **DK CFB allows late swap** (STATUS §1).
  That makes this exactly the NBA late-swap path (`harness/late_swap.py`, MEMORY "NBA late swap path").

**Trust:** these are official, league-mandated and fined, so they carry far more weight than beat
reporters. Known weakness: coaches list liberally. "Questionable" is not 50% in practice. The Big 12
labels the percentages, but nobody has graded them, so **we should fit the real play rates per
designation per conference**.

### Depth charts, snaps, usage, returning production

| source | what | trust / evidence | access | timing |
|---|---|---|---|---|
| **Ourlads NCAA depth charts** ([ourlads.com](https://www.ourlads.com/ncaa-football-depth-charts/)) | All 136 FBS two-deeps, transfer and freshman flags | Long-running scouting service. Update cadence is not stated: pages showed stamps from 7/29 and 10/26, so freshness varies by team. Not graded | free HTML, scrapeable | irregular. Check each team's stamp |
| **Official team depth charts / game notes** | Two-deeps in weekly PDF notes (about Mon-Tue) | Official, but some coaches post "OR" charts or stale ones | free PDFs, per school | Mon-Tue |
| **CFBD player usage** (`/player/usage`, 2013+) | Share of team PPA plays by player, by down and split | Derived from play-by-play; reproducible | free key | weekly, after games |
| **CFBD returning production** (`/player/returning`, 2014+) | Connelly-style share of returning PPA and usage | Connelly reports returning production as one of the stronger preseason predictors of change. *No public graded study was found in this session* | free key | preseason |
| **CFBD transfer portal** (2021+) | Who arrived from where | Factual | free key | portal windows (Jan 2-16 for 2026) |
| **PFF snaps / routes** | Snap share, routes run, targets per route | Charted. The best public route-share source for college | $99.99/yr | 8am ET the day after the game |
| **RotoWire CFB injuries/news** ([rotowire.com/cfootball](https://www.rotowire.com/cfootball/)) | Aggregated injury and news feed | Aggregator; speed was not graded for CFB. We already log RotoWire for NBA | free page / scrape | continuous |
| **Beat writers / On3 / 247** | Practice reports, "expected to start" | Self-reported, no grading; touts-level trust | X / paywalls | continuous |

---

## 3. DFS-specific CFB experts

| source | publishes | verifiable record? | cost | timing |
|---|---|---|---|---|
| **Establish The Run** ([FAQ](https://establishtherun.com/etr-college-football-product-faq/)) | Projections for every player on every DK/FD slate, **ownership for all main slates**, GPP breakdowns | No accuracy metrics published ("unmatched" is a claim). It cites Alex Hardin winning the 2023 DK CFB World Championship and finishing 2nd in 2025; **I could not confirm this independently** | $39.99/wk, $99.99/mo, $374.99/season | *not stated* in the FAQ. Expect Fri, updated Sat morning (unverified) |
| **Stokastic** ([CFB ownership](https://stokastic.com/cfb/cfb-dfs-ownership-draftkings-fanduel-college-football-daily-fantasy)) | Projections, ownership, contest sims | No published accuracy | paid; free preview shows ownership locked | Fri-Sat |
| **Fantasy Alarm** ([ownership](https://www.fantasyalarm.com/articles/cfb/cfb-dfs-tools/college-football-dfs-ownership-projections-for-draftkings-fanduel/178501)) | DK/FD ownership projections and playbooks | none published | paid | Fri-Sat |
| **FantasyLabs, RotoGrinders** | Projections, optimizer, slate breakdowns, expert surveys | none published for CFB | paid | Sat morning updates |
| **SaberSim** | No CFB product confirmed | — | — | — |

**No CFB DFS vendor publishes graded projection or ownership accuracy.** The only way to know
is to buy a stretch and grade it ourselves against DK exports. We have the exports.

One reason ownership vendors matter even if they are not "accurate": **the field reads them.**
STATUS §10 found the field reads DK's FPPG column, and a popular paid ownership or projection sheet
is the same mechanism. A vendor's projection can predict *ownership* without predicting *points*.

---

## 4. Weather, pace and tempo

| source | what | trust | access | translation |
|---|---|---|---|---|
| **NWS api.weather.gov / Open-Meteo** | Hourly wind, precipitation and temperature at stadium coordinates | Government and open models, with documented skill | free, no key (NWS: User-Agent required) | **Delta since the ladder scan:** forecast wind at kickoff minus forecast wind at the time of our last ladder scan |
| **CFBD weather** (2001+) | Historical game-time weather | measured | Patreon Tier 1, $1/month | Backtest the effect of wind |
| **Wind research** | 7,300 CFB games 2005-2017: unders hit about 55% above 10 mph, 58% above 15 mph, 60% above 17 mph. **Wind did not hurt passing teams more than rushing teams** ([Football Study Hall 2018](https://www.footballstudyhall.com/2018/6/25/17500384/football-betting-windy-conditions-effect); the full article failed to load, so these figures come from the search summary. *Opening vs closing total not confirmed*) | One study, not replicated, pre-2018. Totals move toward the under through the week ([Action Network](https://www.actionnetwork.com/ncaaf/cfb-weather-forecast-wind-impacting-michigan-vs-rutgers-odds-3-other-week-4-games)), so much of it is priced by close | | Expect a small effect, concentrated in late-forecast changes |
| **Pace** (CFBD drives/plays, TeamRankings seconds per play, [PFF tempo](https://www.pff.com/news/college-football-the-5-fastest-operating-offenses-in-the-country)) | FBS averages about 26.9 sec/play since the 2023 clock rule; under 67 plays per game for 3 seasons ([ESPN](https://www.espn.com/college-football/story/_/id/49577220/college-football-2026-trends-see-ohio-state-miami)) | descriptive | free | Priced into ladders. Use only as the volume scale for unladdered players |

**How weather and pace shift DFS points:** they move team plays and the pass share, which reach
players through volume. A book that posts ladders 12-26h before kickoff (STATUS §10 for Tuesday
games) and refreshes them prices the forecast it had at the time. Our edge can only be (a) a
forecast change after the book's last rung update, or (b) the totals bias at high wind, if it has
survived since 2018.

---

## 5. Top 5 to try first (expected value to projections NOT already in DK ladders)

The ranking weighs: (size of the hole, measured in our own STATUS numbers) × (chance the source
fills it) ÷ effort.

### 1. Conference availability reports → availability and the promoted backup

- **Hole:**
  - Late news on cheap players is named in STATUS §10 as where the remaining error sits.
  - 4-12% of GPP ownership goes to unladdered players, who are "news, not box scores" (Caleb Smith, 21%).
  - Books pull or skew ladders for doubtful players, but a backup promoted by an Out is invisible to us.
- **Source:** SEC / Big Ten / ACC / Big 12 reports, about 1/3 to 1/2 of a main slate's games
  (conference games only). Free, official, with a fixed schedule: Wed-Fri 8-9pm ET, final report
  90 min to 2h before kickoff.
- **Translation:**
  - **availability**: zero any Out player, and treat a doubtful player as zero in cash.
  - **player share**: give the Out player's share to the next man on the depth chart (#2).
  - **ownership**: a promoted cheap backup is chalk, as Adam already flagged for NBA
    ("promoted-bench chalk").
  - Also wire it into late swap for 3:30 and 7:30 games.
- **Held-out test:**
  - Start logging the reports now; nothing past can be reconstructed. The report pages are
    probably archived, but that is *unverified*.
  - Per slate, measure (a) drafted players listed Out or doubtful and what they scored,
    (b) whether "the backup of an Out starter" predicts ownership residuals and points among
    unladdered players.
  - Fit the play rate per designation per conference on slates 1-k, then report on later slates.
  - Need ≥5 main slates. With about 4 Saturdays left plus bowls/CFP (which have CFP reports),
    this may run into **next season**: say so.

### 2. A paid ownership and projection sheet (ETR first, about $100/month) → ownership

- **Hole:** our ownership misplaces about 50% of the field's ownership (0.50-0.51 on the big
  October slates), and nothing tried beat FPPG held out.
- **Translation:** add the vendor's projected ownership (or its projection, which is what the
  field reads) as a term next to FPPG in the softmax. Grade the vendor's raw ownership MAE against
  ours as a baseline.
- **Held-out test:**
  - Snapshot the sheet pre-lock into `contests.db` and the projection log (a new `vendor_own`
    column).
  - Run leave-one-slate-out over ≥5 GPP slates on misplaced share and pool MAE.
  - GPP only (cash never pooled).
- **Cost and risk:** paid. A month covers about 4 Saturdays plus midweek slates. Scraping a paid
  product into an automated pipeline may breach its terms; ETR offers CSV/Solver sync for
  subscribers (*export not confirmed in the FAQ*). Single-entry GPP is where ownership pays
  (MEMORY "DFS goals").

### 3. Depth chart plus usage share for unladdered players (Ourlads + CFBD `/player/usage`, PFF routes optional)

- **Hole:** unladdered drafted players (n=217): best effort MAE 4.07 against 4.21 for predicting
  zero, which is barely better than nothing. Also the 13 unpriced game-slots.
- **Translation:**
  - **player share** = depth position × recent usage share (last 3 games, minus injured
    starters from #1).
  - × team volume (team implied total from the DK game line, and plays from pace).
  - → points for unladdered players only. Never override a ladder (ladder + usage = noise, §10).
- **Held-out test:**
  - Replay §10's unladdered set by slate with the depth chart captured pre-lock.
  - Depth charts are not archived by us, and Ourlads has no history, so **start capturing weekly
    now**. CFBD usage is historical and can be backtested today.
  - Ship only if MAE beats 4.07 on ≥5 held-out slates.

### 4. FanDuel two-sided college props → coverage (not an expert, but the largest known coverage lever)

- Already named in STATUS §10 as the next lever, with `scripts/ncaaf_overround_fit.py` fetching
  it ad hoc. It is never stored.
- **Translation:** project players DK did not ladder.
- **Test:** store FD props on every scan (24-48h out), then grade them against cfbfastR on held-out
  slates. Listed here because it beats every expert source on evidence quality: it is a market price.

### 5. Wind delta since the last ladder scan (NWS/Open-Meteo forecast; CFBD weather for backtests)

- **Hole:** small but cheap. One study shows unders hit 55-60% above 10-17 mph. Ladders are
  priced up to a day before kickoff, and the forecast can move after that.
- **Translation:** **plays and pass split**. Shrink QB and WR yardage means by a fitted factor per
  mph of forecast change since the ladder's scan time. Also add a game-level variance term for the
  GPP objective.
- **Held-out test:**
  - Backtest now on 2024-25 cfbfastR player-games against CFBD weather (Tier 1, $1/month) and
    CFBD historical lines. That measures the effect *given the closing total*.
  - Then confirm on our ≥5 logged slates, using the residual of ladder projections against
    the wind delta.
  - Expect a null on most slates. November is the only month where it can matter.

**Deliberately not in the top 5:**
- SP+, FPI, FEI, Sagarin, Massey and PFF grades all lose to the line on Prediction Tracker or
  are ungraded.
- Pace and tempo are already priced, and the §10 usage test was negative.
- YouTube/beat-writer buzz is already crawled. Our own grading: touts are coin flips.

---

## Carries to other sports (equivalents to check)

- **NFL:**
  - Official NFL injury report, Wed-Fri, with final designations Friday ([nfl.com/injuries](https://www.nfl.com/injuries/)).
  - nflverse for play-by-play, snap counts and participation ([github.com/nflverse](https://github.com/nflverse)).
  - [FantasyPros accuracy](https://www.fantasypros.com/about/faq/accuracy/) grades weekly expert rankings publicly.
  - [Prediction Tracker NFL](https://www.thepredictiontracker.com/nflresults.php) for team models against the line.
  - ETR NFL ownership: same grade-it-ourselves test as #2.
- **NBA:**
  - Official injury report (already ingested).
  - [DARKO](https://apanalytics.shinyapps.io/DARKO/), a public daily player box-score projection with a documented method and published error. *Not re-verified today.*
  - stats.nba.com tracking. NBA has no line-beating public model; the market wins.
- **NCAAB:**
  - [KenPom](https://kenpom.com) and [Barttorvik](https://barttorvik.com) (Torvik already joined).
  - [Prediction Tracker NCAAB](https://www.thepredictiontracker.com/basketball.php) grades them against the line. *URL not re-verified.*
  - No official injury report outside conference reports. The Big 12 reports cover men's basketball (primary page above).
- **NHL:**
  - Daily Faceoff lines and goalies (already used).
  - [MoneyPuck](https://moneypuck.com) xG with public data downloads.
  - [Natural Stat Trick](https://www.naturalstattrick.com) for TOI and line combinations.
  - Evolving-Hockey (paid) for GAR and xG.
  - Grade any of them against puckline and totals closes before trusting them.
- **MLB:**
  - FanGraphs projections (Steamer, ZiPS, THE BAT X), whose accuracy is graded yearly by third parties ([FantasyPros 2025 results](https://www.fantasypros.com/2026/02/most-accurate-fantasy-baseball-projections-2025-results)).
  - [Baseball Savant](https://baseballsavant.mlb.com) Statcast.
  - Confirmed lineups (MLB.com/RotoWire).
- **NASCAR:**
  - [Racing-Reference](https://www.racing-reference.info) loop data (driver rating, fastest laps, pass differential).
  - Practice and qualifying speeds from NASCAR.com.
  - Ownership is salary plus starting spot (already fitted).
- **MMA:**
  - [UFCStats.com](http://ufcstats.com) for official strike and takedown data.
  - [FightMatrix](https://www.fightmatrix.com) for ratings with a long public history. *Accuracy against the closing moneyline is not verified.*
  - [BestFightOdds](https://www.bestfightodds.com) for line history and closing-line value. The market beats the ratings, as in CFB.

---

## Unverified items (check before relying on them)

- ETR's claim that Alex Hardin won the 2023 DK CFB World Championship and finished 2nd in 2025: from ETR's own FAQ; independent search found nothing.
- Exact Big Ten 2026 and ACC report deadlines: secondary reports only. The Big 12 policy page is the only primary source read.
- Whether conference availability reports are archived per week (needed for backtests).
- SP+'s 2024-26 graded ATS record: not found; Prediction Tracker does not carry SP+.
- Wind-study figures (opening vs closing total; sample through 2017).
- Game On Paper's maintainer and current status (the site returned 403).
- Prediction Tracker's totals-results page URL.
