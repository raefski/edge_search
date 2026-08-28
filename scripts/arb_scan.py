#!/usr/bin/env python3
"""Scan DraftKings, FanDuel and Fanatics for arbitrage, middles and +EV.

Costs no Odds API credits: every book is read from its own public endpoint.

    python3 scripts/arb_scan.py                 # scan, print, write snapshot
    python3 scripts/arb_scan.py --no-write      # print only
    python3 scripts/arb_scan.py --sports baseball_mlb

The snapshot at data/arb_snapshot.json is what pages/5_⚖️_Arbitrage.py reads,
so the Streamlit app can show results even where the books' hosts refuse the
server (Streamlit Community Cloud is a datacenter; these endpoints are fronted
by Akamai and Cloudflare, the same wall pickem_live.py hit). Run this on a
machine in Connecticut and commit the snapshot.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.arb import ArbConfig                     # noqa: E402
from edge.arb.run import snapshot                  # noqa: E402

SNAPSHOT = ROOT / "data" / "arb_snapshot.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sports", nargs="+", help="sport keys to scan")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--min-profit", type=float, default=0.30, help="arb %% floor")
    ap.add_argument("--max-hours", type=float, default=96.0, help="how far ahead to look")
    ap.add_argument("--no-props", action="store_true", help="skip DraftKings prop tabs")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = ArbConfig()
    if args.sports:
        cfg.sports = args.sports
    cfg.bankroll.total = args.bankroll
    cfg.detect.min_profit_pct = args.min_profit
    cfg.detect.max_hours_to_start = args.max_hours
    cfg.draftkings_props = not args.no_props

    def progress(label, i, n):
        print(f"  [{i + 1}/{n}] {label}…", file=sys.stderr, flush=True)

    snap = snapshot(cfg, progress=progress)
    if args.json:
        print(json.dumps(snap, indent=2))
    else:
        s = snap["stats"]
        print(f"\n{s['quotes']} quotes across {s['events']} events "
              f"(fanduel {s['fanduel']}, draftkings {s['draftkings']}, "
              f"fanatics {s['fanatics']}, anchor {s['anchor']})  ·  0 credits")
        opps = snap["opportunities"]
        if not opps:
            print("no opportunities clearing the thresholds")
        for o in opps:
            legs = "  ".join(f"{l['book']} {l['label']} {l['american']} ${l['stake']:,.0f}"
                             for l in o["legs"])
            extra = ""
            if o["kind"] == "middle":
                hits = o.get("hit_values") or []
                extra = (f"  lands on {'/'.join(str(h) for h in hits)}"
                         f", breakeven {o['breakeven_hit_pct']:.1f}%")
            print(f"\n{o['kind'].upper():6} {o['profit_pct']:+.2f}%  {o['matchup']}"
                  f"  ({o['description']}){extra}")
            print(f"       {legs}")
    if not args.no_write:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(snap, indent=1))
        print(f"\nsnapshot → {SNAPSHOT.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
