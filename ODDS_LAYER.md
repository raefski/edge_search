# The market data layer

How prices get into this system, and how the three products read them.

`HANDOFF.md` is the operational picture. This file is the architecture of the
price supply, written when the Odds API was replaced by the arbitrage scrapers
in September 2026.

---

## 1. Why this exists

Three products needed the same fact — a price, on a side, at a book, at a time
— and each was getting it somewhere different:

| product | price source before | cost |
|---|---|---|
| MLB DFS | The Odds API, per-event prop calls | credits, and the expensive endpoint |
| Pick'em | The Odds API featured odds | ~2 credits/week |
| Arbitrage | scraped DraftKings / FanDuel / Fanatics | free |

The arbitrage path was the good one, and it was the only one not being reused.

**The fact that made this cheap to do:** `edge/arb/marketmap.py` already emits
The Odds API's own market keys — `pitcher_strikeouts`, `pitcher_outs`,
`player_pass_yds`, `player_points`. It was written to compare a scrape against
that feed, so the vocabulary was already shared. `edge/arb/catalog.py` keys
leagues the same way (`baseball_mlb`, `americanfootball_nfl`, `basketball_nba`).

So this migration is a change of **shape**, not of **meaning**. That is the
entire reason it is safe.

---

## 2. Architecture

```
   ┌──────────────────────────────────────────────────────────┐
   │ COLLECTORS — edge/arb/*          (unchanged)             │
   │   FanDuel · DraftKings · Fanatics via Oddschecker         │
   │   Fanatics Markets (vig-free anchor)                      │
   │   Owns: HTTP, league discovery, THE MARKET MAP            │
   └───────────────────────────┬──────────────────────────────┘
                               │  Board  (GroupKey -> side -> book -> Quote)
   ┌───────────────────────────▼──────────────────────────────┐
   │ MARKET DATA — edge/odds/                                  │
   │   profiles.py  which leagues, how deep, how fresh          │
   │   collect.py   run a profile, commit it                    │
   │   ingest.py    Board -> rows          (derives nothing)    │
   │   store.py     SQLite, append-only    (derives nothing)    │
   │   source.py    rows -> Odds-API-shaped payloads            │
   │   parity.py    measures free against paid                  │
   └───────┬───────────────────┬──────────────────────┬────────┘
           │                   │                      │
   ┌───────▼──────┐   ┌────────▼────────┐   ┌─────────▼────────┐
   │  ARBITRAGE   │   │       DFS       │   │     PICK'EM      │
   │ engine.scan  │   │ dfs_run         │   │ pickem.make_pick │
   │ (reads Board │   │ .build_slate    │   │ (reads consensus │
   │  directly)   │   │ (reads client)  │   │  from client)    │
   └──────────────┘   └─────────────────┘   └──────────────────┘
```

**The layer rule, and it is worth enforcing:**

| layer | may do | may not do |
|---|---|---|
| collectors | talk to books, map markets | persist, derive |
| store | persist facts | derive anything |
| source | reshape facts | fetch, persist |
| models | derive everything | any I/O |

Ingestion that computes is ingestion that can be wrong in a way you cannot see
later. Every devig, consensus and projection happens on read, from stored raw
decimals.

### Why arbitrage still reads the Board directly

Arbitrage needs quotes seconds old and it needs the `MarketGroup` structure
(`expected_sides`, `best`, `conflicts`) that the flat store deliberately does
not carry. Routing it through the store would add a serialise/deserialise round
trip to the one consumer that is latency-bound, for no gain. It writes to the
store for history; it reads from the Board.

---

## 3. Data flow

```
  scrape (desktop, ~10-65s per profile)
    └─> Board
         ├─> engine.scan() ──> opportunities ──> data/arb_snapshot.json ──> phone
         └─> board_to_rows() ──> OddsStore.write_quotes() ──> data/odds.db
                                                                   │
                       ┌───────────────────────────────────────────┤
                       │                                           │
          ScrapedOddsClient (per profile,                  store.history()
           freshness-contracted)                                   │
                       │                                           │
          get_events / get_event_odds /              line movement, opening
          get_featured_odds                          lines, CLV, backtests
                       │
        ┌──────────────┴──────────────┐
        │                             │
  dfs_run.build_slate       pickem_live._parse_events
   -> project_pitcher        -> weighted_consensus
   -> optimiser              -> pickem.make_pick
```

The phone → desktop loop in `HANDOFF.md` §2 is unchanged. The store is a second
output of the same scrape, not a second scrape.

---

## 4. API design

`ScrapedOddsClient` is **Liskov-substitutable for `OddsAPIClient`**. Same
methods, same payload shape, same exception-on-unavailable behaviour:

```python
from edge.odds import OddsStore, ScrapedOddsClient, client_for, collect

with OddsStore() as store:
    collect("dfs_mlb", store)                     # scrape + persist

client = client_for("dfs", "baseball_mlb")        # profile + freshness resolved
pool   = dfs_run.build_slate(client, date)        # consumer UNCHANGED
```

This was a deliberate choice over a nicer API. `project_pitcher` and
`_parse_events` are backtested code that indexes into
`bookmakers[].markets[].outcomes[]`. Changing the source *and* the shape at
once would mean any change in model output could be either, with no way to
attribute it. The nicer API can come once the free path has earned trust.

| method | returns |
|---|---|
| `get_events(sport)` | `[{id, sport_key, commence_time, home_team, away_team}]` |
| `get_event_odds(sport, event_id, markets, regions)` | one event dict with `bookmakers` |
| `get_featured_odds(sport, markets, regions)` | list of the same |
| `remaining_credits()` | `None` — not metered |
| `spent_this_session` | `0` |

Raises `StaleOdds` when no committed scan meets the caller's freshness
contract — deliberately parallel to the paid client's `DryRunBlocked` /
`CreditFloorError`, which every call site already degrades on gracefully.

### The one place it is not a pure translation

An alternate ladder carries many rungs for one (book, market, subject).
`dfs.player_markets` keeps **one** point per market and the last outcome it
sees wins it — so handing it a whole ladder mixes a price from one rung with
the line from another. That is exactly the shape of every price bug in this
system (`HANDOFF.md` §8: two different bets on one GroupKey).

`main_line_only` (default **on**) collapses each ladder to the rung priced
closest to even on both sides, since alternates are deliberately lopsided.
Tested in `tests/test_odds_store.py::test_ladder_collapses_to_its_main_rung`.

---

## 5. Collection profiles

One scan cannot serve three consumers. The arbitrage scan caps
`prop_events_per_league=6` because props are its most expensive part and it
only needs *some* events to pair. DFS needs **every** game on the slate — a
pitcher whose props were not fetched has no projection and silently leaves the
pool.

| profile | leagues | props | prop events | alt ladders | freshness | measured |
|---|---|---|---|---|---|---|
| `arb` | all catalogued (~30) | yes | 6 | **yes** | 600s | ~200s |
| `dfs_mlb` | MLB | yes | 20 | no | 600s | **63s, 17,878 quotes, 15 events** |
| `dfs_nfl` | NFL | yes | 20 | no | 1800s | **75s, 16,817 quotes, 32 events** |
| `dfs_nba` | NBA | yes | 20 | no | 900s | out of season |
| `pickem_nfl` | NFL | **no** | 0 | no | 86400s | **10s, 5,158 quotes, 32 events** |

Alternate ladders are off for every model profile: middles are the only thing
that consumes them, and they are roughly half the scan cost.

`required_markets` is a **commit guard**, not documentation. `collect(strict=True)`
refuses to commit a scan missing one — `dfs_mlb` requires `pitcher_outs` and
`pitcher_strikeouts` because `project_pitcher` returns `proj=None` without both,
so a scrape that lost them produces a board that looks like a thin slate rather
than a broken parser. That is the failure mode this repo keeps rediscovering.

---

## 6. Database schema

SQLite, WAL, at `data/odds.db`. One writer (the desktop agent), several readers
(app, CLI, backtests) — the shape SQLite is best at. Postgres would buy
concurrent writers, which nothing here has.

```
scan     id, profile, started_at, finished_at, quote_count, event_count,
         conflicts, ok, stats_json
event    event_id PK, sport_key, sport_title, commence_time,
         home_team, away_team, first_seen, last_seen
quote    PK (scan_id, group_key, side, book)
         event_id, sport_key, market, subject, point, side, book,
         decimal, captured_at
```

Three decisions worth knowing:

**`group_key` is stored, not recomputed.** It is `event_id|market|subject|point`.
SQLite treats NULLs inside a composite PRIMARY KEY as distinct from each other,
so a NULL subject — every game-level market — would defeat de-duplication
entirely and the same total would persist twice per scan.

**`captured_at` is the quote's own fetch time, not the scan's.** A wide scan
takes minutes; its first league genuinely is older than its last.

**Scans commit with `ok=0` and flip to `ok=1` only at the end.** Readers never
see a partial scan. A half-written board looks exactly like a real board with
thin coverage, which is the expensive kind of wrong here.

### The append-only decision

Scraping **cannot reach backwards**. The Odds API's `/historical` endpoints
could, at 10× cost, and `scripts/{nfl,nba,pickem}_historical_collect.py` still
use them. Dropping the paid feed means the only history that will ever exist is
history collected from today forward.

So every scan appends, even before anything reads it back. **A day not
collected is a day that cannot be bought later at any price.** Start the
collection cron before the consumers need it, not when.

Rough size: ~18k quotes per MLB scan, ~150 bytes/row ≈ 2.7 MB/scan. Four scans
a day across profiles ≈ **4 GB/year**. `prune(keep_days=400)` bounds it at a
season-plus.

### Where the data lives

`data/odds.db` is **gitignored**, for two independent reasons. This repo is
**public**, and the store is the accumulating corpus the models are built on —
the one asset here that cannot be re-derived after the fact. It is also binary
and unbounded, so every scan would write a full-file delta into git history.
Back it up outside git.

---

## 7. Caching strategy

Four layers, each answering a different question:

| layer | question | mechanism | lifetime |
|---|---|---|---|
| L1 in-process | "same scan, same render?" | `st.cache_data` keyed on `scan_id` | one session |
| L2 store | "did anyone already fetch this?" | `scan.ok=1` + `finished_at` | days–season |
| L3 freshness contract | "is this too old *for me*?" | `Profile.max_age_seconds` | per read |
| L4 collector | "is the book's own page cached?" | `edge/arb/http.py` | per request |

**L3 is the important one and it is per consumer, not global.** DFS before lock
refuses anything older than 10 minutes because props move while salaries are
frozen. Pick'em accepts a day, because the model reads a line *move* against a
pool line frozen all week. A backtest passes `None` and takes whatever exists.
Encoding that on the profile means the number lives in one place instead of
being restated at four call sites and updated at three.

A stale store raises `StaleOdds` rather than serving old prices. `app.py`'s
`make_client` catches it and falls back to the paid client, so a missed
collection degrades to *spend a credit*, not to an empty pool.

---

## 8. Does it compromise accuracy?

The requirement was to drop the Odds API **without compromising prediction
accuracy**. That is a claim about model output, so `edge/odds/parity.py` exists
to measure it rather than argue it.

**What is actually at risk, in order:**

1. **Coverage, not price.** A pitcher whose props were not scraped has no
   projection and vanishes from the pool. That dwarfs being two cents off on
   his strikeout price — and it is invisible in any average taken over the
   players that *did* match. `Divergence.coverage` is reported first, and a
   better MAE over a smaller matched set is a **worse** result.
2. **Book count.** Pick'em's consensus came from ~10 books and now comes from
   3. `edge/pickem_free.py` has always been honest that most of the gain from
   averaging arrives by the third or fourth book. Three is on the flat part of
   that curve — an argument, not a measurement.
3. **Price level.** Bias and dispersion are reported separately: pick'em reads
   a line *move*, so a constant offset cancels and noise does not.

### The head-to-head, run 2026-09-06 (32 credits)

`scripts/odds_parity.py`, paid Odds API against the free scrape, same consumer
code over both:

```
PICK'EM NFL -- current slate only
quantity           cover   paid  free    bias     MAE     p90   worst  unit
home_spread       100.0%     15    15  -0.049   0.204   0.450   1.150  points
total             100.0%     15    15  -0.005   0.168   0.325   0.375  points
n_books           100.0%     15    15  -6.000   6.000   6.000   6.000  books

MLB DFS -- paid Odds API vs free scrape
pitcher_projection 100.0%    27    27  -0.004   0.070   0.300   0.500  DK pts
pitcher_k_mean     100.0%    27    27  -0.015   0.030   0.100   0.200  strikeouts
```

**Read the coverage row first: 100% on both.** Every game on the live slate and
every one of the 27 projected pitchers appears in both sources. Nothing was
dropped, so the error statistics below are over the whole population rather
than a favourable subset.

**Pick'em: the free consensus is inside the model's own resolution.** MAE is
0.204 points against a model that treats 0.5 points as the difference between
a signal and a coin flip, and p90 is 0.45 — so on 90% of games the divergence
is smaller than the smallest move the model acts on. Bias is −0.049, i.e. no
systematic shading, which matters because the model reads a *move* and a
constant offset would cancel anyway. Twelve of fifteen games land within 0.33
points.

Two caveats, stated rather than buried:

* **Books went 9 → 3** (DraftKings, FanDuel, Fanatics against betmgm,
  betonlineag, betrivers, betus, bovada, draftkings, fanduel, lowvig,
  mybookieag). The 0.204 MAE *is* the cost of that, measured. It is small
  because the three kept are the three that carry weight in `BOOK_WEIGHTS`,
  and the six lost are mostly the 0.5-weighted recreational books.
* **One outlier at 1.15 points**, NYJ@TEN. The Fanatics leg is the likely
  cause — BUF@HOU also came back DK +1.5 / FD +1.5 / **Fanatics −0.5**, a
  two-point disagreement of exactly the kind `HANDOFF.md` §8 records for
  Fanatics ladders. Worth a guard: a book more than ~1.5 points off the other
  two on a main line is more likely stale than sharp.

**DFS: effectively identical.** 0.070 DK points of MAE on projections that run
16–24 points is 0.4% relative error, and the worst single pitcher is 0.5
points. That will not reorder the pool, so lineups are the same lineups.
Underneath it, `pitcher_k_mean` differs by 0.030 strikeouts — the prop prices
themselves agree.

Supporting evidence from the collection runs: 15 MLB events / 17,878 quotes
with **zero imputed components** (every pitcher had outs, strikeouts, earned
runs, hits, walks and win present), and **price_conflicts: 0** on both
profiles, which is the required value in a one-shot scan.

### Still to measure

* Re-run on an **NFL DFS** slate once the season opens (2026-09-10) and on
  **NBA** in season, where only two books are available until Oddschecker
  lists it.
* Re-run pick'em **later in a week**, when the line has moved away from the
  opener — this reading is from a slate that had barely moved.

---

## 8a. What extending to NFL found — four silent bugs

Turning on NFL exposed three defects that had been live for as long as
anything had looked, all of the same family: **a wrong id or pattern filters to
nothing, and "returned nothing" is indistinguishable from "the book posts
nothing."** Recorded here because the shape will recur when NBA is switched on.

**1. The DraftKings NFL prop category ids were wrong.** `PROP_CATEGORIES` had
`1342 "Passing"`, `1343 "Rushing"`, `1344 "Receiving"`. DraftKings actually
serves `1000 = Passing`, `1001 = Rushing`, `1342 = **Receiving**`. So 1343 and
1344 matched nothing and 1342 pulled receiving markets under the label
"Passing". NFL came back with receptions and receiving yards **and nothing
else** — no pass yards, no pass TDs, no rush yards. A quarterback cannot be
projected from that. MLB's ids (743, 1031) were verified live and correct,
which is exactly why MLB worked and hid the problem.
`scripts/dk_categories.py` now audits the map against a live payload.

**2. FanDuel's prop tabs were MLB's, for every sport.** `fanduel_tabs` was
`["popular", "pitcher-props", "batter-props"]` globally. **FanDuel answers an
unknown tab with a small generic payload rather than a 404**, so asking an NFL
event for `pitcher-props` returned 9 markets and no error. FanDuel contributed
**zero** NFL props. Tabs are now per sport (`ArbConfig.tabs_for`).

**3. FanDuel's own NFL player markets were unmapped, and one made things
worse.** `classify()` had `PITCHER_RE` for baseball and nothing for
`PLAYER_X_PASSING_YARDS_HIGH`; 22 of 34 live market types mapped to nothing.
Separately, `marketmap` spelled the stat `yards?` while FanDuel writes `Yds` —
and that did not merely drop those markets, it **inverted** them:
`split_player` decides which half of "Drake Maye - Passing Yds" is the person
by finding the half that names a statistic, and with neither half matching it
fell back to "a short Title Case fragment is a name" and chose *Passing Yds*
as the player.

### A fourth, found 2026-09-06 while fitting the touchdown rates

**"Longest Reception" was being filed as `player_receptions`.** The rule is
`(r"receptions?", "player_receptions")`, unanchored, and "Longest Reception"
contains the word. Puka Nacua's receptions line came back with a point of
**26.5** and Terrance Ferguson's **15.5** — nobody catches 26 passes; those are
distances in yards. "Longest Passing Completion" landed on
`player_pass_completions` the same way.

**Why this one is nastier than the three above: `price_conflicts` cannot see
it.** `group_key` is `event|market|subject|point`, so 5.5 receptions and a
26.5-yard longest reception differ in `point`, land in different groups, and
never collide. The counter that caught the category-id bug stays at 0. What
breaks instead is quieter — `dfs.player_markets` keeps one entry per market key
and the last outcome wins it (§4), so on **17 of 140** receptions markets the
longest-reception line silently *replaced* the real one. DK Classic is full
PPR at 1 point per reception, so Nacua projected **+21 DK points** and every
corrupted player went straight to the top of the value board, which is exactly
where an optimiser would take them all.

Fixed by claiming the longest markets first, with their own Odds-API-spelled
keys (`player_reception_longest`, `player_rush_longest`,
`player_pass_longest_completion`) rather than dropping them — the same decision
the combos got, and for the same reason: a separate key is what stops a market
colliding with the stat whose name it contains. Pinned in
`tests/test_marketmap_nfl.py`, including the property directly — a "Longest X"
market must never share a key with the count it names.

**The generalisable bit.** Three of these four bugs are now a bare stat word
matching inside a longer market name. The check that finds them is not a
counter, it is reading the book's own vocabulary back: enumerate every market
name a league serves and print what each maps to. That is a dozen lines and it
found this in one pass.

### And then the smoke alarm went off

Fixing (1) took `price_conflicts` from **0 to 93** in one scan — the number
`HANDOFF.md` §8 says must always be 0. Auditing the whole mapping rather than
the part that changed (as §8 prescribes) found three more mismappings at once:

| DraftKings market | was keyed | actual meaning |
|---|---|---|
| `X Rushing + Receiving Yards O/U` | `player_reception_yds` | a **combined** stat |
| `X Passing + Rushing Yards O/U` | `player_rush_yds` | a **combined** stat |
| `X Interceptions Thrown O/U` | **`totals`** | a QB prop, filed as the **game total** |

The combos are the same shape as the MLB "Player Hits, Runs, RBIs" bug already
in §8; the interception one is the same shape as the team-total-as-game-total
bug. A back's rush+rec over 39.5 is priced 1.13 where his receiving yards
alone over 39.5 is 3.03, so DraftKings appeared to quote one side of one group
at two prices three times apart — on every RB on the slate. Combined stats now
carry their own keys, and conflicts are back to **0**.

### What it bought

| | before | after |
|---|---|---|
| pass yards | 4 players | **33** |
| pass TDs | 0 | **33** |
| rush yards | 11 | **69** |
| receptions | 118 (DK only) | **127** (DK + FanDuel) |
| prop quotes | 6,486 | **10,171** |
| books posting props | DK, Fanatics | **DK, FanDuel, Fanatics** |

Five prop markets now carry two or more books, so **cross-book NFL prop
arbitrage is visible for the first time** — this was a scan-coverage fix, not
only a DFS one.

---

## 9. DFS across sports

`edge/dfs_sport.py` holds one table per sport and `edge/dfs_project.py` holds
the arithmetic, which is identical everywhere: devig a two-sided prop, read the
implied mean off a normal with that stat's sigma, multiply by DK's points per
unit, sum. Adding NBA is filling in a table, not writing a projector.

`DFS_MULTISPORT_PLAN.md` called "duplicate first, abstract later", and that was
right — the differences had to be visible first. They turned out to be five
pieces of data: markets, points per unit, sigma, imputation, roster.

**The generalisation is proved, not asserted.** `project_pitcher` is
backtested code with money behind it, so it is **not replaced**;
`tests/test_dfs_project.py` runs both over the same payloads and requires the
projections to agree. That is what makes the abstraction safe to build on.

Two things the generic engine does that the MLB one did not need:

* **Threshold bonuses are expectations, not step functions.** DK pays +3 for
  100 rushing yards. Scoring that off the projected mean gives a back at 99.4
  nothing and one at 100.1 the full 3 — jumpy exactly where the pool is
  densest. The fitted normal already gives P(yards ≥ 100), so the bonus is its
  expected value.
* **DST comes from the game markets, not from props.** A team defence has no
  prop market at all, which `DFS_MULTISPORT_PLAN.md` flagged as the open
  problem. What the market does price is the opponent's implied team total
  (`total/2 − spread/2`), which is precisely the input DK's points-allowed
  tiers take. Tiers are interpolated rather than snapped, for the same reason.

### Calibration status — read before trusting a number

| sport | status |
|---|---|
| MLB | backtested; sigmas unchanged from `edge/dfs.py` |
| NFL | TD rates and sigmas **fitted 2026-09-06** — see below |
| NBA | **nothing verified** — see below |

### The NFL fit, 2026-09-06

Rushing and receiving touchdowns have **no two-sided market** — DraftKings
prices them only as "Anytime TD", a field with no opposing side — so they are
imputed from yardage. That was two round-number priors; it is now fitted
against 11,017 nflverse player-weeks (2023+2024 REG) by
`scripts/nfl_td_fit.py`. Both priors were too low:

| | prior | measured |
|---|---|---|
| rushing TD | 1 per **180** yds | 1 per **128** |
| receiving TD | 1 per **200** yds | 1 per **165** |

**One rate per route is also the wrong shape.** Bootstrapped 95% CIs over the
pool-like population (expected yards ≥ 20) separate three groups and refuse a
fourth:

```
rush  QB     95 (80-115)  vs  RB/FB 135 (126-147)   separated, p~0.001
rec   WR    161 (151-172) vs  TE    167 (149-191)   NOT separated, p~0.60
rec   WR+TE 162 (153-172) vs  RB/FB 220 (180-281)   separated, p~0.007
```

So `_nfl_impute` now takes a position and carries three rates. Wide receivers
and tight ends are deliberately **not** split — splitting on an unmeasured
difference costs the same as missing a real one and is harder to notice.

Two methodological points that changed the answer:

* **The regressor is an expected mean, not a realized game.** A 5-yard
  touchdown catch *is* 5 receiving yards, so fitting on realized yardage lets
  the touchdown inflate its own predictor — the 0–10 yard bin reads 1 TD per 66
  yards. The fit uses a leave-one-out season mean instead.
* **The proportional shape survived testing.** Yards per TD by expected-yardage
  floor is flat (rushing 131/132/132/131/137, receiving 170/178/177/171/167 at
  floors 0/10/20/30/40), so there is no red-zone non-linearity to model and no
  defensible intercept — the fitted RB rushing one is *negative*.

**What it bought, stated honestly: not accuracy.** Out of sample (fit 2023,
test 2024) the touchdown term's MSE improves with CIs clear of zero, but at the
whole-projection level MAE gets slightly *worse* (4.669 → 4.704) and rank
correlation is unchanged (0.6917 → 0.6920). A touchdown is a 0/1/2 count whose
median is 0, so MAE is minimised by a rate biased toward zero. What the fit
actually removes is **bias**: −0.41 → −0.11 DK points overall, and per position
from −0.82 on rushing quarterbacks to near zero. That matters where offence is
compared against a DST projected on a different scale, and in the cross-position
trade the optimiser makes under a cap.

### The sigmas are nearly inert, which is the more useful finding

`scripts/nfl_sigma_fit.py`. A sigma only does work when the price is away from
even money — `implied_mean = line + sigma*z` — and across 13,690 real two-sided
NFL prop quotes in `data/odds.db` the mean |z| is **0.014–0.128**. Books post
yardage props at −110/−110 and move the *line*. So the passing-yards sigma
being 19% low (65 against a measured 75) moved the implied mean by 0.68 yards
at the 90th percentile of real prices: **0.03 DK points**.

The exception is **receptions** — mean |z| 0.128 against 0.014–0.025 for the
yardage markets, because a reception line is a half-integer on a low count, so
the book must move the price where it could move a line. That sigma was also
the furthest out (1.8 against a measured 2.05).

Where sigma does real work is a threshold bonus. Even at the fitted sigma the
normal **under**-predicts P(100+ yards) by 1.3–1.7pp, because yardage is
right-skewed and a normal's right tail is too thin — about 0.05 DK points, left
in and named.

Every player with an imputed component is still flagged `*` on the board.

### Before NBA ships

NBA is out of season, so both of the things that were wrong for NFL are
currently *unverified* for NBA, with exactly the provenance that made them
wrong: the DraftKings category ids (1215/1216/1217) and the FanDuel tab names.
Run `scripts/dk_categories.py --sport basketball_nba` and probe a real FanDuel
matchup **first**. Double-double and triple-double bonuses are not modelled.

---

## 10. What is not built

* ~~**A cross-sport optimiser.**~~ **Built 2026-09-06** as
  `edge/dfs_opt_nfl.py`, a separate module rather than a mode of
  `edge/dfs_opt.py` — see §11.
* **The WNBA paid call sites.** `scripts/{clv_log,clv_close,wnba_scout,
  pilot_threes_2025}.py` still build `OddsAPIClient` directly. They are WNBA
  and NBA-pilot, not MLB, and WNBA currently maps NO DraftKings prop category
  at all (`scripts/dk_categories.py` reports it) -- so that has to be fixed
  before the swap would give them anything.
* **CLV off the store.** `store.history()` and `opening_and_latest()` already
  hold what the CLV harness pays 10x for, and every scan adds to it. Nothing
  reads them yet.

## 11. The NFL optimiser

`edge/dfs_opt_nfl.py`, built 2026-09-06. `scripts/dfs_lineups_nfl.py` drives it.

**It is a separate module, not a mode of `edge/dfs_opt.py`, and that was the
call to make.** Almost nothing in the MLB optimiser is a parameter of NFL:
`_consecutive_runs` walks a *batting order*, `MAX_HITTERS_PER_TEAM` is a DK MLB
entry rule, `_hitter_slots_assignable` splits the roster into pitchers and
everyone else, `_secondary_stack` builds a second batting-order run. Those are
not MLB-flavoured settings of a general stacker — they are a different theory
of what correlates. Parameterising would put a `sport` branch inside every one.

What *is* shared is the slot matcher, and that is now `edge/dfs_roster.py`
rather than a fourth copy. `edge/dfs_opt.py`'s own docstring records that this
recursion had already been reimplemented three times inside that one module;
NFL needing it again is where extraction earns itself.

### The correlation model is measured

`scripts/nfl_correlation.py`, nflverse 2023+2024, 544 games, 291 players
averaging 5+ DK points. Pearson r between DK totals in the same game:

```
QB  <-> own WR/TE      +0.249     the stack
QB  <-> own RB         +0.065     a back is NOT a stack partner
WR/TE <-> own WR/TE    -0.029     teammates COMPETE for targets
QB  <-> OPPOSING WR/TE +0.089     the bring-back
DST <-> OPPOSING QB    -0.351     the strongest number here
```

Two of those contradict the folk wisdom, which is why it was worth measuring:

* **A second pass-catcher is worth less than the first.** Teammates are
  slightly *anti*-correlated — one football — so QB+2 buys two exposures to the
  quarterback, not a compounding one. `stack_n` defaults to **2**, and the
  ceiling function charges the −0.029 rather than letting a whole receiving
  corps pile onto the cheapest passing game.
* **A defence facing your own offence is a hard constraint, not a preference.**
  At −0.351 against the opposing quarterback it is a *stronger* relationship
  than the +0.249 stack it would cancel. Same rule, same reason, as the MLB
  pitcher-versus-hitter check.

### Two things it refuses to do quietly

* **An impossible stack returns `None`**, not a silently unstacked lineup.
* **`n` lineups means a portfolio, not one lineup `n` times.** Varying the seed
  converges to the same optimum — asking for three and getting one three times
  looks like it worked. `portfolio()` enforces a max overlap and a distinct
  stack quarterback per lineup, and returns *fewer* rather than padding.

### Known gaps

* The ceiling is a correlation-weighted sum, **not a simulation**. It ranks
  lineups in the direction the measurements point; it is not a distribution.
* **DST big plays are still a flat prior** (`DST_BIG_PLAY_POINTS = 5.2`),
  unfitted — the one NFL number that is still a guess. The same team-week data
  that produced the correlations above can fit it.
* Ownership and leverage are not modelled at all, so "gpp" mode means
  *correlated*, not *contrarian*.
* `DK_ALIAS` in `scripts/dfs_lineups_nfl.py` exists because DraftKings writes
  the Rams **LAR** where nflverse writes **LA**. Unaliased, every Rams player
  found no game line and was dropped — which silently removed the highest
  projected receiver on the slate. The script now reports missing teams **by
  name**, because a whole team vanishing and a thin slate look identical.

---

## 12. Scheduling

`deploy/odds-collect@.service` is a systemd **template** — the instance name is
the profile — with one timer per profile, because each profile has its own
freshness contract and one hourly job would over-scrape pick'em and
under-scrape DFS.

```bash
cp deploy/odds-collect@.service deploy/odds-collect-*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now odds-collect-dfs-mlb.timer odds-collect-pickem-nfl.timer
systemctl --user list-timers 'odds-collect*'
journalctl --user -u 'odds-collect@*' -f
```

| timer | cadence | why |
|---|---|---|
| `odds-collect-dfs-mlb` | hourly, then every 15 min 15:00–23:00 | salaries freeze, props keep moving; the value of a scan rises toward lock |
| `odds-collect-pickem-nfl` | 12:20 and 23:20 daily | the pool line is frozen all week; matches `PICKEM_WEEKLY.md`'s twice-weekly capture, run daily because history cannot be backfilled |

`Restart=no` on purpose: a failed collection is not an emergency. The previous
scan stays readable, consumers fall back to the paid client, and the timer
tries again. `--no-strict` is deliberately **not** set, so a scan missing a
required market is refused rather than committed.

The same linger requirement as `arb-agent` applies —
`sudo loginctl enable-linger $USER` — or the timers stop when your last shell
exits.
* **Historical backfill.** Cannot be done. See §6.
