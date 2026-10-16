"""DK College Basketball DFS (edge/dfs_run_ncaab.py): scoring, market
inversion, the game simulator's correlation structure, slot eligibility and a
whole slate built end to end through build_slate on a synthetic board (fake
DK draftables, fake odds client) -- no network. Ported 2026-10-16 from the
basketball web session's tests; its NBA-engine tests went with that engine.
"""
from __future__ import annotations

import random

import numpy as np
import pytest

from edge import (dfs, dfs_ncaab_theory as theory, dfs_opt_ncaab as OC, dfs_run_ncaab as RC,
                  nba, ncaab, ncaab_sim)


# ---------------------------------------------------------------------------
# scoring (one DK basketball table for both leagues: edge/nba.actual_points)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("box", [
    {"PTS": 27, "REB": 7, "AST": 7, "STL": 1, "BLK": 0, "TOV": 3, "FG3M": 3},
    {"PTS": 12, "REB": 11, "AST": 2, "STL": 0, "BLK": 2, "TOV": 1, "FG3M": 0},     # DD
    {"PTS": 22, "REB": 10, "AST": 12, "STL": 2, "BLK": 1, "TOV": 4, "FG3M": 1},    # TD
    {"PTS": 10, "REB": 2, "AST": 1, "STL": 10, "BLK": 0, "TOV": 0, "FG3M": 0},     # pts+stl DD
])
def test_vectorised_scoring_matches_the_reference(box):
    want = nba.actual_points(box)
    a = lambda k: np.array([float(box[k])])                  # noqa: E731
    got = ncaab_sim.dk_points(a("PTS"), a("REB"), a("AST"), a("FG3M"), a("STL"), a("BLK"),
                            a("TOV"))[0]
    assert got == pytest.approx(want)


def test_triple_double_does_not_stack_with_double_double():
    box = {"PTS": 10, "REB": 10, "AST": 10}
    assert nba.actual_points(box) == pytest.approx(10 + 12.5 + 15 + 3.0)


# ---------------------------------------------------------------------------
# positions and team names
# ---------------------------------------------------------------------------
def test_nba_slots():
    assert nba.slots_for("PG/SG") == {"PG", "SG", "G", "UTIL"}
    assert nba.slots_for("PF/C") == {"PF", "C", "F", "UTIL"}
    assert nba.slots_for("C") == {"C", "UTIL"}
    assert nba.slots_for("") == set()


def test_ncaab_slots_have_no_centre():
    assert ncaab.slots_for("G") == {"G", "UTIL"}
    assert ncaab.slots_for("F/C") == {"F", "UTIL"}
    assert ncaab.slots_for("G/F") == {"G", "F", "UTIL"}


def test_dk_team_aliases():
    assert nba.team("GS") == "GSW" and nba.team("NO") == "NOP" and nba.team("PHO") == "PHX"
    assert nba.team_from_name("Golden State Warriors") == "GSW"
    assert nba.team_from_name("LA Clippers") == "LAC"


@pytest.mark.parametrize("code,name,ok", [
    ("DUKE", "Duke Blue Devils", True), ("UNC", "North Carolina Tar Heels", True),
    ("KU", "Kansas Jayhawks", True), ("GONZ", "Gonzaga Bulldogs", True),
    ("DUKE", "North Carolina Tar Heels", False), ("UK", "Kansas Jayhawks", False),
])
def test_college_team_matching(code, name, ok):
    assert (ncaab.match_team(code, name) >= 0.6) is ok


# ---------------------------------------------------------------------------
# market -> means
# ---------------------------------------------------------------------------
def test_even_money_line_inverts_to_the_line():
    for stat, line in (("pts", 24.5), ("reb", 9.5), ("ast", 6.5), ("fg3", 2.5), ("stl", 1.5)):
        mu = ncaab_sim.mean_from_market(stat, {"Over": 1.909, "Under": 1.909, "point": line})
        assert abs(mu - line) < (0.35 if stat in ("pts", "reb", "ast") else 0.6), stat


def test_a_juiced_over_moves_the_mean_up():
    even = ncaab_sim.mean_from_market("pts", {"Over": 1.91, "Under": 1.91, "point": 24.5})
    juiced = ncaab_sim.mean_from_market("pts", {"Over": 1.6, "Under": 2.35, "point": 24.5})
    assert juiced > even + 1.0
    assert ncaab_sim.mean_from_market("pts", {"Over": 1.9, "point": 24.5}) is None


def test_combos_fill_missing_stats():
    m, imp = RC.means_from_props({"pts": [25.0], "pr": [33.0], "pra": [40.0]},
                                RC.PROFILES["F"])
    assert m["reb"] == pytest.approx(8.0) and m["ast"] == pytest.approx(7.0)
    assert "reb" not in imp and "ast" not in imp and "stl" in imp
    assert RC.means_from_props({"reb": [9.0]}, RC.PROFILES["F"]) == (None, [])


def test_means_from_total_hits_the_total():
    m = RC.means_from_total(42.0, RC.PROFILES["G"])
    assert ncaab_sim.fantasy_mean(m) == pytest.approx(42.0, abs=0.05)


# ---------------------------------------------------------------------------
# the simulator
# ---------------------------------------------------------------------------
STAR = {"pts": 27, "reb": 7, "ast": 7, "fg3": 2.8, "stl": 1.1, "blk": 0.6, "tov": 3.2}
ROLE = {"pts": 9, "reb": 3.5, "ast": 2, "fg3": 1.6, "stl": 0.8, "blk": 0.3, "tov": 0.9}


def _two_games():
    pool = []
    for t in ("DUKE", "UNC", "KU", "BAY"):
        for k, m in enumerate((STAR, ROLE, ROLE, ROLE, ROLE, ROLE)):
            pool.append({"name": f"{t}{k}", "team": t, "means": dict(m)})
    games = {"g1": {"home": "DUKE", "away": "UNC", "pts_home": 75, "pts_away": 71},
             "g2": {"home": "KU", "away": "BAY", "pts_home": 66, "pts_away": 75}}
    return pool, games


def test_simulated_means_equal_market_means():
    pool, games = _two_games()
    sim = ncaab_sim.simulate(pool, games, n_sims=20000, seed=3)
    for i, p in enumerate(pool):
        assert sim[:, i].mean() == pytest.approx(ncaab_sim.fantasy_mean(p["means"]), rel=0.04)


def test_same_game_correlates_and_different_games_do_not():
    pool, games = _two_games()
    sim = ncaab_sim.simulate(pool, games, n_sims=20000, seed=4)
    c = np.corrcoef(sim.T)
    bos, nyk, lal = 0, 6, 12
    assert c[bos, nyk] > 0.02                       # shared pace
    assert abs(c[bos, lal]) < 0.03                  # independent games


def test_blowouts_cost_starters_their_minutes():
    """The favourite's star in a lopsided game has a thinner upper tail than
    the same star in a pick'em, at the same mean."""
    pool, _ = _two_games()
    even = {"g1": {"home": "DUKE", "away": "UNC", "pts_home": 72, "pts_away": 72},
            "g2": {"home": "KU", "away": "BAY", "pts_home": 72, "pts_away": 72}}
    lop = {"g1": {"home": "DUKE", "away": "UNC", "pts_home": 80, "pts_away": 65},
           "g2": even["g2"]}
    a = ncaab_sim.simulate(pool, even, n_sims=20000, seed=5)[:, 0]
    b = ncaab_sim.simulate(pool, lop, n_sims=20000, seed=5)[:, 0]
    assert np.percentile(b, 95) < np.percentile(a, 95)


def test_double_double_priced_from_the_joint_distribution():
    big = {"name": "C", "team": "DUKE",
           "means": {"pts": 11.0, "reb": 9.5, "ast": 2, "fg3": 0, "stl": 0.6, "blk": 1.4, "tov": 1.4}}
    sim, box = ncaab_sim.simulate([big], {}, n_sims=20000, seed=6, return_box=True)
    dd = ((box["pts"][:, 0] >= 10) & (box["reb"][:, 0] >= 10)).mean()
    assert 0.25 < dd < 0.6
    assert sim[:, 0].mean() > ncaab_sim.fantasy_mean(big["means"]) + 0.3


# ---------------------------------------------------------------------------
# synthetic slate
# ---------------------------------------------------------------------------
NBA_TEAMS = [("BOS", "NY"), ("LAL", "GS"), ("DEN", "PHO")]      # (home, away), DK codes
BOOK_NAME = {"BOS": "Boston Celtics", "NY": "New York Knicks", "LAL": "Los Angeles Lakers",
             "GS": "Golden State Warriors", "DEN": "Denver Nuggets", "PHO": "Phoenix Suns"}
NBA_POS = ["PG", "SG", "SF", "PF", "C", "PG/SG", "SF/PF", "PF/C", "SG"]


def _salaries(games, positions, prefix):
    rng = random.Random(1)
    out = {}
    for g, (home, away) in enumerate(games):
        start = f"2099-01-0{1 + g // 2}T0{0 + g}:00:00.0000000Z"
        for team in (home, away):
            for k, pos in enumerate(positions):
                name = f"{prefix} {team} Player{k}"
                sal = max(3000, 11000 - 900 * k + rng.randrange(-400, 400, 100))
                fppg = 52.0 - 5.0 * k if k < 8 else None
                out[dfs.norm(name)] = {
                    "name": name, "salary": sal, "position": pos, "team": team,
                    "game": 1000 + g, "matchup": f"{away} @ {home}", "start": start,
                    "dk_fppg": fppg, "player_id": None,
                    "dk_status": "OUT" if (team == home and k == 3 and g == 0) else
                                 ("Q" if (team == away and k == 2 and g == 1) else None)}
    return out


class FakeClient:
    """The scraped client's interface: events, per-event player props, lines."""

    def __init__(self, salaries, games, names, props_per_team=7, book_team=True):
        self.salaries, self.games, self.names = salaries, games, names
        self.k, self.book_team = props_per_team, book_team
        self.age_seconds = 120

    def _events(self):
        return [{"id": f"ev{g}", "home_team": self.names[h], "away_team": self.names[a],
                 "commence_time": f"2099-01-0{1 + g // 2}T0{g}:00:00Z"}
                for g, (h, a) in enumerate(self.games)]

    def get_events(self, sport):
        return self._events()

    def get_event_odds(self, sport, event_id, markets, regions):
        g = int(event_id[2:])
        home, away = self.games[g]
        outs = {"player_points": [], "player_rebounds": [], "player_assists": []}
        for info in self.salaries.values():
            if info["team"] not in (home, away):
                continue
            k = int(info["name"].rsplit("Player", 1)[1])
            if k >= self.k:
                continue
            subject = info["name"] + (f" ({info['team']})" if not self.book_team else "")
            for mk, line in (("player_points", 26 - 2.5 * k), ("player_rebounds", 4 + (k % 4)),
                             ("player_assists", 6 - 0.6 * k)):
                outs[mk] += [{"name": "Over", "description": subject, "price": 1.91,
                              "point": line},
                             {"name": "Under", "description": subject, "price": 1.91,
                              "point": line}]
        return {"bookmakers": [{"key": "draftkings", "markets": [
            {"key": k, "outcomes": v} for k, v in outs.items()]}]}

    def get_featured_odds(self, sport, markets, regions):
        evs = []
        for g, ev in enumerate(self._events()):
            evs.append(dict(ev, bookmakers=[{"key": "draftkings", "markets": [
                {"key": "totals", "outcomes": [{"name": "Over", "price": 1.91, "point": 226.5 + 4 * g},
                                               {"name": "Under", "price": 1.91, "point": 226.5 + 4 * g}]},
                {"key": "spreads", "outcomes": [{"name": ev["home_team"], "price": 1.91, "point": -4.5},
                                                {"name": ev["away_team"], "price": 1.91, "point": 4.5}]},
            ]}]))
        return evs


def _groups(gtype):
    return [{"DraftGroupId": 555, "GameTypeId": gtype, "GameCount": 3,
             "ContestStartTimeSuffix": None, "StartDate": "2099-01-01T00:00:00.0000000Z"},
            {"DraftGroupId": 556, "GameTypeId": 81, "GameCount": 1,
             "ContestStartTimeSuffix": " (BOS vs NY)", "StartDate": "2099-01-01T00:00:00Z"}]


CBB_GAMES = [("DUKE", "UNC"), ("KU", "BAY"), ("GONZ", "ZAGA2")]
CBB_NAME = {"DUKE": "Duke Blue Devils", "UNC": "North Carolina Tar Heels",
            "KU": "Kansas Jayhawks", "BAY": "Baylor Bears", "GONZ": "Gonzaga Bulldogs",
            "ZAGA2": "Some Other Team"}
CBB_POS = ["G", "G", "G/F", "F", "F", "F/C", "G", "F", "G"]


def test_ncaab_slate_end_to_end(monkeypatch):
    sal = _salaries(CBB_GAMES, CBB_POS, "Cbb")
    # no FPPG at all for the third game: early season -> salary fallback
    for info in sal.values():
        if info["game"] == 1002:
            info["dk_fppg"] = None
    monkeypatch.setattr(dfs, "fetch_draftables", lambda gid: sal)
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES",
                        {"CBB": [{"GameTypeId": 5, "Name": "Classic", "DraftType": "SalaryCap"}]})
    client = FakeClient(sal, CBB_GAMES, CBB_NAME, props_per_team=2, book_team=False)
    res = RC.build_slate(client=client, groups=_groups(5), n_sims=1000, iters=20, persist=False)
    assert res["gid"] == 555 and not res.get("error"), res.get("error")
    info = res["stats"]
    # Duke-UNC and Kansas-Baylor matched by name; the third game's away side
    # matches nothing -- it still matches on player votes (props subjects
    # carry "(TEAM)" and resolve to DK names)
    assert "UNC @ DUKE" not in info["missing_lines"] and "BAY @ KU" not in info["missing_lines"]
    assert info["sources"]["props"] and info["sources"]["fppg"] and info["sources"]["salary"]
    for mode in ("cash", "gpp"):
        lu = res[mode]
        assert lu and OC.valid(res["pool"], lu["idx"])
        assert [s for s, _ in lu["slots"]] == list(ncaab.SLOTS)


def test_ncaab_without_lobby_types_falls_back_to_multigame_groups(monkeypatch):
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES", {})
    rows = RC.classic_groups(_groups(5))
    assert [r["gid"] for r in rows] == [555] and rows[0]["unverified_type"]


def test_unpriced_slate_is_not_a_crash(monkeypatch):
    def refuse(gid):
        raise dfs.DraftablesUnavailable("not priced")
    monkeypatch.setattr(dfs, "fetch_draftables", refuse)
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES",
                        {"CBB": [{"GameTypeId": 5, "Name": "Classic", "DraftType": "SalaryCap"}]})
    res = RC.build_slate(client=FakeClient({}, CBB_GAMES, CBB_NAME), groups=_groups(5), persist=False)
    assert res["unpriced"]


@pytest.fixture
def cbb_slate(monkeypatch):
    sal = _salaries(CBB_GAMES, CBB_POS, "Cbb")
    monkeypatch.setattr(dfs, "fetch_draftables", lambda gid: sal)
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES",
                        {"CBB": [{"GameTypeId": 5, "Name": "Classic", "DraftType": "SalaryCap"}]})
    return sal, FakeClient(sal, CBB_GAMES, CBB_NAME, props_per_team=2, book_team=False)


def test_ownership_fills_each_group(cbb_slate):
    sal, client = cbb_slate
    res = RC.build_slate(client=client, groups=_groups(5), n_sims=500, iters=5, persist=False)
    by = {}
    for p in res["pool"]:
        by[p["group"]] = by.get(p["group"], 0.0) + p["own"]
    for grp, total in by.items():
        assert total == pytest.approx(100.0 * theory.GROUP_SLOTS[grp], rel=0.02)
    assert all(0 <= p["own"] <= theory.MAX_OWN for p in res["pool"])


def test_locked_players_stay(cbb_slate):
    sal, client = cbb_slate
    res = RC.build_slate(client=client, groups=_groups(5), n_sims=800, iters=15, persist=False,
                         locked={"Cbb KU Player6"})
    for mode in ("cash", "gpp"):
        assert "Cbb KU Player6" in {r["name"] for r in res[mode]["lineup"]}


def test_portfolio_lineups_differ(cbb_slate):
    sal, client = cbb_slate
    res = RC.build_slate(client=client, groups=_groups(5), n_sims=600, iters=10, persist=False)
    lus = OC.portfolio(res["pool"], res["sim"], 3, iters=8)
    assert len(lus) == 3
    sets = [{r["name"] for r in lu["lineup"]} for lu in lus]
    assert all(len(a & b) <= 5 for i, a in enumerate(sets) for b in sets[i + 1:])


def test_forward_log_is_keyed_by_draft_group(cbb_slate, tmp_path):
    sal, client = cbb_slate
    res = RC.build_slate(client=client, groups=_groups(5), n_sims=300, iters=5, persist=False)
    out = RC.log_forward_test(res["pool"], res["cash"], res["gpp"], 555, res["stats"], root=tmp_path)
    assert out["logged"]
    text = (tmp_path / "data" / "dfs_proj_log_ncaab.csv").read_text().splitlines()
    assert text[0].startswith("date,gid,player") and ",555," in text[1]
    assert (tmp_path / "data" / f"dfs_lineups_ncaab_{out['date']}_g555.csv").exists()
