"""The college rosetta stone (edge/college_teams.py) against data/college_teams.json.

Each case is a spelling that has broken (or would break) a join in this repo."""
from edge import college_teams as ct
from edge import ncaab


def sid(display):
    hits = [k for k, s in ct._load()["schools"].items() if s["display"] == display]
    assert len(hits) == 1, display
    return hits[0]


def test_key_folds_saint_state_and_keeps_at():
    assert ct.key("Saint Mary's (CA)") == ct.key("St. Mary's CA")
    assert ct.key("Kansas St.") == ct.key("Kansas State")
    # "A&T" -> "at" must survive: dropping it made NC A&T == North Carolina (11/21)
    assert ct.key("North Carolina A&T") != ct.key("North Carolina")


def test_every_source_dialect_resolves_to_one_school():
    unc, ncat = sid("North Carolina Tar Heels"), sid("North Carolina A&T Aggies")
    assert ct.resolve("North Carolina") == unc and ct.resolve("NC A&T") == ncat
    olemiss = sid("Ole Miss Rebels")
    for n in ("Mississippi", "Ole Miss", "Ole Miss Rebels"):
        assert ct.resolve(n) == olemiss, n
    ncst = sid("NC State Wolfpack")
    for n in ("N.C. State", "NC State", "North Carolina State"):
        assert ct.resolve(n) == ncst, n
    assert ct.resolve("Wichita State Shockers") == ct.resolve("Wichita St.")
    assert ct.resolve("Miami FL") != ct.resolve("Miami OH")


def test_shared_name_needs_the_game():
    assert ct.resolve("Loyola") is None
    lmd, gtown = sid("Loyola Maryland Greyhounds"), sid("Georgetown Hoyas")
    assert ct.resolve("Loyola", among={lmd, gtown}) == lmd
    assert ct.resolve("Miami") is None


def test_dk_codes():
    nova = sid("Villanova Wildcats")
    assert nova in ct.dk_ids("VU", "CBB")              # draftables say VU, lobby NOVA
    assert len(ct.dk_ids("SDSU", "CBB")) == 2         # San Diego St. AND South Dakota St.
    assert ct.same_school("VU", "Villanova", "CBB") is True
    assert ct.same_school("ZZZZ", "Villanova", "CBB") is None


def test_ncaab_match_team_uses_registry():
    assert ncaab.match_team("VU", "Villanova") == 1.0
    assert ncaab.match_team("GMU", "George Mason") == 1.0
    assert ncaab.match_team("LY-IL", "Loyola Chicago") == 1.0
    assert ncaab.match_team("UK", "Kansas") == 0.0     # the old guards still hold
