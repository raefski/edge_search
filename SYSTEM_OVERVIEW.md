# edge_search: system overview (for an outside reviewer)

Written 2026-11-14. This is the one-page "how it all fits together". The details live
in the files it points to. Live state (what is locking, timer health, failures) is never
written down: run `python3 -m harness status`. Results: `python3 scripts/dfs_scorecard.py show`.

## 1. What it is for

One person (Adam) uses it to make money in three ways, from a phone, before lock:

| activity | what the system does | where |
|---|---|---|
| **DraftKings DFS** (main focus) | projects players, forecasts field ownership, builds cash + GPP lineups, late swap, picks contests | Streamlit pages per sport: `app.py` (MLB), `pages/2_NFL`, `3_NCAAF`, `6_NASCAR`, `7_MMA`, `8_NHL`, `12_NBA`, `13_NCAAB`, `14_NBA_CourtIQ`; `15_DFS_ROI` dashboard |
| **Sports betting** | arbitrage / boosts across books, parlay pricing | `5_Arbitrage`, `9_Parlay_Builder`, `10_Multi_Sport_Parlays` |
| **Pick'em pool + overlay hunting** | weekly pool picks; DK contests likely to end in overlay | `4_Pickem`, `11_Overlay` |

Eight DFS sports, each a **separate model with its own calibration loop**: MLB (off-season), NFL,
NCAAF, NBA, NCAAB, NHL, NASCAR, MMA. Lessons carry across sports, but the parameters do not.

## 2. Data flow

```
 scrapers (book prices, DK lobby/draftables, NHL lines, injury reports, YouTube/podcast buzz)
   │   ~40 systemd user timers in deploy/ on the desktop (WSL)
   ▼
 local stores: data/odds.db (14-day hot) ─weekly→ F:\edge_search\odds\archive
               data/contests.db (graded DK contest exports), per-sport history/ground truth
   │   edge/data_branch.py publishes every feed to the git `data` branch (plumbing, no checkout)
   ▼
 Streamlit Cloud apps read code from `main` and data from GitHub's `data` branch
   (Cloud is blocked from api.draftkings.com, so the desktop does the DK calls)
   ▼
 Adam builds/enters on DK → exports contest results → data/contest_inbox/ →
   `harness run import-contests` → contests.db + grades vs that night's projection log
```

Monitoring: `edge-monitor.timer` (goalie and line changes, scratches) and `edge-overlay.timer`
send ntfy pushes to the phone.

## 3. The DFS model, one sport (they all follow this shape)

1. **Slate**: `edge/dfs.py` + `edge/dfs_slate.py`: DK draftables, games, lock times.
2. **Projection**: `edge/dfs_run_<sport>.py::build_slate` (the app and CLI share this one pipeline).
   It blends book props / game lines (`edge/odds`) with sport-specific models (NBA minutes × pts/min,
   NHL simulation, NCAAF ladder, and so on) and availability (injury/scratch feeds).
3. **Ownership**: `edge/dfs_<sport>_theory.py`. Cash and GPP are fitted separately.
4. **Optimize**: `edge/dfs_opt*.py` (shared percentile climb), `edge/dfs_leverage*.py` (chalk↔contrarian
   frontier + contest EV), late swap in `harness/late_swap.py`.
5. **Log**: every build is written to the forward-test log (`edge/dfs_forward_log.py`).
6. **Grade**: contest exports are matched to the log **by DK draft group**, then
   `scripts/*_calibration.py`, `model_accuracy.py`, `gpp_meta.py` run on them.

Per-sport write-ups: `<SPORT>_STATUS.md`, `DFS_METHODOLOGY.md`, `GPP_META.md`, `DFS_STATUS.md`.

## 4. The self-improvement loop (nightly)

`edge-critique.timer` (02:00) → `python3 -m harness critique-loop` (`harness/critique_loop.py`):

1. Picks **one in-season sport per night** from a rotation. It skips any sport with a slate
   locking soon, and any sport paused after several cycles in a row with nothing passing.
2. **Scout** (`dfs-target-scout` agent + `scripts/dfs_target_scout.py`, `scripts/dfs_transfer.py`)
   picks the one target worth attacking. Adam can pin a target with `--queue`.
3. **Critic** (`dfs-model-critic` / `dfs-availability-critic`, read-only) finds the single biggest
   hole and proves it with data. The `/dfs-critique` skill implements the fix in a git worktree branch.
4. **Gate** (`harness/critique_gate.py`) re-measures main vs the candidate with a fixed lab command,
   held out, using a night-clustered bootstrap. It must be better beyond noise on every primary
   split and not worse on the guard split.
5. A passing candidate **waits ~7 days and is re-gated** on new nights before it merges. Adam
   gets pushed and can `--drop` it. The critic prompts and the gate are "judge files": the loop
   never edits them itself.
6. Every cycle writes `<SPORT>_CRITIQUE_LOG.md`, shipped or not. The not-shipped entries are the
   loop's memory, so it doesn't propose the same idea twice.

Cycles run so far: NBA minutes 41, NHL 18, NCAAF 10, NASCAR 8, NCAAB 5, overlay 5,
NBA ownership 4, MMA 3, NFL 3, pick'em 2.

## 5. Results so far (scorecard 2026-11-14, DK history through 11/12)

ROI since 2026-06-28. The ranges are 90%. "e" = entries, "s" = slates.

| sport | cash | GPP | projection R² / MAE |
|---|---|---|---|
| all | −3% [−23,+16] 135e/67s | +38% [−15,+92] 211e/78s | |
| NFL | +61% 17e/11s | +251% 30e/11s | 0.32 / 5.3 |
| NHL | +21% 35e/25s | +1% 67e/29s | 0.15 / 5.0 |
| MLB | −27% 30e/28s | −37% 37e/35s | 0.09 / 5.7 |
| NBA | −56% 20e/15s | −59% 48e/21s | 0.55 / 8.0 |
| NCAAF | −63% 12e/9s | +132% [−65,+330] 13e/11s | 0.28 / 6.7 |
| NCAAB | −60% 9e/7s | −65% 9e/7s | 0.29 / 8.0 |
| NASCAR | −67% 6e/5s | −54% 4e/4s | 0.03 / 20.4 (4 slates) |
| MMA | thin | thin | thin |

Contest selection shows up as strongly as the projections do. For example, GPPs with 26-100
entries returned +327%, GPPs that ended in overlay +168%, and GPPs with 501-5,000 entries −33%.
Detail: `data/dfs_scorecard.json` and the DFS ROI page.

## 6. Engineering harness

- `python3 -m harness status | list | run <job> | verify | monitor | late-swap | navigate`
- `CLAUDE.md` is the working rules for Claude sessions. `.claude/hooks/` blocks destructive git
  commands and makes Claude run `harness verify` before it stops.
- Branches: code lives on `main`, timer data on `data`. `edge-code-sync.timer` fast-forwards the desktop.
- Standards: out-of-sample or it didn't happen; the slate is the unit; cash and GPP are never
  pooled; names go through `edge.names.norm`; fallbacks must be visible; constants carry their provenance.

## 7. Known stale or uncertain areas

- `README.md` still describes the original WNBA props scout.
- Most sports have under ~30 graded slates, so most ROI intervals cross zero.
- The many plan/status docs overlap (`DFS_IMPROVEMENT_PLAN.md`, `DFS_MULTISPORT_PLAN.md`, `DFS_STATUS.md`).
