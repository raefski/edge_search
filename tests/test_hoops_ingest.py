"""Shared ingest changes made for basketball (2026-10-13 web session, ported
2026-10-16): combo prop markets get their own keys, DK basketball prop tabs are
also selected by NAME, the FPPG stat id is detected when DK sends an unknown
one, and the draftables snapshot keeps DK's injury status. All network-free.
"""
from __future__ import annotations

import json

import pytest

from edge import dfs
from edge.arb.marketmap import canonical_market


@pytest.mark.parametrize("name,key", [
    ("Points O/U", "player_points"), ("Pts + Reb O/U", "player_points_rebounds"),
    ("Pts + Reb + Ast O/U", "player_points_rebounds_assists"),
    ("Points + Assists", "player_points_assists"), ("Reb + Ast O/U", "player_rebounds_assists"),
    ("Steals + Blocks O/U", "player_blocks_steals"), ("Threes O/U", "player_threes"),
    ("Turnovers O/U", "player_turnovers"), ("Rebounds O/U", "player_rebounds"),
])
def test_combo_markets_never_collide_with_their_parts(name, key):
    assert canonical_market(name, player="Jayson Tatum", sport_key="basketball_nba") == key


def test_fppg_id_detected_when_no_known_id():
    rows = [{"salary": 3000 + 300 * k,
             "draftStatAttributes": [{"id": 219, "value": str(10 + 1.5 * k)},
                                     {"id": 77, "value": str((k * 7) % 30 + 1)}]}
            for k in range(30)]
    assert dfs.detect_fppg_id(rows) == 219


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
    snap = json.loads((tmp_path / "1.json").read_text())
    assert "status" not in snap[0] and snap[1]["status"] == "O"
