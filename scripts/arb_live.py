#!/usr/bin/env python3
"""Live arbitrage on ONE game, DraftKings and FanDuel only.

    python3 scripts/arb_live.py giants                       # one pass
    python3 scripts/arb_live.py giants --every 20            # watch until it ends
    python3 scripts/arb_live.py yankees --sport baseball_mlb --every 30
    python3 scripts/arb_live.py giants --no-props            # main + alt lines only, faster

Nothing is shown off a single read: an arbitrage has to still be there on an
immediate second read, and the prices printed are the second read's. See
edge/arb/live.py for why (FanDuel's CDN cache, and five live arbs that were all
gone two minutes later on 2026-09-13).

Must run from a residential connection -- DraftKings 403s datacenter IPs.
DraftKings' feed shows no suspension flag: check its app before staking.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.arb import ArbConfig, engine             # noqa: E402
from edge.arb.draftkings_league import DraftKingsLeague  # noqa: E402
from edge.arb.fanduel import FanDuelScrape         # noqa: E402
from edge.arb.live import (DEFAULT_MAX_AGE_SECONDS, closest, find_game,  # noqa: E402
                           live_config, scan_game)
from edge.arb.normalize import side_label          # noqa: E402

BOOK = {"draftkings": "DraftKings", "fanduel": "FanDuel"}


def _line(market: str, point) -> str:
    """A spread is signed (+4.5 / -4.5); a total or prop line is not."""
    if point is None:
        return ""
    return f" {point:+g}" if engine.is_spread_market(market) else f" {point:g}"


def _leg(l, market: str) -> str:
    return (f"{BOOK.get(l.book, l.book)} {l.label}{_line(market, l.point)} "
            f"@ {l.american} (${l.stake:g})")


def _pass(game, cfg, fd, dk, props: bool) -> bool:
    res = scan_game(game, cfg, fd, dk, props=props)
    r = res.read
    stamp = datetime.now().strftime("%H:%M:%S")
    age = f"FD cache age max {max(r.fd_ages):.0f}s" if r.fd_ages else "FD: no response"
    q = " / ".join(f"{BOOK.get(b, b)} {n}" for b, n in sorted(r.quotes.items()))
    print(f"\n{stamp}  {r.state.describe()}")
    reads = "2 reads" if res.second_read else "1 read"
    print(f"          {reads}, last {r.seconds:.1f}s · {q or 'no quotes'} · {age}")
    if any(r.failed.values()):
        print("  ! failed requests this read: "
              + ", ".join(f"{BOOK.get(b, b)} {n}" for b, n in sorted(r.failed.items()) if n))

    for o in res.confirmed:
        print(f"  ✅ ARB {o.profit_pct:+.2f}%  {o.market}"
              f"{' ' + o.subject if o.subject else ''}  (profit ${o.profit_abs:.2f} on ${o.stake_total:g})")
        for l in o.legs:
            print(f"       {_leg(l, o.market)}")
        for w in o.warnings:
            print(f"       ! {w}")
        print("       ! DraftKings' feed shows no suspension: confirm both prices in the apps first")
    for o in res.vanished:
        print(f"  ✗ gone on re-read: {o.profit_pct:+.2f}% {o.market}"
              f"{' ' + o.subject if o.subject else ''} "
              + " | ".join(f"{BOOK.get(l.book, l.book)} {l.side} {l.point if l.point is not None else ''}"
                           for l in o.legs))
    if not res.confirmed:
        near = closest(r.board, cfg)
        if near:
            print("  no arb. closest:")
            for s, g, best in near:
                ev = g.event
                legs = " | ".join(
                    f"{BOOK.get(qt.book, qt.book)} {side_label(side, ev.home_team, ev.away_team, g.key.subject)}"
                    # the leg's OWN line: spreads are stored folded onto the
                    # home axis, and printed raw both teams lay the same points
                    f"{_line(g.key.market, engine._leg_point(qt, g))} {qt.decimal:.3f}"
                    for side, qt in best)
                print(f"    {s:.4f}  {g.key.market}{' ' + g.key.subject if g.key.subject else ''}  {legs}")
        elif r.quotes:
            print(f"  no arb, and no two-book pair fresh enough to compare -- the cap is "
                  f"{cfg.detect.max_quote_age_seconds:g}s and FanDuel's CDN copy was up to "
                  f"{max(r.fd_ages or [0]):.0f}s old")
        else:
            print("  nothing priced -- is the game on, and is this a residential connection?")
    return r.state.status not in ("FINISHED", "COMPLETED", "ENDED")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game", help="any part of FanDuel's name for it: giants, 'cowboys @'")
    ap.add_argument("--sport", default="americanfootball_nfl")
    ap.add_argument("--every", type=float, default=0.0,
                    help="seconds between passes; 0 = one pass and exit")
    ap.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE_SECONDS,
                    help="refuse a leg older than this, FanDuel's CDN age included")
    ap.add_argument("--bankroll", type=float, default=None)
    ap.add_argument("--min-profit", type=float, default=None, help="arb %% floor")
    ap.add_argument("--no-props", action="store_true")
    ap.add_argument("--state", default="ct")
    args = ap.parse_args()

    cfg = live_config(ArbConfig(), max_age_seconds=args.max_age)
    cfg.state = args.state
    if args.bankroll:
        cfg.bankroll.total = args.bankroll
    if args.min_profit is not None:
        cfg.detect.min_profit_pct = args.min_profit

    fd, dk = FanDuelScrape(state=args.state), DraftKingsLeague(state=args.state)
    game = find_game(args.game, args.sport, fd, dk)
    print(f"{game.name}  (FanDuel {game.fd_event_id}, DraftKings "
          f"{game.dk_event_id or 'NOT FOUND -- FanDuel only, nothing can pair'})")

    while True:
        try:
            going = _pass(game, cfg, fd, dk, props=not args.no_props)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:                   # noqa: BLE001
            # one bad read (a 403, a timeout) must not end a watch mid-game
            print(f"\n{datetime.now():%H:%M:%S}  pass failed: {type(exc).__name__}: {exc}")
            going = True
        if not args.every or not going:
            return 0
        try:
            time.sleep(args.every)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
