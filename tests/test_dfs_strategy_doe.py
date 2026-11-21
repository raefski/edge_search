"""dfs_strategy_doe replays the pool transform production actually ran (critique cycle 38)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dfs_strategy_doe as D  # noqa: E402


class _Fake(D.NBA):
    def __init__(self):
        pass


def test_pre_blend_board_replays_model_only(monkeypatch):
    monkeypatch.setattr(D, "LODO", {"2026-11-01": "lodo_x", "2026-11-12": "lodo_y"})
    monkeypatch.setattr(D, "FORWARD", False)
    monkeypatch.setattr(D, "BACKDATE", False)
    a = _Fake()
    assert a.variant([{"date": "2026-11-01", "mkt": None}]) == "model_only"
    assert a.variant([{"date": "2026-11-12", "mkt": None}, {"date": "2026-11-12", "mkt": 30.0}]) == "lodo_y"
    monkeypatch.setattr(D, "BACKDATE", True)
    assert a.variant([{"date": "2026-11-01", "mkt": None}]) == "lodo_x"
    monkeypatch.setattr(D, "FORWARD", True)
    assert a.variant([{"date": "2026-11-01", "mkt": None}]) == "shipped"
