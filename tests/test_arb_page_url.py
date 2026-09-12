"""The Arbitrage page's URL writes -- how many, and when.

WHY THIS IS A TEST AND NOT A CODE REVIEW NOTE
The boost panels persist themselves into the query string so that a phone
backgrounding the tab (which can silently start a brand-new server-side
session) does not lose the boosts. That is worth keeping. What was not
worth keeping is how it was done: a bare `st.query_params[k] = v` per field.

streamlit/runtime/state/query_params.py calls _send_query_param_msg() from
__setitem__ UNCONDITIONALLY -- there is no "did this value change" check --
and the browser turns every one of those into a history.pushState(). Eight
fields across two panels is sixteen pushStates per rerun, on every rerun,
whether or not anything changed.

Safari and Brave cap pushState at 100 per 10 seconds. Sixteen per rerun
means SEVEN reruns in ten seconds breaks the page with "Bad message format:
Attempt to use history.pushState() more than 100 times per 10 seconds" --
one drag of the boost slider on a phone. Reported from an iPhone 2026-09-11.

The cap is a browser behaviour, so no unit test can observe it directly.
What it can observe is the thing that feeds it: the number of messages one
render emits. That is the number this file pins.

Uses AppTest (the real script runner) rather than tests/test_arb_page.py's
`streamlit` stub, because the stub has no query-param machinery at all and
so cannot see the bug.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

PAGE = ROOT / "pages" / "5_⚖️_Arbitrage.py"


@pytest.fixture
def url_messages(monkeypatch):
    """Counts page_info_changed ForwardMsgs -- one browser pushState each."""
    from streamlit.runtime.state import query_params as qp_mod

    box = {"n": 0}
    original = qp_mod.QueryParams._send_query_param_msg

    def counting(self):
        box["n"] += 1
        return original(self)

    monkeypatch.setattr(qp_mod.QueryParams, "_send_query_param_msg", counting)
    return box


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(PAGE), default_timeout=120)


def _qp(at, key: str) -> str | None:
    """AppTest exposes the raw multi-value mapping (every value is a list),
    where `st.query_params` inside the script hands back the last value as a
    scalar. Read it the way the page does, so these tests assert on what the
    page actually sees."""
    v = at.query_params.get(key)
    if isinstance(v, list):
        return v[-1] if v else None
    return v


def test_a_rerun_that_changes_nothing_writes_nothing_to_the_url(url_messages):
    """THE regression. Scrolling, a sidebar filter, the Scan button, a
    reconnect -- none of them touch a boost field, and none of them have any
    business pushing browser history."""
    at = _app()
    at.run()
    url_messages["n"] = 0

    at.run()

    assert url_messages["n"] == 0, (
        f"an unchanged rerun emitted {url_messages['n']} URL messages; at "
        f"16 per rerun the browser's 100-per-10s pushState cap trips after 7")


def test_one_render_never_costs_more_than_a_single_url_message(url_messages):
    """Sixteen separate writes is what broke it. Even the first render, which
    does have something to say (it seeds the URL from the defaults), must
    say it once -- st.query_params.update() exists to batch exactly this."""
    at = _app()
    at.run()

    assert url_messages["n"] <= 1, (
        f"first render emitted {url_messages['n']} URL messages; they must be "
        f"batched into one update() call")


def test_a_changed_boost_still_reaches_the_url(url_messages):
    """The guard must not win by doing nothing. Persistence is the whole
    point of writing to the query string: session_state dies when a phone
    backgrounds the tab, the URL does not."""
    at = _app()
    at.run()
    at.slider(key="b1_pct").set_value(25).run()

    assert _qp(at, "b1_pct") == "25"


def test_the_url_seeds_the_widgets_back(url_messages):
    """The other half of the same contract -- a fresh server-side session
    landing on that URL has to come back with the boost still set, which is
    what turns 'boosts silently vanished' into 'boosts silently restored'."""
    at = _app()
    at.query_params["b1_pct"] = "40"
    at.run()

    assert at.slider(key="b1_pct").value == 40


def test_an_empty_multiselect_round_trips_as_absent(url_messages):
    """Why the comparison uses get(k, "") rather than get(k).

    An empty value is stored by DROPPING the key (is_empty_url_value treats
    "" as cleared), so `get("b1_sides")` returns None while the page wants
    "". Compared naively those never match and the flush fires on every
    rerun -- most of the original bug, still present, and invisible.
    """
    at = _app()
    at.run()

    assert "b1_sides" not in at.query_params
    assert at.multiselect(key="b1_sides").value == []


# --- "Filter by game" persists the same way, and must stay inside the flush --
# Added with the control. It is the first field outside the boost panels that
# writes to the URL, so it is the first chance anyone has had to reintroduce
# the original bug by giving it an `update()` (or a bare assignment) of its
# own. The count assertions above already forbid that; these pin the
# behaviour that makes keeping it in `_qp_want` worth doing.

def _a_real_game_id() -> str | None:
    """An event_id from the checked-in snapshot, which is what these tests
    run the page against. AppTest exposes a multiselect's FORMATTED options
    only, so the raw value to select has to come from the data."""
    import json

    try:
        snap = json.loads((ROOT / "data" / "arb_snapshot.json").read_text())
    except (OSError, ValueError):
        return None
    for section in ("opportunities", "candidates", "middle_candidates"):
        for row in snap.get(section) or []:
            if row.get("event_id"):
                return str(row["event_id"])
    return None


def test_picking_a_game_reaches_the_url_and_costs_one_message(url_messages):
    """Persistence is the point -- this filter is set precisely so the reader
    can leave for the book's own app and come back -- but it must ride the
    existing batched flush, not add a second one."""
    game = _a_real_game_id()
    if game is None:
        pytest.skip("the checked-in snapshot has no games to pick")

    at = _app()
    at.run()
    url_messages["n"] = 0

    at.multiselect(key="arb_games").set_value([game]).run()

    assert _qp(at, "arb_games") == game
    assert url_messages["n"] <= 1, (
        f"picking a game emitted {url_messages['n']} URL messages; it must go "
        f"through _qp_want and the single st.query_params.update()")


def test_the_url_seeds_the_game_filter_back(url_messages):
    """The other half: a phone that backgrounded the tab and came back on a
    brand-new server-side session still opens on the game it was filtered
    to, rather than the whole slate."""
    game = _a_real_game_id()
    if game is None:
        pytest.skip("the checked-in snapshot has no games to pick")

    at = _app()
    at.query_params["arb_games"] = game
    at.run()

    assert at.multiselect(key="arb_games").value == [game]


def test_an_unpicked_game_filter_round_trips_as_absent(url_messages):
    """Same contract as `b1_sides`: an empty multiselect is stored by
    dropping the key, so "" and a missing key have to compare equal or the
    flush fires on every rerun."""
    at = _app()
    at.run()

    assert "arb_games" not in at.query_params
    assert at.multiselect(key="arb_games").value == []


def test_a_game_id_the_snapshot_no_longer_has_does_not_crash_the_page(url_messages):
    """Real Streamlit raises StreamlitAPIException on a multiselect `default`
    that is not among its `options` -- so a bookmarked link made from last
    Saturday's snapshot would be a white screen, not a stale filter, if the
    seed were passed through unchecked."""
    at = _app()
    at.query_params["arb_games"] = "fd:not-in-this-snapshot"
    at.run()

    assert not at.exception, \
        f"a stale game id in the URL took the page down: {at.exception}"
    assert at.multiselect(key="arb_games").value == []


def test_a_boosts_game_scope_survives_a_lost_session(url_messages):
    """`b1_game` is a boost field like the rest, so it rides the same batched
    flush and comes back from the URL. It matters more than most: a token
    scoped to the wrong game after a reconnect prices the wrong board."""
    game = _a_real_game_id()
    if game is None:
        pytest.skip("the checked-in snapshot has no games to pick")

    at = _app()
    at.run()
    url_messages["n"] = 0

    at.selectbox(key="b1_game").set_value(game).run()

    assert _qp(at, "b1_game") == game
    assert url_messages["n"] <= 1, (
        f"scoping a boost to a game emitted {url_messages['n']} URL messages")

    back = _app()
    back.query_params["b1_game"] = game
    back.run()
    assert back.selectbox(key="b1_game").value == game


def test_a_boost_game_the_snapshot_no_longer_has_falls_back(url_messages):
    """Same stale-bookmark problem as the display filter, and the same
    requirement: degrade to "every game", never take the page down."""
    at = _app()
    at.query_params["b1_game"] = "fd:not-in-this-snapshot"
    at.run()

    assert not at.exception, f"a stale boost game took the page down: {at.exception}"
    assert at.selectbox(key="b1_game").value == "(every game)"
