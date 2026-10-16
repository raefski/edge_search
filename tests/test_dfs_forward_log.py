"""edge/dfs_forward_log.py: the forward-test log rules every sport shares."""
from __future__ import annotations

import csv

from edge import dfs_forward_log as F

COLS = ("date", "gid", "player", "proj")
LCOLS = ("mode", "player")
DAY = "2099-01-02"


def rows(gid, *names):
    return [{"date": DAY, "gid": gid, "player": n, "proj": 1.0} for n in names]


def read(path):
    with path.open() as fh:
        return list(csv.DictReader(fh))


def test_only_this_draft_groups_rows_are_replaced(tmp_path):
    F.write("x", DAY, 1, COLS, rows(1, "a", "b"), root=tmp_path)
    F.write("x", DAY, 2, COLS, rows(2, "c"), root=tmp_path)
    F.write("x", DAY, 1, COLS, rows(1, "a2"), root=tmp_path)
    got = read(tmp_path / "data" / "dfs_proj_log_x.csv")
    assert sorted((r["gid"], r["player"]) for r in got) == [("1", "a2"), ("2", "c")]


def test_a_past_date_is_never_logged(tmp_path):
    res = F.write("x", "2000-01-01", 1, COLS, rows(1, "a"), root=tmp_path)
    assert res["skipped_past_date"] and not (tmp_path / "data" / "dfs_proj_log_x.csv").exists()


def test_a_frozen_slate_keeps_its_first_log(tmp_path):
    F.write("x", DAY, 1, COLS, rows(1, "pre-lock"), root=tmp_path)
    res = F.write("x", DAY, 1, COLS, rows(1, "post-lock"), root=tmp_path, frozen=True)
    assert res["skipped_after_lock"]
    assert [r["player"] for r in read(tmp_path / "data" / "dfs_proj_log_x.csv")] == ["pre-lock"]
    # frozen with nothing logged yet still logs (a first build after lock is all there is)
    assert F.write("x", DAY, 9, COLS, rows(9, "late"), root=tmp_path, frozen=True)["logged"]


def test_a_days_lineups_file_keeps_every_other_slate(tmp_path):
    """The bug four sports had: a three-slate Saturday kept only the last."""
    for gid in (1, 2, 3):
        F.write("x", DAY, gid, COLS, rows(gid, "p"), LCOLS,
                [{"mode": "cash", "player": f"g{gid}"}], root=tmp_path)
    F.write("x", DAY, 2, COLS, rows(2, "p"), LCOLS, [{"mode": "gpp", "player": "g2-rebuilt"}],
            root=tmp_path)
    got = read(tmp_path / "data" / f"dfs_lineups_x_{DAY}.csv")
    assert sorted((r["gid"], r["player"]) for r in got) == [("1", "g1"), ("2", "g2-rebuilt"),
                                                            ("3", "g3")]


def test_an_old_lineups_file_without_gid_is_replaced(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / f"dfs_lineups_x_{DAY}.csv").write_text("mode,player\ncash,old\n")
    F.write("x", DAY, 5, COLS, rows(5, "p"), LCOLS, [{"mode": "cash", "player": "new"}],
            root=tmp_path)
    assert [r["player"] for r in read(tmp_path / "data" / f"dfs_lineups_x_{DAY}.csv")] == ["new"]
