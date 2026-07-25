from edge.names import norm


def test_norm_strips_diacritics():
    # Real bug, caught joining real 2024-25 NBA data: a book's player
    # description ("Nikola Jokic") and stats.nba.com's own display name
    # ("Nikola Jokić") disagree on the accent -- both real, same person.
    assert norm("Nikola Jokić") == norm("Nikola Jokic")
    assert norm("Luka Dončić") == norm("Luka Doncic")
    assert norm("José Ramírez") == norm("Jose Ramirez")


def test_norm_strips_generational_suffixes():
    # Real bug, caught joining real 2024 NFL data: nflverse's
    # player_display_name omits "Jr./II/III" that the Odds API's
    # description keeps -- ~8% of real active skill players silently
    # failed to join before this was fixed.
    assert norm("Travis Etienne Jr.") == norm("Travis Etienne")
    assert norm("Calvin Austin III") == norm("Calvin Austin")
    assert norm("Isaiah Stewart II") == norm("Isaiah Stewart")
    assert norm("Brian Thomas Jr") == norm("Brian Thomas")  # no period, still matches


def test_norm_does_not_strip_a_real_surname_ending_in_a_suffix_letter():
    # The suffix regex requires a preceding space + anchors at the end, so
    # it must not eat part of a real name that happens to end the same way.
    assert norm("Kevin Durant") == "kevindurant"
    assert norm("Chris Paul") == "chrispaul"


def test_norm_handles_both_diacritics_and_suffix_together():
    assert norm("José Ramírez Jr.") == norm("Jose Ramirez")
