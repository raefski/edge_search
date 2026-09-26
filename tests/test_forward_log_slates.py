"""Building a second slate on the same day must not erase the first slate's log.

NCAAF 2026-09-26 lost slate 153831's 106 logged rows when slate 153951 was
built that evening: every logger dropped the whole DATE before appending.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from edge import dfs_run_nascar, dfs_run_ncaaf, dfs_run_nfl

ET = ZoneInfo("America/New_York")


def _later_today() -> str:
    now = datetime.now(ET)
    # Stay on today's ET date whatever the clock says, so the past-date guard
    # never fires.
    t = now.replace(hour=23, minute=0, second=0, microsecond=0)
    return t.astimezone(ZoneInfo("UTC")).isoformat()


def _log(module, root, gid, names):
    pool = [{"name": n} for n in names]
    meta = {"start": _later_today()}
    if module is dfs_run_nascar:
        return module.log_forward_test(pool, None, None, gid, meta, {}, root=root)
    return module.log_forward_test(pool, None, None, gid, meta, root=root)


@pytest.mark.parametrize("module,fname", [
    (dfs_run_ncaaf, "dfs_proj_log_ncaaf.csv"),
    (dfs_run_nfl, "dfs_proj_log_nfl.csv"),
    (dfs_run_nascar, "dfs_proj_log_nascar.csv"),
])
def test_a_second_slate_keeps_the_first(tmp_path, module, fname):
    (tmp_path / "data").mkdir()
    assert _log(module, tmp_path, 1, ["A", "B"])["logged"]
    assert _log(module, tmp_path, 2, ["C"])["logged"]
    assert _log(module, tmp_path, 2, ["D"])["logged"]      # a rebuild of slate 2
    rows = list(csv.DictReader(open(tmp_path / "data" / fname)))
    by_gid = {}
    for r in rows:
        by_gid.setdefault(r["gid"], []).append(r.get("player") or r.get("driver"))
    assert by_gid == {"1": ["A", "B"], "2": ["D"]}
