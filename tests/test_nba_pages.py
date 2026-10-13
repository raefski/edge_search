"""Drive the NBA and college basketball pages headlessly (AppTest, the real
script runner) on the synthetic slate from tests/test_dfs_nba.py -- the lobby,
DK draftables and the odds client are all faked, so no network is touched.

The page calls edge.dfs_pagereload.reload_packages() on first render, which
re-executes every edge.dfs* module and would wipe the fakes; it is stubbed out
for the test (the reload itself has its own tests).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

pytest.importorskip("streamlit")

import test_dfs_nba as T  # noqa: E402

PAGES = {"nba": ROOT / "pages" / "12_🏀_NBA_DFS.py",
         "ncaab": ROOT / "pages" / "13_🏀_NCAAB_DFS.py"}


@pytest.fixture
def fake_world(monkeypatch):
    from edge import dfs, dfs_pagereload
    from edge.odds import cli
    sal = {**T._salaries(T.NBA_TEAMS, T.NBA_POS, "Nba"),
           **T._salaries(T.CBB_GAMES, T.CBB_POS, "Cbb")}
    nba_sal = {k: v for k, v in sal.items() if v["name"].startswith("Nba")}
    cbb_sal = {k: v for k, v in sal.items() if v["name"].startswith("Cbb")}
    groups = {"NBA": T._groups(70), "CBB": [dict(g, DraftGroupId=g["DraftGroupId"] + 100)
                                            for g in T._groups(5)]}
    monkeypatch.setattr(dfs_pagereload, "reload_packages", lambda *a, **k: 0)
    monkeypatch.setattr(dfs, "draft_groups", lambda sport: groups.get(sport, []))
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES",
                        {"CBB": [{"GameTypeId": 5, "Name": "Classic", "DraftType": "SalaryCap"}]})
    monkeypatch.setattr(dfs, "fetch_draftables",
                        lambda gid: nba_sal if int(gid) < 600 else cbb_sal)
    clients = {"basketball_nba": T.FakeClient(nba_sal, T.NBA_TEAMS, T.BOOK_NAME),
               "basketball_ncaab": T.FakeClient(cbb_sal, T.CBB_GAMES, T.CBB_NAME,
                                                props_per_team=2, book_team=False)}
    monkeypatch.setattr(cli, "scraped_client", lambda sport, consumer="dfs", **k: clients[sport])
    # no contest lobby: the GPP tab's contest picker must cope with an empty one
    from edge import dfs_leverage_ui as LU
    monkeypatch.setattr(LU, "slate_gpps", lambda sport, gid: [])
    # the forward-test log must not be written by a test
    from edge import dfs_run_nba
    monkeypatch.setattr(dfs_run_nba, "log_forward_test", lambda *a, **k: {"logged": False})
    return clients


@pytest.mark.parametrize("which", ["nba", "ncaab"])
def test_page_renders_both_lineups(fake_world, which):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(PAGES[which]), default_timeout=180)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    html = " ".join(m.value for m in at.markdown)
    assert "Pts" in html and "UTIL" in html
    assert html.count("<table class='lu'>") >= 3          # cash, gpp, board
    if which == "nba":
        assert "Out (dropped)" in html and "Questionable" in html
        for slot in ("PG", "SG", "SF", "PF", "C", "G", "F"):
            assert f"<td class='pos'>{slot}</td>" in html
    else:
        assert "from DK FPPG" in html and "G G G" not in html


def test_leverage_toggle_renders(fake_world):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(PAGES["nba"]), default_timeout=240)
    at.run()
    at.toggle(key="nba_lev_on").set_value(True).run()
    assert not at.exception, [e.value for e in at.exception]
