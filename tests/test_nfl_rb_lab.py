"""scripts/nfl_rb_lab.py: the lab's refit reproduces the shipped skew constants, and its
'shipped' projection IS production's (parity, NFL critique cycle 2)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import nfl_rb_lab as lab                      # noqa: E402
from edge import dfs_project, dfs_sport       # noqa: E402


@pytest.mark.skipif(not all((lab.GT / f).exists() for f in (
        "player_week_2023.json", "player_week_2024.json", "stats_player_week_2025.csv")),
    reason="nflverse train seasons not on disk")
def test_skew_refit_matches_shipped_constants():
    fit = lab.skew_fit()
    for market, (c, b) in dfs_sport.NFL_RB_YDS_SKEW.items():
        assert fit[market][0] == pytest.approx(c, abs=0.01)
        assert fit[market][1] == pytest.approx(b, abs=0.0005)


def test_lab_projection_is_production():
    mk = {"player_rush_yds": {"Over": 1.87, "Under": 1.95, "point": 52.5},
          "player_reception_yds": {"Over": 1.91, "Under": 1.91, "point": 18.5},
          "player_receptions": {"Over": 1.80, "Under": 2.02, "point": 2.5}}
    sport = dfs_sport.get(lab.SPORT_KEY)
    assert lab.project(mk, "RB", None, "shipped")["proj"] == \
        dfs_project.project(mk, sport, position="RB")["proj"]
    assert lab.project(mk, "RB", None, "pre")["proj"] < \
        lab.project(mk, "RB", None, "shipped")["proj"]
