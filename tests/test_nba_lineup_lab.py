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
