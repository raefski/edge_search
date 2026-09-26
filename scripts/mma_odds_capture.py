#!/usr/bin/env python3
"""Capture DraftKings Sportsbook's UFC markets for the MMA DFS build.

    python3 scripts/mma_odds_capture.py            # capture + archive, no git
    python3 scripts/mma_odds_capture.py --push     # ...and commit/push the snapshot

WHY THIS IS ITS OWN SCRIPT AND NOT AN edge/odds PROFILE
The odds layer maps every market into The Odds API's vocabulary
(`player_pass_yds`, `h2h`, ...) and collapses ladders to one rung, and there is
no Odds API key for "Fighter X by KO/TKO in round 2". The MMA model reads the
round-and-method grid, the six method-of-victory prices and the per-fighter
strike and takedown lines, so it stores DraftKings' own payload and parses it
in edge/mma_market.py. Eight requests a run.

WHAT IT WRITES
  data/odds_snapshot_dfs_mma.json   the latest capture, trimmed to cards in
                                    the next --days. COMMITTED (the cloud page
                                    cannot reach DraftKings; see .gitignore).
  data/mma_odds_history/<date>/<time>.json.gz
                                    every capture, gitignored. The last one
                                    before a card locks is what a future
                                    re-fit of the market calibration joins to
                                    results -- and it cannot be re-scraped
                                    after the fact.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from edge.arb import draftkings_league as dkl        # noqa: E402
from edge.mma_market import DK_BOOK_LEAGUE           # noqa: E402

SNAPSHOT = ROOT / "data" / "odds_snapshot_dfs_mma.json"
HISTORY = ROOT / "data" / "mma_odds_history"

#: The tabs edge/mma_market.parse_book reads, BY NAME. DraftKings' subcategory
#: ids are stable in practice but nothing promises it, and a renamed tab
#: should fail loudly here rather than silently parse to nothing.
WANTED = ("fight lines", "method of victory", "round and method of victory",
          "fight to go the distance", "significant strikes o/u",
          "takedowns landed o/u", "alternative total rounds")
GAP_SECONDS = 0.5


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _trim(payload: dict, keep: set) -> dict:
    """Drop every event not in `keep`, and its markets and selections."""
    out = {k: v for k, v in payload.items() if k != "subscriptionPartials"}
    out["events"] = [e for e in payload.get("events") or [] if str(e.get("id")) in keep]
    mk = [m for m in payload.get("markets") or [] if str(m.get("eventId")) in keep]
    ids = {m.get("id") for m in mk}
    out["markets"] = mk
    out["selections"] = [s for s in payload.get("selections") or []
                         if s.get("marketId") in ids]
    return out


def capture(days: float = 8.0) -> dict:
    dk = dkl.DraftKingsLeague()
    league = dk.fetch_league(DK_BOOK_LEAGUE)
    horizon = datetime.now(timezone.utc) + timedelta(days=days)
    keep = {str(e["id"]) for e in league.get("events") or []
            if (_parse_time(e.get("startEventDate")) or horizon) < horizon}
    bundle = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "source": "draftkings", "league_id": DK_BOOK_LEAGUE,
              "league": _trim(league, keep), "subcategories": {}, "missing": []}
    found = set()
    for sc in league.get("subcategories") or []:
        name = (sc.get("name") or "").strip().lower()
        if name not in WANTED:
            continue
        time.sleep(GAP_SECONDS)
        payload = dk.fetch_league_subcategory(DK_BOOK_LEAGUE, sc["categoryId"], sc["id"])
        bundle["subcategories"][f"{sc['categoryId']}/{sc['id']}"] = {
            "name": sc.get("name"), "payload": _trim(payload, keep)}
        found.add(name)
    bundle["missing"] = [w for w in WANTED if w not in found]
    bundle["events"] = len(keep)
    return bundle


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=8.0,
                    help="keep cards starting within this many days")
    ap.add_argument("--push", action="store_true",
                    help="commit and push the snapshot for the cloud app")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    bundle = capture(args.days)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(bundle, separators=(",", ":")))
    now = datetime.now(timezone.utc)
    arch = HISTORY / now.strftime("%Y-%m-%d") / f"{now:%H%M%S}.json.gz"
    arch.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(arch, "wt") as fh:
        json.dump(bundle, fh, separators=(",", ":"))
    kb = SNAPSHOT.stat().st_size / 1024
    print(f"mma odds: {bundle['events']} fights, {len(bundle['subcategories'])} tabs, "
          f"{kb:.0f} KB -> {SNAPSHOT.relative_to(ROOT)}; archived {arch.relative_to(ROOT)}")
    if bundle["missing"]:
        # Not fatal -- a card far out has no props yet -- but say so, because
        # a tab DraftKings RENAMED looks identical from here.
        print(f"  ! tabs not offered: {', '.join(bundle['missing'])}")
    if args.push:
        from odds_collect import push_snapshot
        return 0 if push_snapshot(SNAPSHOT, "dfs_mma", args.branch) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
