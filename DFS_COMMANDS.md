# DFS Commands Cheat Sheet

Quick reference for the commands you'll actually run day to day. All commands
assume you're in the repo root (`cd ~/Downloads/edge_search`).

## Build lineups

```
python3 scripts/dfs_lineups.py [--date DATE] [--draft-group DRAFT_GROUP]
                                [--from-cache] [--iters ITERS]
                                [--exclude-teams TEAMS] [--list-teams]
```

| Arg | Meaning |
|---|---|
| `--date DATE` | Slate date, `YYYY-MM-DD`. Default = today. |
| `--draft-group DRAFT_GROUP` | Slate name (`Main`, `Early`, `Turbo`, `Night`, `Afternoon`) or a numeric draft-group id. Default = main slate. Use `--list-teams` first if you're unsure which slate/id you want — DK sometimes posts duplicate same-named slates. |
| `--from-cache` | Reuse cached pitcher props instead of pulling fresh (0 Odds API credits). Good for a second look at the same slate, or when props haven't moved. |
| `--iters ITERS` | Optimizer iteration budget. Higher = better GPP stack search, slower. |
| `--exclude-teams TEAMS` | Comma-separated DK abbreviations to drop entirely, e.g. `BAL,CHC` — for a voided/postponed game DK isn't scoring. Not auto-detected (no free API exposes DK's own contest-scoring rules), so this is a manual override. |
| `--list-teams` | Print every team in the resolved slate (with a game-status warning if something looks postponed/suspended) and exit — no lineup built. |

**Common invocations:**

```bash
# Tonight's main slate, fresh props
python3 scripts/dfs_lineups.py --date $(date +%Y-%m-%d)

# Free re-run of the same slate (cached props, no credits spent)
python3 scripts/dfs_lineups.py --date $(date +%Y-%m-%d) --from-cache

# A named sub-slate (Turbo/Night/Afternoon)
python3 scripts/dfs_lineups.py --date $(date +%Y-%m-%d) --draft-group Night

# Sanity-check which slate/teams you'd actually get before spending credits
python3 scripts/dfs_lineups.py --date $(date +%Y-%m-%d) --draft-group Turbo --list-teams

# A game got postponed/voided after you already built — rebuild excluding it
python3 scripts/dfs_lineups.py --date $(date +%Y-%m-%d) --exclude-teams BAL --from-cache
```

## Get scores / grade a date

```
python3 scripts/dfs_grade.py DATE
```

Pulls final box scores (statsapi, free) for `DATE`, computes real DK fantasy
points per player, and reports projection accuracy plus how that date's
logged cash/GPP lineups actually scored. Run once games are final.

```bash
python3 scripts/dfs_grade.py 2026-07-17
```

## Late swap (after lineup lock, before first pitch of remaining games)

```
python3 scripts/dfs_swap.py [--date DATE] [--mode {cash,gpp,both}]
                             [--entry ENTRY] [--pin]
                             [--draft-group DRAFT_GROUP] [--top TOP]
```

| Arg | Meaning |
|---|---|
| `--date DATE` | Slate date. Default = today. |
| `--mode {cash,gpp,both}` | Which lineup to check for swaps. Default = both. |
| `--entry ENTRY` | Path to an entered-lineup CSV, overrides the pinned/default build. |
| `--pin` | Save this run's lineup as the pinned entry so the phone app also sees it. |
| `--draft-group DRAFT_GROUP` | Slate name or numeric id, same as `dfs_lineups.py`. |
| `--top TOP` | Number of replacement suggestions to show per OUT player. |

```bash
python3 scripts/dfs_swap.py --date $(date +%Y-%m-%d) --mode both --top 3
```

## Bonus: analysis / calibration tools

Not part of the nightly build loop, but useful after a batch of real contest
results comes in:

```bash
# Refresh actual-vs-predicted calibration (DK points + ownership)
python3 scripts/dfs_calibration.py

# Where would our built lineups have actually finished in the real field?
python3 scripts/dfs_roi_backtest.py

# Re-tune the ownership softmax against real held-out contest ownership
python3 scripts/dfs_ownership_gamma_sweep.py
```

## Bonus: when the deployed app's live props pull fails

If the phone app 404s on "Pull fresh pitcher props" but a local run above
succeeds (Streamlit Cloud network issue, not a code bug), ride your
already-fetched cache to the cloud instead of retrying the live pull:

```bash
git add -f data/cache
git commit -m "Cache snapshot: today's real pitcher props"
git push origin main
```

Then tap **"🔄 Refresh (free)"** on the phone app, not "Pull fresh pitcher
props" — cache mode will serve the snapshot without needing its own live
Odds API call.
