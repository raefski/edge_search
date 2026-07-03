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

## Layout
| Path | Role |
|---|---|
| `edge/oddsmath.py` | odds conversion + de-vig (multiplicative / power / shin) |
| `edge/client.py` | Odds API client: cache, credit ledger, dry-run guard |
| `edge/fairodds.py` | consensus fair prob, excluding the target book |
| `edge/scanner.py` | normalise payloads, de-vig, flag +EV vs consensus |
| `scripts/wnba_scout.py` | runnable WNBA scout (dry-run default) |
| `tests/` | de-vig + EV unit tests |

## Extension points (not built yet)
- **CLV harness**: re-pull flagged events near tip-off (or historical snapshot
  nearest commence) and grade closing-line value — the real success metric.
- Other soft sports (niche soccer, lacrosse, cricket) via `get_featured_odds`.
- Storage to DuckDB/parquet for backtests; Pinnacle-weighted fair odds.
