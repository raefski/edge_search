#!/usr/bin/env python3
"""Refresh cfbfastR and publish each QB's rushing touchdowns per team game.

    python3 scripts/ncaaf_qb_rates_publish.py           # write data/ncaaf_qb_rush_td.json
    python3 scripts/ncaaf_qb_rates_publish.py --push    # ...and publish it to the data branch

The NCAAF build projects a quarterback's rushing touchdowns from his own
season rate rather than his rush yards / 110 (edge/dfs_run_ncaaf.py
QB_RUSH_TD_*, critique cycle 3, 2026-11-07). The rate is
edge.ncaaf.qb_rush_td_rates over the whole cfbfastR season file, which only
holds games already played. The file has to be FRESH for that to be the rate
the lab measured (weeks before the slate): on 2026-11-07 the cached copy
stopped at week 7 and the season was in week 10. So this re-downloads it
(~35 MB) every run.

Published to the data branch because Streamlit Cloud builds the pool too and
cannot be asked to download cfbfastR on every page load: it reads this small
table through edge/repo_files. The table is skipped (not rewritten) when the
rates did not change, so a quiet day makes no commit.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import ncaaf  # noqa: E402
from edge.dfs_run_ncaaf import QB_RUSH_TD_PATH  # noqa: E402


def build(season: int) -> dict:
    plays = ncaaf.fetch_season(season, refresh=True)
    rates = ncaaf.qb_rush_td_rates(plays)
    # Every uniquely named player, not only passers, so the build rates exactly
    # who the lab rated (a DK QB whose snaps so far were all runs included):
    # ~5,500 players, ~650 KB.
    players = {k: {**v, "rate": round(v["rate"], 4)} for k, v in rates.items()}
    return {"season": season, "through_week": max(int(r["week"]) for r in plays),
            "players": players}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--season", type=int, default=datetime.now().year)
    a = ap.parse_args()
    doc = build(a.season)
    try:
        old = json.loads(QB_RUSH_TD_PATH.read_text())
    except (OSError, ValueError):
        old = {}
    if {k: old.get(k) for k in doc} == doc:
        print(f"unchanged: {len(doc['players'])} players through week {doc['through_week']}")
        return 0
    doc["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    QB_RUSH_TD_PATH.write_text(json.dumps(doc, indent=0, sort_keys=True))
    print(f"wrote {QB_RUSH_TD_PATH.relative_to(ROOT)}: {len(doc['players'])} players "
          f"through cfbfastR week {doc['through_week']}")
    if a.push:
        from edge.data_branch import publish_one
        if not publish_one(str(QB_RUSH_TD_PATH.relative_to(ROOT)), "ncaaf QB rush-TD rates"):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
