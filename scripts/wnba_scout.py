#!/usr/bin/env python3
"""WNBA live +EV scout.

Pulls current WNBA game lines + player props (US books), de-vigs each book,
and flags prices that beat the consensus of the other books — the soft,
non-superstar lines we're hunting.

DRY-RUN BY DEFAULT: prints the exact credit estimate and spends nothing.
Add --confirm to actually pull.

    python3 scripts/wnba_scout.py                 # estimate only (0 credits)
    python3 scripts/wnba_scout.py --confirm        # pull + scan (spends credits)
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.client import OddsAPIClient  # noqa: E402
from edge.scanner import scan  # noqa: E402

SPORT = "basketball_wnba"
FEATURED = ["h2h", "spreads", "totals"]
# DK's actual WNBA two-way prop coverage (probed 2026-06-27). Blocks/steals/
# turnovers are NOT offered by DK for WNBA. The +x_y combos carry only ~2 books
# so they seldom clear a sharp >=2-book consensus, but cost ~0 when thin/empty
# (the API bills per populated market, so listing them is free when absent).
DEFAULT_PROPS = [
    "player_points", "player_rebounds", "player_assists", "player_threes",
    "player_points_rebounds_assists", "player_points_rebounds",
    "player_points_assists", "player_rebounds_assists", "player_double_double",
]
# We bet DraftKings, so DK is the target. Anchor "fair" to sharper books only —
# leaving soft recreational books (bovada/betonlineag/mybookieag) in the
# consensus pollutes it and hides or fabricates DK edges (see tests).
SHARP_BOOKS = ["pinnacle", "fanduel", "betmgm", "williamhill_us", "betrivers", "fanatics"]


def load_env():
    """Make ODDS_API_KEY available without committing a key. Prefers the
    process env, then this project's .env, then the strikeouts .env as a
    fallback so the scout runs against the existing account immediately."""
    if os.environ.get("ODDS_API_KEY"):
        return
    for p in (ROOT / ".env", Path("/home/asr/Downloads/strikeouts/.env")):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line.startswith("ODDS_API_KEY") and "=" in line:
                    os.environ["ODDS_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return


def fmt_report(flagged, top):
    if not flagged:
        return "No +EV outcomes cleared the threshold."
    rows = flagged[:top]
    out = [f"\n{'EV%':>6}  {'market':18} {'player / side':28} {'pt':>5}  {'book':12} {'price':>6} {'fair%':>6} {'n':>2}"]
    out.append("-" * 96)
    for f in rows:
        who = (f["subject"] or f["side"])[:24]
        side = f["side"] if f["subject"] else ""
        label = f"{who} {side}".strip()[:28]
        pt = "" if f["point"] is None else f'{f["point"]:g}'
        am = f'{f["american"]:+d}'
        out.append(
            f'{f["ev"]*100:>6.1f}  {f["market"]:18} {label:28} {pt:>5}  '
            f'{f["book"]:12} {am:>6} {f["fair_consensus"]*100:>5.1f}% {f["n_books"]:>2}'
        )
    out.append(f"\n{len(flagged)} total flagged; showing top {len(rows)}.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true", help="actually spend credits")
    ap.add_argument("--regions", default="us")
    ap.add_argument("--props", default=",".join(DEFAULT_PROPS))
    ap.add_argument("--ev-threshold", type=float, default=0.02)
    ap.add_argument("--method", default="multiplicative",
                    choices=["multiplicative", "power", "shin"])
    ap.add_argument("--min-books", type=int, default=2)
    ap.add_argument("--target-book", default="draftkings",
                    help="only flag prices bettable here (the app you use); 'all' to see every book")
    ap.add_argument("--ref-books", default=",".join(SHARP_BOOKS),
                    help="comma list: build consensus from only these (sharp) books; 'all' to use every other book")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    load_env()
    prop_markets = [m.strip() for m in args.props.split(",") if m.strip()]
    client = OddsAPIClient(
        cache_dir=ROOT / "data/cache",
        ledger_path=ROOT / "data/odds_api_credits.json",
        dry_run=not args.confirm,
    )

    events = client.get_events(SPORT)  # FREE
    n = len(events)
    feat_cost = client.estimate_featured(len(FEATURED), args.regions)
    prop_cost = client.estimate_event_props(n, len(prop_markets), args.regions)
    total = feat_cost + prop_cost

    print(f"== WNBA scout ==  events on slate: {n}  regions: {args.regions}")
    print(f"   featured ({'+'.join(FEATURED)}): {feat_cost} cr")
    print(f"   props {prop_markets} x {n} events: {prop_cost} cr")
    print(f"   ESTIMATED TOTAL (live rate = markets x regions): {total} cr")
    rem = client.remaining_credits()
    if rem is not None:
        print(f"   account remaining (last seen): {rem}")

    if not args.confirm:
        print("\nDRY RUN — no credits spent. Re-run with --confirm to pull.")
        return

    print("\nConfirmed. Pulling...")
    all_events = []
    feat = client.get_featured_odds(SPORT, FEATURED, args.regions)
    all_events.extend(feat)
    for i, ev in enumerate(events, 1):
        eo = client.get_event_odds(SPORT, ev["id"], prop_markets, args.regions)
        all_events.append(eo)
        print(f"   [{i}/{n}] {ev.get('away_team')} @ {ev.get('home_team')}  "
              f"(session spend so far: {client.spent_this_session} cr)")

    target = None if args.target_book == "all" else {args.target_book}
    ref = None if args.ref_books == "all" else ({b.strip() for b in args.ref_books.split(",") if b.strip()} or None)
    flagged = scan(
        all_events,
        method=args.method,
        ev_threshold=args.ev_threshold,
        min_books=args.min_books,
        target_books=target,
        ref_books=ref,
    )
    print(f"\ntarget book (bettable): {args.target_book}"
          + (f"  |  consensus from: {sorted(ref)}" if ref else ""))
    print(fmt_report(flagged, args.top))
    print(f"\nCredits spent this session: {client.spent_this_session}  |  "
          f"remaining: {client.remaining_credits()}")


if __name__ == "__main__":
    main()
