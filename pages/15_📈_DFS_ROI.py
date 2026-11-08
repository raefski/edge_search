"""pages/15_📈_DFS_ROI.py -- a door to the DFS ROI dashboard on claude.ai.

The dashboard itself (ROI by sport / cash-GPP with 90% ranges, contest groups,
projected vs actual, "Is the model improving?") is a private claude.ai artifact,
refreshed from the desktop (`harness run roi-dashboard-rows`, then uploaded). It is
NOT rebuilt here on purpose: this app reads its data off a PUBLIC GitHub repo, and
Adam chose (2026-11-08) to keep his P&L off it. The link opens in the Claude app or
claude.ai, where he is signed in; nobody else can open it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pagereload import fresh  # noqa: E402

fresh()   # a Cloud deploy reloads every repo module (edge/pagereload.py)

DASHBOARD = "https://claude.ai/artifact/N4rKiCCFV2h9LHcTTq3Xc5"

st.set_page_config(page_title="DFS ROI", page_icon="📈", layout="centered")
st.title("📈 DFS ROI")
st.caption("ROI by sport and contest type with 90% ranges, contest size and overlay groups, "
           "projected vs actual points, and **Is the model improving?** (every critique "
           "cycle, shipped or not).")
st.link_button("Open the ROI dashboard", DASHBOARD, type="primary", width="stretch")
st.caption("Private to you: it opens in the Claude app or claude.ai, where you're signed in. "
           "It is kept off this app because the app's data is on a public GitHub repo. "
           "Numbers are as of the last desktop refresh (ask Claude to \"refresh the ROI dashboard\").")
