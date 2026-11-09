"""scripts/dfs_target_scout.py: the mechanical half of picking a critique target."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dfs_target_scout as S  # noqa: E402


def test_money_reads_model_era_cash_and_gpp_only():
    card = {"roi": [{"period": "model", "sport": "NBA", "type": "cash", "roi": -0.67, "roi_lo": -0.9,
                     "roi_hi": -0.4, "entries": 17, "slates": 12, "enough_data": True},
                    {"period": "model", "sport": "NBA", "type": "all", "roi": -0.5},
                    {"period": "all", "sport": "NBA", "type": "cash", "roi": 0.1}]}
    got = S.money(card, "nba")
    assert [r["type"] for r in got] == ["cash"] and got[0]["roi"] == -0.67


def test_unused_markets_flags_a_collected_market_no_model_file_reads(monkeypatch, tmp_path):
    from edge.odds import profiles
    (tmp_path / "edge").mkdir()
    (tmp_path / "edge" / "dfs_run_nba.py").write_text('M = ["player_points"]\\n')
    monkeypatch.setattr(S, "ROOT", tmp_path)
    monkeypatch.setitem(S.MODEL_FILES, "nba", ("dfs_run_nba.py",))
    got = S.unused_markets("nba")
    assert got["collected"] and "player_points" not in got["unused"] and "player_rebounds" in got["unused"]
    assert set(got["markets"]) >= set(profiles.PROFILES["dfs_nba"].required_markets)
    assert S.unused_markets("nascar")["collected"] is False


def test_every_rotation_sport_has_model_files_and_a_harness_entry():
    from harness import critique_loop as C
    for s in C.ROTATION:
        assert s in S.MODEL_FILES and s in S.HARNESS
