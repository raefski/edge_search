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


def _college_pool():
    return [{"name": "Darius Bivins", "team": "STMRY", "salary": 6300, "game": 1},
            {"name": "Mantas Juzenas", "team": "STMRY", "salary": 6700, "game": 1},
            {"name": "Jalen Johnson", "team": "TEX", "salary": 4000, "game": 2},
            {"name": "Jalen Johnson", "team": "UNC", "salary": 4000, "game": 3},
            {"name": "Michael Porter Jr.", "team": "MIZ", "salary": 5100, "game": 4}]


def test_college_join_by_name_and_salary_not_team_codes():
    rows = [{"player": "Darius Bivins", "team": "SMC", "salary": 6300, "proj": 34.8},    # site's own school code
            {"player": "Mantas Juzenas", "team": "STMRY", "salary": "6700", "proj": 22.0},
            {"player": "Jalen Johnson", "team": "TEX", "salary": 4000, "proj": 15.0},    # two on the slate at $4,000: refused
            {"player": "Mike Porter", "team": "MIZ", "salary": 5100, "proj": 20.0},      # nickname + Jr. dropped
            {"player": "Darius Bivins", "team": "STMRY", "salary": 3000, "proj": 5.0},  # wrong salary: refused
            {"player": "Jayln Collingwood", "team": "MISSST", "salary": 3300, "proj": 7.7},  # site typo
            {"player": "Isaiah Collingwood", "team": "TEX", "salary": 3300, "proj": 9.0}]    # other initial: refused
    pool = _college_pool() + [{"name": "Jalyn Collingwood", "team": "MSST", "salary": 3300, "game": 5}]
    got = {(r["player"], r["team"]): r["proj"] for r in O.join_by_salary(rows, pool)}
    assert got == {("Darius Bivins", "STMRY"): 34.8, ("Mantas Juzenas", "STMRY"): 22.0,
                   ("Michael Porter Jr.", "MIZ"): 20.0, ("Jalyn Collingwood", "MSST"): 7.7}


def test_capture_ncaab_counts_rows_that_join_nobody(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "PAUSE", 0)
    res = O.capture_ncaab(_college_pool(), 157300, "2026-11-27", None, path=tmp_path / "c.csv",
                          fetchers={"rotowire": lambda ctx: [
                              {"player": "Darius Bivins", "team": "SMC", "salary": 6300, "proj": 34.8},
                              {"player": "Not On Slate", "team": "X", "salary": 9000, "proj": 30.0}]})
    assert res == {"rotowire": 1, "rotowire_unjoined": 1}
    assert O.load(tmp_path / "c.csv")["157300"]["rotowire"]["Darius Bivins"]["team"] == "STMRY"
