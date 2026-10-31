"""edge/nba_minutes_ml.py: the gradient-boosted minutes correction inside nba_minutes.project."""
from __future__ import annotations

import pytest

from edge import nba_minutes as NM, nba_minutes_ml as ML


def team(starters_known=True):
    out = []
    for k in range(13):
        role = 34 - 2.5 * k
        hist = [(max(0.0, role), k < 5)] * 20
        out.append({"id": f"p{k}", "position": "G" if k % 2 else "F", "inactive": k == 12,
                    "starter": (k < 5) if starters_known else None, "history": hist, "cur_n": 20})
    return out


def test_the_model_keeps_the_team_total_and_records_the_shipped_minutes():
    ship = NM.project(team(), {"ML": False})
    ml = NM.project(team())
    assert ML.TREES.exists() and ML.LAST_SOURCE == "ml"
    assert sum(v["minutes"] for v in ml.values()) == pytest.approx(sum(v["minutes"] for v in ship.values()), abs=1.0)
    assert all(ml[i]["ship_minutes"] == ship[i]["minutes"] for i in ship)
    assert all(0 <= v["minutes"] <= 48 for v in ml.values())


def test_without_a_five_the_starter_flags_are_unknown_to_the_model(monkeypatch):
    monkeypatch.setattr(ML, "ENABLED", False)
    ML.CAPTURE = []
    try:
        NM.project(team(starters_known=False))
        mode, F = ML.CAPTURE[-1]
    finally:
        ML.CAPTURE = None
    i_s, i_p = ML.FEATURES.index("starter"), ML.FEATURES.index("promoted")
    assert mode == "inactives"
    assert all(v[i_s] != v[i_s] and v[i_p] != v[i_p] for v in F.values())     # NaN


def test_no_model_file_means_the_shipped_minutes(monkeypatch, tmp_path):
    monkeypatch.setattr(ML, "TREES", tmp_path / "none.npz")
    monkeypatch.setattr(ML, "_MODELS", None)
    ship = NM.project(team(), {"ML": False})
    assert NM.project(team()) == ship and "no model file" in ML.LAST_SOURCE
    monkeypatch.setattr(ML, "_MODELS", None)
