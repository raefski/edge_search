"""pages/12_🏀_NBA_DFS.py — DK NBA cash + GPP lineups, on your phone.

Everything comes from edge/dfs_run_nba.py, which the CLI also calls: DraftKings
salaries and injury status, every book's player props (points, rebounds,
assists, threes, steals, blocks, turnovers, combos), the game lines, and a
simulation of every game on the slate (edge/nba_sim.py) that scores lineups on
percentiles. The body is edge/dfs_hoops_ui.py, shared with college; only the
cached functions live here, because pages share Streamlit's cache by name.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="DK NBA DFS Lineups", page_icon="🏀",
                   layout="wide", initial_sidebar_state="auto")

from edge.dfs_pagereload import reload_packages, source_fingerprint  # noqa: E402


@st.cache_resource(show_spinner=False)
def _reload_edge(fingerprint: float) -> float:
    reload_packages()
    return fingerprint


_reload_edge(source_fingerprint())

from edge import dfs_hoops_ui as UI, dfs_opt_nba, dfs_run_nba as R  # noqa: E402


@st.cache_data(ttl=300, show_spinner=False)
def _nba_slates(_nonce: int):
    from edge import dfs, nba
    return R.classic_groups(dfs.draft_groups(nba.DK_SPORT))


@st.cache_data(ttl=300, show_spinner=False)
def _nba_build(gid, sims: int, iters: int, _nonce: int):
    return R.build_slate(draft_group=gid, n_sims=sims, iters=iters)


@st.cache_data(ttl=600, show_spinner=False)
def _nba_leverage(gid, sims: int, iters: int, _nonce: int):
    from edge import dfs_leverage_sports as LS
    return LS.hoops(_nba_build(gid, sims, iters, _nonce), None, iters=max(20, iters // 3))


@st.cache_data(ttl=600, show_spinner=False)
def _nba_leverage_for(gid, sims: int, iters: int, _nonce: int, contest: dict):
    from edge import dfs_leverage as lev
    return lev.with_contest(_nba_leverage(gid, sims, iters, _nonce),
                            _nba_build(gid, sims, iters, _nonce)["sim"], contest)


def _portfolio(res, n, iters):
    return dfs_opt_nba.portfolio(res["pool"], res["sim"], n, dfs_opt_nba.NBA, iters=iters)


UI.run(sport="NBA", key="nba", title="NBA DFS", icon="🏀", slates_fn=_nba_slates,
       build_fn=_nba_build, lev_fn=_nba_leverage, lev_for_fn=_nba_leverage_for,
       portfolio_fn=_portfolio, default_slate=R.default_slate,
       board_note="Source marks: none = props (every book that posted a line, "
                  "averaged); ·f = DK's FPPG, scaled to tonight's implied team points "
                  "(it does NOT move when a teammate is ruled out); ⚠️ = DK lists him "
                  "questionable. A priced player with no prop and no FPPG is left off "
                  "the board as a probable DNP.")
