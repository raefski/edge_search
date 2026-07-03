#!/usr/bin/env python3
"""Grade scraped DK same-game-parlay quotes for correlation edge.

Reads quotes (JSONL, written by dk_sgp_scaffold.py), devigs DK's own singles
for each leg, looks up the measured true correlation from data/phi_table.csv,
and prints the edge. Flags a bet only when DK is actually UNDER-correlating.

    python3 scripts/sgp_grade.py --demo               # run a worked example
    python3 scripts/sgp_grade.py data/dk_sgp_quotes.jsonl
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.sgp import load_phi_table, grade_quote  # noqa: E402

PHI = ROOT / "data/phi_table.csv"

DEMO = {
    "game_id": "DEMO Tigers@Guardians",
    "dk_decimal": 3.5,  # <- a HYPOTHETICAL DK SGP price; replace with a real scrape
    "legs": [
        {"type": "starter_outs_over", "line": 17.5, "desc": "Skubal Over 17.5 outs",
         "taken_dec": 1.95, "opp_dec": 1.87},
        {"type": "opp_total_under", "line": 4.5, "desc": "Guardians Under 4.5 runs",
         "taken_dec": 1.91, "opp_dec": 1.91},
    ],
}


def show(q, r):
    legs = " + ".join(l["desc"] for l in q["legs"])
    print(f"\n{q.get('game_id','')}: {legs}")
    if r.get("verdict") != "graded":
        print(f"  -> {r['verdict']}: {r.get('reason','')}")
        return
    print(f"  DK SGP price        : {r['dk_decimal']:.2f}")
    print(f"  fair price (phi={r['phi_true']:+.2f}): {r['fair_decimal']:.2f}")
    print(f"  DK implied corr     : {r['dk_implied_phi']:+.3f}   (true {r['phi_true']:+.3f})")
    gap = "UNDER-correlated" if r['dk_implied_phi'] < r['phi_true'] else "over/at fair"
    print(f"  -> DK is {gap};  EV = {r['ev']*100:+.1f}%   {'*** BET ***' if r['bet'] else '(no bet)'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("quotes", nargs="?", help="JSONL of scraped quotes")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--ev-threshold", type=float, default=0.03)
    args = ap.parse_args()

    if not PHI.exists():
        sys.exit(f"missing {PHI} — build it first (the binary-phi table).")
    table = load_phi_table(PHI)

    if args.demo:
        print("DEMO — hypothetical DK price, not a real scrape. Shows the pipeline end to end.")
        show(DEMO, grade_quote(DEMO, table, args.ev_threshold))
        return
    if not args.quotes:
        sys.exit("provide a quotes JSONL file, or use --demo")
    for line in Path(args.quotes).read_text().splitlines():
        line = line.strip()
        if line:
            q = json.loads(line)
            show(q, grade_quote(q, table, args.ev_threshold))


if __name__ == "__main__":
    main()
