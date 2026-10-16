"""Drive the college basketball page headlessly (AppTest, the real script
runner) on the synthetic slate from tests/test_dfs_ncaab.py -- the lobby, DK
draftables and the odds client are all faked, so no network is touched.

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

import test_dfs_ncaab as T  # noqa: E402

PAGE = ROOT / "pages" / "13_🏀_NCAAB_DFS.py"


@pytest.fixture
def fake_world(monkeypatch):
    from edge import dfs, dfs_pagereload, dfs_run_ncaab
    from edge.odds import cli
    sal = T._salaries(T.CBB_GAMES, T.CBB_POS, "Cbb")
    monkeypatch.setattr(dfs_pagereload, "reload_packages", lambda *a, **k: 0)
    monkeypatch.setattr(dfs, "draft_groups", lambda sport: T._groups(5) if sport == "CBB" else [])
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES",
                        {"CBB": [{"GameTypeId": 5, "Name": "Classic", "DraftType": "SalaryCap"}]})
    monkeypatch.setattr(dfs, "fetch_draftables", lambda gid: sal)
    client = T.FakeClient(sal, T.CBB_GAMES, T.CBB_NAME, props_per_team=2, book_team=False)
    monkeypatch.setattr(cli, "scraped_client", lambda sport, consumer="dfs", **k: client)
    # no contest lobby: the GPP tab's contest picker must cope with an empty one
    from edge import dfs_leverage_ui as LU
    monkeypatch.setattr(LU, "slate_gpps", lambda sport, gid: [])
    # the forward-test log must not be written by a test
    monkeypatch.setattr(dfs_run_ncaab, "log_forward_test", lambda *a, **k: {"logged": False})
    return client


def test_page_renders_both_lineups(fake_world):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(PAGE), default_timeout=180)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    html = " ".join(m.value for m in at.markdown)
    assert "Pts" in html and "UTIL" in html
    assert html.count("<table class='lu'>") >= 3          # cash, gpp, board
    assert "from DK FPPG" in html and "Out (dropped)" in html


def test_leverage_toggle_renders(fake_world):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(PAGE), default_timeout=240)
    at.run()
    at.toggle(key="cbb_lev_on").set_value(True).run()
    assert not at.exception, [e.value for e in at.exception]
