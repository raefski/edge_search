"""scripts/nba_lineup_lab.py: the build cache must miss whenever the board, the variant or
the code it runs changes (a stale cached lineup would grade the wrong code)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nba_lineup_lab as L  # noqa: E402


def test_cache_key_moves_with_board_variant_and_sources(tmp_path, monkeypatch):
    ps = [{"name": "A", "proj": 30.0, "salary": 6000}]
    k = L._key(ps, "shipped")
    assert L._key(ps, "shipped") == k
    assert L._key([{**ps[0], "proj": 31.0}], "shipped") != k
    assert L._key(ps, "other") != k
    src = tmp_path / "edge"
    src.mkdir()
    for f in L.SOURCES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x = 1\n")
    monkeypatch.setattr(L, "ROOT", tmp_path)
    k1 = L._key(ps, "shipped")
    (tmp_path / L.SOURCES[0]).write_text("x = 2\n")
    assert L._key(ps, "shipped") != k1


def test_every_source_the_build_runs_is_hashed():
    import edge.dfs_nba_theory as T
    import edge.dfs_opt_nba as O
    for mod in (T, O, T.CQ, O.climb):
        rel = str(Path(mod.__file__).resolve().relative_to(L.ROOT))
        assert rel in L.SOURCES, rel


def test_lineup_sets_use_production_portfolio_settings():
    import inspect
    from edge import dfs_opt_nba
    sig = inspect.signature(dfs_opt_nba.portfolio)
    assert L.SET_ITERS == sig.parameters["iters"].default
    assert sig.parameters["max_overlap"].default == 5          # build_set passes 5 too


def test_sets_pool_one_lineup_per_job_and_match_build_set(tmp_path, monkeypatch):
    """build_sets_many flattens every (board, variant, mode, seed) into one pool and must give
    exactly build_set's lineups (seed k = the set's k-th); seed set s0 runs seeds s0..s0+n-1."""
    calls = []

    def fake_seed_lineup(pool, mode, seed):
        calls.append((pool[0]["_gid"], mode, seed))
        return [f"{mode}-{seed}"] * 8

    monkeypatch.setattr(L, "seed_lineup", fake_seed_lineup)
    monkeypatch.setattr(L, "CACHE", tmp_path)
    monkeypatch.setattr(L, "_pool", lambda fn, todo, workers=1: map(fn, todo))
    B1 = {1: [{"name": "A", "proj": 30.0, "salary": 6000, "date": "2026-11-01"}]}
    B2 = {2: [{"name": "B", "proj": 20.0, "salary": 5000, "date": "2026-11-02"}]}
    out = L.build_sets_many([(B1, "model_only"), (B2, "model_only")], n=3, seeds=(0, 1000))
    assert out[("model_only", 0)][1]["gpp"] == [["gpp-0"] * 8, ["gpp-1"] * 8, ["gpp-2"] * 8]
    assert out[("model_only", 1000)][2]["cash"][0] == ["cash-1000"] * 8
    assert len(calls) == 2 * 2 * 2 * 3                     # boards x modes x seed sets x n
    assert L.build_set(B1[1], "model_only", 1, "gpp", 3) == out[("model_only", 0)][1]["gpp"]
    calls.clear()                                          # second call: all cached
    L.build_sets_many([(B1, "model_only")], n=3, seeds=(0, 1000))
    assert calls == []


def test_pool_rows_weights_by_lineups():
    a = {("gpp", 9): {"date": "d", "n": 20, "pctile": 0.6, "cashed": 0.5, "points": 260.0, "ret": 2.0}}
    b = {("gpp", 9): {"date": "d", "n": 20, "pctile": 0.4, "cashed": 0.3, "points": 250.0, "ret": 0.0}}
    r = L.pool_rows([a, b])[("gpp", 9)]
    assert r["n"] == 40 and abs(r["pctile"] - 0.5) < 1e-12 and abs(r["points"] - 255.0) < 1e-9
