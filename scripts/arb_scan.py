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
from edge.arb.config import Detect                 # noqa: E402
from edge.arb.run import snapshot                  # noqa: E402

SNAPSHOT = ROOT / "data" / "arb_snapshot.json"


def _parse_boost(spec: str):
    """book:pct[:max_stake[:sport]][:parlay] -> engine.Boost."""
    from edge.arb.engine import Boost
    parts = [p.strip() for p in spec.split(":")]
    if len(parts) < 2:
        raise SystemExit(f"--boost {spec!r}: need at least book:pct")
    parlay = parts[-1].lower() == "parlay"
    if parlay:
        parts = parts[:-1]
    book, pct = parts[0], float(parts[1])
    if pct > 1.0:                       # 25 and 0.25 both mean 25%
        pct /= 100.0
    max_stake = float(parts[2]) if len(parts) > 2 and parts[2] else 10.0
    sports = [parts[3]] if len(parts) > 3 and parts[3] else []
    return Boost(book=book, pct=pct, max_stake=max_stake, sports=sports,
                 requires_parlay=parlay)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sports", nargs="+", help="sport keys to scan")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--min-profit", type=float, default=0.30, help="arb %% floor")
    ap.add_argument("--max-hours", type=float, default=Detect().max_hours_to_start,
                    help="how far ahead to look (default covers a full weekly "
                         "football cycle -- see edge/arb/config.py)")
    ap.add_argument("--no-props", action="store_true", help="skip DraftKings prop tabs")
    ap.add_argument("--boost", action="append", default=[], metavar="SPEC",
                    help="a profit boost, book:pct[:max_stake[:sport]] -- e.g. "
                         "fanduel:25:25:basketball_wnba. Repeatable. Boosts are "
                         "per-account and cannot be discovered, so they are "
                         "entered by hand. Append ':parlay' to mark a "
                         "parlay-only token (excluded: a hedge needs singles).")
    ap.add_argument("--top", type=int, default=0,
                    help="show only the best N per sport")
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
    cfg.boosts = [_parse_boost(spec) for spec in args.boost]
    for b in cfg.boosts:
        print(f"  boost: {b.describe()}"
              + ("  [parlay only -- not usable for a hedge]" if b.requires_parlay else ""),
              file=sys.stderr)

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
