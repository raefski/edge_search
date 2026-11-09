"""scripts/nfl_lineup_lab.py: the pure pieces (payout at the rank taken, the scorer, the
replay's call into the production optimizer). The replay itself is checked by
`nfl_lineup_lab.py parity` (2026-11-09: 32 of 35 logged boards cash, 34 of 35 GPP at
iters 700), which needs the contest store and the projection log."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nfl_lineup_lab as L  # noqa: E402


def _contest():
    return {"pts": np.array([150.0, 140.0, 130.0, 120.0]), "fee": 1.0,
            "payout": [(1, 2, 1.8)], "line": 140.0}


def test_won_pays_the_rank_taken():
    k = _contest()
    assert L.won(k, 160.0) == 1.8          # rank 1
    assert L.won(k, 140.0) == 1.8          # ties to the better place: rank 2
    assert L.won(k, 135.0) == 0.0          # rank 3, unpaid


def test_score_needs_nine_and_counts_unscored_as_zero():
    L.UNSCORED.clear()
    truth = {"a": 10.0, "b": 5.0}
    lu = ["A", "B", "C"] + ["A"] * 6
    assert L.ROSTER == 9
    assert L.score(truth, lu) == 10.0 * 7 + 5.0
    assert sum(L.UNSCORED.values()) == 1
    assert L.score(truth, lu[:8]) is None


def _board():
    """Five games, enough of every position for a legal lineup under the cap (with only
    two games the random fill nearly always puts a DST against its own lineup)."""
    ps = []
    codes = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ"]
    teams = [(codes[i], codes[i ^ 1], str(i // 2)) for i in range(len(codes))]
    for t, o, g in teams:
        for pos, n, sal, proj in (("QB", 1, 6000, 18.0), ("RB", 2, 5000, 12.0),
                                  ("WR", 3, 4500, 11.0), ("TE", 1, 3500, 7.0), ("DST", 1, 2500, 6.0)):
            for i in range(n):
                name = f"{t} {pos}{i}" if pos != "DST" else f"{t} D"
                ps.append({"name": name, "team": t, "opp_team": o, "dk_pos": pos,
                           "pos": sorted(L.dfs_opt_nfl.eligible_slots(pos)), "salary": sal - 100 * i,
                           "proj": proj - i, "game": g, "own": 5.0, "leverage": 0.0,
                           "date": "2026-11-01"})
    return ps


def test_build_returns_a_legal_nine_for_both_modes():
    ps = _board()
    for mode in ("cash", "gpp"):
        lu = L.build(ps, mode, iters=30)
        assert len(lu) == 9 and len(set(lu)) == 9
        sal = {p["name"]: p["salary"] for p in ps}
        assert sum(sal[n] for n in lu) <= 50000


def test_build_is_deterministic():
    ps = _board()
    assert L.build(ps, "cash", iters=30) == L.build(ps, "cash", iters=30)
