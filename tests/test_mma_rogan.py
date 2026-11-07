"""scripts/mma_rogan.py: the parts that could fake an edge (timing, who a
claim is about, counting one episode many times) or hide one."""
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "transcripts"))

import mma_rogan as R  # noqa: E402


def test_episode_keys_join_rss_and_youtube_titles():
    assert R.episode_key("JRE MMA Show #92 with Angela Hill") == "mma:92"
    assert R.episode_key("#1450 - Brendan Schaub") == "jre:1450"
    assert R.episode_key("Joe Rogan Experience #1450 - Brendan Schaub") == "jre:1450"
    assert R.episode_key("Fight Companion - March 9, 2024") == "fc:2024-03-09"
    assert R.episode_key("JRE Fight Companion - Feb. 4, 2023") == "fc:2023-02-04"


def test_classify_matches_guests_on_mma_show_and_regular_episodes():
    names = {"angelahill", "brendanschaub"}
    assert R.classify("JRE MMA Show #92 with Angela Hill", names) == ("mma_show", ["angelahill"])
    assert R.classify("#799 - Brendan Schaub & Bryan Callen", names) == ("jre_mma_guest", ["brendanschaub"])
    assert R.classify("#1000 - Some Comedian", names) == (None, [])
    assert R.classify("Fight Companion - May 10, 2014", names)[0] == "fight_companion"


def test_public_at_is_the_rss_date_and_ignores_reuploads():
    ep = {"rss_pub": "2020-02-18T00:00:00+00:00"}
    meta = {"published": "2020-02-18T12:00:07-08:00", "live": False}
    assert R.public_at(ep, meta) == dt.datetime(2020, 2, 18, 23, 59, 59, tzinfo=dt.timezone.utc)
    timed = {"rss_pub": "2020-02-18T17:00:00+00:00"}
    assert R.public_at(timed, meta) == dt.datetime(2020, 2, 18, 20, 0, 7, tzinfo=dt.timezone.utc)
    # a 2024 re-upload of a 2021 episode is still a 2021 episode
    old = {"rss_pub": "2021-02-24T18:00:00+00:00"}
    assert R.public_at(old, {"published": "2024-06-20T10:00:00+00:00", "live": False}).year == 2021
    # YouTube earlier than the audio never moves a claim EARLIER
    assert R.public_at(timed, {"published": "2020-02-10T00:00:00+00:00", "live": False}).day == 18
    assert R.public_at(ep, None).day == 18


def _pats(*names):
    fx = [{"name": n, "key": n.replace(" ", "").lower(), "fight": ("f", "2020-01-01", i, "pre")}
          for i, n in enumerate(names)]
    return R.name_patterns(fx, common=set())


def _types(rows, fighter):
    return {r["type"] for r in rows if r["fighter"] == fighter}


def test_another_fighters_name_bounds_the_claim():
    walls = re.compile(r"\b(?:frankie\s+edgar|edgar)\b")
    text = ("we were talking about aldo versus max holloway which is only happening "
            "because frankie edgar apparently got injured")
    rows = R.claims_in(text, _pats("Max Holloway"), walls)
    assert "injury" not in _types(rows, "Max Holloway")
    text = "max holloway got injured in camp and still took the fight"
    assert "injury" in _types(R.claims_in(text, _pats("Max Holloway"), walls), "Max Holloway")


def test_picks_and_hypotheticals():
    rows = R.claims_in("i think israel adesanya knocks him out in the second", _pats("Israel Adesanya"))
    assert "pick_for" in _types(rows, "Israel Adesanya")
    rows = R.claims_in("if max holloway wins this fight spectacularly he moves up", _pats("Max Holloway"))
    assert "pick_for" not in _types(rows, "Max Holloway")
    rows = R.claims_in("honestly i got angela hill in this one", _pats("Angela Hill"))
    assert "pick_for" in _types(rows, "Angela Hill")


def test_one_episode_counts_once_and_signal_is_antisymmetric():
    base = {"kind": "pre", "gap_days": 4, "fight_id": "F", "type": "pick_for", "sign": 1}
    claims = [dict(base, side=0, episode="mma:92")] * 5 + [dict(base, side=1, episode="mma:93")]
    sig = R.fight_signals(claims, "pre")
    x, eps = sig["F"]["pick_for"]
    assert x == 0 and eps == {"mma:92", "mma:93"}        # +1 for A, +1 for B
    sig = R.fight_signals([dict(base, side=1, episode="e")], "pre")
    assert sig["F"]["pick"][0] == -1
    # past the horizon: ignored
    assert R.fight_signals([dict(base, side=0, episode="e", gap_days=45)], "pre") == {}


def test_fit_beta_is_zero_without_signal_and_follows_it():
    rows = [(0.0, 1.0, 1), (0.0, 1.0, 0)] * 50
    assert abs(R.fit_beta(rows)) < 1e-6
    rows = [(0.0, 1.0, 1)] * 70 + [(0.0, 1.0, 0)] * 30
    assert R.fit_beta(rows) > 0.5


def test_surname_inside_another_fighters_full_name_is_not_a_mention():
    walls = re.compile(r"\b(?:charles\s+oliveira|oliveira)\b")
    rows = R.claims_in("dustin poirier versus charles oliveira for the belt", _pats("Vinicius Oliveira"), walls)
    assert not rows
    rows = R.claims_in("vinicius oliveira is a killer", _pats("Vinicius Oliveira"), walls)
    assert rows
