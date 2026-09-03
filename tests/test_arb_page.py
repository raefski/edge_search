"""Run the Streamlit page top to bottom against a fake `streamlit`.

The page had no test coverage at all, and twice in two days a change to a
shared contract broke it in a way only a human clicking the app could find:

  * the ranking change made "0" mean "no cap", and the two boost panels passed
    that straight to a top-N helper that kept ZERO rows -- so entering a boost
    raised IndexError on `shown[0]`;
  * the same helper read `profit_pct`, which the boosted +EV rows do not have.

Neither needed a clever test. They needed the page to be executed once with a
populated snapshot and a boost entered. That is all this does: it stubs
`streamlit`, execs the real page source, and fails if the page raises.

It is a SMOKE test and deliberately shallow -- it asserts the page runs and
renders something, not what it renders. Its value is that it exercises every
line of a 570-line script that is otherwise only ever run by hand.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "5_⚖️_Arbitrage.py"
sys.path.insert(0, str(ROOT))


class _Stopped(Exception):
    """What `st.stop()` raises -- the page ending early is a normal outcome."""


class _Ctx:
    """A no-op context manager, for st.sidebar / st.container / st.expander."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, _name):
        return _Ctx._noop

    @staticmethod
    def _noop(*_a, **_k):
        return _Ctx()


class FakeStreamlit(types.ModuleType):
    """Enough of Streamlit to execute the page and record what it drew.

    Widgets return the value real Streamlit would return on first render --
    the default -- unless `answers` overrides them by label, which is how a
    test drives a particular path (a boost entered, a cap of 5).
    """

    def __init__(self, answers: dict | None = None):
        super().__init__("streamlit")
        self.answers = answers or {}
        self.drawn: list[tuple[str, object]] = []
        self.session_state: dict = {}
        self.secrets: dict = {}
        self.sidebar = _Ctx()

    # -- widgets that must return a value ---------------------------------
    def _answer(self, label, default):
        return self.answers.get(label, default)

    def slider(self, label, _min=None, _max=None, value=None, *_a, **_k):
        return self._answer(label, value)

    def number_input(self, label, _min=None, _max=None, value=None, *_a, **_k):
        return self._answer(label, value)

    def checkbox(self, label, value=False, *_a, **_k):
        return self._answer(label, value)

    def multiselect(self, label, options=(), default=None, *_a, **_k):
        return self._answer(label, list(default) if default is not None else [])

    def selectbox(self, label, options=(), *_a, **_k):
        opts = list(options)
        return self._answer(label, opts[0] if opts else None)

    def radio(self, label, options=(), *_a, **_k):
        opts = list(options)
        return self._answer(label, opts[0] if opts else None)

    def text_input(self, label, value="", *_a, **_k):
        return self._answer(label, value)

    def date_input(self, label, value=None, *_a, **_k):
        return self._answer(label, value)

    def button(self, label, *_a, **_k):
        return self._answer(label, False)

    # -- output, recorded so a test can assert the page drew something -----
    def _record(self, kind):
        def draw(*a, **_k):
            self.drawn.append((kind, a[0] if a else None))
            return _Ctx()
        return draw

    def __getattr__(self, name):
        # markdown / caption / warning / dataframe / divider / title / ...
        if name.startswith("_"):
            raise AttributeError(name)
        return self._record(name)

    # -- control flow and layout ------------------------------------------
    def stop(self):
        raise _Stopped

    def container(self, *_a, **_k):
        return _Ctx()

    def expander(self, *_a, **_k):
        return _Ctx()

    def columns(self, spec, *_a, **_k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Ctx() for _ in range(n)]

    def set_page_config(self, *_a, **_k):
        return None

    def cache_data(self, *args, **kwargs):
        """Usable bare (@st.cache_data) or called (@st.cache_data(ttl=...))."""
        if args and callable(args[0]):
            return args[0]

        def wrap(fn):
            return fn
        return wrap

    cache_resource = cache_data


def run_page(snapshot: dict | None, answers: dict | None = None, tmp_path=None):
    """Execute the real page source against a snapshot. Returns the fake st."""
    st = FakeStreamlit(answers)
    snap_path = Path(tmp_path) / "arb_snapshot.json"
    if snapshot is not None:
        snap_path.write_text(json.dumps(snapshot))

    src = PAGE.read_text()
    marker = 'SNAPSHOT = ROOT / "data" / "arb_snapshot.json"'
    assert src.count(marker) == 1, "page moved its snapshot path"
    src = src.replace(marker, f"SNAPSHOT = Path({str(snap_path)!r})")

    saved = sys.modules.get("streamlit")
    sys.modules["streamlit"] = st
    try:
        code = compile(src, str(PAGE), "exec")
        exec(code, {"__name__": "__main__", "__file__": str(PAGE)})
    except _Stopped:
        pass                      # the page choosing to end early is fine
    finally:
        if saved is not None:
            sys.modules["streamlit"] = saved
        else:
            sys.modules.pop("streamlit", None)
    return st


# ---------------------------------------------------------------- fixtures
def _snapshot(n_opps: int = 3, n_cands: int = 40) -> dict:
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()

    def leg(book, side, dec, point=None):
        return {"book": book, "side": side, "label": side.title(),
                "decimal": dec, "american": "+100", "point": point,
                "stake": 500.0, "payout": 1000.0, "boost_pct": 0.0,
                "raw_decimal": dec, "age_seconds": 1.0, "link": None,
                "limit": None}

    opps = []
    for i in range(n_opps):
        kind = ("arb", "middle", "ev")[i % 3]
        o = {"kind": kind, "fingerprint": f"f{i}", "sport_key": "americanfootball_ncaaf",
             "sport_title": "NCAAF", "event_id": f"e{i}", "matchup": f"A{i} @ B{i}",
             "commence_time": soon, "market": "totals", "subject": None,
             "description": "totals 50.5", "profit_pct": 2.5 + i,
             "expected_pct": 1.5 + i, "stake_total": 1000.0, "profit_abs": 25.0,
             "max_loss_pct": 4.0, "breakeven_hit_pct": 4.2, "hit_values": [51],
             "push_values": [], "pushes": False, "middle_window": [50.5, 51.5],
             "fair_prob": 0.04, "kelly_stake": 3.0, "anchor_book": "pinnacle",
             "boost": None, "max_age_seconds": 2.0, "warnings": [],
             "found_at": soon,
             "legs": [leg("draftkings", "over", 1.95, 50.5),
                      leg("fanduel", "under", 2.05, 51.5)]}
        if kind == "ev":
            o["legs"] = [leg("draftkings", "over", 1.95, 50.5)]
        opps.append(o)

    cands = []
    for i in range(n_cands):
        cands.append({
            "sport_key": "americanfootball_ncaaf", "sport_title": "NCAAF",
            "event_id": f"c{i}", "matchup": f"C{i} @ D{i}", "commence_time": soon,
            "market": "totals", "subject": None, "point": 50.5 + i,
            "arb_sum": 1.03, "single_book": False,
            "legs": [{"side": "over", "book": "draftkings", "decimal": 1.95,
                      "label": "Over"},
                     {"side": "under", "book": "fanduel", "decimal": 1.95,
                      "label": "Under"}],
            "prices": {"over": {"draftkings": 1.95, "fanduel": 1.92},
                       "under": {"draftkings": 1.92, "fanduel": 1.95}},
        })
    return {"generated_at": soon,
            "stats": {"bankroll": 1000, "events": 10, "groups": 100,
                      "quotes": 500, "fanduel": 200, "draftkings": 200,
                      "fanatics": 100, "anchor": 0, "skipped_events": {}},
            "opportunities": opps, "candidates": cands}


def _snapshot_on_two_dates() -> dict:
    """One opportunity/candidate pair 6 hours out, one 10 days out -- far
    enough apart that "Today" and the +10-day game never land on the same
    US/Eastern calendar date no matter when this test runs."""
    now = datetime.now(timezone.utc)
    near, far = (now + timedelta(hours=6)).isoformat(), (now + timedelta(days=10)).isoformat()

    def leg(book, side, dec):
        return {"book": book, "side": side, "label": side.title(), "decimal": dec,
                "american": "+100", "point": 50.5, "stake": 500.0, "payout": 1000.0,
                "boost_pct": 0.0, "raw_decimal": dec, "age_seconds": 1.0,
                "link": None, "limit": None}

    def opp(tag, commence_time):
        return {"kind": "arb", "fingerprint": tag, "sport_key": "americanfootball_ncaaf",
                "sport_title": "NCAAF", "event_id": tag, "matchup": f"{tag} A @ {tag} B",
                "commence_time": commence_time, "market": "totals", "subject": None,
                "description": "totals 50.5", "profit_pct": 3.0, "expected_pct": 3.0,
                "stake_total": 1000.0, "profit_abs": 25.0, "max_loss_pct": 0.0,
                "breakeven_hit_pct": 0.0, "hit_values": [], "push_values": [],
                "pushes": False, "middle_window": None, "fair_prob": None,
                "kelly_stake": None, "anchor_book": None, "boost": None,
                "max_age_seconds": 2.0, "warnings": [], "found_at": commence_time,
                "legs": [leg("draftkings", "over", 1.95), leg("fanduel", "under", 2.05)]}

    def cand(tag, commence_time):
        return {"sport_key": "americanfootball_ncaaf", "sport_title": "NCAAF",
                "event_id": tag, "matchup": f"{tag} A @ {tag} B",
                "commence_time": commence_time, "market": "totals", "subject": None,
                "point": 50.5, "arb_sum": 1.03, "single_book": False,
                "legs": [{"side": "over", "book": "draftkings", "decimal": 1.95,
                          "label": "Over", "point": 50.5},
                         {"side": "under", "book": "fanduel", "decimal": 1.95,
                          "label": "Under", "point": 50.5}],
                "prices": {"over": {"draftkings": 1.95, "fanduel": 1.92},
                           "under": {"draftkings": 1.92, "fanduel": 1.95}}}

    return {"generated_at": near,
            "stats": {"bankroll": 1000, "events": 2, "groups": 2, "quotes": 8,
                      "fanduel": 4, "draftkings": 4, "fanatics": 0, "anchor": 0,
                      "skipped_events": {}},
            "opportunities": [opp("near", near), opp("far", far)],
            "candidates": [cand("near", near), cand("far", far)]}


# ---------------------------------------------------------------- the tests
def test_the_page_runs_with_no_snapshot_at_all(tmp_path):
    st = run_page(None, tmp_path=tmp_path)
    assert st.drawn, "the page should still render its title and sidebar"


def test_the_page_runs_with_an_empty_snapshot(tmp_path):
    st = run_page(_snapshot(n_opps=0, n_cands=0), tmp_path=tmp_path)
    assert st.drawn


def test_the_page_runs_with_opportunities(tmp_path):
    st = run_page(_snapshot(), tmp_path=tmp_path)
    assert st.drawn


def _captions(st) -> list[str]:
    return [str(a) for k, a in st.drawn if k == "caption"]


def _panel_rendered_rows(st) -> bool:
    """Did the boosted-arbitrage panel actually draw its rows?

    The page is defensive -- an empty `shown` degrades to a blank caption
    rather than an IndexError -- which is right in production and useless in a
    test. So assert on the caption that can only be written from shown[0].
    """
    return any("% position." in c for c in _captions(st))


@pytest.mark.parametrize("cap", [0, 1, 5])
def test_entering_a_boost_renders_rows_at_any_cap(tmp_path, cap):
    """The regression this file exists for. "Cap per sport" defaulting to 0
    meant the boost panels kept zero rows and then indexed shown[0]; a cap of 0
    is also the DEFAULT, so this crashed on the first boost anyone entered.

    Asserting the page merely does not raise is NOT enough -- the page now
    survives an empty `shown`, so a pure smoke assertion passes against the
    original bug. This checks that rows were drawn."""
    st = run_page(_snapshot(), tmp_path=tmp_path, answers={
        "Boost %": 50,
        "Cap per sport (0 = no cap)": cap,
    })
    assert st.drawn
    assert any("boosted arbitrage" in str(a) for k, a in st.drawn if k == "subheader"),         "the boost panel should have found rows on this fixture"
    assert _panel_rendered_rows(st),         f"cap={cap} produced a panel with no rows -- 0 must mean no cap"


@pytest.mark.parametrize("cap", [0, 5])
def test_the_boosted_ev_panel_renders_rows(tmp_path, cap):
    """price_boosted_ev rows carry `ev_pct`, not `profit_pct`. A helper that
    read only the latter raises KeyError here."""
    st = run_page(_snapshot(), tmp_path=tmp_path, answers={
        "Boost %": 50,
        "Cap per sport (0 = no cap)": cap,
        "Boosted view": "Best +EV (unhedged)",
    })
    assert st.drawn
    assert any("boosted +EV" in str(a) for k, a in st.drawn if k == "subheader"), \
        "the +EV panel should have found rows on this fixture"
    assert any("Place the boosted leg only" in c for c in _captions(st)), \
        f"cap={cap} rendered no +EV rows"


def test_a_stale_cached_engine_module_is_reloaded_before_use(tmp_path):
    """Streamlit Community Cloud reruns this script WITHOUT restarting the
    Python process, so `sys.modules` keeps whatever module object an earlier
    run imported. That is not hypothetical: it is exactly what undid the
    cap-0 boost fix in production -- the page's own code updated on the next
    deploy, but `edge.arb.engine` stayed the pre-fix module object, so
    `top_rows_per_sport` kept returning zero rows for every sport with no
    error at all.

    Simulated here by planting a broken `top_rows_per_sport` directly onto the
    already-imported module -- standing in for "what a previous run left
    behind" -- and checking the page throws it out before using it.
    """
    import edge.arb.engine as real_engine

    def stale(rows, n=3):
        return []                 # the pre-fix shape: always empty

    sys.modules["edge.arb.engine"].top_rows_per_sport = stale
    try:
        st = run_page(_snapshot(), tmp_path=tmp_path, answers={
            "Boost %": 50,
            "Cap per sport (0 = no cap)": 0,
        })
    finally:
        importlib.reload(real_engine)
    assert _panel_rendered_rows(st), (
        "a stale cached edge.arb.engine kept the pre-fix top_rows_per_sport "
        "and the boost panel rendered nothing despite finding real rows")


def test_a_sport_filter_that_matches_nothing_is_survivable(tmp_path):
    st = run_page(_snapshot(), tmp_path=tmp_path,
                  answers={"Filter by sport": ["Not A Real Sport"]})
    assert st.drawn


def test_the_date_filter_narrows_to_the_selected_range(tmp_path):
    """"Game date" reads the event's US/Eastern calendar date, not the raw
    UTC timestamp. A custom range covering only the near fixture must keep
    it and drop the one ten days out -- from both the main list and the
    candidates the boost panel prices."""
    from zoneinfo import ZoneInfo

    snap = _snapshot_on_two_dates()
    near_et = (datetime.fromisoformat(snap["opportunities"][0]["commence_time"])
              .astimezone(ZoneInfo("America/New_York")).date())
    st = run_page(snap, tmp_path=tmp_path, answers={
        "Game date": "Custom range",
        "Range (ET)": (near_et, near_et),
        "Boost %": 50,
    })
    captions = _captions(st)
    assert any("near A @ near B" in c for c in captions)
    assert not any("far A @ far B" in c for c in captions), \
        "the +10-day fixture leaked past a range that only covers the near one"


def test_a_corrupt_snapshot_does_not_take_the_page_down(tmp_path):
    (tmp_path / "arb_snapshot.json").write_text("{ this is not json")
    st = run_page(None, tmp_path=tmp_path)
    assert st.drawn
