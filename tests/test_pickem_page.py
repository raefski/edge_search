"""Run pages/4_🎯_Pickem.py top to bottom against a fake `streamlit`.

WHY THIS FILE EXISTS
The pick'em page is the artefact Adam actually reads on his phone on a Sunday
morning, and until 2026-09-09 it had ZERO test coverage -- 54 passing pick'em
tests executed not one line of it. Two live-affecting bugs shipped straight
through that gap:

  * the page joined a TWO-WEEK odds board by home team alone, with no slate
    filter and no CBS/market team alias. Five home teams appeared twice on the
    2026-09-08 board, so BUF@HOU resolved to CIN@HOU (Week 2) and the page
    printed a Texans pick where the Week 1 market says Bills -- a SIDE FLIP on
    the showcase play of the week. SF@LAR matched nothing at all, because the
    market spells the Rams "LA" and CBS spells them "LAR", and silently fell
    back to live_line = cbs_line (edge 0).
  * every row of data/pickem_current_week.csv was tagged `provisional`, and the
    note renderer suppressed exactly the word "provisional" -- so the one
    caveat that mattered was the one caveat that could never be displayed.

Same shape as tests/test_arb_page.py, whose harness this copies: stub
`streamlit`, exec the real page source, and assert on what it computed. The
assertions here are on the `(pool_line, live_line)` pairs handed to
`edge.pickem.make_pick`, NOT on HTML -- those two numbers are the entire model
input, so a test that pins them cannot be fooled by a cosmetic re-render.
"""
from __future__ import annotations

import csv
import io
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "4_🎯_Pickem.py"
sys.path.insert(0, str(ROOT))

CSV_FIELDS = ["week", "away_abbr", "home_abbr", "away_name", "home_name",
              "cbs_line_home", "kickoff_utc", "tv", "comm_pct_away",
              "comm_pct_home", "note"]


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


class _Col:
    """A column that records what was drawn into it.

    st.columns() returning bare no-ops would hide the "Expected wins" metric,
    which is one of the numbers a no-live-line game is supposed to change.
    """

    def __init__(self, drawn: list):
        self._drawn = drawn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        def draw(*a, **_k):
            self._drawn.append((name, a))
            return _Ctx()
        return draw


class FakeStreamlit(types.ModuleType):
    """Enough of Streamlit to execute the page and record what it drew."""

    def __init__(self, answers: dict | None = None):
        super().__init__("streamlit")
        self.answers = answers or {}
        self.drawn: list[tuple[str, object]] = []
        self.session_state: dict = {}
        self.secrets: dict = {}
        self.sidebar = _Ctx()
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
        return [_Col(self.drawn) for _ in range(n)]

    def set_page_config(self, *_a, **_k):
        return None

    def cache_data(self, *args, **kwargs):
        if args and callable(args[0]):
            return args[0]

        def wrap(fn):
            return fn
        return wrap

    cache_resource = cache_data

    # -- helpers for assertions -------------------------------------------
    def text_of(self, *kinds: str) -> str:
        """Everything the page drew of these kinds, concatenated."""
        want = set(kinds) if kinds else None
        return "\n".join(
            str(payload)
            for kind, payload in self.drawn
            if (want is None or kind in want) and payload is not None)


class FakeClient:
    """A `ScrapedOddsClient` stand-in that serves a fixed board.

    Only `get_featured_odds` is on the path edge.pickem_live.fetch_week takes,
    so only it needs to be real. Deliberately NOT named *SnapshotOddsClient*:
    the page branches on that class name to print a staleness caption.
    """

    def __init__(self, events: list[dict]):
        self.events = events
        self.spent_this_session = 0
        self.dry_run = False

    def remaining_credits(self):
        return None

    def get_featured_odds(self, sport, markets, regions="us"):
        return self.events


def event(away_name: str, home_name: str, kickoff: str, home_point: float,
          total: float = 44.0, books=("draftkings", "fanduel")) -> dict:
    """One Odds-API-shaped event, priced identically at every book.

    Equal prices across books make the consensus exactly `home_point`, so a
    test can assert on the number it wrote rather than on a weighted average
    it would have to recompute.
    """
    return {
        "home_team": home_name, "away_team": away_name,
        "commence_time": kickoff,
        "bookmakers": [
            {"key": b, "markets": [
                {"key": "spreads", "outcomes": [
                    {"name": home_name, "point": home_point},
                    {"name": away_name, "point": -home_point}]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "point": total},
                    {"name": "Under", "point": total}]},
            ]} for b in books],
    }


def run_page(csv_rows: list[dict], events: list[dict] | None,
             tmp_path, answers: dict | None = None,
             line_log: list[dict] | None = None,
             failures: str | None = None):
    """Execute the real page source. Returns (fake_st, make_pick_calls).

    `make_pick_calls` is a list of the kwargs-free positional arguments the
    page handed the model, which is the contract this file exists to pin.
    """
    import edge.odds.cli as odds_cli
    import edge.pickem
    import edge.pickem_log

    st = FakeStreamlit(answers)
    csv_path = Path(tmp_path) / "pickem_current_week.csv"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    w.writeheader()
    for r in csv_rows:
        w.writerow({k: r.get(k, "") for k in CSV_FIELDS})
    csv_path.write_text(buf.getvalue())

    src = PAGE.read_text()
    marker = 'CURRENT_WEEK_CSV = ROOT / "data" / "pickem_current_week.csv"'
    assert src.count(marker) == 1, "page moved its CBS-line path"
    src = src.replace(marker, f"CURRENT_WEEK_CSV = Path({str(csv_path)!r})")

    # The capture-failure banner reads a file written by
    # deploy/pickem-capture-failed@.service, so point it somewhere writable.
    fail_path = Path(tmp_path) / "pickem_capture_failures.log"
    if failures is not None:
        fail_path.write_text(failures)
    fmarker = 'FAILURES_LOG = ROOT / "data" / "pickem_capture_failures.log"'
    assert src.count(fmarker) == 1, "page moved its capture-failure path"
    src = src.replace(fmarker, f"FAILURES_LOG = Path({str(fail_path)!r})")

    calls: list[tuple] = []
    real_make_pick = edge.pickem.make_pick

    def recording_make_pick(*a, **k):
        pick = real_make_pick(*a, **k)
        calls.append((a, k, pick))
        return pick

    saved_st = sys.modules.get("streamlit")
    saved_client = odds_cli.scraped_client
    saved_make_pick = edge.pickem.make_pick
    saved_load = edge.pickem_log.load

    sys.modules["streamlit"] = st
    odds_cli.scraped_client = lambda *_a, **_k: FakeClient(events or [])
    edge.pickem.make_pick = recording_make_pick
    edge.pickem_log.load = lambda *_a, **_k: list(line_log or [])
    try:
        code = compile(src, str(PAGE), "exec")
        exec(code, {"__name__": "__main__", "__file__": str(PAGE)})
    except _Stopped:
        pass
    finally:
        if saved_st is not None:
            sys.modules["streamlit"] = saved_st
        else:
            sys.modules.pop("streamlit", None)
        odds_cli.scraped_client = saved_client
        edge.pickem.make_pick = saved_make_pick
        edge.pickem_log.load = saved_load
    return st, calls


def picks_by_home(calls) -> dict:
    """{home_name: Pick} for every game the page scored."""
    return {a[1]: pick for a, _k, pick in calls}


# --------------------------------------------------------------- fixtures
def _week1_csv() -> list[dict]:
    """Two real Week-1 rows, verbatim from data/pickem_current_week.csv."""
    return [
        {"week": 1, "away_abbr": "SF", "home_abbr": "LAR", "away_name": "49ers",
         "home_name": "Rams", "cbs_line_home": -3.5,
         "kickoff_utc": "2026-09-11T00:35:00Z", "tv": "Netflix",
         "comm_pct_away": 19, "comm_pct_home": 81, "note": ""},
        {"week": 1, "away_abbr": "BUF", "home_abbr": "HOU", "away_name": "Bills",
         "home_name": "Texans", "cbs_line_home": -1.5,
         "kickoff_utc": "2026-09-13T17:00:00Z", "tv": "CBS",
         "comm_pct_away": 78, "comm_pct_home": 22, "note": ""},
    ]


def _two_week_board() -> list[dict]:
    """The 2026-09-08 board's shape: Week 1 and Week 2 on one feed.

    HOU hosts in both weeks -- one of the five duplicated home teams that made
    the home-team-only join pick the wrong game -- and the Rams event carries
    the market's spelling ("Los Angeles Rams" -> LA), not CBS's (LAR).
    """
    return [
        # Week 1 (pool week 1 = Tue 2026-09-08 -> Tue 2026-09-15 ET)
        event("Buffalo Bills", "Houston Texans", "2026-09-13T17:00:00Z", 1.5),
        event("San Francisco 49ers", "Los Angeles Rams",
              "2026-09-11T00:35:00Z", -4.0, total=48.5),
        # Week 2 -- must be filtered out before any join happens
        event("Cincinnati Bengals", "Houston Texans",
              "2026-09-20T17:01:00Z", -2.5),
        event("New York Giants", "Los Angeles Rams",
              "2026-09-22T00:16:00Z", -9.5),
    ]


# ------------------------------------------------------- the finding-1 case
def test_page_uses_the_selected_weeks_line_and_resolves_the_rams(tmp_path):
    """REGRESSION, live-affecting, found 2026-09-09.

    Joining a two-week board by home team alone gave BUF@HOU the Week 2
    CIN@HOU line (-2.5) instead of the Week 1 one (+1.5). CBS froze Houston
    -1.5; the Week 1 market has Houston +1.5, which is a FAVOURITE FLIP -- the
    strongest pattern in the model -- and the page printed a Texans LEAN.

    And the Rams, whom the market calls LA and CBS calls LAR, matched nothing
    at all, so the page silently used live_line = cbs_line: a zero edge that
    looks exactly like a game the market agrees with.
    """
    st, calls = run_page(_week1_csv(), _two_week_board(), tmp_path)

    by_home = picks_by_home(calls)
    assert set(by_home) == {"Texans", "Rams"}, "one card per CBS row, no more"

    hou = by_home["Texans"]
    assert hou.pool_line == -1.5
    assert hou.live_line == 1.5, "Week 2's CIN@HOU line must not leak in"
    assert hou.side == "away" and hou.tier == "STRONG", "BUF +1.5 STRONG"

    rams = by_home["Rams"]
    assert rams.pool_line == -3.5
    assert rams.live_line == -4.0, "LA (market) must alias onto LAR (CBS)"
    assert rams.live_line != rams.pool_line, "an unmatched team fakes a 0 edge"


def test_page_refuses_an_ambiguous_board_rather_than_guessing(tmp_path):
    """If two games for one home team survive the week filter, stop.

    edge.pickem_free.filter_to_slate raises on that, and the page must surface
    the refusal rather than swallow it -- guessing is what produced the wrong
    side in the first place.
    """
    board = _two_week_board() + [
        # a second Week-1 Texans game: impossible, therefore the window is wrong
        event("Cleveland Browns", "Houston Texans", "2026-09-14T17:00:00Z", -7.0),
    ]
    st, calls = run_page(_week1_csv(), board, tmp_path)

    assert calls == [], "no pick may be computed off an ambiguous board"
    errors = st.text_of("error")
    assert "HOU" in errors and "more than once" in errors


# ------------------------------------------------------- the finding-4 cases
def test_every_game_is_flagged_when_no_live_line_exists(tmp_path):
    """An empty board is a data outage, not a slate the market agrees with.

    REWRITTEN 2026-09-10. This test used to assert the very behaviour that
    turned out to be the bug -- that the page still produced a Pick per game
    with `pick.live_line == pick.pool_line`. That fabricated 0 edge is exactly
    what routes into the coin-flip fallback and prints CBS's own favourite. The
    contract now is: a game with no reading gets NO pick, and every one of them
    is badged.
    """
    st, calls = run_page(_week1_csv(), [], tmp_path)

    assert calls == [], "the model must not be asked about an unpriced game"

    body = st.text_of("markdown")
    assert body.count("NO READING") == 2, "one per game, in the pick box"

    metrics = [a for kind, a in st.drawn if kind == "metric"]
    expected = next(a[1] for a in metrics if a[0] == "Expected wins")
    assert expected.startswith("1.0 /"), "two unpriced games count 0.5 each"


def test_provisional_notes_are_shown_not_hidden(tmp_path):
    """The note filter suppressed the single word that mattered.

    `if r.get("note") and "provisional" not in r["note"]` meant a row flagged
    `provisional -- re-verify Tue Sep 8 after 1pm ET` rendered NO note, so a
    CSV last touched on Aug 20 looked identical to a verified one.
    """
    rows = _week1_csv()
    rows[1]["note"] = "provisional; CBS froze Texans favored -- watch for a flip"
    st, _calls = run_page(rows, _two_week_board(), tmp_path)

    body = st.text_of("markdown")
    assert "watch for a flip" in body, "the note must render"

    errors = st.text_of("error")
    assert "provisional" in errors.lower(), "and be called out at the top"
    assert "pickem_pool_import" in errors, "with the remedy named"


# ------------------------------------------------- the finding-4 case (day)
def test_day_headers_are_eastern_not_utc(tmp_path):
    """REGRESSION, live-affecting, found 2026-09-09 (iteration 2).

    `day_label = r.get("kickoff_utc","")[:10]` is the first ten characters of a
    UTC timestamp, and every NFL night game kicks off after 00:00 UTC. On the
    Week 1 slate that is four of sixteen games -- and they are exactly the four
    that carry their own separate pool deadline:

        NE@SEA  2026-09-10T00:20:00Z -> "2026-09-10", really Wed 09-09 8:20pm ET
        SF@LAR  2026-09-11T00:35:00Z -> "2026-09-11", really Thu 09-10 8:35pm ET
        DAL@NYG 2026-09-14T00:20:00Z -> "2026-09-14", really Sun 09-13 8:20pm ET
        DEN@KC  2026-09-15T00:15:00Z -> "2026-09-15", really Mon 09-14 8:15pm ET

    The TOO-GOODE pool's deadline is PER DAY, and PICKEM_WEEKLY.md section 2
    calls an empty day "the one truly fatal mistake". A page that files
    Sunday's night game under Monday and Monday's under Tuesday is telling the
    reader they have a day longer than they do.
    """
    rows = [
        {"week": 1, "away_abbr": "NE", "home_abbr": "SEA", "away_name": "Patriots",
         "home_name": "Seahawks", "cbs_line_home": -3.5,
         "kickoff_utc": "2026-09-10T00:20:00Z", "tv": "NBC",
         "comm_pct_away": 30, "comm_pct_home": 70, "note": ""},
        {"week": 1, "away_abbr": "DEN", "home_abbr": "KC", "away_name": "Broncos",
         "home_name": "Chiefs", "cbs_line_home": -2.5,
         "kickoff_utc": "2026-09-15T00:15:00Z", "tv": "ABC",
         "comm_pct_away": 51, "comm_pct_home": 49, "note": ""},
    ]
    st, calls = run_page(rows, [], tmp_path)
    drawn = st.text_of("markdown")

    assert '<div class="pk-day">Wed Sep 9</div>' in drawn
    assert '<div class="pk-day">Mon Sep 14</div>' in drawn
    assert "2026-09-10" not in drawn, "the UTC calendar date is a day early"
    assert "2026-09-15" not in drawn


def test_an_unparseable_kickoff_still_gets_a_header(tmp_path):
    """Falling back to the raw string keeps a bad row visible.

    A day with no header would collapse into the previous day's group, which
    is the failure mode this whole finding is about.
    """
    rows = [{"week": 1, "away_abbr": "NE", "home_abbr": "SEA",
             "away_name": "Patriots", "home_name": "Seahawks",
             "cbs_line_home": -3.5, "kickoff_utc": "sometime Sunday",
             "tv": "NBC", "comm_pct_away": 30, "comm_pct_home": 70, "note": ""}]
    st, _ = run_page(rows, [], tmp_path)
    assert '<div class="pk-day">sometime Sunday</div>' in st.text_of("markdown")


def test_the_week_selector_defaults_to_the_current_week(tmp_path, monkeypatch):
    """From Week 2 on, the page opened on Week 1 and looked empty.

    `edge.pickem_week.current_week` was written on 2026-09-09 for the timers'
    `--week auto` and the page never imported it, so `value=1` was hard-wired.
    scripts/pickem_pool_import.py truncates data/pickem_current_week.csv to the
    week it imports, so from Week 2 the page would render "No Week 1 lines
    captured yet" with that week's slate sitting in the file it just read.
    """
    import edge.pickem_week

    monkeypatch.setattr(edge.pickem_week, "current_week", lambda *a, **k: 3)

    rows = [{"week": 3, "away_abbr": "BUF", "home_abbr": "HOU",
             "away_name": "Bills", "home_name": "Texans", "cbs_line_home": -1.5,
             "kickoff_utc": "2026-09-27T17:00:00Z", "tv": "CBS",
             "comm_pct_away": 78, "comm_pct_home": 22, "note": ""}]
    board = [event("Buffalo Bills", "Houston Texans",
                   "2026-09-27T17:00:00Z", -2.5)]

    st, calls = run_page(rows, board, tmp_path)
    assert calls, "the page stopped instead of rendering the current week"
    (a, _k, _p) = calls[0]
    assert a[1] == "Texans" and a[2] == -1.5
    assert a[3] == -2.5, "and it joined the week-3 board, not week 1"


def test_a_recorded_capture_failure_is_shown_at_the_top(tmp_path):
    """A silently missed capture is a permanently missing reading.

    deploy/pickem-capture-failed@.service appends one line per failed run.
    Nothing else reads that file, and the person who would act on it reads this
    page -- so it surfaces here, above the picks, not in a journal.
    """
    st, _ = run_page(_week1_csv(), _two_week_board(), tmp_path,
                     failures="2026-09-13T16:00:12Z | lock-sun | exit-code\n")
    assert "lock-sun" in st.text_of("error")
    assert "pickem_capture" in st.text_of("error", "caption", "code")


def test_no_failure_banner_when_nothing_has_failed(tmp_path):
    st, _ = run_page(_week1_csv(), _two_week_board(), tmp_path)
    assert "lock-sun" not in st.text_of("error")


# ------------------------------------------- the iteration-3 finding-1 cases
#
# A game with no market reading used to be handed `live_line = cbs_line`, which
# is an edge of exactly 0. edge.pickem._coinflip_side then returns the MARKET
# FAVOURITE -- and with no market, "the favourite" is whichever side CBS froze
# as favourite, i.e. the OPPOSITE of the side the market had moved to. Verified
# on the real 2026 Week 1 board:
#
#   BUF@HOU: with board -> BUF STRONG 59% | board MISSING -> HOU COIN FLIP 50%
#   ARI@LAC: with board -> ARI LEAN  53% | board MISSING -> LAC COIN FLIP 50%
#   NYJ@TEN: with board -> NYJ LEAN  53% | board MISSING -> TEN COIN FLIP 50%
#
# This is live right now: NE@SEA dropped off the books' board at kickoff, and
# by Sunday 4:30pm ten of sixteen cards are in this state. The `no live line`
# badge is a 10px pill; the flipped team sat in the 15px bold `pk-pickbox`.
# On a phone that reads as a pick.

def _hou_dropped_board():
    """The Week-1 board with Houston's game pulled -- what a book does when a
    game kicks off, or when it takes a game down on QB news."""
    return [e for e in _two_week_board() if not (
        e["home_team"] == "Houston Texans"
        and e["commence_time"].startswith("2026-09-13"))]


def test_a_game_with_no_market_reading_gets_no_pick_at_all(tmp_path):
    """REGRESSION, live-affecting, found 2026-09-10.

    With no reading there is nothing to pick FROM, so the card must show no
    side, no tier and no probability -- and the model must never be asked.
    Handing it (cbs_line, cbs_line) is not a neutral no-op: it is a request for
    the coin-flip fallback, which answers with CBS's own favourite.
    """
    st, calls = run_page(_week1_csv(), _hou_dropped_board(), tmp_path)

    by_home = picks_by_home(calls)
    assert "Texans" not in by_home, "no pick may be computed without a reading"
    assert set(by_home) == {"Rams"}, "the game that HAS a reading still renders"

    for (a, _k, _p) in calls:
        assert not (a[2] == a[3] == -1.5), \
            "make_pick was handed (cbs_line, cbs_line) -- the fabricated 0 edge"

    body = st.text_of("markdown")
    card = body.split("Bills")[1] if "Bills" in body else ""
    assert card, "the matchup itself must still be shown"
    # the pick box, and only the pick box, is what a phone reads as the answer
    assert "NO READING" in body
    assert 'class="pk-pickbox">Texans' not in body
    assert 'class="pk-pickbox">Bills' not in body


def test_the_flipped_side_can_never_reach_the_pick_box(tmp_path):
    """The concrete flip, pinned end to end.

    With the board, BUF@HOU is a Bills STRONG (CBS froze Houston -1.5, the
    market has Houston +1.5 -- a favourite flip, the strongest pattern in the
    model). With the board missing, the old page printed a Texans COIN FLIP:
    the exact opposite side, in the same bold box, at 50%.
    """
    lit, lit_calls = run_page(_week1_csv(), _two_week_board(), tmp_path)
    dark, dark_calls = run_page(_week1_csv(), _hou_dropped_board(), tmp_path)

    hou = picks_by_home(lit_calls)["Texans"]
    assert hou.side == "away" and hou.tier == "STRONG", "sanity: the lit card"
    assert 'class="pk-pickbox">Bills +1.5' in lit.text_of("markdown")

    body = dark.text_of("markdown")
    assert "Texans -1.5<" not in body.replace("</span>", "<")
    assert "COIN FLIP" not in body.split("Bills")[1].split("</div>\n</div>")[0]


def test_a_dark_card_shows_no_probability(tmp_path):
    """50% is a model output. A game the model never saw has none."""
    st, _ = run_page(_week1_csv(), [], tmp_path)
    body = st.text_of("markdown")
    assert "50%" not in body, "an outage must not render as a modelled 50%"
    assert body.count("NO READING") == 2, "one per unpriced game"


def test_no_reading_games_are_not_counted_as_coin_flips(tmp_path):
    """The Coin flips metric lumped them in.

    A coin flip is a game the model looked at and found no edge on. A dark game
    is a game the model never saw. Adding them makes the one number that says
    'how much of this slate is guesswork' unable to tell the two apart.
    """
    st, _ = run_page(_week1_csv(), _hou_dropped_board(), tmp_path)
    metrics = {a[0]: a[1] for kind, a in st.drawn if kind == "metric"}
    assert metrics["Coin flips"] == 0, \
        "SF@LAR is a real pick and BUF@HOU has no reading -- neither is a flip"


def test_expected_wins_still_books_a_dark_game_at_a_flat_half(tmp_path):
    """You still have to submit a pick, so a dark game is worth 0.5 -- but the
    caption has to say that is an outage, not an edge."""
    st, _ = run_page(_week1_csv(), [], tmp_path)
    metrics = [a for kind, a in st.drawn if kind == "metric"]
    expected = next(a[1] for a in metrics if a[0] == "Expected wins")
    assert expected.startswith("1.0 /"), "two unpriced games count 0.5 each"


def test_a_played_game_reads_as_played_not_as_a_data_gap(tmp_path):
    """Books pull a game at kickoff, so every finished game goes dark.

    'Not on the board' sends Adam to re-collect the odds; 'kicked off' tells
    him nothing is wrong. Same missing data, opposite remedy -- and by Sunday
    4:30pm ten of sixteen cards are in this state.
    """
    rows = [
        {"week": 1, "away_abbr": "NE", "home_abbr": "SEA", "away_name": "Patriots",
         "home_name": "Seahawks", "cbs_line_home": -3.5,
         "kickoff_utc": "2020-09-10T00:20:00Z", "tv": "NBC",
         "comm_pct_away": 30, "comm_pct_home": 70, "note": ""},
        {"week": 1, "away_abbr": "BUF", "home_abbr": "HOU", "away_name": "Bills",
         "home_name": "Texans", "cbs_line_home": -1.5,
         "kickoff_utc": "2099-09-13T17:00:00Z", "tv": "CBS",
         "comm_pct_away": 78, "comm_pct_home": 22, "note": ""},
    ]
    st, calls = run_page(rows, [], tmp_path)
    assert calls == [], "neither game has a reading"

    body = st.text_of("markdown")
    played = body.split("Patriots")[1].split("Bills")[0]
    upcoming = body.split("Bills")[1]
    assert "kicked off" in played.lower() or "played" in played.lower()
    assert "not on the board" in upcoming.lower()
    assert "not on the board" not in played.lower()

    warn = st.text_of("warning")
    assert "1" in warn and "board" in warn.lower()


def test_a_live_card_keeps_its_pick_tier_and_probability(tmp_path):
    """The other half of the contract: nothing about a priced card changes."""
    st, calls = run_page(_week1_csv(), _two_week_board(), tmp_path)
    body = st.text_of("markdown")

    assert 'class="pk-pickbox">Bills +1.5' in body
    assert "59%" in body and "STRONG" in body
    assert "NO READING" not in body
    assert body.count('class="pk-nolive"') == 0
    assert len(calls) == 2
