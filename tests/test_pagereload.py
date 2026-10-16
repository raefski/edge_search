"""edge/pagereload.py: one reload gate for every page, over the whole repo.

The bugs this guards against were all "the reload covered the wrong set":
the 2026-09-24 NCAAF outage (edge.odds not reloaded), the parlay pages
reloading only edge.arb, and app.py / Pick'em reloading nothing. So the tests
pin the SET: everything under edge/, scripts/ and harness/, the fingerprint
watching the same trees, and every page calling fresh() before importing the
repo. matches() is tested instead of reloading live modules: a reload rebinds
classes (edge.odds.source.StaleOdds) and breaks other tests' pytest.raises.
"""
from __future__ import annotations

import ast
import os
import time
from pathlib import Path

from edge import pagereload as pr

ROOT = Path(__file__).resolve().parents[1]


def test_every_repo_module_is_covered():
    for name in ("edge.odds.profiles", "edge.dfs_ladder", "edge.arb.engine", "edge.repo_files",
                 "edge.nba_minutes", "edge.dfs_opt_climb", "scripts.dfs_overlay",
                 "harness.monitor", "edge"):
        assert pr.matches(name), name


def test_the_matcher_does_not_swallow_unrelated_names():
    for name in ("edgecase", "numpy", "streamlit.runtime", "scriptsx.y", "edge_other"):
        assert not pr.matches(name), name


def test_the_reloader_never_reloads_itself():
    assert not pr.matches(pr.__name__)


def test_the_fingerprint_watches_every_tree_the_reload_touches():
    globs = {g.split("/")[0] for g in pr.GLOBS}
    assert globs == set(pr.PACKAGES)
    # a file deep in a subpackage moves the fingerprint
    f = ROOT / "edge" / "odds" / "profiles.py"
    before = pr.source_fingerprint()
    st = f.stat()
    try:
        os.utime(f, (st.st_atime, time.time() + 60))
        assert pr.source_fingerprint() > before
    finally:
        os.utime(f, (st.st_atime, st.st_mtime))


def test_fingerprint_is_a_real_number_and_ignores_a_dead_glob(tmp_path):
    assert pr.source_fingerprint(("nothing/*.py",), root=tmp_path) == 0.0
    assert pr.source_fingerprint() > 0


def _first_repo_import(path: Path) -> tuple[int | None, int | None]:
    """(line of fresh(), line of the first other edge/scripts/harness import)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fresh = other = None
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) \
                and getattr(node.value.func, "id", None) == "fresh":
            fresh = fresh or node.lineno
        mod = node.module if isinstance(node, ast.ImportFrom) else None
        if mod and mod.split(".")[0] in pr.PACKAGES and mod != "edge.pagereload":
            other = other or node.lineno
    return fresh, other


def test_every_page_calls_fresh_before_importing_the_repo():
    pages = [ROOT / "app.py", *sorted((ROOT / "pages").glob("*.py"))]
    for page in pages:
        fresh, other = _first_repo_import(page)
        assert fresh, f"{page.name} never calls fresh()"
        assert other is None or fresh < other, f"{page.name} imports the repo before fresh()"


def test_fresh_reloads_once_per_source_change(monkeypatch):
    calls = []
    monkeypatch.setattr(pr, "reload_packages", lambda *a, **k: calls.append(1) or 0)
    monkeypatch.setattr(pr, "_loaded", pr.source_fingerprint())
    assert pr.fresh() is False and not calls            # nothing changed: a plain rerun
    monkeypatch.setattr(pr, "_loaded", pr._loaded - 10)  # a deploy wrote newer sources
    assert pr.fresh() is True and len(calls) == 1
    assert pr.fresh() is False and len(calls) == 1      # every later rerun / page: quiet
