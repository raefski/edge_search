"""Streamlit pages must not share cached functions, and a slate DK has not
priced must read as "not priced yet" on every sport's page, never a 403.

Both failed live on 2026-09-26. Streamlit keys st.cache_data by the function's
module, name and SOURCE, and every page runs as `__main__`, so the NASCAR and
MMA pages' identical `_slates()` shared one cache: the NASCAR page was handed
the MMA lobby and tried to build the MMA card (draft group 154125) as a race.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from edge import dfs, repo_files

ROOT = Path(__file__).resolve().parents[1]
#: The reload gate is identical on purpose: one reload per process serves
#: every page, so sharing its cache is the intended behaviour.
SHARED_OK = {"_reload_edge"}


def _cached_functions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    return [n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            and any("cache" in ast.unparse(d) for d in n.decorator_list)]


def test_no_two_pages_share_a_cached_function_name():
    scripts = [ROOT / "app.py", *sorted((ROOT / "pages").glob("*.py"))]
    seen: dict[str, Path] = {}
    for path in scripts:
        for name in _cached_functions(path):
            if name in SHARED_OK:
                continue
            assert name not in seen, (f"{name} is cached in both {seen[name].name} "
                                      f"and {path.name}; pages share Streamlit's "
                                      "cache, so give it a page-specific name")
            seen[name] = path


def test_no_salaries_and_no_snapshot_is_a_typed_error(monkeypatch):
    def refuse(url):
        raise RuntimeError("HTTP Error 403")
    monkeypatch.setattr(dfs, "_get", refuse)
    monkeypatch.setattr(repo_files, "read", lambda path, session=None: None)
    with pytest.raises(dfs.DraftablesUnavailable):
        dfs.fetch_draftables(999999)


def _group(game_type: int, games: int = 3) -> dict:
    return {"DraftGroupId": 999999, "GameTypeId": game_type,
            "ContestStartTimeSuffix": None, "GameCount": games,
            "StartDate": "2026-10-03T17:00:00.0000000Z", "DraftGroupTag": "Featured"}


def _unavailable(gid):
    raise dfs.DraftablesUnavailable("not priced")


@pytest.mark.parametrize("modname", ["edge.dfs_run_nfl", "edge.dfs_run_ncaaf"])
def test_football_reports_unpriced(monkeypatch, modname):
    import importlib
    mod = importlib.import_module(modname)
    monkeypatch.setattr(dfs, "fetch_draftables", _unavailable)
    res = mod.build_slate(None, draft_group=999999,
                          groups=[_group(mod.CLASSIC_GAME_TYPE)], persist=False)
    assert res.get("unpriced") and res.get("unpriced_reason") == "not priced"


def test_nascar_reports_unpriced(monkeypatch):
    from edge import dfs_run_nascar as N
    monkeypatch.setattr(dfs, "fetch_draftables", _unavailable)
    res = N.build_slate(draft_group=999999, groups=[_group(N.CLASSIC_GAME_TYPE, 1)],
                        persist=False)
    assert res.get("unpriced")
    assert res["stats"]["unpriced_reason"] == "not priced"


def test_mma_reports_unpriced(monkeypatch):
    from edge import dfs_run_mma as M
    monkeypatch.setattr(dfs, "_draftables_raw", _unavailable)
    res = M.build_slate(draft_group=999999, groups=[_group(M.CLASSIC_GAME_TYPE, 0)],
                        persist=False)
    assert res.get("unpriced")


def test_salary_lookup_tolerates_a_dropped_suffix():
    """2026-09-27: DK's sportsbook said "James Cook", DK DFS "James Cook III";
    seven NFL main-slate players fell out of the pool."""
    lk = dfs.SalaryLookup({dfs.norm(n): {"name": n} for n in (
        "James Cook III", "Deebo Samuel Sr.", "Josh Allen", "John Smith", "John Smith Jr.")})
    assert lk.get("James Cook")["name"] == "James Cook III"
    assert lk.get("Deebo Samuel")["name"] == "Deebo Samuel Sr."
    assert lk.get("John Smith")["name"] == "John Smith"          # exact wins
    assert lk.get("John Smith Jr.")["name"] == "John Smith Jr."
    assert lk.ambiguous == ["johnsmith"]
    assert lk.get("Nobody") is None
