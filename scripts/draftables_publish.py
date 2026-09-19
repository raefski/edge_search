"""Snapshot DraftKings draftables (salaries) for every lobby slate and push them.

DK's draftables API 403s Streamlit Cloud's datacenter IPs (first seen live
2026-09-16, minutes before a 7:10 PM lock), so the cloud app reads
data/draftables_snapshot/<gid>.json instead -- see edge/dfs.py::_draftables_raw.
That fallback is only as good as the snapshot being there before the phone
asks, so this runs on a timer from this machine (deploy/draftables-publish.timer).

Salaries are frozen per draft group, so most ticks change nothing and push
nothing; a commit lands when DK posts a new slate. Groups that have left the
lobby are pruned -- the app resolves slates from the lobby, so it can never ask
for them again.

    python3 scripts/draftables_publish.py            # snapshot + prune, no git
    python3 scripts/draftables_publish.py --push     # ...and commit/push
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from edge import dfs  # noqa: E402
from odds_collect import push_snapshot  # noqa: E402

SPORTS = ("MLB", "NFL")


# Last attempt per draft group, for groups DK has not priced yet (those write
# no snapshot file, so there is nothing else to date them by). Outside
# _SNAP_DIR on purpose: push_snapshot force-adds that whole directory.
_ATTEMPTS = ROOT / "data" / ".draftables_attempts.json"


def _load_attempts() -> dict:
    """{gid: [last_attempt_epoch, consecutive_misses]}."""
    try:
        raw = json.loads(_ATTEMPTS.read_text())
        return {int(k): [float(v[0]), int(v[1])] for k, v in raw.items()}
    except (OSError, ValueError, TypeError, AttributeError, IndexError, KeyError):
        return {}


def _save_attempts(attempts: dict, live: set) -> None:
    try:
        _ATTEMPTS.parent.mkdir(parents=True, exist_ok=True)
        _ATTEMPTS.write_text(json.dumps(
            {str(k): [round(v[0], 1), v[1]] for k, v in attempts.items() if k in live}))
    except OSError:
        pass


def _start_epoch(g: dict) -> float | None:
    raw = (g.get("StartDate") or "").replace("Z", "+00:00")
    # DK sends 7 fractional digits; fromisoformat takes 3 or 6.
    raw = re.sub(r"\.(\d{6})\d+", r".\1", raw)
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def _should_fetch(gid: int, g: dict, attempts: dict, args) -> bool:
    """Whether this group is worth spending a DraftKings request on.

    THE THROTTLE. Before 2026-09-19 this file fetched every draft group in the
    lobby on every tick: ~62 requests, 34 ticks a day, ~2,100 requests/day to
    api.draftkings.com -- while DRAFTKINGS_ACCESS.md described this poll as
    "1-2 calls per tick". Almost all of it was provably wasted, in two ways:

      * A PRICED group cannot change. DK salaries are frozen once posted, and
        that invariant is what makes the snapshot fallback exact at all
        (edge/dfs.py). Re-fetching one cannot return anything new, so it drops
        to a slow --refresh-hours heartbeat.
      * Most UNPRICED groups are never going to be priced. Roughly 45 of the 62
        are Tiers / Snake / Best Ball / season-long game types that carry no
        salary field at all, so they returned "unpriced" every 30 minutes
        forever.

    The second rule is deliberately MEASURED, not a guess at DK's game-type
    taxonomy: a group is backed off because it has actually answered without
    salaries N times in a row, doubling 30min -> 1h -> 2h -> 4h -> cap. That
    can never starve a group the app needs, because the counter resets the
    moment DK prices it, and because a gid seen for the FIRST time has zero
    misses and so is always fetched on the tick it appears -- which is the one
    case the 30-minute timer exists for. Filtering by GameTypeId instead would
    have been fewer requests and a standing risk of silently dropping a slate
    the picker offers (MLB alone lists priced groups under two game types).
    """
    path = dfs._SNAP_DIR / f"{gid}.json"
    now = time.time()
    if path.exists() and path.stat().st_size > 0:            # priced: frozen
        return args.refresh_hours <= 0 or (
            now - path.stat().st_mtime) >= args.refresh_hours * 3600
    last, misses = attempts.get(gid, (0.0, 0))
    start = _start_epoch(g)
    hot = start is None or (start - now) <= args.hot_hours * 3600
    cap = (args.cold_hours if hot else args.cold_hours * 4) * 3600
    # misses-1 so the FIRST retry still lands on the next 30-minute tick:
    # a slate DK prices minutes after listing it must not wait an hour.
    return (now - last) >= min(1800 * (2 ** max(0, misses - 1)), cap)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--push", action="store_true", help="commit and push the snapshot dir")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--refresh-hours", type=float, default=6.0,
                    help="re-fetch an already-priced group at most this often "
                         "(salaries are frozen per group; 0 = every tick)")
    ap.add_argument("--gap", type=float, default=0.5,
                    help="seconds between DK requests")
    ap.add_argument("--hot-hours", type=float, default=36.0,
                    help="an unpriced slate starting within this many hours is "
                         "checked every tick")
    ap.add_argument("--cold-hours", type=float, default=6.0,
                    help="how often to re-check an unpriced slate further out")
    args = ap.parse_args()

    attempts = _load_attempts()
    live, failed, fetched, skipped = set(), False, 0, 0
    for sport in SPORTS:
        try:
            groups = dfs.draft_groups(sport)
        except Exception as e:
            print(f"{sport}: lobby fetch failed ({e}) -- keeping existing snapshots")
            failed = True
            continue
        for g in groups:
            gid = g.get("DraftGroupId")
            if gid is None:
                continue
            gid = int(gid)
            live.add(gid)
            if not _should_fetch(gid, g, attempts, args):
                skipped += 1
                continue
            prev_misses = attempts.get(gid, (0.0, 0))[1]
            attempts[gid] = [time.time(), prev_misses + 1]
            try:
                n = dfs.save_draftables_snapshot(gid)
                if n:
                    attempts[gid][1] = 0          # priced: reset the backoff
                print(f"  {sport} {gid}: {n or 'unpriced'}")
            except Exception as e:
                print(f"  {sport} {gid}: fetch failed ({e})")
                failed = True
            finally:
                # ALWAYS, including after a failure. This used to sit after the
                # success print and be jumped by `continue` on the error path,
                # so a 403 storm fired every request back-to-back with no gap
                # -- the exact burst shape most likely to earn an IP-reputation
                # flag, happening precisely when already being refused.
                fetched += 1
                time.sleep(args.gap)

    _save_attempts(attempts, live)
    print(f"  DK draftables requests this tick: {fetched} "
          f"({skipped} skipped -- priced already, or not close enough to matter)")

    if not failed:
        for f in dfs._SNAP_DIR.glob("*.json"):
            if f.stem.isdigit() and int(f.stem) not in live:
                f.unlink()
                print(f"  pruned {f.name}")

    if args.push and dfs._SNAP_DIR.exists():
        return 0 if push_snapshot(dfs._SNAP_DIR, "draftables", args.branch) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
