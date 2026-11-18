"""edge/dfs_outside.py: other sites' projections joined to DK's pool and logged."""
from edge import dfs_outside as O


def _pool():
    return [{"name": "Michael Porter Jr.", "team": "BKN", "salary": 7400, "proj": 37.0, "game": 1},
            {"name": "Nicolas Claxton", "team": "BKN", "salary": 6000, "proj": 30.0, "game": 1},
            {"name": "Cade Cunningham", "team": "DET", "salary": 10000, "proj": 50.0, "game": 1}]


def test_join_by_name_team_and_nickname_drops_unknowns():
    rows = [{"player": "Michael Porter", "team": "BKN", "proj": 36.0},     # suffix dropped by the site
            {"player": "Nic Claxton", "team": "BKN", "proj": 29.0},        # nickname
            {"player": "Cade Cunningham", "team": "NOP", "proj": 49.0},    # wrong team: refused
            {"player": "Nobody Here", "team": "DET", "proj": 10.0}]
    got = {r["player"]: r["proj"] for r in O.join(rows, _pool())}
    assert got == {"Michael Porter Jr.": 36.0, "Nicolas Claxton": 29.0}


def test_capture_logs_each_source_and_a_failure_is_recorded_not_filled(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "PAUSE", 0)

    def boom(ctx):
        raise ValueError("blocked")
    res = O.capture(_pool(), 156831, "2026-11-18", None, path=tmp_path / "o.csv",
                    fetchers={"a": lambda ctx: [{"player": "Cade Cunningham", "team": "DET", "proj": 48.0,
                                                 "own": 30}], "b": boom})
    assert res == {"a": 1, "b": "failed: ValueError"}
    got = O.load(tmp_path / "o.csv")["156831"]
    assert list(got) == ["a"] and got["a"]["Cade Cunningham"]["own"] == "30"


def test_a_partial_source_builds_no_lineup():
    pool = _pool()
    assert O.source_lineups(pool, [{"player": "Cade Cunningham", "proj": 48.0}]) == {}
