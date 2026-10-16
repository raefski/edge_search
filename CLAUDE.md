# edge_search — instructions for Claude

Adam's sports-betting + DraftKings DFS workbench. One repo feeds phone apps on
Streamlit Cloud (app.py + pages/), CLIs (scripts/), and ~18 systemd user
timers (deploy/) that collect, build, log and publish around the clock.
Adam is on his phone before lock; his time is the scarce resource.

## Start here

```bash
python3 -m harness status          # slates locking, timer health, data freshness, failures, alerts
python3 -m harness list --jobs     # runnable jobs; `harness list --role evaluate --sport nhl` for files
python3 -m harness verify          # compile + the tests that cover what you changed
```

Durable lessons live in Claude's auto-memory (MEMORY.md index, loaded each
session) and the write-ups: DFS_STATUS.md, NHL_STATUS.md, NCAAF_STATUS.md,
NASCAR_STATUS.md, MMA_STATUS.md, NBA_STATUS.md (NBA + college), DFS_METHODOLOGY.md, GPP_META.md (what wins
GPPs + model accuracy), PICKEM_STATUS.md, ODDS_LAYER.md, DRAFTKINGS_ACCESS.md.
Read the sport's STATUS file before changing its model. Live state (locks,
failures) is never written by hand: `harness status` reads it.

## Map

| role | where |
|---|---|
| apps | `app.py` (MLB), `pages/2_NFL 3_NCAAF 4_Pickem 5_Arbitrage 6_NASCAR 7_MMA 8_NHL 9_Parlay_Builder 10_Multi_Sport_Parlays 11_Overlay 12_NBA 13_NCAAB` |
| ingest | `edge/odds/` (scraped prices → `data/odds.db`), `edge/arb/` (book scrapers, DK HTTP/2 shim), `edge/dfs.py` (DK draftables/lobby), `edge/nhl.py` (NHL API + DailyFaceoff), `edge/buzz.py` + `packages/transcripts` (YouTube/podcasts) |
| model | per sport: `edge/dfs_<sport>_theory.py` (objectives, ownership), `edge/<sport>_sim.py` / `edge/dfs_sim.py` (simulators), `edge/dfs_run_<sport>.py` (`build_slate`, the one pipeline app+CLI share) |
| optimize | `edge/dfs_opt*.py` (`dfs_opt_climb.py` = the shared percentile search NBA/NCAAB run on), `edge/dfs_leverage*.py` (chalk↔contrarian frontier + contest EV, every sport), `edge/dfs_nfl_contest.py`, `harness/late_swap.py`, `edge/dfs_swap.py` (MLB) |
| evaluate | `scripts/*_calibration.py`, `scripts/contest_db.py` → `data/contests.db`, `scripts/gpp_meta.py`, `scripts/model_accuracy.py`, `scripts/buzz_trust.py`, backtests |
| publish | `scripts/odds_collect.py --push`, `draftables_publish.py`, `nhl_lines_publish.py`, `mirror_app.py` (generated standalone repos — never edit a mirror) |
| monitor | `harness/monitor.py` (goalie changes, line moves → ntfy), `scripts/dfs_overlay.py`, `harness/status.py` |
| harness | `harness/` (catalog, runner + `data/harness/runs.jsonl`, verify, status, monitor, late swap, navigator), `.claude/hooks/` |

Sports are separate models with separate calibration loops (MLB, NFL, NCAAF,
NHL, NASCAR, MMA, NBA, NCAAB — college basketball unverified live until ~11/3: NBA_STATUS.md). Learnings carry over; parameters do not.

## Standards

- **Out-of-sample or it didn't happen.** Fit on some slates, report on others;
  the slate is the unit (entries in one contest share one realisation).
  Report n slates / contests / entries with every number and say "not enough
  data" below ~5 slates. A backtest that was not held out is a hypothesis.
- **Cash and GPP are never pooled** for ownership (small cash fields put
  80-95% on the top play; GPPs don't).
- **Match projection logs to a contest by draft group**, never "the nearest
  slate" — graders have silently matched the wrong night before.
- **Names:** `edge.names.norm` everywhere (Jr./III were dropped by a second normaliser once).
- **A fallback must be visible.** Snapshot/cached data shown as live hid a
  day-long outage; record the source (`LAST_DRAFTABLES_SOURCE`-style) and show it.
- **Constants carry provenance**: what they were fitted on, n, and the date,
  in the comment beside them (see `edge/dfs_nfl_contest.py`).
- **Automate, don't offload.** If a scrape is blocked, capture what the real
  browser requests (`python3 -m harness navigate <url>`) and replay it; never
  hand Adam per-item manual steps.
- Match the surrounding code: long explanatory comments about *why*, stdlib
  first (numpy/pandas/scipy/statsmodels/playwright are available).

## DraftKings — read DRAFTKINGS_ACCESS.md before any new DK call

`api.draftkings.com` needs HTTP/2 + a real browser UA (use `edge.dfs._get` /
`edge.arb.http`) and is blocked from Streamlit Cloud; `www.draftkings.com/lobby`
works from Cloud. Broad sweeps earn a residential IP block for hours: one
lobby listing per sport per hour, `arb_scan.py` only with `--sports`, ≥1 s
between ad-hoc lookups.

## Verify every change (the Stop hook enforces it)

| changed | run |
|---|---|
| anything | `python3 -m harness verify` (compiles + covering tests) |
| a DFS page / app.py | also drive it: `streamlit.testing.v1.AppTest.from_file(<abs path>)`; for layout, Playwright at 390px width (iPhone); pages run as `__main__`, so every cached function name must be unique across pages |
| an optimizer / model | its sport's tests + a build on a live or logged slate; compare against the previous output before claiming better |
| DFS suite | `python3 -m pytest -q tests/test_dfs*.py tests/test_page_cache_isolation.py tests/test_streamlit_api.py` (~2 min) |
| everything | `python3 -m pytest -q` |

## Working in this repo

- The timers commit and push to `main` all day, serialised by a lock
  (`edge/git_lock.py`). Commit only your own paths (`git add <paths>`; never
  `git add -A`), then push under the same lock:
  `flock .git/edge_git.lock -c 'git pull -q --rebase --autostash && git push -q'`.
  If git ever says a rebase is in progress, do not abort or reset: run
  `git rebase --quit` (it saves the autostash to the stash list), then
  `git stash apply` and check the tree — that stash holds uncommitted work. The working tree always holds
  timer-written data — don't discard it (`.claude/hooks/guard_bash.py` blocks
  `reset --hard`, `clean -f`, `checkout .`, force push, deleting `data/*.db`).
- Streamlit Cloud redeploys from `main` but unreliably picks up code: tell
  Adam to **Reboot** the app after a code push. Data is read from GitHub
  (`edge/repo_files.py`), so data-only pushes need no reboot. After changing
  NHL/NCAAF/NASCAR/MMA code, regenerate the mirrors:
  `python3 -m harness run mirror-<sport>`.
- `data/odds.db` (scrape history) and `data/contests.db` cannot be rebuilt
  from scratch for past dates — never delete or rewrite them.
- Contest exports: Adam downloads them to Windows Downloads;
  `python3 -m harness run import-contests` files and tags them, then
  `python3 -m harness run contest-db` and the sport's calibration.
- Commit messages: what changed and the evidence (numbers, n, slates), like
  the existing history; end with the Co-Authored-By trailer.
