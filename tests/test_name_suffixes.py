"""Jr./Sr./II/III must not drop a player from a join (DFS, every sport)."""
from edge import dfs
from edge.buzz import build_aliases, count_mentions
from edge.dfs_contest import parse_contest_file  # noqa: F401  (shares names.norm)
from edge.names import norm, rekey_bare


def test_contest_key_is_suffix_free_and_rekey_matches_it():
    # edge.dfs.norm keeps the suffix; the contest board's key does not.
    assert dfs.norm("Bobby Witt Jr.") == "bobbywittjr"
    assert norm("Bobby Witt Jr.") == "bobbywitt"
    mlb = {dfs.norm(n): v for n, v in [("Bobby Witt Jr.", 1), ("Michael Harris II", 2),
                                       ("Cody Bellinger", 3), ("Anthony Evans III", 4)]}
    keyed = rekey_bare(mlb)
    for name, v in [("Bobby Witt Jr.", 1), ("Michael Harris II", 2),
                    ("Cody Bellinger", 3), ("Anthony Evans III", 4)]:
        assert keyed[norm(name)] == v


def test_rekey_drops_ambiguous_instead_of_guessing():
    d = {dfs.norm("Luis Garcia"): 1, dfs.norm("Luis Garcia Jr."): 2, dfs.norm("Aaron Judge"): 3}
    keyed = rekey_bare(d)
    assert norm("Luis Garcia") not in keyed
    assert keyed[norm("Aaron Judge")] == 3


def test_buzz_counts_suffixed_players_by_spoken_name():
    board = [{"name": "Michael Hawkins Jr.", "pos": "QB"},
             {"name": "Anthony Evans III", "pos": "WR"},
             {"name": "Re'Shaun Sanford II", "pos": "RB"}]
    counts = count_mentions(
        "Michael Hawkins is the chalk QB, Anthony Evans likes the matchup, "
        "and Re'Shaun Sanford is cheap", build_aliases(board))
    assert counts["Michael Hawkins Jr."] and counts["Anthony Evans III"] and counts["Re'Shaun Sanford II"]


def test_south_dakota_is_not_a_player():
    board = [{"name": "Dakota Twitty", "pos": "WR"}]
    assert not count_mentions("South Dakota State and North Dakota State", build_aliases(board))
