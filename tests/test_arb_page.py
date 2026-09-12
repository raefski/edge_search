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
        # A plain dict covers the page's usage: .get(key, default) to read,
        # [key] = value to write back. Real st.query_params also reflects
        # writes into the browser URL, which is exactly why the page uses it
        # to survive a lost session -- irrelevant here, where only the
        # read/write contract needs to hold.
        self.query_params: dict = {}

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


def run_page(snapshot: dict | None, answers: dict | None = None, tmp_path=None,
             st: "FakeStreamlit | None" = None):
    """Execute the real page source against a snapshot. Returns the fake st.

    `st` lets a caller hand in a stub it has already prepared -- seeded query
    params, or a widget method wrapped in a spy -- for the few things that
    cannot be asserted from `drawn` alone, such as what a widget was OFFERED
    rather than what it returned.
    """
    if st is None:
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


def _snapshot_with_a_live_game() -> dict:
    """Reuses _snapshot_on_two_dates' builders with a "live" (30 minutes ago)
    fixture standing in for the far one, and a normal upcoming fixture for
    the near one."""
    snap = _snapshot_on_two_dates()
    live_ct = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    for o in snap["opportunities"]:
        if o["fingerprint"] == "far":
            o["commence_time"] = live_ct
    for c in snap["candidates"]:
        if c["event_id"] == "far":
            c["commence_time"] = live_ct
    return snap


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


def test_a_free_middle_ranks_above_a_bigger_plain_arb(tmp_path):
    """A free middle has no downside AND the middle's upside, so it must
    lead the list even against an arb with a much larger expected_pct --
    ranking is not just "highest expected value" for this one case."""
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()

    def leg(book, side, dec):
        return {"book": book, "side": side, "label": side.title(), "decimal": dec,
                "american": "+100", "point": 50.5, "stake": 500.0, "payout": 1000.0,
                "boost_pct": 0.0, "raw_decimal": dec, "age_seconds": 1.0,
                "link": None, "limit": None}

    big_arb = {"kind": "arb", "fingerprint": "bigarb", "sport_key": "americanfootball_nfl",
              "sport_title": "NFL", "event_id": "bigarb", "matchup": "Big Arb Away @ Big Arb Home",
              "commence_time": soon, "market": "totals", "subject": None,
              "description": "totals 50.5", "profit_pct": 8.0, "expected_pct": 8.0,
              "stake_total": 1000.0, "profit_abs": 80.0, "max_loss_pct": 0.0,
              "breakeven_hit_pct": 0.0, "hit_values": [], "push_values": [],
              "pushes": False, "middle_window": None, "fair_prob": None,
              "free_middle": False, "kelly_stake": None, "anchor_book": None,
              "boost": None, "max_age_seconds": 2.0, "warnings": [], "found_at": soon,
              "legs": [leg("draftkings", "over", 1.95), leg("fanduel", "under", 2.05)]}
    free_middle = {"kind": "middle", "fingerprint": "freemid", "sport_key": "baseball_mlb",
                  "sport_title": "MLB", "event_id": "freemid",
                  "matchup": "Free Middle Away @ Free Middle Home",
                  "commence_time": soon, "market": "totals", "subject": None,
                  "description": "totals middle 45.5-47.5 (wins both on 46/47)",
                  "profit_pct": 45.0, "expected_pct": 1.0, "stake_total": 1000.0,
                  "profit_abs": 450.0, "max_loss_pct": 0.0, "breakeven_hit_pct": 0.0,
                  "hit_values": [46, 47], "push_values": [], "pushes": False,
                  "middle_window": [45.5, 47.5], "fair_prob": 0.05,
                  "free_middle": True, "free_middle_floor_pct": 2.0,
                  "kelly_stake": None, "anchor_book": None, "boost": None,
                  "max_age_seconds": 2.0, "warnings": [], "found_at": soon,
                  "legs": [leg("draftkings", "over", 2.5), leg("fanduel", "under", 2.5)]}
    snap = {"generated_at": soon,
            "stats": {"bankroll": 1000, "events": 2, "groups": 2, "quotes": 8,
                      "fanduel": 4, "draftkings": 4, "fanatics": 0, "anchor": 0,
                      "skipped_events": {}},
            "opportunities": [big_arb, free_middle], "candidates": []}

    st = run_page(snap, tmp_path=tmp_path)
    markdowns = [str(a) for k, a in st.drawn if k == "markdown"]
    free_idx = next(i for i, m in enumerate(markdowns) if "FREE MIDDLE" in m)
    arb_idx = next(i for i, m in enumerate(markdowns) if "+8.00%" in m)
    assert free_idx < arb_idx, \
        "the free middle (no downside) must render before the 8% plain arb"


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
        "Boost 1 %": 50,
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
        "Boost 1 %": 50,
        "Cap per sport (0 = no cap)": cap,
        "Boosted view": "Best +EV (unhedged)",
    })
    assert st.drawn
    assert any("boosted +EV" in str(a) for k, a in st.drawn if k == "subheader"), \
        "the +EV panel should have found rows on this fixture"
    assert any("Place the boosted leg only" in c for c in _captions(st)), \
        f"cap={cap} rendered no +EV rows"


def test_two_boosts_stack_and_show_best_floor_ceiling(tmp_path):
    """Two different tokens on two different books (Boost 1 defaults to
    DraftKings, Boost 2 to FanDuel) must both apply to the same candidate,
    and the page must surface a combined floor/ceiling summary -- not just
    whichever boost happens to win alone."""
    st = run_page(_snapshot(), tmp_path=tmp_path, answers={
        "Boost 1 %": 50,
        "Boost 2 %": 30,
        "Cap per sport (0 = no cap)": 0,
    })
    assert st.drawn
    assert any("boosted arbitrage" in str(a) for k, a in st.drawn if k == "subheader"), \
        "the boost panel should have found rows on this fixture"
    assert any("Best floor/ceiling" in str(a) for k, a in st.drawn if k == "markdown"), \
        "stacking two boosts should surface a combined floor/ceiling summary"
    assert _panel_rendered_rows(st)


def test_boosted_middle_panel_renders(tmp_path):
    """`middle_candidates` in the snapshot must produce a boosted-middle
    panel when a boost is entered, not just the two-way arb panel."""
    snap = _snapshot()
    soon = snap["candidates"][0]["commence_time"]
    snap["middle_candidates"] = [{
        "sport_key": "americanfootball_ncaaf", "sport_title": "NCAAF",
        "event_id": "m0", "matchup": "M0 @ N0", "commence_time": soon,
        "market": "totals", "subject": None, "window": [45.5, 47.5],
        "cost_pct": 4.0,
        "legs": [
            {"side": "over", "book": "draftkings", "decimal": 1.91,
             "label": "Over", "point": 45.5, "line": 45.5},
            {"side": "under", "book": "fanduel", "decimal": 1.91,
             "label": "Under", "point": 47.5, "line": 47.5},
        ],
    }]
    st = run_page(snap, tmp_path=tmp_path, answers={
        "Boost 1 %": 50,
        "Boost 2 %": 30,
    })
    assert any("boosted middle" in str(a) for k, a in st.drawn if k == "subheader"), \
        "a middle_candidates entry should render its own boosted-middle panel"


def test_casino_mode_filters_to_dk_fd_and_ranks_by_smallest_risk(tmp_path):
    """Casino mode must drop any opportunity with a Fanatics leg, keep a real
    arbitrage on top regardless of size, and rank everything else by
    ascending risk_pct -- not by expected_pct like the normal view does. Gap
    opportunities must appear even though "gap" is not in the default Show
    selection, since Casino mode always wants them on the board."""
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()

    def leg(book, side, dec):
        return {"book": book, "side": side, "label": side.title(), "decimal": dec,
                "american": "+100", "point": None, "stake": 500.0, "payout": 1000.0,
                "boost_pct": 0.0, "raw_decimal": dec, "age_seconds": 1.0,
                "link": None, "limit": None}

    base = {"kind": "arb", "sport_key": "baseball_mlb", "sport_title": "MLB",
           "commence_time": soon, "market": "h2h", "subject": None,
           "description": "h2h", "stake_total": 1000.0, "profit_abs": 200.0,
           "max_loss_pct": 0.0, "breakeven_hit_pct": 0.0, "hit_values": [],
           "push_values": [], "pushes": False, "middle_window": None,
           "fair_prob": None, "free_middle": False, "kelly_stake": None,
           "anchor_book": None, "boost": None, "max_age_seconds": 2.0,
           "warnings": [], "found_at": soon}

    fanatics_arb = {**base, "fingerprint": "fan", "event_id": "fan",
                    "matchup": "Fan A @ Fan B", "profit_pct": 20.0, "expected_pct": 20.0,
                    "risk_pct": 0.0,
                    "legs": [leg("draftkings", "home", 2.0), leg("fanatics", "away", 2.0)]}
    dkfd_arb = {**base, "fingerprint": "dkfd", "event_id": "dkfd",
               "matchup": "DKFD A @ DKFD B", "profit_pct": 2.0, "expected_pct": 2.0,
               "risk_pct": 0.0,
               "legs": [leg("draftkings", "home", 2.0), leg("fanduel", "away", 2.0)]}
    gap_base = {**base, "kind": "gap", "market": "totals", "expected_pct": None,
               "max_loss_pct": 100.0, "hit_values": [43], "middle_window": [42.5, 44.5],
               "fair_prob": 0.02, "warnings": ["NOT a guaranteed position"]}
    safe_gap = {**gap_base, "fingerprint": "safegap", "event_id": "safegap",
               "matchup": "Safe A @ Safe B", "profit_pct": 1.0, "risk_pct": 2.0,
               "legs": [leg("draftkings", "home", 2.05), leg("fanduel", "away", 2.0)]}
    risky_gap = {**safe_gap, "fingerprint": "riskygap", "event_id": "riskygap",
                "matchup": "Risky A @ Risky B", "risk_pct": 40.0}

    snap = {"generated_at": soon,
            "stats": {"bankroll": 1000, "events": 4, "groups": 4, "quotes": 8,
                      "fanduel": 4, "draftkings": 4, "fanatics": 4, "anchor": 0,
                      "skipped_events": {}},
            "opportunities": [fanatics_arb, dkfd_arb, safe_gap, risky_gap],
            "candidates": []}

    st = run_page(snap, tmp_path=tmp_path,
                 answers={"🎰 Casino mode (DraftKings + FanDuel only)": True})
    all_text = [str(a) for k, a in st.drawn if k in ("markdown", "caption")]
    assert not any("Fan A @ Fan B" in t for t in all_text), \
        "a Fanatics leg must be dropped in Casino mode"
    idx_dkfd = next(i for i, t in enumerate(all_text) if "DKFD A" in t)
    idx_safe = next(i for i, t in enumerate(all_text) if "Safe A" in t)
    idx_risky = next(i for i, t in enumerate(all_text) if "Risky A" in t)
    assert idx_dkfd < idx_safe < idx_risky, \
        "real arb first regardless of size, then gaps by ascending risk"


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
            "Boost 1 %": 50,
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


# --- "Filter by game" ------------------------------------------------------
# Books issue profit boosts scoped to ONE GAME ("50% on Ohio State vs
# Michigan"). Before this control the only way to act on one was to enter the
# token and then scroll the whole slate looking for the game it was good on,
# which on a phone minutes before kickoff is the entire session.

def _snapshot_of_many_games() -> dict:
    """Three games across two leagues, each carrying BOTH an opportunity and
    a candidate.

    Both, because the filter has to narrow both boards: the plain
    opportunity list, and the boost panel that is priced from `candidates`
    and is the one actually being scrolled once a token is entered.
    """
    now = datetime.now(timezone.utc)

    def leg(book, side, dec):
        return {"book": book, "side": side, "label": side.title(), "decimal": dec,
                "american": "+100", "point": 50.5, "stake": 500.0, "payout": 1000.0,
                "boost_pct": 0.0, "raw_decimal": dec, "age_seconds": 1.0,
                "link": None, "limit": None}

    games = [
        ("g_osu", "americanfootball_ncaaf", "NCAAF", "Ohio State @ Michigan", 6),
        ("g_bama", "americanfootball_ncaaf", "NCAAF", "Alabama @ Auburn", 9),
        ("g_nyy", "baseball_mlb", "MLB", "Yankees @ Red Sox", 7),
    ]

    opps, cands = [], []
    for event_id, sport_key, sport_title, matchup, hours in games:
        ct = (now + timedelta(hours=hours)).isoformat()
        opps.append({
            "kind": "arb", "fingerprint": event_id, "sport_key": sport_key,
            "sport_title": sport_title, "event_id": event_id, "matchup": matchup,
            "commence_time": ct, "market": "totals", "subject": None,
            "description": "totals 50.5", "profit_pct": 3.0, "expected_pct": 3.0,
            "stake_total": 1000.0, "profit_abs": 30.0, "max_loss_pct": 0.0,
            "breakeven_hit_pct": 0.0, "hit_values": [], "push_values": [],
            "pushes": False, "middle_window": None, "fair_prob": None,
            "free_middle": False, "kelly_stake": None, "anchor_book": None,
            "boost": None, "max_age_seconds": 2.0, "warnings": [], "found_at": ct,
            "legs": [leg("draftkings", "over", 1.95), leg("fanduel", "under", 2.05)]})
        cands.append({
            "sport_key": sport_key, "sport_title": sport_title,
            "event_id": event_id, "matchup": matchup, "commence_time": ct,
            "market": "totals", "subject": None, "point": 50.5, "arb_sum": 1.03,
            "single_book": False,
            "legs": [{"side": "over", "book": "draftkings", "decimal": 1.95,
                      "label": "Over", "point": 50.5},
                     {"side": "under", "book": "fanduel", "decimal": 1.95,
                      "label": "Under", "point": 50.5}],
            "prices": {"over": {"draftkings": 1.95, "fanduel": 1.92},
                       "under": {"draftkings": 1.92, "fanduel": 1.95}}})

    return {"generated_at": now.isoformat(),
            "stats": {"bankroll": 1000, "events": 3, "groups": 3, "quotes": 12,
                      "fanduel": 6, "draftkings": 6, "fanatics": 0, "anchor": 0,
                      "skipped_events": {}},
            "opportunities": opps, "candidates": cands}


def test_the_game_filter_narrows_the_board_to_the_chosen_game(tmp_path):
    """The whole point: pick the game a token is scoped to, see only it."""
    st = run_page(_snapshot_of_many_games(), tmp_path=tmp_path,
                  answers={"Filter by game": ["g_osu"]})
    text = " ".join(str(a) for k, a in st.drawn if k in ("markdown", "caption"))

    assert "Ohio State @ Michigan" in text
    assert "Alabama @ Auburn" not in text, \
        "a game the filter excluded leaked onto the board"
    assert "Yankees @ Red Sox" not in text, \
        "the other league leaked past a filter naming one game"


def test_the_game_filter_narrows_the_boost_panel_too(tmp_path):
    """With a token entered it is the BOOST panel being scrolled, priced off
    `candidates` -- narrowing only the plain list below would leave the
    actual workflow untouched."""
    st = run_page(_snapshot_of_many_games(), tmp_path=tmp_path, answers={
        "Boost 1 %": 50,
        "Filter by game": ["g_nyy"],
    })
    text = " ".join(str(a) for k, a in st.drawn if k in ("markdown", "caption"))

    assert "Yankees @ Red Sox" in text
    assert "Ohio State @ Michigan" not in text, \
        "the boost panel still priced candidates from an excluded game"


def test_picking_several_games_keeps_all_of_them(tmp_path):
    st = run_page(_snapshot_of_many_games(), tmp_path=tmp_path,
                  answers={"Filter by game": ["g_osu", "g_nyy"]})
    text = " ".join(str(a) for k, a in st.drawn if k in ("markdown", "caption"))

    assert "Ohio State @ Michigan" in text
    assert "Yankees @ Red Sox" in text
    assert "Alabama @ Auburn" not in text


def test_a_game_filter_matching_nothing_blames_the_filters_not_a_stale_scan(tmp_path):
    """Requirement three of the empty state. `_empty_reason` tells "this scan
    is old, every game has kicked off" apart from "your filters exclude
    everything", and sending someone to request a fresh scan they do not need
    costs the session.

    A game that is pickable but has no opportunity of its own is the ordinary
    way to land here: the options include candidates-only games, because
    those are the ones a boost can turn into something. Three good
    opportunities are still on this board, so this is a FILTER outcome.
    """
    snap = _snapshot_of_many_games()
    only_cand = dict(snap["candidates"][0])
    only_cand.update({"event_id": "g_only_cand", "matchup": "Ducks @ Beavers"})
    snap["candidates"].append(only_cand)

    st = run_page(snap, tmp_path=tmp_path,
                  answers={"Filter by game": ["g_only_cand"]})
    msg = _warnings(st)

    assert "clears the filters" in msg, \
        "an unmatched game filter should land in the filters branch"
    assert "Ducks @ Beavers" in msg, \
        "the message should name the game that came up empty"
    assert "Filter by game" in msg, \
        "and name the filter most likely to be at fault"
    assert "stale scan" not in msg, \
        "an unmatched game filter must not make the page claim the scan is old"
    assert "already kicked off" not in msg
    assert "days with no" not in msg, \
        "three opportunities were found; the quiet-board reassurance is false here"


def test_a_game_id_the_snapshot_never_had_is_still_survivable(tmp_path):
    """The same branch, reached with an id no longer in the snapshot at all.
    Nothing in the UI can produce this -- the widget only returns its own
    options and the URL seed is intersected with them -- but the empty state
    should not depend on that being true."""
    st = run_page(_snapshot_of_many_games(), tmp_path=tmp_path,
                  answers={"Filter by game": ["g_not_in_this_snapshot"]})
    msg = _warnings(st)

    assert st.drawn
    assert "clears the filters" in msg
    assert "stale scan" not in msg


def test_a_picked_game_that_has_started_does_not_condemn_the_whole_scan(tmp_path):
    """The adjacent trap. "All N opportunities in this scan have already
    kicked off -- this is a stale scan" is a claim about the WHOLE board, and
    with one game picked it is simply false: the rest of the slate can be
    fresh. Minutes before kickoff, the game you picked having just started is
    the likeliest case there is."""
    snap = _snapshot_of_many_games()
    started = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    for row in snap["opportunities"] + snap["candidates"]:
        if row["event_id"] == "g_osu":
            row["commence_time"] = started

    st = run_page(snap, tmp_path=tmp_path, answers={"Filter by game": ["g_osu"]})
    msg = _warnings(st)

    assert "already kicked off" in msg
    assert "Ohio State @ Michigan" in msg, \
        "the message should name the game that started, not the whole scan"
    assert "stale scan" not in msg, \
        "one started game is not a stale scan while the rest of the board is live"


def test_casino_mode_with_a_game_picked_offers_the_game_filter_first(tmp_path):
    """Casino mode's rule is the narrowest so it keeps the branch, but a game
    filter set on top of it is the cheaper thing to relax -- standing at a
    window with one boosted game is a real combination, and the message
    should not pretend the only options are the date range and the mode."""
    snap = _snapshot_of_many_games()
    # every leg is DraftKings/FanDuel already, so empty the board a different
    # way: a minimum the 3% opportunities cannot clear
    st = run_page(snap, tmp_path=tmp_path, answers={
        "🎰 Casino mode (DraftKings + FanDuel only)": True,
        "Filter by game": ["g_osu"],
        "Minimum %": 15.0,
    })
    msg = _warnings(st)

    assert "Casino mode" in msg
    assert "Filter by game" in msg, \
        "with a game picked, clearing it should be offered as a way out"
    assert "Ohio State @ Michigan" in msg


def test_an_all_live_board_still_says_stale_when_no_game_is_picked(tmp_path):
    """The scoping above must not disarm the original warning. With no game
    filter on, an entirely kicked-off board is still a stale scan."""
    st = run_page(_all_live_snapshot(), tmp_path=tmp_path)
    msg = _warnings(st)

    assert "stale scan, not a quiet board" in msg


def _spy_on_the_game_multiselect(st: FakeStreamlit) -> dict:
    """Record what "Filter by game" was OFFERED and SEEDED with.

    `drawn` only captures output elements, and the interesting half of this
    control is its inputs: which games made the option list, how they are
    labelled and ordered, and what default survived the URL seed.
    """
    box: dict = {}
    original = st.multiselect

    def spy(label, options=(), default=None, *a, **k):
        if label == "Filter by game":
            fmt = k.get("format_func") or (lambda x: x)
            box["options"] = list(options)
            box["labels"] = [fmt(o) for o in options]
            box["default"] = list(default or [])
        return original(label, options, default, *a, **k)

    st.multiselect = spy
    return box


def test_the_game_options_cover_games_that_are_only_in_candidates(tmp_path):
    """Options are drawn from `candidates` and `middle_candidates` as well as
    `opportunities`, because the boost panels are priced from those -- a game
    with no opportunity of its own is exactly the one a token might turn
    into one."""
    snap = _snapshot_of_many_games()
    only_cand = dict(snap["candidates"][0])
    only_cand.update({"event_id": "g_only_cand", "matchup": "Ducks @ Beavers"})
    snap["candidates"].append(only_cand)

    st = FakeStreamlit()
    seen = _spy_on_the_game_multiselect(st)
    run_page(snap, tmp_path=tmp_path, st=st)

    assert "g_only_cand" in seen["options"], \
        "a game only present in candidates must still be pickable"
    assert "g_osu" in seen["options"]


def test_the_game_options_cover_middle_candidates_too(tmp_path):
    """`middle_candidates` feeds its own boosted-middle panel, so a game that
    only ever shows up there is just as pickable as one in `candidates`."""
    snap = _snapshot_of_many_games()
    snap["middle_candidates"] = [{
        "sport_key": "icehockey_nhl", "sport_title": "NHL",
        "event_id": "g_bruins", "matchup": "Bruins @ Rangers",
        "commence_time": snap["candidates"][0]["commence_time"],
        "market": "totals", "subject": None, "window": [5.5, 6.5],
        "cost_pct": 4.0,
        "legs": [{"side": "over", "book": "draftkings", "decimal": 1.91,
                  "label": "Over", "point": 5.5, "line": 5.5},
                 {"side": "under", "book": "fanduel", "decimal": 1.91,
                  "label": "Under", "point": 6.5, "line": 6.5}]}]

    st = FakeStreamlit()
    seen = _spy_on_the_game_multiselect(st)
    run_page(snap, tmp_path=tmp_path, st=st)

    assert "g_bruins" in seen["options"], \
        "a game only present in middle_candidates must still be pickable"
    label = seen["labels"][seen["options"].index("g_bruins")]
    assert label.startswith("NHL · Bruins @ Rangers · "), label


def test_the_game_labels_carry_the_sport_and_the_kickoff(tmp_path):
    """Phone-first: a raw list of matchups is unpickable. Each option reads
    "NCAAF - Ohio State @ Michigan - Sat 3:30 PM", and the list is sorted by
    sport so one league's games sit together."""
    st = FakeStreamlit()
    seen = _spy_on_the_game_multiselect(st)
    run_page(_snapshot_of_many_games(), tmp_path=tmp_path, st=st)

    labels = seen["labels"]
    osu = next(x for x in labels if "Ohio State" in x)
    sport, matchup, kickoff = osu.split(" · ")
    assert sport == "NCAAF", f"no sport on the label: {osu!r}"
    assert matchup == "Ohio State @ Michigan"
    assert kickoff, f"no kickoff on the label: {osu!r}"
    # sorted by sport, so MLB's game comes before both NCAAF ones -- a
    # multiselect has no option groups, so sorting IS the grouping
    assert [x.split(" · ")[0] for x in labels] == ["MLB", "NCAAF", "NCAAF"]
    # and within a sport, by kickoff: Ohio State is 6h out, Alabama 9h
    assert labels[1].split(" · ")[1] == "Ohio State @ Michigan"
    assert labels[2].split(" · ")[1] == "Alabama @ Auburn"


def test_a_single_league_snapshot_drops_the_repeated_sport_prefix(tmp_path):
    """Phone-first. A Saturday snapshot is routinely all NCAAF, and prefixing
    every one of 77 rows with the same league name spends width that the
    kickoff then loses to truncation. The prefix is there to tell sports
    apart; with one sport there is nothing to tell apart."""
    snap = _snapshot_of_many_games()
    for row in snap["opportunities"] + snap["candidates"]:
        row["sport_key"] = "americanfootball_ncaaf"
        row["sport_title"] = "NCAAF"

    st = FakeStreamlit()
    seen = _spy_on_the_game_multiselect(st)
    run_page(snap, tmp_path=tmp_path, st=st)

    labels = seen["labels"]
    assert not any(x.startswith("NCAAF") for x in labels), \
        f"one league, so the league name is noise: {labels}"
    osu = next(x for x in labels if "Ohio State" in x)
    matchup, kickoff = osu.split(" · ")
    assert matchup == "Ohio State @ Michigan"
    assert kickoff, "the kickoff must survive dropping the sport"


def test_a_stale_game_id_in_the_url_does_not_take_the_page_down(tmp_path):
    """A bookmarked link outlives the snapshot it was made from. Real
    Streamlit raises StreamlitAPIException on a multiselect `default` that is
    not among its `options` -- a white screen for what is only an old URL --
    so the seed is intersected with what this snapshot actually has."""
    st = FakeStreamlit()
    st.query_params["arb_games"] = "g_osu,g_from_last_saturday"
    seen = _spy_on_the_game_multiselect(st)
    run_page(_snapshot_of_many_games(), tmp_path=tmp_path, st=st)

    assert st.drawn, "a stale game id in the URL took the page down"
    assert seen["default"] == ["g_osu"], \
        "the dead id must be dropped and the live one still seed the widget"


def test_the_game_filter_is_remembered_in_the_url(tmp_path):
    """Same reasoning as the boost panels: `st.session_state` dies when a
    phone backgrounds the tab, and this filter is set precisely so the reader
    can go and place the bet in the book's own app. The URL survives that."""
    st = run_page(_snapshot_of_many_games(), tmp_path=tmp_path,
                  answers={"Filter by game": ["g_osu", "g_nyy"]})

    assert st.query_params.get("arb_games") == "g_osu,g_nyy"


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
        "Boost 1 %": 50,
    })
    captions = _captions(st)
    assert any("near A @ near B" in c for c in captions)
    assert not any("far A @ far B" in c for c in captions), \
        "the +10-day fixture leaked past a range that only covers the near one"


def test_live_games_are_hidden_by_default_and_shown_when_toggled_on(tmp_path):
    snap = _snapshot_with_a_live_game()

    hidden = run_page(snap, tmp_path=tmp_path, answers={"Boost 1 %": 50})
    captions = _captions(hidden)
    assert any("near A @ near B" in c for c in captions)
    assert not any("far A @ far B" in c for c in captions), \
        "a live game leaked past the default (Include live is off)"

    shown = run_page(snap, tmp_path=tmp_path, answers={
        "Boost 1 %": 50, "Include live/in-progress games": True})
    assert any("far A @ far B" in c for c in _captions(shown)), \
        "turning the toggle on must surface the live game"


def test_a_corrupt_snapshot_does_not_take_the_page_down(tmp_path):
    (tmp_path / "arb_snapshot.json").write_text("{ this is not json")
    st = run_page(None, tmp_path=tmp_path)
    assert st.drawn


# --- the empty board has to say WHICH kind of empty it is --------------------
# Added 2026-09-11. Every empty board rendered the same sentence: "days with
# no arbitrage are normal". On 2026-09-10 the snapshot held 30 opportunities
# and every one had already kicked off, so the page reassured its reader that
# there was nothing to find while the real answer was "this scan is old, tap
# Scan". A reassurance shown in place of an action costs the whole session.

def _all_live_snapshot() -> dict:
    """Every opportunity already under way -- yesterday's scan, read today."""
    snap = _snapshot(n_opps=3, n_cands=0)
    live_ct = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    for o in snap["opportunities"]:
        o["commence_time"] = live_ct
    return snap


def _warnings(st) -> str:
    return " ".join(str(a) for kind, a in st.drawn if kind == "warning")


def test_an_all_live_board_says_the_scan_is_stale_not_that_nothing_was_found(tmp_path):
    st = run_page(_all_live_snapshot(), tmp_path=tmp_path)
    msg = _warnings(st)

    assert "already kicked off" in msg
    assert "stale scan, not a quiet board" in msg
    assert "days with no" not in msg, (
        "the reassurance must not be shown when the real problem is an old scan")


def test_a_genuinely_empty_scan_still_gets_the_reassurance(tmp_path):
    """The other half: when the scan really did find nothing, saying so is
    correct and the user should not be sent chasing a fresh scan."""
    st = run_page(_snapshot(n_opps=0, n_cands=0), tmp_path=tmp_path)
    msg = _warnings(st)

    assert "days with no" in msg
    assert "already kicked off" not in msg


# --- scoping the BOOST to a game, not just the display ----------------------
# The display filter above hides rows after the fact. This scopes the SEARCH:
# `Boost.events` tells the engine the token is only good on one game, so a
# boosted arbitrage is never reported in a game the token cannot be spent on.

def _candidates_only(snap: dict) -> dict:
    """Drop the opportunities so the only matchups drawn come from the boost
    panel. Without this the plain board below renders all three games too
    and "did the panel narrow" cannot be read off the output."""
    snap = dict(snap)
    snap["opportunities"] = []
    return snap


def test_scoping_a_boost_to_a_game_narrows_the_boost_panel(tmp_path):
    st = run_page(_candidates_only(_snapshot_of_many_games()), tmp_path=tmp_path,
                  answers={"Boost 1 %": 50, "Boost 1 game": "g_osu"})
    text = " ".join(str(a) for k, a in st.drawn if k in ("markdown", "caption"))

    assert "Ohio State @ Michigan" in text
    assert "Alabama @ Auburn" not in text, \
        "the token was priced against a game it is not valid on"
    assert "Yankees @ Red Sox" not in text


def test_a_boost_with_no_game_still_reaches_every_game(tmp_path):
    """The control for the test above: the picker defaults to every game and
    must not quietly narrow anything."""
    st = run_page(_candidates_only(_snapshot_of_many_games()), tmp_path=tmp_path,
                  answers={"Boost 1 %": 50})
    text = " ".join(str(a) for k, a in st.drawn if k in ("markdown", "caption"))

    for matchup in ("Ohio State @ Michigan", "Alabama @ Auburn", "Yankees @ Red Sox"):
        assert matchup in text, f"{matchup} should still be priced"


def test_a_game_scoped_boost_says_so_where_the_token_is_named(tmp_path):
    """The token's own description carries the game, so every place the page
    names the token says which one it is. "50% boost on DraftKings" against
    a one-game token is an overstatement of what you are holding.

    Read off the nothing-qualifies warning, which is one of the two places
    the page prints a token's description; the boosted-middle rows print the
    same string in their "Needs ... applied" line."""
    st = run_page(_candidates_only(_snapshot_of_many_games()), tmp_path=tmp_path,
                  answers={"Boost 1 %": 50, "Boost 1 game": "g_osu",
                           "Minimum %": 90.0})
    msg = _warnings(st)

    assert "No market clears" in msg, "fixture: nothing should clear 90%"
    assert "Ohio State @ Michigan only" in msg, \
        f"the token was named without its game: {msg}"


def test_a_boost_scoped_to_a_game_and_a_sport_it_is_not_in_warns(tmp_path):
    """Both terms have to be satisfied, so the pair can never match. The
    engine just ANDs them and finds nothing; the page should say why rather
    than leave an empty panel looking broken."""
    st = run_page(_snapshot_of_many_games(), tmp_path=tmp_path, answers={
        "Boost 1 %": 50,
        "Boost 1 game": "g_nyy",              # MLB
        "Boost 1 sport": "americanfootball_ncaaf",
    })
    captions = _captions(st)

    assert any("Yankees @ Red Sox" in c and "NCAAF" in c for c in captions), \
        "a game/sport pair that can never match should be called out"


def test_the_display_filter_and_the_boost_scope_are_independent(tmp_path):
    """Two different controls answering two different questions: one hides
    rows, the other tells the search which game the token is good on. Using
    both at once must narrow to their intersection, not fight."""
    snap = _snapshot_of_many_games()
    st = run_page(snap, tmp_path=tmp_path, answers={
        "Boost 1 %": 50,
        "Boost 1 game": "g_osu",
        "Filter by game": ["g_osu"],
    })
    text = " ".join(str(a) for k, a in st.drawn if k in ("markdown", "caption"))

    assert "Ohio State @ Michigan" in text
    assert "Alabama @ Auburn" not in text
    assert "Yankees @ Red Sox" not in text
