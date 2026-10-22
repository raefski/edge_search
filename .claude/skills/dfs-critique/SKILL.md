---
name: dfs-critique
description: Run ONE cycle of the recursive self-improvement loop on a DFS sport's projection model -- a fresh, independent critic agent finds the single biggest hole, you fix it, re-measure held out, ship only if it improves, and log it. Use when Adam says "run a critique cycle", "/dfs-critique <sport>", or asks to improve a sport's model the critic way. Argument: the sport (nba, ncaab, nhl, nfl, ncaaf, mlb, nascar, mma) or a non-projection model (overlay).
---

# One critique cycle on a DFS model

Proven on NBA minutes 2026-10-21 (three cycles, NBA_MINUTES_CRITIQUE_LOG.md: RMSE
7.76 -> 6.59 held out). One invocation = ONE cycle. Argument = sport; if missing, ask.

## 1. The sport's target metric and where things are

Each sport is judged on the quantity that drives its DK points and that its model
actually predicts -- not one metric for all.

| sport | target metric (held out) | model / harness to read | log |
|---|---|---|---|
| nba | **minutes** per active player (RMSE, MAE, bias; slices) | NBA_MINUTES_METHOD.md, edge/nba_minutes.py, scripts/nba_minutes_lab.py (`report`, `live`, `cycles`) | NBA_MINUTES_CRITIQUE_LOG.md |
| ncaab | **minutes** where box scores exist, else DK points | NBA_STATUS.md (college), edge/dfs_run_ncaab.py, edge/ncaab_sim.py | NCAAB_CRITIQUE_LOG.md |
| nhl | **skater DK points vs. pre-lock projection** (goalies separately: points barely beat a constant) | NHL_STATUS.md, edge/nhl_sim.py, edge/dfs_run_nhl.py, scripts/nhl_calibration.py, nhl_props_value.py, nhl_context_features.py | NHL_CRITIQUE_LOG.md |
| nfl | **DK points by position (QB/RB/WR/TE/DST), players with a role** | DFS_STATUS.md, edge/dfs_run_nfl.py, scripts/model_accuracy.py, nfl_*_backtest.py | NFL_CRITIQUE_LOG.md |
| ncaaf | **DK points vs. projection** (known −2.3 bias) | NCAAF_STATUS.md, edge/dfs_run_ncaaf.py, scripts/ncaaf_calibration.py | NCAAF_CRITIQUE_LOG.md |
| mlb | **hitter DK points** (pitchers separately: strikeouts / outs) | DFS_STATUS.md, edge/dfs.py, scripts/dfs_hitter_backtest.py, dfs_calibration.py | MLB_CRITIQUE_LOG.md |
| nascar | **finishing position** (and place differential) | NASCAR_STATUS.md, edge/nascar_sim.py, scripts/nascar_calibration.py | NASCAR_CRITIQUE_LOG.md |
| mma | **fight outcome probability + DK points given method** | MMA_STATUS.md, edge/mma_sim.py, scripts/mma_calibration.py | MMA_CRITIQUE_LOG.md |
| overlay | **field at lock, Classic only**, forecast at the final call (4.5-9.5 min out): err/cap MAE/RMSE/bias + overlay calls missed / false + **$ of the calls (decides shipping)**; split by lock date (a day never straddles) | edge/dfs_entry_edge.py (fit_late_fill, likely_at_lock), harness/overlay.py (assess, collect_finals), scripts/dfs_overlay.py, data/contest_fill_log.csv, data/contest_finals.csv, scripts/overlay_lab.py (`report --cutoff`) | OVERLAY_CRITIQUE_LOG.md |

Read the sport's STATUS file and its log (if any) first. If the sport has no
reproducible held-out harness for its metric, building one (like nba_minutes_lab.py:
live-faithful, fit on an earlier period / slates, test on later ones, slices,
`report` command) IS this cycle's work -- say so and do it before any critic.

## 2. Record the baseline

Run the harness's report for the shipped model; if the log doesn't exist, create it
with a header and "Cycle 0" holding that table.

## 3. Spawn the critic (fresh agent, knows nothing)

Agent tool, `subagent_type: "dfs-model-critic"` (if not registered this session, use
`general-purpose` and paste `.claude/agents/dfs-model-critic.md`'s body as the prompt).
Give it: sport, target metric, the files above, the log, the data locations, the
held-out split, a scratch dir (`<scratchpad>/critic-<sport>-<n>`), and the deliverable
format. Foreground: the next step depends on it. Never tell it what you suspect.

## 4. Fix, honestly

- Implement the hole as a parameter in the harness; FIT ONLY ON THE TRAIN PERIOD;
  report the held-out table. If the critic chose anything by looking at held-out
  data, re-decide it on train and record any judgment call.
- Ship only if held out improves (the target metric, both information states where
  the sport has them). If not: record "not shipped" with the numbers.
- Shipping: production code + provenance comments, parity check production == harness,
  tests updated, `python3 -m harness verify`, the sport's DFS tests, AppTest the page,
  update the METHOD/STATUS doc so the NEXT critic sees the current model, regenerate
  mirrors if NHL/NCAAF/NASCAR/MMA (`python3 -m harness run mirror-<sport>`).

## 5. Log and report

Append "Cycle N" to the log: critic's hole + evidence (summary), the fix, the decision,
before/after table, runner-ups. Commit own paths, push under the repo lock, tell Adam
to Reboot if code changed. Reply with: the hole, the fix, the before/after metric table.
