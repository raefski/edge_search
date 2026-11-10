"""scripts/mma_own_lab.py: the parts that could fake a held-out result -- fitting on
the held-out slate, pooling cash with GPP, leaving production's FPPF weight
changed after a grid search, the gate row shape."""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mma_own_lab as L  # noqa: E402
from edge import dfs_mma_theory as theory  # noqa: E402


def _slate(dg=1):
    # 12 fighters, 6 bouts (a pair-free lineup needs six bouts), all under the cap
    pool = [{"norm": f"f{i}", "name": f"F {i}", "salary": 7000 + 200 * i,
             "proj": 40.0 + 5 * i, "dk_fppf": 50.0 if i % 2 else None,
             "bout": f"b{i // 2}"} for i in range(12)]
    return L.Slate(dg, "test", pool, captain=False)


def _contest(dg, ctype, cid):
    own = {f"f{i}": (100.0 if i >= 6 else 0.0, 10.0 * i) for i in range(12)}
    return {"cid": cid, "type": ctype, "name": "x", "field": 100, "dg": dg,
            "date": "2026-11-07", "paid": 44, "own": own,
            "points": sorted(np.linspace(100, 300, 100), reverse=True)}


def test_ownership_sums_to_six_fighters_and_restores_the_fppf_weight():
    s = _slate()
    keep = theory.PUBLIC_FPPF_WEIGHT
    own, _fl, _w = s.field(0.7, 10.0)
    assert theory.PUBLIC_FPPF_WEIGHT == keep
    assert abs(own.sum() - 600.0) < 1e-6


def test_loso_never_fits_on_the_held_out_slate_and_never_pools_types(monkeypatch):
    seen = []

    def spy(train, _slates):
        seen.append(sorted({(c["dg"], c["type"]) for c in train}))
        return {"cash": (0.15, 8.0), "gpp": (0.15, 16.0)}
    monkeypatch.setitem(L.VARIANTS, "spy", spy)
    slates = {1: _slate(1), 2: _slate(2)}
    cts = [_contest(1, "cash", 10), _contest(1, "gpp", 11), _contest(2, "gpp", 12)]
    out = L.loso(cts, slates, "spy")
    assert len(out) == 3
    assert seen == [[(2, "gpp")], [(1, "cash"), (1, "gpp")]]
    assert {r["tau"] for r in out if r["type"] == "cash"} == {8.0}


def test_tau_fit_uses_only_its_own_contest_type():
    slates = {1: _slate(1)}
    p = L._tau_only([_contest(1, "gpp", 11)], slates)
    assert p["cash"] == (theory.PUBLIC_FPPF_WEIGHT, theory.FIELD_TAU_CASH)   # no cash: shipped
    assert p["gpp"][1] in L.TAUS


def test_gate_rows_have_the_gate_shape(monkeypatch, tmp_path):
    slates = {1: _slate(1)}
    monkeypatch.setattr(L, "load", lambda: ([_contest(1, "cash", 10)], slates))
    p = tmp_path / "g.jsonl"
    L.gate(str(p))
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    assert len(rows) == 12
    assert set(rows[0]) == {"key", "cluster", "split", "state", "pred", "actual"}
    assert rows[0]["split"] == "primary" and rows[0]["state"] == "cash"
