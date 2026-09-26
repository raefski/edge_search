# edge_search

Cross-sport +EV / price-disagreement scanner. Finds soft lines (non-superstars,
small-market teams/sports) by pricing a book against a vig-free **consensus of
the other books**, then validating with Closing Line Value before risking money.

First target: **WNBA player props**, US books, live scan.

## Setup
```bash
cp .env.example .env          # add your The Odds API v4 key (ODDS_API_KEY)
pip install -r requirements.txt   # only pytest is required; core has no deps
pytest -q                     # devig + EV math (10 tests)
```

## Run the WNBA scout
```bash
python3 scripts/wnba_scout.py            # DRY RUN — prints credit estimate, spends 0
python3 scripts/wnba_scout.py --confirm  # pulls + scans (spends credits)
```
Options: `--regions`, `--props`, `--ev-threshold`, `--method {multiplicative,power,shin}`,
`--min-books`, `--top`.

## Free prices for every model (`edge/odds`)

The arbitrage scrapers are the price source for DFS and pick'em too, replacing
The Odds API. `edge/arb/marketmap.py` already emits that API's own market keys,
so this is a change of shape rather than of meaning. **Read `ODDS_LAYER.md`
before touching any of it.**

```bash
python3 scripts/odds_collect.py --profile dfs_mlb     # scrape + persist
python3 scripts/odds_collect.py --status              # what the store holds
```

```python
from edge.odds import client_for
pool = dfs_run.build_slate(client_for("dfs", "baseball_mlb"), date)  # 0 credits
```

## Credit discipline
- `/sports` & `/events` are **free**. Live odds cost `markets × regions`;
  historical is **10×** that. Estimates print before any spend.
- Dry-run is the default; only `--confirm` spends.
- Every response is cached (`data/cache/`), so re-scans cost **0 credits**.
- Remaining credits are logged from response headers each call and a floor
  (`EDGE_CREDITS_FLOOR`, default 5000) aborts paid calls before they breach it.

## Portable DK MLB DFS lineup app (on your phone before lock)

A Streamlit front-end over the same `edge.dfs_run.build_slate` pipeline the CLI
uses, so lineups match `scripts/dfs_lineups.py` exactly.

```bash
streamlit run app.py            # local
```

**Refresh model = your bash `--from-cache` flag, in the browser:**
- **🔄 Refresh (free)** — re-pulls DK salaries + confirmed batting lineups
  (free public APIs, **0 credits**). These are what change before lock, so tap
  this as orders post. Pitcher props are served from `data/cache/`.
- **💰 Pull fresh pitcher props** — the one *paid* live pull of sportsbook props
  for the projections, then cached to disk so every later refresh is free.

The app defaults to cache mode and never spends a credit without that explicit tap.

**Projected lineups + late-swap.** Lineups build even before official orders post:
teams whose lineup isn't out yet get a PROJECTED batting order from their most
recent game (flagged with `*`), so you can stack a team before its lineup drops.
On the **🔁 Late-swap** tab, tap *📌 Save this as my DK entry*, then keep tapping
🔄 Refresh (free) as orders post — anyone whose team posts a lineup **without**
them is flagged with fitting same-position replacements (salary that frees up,
confirmed starter, game not yet locked). Same logic on the CLI:
`python3 scripts/dfs_swap.py --date <ISO>` (reads `data/dfs_lineups_<date>.csv`).

### Deploy (same as strikeouts → Streamlit Community Cloud)
1. Push this repo to GitHub:
   ```bash
   git remote add origin https://github.com/raefski/edge_search.git
   git branch -M main && git push -u origin main
   ```
2. On share.streamlit.io → **New app** → pick the repo, **Main file** = `app.py`.
3. App **Settings → Secrets**, paste (see `.streamlit/secrets.toml.example`):
   ```toml
   ODDS_API_KEY = "your_key"
   ```
4. Open the URL on your phone. Free model data (park factors, skill rates) is
   committed so cold starts are fast; the paid odds cache is **not** committed.
   For a fully $0 phone session, snapshot props before you leave:
   `git add -f data/cache && git commit -m "cache snapshot" && git push`.

## DK NFL DFS (`pages/2_🏈_NFL_DFS.py`) — cash + GPP, same app

The NFL half of the lineup app, on DK Classic (QB/RB/RB/WR/WR/WR/TE/FLEX/DST,
$50k). Same sidebar, same phone, **0 credits** — props come from the free
scraped store (`dfs_nfl` profile) on the desktop and from the committed
snapshot on Streamlit Cloud; DK salaries come from DraftKings' own draftables
endpoint.

```bash
python3 scripts/dfs_lineups_nfl.py                 # cash + gpp, DK's MAIN slate
python3 scripts/dfs_lineups_nfl.py --list-slates
python3 scripts/dfs_lineups_nfl.py --mode gpp -n 3 # a diversified portfolio
```

**The two modes are two game theories, not one with a stack flag.** A lineup is
a sum of nine correlated random variables, so give each player a mean and a
standard deviation and both objectives fall out of the same two numbers:

| mode | objective | what it does on its own |
|---|---|---|
| cash | `mean − 0.75·sd` | spreads across games, **refuses** a stack, pays up at QB |
| gpp | `mean + 1.25·sd` | concentrates into a QB stack with a bring-back |

Correlation raises a lineup's spread, so the cash objective walks away from a
stack and the GPP objective walks into one — neither is told to. The per-player
spreads are measured (`scripts/nfl_variance_fit.py`, 9,979 leak-free
player-weeks) and the correlation matrix is measured
(`scripts/nfl_correlation.py`, 544 games). Everything is in
`edge/dfs_nfl_theory.py` with its provenance; the head-to-head that tests the
claim is `scripts/nfl_lineup_backtest.py`.

**Ownership is a PRIOR, not a fit** — there are no NFL contest exports on this
machine, unlike MLB's gammas. The page says so. Treat leverage as a tilt.

**Deploying it for the phone.** Streamlit Cloud cannot scrape, so it reads
`data/odds_snapshot_dfs_nfl.json`, and `scraped_client` refuses a snapshot over
6 hours old. `deploy/odds-publish-dfs-nfl.timer` pushes a fresh one every 30
minutes on Sunday 08:00–13:00 ET (and Thursday evenings). By hand:

```bash
python3 scripts/odds_collect.py --profile dfs_nfl --push
```

## DK College Football DFS (`pages/3_🏈_NCAAF_DFS.py`) — cash + GPP

DK CFB Classic: **QB/RB/RB/WR/WR/WR/FLEX/S-FLEX**, $50k. No defence and no
tight-end slot, and the **S-FLEX takes a quarterback** — which is the whole
game, because college QBs average 17.2 DK points against 8.9 for receivers.

```bash
python3 scripts/odds_collect.py --profile dfs_ncaaf --push   # Friday night
python3 scripts/dfs_lineups_ncaaf.py                         # cash + gpp
python3 scripts/dfs_lineups_ncaaf.py --board --top 40
```

**DraftKings posts college props ONLY as one-sided milestone ladders** ("15+",
"25+", "40+"), so `edge/dfs_project.project` cannot read them at all — it needs
an Over/Under pair and returns `proj=None` for every player. `edge/dfs_ladder.py`
projects from the whole ladder instead: `E[X] = ∫P(X ≥ t)dt`, with the rungs
supplying most of the terms and only the unpriced head and tail modelled. That
inverts the usual expectation — college's uglier market data yields a
*better-founded* projection than the NFL's, because a ladder IS the distribution
where a two-sided line is one point on it.

**Four findings contradict the NFL build** rather than reproducing it: the
S-FLEX is a second QB, never two QBs from one team (−0.098), team-mate
receivers are *positively* correlated in college (+0.048 vs the NFL's −0.029)
so the stack is 3 rather than 2, and QB is the *most* volatile position here
rather than the least. **`NCAAF_STATUS.md`** has all of it, plus §5 on what is
not validated.

## DK NASCAR DFS (`pages/6_🏁_NASCAR_DFS.py`) — cash + GPP

Six drivers, $50k, no positions. **No sportsbook is involved**: place
differential, laps led and fastest laps have no betting market at any book, so
this build reads NASCAR's own free timing feeds and costs nothing, works from
any IP, and has no freshness contract.

```bash
python3 scripts/dfs_lineups_nascar.py --board
python3 scripts/dfs_lineups_nascar.py            # AFTER qualifying — no late swap
```

**It is the only sport here with a simulator instead of a correlation matrix**,
because its scoring components are constrained sums over the whole field:
finishing position is a permutation, place differential sums to zero, laps led
sums to the race distance. Six drivers cannot all dominate. The objectives are
percentiles of the simulated lineup total (25th for cash, 90th for GPP) rather
than mean ± k·sd, because a NASCAR score is not symmetric — a wreck is in the
left tail and a dominator in the right.

**A superspeedway is nearly a lottery** (finish model R² 0.029 against 0.287 at
a short track), fastest laps are *uncorrelated* with leading there (r=0.01), and
the front of the grid is the most dangerous place to be (28.2% DNF vs 23.3% from
the back). **`NASCAR_STATUS.md`** has the full table.

## DK MMA DFS (`pages/7_🥊_MMA_DFS.py`) — cash + GPP

Six fighters, $50k, no positions, **no late swap — lock is the first fight's
bell**. The win bonus (90/70/45 by round, 30 for a decision) is most of a DK MMA
score, and DraftKings Sportsbook prices exactly what it pays on: winner ×
method × round. So the build reads those prices, **de-biases them** (the method
market has underpriced decisions by 3–4 points in every era since 2013 — the
expensive direction, since a finish pays up to three times a decision),
simulates whole fights with UFCStats style indices, and scores every legal
lineup against a **simulated field** on the same simulated cards: cash =
P(beat the double-up line), GPP = P(top 1%).

```bash
python3 scripts/dfs_lineups_mma.py --capture     # fresh DK prices, then cash + gpp
python3 scripts/dfs_lineups_mma.py --board
python3 scripts/mma_calibration.py --grade --refresh   # the day after
```

Scoring reproduces DK's own FPPF to the decimal for 15 of 18 fighters.
Backtested end to end on 3,454 held-out fighter-fights (2022–26): MAE **30.0**
against **36.5** for the career average DK prints in its lobby, with the
simulated distribution calibrated at every percentile checked. Ownership is a
prior. **`MMA_STATUS.md`** has everything.

## NFL pick'em (TOO-GOODE pool) — sidebar page, not DFS

`pages/4_🎯_Pickem.py`, added to the app's sidebar automatically by Streamlit's
multi-page mechanism (no shared code with the MLB app or the NFL/NBA *DFS* build in
`DFS_MULTISPORT_PLAN.md` — pick'em is a spread contest, not a salary-cap lineup game,
despite both saying "NFL"). Full status, backtest numbers, and open questions:
**`PICKEM_STATUS.md`** — start there for anything pick'em-related, and
**`PICKEM_MODEL.md`** for how the model actually works plus every feature we've
tested and killed (read it before proposing a new one), and **`PICKEM_WEEKLY.md`** for the
actual weekly checklist -- what to run, in order, to keep it fed.

One-line summary: pool operators freeze a spread and never update it; the market keeps
moving all week. `edge/pickem.py::make_pick` scores that gap, backtested 55.7% ATS
out-of-sample on 543 held-out 2023–24 games (`scripts/pickem_backtest.py`). Live market
line comes from The Odds API (~1 credit for a whole week's slate, cache-first); CBS's
frozen line is login-gated and has to be captured by hand into
`data/pickem_current_week.csv`.

## Layout
| Path | Role |
|---|---|
| `edge/odds/` | **market data layer** — free scraped prices for every model (`ODDS_LAYER.md`) |
| `edge/arb/` | the scrapers: DraftKings, FanDuel, Fanatics + arbitrage engine |
| `edge/oddsmath.py` | odds conversion + de-vig (multiplicative / power / shin) |
| `edge/client.py` | Odds API client: cache, credit ledger, dry-run guard (**being retired**) |
| `edge/fairodds.py` | consensus fair prob, excluding the target book |
| `edge/scanner.py` | normalise payloads, de-vig, flag +EV vs consensus |
| `edge/dfs_ladder.py` | **milestone-ladder -> projection** (NCAAF; see NCAAF_STATUS.md) |
| `edge/nascar_sim.py` | **race simulator** (NASCAR; see NASCAR_STATUS.md) |
| `edge/mma_sim.py` | **fight simulator** (MMA; see MMA_STATUS.md) |
| `scripts/mirror_app.py` | generates the standalone `ncaaf_fantasy` / `nascar_fantasy` / `mma_fantasy` repos |
| `edge/pickem.py` | pick'em spread-edge model (see PICKEM_STATUS.md) |
| `edge/pickem_features.py` | as-of-week efficiency + coach features (tested, NOT shipped — see PICKEM_MODEL.md) |
| `edge/pickem_live.py` | live NFL spreads via The Odds API (~1 credit/week) |
| `scripts/wnba_scout.py` | runnable WNBA scout (dry-run default) |
| `scripts/pickem_backtest.py` | leak-free pick'em backtest, 2014–2024 |
| `scripts/pickem_feature_lab.py` | feature experiments (holdout-safe, dev split only) |
| `tests/` | de-vig + EV + pick'em unit tests |

## Extension points (not built yet)
- **CLV harness**: re-pull flagged events near tip-off (or historical snapshot
  nearest commence) and grade closing-line value — the real success metric.
- Other soft sports (niche soccer, lacrosse, cricket) via `get_featured_odds`.
- Storage to DuckDB/parquet for backtests; Pinnacle-weighted fair odds.
