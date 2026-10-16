"""The forward-test log: what every DFS build is graded against, written once.

A contest export is only gradable if the projections it is compared with were
logged BEFORE lock (an unlogged slate is unrecoverable -- MLB 2026-09-17, the
lost 7/24-7/31 slates). Seven sports each carried their own copy of the
writer, and the copies had drifted: four of them overwrote the whole day's
lineups file, so on a three-slate Saturday the NHL file kept only the last
slate's lineups (MMA had found and fixed that on its first card). The rules
now live here and each sport supplies only its columns:

  1. A build for a PAST date (US/Eastern) is never logged -- a review rebuild
     clobbered real forward-test data once already.
  2. Only THIS draft group's rows are replaced. Dropping the whole date wiped
     the day's other slates (NCAAF 2026-09-26 lost slate 153831's 106 rows when
     153951 was built that evening). Rows are matched by DRAFT GROUP, never by
     "the nearest slate" (CLAUDE.md).
  3. `frozen=True` keeps an existing log for this draft group untouched (MMA
     after the first bout: the book drops its market and a rebuild would price
     that bout from salaries -- found 2026-09-26).
  4. The day's lineups file `dfs_lineups_<sport>_<date>.csv` leads with `gid`
     and keeps every OTHER draft group's rows. A pre-2026-10-16 file without a
     gid column is replaced, as it always was.

MLB (edge/dfs_run.py) is the exception: its log predates draft-group keys,
splits sub-slates into per-(date, gid) files, and its calibration scripts read
those names.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")


def today_et() -> str:
    return datetime.now(ET).date().isoformat()


def _read(path: Path) -> tuple[list[str], list[dict]]:
    if not path.exists():
        return [], []
    with path.open(newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def write(sport: str, date: str | None, gid, cols: tuple, rows: list[dict],
          lineup_cols: tuple | None = None, lineups: list[dict] | None = None,
          root: Path | None = None, frozen: bool = False) -> dict:
    """Log one build. `rows` are the pool, `lineups` the recommended lineups
    (None = leave the lineups file alone), both dicts keyed by their columns.
    Returns {logged, n, date} plus skipped_past_date / skipped_after_lock /
    lineup_file when they apply."""
    root = root or ROOT
    result = {"logged": False, "n": 0, "date": date}
    if not date:
        return result
    if date < today_et():
        result["skipped_past_date"] = True
        return result
    gid = str(gid)
    plog = root / "data" / f"dfs_proj_log_{sport}.csv"
    plog.parent.mkdir(parents=True, exist_ok=True)
    _, prior = _read(plog)
    mine = [r for r in prior if r.get("date") == date and str(r.get("gid")) == gid]
    if frozen and mine:
        result["skipped_after_lock"] = True
        return result
    keep = [r for r in prior if not (r.get("date") == date and str(r.get("gid")) == gid)]
    with plog.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(cols))
        for r in keep + list(rows):
            w.writerow([r.get(c, "") for c in cols])
    result.update({"logged": True, "n": len(rows)})

    if lineups is not None and lineup_cols:
        lpath = root / "data" / f"dfs_lineups_{sport}_{date}.csv"
        header, old = _read(lpath)
        others = [r for r in old if str(r.get("gid")) != gid] if "gid" in header else []
        full = ("gid", *lineup_cols)
        with lpath.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(list(full))
            for r in others:
                w.writerow([r.get(c, "") for c in full])
            for r in lineups:
                w.writerow([gid] + [r.get(c, "") for c in lineup_cols])
        result["lineup_file"] = str(lpath.relative_to(root))
    return result
