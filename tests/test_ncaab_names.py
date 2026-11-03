"""College basketball names -> DraftKings: the joins that decide money (Adam, 2026-11-02).

Fixtures are read off DK's first CBB board (draft group 155565) and what the books,
RotoWire and Torvik wrote for those players that morning.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs, dfs_ladder, dfs_run_ncaab as R, ncaab_rotowire as W, ncaab_torvik as T  # noqa: E402
from edge.names import first_names_agree  # noqa: E402
from edge.ncaab_names import Audit, Rosters, is_bare_initial  # noqa: E402


def row(name, team, game=1, salary=5000, fppg=None, pos="G"):
    return {"name": name, "team": team, "game": game, "salary": salary, "dk_fppg": fppg,
            "position": pos, "matchup": "A @ B", "start": "2026-11-02T23:00:00Z"}


BOARD = {
    "trentperry": row("Trent Perry", "UCLA", 2, 7600, 22.6),
    "trentperry|KU": row("Trent Perry", "KU", 3, 7500),
    "robertwrightiii": row("Robert Wright III", "BYU", 4, 8900, 31.6),
    "johnmobleyjr": row("John Mobley Jr.", "OSU", 4, 7900, 25.4),
    "patrickngongbaii": row("Patrick Ngongba II", "DUKE", 5, 8800, 23.2),
    "treykaufmanrenn": row("Trey Kaufman-Renn", "KU", 3, 7000, 29.3),
    "joaquimboumtjeboumtje": row("Joaquim Boumtje Boumtje", "DUKE", 5, 5400),
    "raselijahgodbolt": row("Ras Elijah Godbolt", "FDU", 6, 4700, 4.4),
    "cjcox": row("C.J. Cox", "PUR", 7, 6200, 15.7),
    "rjgodfrey": row("RJ Godfrey", "ARIZ", 2, 5800, 23.0),
    "denzelaberdeen": row("Denzel Aberdeen", "UF", 8, 5800, 23.4),
    "antionewestjr": row("Antione West Jr.", "PUR", 7, 4600, 0.0),
    "maliknelson": row("Malik Nelson", "PUR", 7, 3000),
    "mikenelson": row("Mike Nelson", "PUR", 7, 3000),
    "jakedavis": row("Jake Davis", "IL", 6, 4500, 10.8),
    "cameronwilliams": row("Cameron Williams", "DUKE", 5, 9700),
    "isaiahbrown": row("Isaiah Brown", "UF", 8, 3000, 10.9),
}


@pytest.fixture
def rosters():
    return Rosters(BOARD)


@pytest.mark.parametrize("source,teams,want,how", [
    # suffix on one side only, either way round
    ("Robert Wright", ["BYU"], "Robert Wright III", "suffix"),
    ("John Mobley", ["OSU"], "John Mobley Jr.", "suffix"),
    ("John Mobley Sr.", ["OSU"], "John Mobley Jr.", "suffix"),
    ("Patrick Ngongba", ["DUKE"], "Patrick Ngongba II", "suffix"),
    # nicknames
    ("Rob Wright", ["BYU"], "Robert Wright III", "nickname"),
    ("Bobby Wright", ["BYU"], "Robert Wright III", "nickname"),
    ("Trey Wright", ["BYU"], "Robert Wright III", "nickname"),
    # compound surnames and first names
    ("Trey Kaufman", ["KU"], "Trey Kaufman-Renn", "partial"),
    ("Trey Kaufman Renn", ["KU"], "Trey Kaufman-Renn", "exact"),
    ("Joaquim Boumtje-Boumtje", ["DUKE"], "Joaquim Boumtje Boumtje", "exact"),
    ("Elijah Godbolt", ["FDU"], "Ras Elijah Godbolt", "partial"),
    # punctuation and order
    ("CJ Cox", ["PUR"], "C.J. Cox", "exact"),
    ("C. J. Cox", ["PUR"], "C.J. Cox", "exact"),
    ("Cox, C.J.", ["PUR"], "C.J. Cox", "exact"),
    ("R.J. Godfrey", ["ARIZ"], "RJ Godfrey", "exact"),
    # a tag narrows to the team named
    ("Trent Perry (KU)", ["UCLA", "KU"], "Trent Perry", "exact"),
    # a spelling slip on a long first name
    ("Antoine West", ["PUR"], "Antione West Jr.", "nickname"),
])
def test_resolves(rosters, source, teams, want, how):
    info, got = rosters.resolve(source, teams)
    assert info is not None, got
    assert info["name"] == want and got == how


def test_two_trent_perrys_stay_two_players(rosters):
    ucla, _ = rosters.resolve("Trent Perry", ["UCLA"])
    ku, _ = rosters.resolve("Trent Perry", ["KU"])
    assert ucla["salary"] == 7600 and ku["salary"] == 7500
    assert rosters.key(ucla) != rosters.key(ku)
    # asked across both teams with no tag, it is refused rather than guessed
    assert rosters.resolve("Trent Perry", ["UCLA", "KU"]) == (None, "ambiguous")


@pytest.mark.parametrize("source", ["D. Aberdeen", "D Aberdeen", "Aberdeen, D."])
def test_first_initial_is_never_a_first_name(rosters, source):
    assert is_bare_initial(source)
    assert rosters.resolve(source, ["UF"]) == (None, "initial")


@pytest.mark.parametrize("source,teams", [
    ("Mitch Nelson", ["PUR"]),          # no such first name on the team: a surname is not enough
    ("Ryan Godfrey", ["ARIZ"]),         # RJ is not the initials of Ryan: listed, not guessed
    ("Trent Perry", ["DUKE"]),          # right name, wrong team
    ("Denzel Aberdeen", ["KU"]),
])
def test_not_guessed(rosters, source, teams):
    info, how = rosters.resolve(source, teams)
    assert info is None and how == "unmatched"


def test_same_initial_does_not_make_the_same_man(rosters):
    assert not first_names_agree("Mike", "Malik")
    assert not first_names_agree("M", "Malik")
    assert not first_names_agree("M.", "Mike")
    info, _ = rosters.resolve("Mike Nelson", ["PUR"])
    assert info["name"] == "Mike Nelson"


def test_audit_lists_what_was_not_exact(rosters):
    a = Audit("x")
    for name in ("Robert Wright", "Rob Wright", "D. Aberdeen"):
        info, how = rosters.resolve(name, ["BYU", "UF"])
        a.add(name, info, how)
    r = a.row()
    assert r["joined"] == 2
    assert r["loose"] == ["Rob Wright = Robert Wright III (BYU, nickname)"]
    assert r["refused"] == ["D. Aberdeen: initial"]


def test_salary_lookup_shares_the_nickname_table():
    lk = dfs.SalaryLookup({"robertwrightiii": row("Robert Wright III", "BYU"),
                           "kevinsmith": row("Kevin Smith", "A")})
    assert lk.get("Bobby Wright")["name"] == "Robert Wright III"
    assert lk.get("B. Wright") is None
    # slate-wide there is no typo rule: this is another man
    assert lk.get("Kelvin Smith") is None and lk.get("Devin Smith") is None


def test_typo_rule_is_opt_in_and_needs_the_first_letter():
    assert first_names_agree("Antoine", "Antione", typo=True)
    assert not first_names_agree("Antoine", "Antione")
    assert not first_names_agree("Devin", "Kevin", typo=True)


# ---------------------------------------------------------------------------
# Torvik: a name is checked by the numbers
# ---------------------------------------------------------------------------
def tv(name, team, pts, conf="B10", gp=30):
    return {"name": name, "team": team, "conf": conf, "gp": gp, "mpg": 25.0, "pts": pts, "reb": 4.0,
            "ast": 2.0, "stl": 1.0, "blk": 0.5, "fg3": 1.0, "tov": 2.0, "cls": "Jr", "role": "Wing G"}


def test_torvik_join_refuses_a_namesake():
    rows = [tv("Cameron Williams", "Portland", 9.0, "WCC"),      # not Duke's freshman
            tv("Jake Davis", "Illinois", 1.0), tv("Jake Davis", "Cal Poly", 5.0, "BW"),
            tv("Isaiah Brown", "Howard", 1.0, "MEAC"), tv("Isaiah Brown", "Florida", 2.0, "SEC"),
            tv("Trent Perry", "UCLA", 8.5, "B10")]
    rows[1]["pts"] = BOARD["jakedavis"]["dk_fppg"] - (T.dk_points(rows[1]) - rows[1]["pts"])
    joined, audit = T.join(BOARD, rows)
    assert "cameronwilliams" not in joined and "Cameron Williams" in audit["refused_mismatch"]
    assert joined["jakedavis"]["team"] == "Illinois" and joined["jakedavis"]["same_school"]
    assert joined["isaiahbrown"]["team"] == "Florida"
    assert joined["trentperry"]["team"] == "UCLA" and "trentperry|KU" not in joined


def test_torvik_transfer_joins_by_fingerprint():
    rows = [tv("Trey Kaufman-Renn", "Purdue", 0.0)]
    rows[0]["pts"] = BOARD["treykaufmanrenn"]["dk_fppg"] - T.dk_points(rows[0])
    joined, _ = T.join(BOARD, rows)
    assert joined["treykaufmanrenn"]["team"] == "Purdue"
    assert not joined["treykaufmanrenn"]["same_school"]


def test_torvik_csv_layout():
    cells = ["0"] * 67
    for i, v in {0: "Jake Davis", 1: "Illinois", 2: "B10", 3: "30", 12: "20", 14: "60", 17: "120",
                 19: "30", 20: "90", 25: "Jr", 54: "20.5", 57: "1", 58: "3", 59: "4", 60: "2",
                 61: "1", 62: "0.5", 63: "10", 64: "Wing F"}.items():
        cells[i] = v
    r = T.parse_csv(",".join(cells))[0]
    assert r["name"] == "Jake Davis" and r["mpg"] == 20.5 and r["fg3"] == 1.0
    # TOV% 20 on 7 FGA and 2 FTA a game: 0.2 * (7 + .88) / .8
    assert r["tov"] == pytest.approx(1.97, abs=0.01)


# ---------------------------------------------------------------------------
# RotoWire: the title, never the shortened text
# ---------------------------------------------------------------------------
PAGE = """<div class="lineup__box"><div class="lineup__teams">
<img class="lineup__logo" src="x" alt="Florida"><img class="lineup__logo" src="y" alt="Miami"></div>
<ul class="lineup__list is-visit">
<li class="lineup__player"><div class="lineup__pos">SG</div><a title="Denzel Aberdeen" href="/d">D. Aberdeen</a>
<span style="c"></span></li>
<li class="lineup__title is-middle">Injuries</li>
<li class="lineup__player"><div class="lineup__pos" style="w">G</div><a title="Boogie Fland" href="/b">Boogie Fland</a>
<span style="c">GTD</span></li></ul>
<ul class="lineup__list is-home">
<li class="lineup__player"><div class="lineup__pos">C</div><a title="Somto Cyril" href="/s">S. Cyril</a>
<span style="c"></span></li>
<li class="lineup__title is-middle">Injuries</li>
<li class="lineup__player"><div class="lineup__pos" style="w">G</div><a title="L.J. Cason" href="/l">L.J. Cason</a>
<span style="c">OFS</span></li></ul></div>"""


def test_rotowire_reads_titles_and_splits_injuries():
    rows = W.parse(PAGE)
    assert [r["team"] for r in rows] == ["Florida", "Miami"]
    assert rows[0]["starters"][0]["name"] == "Denzel Aberdeen"
    assert rows[0]["injuries"] == [{"pos": "G", "name": "Boogie Fland", "tag": "GTD"}]
    assert rows[1]["starters"][0]["name"] == "Somto Cyril"
    assert rows[1]["injuries"][0]["tag"] == "OFS"


def test_lineup_news_is_team_scoped():
    board = {"denzelaberdeen": row("Denzel Aberdeen", "UF"), "boogiefland": row("Boogie Fland", "UF"),
             "somtocyril": row("Somto Cyril", "MIA"), "ljcason": row("L.J. Cason", "MIA")}
    starters, tags, audit, source = R.lineup_news(board, Rosters(board), {"UF", "MIA"}, PAGE)
    assert source == "rotowire" and set(starters) == {"denzelaberdeen", "somtocyril"}
    assert tags == {"boogiefland": "GTD", "ljcason": "OFS"}
    assert audit.row()["refused"] == []


def test_lineup_news_failure_says_so(monkeypatch):
    def down():
        raise OSError("no route")
    monkeypatch.setattr(W, "fetch", down)
    _, _, _, source = R.lineup_news({}, Rosters({}), set())
    assert source == "unavailable (OSError)"


# ---------------------------------------------------------------------------
# Ladders, and the two Trent Perrys through the prop join
# ---------------------------------------------------------------------------
def ladder(name, rungs):
    return [{"name": "Over", "description": name, "point": k - 0.5, "price": d} for k, d in rungs]


HAUGH = [(8, 1.05), (10, 1.125), (12, 1.263), (15, 1.645), (16, 1.833), (18, 2.4), (20, 3.4),
         (25, 9.5), (30, 31.0)]


def test_gapped_ladder_mean_is_near_the_even_money_rung():
    rungs = dfs_ladder.rungs_from_outcomes(ladder("Thomas Haugh", HAUGH), "Thomas Haugh")
    assert rungs[0] == (8.0, 1.05)
    mu = dfs_ladder.mean_gapped(rungs, dfs_ladder.HOOPS_OVERROUND["player_points"])
    assert 14.5 < mu < 17.0


def test_gapped_mean_counts_the_gaps_mean_count_skips():
    rungs = dfs_ladder.rungs_from_outcomes(ladder("X", HAUGH), "X")
    v = dfs_ladder.HOOPS_OVERROUND["player_points"]
    assert dfs_ladder.mean_gapped(rungs, v) > dfs_ladder.mean_count(rungs, v)["mean"] + 4


def test_one_rung_is_no_ladder():
    assert dfs_ladder.mean_gapped([(10.0, 2.0)], 0.07) is None
    assert R.market_mean("player_points", "pts", ladder("X", [(10, 2.0)]), "X") is None


def test_two_sided_line_wins_over_rungs():
    out = ladder("X", HAUGH) + [{"name": "Over", "description": "X", "point": 21.5, "price": 1.91},
                                {"name": "Under", "description": "X", "point": 21.5, "price": 1.91}]
    assert R.market_mean("player_points", "pts", out, "X") == pytest.approx(21.5, abs=0.6)


class FakeProps:
    def __init__(self, events):
        self.events = events

    def get_events(self, sport):
        return [{k: e[k] for k in ("id", "commence_time", "home_team", "away_team")} for e in self.events]

    def get_event_odds(self, sport, ev_id, markets, regions):
        e = next(x for x in self.events if x["id"] == ev_id)
        return {"id": ev_id, "bookmakers": [{"key": "draftkings", "markets": [
            {"key": "player_points", "outcomes": sum((ladder(n, HAUGH) for n in e["subjects"]), [])}]}]}


def test_props_go_to_the_right_namesake():
    board = {"trentperry": row("Trent Perry", "UCLA", 2, 7600), "kyledaniels": row("Kyle Daniels", "ARIZ", 2),
             "trentperry|KU": row("Trent Perry", "KU", 3, 7500), "jimsmith": row("Jim Smith", "FORD", 3)}
    games = {2: {"home": "UCLA", "away": "ARIZ", "start": R._parse_time("2026-11-03T03:00:00Z"), "matchup": "ARIZ @ UCLA"},
             3: {"home": "KU", "away": "FORD", "start": R._parse_time("2026-11-03T01:00:00Z"), "matchup": "FORD @ KU"}}
    client = FakeProps([
        {"id": "a", "commence_time": "2026-11-03T03:00:00+00:00", "home_team": "UCLA", "away_team": "Arizona",
         "subjects": ["Trent Perry", "Kyle Daniels"]},
        # Same tip window, a different school's namesake: must not touch either man.
        {"id": "b", "commence_time": "2026-11-03T03:00:00+00:00", "home_team": "Oregon", "away_team": "Montana",
         "subjects": ["Trent Perry", "Some Duck", "Other Duck"]},
    ])
    rosters = Rosters(board)
    audit = Audit("props")
    props, _, _ = R.player_props(client, rosters, games, board, audit)
    assert "trentperry" in props and "trentperry|KU" not in props
    assert len(props["trentperry"]["pts"]) == 1            # the Oregon line was not averaged in


# ---------------------------------------------------------------------------
# No-prop projections
# ---------------------------------------------------------------------------
def test_low_major_star_is_not_carried_at_his_old_number():
    star = tv("Dennis Parker Jr.", "Radford", 0.0, "BSth")
    star["pts"] = 31.5 - T.dk_points(star)
    star["same_school"] = False
    # Starting at Kansas at $3,500: moved up, so the carryover says about 20, not 31.5.
    fp, src = R.no_prop_points(3500, star, 31.5, True, 2, 72.5)
    assert src == "blend" and 15 < fp < 22


def test_freshman_gets_the_salary_line_not_the_old_pro_rule():
    fp, src = R.no_prop_points(10300, None, None, True, 2, 83.5)
    assert src == "salary"
    assert fp == pytest.approx(R.SALARY_TIER_LINE[0] + R.SALARY_TIER_LINE[1] * 10.3)
    assert fp < 35        # 4.6 per $1K said 47


def test_cheap_nonstarter_gets_no_salary_credit():
    assert R.no_prop_points(3000, None, None, False, 2, 72.5) == (R.BENCH_POINTS, "bench")
    walkon = tv("W", "Duke", 0.5)
    walkon["same_school"] = True
    fp, src = R.no_prop_points(3000, walkon, 1.0, False, 2, 72.5)
    assert src == "bench" and fp < 10


def test_unlisted_team_is_not_a_bench_full_of_starters():
    """RotoWire named no Memphis starters on 2026-11-02; books priced its $3,700 forward at 23."""
    weeks = tv("Tylik Weeks", "Southern Miss", 0.0, "SB")
    weeks["pts"] = 31.4 - T.dk_points(weeks)
    weeks["same_school"] = False
    listed = R.no_prop_points(3700, weeks, 31.4, False, 1, 70.5, team_listed=True)
    unlisted = R.no_prop_points(3700, weeks, 31.4, False, 1, 70.5, team_listed=False)
    assert listed[1] == "bench" and unlisted[1] == "blend" and unlisted[0] > listed[0] + 3


def test_only_the_bench_fits_the_budget():
    rows = [{"team": "A", "source": "props", "fp": 100.0}, {"team": "A", "source": "blend", "fp": 40.0},
            {"team": "A", "source": "bench", "fp": 40.0}]
    implied = {"A": 75.0}                                  # budget 153: 13 left for a bench of 40
    scales = R.apply_team_budget(rows, implied)
    assert rows[0]["fp"] == 100.0 and rows[1]["fp"] == 40.0
    assert rows[2]["fp"] == pytest.approx(40.0 * max(13.0 / 40.0, R.BENCH_SCALE_FLOOR))
    assert scales["A"] == pytest.approx(max(13.0 / 40.0, R.BENCH_SCALE_FLOOR), abs=0.01)


def test_a_shortfall_is_not_handed_to_known_players():
    rows = [{"team": "A", "source": "blend", "fp": 20.0}, {"team": "A", "source": "bench", "fp": 5.0}]
    R.apply_team_budget(rows, {"A": 75.0})
    assert rows[0]["fp"] == 20.0 and rows[1]["fp"] == 5.0


def test_main_line_is_the_one_near_even_money():
    outs = [{"name": "Over", "point": 140.5, "price": 1.3}, {"name": "Under", "point": 140.5, "price": 3.4},
            {"name": "Over", "point": 148.5, "price": 1.91}, {"name": "Under", "point": 148.5, "price": 1.91},
            {"name": "Over", "point": 160.5, "price": 4.0}, {"name": "Under", "point": 160.5, "price": 1.2}]
    assert R._main_line(outs, "Over", "Under") == 148.5
    spread = [{"name": "Duke", "point": -30.5, "price": 2.5}, {"name": "Army", "point": 30.5, "price": 1.5},
              {"name": "Duke", "point": -38.5, "price": 1.91}, {"name": "Army", "point": 38.5, "price": 1.91}]
    assert R._main_line(spread, "Duke", "Army") == -38.5
