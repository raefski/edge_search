#!/usr/bin/env python3
"""Grade open CLV bets against DK's CLOSING line. Run manually near tip-off.

Re-pulls ONLY the flagged events (not the whole slate) for the markets you
actually bet, so it's cheap. DRY-RUN by default; --confirm to spend.

    python3 scripts/clv_close.py            # estimate only (0 credits)
    python3 scripts/clv_close.py --confirm  # re-pull flagged events + grade
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.wnba_scout import load_env  # noqa: E402
from edge.client import OddsAPIClient  # noqa: E402
from edge.clv import load, grade, summary  # noqa: E402

LOG = ROOT / "data/clv_log.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true", help="actually spend credits")
    ap.add_argument("--regions", default="us")
    args = ap.parse_args()

    load_env()
    rows = [r for r in load(LOG) if r.get("status") == "open"]
    if not rows:
        print("No open bets to grade.")
        return

    # Re-pull each open event for only the markets we bet on it (plus their
    # alternate ladder, so a line that moved off the number still grades).
    ALT_OK = {"player_points", "player_rebounds", "player_assists", "player_threes",
              "player_points_rebounds_assists", "player_points_rebounds",
              "player_points_assists", "player_rebounds_assists"}
    by_event: dict[tuple, set] = defaultdict(set)
    for r in rows:
        key = (r["sport"], r["event_id"])
        by_event[key].add(r["market"])
        if r["market"] in ALT_OK:
            by_event[key].add(r["market"] + "_alternate")

    nreg = len(args.regions.split(","))
    est = sum(len(mks) for mks in by_event.values()) * nreg
    print(f"Open bets: {len(rows)} across {len(by_event)} events.")
    print(f"Estimated closing re-pull cost: {est} cr ({args.regions})")

    c = OddsAPIClient(cache_dir=ROOT / "data/cache", ledger_path=ROOT / "data/odds_api_credits.json",
                      dry_run=not args.confirm, live_ttl=0)  # ttl=0 -> always fresh closing prices
    if c.remaining_credits() is not None:
        print(f"Account remaining: {c.remaining_credits()}")
    if not args.confirm:
        print("\nDRY RUN — no credits spent. Re-run with --confirm near tip-off.")
        return

    events = []
    for (sport, eid), mks in by_event.items():
        events.append(c.get_event_odds(sport, eid, sorted(mks), args.regions))

    changed = grade(LOG, events)
    print(f"\nGraded {len(changed)} bet(s):")
    print(f"{'result':14} {'subject':22} {'side':6} {'taken':>7} {'close':>7} {'priceCLV':>9} {'probCLV':>8}")
    print("-" * 78)
    for r in changed:
        if r["status"] == "graded":
            res = "BEAT close" if str(r["beat_close"]) == "True" else "lost close"
            pcl = f'{float(r["price_clv_pct"])*100:+.1f}%'
            prb = f'{float(r["prob_clv"])*100:+.1f}%' if r["prob_clv"] != "" else "—"
            print(f'{res:14} {r["subject"][:22]:22} {r["side"]:6} {int(r["taken_american"]):+7d} '
                  f'{int(r["close_american"]):+7d} {pcl:>9} {prb:>8}')
        else:
            print(f'{r["status"]:14} {r["subject"][:22]:22} {r["side"]:6}  (DK moved off the number)')

    print(f"\nRunning CLV summary: {summary(LOG)}")
    print(f"Credits spent this session: {c.spent_this_session}  |  remaining: {c.remaining_credits()}")


if __name__ == "__main__":
    main()
