"""edge/nba_courtiq.py: with/without splits and who starts at each slot."""
from __future__ import annotations

from edge import nba_courtiq as C


def game(d, five, played):
    return [d, d, five, played]


def table():
    # C "c" plays games 1-2, misses 3-4: the PF "pf" slides to C, "w" starts at PF,
    # bench big "b" gains minutes
    base = {"pg": "PG", "sg": "SG", "sf": "SF"}
    g = []
    for d in ("2026-10-21", "2026-10-22"):
        g.append(game(d, {**base, "pf": "PF", "c": "C"},
                      {"c": [32, 40, 10, 2, 2], "pf": [30, 30, 7, 2, 1], "b": [10, 8, 3, 0, 1], "w": [20, 15, 3, 2, 0]}))
    for d in ("2026-10-24", "2026-10-26"):
        g.append(game(d, {**base, "pf": "C", "w": "PF"},
                      {"pf": [34, 38, 11, 2, 2], "b": [22, 24, 8, 1, 2], "w": [30, 22, 5, 2, 0]}))
    return {"names": {k: k.upper() for k in ("pg", "sg", "sf", "pf", "c", "b", "w")}, "teams": {"HOU": g}}


def test_the_pf_slides_to_c_and_the_bench_big_gains_minutes():
    s = C.split(table(), "HOU", ["c"])
    assert (len(s["with"]), len(s["without"])) == (2, 2)
    assert s["slots"]["with"]["C"] == [("c", 1.0)]
    assert s["slots"]["without"]["C"] == [("pf", 1.0)]
    assert s["slots"]["without"]["PF"] == [("w", 1.0)]
    m = {r["pid"]: r for r in s["mates"]}
    assert m["b"]["dmin"] == 12 and m["b"]["without"]["slot"] == ""
    assert m["w"]["without"]["start"] == 1.0 and m["w"]["with"]["start"] == 0.0


def test_a_player_never_on_the_team_has_no_split():
    assert C.split(table(), "HOU", ["nobody"])["mates"] == []
