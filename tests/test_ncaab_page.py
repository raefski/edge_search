"""Drive the college basketball page headlessly (AppTest, the real script
runner) on the synthetic slate from tests/test_dfs_ncaab.py -- the lobby, DK
draftables and the odds client are all faked, so no network is touched.

The page calls edge.pagereload.fresh() on first render, which
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
    from edge import dfs, dfs_run_ncaab, pagereload
    from edge.odds import cli
    sal = T._salaries(T.CBB_GAMES, T.CBB_POS, "Cbb")
    monkeypatch.setattr(pagereload, "reload_packages", lambda *a, **k: 0)
    monkeypatch.setattr(dfs, "draft_groups", lambda sport: T._groups(5) if sport == "CBB" else [])
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES",
                        {"CBB": [{"GameTypeId": 5, "Name": "Classic", "DraftType": "SalaryCap"}]})
    monkeypatch.setattr(dfs, "fetch_draftables", lambda gid: sal)
    client = T.FakeClient(sal, T.CBB_GAMES, T.CBB_NAME, props_per_team=2, book_team=False)
    monkeypatch.setattr(cli, "scraped_client", lambda sport, consumer="dfs", **k: client)
    # no contest lobby: the GPP tab's contest picker must cope with an empty one
    from edge import dfs_leverage_ui as LU
    monkeypatch.setattr(LU, "slate_gpps", lambda sport, gid: [])
    # no RotoWire call from a test
    monkeypatch.setattr(dfs_run_ncaab.ncaab_rotowire, "fetch", lambda: "")
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
    assert "last season + salary" in html and "Out (dropped)" in html
    assert any("Name check against DraftKings" in e.label for e in at.expander)


def test_leverage_toggle_renders(fake_world):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(PAGE), default_timeout=240)
    at.run()
    at.toggle(key="cbb_lev_on").set_value(True).run()
    assert not at.exception, [e.value for e in at.exception]


def test_no_fresh_prices_is_a_message_not_a_traceback(fake_world, monkeypatch):
    """A weekly sport's snapshot ages out before the books post props: normal, not a crash."""
    from streamlit.testing.v1 import AppTest
    from edge.odds import cli
    from edge.odds.source import StaleOdds

    def stale(sport, consumer="dfs", **k):
        exc = StaleOdds("snapshot x.json was generated long ago")
        exc.age_seconds = 35 * 3600
        raise exc
    monkeypatch.setattr(cli, "scraped_client", stale)
    import streamlit as st
    st.cache_data.clear()                  # the other tests' builds are cached under these args
    at = AppTest.from_file(str(PAGE), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("last published 35 hours ago" in w.value for w in at.warning)
    assert not any("Build failed" in e.value for e in at.error)


def test_every_dfs_page_handles_stale_odds():
    pages = sorted(p for p in (ROOT / "pages").glob("*DFS*.py") if "ROI" not in p.name)
    assert len(pages) == 7
    for page in pages:
        assert "stale_odds_note(exc" in page.read_text(), page.name


def test_stale_odds_note_only_claims_its_own_errors():
    from edge.odds.cli import stale_odds_note
    from edge.odds.source import StaleOdds
    assert stale_odds_note(ValueError("boom")) is None
    assert "none published" in stale_odds_note(RuntimeError("no free odds for 'dfs_x'"))
    note = stale_odds_note(StaleOdds("old"), "Soon.")
    assert note.endswith("Soon.")


def test_snapshot_staleness_carries_its_age(tmp_path):
    import json
    from edge.odds.publish import SnapshotOddsClient
    from edge.odds.source import StaleOdds
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"profile": "dfs_x", "generated_at": "2026-01-01T00:00:00+00:00", "events": []}))
    with pytest.raises(StaleOdds) as e:
        SnapshotOddsClient(p, max_age_seconds=60)
    assert e.value.age_seconds > 3600
