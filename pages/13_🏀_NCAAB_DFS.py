"""pages/13_🏀_NCAAB_DFS.py — DK College Basketball cash + GPP lineups.

edge/dfs_run_ncaab.py: the NBA pipeline (edge/dfs_run_nba.py) under college's
roster (G G G F F F UTIL UTIL), prop supply and team names. Most college
players have no prop, so most projections are DK's FPPG scaled to tonight's
implied team points -- the board marks every player's source. The body is
edge/dfs_hoops_ui.py, shared with the NBA page.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="DK College Basketball DFS", page_icon="🏀",
                   layout="wide", initial_sidebar_state="auto")

from edge.dfs_pagereload import reload_packages, source_fingerprint  # noqa: E402


@st.cache_resource(show_spinner=False)
def _reload_edge(fingerprint: float) -> float:
    reload_packages()
    return fingerprint


_reload_edge(source_fingerprint())

from edge import dfs_hoops_ui as UI, dfs_opt_nba, dfs_run_ncaab as R  # noqa: E402


@st.cache_data(ttl=300, show_spinner=False)
def _cbb_slates(_nonce: int):
    from edge import dfs, ncaab
    return R.classic_groups(dfs.draft_groups(ncaab.DK_SPORT))


@st.cache_data(ttl=300, show_spinner=False)
def _cbb_build(gid, sims: int, iters: int, _nonce: int):
    return R.build_slate(draft_group=gid, n_sims=sims, iters=iters)


@st.cache_data(ttl=600, show_spinner=False)
def _cbb_leverage(gid, sims: int, iters: int, _nonce: int):
    from edge import dfs_leverage_sports as LS
    return LS.hoops(_cbb_build(gid, sims, iters, _nonce), None, iters=max(20, iters // 3))


@st.cache_data(ttl=600, show_spinner=False)
def _cbb_leverage_for(gid, sims: int, iters: int, _nonce: int, contest: dict):
    from edge import dfs_leverage as lev
    return lev.with_contest(_cbb_leverage(gid, sims, iters, _nonce),
                            _cbb_build(gid, sims, iters, _nonce)["sim"], contest)


def _portfolio(res, n, iters):
    return dfs_opt_nba.portfolio(res["pool"], res["sim"], n, dfs_opt_nba.NCAAB, iters=iters)


UI.run(sport="NCAAB", key="cbb", title="NCAAB DFS", icon="🏀", slates_fn=_cbb_slates,
       build_fn=_cbb_build, lev_fn=_cbb_leverage, lev_for_fn=_cbb_leverage_for,
       portfolio_fn=_portfolio, default_slate=R.default_slate,
       board_note="Source marks: none = props; ·f = DK's FPPG scaled to tonight's implied "
                  "team points; ·$ = no FPPG yet (early season, or a new rotation player), "
                  "projected off DK's own salary at 4.6 points per $1K — a prior. ⚠️ = DK "
                  "lists him questionable. A min-salary player with no FPPG is left off as "
                  "a probable DNP.")
