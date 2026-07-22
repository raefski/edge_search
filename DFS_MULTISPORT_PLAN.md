# Multi-Sport DFS Plan — NFL and NBA, One App

Written 2026-07-21. Scope: extend the MLB DFS system (DFS_METHODOLOGY.md,
DFS_IMPROVEMENT_PLAN.md) to NFL and NBA under a single "pick your sport" app, in time for
real use when each season opens. This is a **planning document** — nothing here has been
built yet. Every factual claim below (market keys, scoring rules, real schedule dates) was
checked directly rather than recalled from training data; where a claim couldn't be
independently confirmed, that's stated plainly rather than presented as fact.

## 0. The real calendar, checked directly

Pulled live from the Odds API (`get_sports()`, `get_events()` — both free, cost=0):

- **NFL**: `americanfootball_nfl` is already active with **75 real scheduled games**,
  season opener **2026-09-10**. `americanfootball_nfl_preseason` has one game listed so far,
  **2026-08-07** (Hall of Fame Game) — meaning a testable, real, live slate exists in about
  **2.5 weeks**, well before the "regular season" most people mean by "NFL starts."
- **NBA**: `basketball_nba` (the real in-season key) doesn't even appear in the sports list
  yet — only the year-round `basketball_nba_championship_winner` futures market exists right
  now. This is normal (the API only lists a sport as active once it has a real schedule to
  quote), but it means **no live NFL/NBA player-prop verification is possible today** for
  NBA at all, and only thinly for NFL (preseason props, if posted, will be the first real
  test). This shapes the whole plan: **backtesting has to lean on free historical data now,
  live-prop validation waits for real games.**

So: "NFL in a month" and "NBA in 3 months" (the user's own framing) map to real, verifiable
dates — NFL preseason is actually sooner than a month, NBA has real breathing room.

## 1. What's confirmed vs. still to verify

**Player-prop market keys** (from The Odds API's own current documentation, fetched
2026-07-21 — see markets list below). **DK's exact scoring values** were cross-referenced
across multiple independent sources (search results converged on the same numbers for every
category checked) but **DK's own rules pages timed out/blocked every fetch attempt** — these
numbers should get one final direct confirmation against a live DK contest page before any
scoring formula ships in code, exactly the same discipline this project already applies
everywhere else ("verify against real data, don't assume").

### NFL

**Player-prop markets that exist** (Odds API, `player_*` keys, each with an `_alternate`
line-shopping variant): passing — `player_pass_yds`, `player_pass_tds`,
`player_pass_completions`, `player_pass_attempts`, `player_pass_interceptions`,
`player_pass_longest_completion`; rushing — `player_rush_yds`, `player_rush_attempts`,
`player_rush_tds`, `player_rush_longest`; receiving — `player_receptions`,
`player_reception_yds`, `player_reception_tds`, `player_reception_longest`; combo —
`player_pass_rush_yds`, `player_rush_reception_yds`, `player_pass_rush_reception_yds`,
`player_rush_reception_tds`; TD-scorer — `player_1st_td`, `player_anytime_td`,
`player_last_td`, `player_tds_over`; kicking — `player_field_goals`,
`player_kicking_points`, `player_pats`; individual-defender — `player_sacks`,
`player_defensive_interceptions`, `player_solo_tackles`, `player_tackles_assists`.

**No team-DST prop market exists at all.** This is the single most important architectural
fact for NFL: the DST roster slot cannot be projected the props-based way pitchers/skill
players are. It needs its own model (§3).

**DK Classic scoring** (converged across sources, not yet DK-page-confirmed): passing 1
pt/25 yds + 4/TD; rushing 1 pt/10 yds + 6/TD; receiving 1 pt/10 yds + 6/TD + **1 pt/reception
(PPR)**; +3 bonus at 100+ rush yds, 100+ rec yds, or 300+ pass yds; fumble lost −1;
interception thrown −1; 2 pt conversion (pass/rush/rec) = 2 pts. **DST**: sack +1,
interception +2, fumble recovery +2, safety +2, blocked kick +2, defensive/return TD +6,
points-allowed tiers **0 pts→+10, 1–6→+7, 7–13→+4, 14–20→+1, 21–27→0, 28–34→−1, 35+→−4**.
**Roster**: Classic = QB/RB/RB/WR/WR/WR/TE/FLEX(RB-WR-TE)/DST, 9 slots, $50,000 cap.
Showdown/Captain Mode (single-game slates — relevant for the Aug 7 Hall of Fame Game and
Thu/Mon-night slates) = 1 Captain (all points ×1.5, priced up) + 5 FLEX (any position), 6
slots, same cap — not yet independently re-confirmed this session, stated at normal
confidence from established DFS convention.

### NBA

**Player-prop markets**: `player_points`, `player_rebounds`, `player_assists`,
`player_threes`, `player_blocks`, `player_steals`, `player_turnovers`, combo props
(`player_points_rebounds_assists`, `player_points_rebounds`, `player_points_assists`,
`player_rebounds_assists`), `player_field_goals`, `player_frees_made`,
`player_frees_attempts`, `player_double_double`, `player_triple_double`,
**`player_fantasy_points`** (a market book-quoted directly on fantasy output — no MLB
position had this; needs its own calibration against DK's exact formula, since books
building "fantasy points" almost never use DK's specific weights, but as a same-direction
signal this is a real, underused advantage NBA has over both other sports).

**DK Classic scoring** (converged across sources): points ×1, three-pointer +0.5 bonus (on
top of the 1 pt already earned for the point), rebound ×1.25, assist ×1.5, steal ×2,
block ×2, turnover ×−0.5, double-double bonus +1.5, triple-double bonus +3 (does not
stack with double-double). **Roster**: PG/SG/SF/PF/C/G(PG-SG)/F(SF-PF)/UTIL, 8 slots,
$50,000 cap, minimum 2 different teams and 2 different games represented (a real
structural constraint the optimizer must enforce, parallel to MLB's max-5-per-team rule).

**This means NBA has book-quoted props covering essentially every scoring category DK
uses, including a direct fantasy-points line.** Of all three sports, NBA is the one where a
genuinely props-driven mean projection (the same approach that gave MLB pitchers their
0.35 backtest corr) looks most promising on paper — worth testing early and rigorously
rather than assuming, same as every other claim in this doc.

## 2. Architecture: duplicate first, abstract later

**Recommendation: do not generalize `edge/dfs*.py` into a sport-agnostic framework yet.**
Guessing the right abstraction boundary from one working example (MLB) is a classic trap —
NFL's DST-has-no-props problem and NBA's fantasy-points-market advantage are exactly the
kind of sport-specific wrinkle a premature abstraction would fight. Build NFL and NBA as
their own modules, following the SAME conventions `edge/dfs.py` established (a projection
function per position group, a scoring function, an ownership model, a `build_slate`-shaped
orchestrator) but with fresh code. Once 2–3 sports exist side by side and the REAL shared
seams are visible (not guessed), do one deliberate extraction pass.

**What already IS shared, today, with zero changes needed:**
- `edge/client.py::OddsAPIClient` — already sport-parameterized (`sport: str` on every
  call), already cost-tracked (`markets × regions`, confirmed identical for NFL). No new
  client code needed for either sport.
- The credit ledger, dry-run/cache-mode pattern, the whole "CACHE mode is 0 credits, one
  paid live pull refreshes the cache" workflow — directly reusable as-is.
- The METHODOLOGY (not the code): leak-free train/test backtesting, forward-test logging,
  killed-signal discipline, real-contest calibration — this is the playbook to repeat, and
  it's exactly what took MLB from a flat 0.02-corr model to a validated 0.18 one.

**What's new per sport:** `edge/nfl.py` + `edge/nfl_run.py` (+ reuse `edge/dfs_opt.py`'s
*shape* — randomized greedy + hill-climb — but with NFL's own roster/position/stacking
constraints, likely as a new `edge/nfl_opt.py` since the constraint set genuinely differs);
same pattern for `edge/nba.py` + `edge/nba_run.py` + `edge/nba_opt.py`.

**UI: Streamlit's native multi-page app support**, not custom routing. Move the current
`app.py` MLB UI into `pages/1_⚾_MLB.py` unchanged (zero risk to the working, tested MLB
flow), add `pages/2_🏈_NFL.py` and `pages/3_🏀_NBA.py` as they're built. Streamlit
auto-generates the sidebar sport-picker from the `pages/` directory — this is the literal
"one app, pick your sport" mechanism the user asked for, with no bespoke plumbing.

**Made sport-aware now, not later:** `scripts/dfs_entry_history.py` currently hardcodes
`Sport == "MLB"`. The user's real DK entry-history export almost certainly already contains
other sports mixed in (NFL/NBA contests they've played historically, or will once these
ship) — generalizing the filter to report per-sport AND combined ROI is a small change with
immediate value, and it's the one piece of existing tooling that directly delivers on
"one-stop-shop" before any new sport's model even exists.

## 3. NFL — edge thesis, data, and the DST problem

**Edge thesis**: the same structural one as MLB (frozen weekly DK salary vs. live-moving
props/lines), plus a NEW angle MLB never had — NFL is a **weekly** cadence, not daily. That
changes the operating rhythm entirely: one real build cycle per week (open Tue/Wed as lines
firm up, refine through the week's injury reports, finalize close to Sunday lock) rather
than MLB's near-daily grind. Less automation pressure, more room to get each week right.

**Skill players (QB/RB/WR/TE): props-driven, parallel to MLB pitchers.** The full market
list above covers passing/rushing/receiving yards and TDs directly — this is structurally
the closest thing to MLB pitcher props of anything in this plan. Start there, backtest
against free historical data before trusting it (see below) — don't assume it'll work just
because MLB pitchers did; every other assumption in the MLB build got tested and several
failed (batter props flopped at 0.02 corr for the exact same "single-game props barely
spread" reason that could just as easily bite NFL WR receiving-yard props on a given week).

**DST: no props market exists — needs an entirely different model.** Concrete approach:
project DST from (a) the opponent's implied team total (free-tier `h2h`/`spreads`/`totals`
game odds, cost 3 credits per pull for the whole slate, not per-team) as the primary
points-allowed-tier driver, blended with (b) the DST's own season/recent defensive stat
rates — sacks/game, takeaways/game, defensive+return-TD rate — pulled from free historical
data. This is a genuinely new projection *shape*, not a reuse of anything MLB has; budget
real design/testing time for it specifically, not just "same as pitchers but defense."

**Free historical data for backtesting (the MLB playbook — free box scores, not paid
props):** the **nflverse** ecosystem (community-maintained, hosted as free CSV/parquet
releases on GitHub — `github.com/nflverse/nflverse-data`) has full play-by-play and
player-week stats back decades, more than enough for a leak-free 25,000-row-scale backtest
exactly like the one that validated MLB's hitter model. This is the first build step,
before any live props are needed: reconstruct real weekly fantasy scores from free data,
test a skill-based projection AND (separately) whether historical props would have beaten
it, the same head-to-head test MLB ran for hitters. Unlike MLB hitters, **the user has
confirmed credit budget for a modest paid historical-props pull** if the free-data test
leaves that question open — a real, newly-affordable option MLB's own doc explicitly
didn't have.

**Correlation/stacking, a genuinely new design question:** NFL GPP correlation runs through
QB↔pass-catcher stacks (a QB's passing yards and his WR's receiving yards are the same
yards) and game-total-driven bring-back stacks (rostering both offenses in a projected
shootout) — a different mechanism from MLB's batting-order-adjacency logic, needs its own
simulator design once `edge/nfl_sim.py` is built, not a port of `edge/dfs_sim.py`'s
run-allocation logic.

**Realistic timeline**: MVP (props-based skill-player projections + a first-pass DST model
backtested on nflverse data + Classic roster/optimizer + a heuristic ownership model + the
new app page) is achievable before the **Aug 7 preseason slate** if prioritized now — that
game is real, live, and bettable, making it a genuine first test rather than a simulated
one. Full maturity (multiple rounds of ownership recalibration, a validated field simulator,
real forward-tested weeks) took MLB roughly 3–4 real weeks of *daily* forward-testing;
expect NFL to need a comparable number of real *game weeks* — meaning realistically not
fully mature until well into the season. That's fine; it's the same organic path MLB took,
just on a slower clock.

## 4. NBA — edge thesis, data, and why late-swap is the priority

**Edge thesis — stated with real optimism, but still to be tested, not assumed:** the
user's own instinct here is the correctly-identified, industry-recognized NBA DFS edge —
**vacated usage**. When a rotation player is out, the players around him absorb minutes and
usage in a fairly predictable way based on recent role, and the field (particularly
recreational, high-volume-GPP players) is often slow to fully re-price this close to lock
because news lands late and DK salaries don't move at all. This is a *structurally cleaner*
mechanism than MLB's salary-staleness thesis, which §10's own rigorous test found didn't
clearly beat salary alone — worth real confidence here, but it still needs the same
discipline: measure it, don't assume it, before leaning on it.

**This is exactly why the user is right that late-swap matters more for NBA than any other
sport.** `edge/dfs_swap.py`'s replacement-suggestion tool already exists and works for MLB;
for NBA it should be treated as core functionality from day one, not a nice-to-have added
after the main build — build the NBA equivalent (`edge/nba_swap.py`) in the SAME pass as the
initial projection model, not after. The practical mechanism: track questionable/doubtful/out
designations as close to lock as data allows, and give a real minutes/usage-bump estimate to
the backup(s) who'd absorb an out player's role, ranked into the swap-suggestion tool the
same way MLB's does today.

**Data**: `stats.nba.com`'s own backend (unofficial, but the standard free source the entire
public NBA-analytics community uses — e.g. via the `nba_api` package) or `balldontlie.io`
(simpler, free, rate-limited) provide full historical box scores for a large-n leak-free
backtest, the same free-data-first approach as everywhere else in this project. **Flagged
honestly**: unlike MLB's `statsapi` (an official, stable, consistently-available league API
this whole project has leaned on without incident), `stats.nba.com` is NOT an officially
documented third-party API — it's known in the community to occasionally need specific
request headers and can be flakier/more rate-limit-sensitive than MLB's source has ever been.
Worth a small early spike to confirm reachability from this environment before committing
the whole backtest pipeline to it, rather than assuming it'll be as frictionless as statsapi
was throughout the entire MLB build.

**Props coverage is a real advantage here** (§1) — `player_fantasy_points` plus book lines on
every individual DK scoring category means NBA is the best candidate of the three sports for
a genuinely props-driven mean model, worth testing head-to-head against a skill-based model
exactly like MLB did for both its pitchers (props won) and hitters (props lost) — don't
assume which way NBA breaks; measure it.

**Correlation/stacking, a third distinct design question:** NBA GPP correlation runs through
pace and blowout risk — a fast, high-total game lifts both teams' box-score counting stats
together, while a blowout compresses starters' minutes and inflates bench/garbage-time
usage on the losing side. Neither MLB's batting-order logic nor NFL's QB-stack logic
transfers; `edge/nba_sim.py` needs its own design once there's a real projection model to
anchor it to.

**Realistic timeline**: 3 months is genuinely comfortable. Recommended order: free-data
backtest first (no rush, do it right), then the late-swap-centric app build, then — only
once the real season is close enough that `basketball_nba` shows up as an active sport with
real events — a live-props validation pass, mirroring the same free-first, paid-last
discipline as NFL.

## 5. Cross-cutting work (do regardless of which sport is prioritized first)

- **`dfs_entry_history.py` → sport-aware.** Small change, immediate value across all sports
  the moment there's any NFL/NBA history to report on, and arguably the single fastest way
  to make this feel like "one app" before either new sport's model exists.
- **Contest metadata schema (`contest_meta.json`) already sport-agnostic in shape** (keyed
  by contest id, not sport) — no change needed, just start tagging NFL/NBA contest ids the
  same way once they exist.
- **The calibration/dashboard PATTERN, not the code**, repeats per sport: leak-free backtest
  → ship → forward-test log → real-contest grading → killed-signal discipline. Each sport
  gets its own `scripts/nfl_grade.py`/`scripts/nba_grade.py` etc. following
  `scripts/dfs_grade.py`'s shape, not a shared abstraction (see §2).
- **Credit budget**: no live spend needed yet for either sport (no games to bet on for real
  right now). First real spend will likely be a bounded, planned historical-props backtest
  pull for NFL and/or NBA specifically to test props-vs-skill-model head to head — worth
  sizing a concrete credit budget for that pull once the free-data backtest scaffolding
  exists and shows exactly what's still an open question, rather than an open-ended spend
  authorized in advance.

## 6. Priority order and near-term next steps

1. **NFL free-data backtest scaffolding** (nflverse ingestion + a leak-free train/test
   harness mirroring `scripts/dfs_hitter_backtest.py`'s shape) — the highest-urgency item
   given the Aug 7 preseason date is real and close.
2. **NFL DST projection model** — the one piece with no MLB analog at all; needs dedicated
   design time, not a copy-paste.
3. **NFL Classic roster/optimizer + heuristic ownership + app page** — an MVP is genuinely
   achievable before Aug 7 if 1–2 start now.
4. **`dfs_entry_history.py` sport-awareness** — small, do it anytime, high leverage for the
   "one-stop-shop" feel.
5. **NBA free-data backtest scaffolding** (stats.nba.com/balldontlie spike first, to
   de-risk the data source before building on it) — start once NFL's MVP is in a stable
   state, no need to rush given the 3-month runway.
6. **NBA late-swap tooling**, built alongside (not after) NBA's initial projection model,
   per the user's own correctly-prioritized instinct.
7. **NBA Classic roster/optimizer + app page.**
8. **Live-props validation for both sports**, only once each sport's real season is close
   enough that the Odds API actually lists live, bettable events with posted player props.

## 7. Open decisions for the user

- Confirm DK's exact NFL/NBA scoring values directly (a live DK contest page, or the app
  itself once opened) before any scoring formula ships in code — everything above is
  cross-source-converged but not DK-page-confirmed this session.
- Sign off on the "duplicate first, abstract later" call in §2 — the alternative (a shared
  sport-agnostic framework from day one) is more elegant on paper but riskier to get right
  with only one real example (MLB) to generalize from.
- Confirm priority: this plan assumes NFL first (real, closer deadline) with NBA following
  once NFL's MVP is stable — say so if a different order is wanted.
- Authorize a specific, bounded credit spend once the free-data backtest for either sport
  identifies a concrete open question a historical-props pull would resolve, rather than a
  blank check now.
