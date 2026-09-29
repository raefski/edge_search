#!/usr/bin/env python3
"""Snapshot DailyFaceoff's NHL lines and starting goalies for the cloud app.

    python3 scripts/nhl_lines_publish.py            # write data/nhl_lines_snapshot.json
    python3 scripts/nhl_lines_publish.py --push     # ...and commit + push it if it changed

Covers every team playing today or tomorrow (ET). edge/nhl.py reads DailyFaceoff
live and falls back to this file when it cannot -- the same arrangement as the
odds and DraftKings snapshots, for the same reason: the phone app runs on a
cloud IP that a site may refuse.

Pushes ONLY when lines or goalies changed. The file carries its own capture
time, so writing it unconditionally would make a commit on every tick.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import nhl  # noqa: E402


def collect() -> dict:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    dates = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
    teams, goalies = set(), {}
    for d in dates:
        for g in nhl.schedule(d):
            teams |= {g["home"], g["away"]}
        goalies[d] = nhl.starting_goalies(d, live_only=True)
    lines = {}
    for t in sorted(teams):
        try:
            lines[t] = nhl.team_lines(t, live_only=True)
        except Exception as exc:                            # noqa: BLE001
            print(f"  {t}: {exc}")
    return {"lines": lines, "goalies": goalies}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    data = collect()
    path = nhl.LINES_SNAPSHOT
    old = json.loads(path.read_text()) if path.exists() else {}
    if {k: old.get(k) for k in ("lines", "goalies")} == data:
        print("lines and goalies unchanged -- nothing to write")
        return 0
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(data, indent=0, sort_keys=True))
    print(f"wrote {path.relative_to(ROOT)}: {len(data['lines'])} teams, goalies for "
          f"{', '.join(f'{d} ({len(v)})' for d, v in data['goalies'].items())}")
    if args.push:
        from scripts.odds_collect import push_snapshot
        return 0 if push_snapshot(path, "nhl lines") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
