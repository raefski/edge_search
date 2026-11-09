"""scripts/nascar_lineup_lab.py: the pure pieces (payout at the rank taken, the scorer,
DK's lineup string, the objective-percentile swap). The replay itself needs the NASCAR
feeds and the contest store: `nascar_lineup_lab.py report` / `parity`."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nascar_lineup_lab as L  # noqa: E402
from edge import dfs_nascar_theory as theory  # noqa: E402


def test_won_pays_the_rank_taken():
    k = {"pts": np.array([150.0,140.0, 130.0, 120.0]), "fee": 1.0,
         "payout": [(1, 2, 1.8)], "line": 140.0}
    assert L.won(k, 160.0) == 1.8          # rank 1
    assert L.won(k, 140.0) == 1.8          # ties to the better place: rank 2
    assert L.won(k, 135.0) == 0.0          # rank 3, unpaid


def test_score_counts_unscored_drivers_as_zero():
    L.UNSCORED.clear()
    truth = {"a": 10.0, "b": 5.0}
    lu = ["A", "B", "C", "A", "A", "A"]
    assert L.score(truth, lu) == 10.0 * 4 + 5.0
    assert sum(L.UNSCORED.values()) == 1
    assert L.score(truth, lu[:5]) is None


def test_parse_lineup_keeps_names_with_a_d():
    raw = "D Denny Hamlin D Kyle Larson D AJ Allmendinger D Daniel Suarez D Zane Smith D Todd Gilliland"
    assert L.parse_lineup(raw) == ["Denny Hamlin", "Kyle Larson", "AJ Allmendinger",
                                   "Daniel Suarez", "Zane Smith", "Todd Gilliland"]


def test_build_restores_the_objective_percentiles():
    rng = np.random.default_rng(0)
    pool = [{"name": f"d{i}", "salary": 6000 + 300 * i, "floor": float(i), "ceil": float(i)}
            for i in range(12)]
    sim = rng.normal(30, 20, (300, 12))
    before = theory.CASH_PCT, theory.GPP_PCT
    lu = L.build(pool, sim, "cash", cash_pct=50.0)
    assert len(lu) == 6
    assert (theory.CASH_PCT, theory.GPP_PCT) == before
