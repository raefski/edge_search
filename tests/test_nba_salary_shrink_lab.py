"""scripts/nba_salary_shrink_lab.py: DK scoring and the replay's lineup builder."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nba_salary_shrink_lab as L  # noqa: E402


def test_dk_points_with_a_double_double():
    r = {"PTS": 20, "FG3M": 2, "REB": 10, "AST": 5, "STL": 1, "BLK": 0, "TOV": 3}
    assert L.dk(r) == 20 + 1 + 12.5 + 7.5 + 2 - 1.5 + 1.5


def test_optimizer_fills_every_slot_legally_under_the_cap():
    pos = ["PG", "SG", "SF", "PF", "C", "PG/SG", "SF/PF", "C", "PG", "SF"]
    pool = [{"pos": p, "salary": 5000 + 300 * i, "team": "A" if i % 2 else "B", "opp": "B" if i % 2 else "A"}
            for i, p in enumerate(pos)]
    pool += [{"pos": "PF", "salary": 4000, "team": "C", "opp": "D"}]
    pick = L.optimize(pool, np.arange(len(pool), dtype=float))
    assert pick is not None and len(set(pick)) == 8
    assert sum(pool[i]["salary"] for i in pick) <= L.CAP


def test_box_truth_joins_a_first_name_dk_spells_differently(monkeypatch):
    """Cycle 26: DK 'Alexandre Sarr' = NBA.com 'Alex Sarr' scored 0 in the labs."""
    monkeypatch.setattr(L, "_BOX", {})
    rows = [{"GAME_DATE": "2026-11-07", "PLAYER_NAME": "Alex Sarr", "PTS": 20, "FG3M": 0, "REB": 10,
             "AST": 0, "STL": 0, "BLK": 0, "TOV": 0}]
    monkeypatch.setattr(L.BOX.__class__, "read_text", lambda self: __import__("json").dumps(rows))
    assert L.box_lookup("2026-11-07", "Alexandre Sarr") == 20 + 12.5 + 1.5
    assert L.box_lookup("2026-11-07", "Alex Smith") is None
