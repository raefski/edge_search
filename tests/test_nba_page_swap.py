"""Drive the NBA page headlessly (AppTest, the real script runner) on the night's worst
case, 2026-11-01: a slate locked at 8:30, Poeltl ruled out at 8:44, his game tipping at
9:00. The alert's link has to land on a swap that works, with DK's lobby no longer
listing the slate and the page's current build no longer listing the player."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from edge import dfs, dfs_nba_theory as T, dfs_opt_nba as O, dfs_run_nba as R

PAGE = next((Path(__file__).resolve().parents[1] / "pages").glob("12_*NBA_DFS.py"))
GID, NEXT = 156049, 156054


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


@pytest.fixture
def night(monkeypatch, tmp_path):
    import streamlit as st
    st.cache_data.clear()              # the page's slate list outlives a test otherwise
    now = datetime.now(timezone.utc)
    tipped, tips = _iso(now - timedelta(minutes=17)), _iso(now + timedelta(minutes=13))
    pos = ["PG", "SG", "SF", "PF", "C", "PG/SG", "SF/PF", "PF/C"]
    pool = []
    for k, (team, game, start) in enumerate((("GSW", "g1", tipped), ("LAC", "g1", tipped),
                                             ("TOR", "g2", tips), ("LAL", "g2", tips))):
        for i in range(10):
            pool.append({"name": f"{team}{i}", "team": team, "opp": "", "game": game, "start": start,
                         "dk_pos": pos[i % 8], "pos": pos[i % 8].split("/")[0], "elig": O.eligible(pos[i % 8]),
                         "salary": 3000 + 700 * ((i * 7 + k) % 10), "proj": 15.0 + 3 * ((i * 5 + k) % 9),
                         "minutes": 25.0, "sd": 8.0, "lineup": "Confirmed", "starter": i < 5})
    sim = T.simulate(pool, n_sims=300, seed=2)
    T.summarise(sim, pool)
    T.add_ownership(pool)
    for p in pool:
        p["value"] = p["proj"] / p["salary"] * 1000
    priced = {p["name"]: {k: p[k] for k in ("salary", "dk_pos", "team", "start", "game")} for p in pool}
    entered = O.optimize(pool, sim, mode="cash", iters=15, seed=0)
    names = [p["name"] for _, p in entered["slots"]]
    out = next(n for n in names if priced[n]["game"] == "g2")
    keep = [i for i, p in enumerate(pool) if p["name"] != out]
    board, sim_b = [pool[i] for i in keep], sim[:, keep]
    current = O.optimize(board, sim_b, mode="cash", iters=15, seed=0)

    def build(draft_group=None, **kw):
        info = {"date": "2026-11-01", "games": 2, "pool": len(board), "priced": len(priced), "priced_rows": priced,
                "implied": {}, "imputed": [], "out": [], "built_at": "8:47 PM ET", "lineups_source": "live"}
        return {"gid": draft_group, "meta": {"gid": draft_group, "label": "(explicit)", "games": 0}, "pool": board,
                "sim": sim_b, "stats": info, "cash": current, "gpp": current, "log": None, "slates": []}

    monkeypatch.setattr(R, "build_slate", build)
    monkeypatch.setattr(R, "logged_lineups", lambda date, path=None: {GID: {"cash": [{"name": n, "team": ""} for n in names]}})
    monkeypatch.setattr(dfs, "draft_groups", lambda sport: [
        {"DraftGroupId": NEXT, "GameTypeId": 70, "GameCount": 11, "ContestStartTimeSuffix": None,
         "StartDate": _iso(now + timedelta(hours=20))}])
    monkeypatch.setattr(dfs, "_SNAP_DIR", tmp_path)
    (tmp_path / dfs.SLATE_INDEX_NAME).write_text(json.dumps({"NBA": {str(GID): {
        "DraftGroupId": GID, "GameTypeId": 70, "GameCount": 2, "ContestStartTimeSuffix": " (Night)",
        "StartDate": tipped, "last_start": tips}}}))
    from edge import dfs_leverage_ui as LU
    monkeypatch.setattr(LU, "slate_gpps", lambda sport, gid: [])
    return {"out": out, "names": names}


def _page(**params):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(PAGE), default_timeout=120)
    for k, v in params.items():
        at.query_params[k] = v
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _html(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_the_alert_link_lands_on_a_legal_swap_with_the_logged_lineup_loaded(night):
    at = _page(gid=str(GID), swap="1", mode="cash", at="1")
    assert at.sidebar.selectbox[0].value.startswith("🔒 Night · 2 games · swap until")
    assert "🔁 Swap" not in [t.label for t in at.tabs]         # it is above them, on the first screen
    assert at.radio(key="nba_swap_mode").value == "cash"
    assert set(at.multiselect(key=f"nba_swap_entered_{GID}_cash").value) == set(night["names"])
    html = _html(at)
    assert "Swap by" in html and night["out"] in html and "→" in html
    assert html.index("Swap by") < html.index("players projected")    # before the summary
    swapped_in = html.split("→")[1].split("<b>")[1].split("</b>")[0]
    assert not swapped_in.startswith(("GSW", "LAC"))           # g1 tipped: DK would refuse them


def test_with_no_link_the_page_opens_on_the_next_slate_and_still_offers_the_locked_one(night):
    at = _page()
    picker = at.sidebar.selectbox[0]
    assert picker.value.startswith("Main · 11 games")
    assert any(o.startswith("🔒 Night") for o in picker.options)
    assert [t.label for t in at.tabs][0] == "💵 CASH"
    assert "This slate has locked" not in _html(at)
    assert "🔁 Swap" in [t.label for t in at.tabs]


def test_the_link_works_when_the_slate_index_does_not_list_the_slate(night):
    (dfs._SNAP_DIR / dfs.SLATE_INDEX_NAME).unlink()
    at = _page(gid=str(GID), swap="1", mode="cash", at="2")
    assert "from the alert" in at.sidebar.selectbox[0].value
    assert "Swap by" in _html(at)


def test_an_unreadable_index_says_so(night):
    (dfs._SNAP_DIR / dfs.SLATE_INDEX_NAME).write_text("{not json")
    at = _page()
    assert any("Locked slates could not be read" in w.value for w in at.sidebar.warning)
    assert at.sidebar.selectbox[0].value.startswith("Main")
