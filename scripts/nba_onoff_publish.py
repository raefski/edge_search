#!/usr/bin/env python3
"""Build data/nba_onoff.json (edge/nba_onoff.py's with/without table) and push it.

    python3 scripts/nba_onoff_publish.py              # refresh box scores, build, no git
    python3 scripts/nba_onoff_publish.py --push       # ...and commit/push if it changed
    python3 scripts/nba_onoff_publish.py --no-fetch   # build from what is on disk

This season's box scores (and last season's, once, if missing) come from
scripts/nba_ground_truth_collect.py -- one stats.nba.com call per season, so
running this daily costs two requests. Last season's splits are merged in at
half weight (edge/nba_onoff.merge), which is what carries the table through
the first weeks of a season.

stats.nba.com wants a residential IP and browser headers (the collector sends
them); it is not reachable from Streamlit Cloud, which reads the committed
JSON through edge/repo_files.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from edge import nba_onoff  # noqa: E402

GT = ROOT / "data" / "nba_ground_truth"


def season_of(day: date) -> str:
    """'2026-27' from October 2026 on; the season ends in June."""
    y = day.year if day.month >= 9 else day.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def fetch(season: str) -> bool:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "nba_ground_truth_collect.py"),
                           "--season", season], capture_output=True, text=True, timeout=180)
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode == 0


def rows(season: str) -> list:
    p = GT / f"player_gamelog_{season}.json"
    return json.loads(p.read_text()) if p.exists() else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", default=None, help="default: the current one")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    cur = args.season or season_of(date.today())
    y = int(cur[:4])
    prev = f"{y - 1}-{y % 100:02d}"
    if not args.no_fetch:
        fetch(cur)
        if not (GT / f"player_gamelog_{prev}.json").exists():
            fetch(prev)
    now, last = rows(cur), rows(prev)
    if not now and not last:
        print("no box scores on disk -- nothing to build")
        return 1
    table = nba_onoff.build(now) if now else {}
    if last:
        table = nba_onoff.merge(table, nba_onoff.build(last))
    seasons = [s for s, r in ((cur, now), (prev, last)) if r]
    path = nba_onoff.save(table, seasons)
    n_keys = sum(len(v) for v in table.values())
    n_splits = sum(len(e["mates"]) for v in table.values() for e in v.values())
    print(f"{path.relative_to(ROOT)}: {len(table)} teams, {n_keys} key players, "
          f"{n_splits} teammate splits ({', '.join(seasons)}; {len(now)} player-games this "
          f"season)")
    if args.push:
        from odds_collect import push_snapshot
        return 0 if push_snapshot(path, "nba_onoff") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
