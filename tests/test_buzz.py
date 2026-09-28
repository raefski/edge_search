from collections import Counter

from edge.buzz import aggregate, build_aliases, count_mentions, learn_variants

BOARD = [
    {"name": "Jordan Love", "pos": "QB"},
    {"name": "Ja'Marr Chase", "pos": "WR"},
    {"name": "Chase Brown", "pos": "RB"},
    {"name": "A.J. Brown", "pos": "WR"},
    {"name": "Amon-Ra St. Brown", "pos": "WR"},
    {"name": "Jordan Mason", "pos": "RB"},
    {"name": "Jonathan Taylor", "pos": "RB"},
    {"name": "Mason Taylor", "pos": "TE"},
    {"name": "Bijan Robinson", "pos": "RB"},
    {"name": "Kyle Pitts", "pos": "TE"},
    {"name": "Christian McCaffrey", "pos": "RB"},
    {"name": "Marvin Harrison Jr.", "pos": "WR"},
    {"name": "Jahmyr Gibbs", "pos": "RB"},
    {"name": "DeeJay Dallas", "pos": "RB"},
    {"name": "Chargers", "pos": "DST"},
]
HISTORY = Counter({"kyle": 6, "jordan": 4, "bijan": 0})


def count(text):
    return count_mentions(text, build_aliases(BOARD, HISTORY))


def test_sentence_initial_common_word_is_not_a_player():
    assert count("Love the matchup this week. I like it.")["Jordan Love"] == 0


def test_capitalised_surname_mid_sentence_counts():
    assert count("I think Love is fine and so is Gibbs.") == Counter(
        {"Jordan Love": 1, "Jahmyr Gibbs": 1})


def test_lowercase_word_is_not_a_surname():
    assert count("You don't want to chase points, you chase ceiling.")["Ja'Marr Chase"] == 0


def test_full_name_beats_a_shorter_alias():
    got = count("I'll play Chase Brown and also Chase in the same lineup.")
    assert got == Counter({"Chase Brown": 1, "Ja'Marr Chase": 1})


def test_initialled_name_with_or_without_dots():
    got = count("Give me A. J. Brown and AJ Brown again.")
    assert got["A.J. Brown"] == 2


def test_shared_surname_alone_is_ambiguous():
    assert count("I'm just not sure about Brown this week.") == Counter()


def test_particle_surname():
    assert count("Aman Ross St. Brown in the dome")["Amon-Ra St. Brown"] == 1


def test_off_pool_full_name_is_not_split_into_two_players():
    assert count("I like Mason Taylor as a punt.") == Counter({"Mason Taylor": 1})


def test_rare_first_name_counts_common_one_does_not():
    got = count("Everyone loves Bijan. And Kyle Shanahan will scheme it up for Kyle today.")
    assert got["Bijan Robinson"] == 1
    assert got["Kyle Pitts"] == 0


def test_common_first_name_is_not_a_player():
    board = BOARD + [{"name": "Evan Engram", "pos": "TE"}]
    got = count_mentions("Here's what Evan thinks. I like Engram.", build_aliases(board, HISTORY))
    assert got == Counter({"Evan Engram": 1})


def test_team_city_is_not_a_surname():
    got = count("You have to talk that Commanders Dallas game. I'd play DeeJay Dallas though.")
    assert got == Counter({"DeeJay Dallas": 1})


def test_defense_and_nickname_and_suffix():
    got = count("Chargers defense or the Chargers D/ST. CMC and MHJ, Marvin Harrison.")
    assert got["Chargers"] == 2
    assert got["Christian McCaffrey"] == 1
    assert got["Marvin Harrison Jr."] == 2


def test_uncased_track_only_matches_full_names():
    got = count("i like gibbs and jahmyr gibbs and love and chase")
    assert got == Counter({"Jahmyr Gibbs": 1})


def test_caption_misspellings_are_learned_from_the_slate():
    board = BOARD + [{"name": "Wan'Dale Robinson", "pos": "WR"},
                     {"name": "Brian Robinson Jr.", "pos": "RB"},
                     {"name": "Ashton Jeanty", "pos": "RB"}]
    aliases = build_aliases(board, HISTORY)
    texts = ["I love Bejian Robinson. So does he, Bejian Robinson again. "
             "Then Bejian Robinson and Ashton Genty, and Ashton Genty, then Ashton Genty.",
             "Give me Bejian. Or Genty. Brian Robinson is not on this slate."]
    learned = learn_variants(texts, board, aliases)
    got = count_mentions(texts[1], {**aliases, **learned})
    assert got["Bijan Robinson"] == 1 and got["Ashton Jeanty"] == 1
    assert ("brian",) not in learned          # a common name is never learned


def test_a_spelling_seen_once_is_not_learned():
    board = BOARD + [{"name": "Wan'Dale Robinson", "pos": "WR"}]
    aliases = build_aliases(board, HISTORY)
    assert learn_variants(["I like Bejian Robinson."], board, aliases) == {}


def test_aggregate_counts_videos_channels_and_reach():
    rows = aggregate([
        {"channel": "a", "views": 1000, "counts": Counter({"X": 3, "Y": 1})},
        {"channel": "a", "views": 100, "counts": Counter({"X": 1})},
        {"channel": "b", "views": 0, "counts": Counter({"Y": 2})},
    ])
    assert rows["X"] == {"mentions": 4, "videos": 2, "channels": 1, "reach": 850.0}
    assert rows["Y"] == {"mentions": 3, "videos": 2, "channels": 2, "reach": 250.0}


def _pos_pool():
    return [{"name": n, "dk_pos": "WR", "salary": 5000, "proj": 12.0} for n in "ABCDEFGHIJKLMNOPQRST"]


def test_ownership_without_buzz_is_unchanged():
    from edge import dfs_nfl_theory as theory
    plain = theory.add_ownership(_pos_pool())
    again = theory.add_ownership(_pos_pool(), gamma=theory.OWNERSHIP_GAMMA)
    assert [p["own"] for p in plain] == [p["own"] for p in again]


def test_buzz_moves_ownership_toward_the_talked_about_player():
    from edge import dfs_nfl_theory as theory
    pool = _pos_pool()
    for p, b in zip(pool, [300] + [0] * 19):
        p["buzz"] = b
    own = {p["name"]: p["own"] for p in theory.add_ownership(pool)}
    assert own["A"] > own["B"] == own["C"]


def test_attach_buzz_reads_only_this_slate(tmp_path):
    from edge.dfs_run_nfl import attach_buzz
    f = tmp_path / "buzz.csv"
    f.write_text("date,player,mentions\n2026-09-27,Kenneth Walker III,120\n"
                 "2026-09-20,Kenneth Walker III,5\n")
    pool = [{"name": "Kenneth Walker III"}, {"name": "Nobody Talked"}]
    assert attach_buzz(pool, "2026-09-27", f) == 1
    assert [p["buzz"] for p in pool] == [120.0, 0.0]
    fresh = [{"name": "Kenneth Walker III"}]
    assert attach_buzz(fresh, "2026-10-04", f) == 0 and "buzz" not in fresh[0]


def test_new_spelling_rules_from_the_2026_09_27_slate():
    board = BOARD + [{"name": "Tyler Shough", "pos": "QB"},
                     {"name": "Tyler Warren", "pos": "TE"},
                     {"name": "Rashod Bateman", "pos": "WR"},
                     {"name": "Rachaad White", "pos": "RB"}]
    aliases = build_aliases(board, HISTORY)
    texts = ["I like Tyler Shuck here. " * 12
             + "Then Rashad Baitman again. " * 12
             + "And Rashad White too. " * 4]
    learned = learn_variants(texts, board, aliases)
    assert learned[("shuck",)][0] == "Tyler Shough"          # heavy evidence, sounds-like
    assert learned[("baitman",)][0] == "Rashod Bateman"      # frequent, no exact context
    assert ("rashad",) not in learned                        # mostly someone else's name
    got = count_mentions("Everyone is on Rashad Baitman.", {**aliases, **learned})
    assert got == Counter({"Rashod Bateman": 1})
