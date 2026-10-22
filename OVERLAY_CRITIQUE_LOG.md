# Overlay critique log

The dfs-critique loop applied to the overlay app's at-lock field forecast
(`edge/dfs_entry_edge.py` likely_at_lock + the straight-line floor, combined as in
`harness/overlay.py` assess). **Classic contests only** (Showdown/Captain alerts are
off). Target: the field at lock, forecast at the final call (latest snapshot
4.5-9.5 min before lock), versus DK's reported final (`data/contest_finals.csv`).
Harness: `python3 scripts/overlay_lab.py report --cutoff <date>`; the late-fill model
is refitted on train locks only, and the split is by lock date in ET.

Metrics: err/cap = (forecast - final) / cap (bias + = forecast too full), and the
overlay call "prize pool > fee x entries at lock": *missed* = said no, there was
one (lost EV); *false* = said yes, there wasn't (entries into a raked contest).

## Cycle 0 -- baseline (2026-10-22)

Model as shipped 10/15 (pooled curves, STRATIFY=False, tail from finals).

Split A: fit locks < 10/18, test 10/18-10/22 (includes NFL Sunday 10/18).

| slice | n | MAE | RMSE | bias | overlays | missed | false |
|---|---|---|---|---|---|---|---|
| ALL | 772 | 0.081 | 0.108 | +0.017 | 355 | 198 | 77 |
| NFL | 344 | 0.100 | 0.125 | -0.001 | 185 | 88 | 59 |
| NBA | 297 | 0.065 | 0.094 | +0.026 | 116 | 75 | 10 |
| NHL | 77 | 0.064 | 0.092 | +0.048 | 21 | 10 | 4 |
| NASCAR | 43 | 0.076 | 0.088 | +0.028 | 28 | 21 | 4 |
| CFB | 11 | -- not enough data -- | | | | | |
| fill <0.25 | 21 | 0.258 | 0.288 | -0.112 | 12 | 0 | 9 |
| fill 0.25-0.5 | 90 | 0.140 | 0.171 | +0.002 | 55 | 0 | 34 |
| fill >0.5 | 661 | 0.067 | 0.085 | +0.023 | 288 | 198 | 34 |

Split B: fit < 10/20, test 10/20-10/22 (NBA week): n=352, MAE 0.058, RMSE 0.086,
bias +0.007, overlays 134, missed 41, false 26.

Read: test-day count is small (5 lock days on A, 3 on B), so slice numbers are
weak; the tail share moved 0.45 -> 0.33 between the two fits (it is drifting). All
198 misses on A are contests already over half full at the call, so the forecast
over-fills nearly-full contests. The false calls sit in the emptier bands.
Caveats for the critic: finals exist only for contests collect_finals looked up
(cap >= 10, not full at the last snapshot), and the call snapshot depends on the
timer's cadence, which is every minute since 10/22 and was coarser before.
