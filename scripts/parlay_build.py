#!/usr/bin/env python3
"""Build the best boosted parlay off the current arb snapshot -- the CLI twin of
pages/9_🎰_Parlay_Builder.py.

    python3 scripts/parlay_build.py --preset cfb
    python3 scripts/parlay_build.py --preset mlb --cap 50
    python3 scripts/parlay_build.py --promo-file promo.txt --legs 4
    python3 scripts/parlay_build.py --preset tennis --scan     # scrape first

--scan runs the scrapers for the token's sport and overwrites
data/arb_snapshot.json, exactly like the Arbitrage page's "Scan live".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from edge.arb import ArbConfig, oddsmath as om, parlay as PL  # noqa: E402

SNAPSHOT = ROOT / "data" / "arb_snapshot.json"
ET = ZoneInfo("America/New_York")


def kickoff(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(ET).strftime("%a %-I:%M%p")
    except (TypeError, ValueError):
        return ""


def pick_preset(word: str) -> PL.ParlayPromo:
    hits = [k for k in PL.PRESETS if word.lower() in k.lower()]
    if len(hits) != 1:
        raise SystemExit(f"--preset {word!r} matches {hits or 'nothing'}; "
                         f"choose from: {list(PL.PRESETS)}")
    return PL.PRESETS[hits[0]]


def line(p: PL.Parlay) -> str:
    return (f"{p.n:2d} legs  boost {p.boost:4.0%}  EV {p.ev:+7.1%} (${p.ev_dollars:+.2f})  "
            f"1 in {p.one_in:>8,.0f}  {om.format_american(p.decimal):>8} -> "
            f"{om.format_american(p.boosted_decimal):>8}  to win ${p.to_win:,.0f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--preset", help="substring of a preset name: cfb, mlb, tennis, fanduel, no boost")
    src.add_argument("--promo-file", type=Path, help="the offer's text, pasted into a file")
    ap.add_argument("--book", choices=["draftkings", "fanduel"])
    ap.add_argument("--sport", help="override the token's sport key")
    ap.add_argument("--legs", type=int, help="fix the leg count (default: best)")
    ap.add_argument("--cap", type=float, default=100, help="longest shot, as '1 in N' (0 = any)")
    ap.add_argument("--stake", type=float)
    ap.add_argument("--lead", type=float, default=10,
                    help="skip games starting within this many minutes (default 10)")
    ap.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    ap.add_argument("--scan", action="store_true", help="scrape the sport first")
    a = ap.parse_args()

    if a.preset:
        promo = pick_preset(a.preset)
    else:
        promo, missing = PL.parse_promo_text(a.promo_file.read_text(), book=a.book)
        if missing:
            print(f"! could not read from the promo: {', '.join(missing)}")
    if a.book:
        promo.book = a.book
    if a.sport:
        promo.sports = [a.sport]

    if a.scan:
        from edge.arb.run import snapshot as build_snapshot
        cfg = ArbConfig()
        cfg.sports = list(promo.sports)
        print(f"scanning {cfg.sports or 'everything'} …")
        snap = build_snapshot(cfg)
        a.snapshot.write_text(json.dumps(snap, indent=1))
    snap = json.loads(a.snapshot.read_text())
    print(f"board {snap.get('generated_at')}  ·  {promo.describe()}  ·  "
          f"{PL.BOOK_NAMES[promo.book]}  ·  {promo.sports or 'any sport'}")

    legs = PL.legs_from_candidates(snap.get("candidates", []), promo,
                                   min_minutes_to_start=a.lead)
    res = PL.build(legs, promo, n_legs=a.legs, stake=a.stake, max_one_in=a.cap or None)
    print(f"{len(legs):,} legs from {res.events_considered} games")
    if res.note:
        print("!", res.note)
    if res.pick is None:
        return 1
    print("\nTHE TICKET   " + line(res.pick))
    for l in res.pick.legs:
        flag = "  ⚠ off-market" if l.suspect else ("  (book only)" if l.book_only else "")
        print(f"   {l.describe():38.38} {l.american:>7}  fair {l.fair_prob:5.1%}  edge {l.edge_pct:+5.1f}%"
              f"   {kickoff(l.commence_time):>11}  {l.matchup[:34]}{flag}")
    print("\nBEST EV AT EACH HIT RATE")
    for p in res.frontier:
        print("   " + line(p))
    if len(res.by_legs) > 1:
        print("\nBY LEG COUNT")
        for n, p in sorted(res.by_legs.items()):
            print("   " + line(p))
    if res.best_growth:
        print("\nKELLY ($1,000)  " + line(res.best_growth))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
