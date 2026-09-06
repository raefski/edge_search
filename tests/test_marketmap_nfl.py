"""NFL prop mapping: the combos and the QB interception line.

Every case here is a real DraftKings market name, read off a live NFL payload
on 2026-09-06 -- the scan where correcting PROP_CATEGORIES took the board from
0 price conflicts to 93 and exposed three mismappings at once. They are pinned
because all three are the same failure this repo keeps meeting: a market that
maps to the WRONG key is silently paired against a different bet, where a
market that maps to nothing is merely absent.
"""
from __future__ import annotations

import pytest

from edge.arb.marketmap import canonical_market


@pytest.mark.parametrize("name,want", [
    # Combined stats must NOT collapse onto the part they contain. A back's
    # rush+rec over 39.5 was 1.13 against 3.03 for his receiving yards alone.
    ("Bijan Robinson Rushing + Receiving Yards O/U", "player_rush_reception_yds"),
    ("Breece Hall Rushing + Receiving Yards", "player_rush_reception_yds"),
    ("Brock Purdy Passing + Rushing Yards O/U", "player_pass_rush_yds"),
    ("C.J. Stroud Passing + Rushing Yards", "player_pass_rush_yds"),
    # A QB's interception line is a PLAYER prop. It used to fall through the
    # football rules entirely and land on the GAME TOTAL at 0.5.
    ("Sam Darnold Interceptions Thrown O/U", "player_pass_interceptions"),
    ("Matthew Stafford Interceptions Thrown", "player_pass_interceptions"),
    # The singles must still map as before.
    ("Drake Maye Passing Yards", "player_pass_yds"),
    ("Drake Maye Pass Yards O/U", "player_pass_yds"),
    ("Baker Mayfield Pass TDs O/U", "player_pass_tds"),
    ("Aaron Rodgers Passing Completions O/U", "player_pass_completions"),
    ("Bo Nix Pass Attempts O/U", "player_pass_attempts"),
    ("Chase Brown Rushing Yards O/U", "player_rush_yds"),
    ("Bucky Irving Rush Attempts O/U", "player_rush_attempts"),
    ("Puka Nacua Receiving Yards", "player_reception_yds"),
    ("Puka Nacua Rec Yards O/U", "player_reception_yds"),
    ("Travis Kelce Receptions O/U", "player_receptions"),
])
def test_nfl_player_market_names(name, want):
    assert canonical_market(name, player="X") == want


def test_no_nfl_player_prop_lands_on_a_game_market():
    """The specific catastrophe: a player prop keyed as `totals` or `spreads`
    meets the real game total at a shared line and invents an arbitrage."""
    names = [
        "Sam Darnold Interceptions Thrown O/U",
        "Brock Purdy Passing + Rushing Yards O/U",
        "Bijan Robinson Rushing + Receiving Yards O/U",
        "Aaron Rodgers Passing Completions O/U",
        "Drake Maye Pass TDs O/U",
    ]
    for name in names:
        assert canonical_market(name, player="X") not in ("totals", "spreads", "h2h"), name


def test_combo_and_its_parts_get_different_keys():
    """The property that actually prevents the conflict, stated directly:
    a combined market and each stat inside it must never share a key."""
    combo = canonical_market("Bijan Robinson Rushing + Receiving Yards O/U", player="X")
    rush = canonical_market("Bijan Robinson Rushing Yards O/U", player="X")
    rec = canonical_market("Bijan Robinson Receiving Yards O/U", player="X")
    assert combo not in (rush, rec)
    assert rush != rec

    pr_combo = canonical_market("Brock Purdy Passing + Rushing Yards O/U", player="X")
    assert pr_combo not in (canonical_market("Brock Purdy Passing Yards", player="X"),
                            canonical_market("Brock Purdy Rushing Yards", player="X"))
