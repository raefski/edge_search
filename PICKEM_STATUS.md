# Pick'em Project Status — Orientation Doc

**Purpose of this file**: a 60-second re-orientation, not a full record — the same job
`DFS_STATUS.md` does for the DFS side. Point a new conversation here first for anything
pick'em-related. Not a DFS project (no salary cap, no lineups) despite sharing "NFL" and
this repo with the real NFL DFS build in `DFS_MULTISPORT_PLAN.md` — different game, kept
deliberately separate (`edge/pickem.py`, not `edge/nfl.py`).

**Weekly steps live in `PICKEM_WEEKLY.md`** — what to actually run, in order, to feed
this project data each week. Point Adam there first for "what do I do now" questions.

**Feature history lives in `PICKEM_MODEL.md`** — how the model works in plain English,
what improved it, and the full graveyard of tested-and-killed ideas (DVOA-style efficiency
ratings, coaching features). Read that before proposing any new feature; it exists so dead
ends don't get rediscovered.

**Keep this file current.** Update it at the end of any session that changes the model,
kills a signal, or resolves/updates an open question.

---

## What this is

Adam plays in **TOO-GOODE FOOTBALL POOL**, a 15–20 player, $150-buy-in NFL spread pick'em
league on CBS Sportsline (`picks.cbssports.com`, "My Pools"). CBS posts a spread per game
(by 1pm ET Tuesday, its own contest rules say) and freezes it for the week — the real
market keeps moving on injury news, sharp money, and weather. The edge is that gap.
Real money, real prize structure ($50/week to 1st, $1,200/$450/$300/$150 season 1st–4th).

## Where the model stands — SHIPPED, validated once, not yet forward-tested live

`edge/pickem.py::make_pick` is the whole model:
- `edge = live_line - pool_line` (home-team spread, negative = home favored)
- `|edge| >= 0.5`: follow the side the market moved toward. Below that: no validated
  edge (test-era unmoved games: 50.6%, a coin flip) — default to the live market's own
  favorite rather than manufacture a signal.
- Favorite flips sides entirely -> automatic STRONG regardless of point size (the single
  strongest pattern in the data).
- `P(cover) = Phi(|edge| / 13.45)` (Stern 1991's normal approximation of NFL margin vs.
  spread) — every point of edge is worth about +3% win probability.
- Tiers: STRONG (3+ pts or flip) / SOLID (1.5–3) / LEAN (0.5–1.5) / COIN FLIP (<0.5).

**Backtest** (`scripts/pickem_backtest.py`, `data/pickem_backtest_results.json`):
2,878 NFL regular-season games (2014–2024), open+close spreads from a single book's
recorded history (`scripts/pickem_historical_collect.py` — see its docstring for the one
fragile step: the source site is dead, data comes from a pinned Wayback Machine capture).
Chronological split — train 2014–2022 (2,335 games), test 2023–2024 (543 games),
evaluated **once**, with `make_pick` itself (no separate reimplementation, no drift
possible between what's tested and what ships):

- **Test: 298-235-10 = 55.9% ATS.** Signal games (83% of slate) 56.7%, fallback/unmoved
  games 50.6%. Weekly pace: avg 8.9/16, 10/36 test weeks at a 10+/16 pace, 21/36 at 9+/16.
- Baselines beaten: blind favorites 54.2%, blind home teams 51.2%, blind underdogs 45.8%.
- **The load-bearing negative result**: run the identical strategy with `pool_line ==
  live_line == the closing line` (i.e., pretend the frozen number was never stale) —
  48.9%, a dead coin flip, baked into the backtest permanently as a sanity check. The
  edge is confirmed to be the staleness itself, not a market-beating signal in disguise.

**A real bug caught and fixed during the port (2026-08-21)**: the original prototype
(built outside this repo, in a throwaway `/home/asr/pickem` directory) validated one
fallback rule in its backtest (`fallback == 'home'`, literally always pick the home
team on a no-signal game) but the deployed script used a DIFFERENT one (live-market
favorite) — silent drift between what was tested and what shipped. Porting into this
repo's discipline (backtest calls the shipped function directly) caught it. Net effect
was tiny (55.5% -> 55.7%, both fallback rules are near-coinflip-equivalent on unmoved
games either way) but the METHODOLOGY gap was real and is now closed structurally: it
can't recur because there's only one implementation left to drift from.

## Round 2 feature work (2026-08-22) -- market features

Tested key-number weighting, moneyline drift, totals drift, and coin-flip tiebreaks.
**One small change shipped** (totals-drift tiebreak on no-movement games: dev +1.2pp,
holdout +0.2pp -- one extra win in 543, kept but unproven). **One real effect found but
NOT shipped**: crossing key numbers 3/7 covered 62.9% vs 55.7% on the holdout and beat
not-crossing in 9 of 9 dev seasons, but the dev-fitted magnitude overshot badly and made
calibration worse, so `KEY_BONUS` ships at 0.0 pending clean data. Four further experiments
(CBS post-offset, line velocity, sharp-book agreement, public-pick fading) are **blocked on
data that does not exist yet**. Full detail, numbers, and the weekly logging that unblocks
them: **PICKEM_MODEL.md sections 4, 5d-5f**.

**This was the project's SECOND holdout evaluation** (first was the original model). A third
should be resisted until there is genuinely new data -- each look inflates optimism.

## Infrastructure round (2026-08-22) -- built, mostly unvalidated

Following round 2's conclusion that further gains are not in feature engineering, built the
plumbing instead. **None of this changes the 55.9% model.**

- **Weekly capture** (`scripts/pickem_capture.py` + `edge/pickem_log.py`): append-only
  snapshot log at `data/pickem_line_log.csv` (COMMITTED -- public market data; Adam's picks
  stay in gitignored `data/pickem/`). Records CBS's line, a contemporaneous market line and
  total, per-book numbers, and community pick %. Unblocks all four PICKEM_MODEL.md 5f
  experiments. 2 credits per run, dry-run by default. **This needs to actually be run every
  Tuesday and before each deadline -- a missed week is a permanently missing row.**
- **Multi-book consensus** (`edge/pickem_live.py`): now pulls spreads AND totals across all
  available books with sharp books upweighted, exposing per-book disagreement as a
  confidence caveat. Cannot be backtested (historical file is single-book) -- the weighting
  is a documented prior, not a validated result. Circa is not on The Odds API; Pinnacle
  requires the `eu` region at double cost.
- **Standings strategy** (`edge/pickem_strategy.py`): protect/chase/neutral modes gated to
  Week 14+, divergence budget ~gap^2/weeks_remaining, spends coin flips first and never
  flips an edge above 12%. **UNVALIDATED and unvalidatable without historical pool
  standings** -- labelled as such everywhere.
- The Streamlit page now feeds the logged post-snapshot total into the shipped totals
  tiebreak, and shows book count + disagreement per game.

## Honest caveats (don't let these get lost)

1. **CBS sets spreads "at its own discretion"**, not as a mirror of a specific
   sportsbook's opener — some of any observed gap could be CBS's own house-methodology
   offset from the market rather than time-decay drift. `make_pick` has no way to net
   this out from a single reading; `data/pickem/tracker.csv`'s optional
   `live_line_at_post_home` column exists for this (capture a market reading within
   minutes of CBS posting, and the *change* since then is the truer signal) — not yet
   used in practice, first real chance is Week 1 2026.
2. **The backtest's "at lock" proxy is the true closing line; your real lock is hours
   earlier** for most games (per-day deadline, not per-game). Some late movement the
   backtest effectively "sees" won't be visible to a real pick in time. Expect live
   results a bit below 55.7%, not above, until proven otherwise.
3. **One book's open/close history**, not a multi-book consensus — noisier than ideal.
   The cover-rate-by-move-size staircase holding up in both train and test eras is the
   main reassurance this isn't just single-book noise.
4. **543 test games** -> roughly ±4% 95% CI around 55.7%. Direction-of-move (toward
   favorite vs. underdog) looked like a real signal in test (60.7%) and the *opposite*
   signal in train (56.6% the other way) — textbook noise, deliberately excluded from
   `make_pick`. Don't re-add it without a much bigger sample.
5. **No live forward-test yet.** Everything above is backtested on history. Week 1 2026
   is the first real-time run — see "Immediate next step" below.

## Model concerns raised 2026-09-10 — WRITTEN DOWN, DELIBERATELY NOT APPLIED

`edge/pickem.py` is frozen. These are observations to weigh against a real sample
later, not changes. Recording them here so they are not re-discovered as if new.

1. **NE@SEA (Week 1, the opener) would have been a WIN at the true closing line,
   and it is n=1.** CBS froze Seahawks -3.5. The Tuesday `post` reading was -3.188,
   an edge of +0.312 — under `MOVE_FLOOR`, so the model fell through to the
   coin-flip fallback, took the market favourite (Seahawks -3.5), and lost: NE 10
   SEA 13, home margin +3 against a -3.5 line. The line kept moving toward New
   England after the freeze; at the true close the edge lands at exactly 0.50, i.e.
   on the floor. **This is not a reason to touch `MOVE_FLOOR`.** One game is worth
   nothing against a floor fitted on 543. Tuning a threshold because the single
   game it excluded would have won is the purest form of the overfitting this
   project's rules exist to prevent. The honest use of this observation is as one
   data point in the *capture-timing* question (section 6: how late can we legally
   read the market), not the threshold question.
2. **The fallback is doing more work than the backtest implies, and in a worse
   spot.** `_coinflip_side` returns the market favourite, and it backtested at
   50.6% — i.e. no edge at all. On a live slate where a game has NO market reading
   (a book pulling a game, or the game having kicked off) it used to return CBS's
   frozen favourite instead, which is not the same object: it is the side the
   market moved AWAY from. That path is now closed on the page (no reading, no
   pick), but the same substitution is still available to any future caller that
   passes `pool_line` as `live_line`. A guard inside `make_pick` — refuse, rather
   than fall back, when the two arguments are the identical object — would make it
   unrepresentable. Not applied: the file is frozen.
3. **`stale_gap` in the tracker is not the model's edge.** The hand-typed rows carry
   `0.0` where the log-derived edge is `+0.312`. `scripts/pickem_grade.py` now
   prints a MISMATCH line for every such disagreement rather than silently keeping
   the old number, but the old numbers themselves are still there and are still
   what a spreadsheet formula over that column would read.

## Capture plumbing — rebuilt 2026-09-08, and it had never run

The weekly capture had banked **nothing** as of the first Tuesday of the season.
`data/pickem_line_log.csv` did not exist, locally or on the remote, and every experiment in
`scripts/pickem_blocked.py` read 0/N. Four separate reasons, all found the same evening:

1. **The GitHub workflow could never have worked for free.** It was the only scheduled
   capture, and a runner cannot read `data/odds.db` (gitignored, 1.5GB) or rebuild it
   (DraftKings 403s datacenter IPs). It can only capture through the paid API, and its
   `ODDS_API_KEY` secret was never added — so every scheduled run failed by design.
   Capture now runs on the desktop, `deploy/pickem-capture@.service`, six timers.
2. **`pickem_capture.py` could not find the API key by hand either.** It built
   `OddsAPIClient` directly, and `edge/client.py` only reads the environment; the key lives
   in `~/arbitrage/.env`. The documented Tuesday command raised `NoApiKey` unless you had
   exported it yourself. Now goes through `edge.odds.cli.load_key()`.
3. **The market join silently dropped two teams.** The market side spells them the nflverse
   way (`LA`, `WAS`), the pool side the CBS way (`LAR`, `WSH`). Unaliased, the Rams and
   Commanders logged a CBS line with no market reading beside it — the exact pairing a
   snapshot exists to create.
4. **The board spans more than one week, and the capture keyed on home team alone.** On
   2026-09-08 the feed held weeks 1 AND 2, five teams hosted in both, and all five resolved
   to the **week 2** game — including DEN@KC and BUF@HOU. Had the capture run, week 1's
   `post` snapshot would have carried next week's lines against this week's CBS numbers,
   in an append-only file. It now uses `edge.pickem_free.filter_to_slate`, the guard added
   on 2026-08-24 for this same bug in the live path, which refuses rather than guesses.

Also: the pool's deadline is per DAY, so a week has up to four locks, but
`edge.pickem_log.append` de-dupes on `(season, week, snapshot, home_team)` — one shared
`lock` label would have kept the first reading of the week and dropped the rest. Each
deadline now has its own label (`lock-wed`/`lock-thu`/`lock-sun`/`lock-mon`), and both
`pickem_transferability.py` and `pickem_blocked.py` accept the `lock*` family. The
transferability collector also refuses a reading captured after that game kicked off, since
a Monday capture still returns Sunday's games.

## Immediate next step — DONE 2026-09-11, and it produced the first live numbers

Run a real week live and grade it: capture CBS's actual Week 1 lines, let the model pick,
log results to `data/pickem/tracker.csv` (gitignored, real pool data), and see whether
live performance tracks the 55.7% backtest or comes in under it per caveat #2 above.

**CBS's lines are verified and in the log.** The `provisional` markers cleared on
2026-09-10 when the automated CBS fetch first ran (`scripts/pickem_pool_fetch.py`), and
every one of the 16 lines matched the hand transcription — the week 1 picks were right
all along. `post` and `lock-wed` were then completed on 2026-09-11 ("wrote 0 new,
completed 16" for each), which is the exact step this section used to be waiting on.

**FIRST READINGS — one week, n=16, treat the CI as the answer.** From
`scripts/pickem_transferability.py`, the measurement PICKEM_MODEL.md 5j r3/r6 calls the
single largest source of uncertainty in the project (~$400/season):

| quantity | first reading | what it means |
|---|---|---|
| implied `w` | **0.29**, 95% CI [0.00, 0.61] | how much of the backtested edge survives CBS freezing before you pick |
| margin over chalk | **+4.19 to +4.67pp** (backtest assumed w=1.0 → +6.72/+7.70pp) | **~62% of the backtested margin transfers** |
| `cbs_offset` (market at post − CBS) | **+0.316**, mean abs 0.551, n=16 | near zero ⇒ CBS posts the contemporaneous market, so essentially all the live edge is post-Tuesday **drift**, not CBS shading |
| drift: freeze → midweek | mean abs move **0.781** | |
| drift: midweek → lock | mean abs move **0.076** | |

The last two are the thing the 2014–2024 archive can never supply (it holds only two
snapshots per game) and they matter for section 6's ranking: **drift looks ~10× larger
before midweek than after it**, which is the front-loading 5j round 3(a) warned about,
now measured rather than suspected. If it holds up, "capture as late as legally possible"
buys much less than ranked. One week is not enough to act on — the CI on `w` still spans
0 to 0.61 — but the direction is now data, and ~4 weeks settles `w` because it needs no
game results at all.

**The grading half of that now exists** (`scripts/pickem_grade.py`, added 2026-09-09): it
takes CBS's frozen line and the last `lock*` reading before each kickoff, runs the *shipped*
`make_pick`, joins nflverse scores, and grades with the backtest's own `ats_result`. Prints
W-L-P overall / by tier / signal-vs-fallback against 55.9% / 56.7% / 50.6% with a Wilson
interval; `--write` fills the blank graded columns of `tracker.csv`. It now has its input
and runs clean — the first graded game is wk1 NE@SEA (coin flip, final 10-13). Week 1
completes Monday 9/14, so the real read comes then.

**It is reporting 4 populated cells in `tracker.csv` that contradict the log**, all on the
two Wednesday/Thursday games — e.g. `live_line_at_post_home` on file `-3.5` where the log
computes `-3.188`, and `stale_gap` `0.0` where it computes `0.312`. Those are provisional
numbers typed before the freeze (CBS's own line pasted into a column that wants the
*market* at post). The grader keeps what is on file and refuses to overwrite a recorded
measurement, so **they stand until someone clears those cells by hand** — that is a
judgement call about your own pool file, not something to automate. Clear them and re-run
with `--write` to let the computed values in.

## App / deployment

`pages/4_🎯_Pickem.py` — Streamlit multi-page (added to the sidebar automatically
alongside the MLB DFS home page in `app.py`, no shared code, per
`DFS_MULTISPORT_PLAN.md §2`'s "duplicate first, abstract later" call). Deployed at
https://edgesearch-h2dkcwvywzteys8e7tjk6v.streamlit.app/ — **visually verified live**
via a real headless-Chromium drive of the actual deployed URL (2026-08-21), sidebar nav
and page render both confirmed correct with screenshots.

**Live market line went through two data sources before landing on the right one:**
1. ESPN's public scoreboard API (free, no key) -- worked in isolated testing, then hit
   `403 Forbidden` / `Server: AkamaiGHost` in production, confirmed on the real deployed
   app (Adam saw the exact error banner live, not just in a sandbox).
2. DraftKings' own sportsbook eventgroups endpoint (also free/keyless when reachable) --
   tried as a direct alternative; hit the **identical** `AkamaiGHost` 403. Akamai is
   shared security infrastructure sitting in front of both ESPN and DraftKings, so this
   reads as one structural block, not two independent ones. Did not attempt to route
   around either (header changes tried once for ESPN, didn't help, an IP-reputation
   block isn't fixable by request-shaping and repeatedly trying isn't the right move
   regardless of the target).
3. **Landed on The Odds API** (`edge/client.py`, the same paid source every other sport
   in this repo already uses) -- `get_featured_odds('americanfootball_nfl',
   ['spreads'], 'us')` costs `markets x regions` = **1 credit for the entire week's
   slate in one call**, cached free for `live_ttl` (600s) after. Adam is on the Odds API
   free tier (500 credits/month, shared with MLB/WNBA use) -- at ~1 credit/pull this is
   a small fraction of that budget even checked several times a week all season.
   Dry-run/cache-first by default, matching app.py's MLB pattern exactly: nothing
   spends unless the sidebar's "💰 Pull fresh lines" button is explicitly tapped.
   `edge/pickem_live.py::_parse_events` (the JSON-parsing logic, independently testable,
   `tests/test_pickem_live.py`) means the home-team spread across bookmakers,
   a simple unweighted consensus -- revisit if that ever looks too noisy in practice.

CBS's frozen line: still cannot be fetched server-side (login-gated) -- comes from
`data/pickem_current_week.csv`, committed each time a screenshot is captured and parsed.

**Lesson learned the hard way**: adding a brand-new file to `pages/` needs an explicit
**Reboot** from the Streamlit Cloud dashboard after pushing -- a plain git push
successfully pulls the code and hot-updates the running app (confirmed in the deploy
log), but the sidebar's page list is discovered once at process startup and a hot
update doesn't reliably re-scan it. Editing an *existing* page or an `edge/*.py` module
it imports does NOT have this problem (confirmed: the Odds API fix above shipped via a
normal push+hot-update, no reboot needed). Only new files under `pages/` need the
manual reboot step.

## Quick facts easy to forget

- Uses ZERO Odds-API credits — **true again since 2026-09-08, for a different reason
  than this line originally claimed.** It was written when live data came from ESPN's
  free scoreboard; ESPN then 403'd and the project moved to the paid Odds API at 2
  credits a capture (see "App / deployment" above, which contradicted this bullet for
  two weeks). Captures now read the scraped DK / FanDuel / Fanatics board that
  `odds-collect-pickem-nfl.timer` already collects, so the cost is genuinely zero.
  `--source paid` is still there as a fallback and does spend.
- `data/pickem/` (tracker, standings) is gitignored — TOO-GOODE's real opponents,
  standings, and $ amounts never get committed. `data/pickem_current_week.csv` and
  `data/pickem_backtest_results.json` ARE committed (just spreads + aggregate stats,
  nothing personal).
- CBS's picks deadline is **per day** (before that day's first kickoff), not the
  generic "2 hours before" widely assumed — meaning inactive lists (T-90 min) are
  public before most deadlines. See the pool's own Settings page, not CBS's generic
  public-contest rules, which describe a different (free, mass-market) product.
