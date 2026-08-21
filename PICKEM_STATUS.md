# Pick'em Project Status — Orientation Doc

**Purpose of this file**: a 60-second re-orientation, not a full record — the same job
`DFS_STATUS.md` does for the DFS side. Point a new conversation here first for anything
pick'em-related. Not a DFS project (no salary cap, no lineups) despite sharing "NFL" and
this repo with the real NFL DFS build in `DFS_MULTISPORT_PLAN.md` — different game, kept
deliberately separate (`edge/pickem.py`, not `edge/nfl.py`).

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

- **Test: 297-236-10 = 55.7% ATS.** Signal games (83% of slate) 56.7%, fallback/unmoved
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

## Immediate next step, not yet done

Run a real week live and grade it: capture CBS's actual Week 1 lines (in progress —
`data/pickem_current_week.csv` has provisional numbers from an early screenshot, needs
re-verification Tuesday Sep 8 after 1pm ET per CBS's posting rule), let the model pick,
log results to `data/pickem/tracker.csv` (gitignored, real pool data), and see whether
live performance tracks the 55.7% backtest or comes in under it per caveat #2 above.

## App / deployment

`pages/4_🎯_Pickem.py` — Streamlit multi-page (added to the sidebar automatically
alongside the MLB DFS home page in `app.py`, no shared code, per
`DFS_MULTISPORT_PLAN.md §2`'s "duplicate first, abstract later" call). Live market line:
`edge/pickem_live.py`, ESPN's public scoreboard API, free, no key, refreshed on tap —
verified working directly (see commit). CBS's frozen line: cannot be fetched
server-side (login-gated) — comes from `data/pickem_current_week.csv`, committed each
time a screenshot is captured and parsed. **The page's rendering itself has NOT been
visually verified** — the session that built it had no `streamlit` or `playwright`
installed and no way to add them (no `pip`), so only the underlying data/model logic
was dry-run tested (matches the backtest's numbers exactly) and the file was
`py_compile`d clean. Run it for real (`streamlit run app.py`, or
`.claude/skills/run-dfs-app/`, pointed at this repo's actual path) before trusting the
UI renders as intended.

## Quick facts easy to forget

- Uses ZERO Odds-API credits — pick'em's live data is ESPN's free scoreboard, not the
  Odds API. Doesn't compete with the DFS side's 500-credit/month budget at all.
- `data/pickem/` (tracker, standings) is gitignored — TOO-GOODE's real opponents,
  standings, and $ amounts never get committed. `data/pickem_current_week.csv` and
  `data/pickem_backtest_results.json` ARE committed (just spreads + aggregate stats,
  nothing personal).
- CBS's picks deadline is **per day** (before that day's first kickoff), not the
  generic "2 hours before" widely assumed — meaning inactive lists (T-90 min) are
  public before most deadlines. See the pool's own Settings page, not CBS's generic
  public-contest rules, which describe a different (free, mass-market) product.
