"""scripts/ncaaf_lineup_lab.py: the pure pieces (payout at the rank taken, the scorer,
the cycle-7 calibration). The replay itself is checked by `ncaaf_lineup_lab.py parity`
(29 of 29 logged boards, cash and GPP, 2026-11-09), which needs the contest store."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ncaaf_lineup_lab as L  # noqa: E402


def _contest():
    return {"pts": np.array([150.0, 140.0, 130.0, 120.0]), "fee": 1.0,
            "payout": [(1, 2, 1.8)], "line": 140.0}


def test_won_pays_the_rank_taken():
    k = _contest()
    assert L.won(k, 160.0) == 1.8          # rank 1
    assert L.won(k, 140.0) == 1.8          # ties to the better place: rank 2
    assert L.won(k, 135.0) == 0.0          # rank 3, unpaid


def test_score_counts_unscored_players_as_zero():
    L.UNSCORED.clear()
    truth = {"a": 10.0, "b": 5.0}
    lu = ["A", "B", "C", "A", "A", "A", "A", "A"]
    assert L.score(truth, lu) == 10.0 * 6 + 5.0
    assert sum(L.UNSCORED.values()) == 1
    assert L.score(truth, lu[:7]) is None


def test_calibration_recovers_a_salary_slope():
    rng = np.random.default_rng(0)
    data = []
    for pos, (a, b, c) in {"QB": (-10.0, 1.1, 1.5), "RB": (-1.0, 1.0, 0.35), "WR": (0.0, 0.75, 0.75)}.items():
        for _ in range(200):
            proj, sal = rng.uniform(5, 25), rng.uniform(3, 10)
            data.append(("2026-10-01", pos, proj, sal, a + b * proj + c * sal))
    co = L.fit_calibration(data)
    assert np.allclose(co["QB"], [-10.0, 1.1, 1.5], atol=1e-6)
    out = L.calibrate([{"name": "x", "dk_pos": "QB", "proj": 20.0, "salary": 8000}], co)
    assert abs(out[0]["proj"] - (-10.0 + 22.0 + 12.0)) < 0.01


def test_calibration_leaves_thin_positions_alone():
    co = L.fit_calibration([("d", "QB", 10.0, 5.0, 12.0)] * (L.CAL_MIN_ROWS - 1))
    assert "QB" not in co
    p = {"name": "x", "dk_pos": "QB", "proj": 17.3, "salary": 7000}
    assert L.calibrate([p], co)[0]["proj"] == 17.3
