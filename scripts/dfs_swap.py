#!/usr/bin/env python3
"""Late-swap helper: replace a PROJECTED hitter who got ruled out of the order.

Workflow: you build near lock (scripts/dfs_lineups.py) using confirmed lineups
where posted and PROJECTED orders otherwise. As official lineups drop, re-run
this. It re-pulls confirmed lineups + the fresh hitter pool for FREE (pitcher
props come from cache = $0), compares your entered lineup against reality, and:

  * upgrades PROJECTED players who confirmed into their spot  -> no action
  * flags PROJECTED players whose team posted a lineup WITHOUT them (OUT)
  * for each OUT player, suggests the best same-position replacement that fits
    the salary freed up, is a confirmed starter, and whose game hasn't locked.

DK locks each player at THEIR game's first pitch, so the tool prints lock status:
if the OUT player's game already started you're stuck (0 pts); otherwise swap.

REDUNDANCY with the phone app: both read/write the same pinned-entry file,
data/dfs_entries_<date>_<mode>.csv (edge/dfs_swap.entry_path). If you pinned
your entry on the app, this script picks it up automatically with no --entry
needed. If you're building here on your computer instead, pass --pin to save
this run's lineup there too, so the app's Late-swap tab sees the same entry.

Usage:
  python3 scripts/dfs_swap.py --date 2026-07-05 [--mode cash|gpp|both]
                              [--entry data/dfs_lineups_2026-07-05.csv] [--pin]
                              [--draft-group Main] [--top 4]
"""
import argparse
import csv
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.wnba_scout import load_env  # noqa: E402
from edge.client import OddsAPIClient  # noqa: E402
from edge import dfs_run, dfs_swap  # noqa: E402


def _rows_for_mode(args, date, mode):
    """Resolution order: explicit --entry file (if given) > pinned entry
    (shared with the phone app) > the model's own dfs_lineups_<date>.csv build."""
    if args.entry:
        rows = [r for r in csv.DictReader(open(args.entry)) if r.get("mode", mode) == mode]
        return rows, f"--entry {args.entry}"
    pinned = dfs_swap.load_pinned_entry(ROOT, date, mode)
    if pinned:
        return pinned, "pinned entry (shared with app)"
    lf = ROOT / f"data/dfs_lineups_{date}.csv"
    if lf.exists():
        rows = [r for r in csv.DictReader(open(lf)) if r["mode"] == mode]
        return rows, str(lf)
    return [], None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--mode", default="both", choices=["cash", "gpp", "both"])
    ap.add_argument("--entry", default=None, help="entered-lineup CSV (overrides the pinned entry / default build)")
    ap.add_argument("--pin", action="store_true",
                    help="save this run's lineup as the pinned entry, so the phone app sees it too")
    ap.add_argument("--draft-group", default=None)
    ap.add_argument("--top", type=int, default=4, help="replacement suggestions per OUT player")
    args = ap.parse_args()
    load_env()

    # FREE refresh: cache-mode client => pitcher props from disk, lineups/pool live.
    c = OddsAPIClient(cache_dir=ROOT / "data/cache", ledger_path=ROOT / "data/odds_api_credits.json",
                      dry_run=True, live_ttl=10**9)
    res = dfs_run.build_slate(c, args.date, draft_group=args.draft_group, iters=1)
    if res.get("error") or res.get("unpriced"):
        sys.exit(f"could not load fresh pool: {res.get('error') or 'slate not priced'}")

    hh = res["hitters"]
    started = dfs_swap.game_started_map(args.date)
    confirmed_teams = {h["team"] for h in hh if h.get("confirmed", True)}
    print(f"fresh pool: {len(hh)} hitters | {len(confirmed_teams)} teams confirmed | spent {c.spent_this_session} cr\n")

    modes = ["cash", "gpp"] if args.mode == "both" else [args.mode]
    any_action = False
    any_rows = False
    for mode in modes:
        rows, source = _rows_for_mode(args, args.date, mode)
        if not rows:
            continue
        any_rows = True
        if args.pin:
            dfs_swap.save_pinned_entry(ROOT, args.date, mode, rows)
            source += "  [pinned -> shared with app]"
        total_sal = sum(int(r["salary"]) for r in rows)
        print(f"=== {mode.upper()} === source: {source}")
        print(f"entered salary ${total_sal:,}  cap room ${dfs_swap.CAP - total_sal:,}")
        for rec in dfs_swap.suggest_swaps(rows, hh, started, mode=mode, top=args.top):
            if rec["status"] == "confirmed":
                if rec["was_projected"]:
                    print(f"  ✓ {rec['player']:22} {rec['team']:3} -> CONFIRMED, locked into order")
            elif rec["status"] == "hold":
                print(f"  … {rec['player']:22} {rec['team']:3} projected — {rec['team']} lineup NOT posted yet, hold")
            else:  # out
                any_action = True
                note = "  ⚠ THEIR GAME LOCKED — can't swap, stuck at 0" if rec["locked"] else ""
                print(f"  ✗ {rec['player']:22} {rec['team']:3} ${rec['salary']:>5} OUT of {rec['team']} order{note}")
                if rec["locked"]:
                    continue
                if not rec["suggestions"]:
                    print(f"       (no eligible replacement <= ${rec['max_salary']:,})")
                for s in rec["suggestions"]:
                    tag = " [same-team stack]" if s["same_team"] else ""
                    print(f"       -> {s['name']:22} {s['team']:3} ${s['salary']:>5} "
                          f"{mode} {s['val']:>5} own {s['own']:>4.1f}%{tag}")
        print()

    if not any_rows:
        sys.exit(f"no entered lineup found for {args.date} — pin one from the app, pass --entry, "
                 f"or build one first with scripts/dfs_lineups.py")
    if not any_action:
        print("No swaps needed — every projected player either confirmed into the order or hasn't posted yet.")


if __name__ == "__main__":
    main()
