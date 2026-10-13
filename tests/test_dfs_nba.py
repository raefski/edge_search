"""DK NBA + College Basketball DFS: scoring, market inversion, the simulator's
correlation structure, slot eligibility, the optimiser, and a whole slate
built end to end through build_slate on a synthetic board (fake DK draftables,
fake odds client) -- no network.
"""
from __future__ import annotations

import random

import numpy as np
import pytest

from edge import (dfs, dfs_nba_theory as theory, dfs_opt_nba as opt, dfs_run_nba as R,
                  dfs_run_ncaab as RC, nba, nba_sim, ncaab)
from edge.arb.marketmap import canonical_market


# ---------------------------------------------------------------------------
# scoring
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
    got = nba_sim.dk_points(a("PTS"), a("REB"), a("AST"), a("FG3M"), a("STL"), a("BLK"),
                            a("TOV"))[0]
    assert got == pytest.approx(want)


def test_triple_double_does_not_stack_with_double_double():
    box = {"PTS": 10, "REB": 10, "AST": 10}
    assert nba.actual_points(box) == pytest.approx(10 + 12.5 + 15 + 3.0)


# ---------------------------------------------------------------------------
# positions
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
# markets
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,key", [
    ("Points O/U", "player_points"), ("Pts + Reb O/U", "player_points_rebounds"),
    ("Pts + Reb + Ast O/U", "player_points_rebounds_assists"),
    ("Points + Assists", "player_points_assists"), ("Reb + Ast O/U", "player_rebounds_assists"),
    ("Steals + Blocks O/U", "player_blocks_steals"), ("Threes O/U", "player_threes"),
    ("Turnovers O/U", "player_turnovers"), ("Rebounds O/U", "player_rebounds"),
])
def test_combo_markets_never_collide_with_their_parts(name, key):
    assert canonical_market(name, player="Jayson Tatum", sport_key="basketball_nba") == key


def test_even_money_line_inverts_to_the_line():
    for stat, line in (("pts", 24.5), ("reb", 9.5), ("ast", 6.5), ("fg3", 2.5), ("stl", 1.5)):
        mu = nba_sim.mean_from_market(stat, {"Over": 1.909, "Under": 1.909, "point": line})
        assert abs(mu - line) < (0.35 if stat in ("pts", "reb", "ast") else 0.6), stat


def test_a_juiced_over_moves_the_mean_up():
    even = nba_sim.mean_from_market("pts", {"Over": 1.91, "Under": 1.91, "point": 24.5})
    juiced = nba_sim.mean_from_market("pts", {"Over": 1.6, "Under": 2.35, "point": 24.5})
    assert juiced > even + 1.0
    assert nba_sim.mean_from_market("pts", {"Over": 1.9, "point": 24.5}) is None


def test_combos_fill_missing_stats():
    m, imp = R.means_from_props({"pts": [25.0], "pr": [33.0], "pra": [40.0]},
                                R.NBA_PROFILES["SF"])
    assert m["reb"] == pytest.approx(8.0) and m["ast"] == pytest.approx(7.0)
    assert "reb" not in imp and "ast" not in imp and "stl" in imp
    assert R.means_from_props({"reb": [9.0]}, R.NBA_PROFILES["C"]) == (None, [])


def test_means_from_total_hits_the_total():
    m = R.means_from_total(42.0, R.NBA_PROFILES["PG"])
    assert nba_sim.fantasy_mean(m) == pytest.approx(42.0, abs=0.05)


# ---------------------------------------------------------------------------
# the simulator
# ---------------------------------------------------------------------------
STAR = {"pts": 27, "reb": 7, "ast": 7, "fg3": 2.8, "stl": 1.1, "blk": 0.6, "tov": 3.2}
ROLE = {"pts": 9, "reb": 3.5, "ast": 2, "fg3": 1.6, "stl": 0.8, "blk": 0.3, "tov": 0.9}


def _two_games():
    pool = []
    for t in ("BOS", "NYK", "LAL", "DEN"):
        for k, m in enumerate((STAR, ROLE, ROLE, ROLE, ROLE, ROLE)):
            pool.append({"name": f"{t}{k}", "team": t, "means": dict(m)})
    games = {"g1": {"home": "BOS", "away": "NYK", "pts_home": 118, "pts_away": 112},
             "g2": {"home": "LAL", "away": "DEN", "pts_home": 104, "pts_away": 118}}
    return pool, games


def test_simulated_means_equal_market_means():
    pool, games = _two_games()
    sim = nba_sim.simulate(pool, games, n_sims=20000, seed=3)
    for i, p in enumerate(pool):
        assert sim[:, i].mean() == pytest.approx(nba_sim.fantasy_mean(p["means"]), rel=0.04)


def test_same_game_correlates_and_different_games_do_not():
    pool, games = _two_games()
    sim = nba_sim.simulate(pool, games, n_sims=20000, seed=4)
    c = np.corrcoef(sim.T)
    bos, nyk, lal = 0, 6, 12
    assert c[bos, nyk] > 0.02                       # shared pace
    assert abs(c[bos, lal]) < 0.03                  # independent games


def test_blowouts_cost_starters_their_minutes():
    """The favourite's star in a lopsided game has a thinner upper tail than
    the same star in a pick'em, at the same mean."""
    pool, _ = _two_games()
    even = {"g1": {"home": "BOS", "away": "NYK", "pts_home": 114, "pts_away": 114},
            "g2": {"home": "LAL", "away": "DEN", "pts_home": 114, "pts_away": 114}}
    lop = {"g1": {"home": "BOS", "away": "NYK", "pts_home": 126, "pts_away": 102},
           "g2": even["g2"]}
    a = nba_sim.simulate(pool, even, n_sims=20000, seed=5)[:, 0]
    b = nba_sim.simulate(pool, lop, n_sims=20000, seed=5)[:, 0]
    assert np.percentile(b, 95) < np.percentile(a, 95)


def test_double_double_priced_from_the_joint_distribution():
    big = {"name": "C", "team": "BOS",
           "means": {"pts": 11.0, "reb": 9.5, "ast": 2, "fg3": 0, "stl": 0.6, "blk": 1.4, "tov": 1.4}}
    sim, box = nba_sim.simulate([big], {}, n_sims=20000, seed=6, return_box=True)
    dd = ((box["pts"][:, 0] >= 10) & (box["reb"][:, 0] >= 10)).mean()
    assert 0.25 < dd < 0.6
    assert sim[:, 0].mean() > nba_sim.fantasy_mean(big["means"]) + 0.3


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


@pytest.fixture
def nba_slate(monkeypatch):
    sal = _salaries(NBA_TEAMS, NBA_POS, "Nba")
    monkeypatch.setattr(dfs, "fetch_draftables", lambda gid: sal)
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES", {})
    return sal, FakeClient(sal, NBA_TEAMS, BOOK_NAME)


def test_nba_slate_end_to_end(nba_slate):
    sal, client = nba_slate
    res = R.build_slate(client=client, groups=_groups(nba.CLASSIC_GAME_TYPE), n_sims=1500,
                        iters=25, persist=False)
    assert res["gid"] == 555 and not res.get("error"), res.get("error")
    info = res["stats"]
    # all three games carry a line, with DK's GS / NY / PHO matched to book names
    assert not info["missing_lines"] and {"GSW", "NYK", "PHX"} <= set(info["implied"])
    assert info["implied"]["BOS"] > info["implied"]["NYK"]        # -4.5 home favourite
    assert any("Player3" in o for o in info["out"])
    assert any("Player2" in d for d in info["doubtful"])
    names = {p["name"] for p in res["pool"]}
    assert not any(p["name"].endswith("Player3") and p["team"] == "BOS" for p in res["pool"])
    # 7 props per team -> props; Player7 has FPPG -> fppg; Player8 has neither -> dropped
    srcs = {p["name"].rsplit("Player", 1)[1]: p["source"] for p in res["pool"]}
    assert srcs["0"] == "props" and srcs["7"] == "fppg"
    assert "Nba BOS Player8" not in names and "Nba BOS Player8" in info["dropped_no_projection"]
    for mode in ("cash", "gpp"):
        lu = res[mode]
        assert lu and opt.valid(res["pool"], lu["idx"], opt.NBA)
        assert lu["salary"] <= 50000 and len({r["game"] for r in lu["lineup"]}) >= 2
        assert [s for s, _ in lu["slots"]] == list(nba.SLOTS)
        for slot, p in lu["slots"]:
            assert slot in p["slots"]
    assert res["gpp"]["ceil"] >= res["cash"]["ceil"] - 1e-6
    assert res["cash"]["floor"] >= res["gpp"]["floor"] - 1e-6


def test_late_tips_go_to_the_flex_slots(nba_slate):
    sal, client = nba_slate
    res = R.build_slate(client=client, groups=_groups(nba.CLASSIC_GAME_TYPE), n_sims=800,
                        iters=15, persist=False)
    slots = res["gpp"]["slots"]
    strict = [p["start"] for s, p in slots if s in ("PG", "SG", "SF", "PF", "C")]
    flex = [p["start"] for s, p in slots if s in ("G", "F", "UTIL")]
    # no strict-slot player tips later than a flex player he could have swapped with
    for s, p in slots:
        if s in ("G", "F", "UTIL"):
            continue
        for s2, q in slots:
            if s2 in ("G", "F", "UTIL") and s in q["slots"] and s2 in p["slots"]:
                assert str(p["start"]) <= str(q["start"])
    assert strict and flex


def test_locked_players_stay(nba_slate):
    sal, client = nba_slate
    res = R.build_slate(client=client, groups=_groups(nba.CLASSIC_GAME_TYPE), n_sims=800,
                        iters=15, persist=False, locked={"Nba DEN Player6"})
    for mode in ("cash", "gpp"):
        assert "Nba DEN Player6" in {r["name"] for r in res[mode]["lineup"]}


def test_classic_resolved_by_lobby_name(monkeypatch):
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES",
                        {"NBA": [{"GameTypeId": 999, "Name": "Classic", "DraftType": "SalaryCap"},
                                 {"GameTypeId": 81, "Name": "Showdown Captain Mode"}]})
    rows = R.classic_groups(_groups(999))
    assert [r["gid"] for r in rows] == [555]


def test_unpriced_slate_is_not_a_crash(monkeypatch):
    def refuse(gid):
        raise dfs.DraftablesUnavailable("not priced")
    monkeypatch.setattr(dfs, "fetch_draftables", refuse)
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES", {})
    res = R.build_slate(client=FakeClient({}, NBA_TEAMS, BOOK_NAME),
                        groups=_groups(nba.CLASSIC_GAME_TYPE), persist=False)
    assert res["unpriced"]


# ---------------------------------------------------------------------------
# college
# ---------------------------------------------------------------------------
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
        assert lu and opt.valid(res["pool"], lu["idx"], opt.NCAAB)
        assert [s for s, _ in lu["slots"]] == list(ncaab.SLOTS)


def test_ncaab_without_lobby_types_falls_back_to_multigame_groups(monkeypatch):
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES", {})
    rows = RC.classic_groups(_groups(5))
    assert [r["gid"] for r in rows] == [555] and rows[0]["unverified_type"]


# ---------------------------------------------------------------------------
# optimiser + ownership
# ---------------------------------------------------------------------------
def test_matching_is_exact():
    r = opt.NBA
    m = lambda pos: opt._mask(r, nba.slots_for(pos))         # noqa: E731
    ok = [m(p) for p in ("PG", "SG", "SF", "PF", "C", "PG", "SF", "C")]
    bad = [m(p) for p in ("PG", "PG", "PG", "PF", "C", "SF", "SF", "C")]    # three PGs, no SG
    assert opt._matchable(opt._key(ok), 8)
    assert not opt._matchable(opt._key(bad), 8)


def test_ownership_fills_each_group(nba_slate):
    sal, client = nba_slate
    res = R.build_slate(client=client, groups=_groups(nba.CLASSIC_GAME_TYPE), n_sims=500,
                        iters=5, persist=False)
    by = {}
    for p in res["pool"]:
        by[p["group"]] = by.get(p["group"], 0.0) + p["own"]
    for grp, total in by.items():
        assert total == pytest.approx(100.0 * theory.NBA_GROUP_SLOTS[grp], rel=0.02)
    assert all(0 <= p["own"] <= theory.MAX_OWN for p in res["pool"])


def test_portfolio_lineups_differ(nba_slate):
    sal, client = nba_slate
    res = R.build_slate(client=client, groups=_groups(nba.CLASSIC_GAME_TYPE), n_sims=600,
                        iters=10, persist=False)
    lus = opt.portfolio(res["pool"], res["sim"], 3, iters=8)
    assert len(lus) == 3
    sets = [{r["name"] for r in lu["lineup"]} for lu in lus]
    assert all(len(a & b) <= 5 for i, a in enumerate(sets) for b in sets[i + 1:])


def test_forward_log_is_keyed_by_draft_group(nba_slate, tmp_path):
    sal, client = nba_slate
    res = R.build_slate(client=client, groups=_groups(nba.CLASSIC_GAME_TYPE), n_sims=300,
                        iters=5, persist=False)
    out = R.log_forward_test(R.SPEC, res["pool"], res["cash"], res["gpp"], 555,
                             res["stats"], root=tmp_path)
    assert out["logged"]
    text = (tmp_path / "data" / "dfs_proj_log_nba.csv").read_text().splitlines()
    assert text[0].startswith("date,gid,player") and ",555," in text[1]
    assert (tmp_path / "data" / f"dfs_lineups_nba_{out['date']}_g555.csv").exists()


def test_fppg_id_detected_when_no_known_id():
    rows = [{"salary": 3000 + 300 * k,
             "draftStatAttributes": [{"id": 219, "value": str(10 + 1.5 * k)},
                                     {"id": 77, "value": str((k * 7) % 30 + 1)}]}
            for k in range(30)]
    assert dfs.detect_fppg_id(rows) == 219


# ---------------------------------------------------------------------------
# shared-layer changes made for basketball
# ---------------------------------------------------------------------------
def test_basketball_prop_tabs_selected_by_name_only_for_basketball():
    from edge.arb.draftkings_league import DraftKingsLeague
    dk = DraftKingsLeague.__new__(DraftKingsLeague)
    payload = {"categories": [{"id": 9001, "name": "Player Threes"},
                              {"id": 9002, "name": "Game Lines"}],
               "subcategories": [{"categoryId": 9001, "id": 1, "name": "Threes O/U"},
                                 {"categoryId": 9002, "id": 2, "name": "Spread"}]}
    assert [s for _, s, _ in dk.prop_subcategories(payload, sport_key="basketball_nba")] == [1]
    assert dk.prop_subcategories(payload, sport_key="icehockey_nhl") == []
    assert dk.prop_subcategories(payload) == []


def test_snapshot_keeps_injury_status_but_not_dks_none(monkeypatch, tmp_path):
    rows = [{"displayName": "A", "salary": 5000, "position": "PG", "teamAbbreviation": "BOS",
             "competition": {"name": "NY @ BOS"}, "draftStatAttributes": [{"id": 408, "value": 30}],
             "playerId": 1, "status": "None"},
            {"displayName": "B", "salary": 6000, "position": "C", "teamAbbreviation": "BOS",
             "competition": {"name": "NY @ BOS"}, "draftStatAttributes": [{"id": 408, "value": 35}],
             "playerId": 2, "status": "O"}]
    monkeypatch.setattr(dfs, "_get", lambda url: {"draftables": rows})
    monkeypatch.setattr(dfs, "_SNAP_DIR", tmp_path)
    assert dfs.save_draftables_snapshot(1) == 2
    import json
    snap = json.loads((tmp_path / "1.json").read_text())
    assert "status" not in snap[0] and snap[1]["status"] == "O"
