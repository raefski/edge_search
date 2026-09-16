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
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from edge import dfs  # noqa: E402
from odds_collect import push_snapshot  # noqa: E402

SPORTS = ("MLB", "NFL")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--push", action="store_true", help="commit and push the snapshot dir")
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    live, failed = set(), False
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
            live.add(int(gid))
            try:
                n = dfs.save_draftables_snapshot(int(gid))
            except Exception as e:
                print(f"  {sport} {gid}: fetch failed ({e})")
                failed = True
                continue
            print(f"  {sport} {gid}: {n or 'unpriced'}")
            time.sleep(0.3)

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
