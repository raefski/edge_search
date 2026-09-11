"""Tests for the CBS scrapers (edge/pickem_cbs.py)."""
from edge.pickem_cbs import (
    NICK_TO_ABBR, parse_odds_tables, parse_pool_text, parse_spread, parse_total,
)


def test_parse_spread_ignores_american_prices():
    # the odds page puts the price on the second line of the same cell, and
    # bare price cells appear in the moneyline column -- neither is a spread
    assert parse_spread("-3.5\n-110") == -3.5
    assert parse_spread("+3.5\n-108") == 3.5
    assert parse_spread("-180") is None          # moneyline, not a spread
    assert parse_spread("+162") is None
    assert parse_spread("PK") == 0.0
    assert parse_spread("") is None


def test_parse_total_handles_over_under_prefixes():
    assert parse_total("o44.5\n-108") == 44.5
    assert parse_total("u44.5") == 44.5
    assert parse_total("") is None


def _tbl(extra_col=False):
    """Odds-page shape. CBS inserts a 'Final' score column during and after
    a game week, which shifts every index -- the regression this guards."""
    hdr = ["Wed Sep 9, 8:20pm"] + (["Final"] if extra_col else []) + \
          ["Open", "Spread", "Moneyline", "Total"]
    away = ["Patriots"] + (["0"] if extra_col else []) + \
           ["o44.5\n-111", "+3.5\n-108", "+162", "o44.5\n-108"]
    home = ["Seahawks"] + (["0"] if extra_col else []) + \
           ["-3.5\n-110", "-4.0\n-110", "-180", "u44.5\n-105"]
    return [hdr, away, home]


def test_parse_odds_tables_reads_columns_by_header_not_position():
    without = parse_odds_tables([_tbl(False)])[0]
    with_final = parse_odds_tables([_tbl(True)])[0]
    for g in (without, with_final):
        assert g.away_abbr == "NE" and g.home_abbr == "SEA"
        assert g.open_line == -3.5
        assert g.current_line == -4.0     # would be the score column if indexed by position
        assert g.total == 44.5


def test_parse_odds_tables_skips_malformed_tables():
    assert parse_odds_tables([[["hdr"]], []]) == []


def test_parse_pool_text_extracts_line_and_community_split():
    g = parse_pool_text("PATRIOTS 0-0 30% +3.5 AT -3.5 70% SEAHAWKS 0-0")[0]
    assert (g["away_abbr"], g["home_abbr"]) == ("NE", "SEA")
    assert g["cbs_line_home"] == -3.5
    assert (g["comm_pct_away"], g["comm_pct_home"]) == (30, 70)


def test_parse_pool_text_handles_an_away_favorite():
    # the sign that is easiest to get backwards: road team laying the points
    g = parse_pool_text("RAVENS 0-0 68% -3.5 AT +3.5 32% COLTS 0-0")[0]
    assert g["home_abbr"] == "IND"
    assert g["cbs_line_home"] == 3.5      # home team RECEIVING points


def test_parse_pool_text_reads_a_multi_game_page():
    txt = ("Wed @ 8:20 PM NBC\nMatchup Analysis\n"
           "PATRIOTS 0-0 30% +3.5 AT -3.5 70% SEAHAWKS 0-0\n"
           "Thu @ 8:35 PM NFLX\n"
           "49ERS 0-0 19% +3.5 AT -3.5 81% RAMS 0-0\n")
    gs = parse_pool_text(txt)
    assert [g["home_abbr"] for g in gs] == ["SEA", "LAR"]
    assert gs[1]["away_abbr"] == "SF"     # '49ERS' must not fall through title-casing


def test_parse_pool_text_returns_nothing_for_an_unrelated_page():
    assert parse_pool_text("Pool Settings Entry Fee $150 Weekly Payout") == []


def test_every_nfl_team_maps_to_a_unique_abbreviation():
    assert len(NICK_TO_ABBR) == 32
    assert len(set(NICK_TO_ABBR.values())) == 32


# ---------------------------------------------------------------------------
# Automated pool fetch (edge.pickem_cbs.fetch_pool_text / SessionExpired)
# ---------------------------------------------------------------------------
import sys
import types

import pytest

from edge import pickem_cbs
from edge.pickem_cbs import SessionExpired, fetch_pool_text, pool_url


def test_env_prefers_the_process_environment(monkeypatch):
    monkeypatch.setenv("CBS_POOL_URL", "https://example.test/from-env")
    assert pool_url() == "https://example.test/from-env"


def test_env_falls_back_to_this_repos_dot_env(tmp_path, monkeypatch):
    """`_env` mirrors scripts/odds_parity.py's ODDS_API_KEY lookup order --
    environment, then this repo's .env -- rather than inventing a second
    convention. Points the module's own __file__ into a scratch tree so this
    cannot read the real edge_search/.env."""
    monkeypatch.delenv("CBS_POOL_URL", raising=False)
    (tmp_path / "edge").mkdir()
    (tmp_path / ".env").write_text('CBS_POOL_URL="https://example.test/from-repo-env"\n')
    monkeypatch.setattr(pickem_cbs, "__file__", str(tmp_path / "edge" / "pickem_cbs.py"))
    assert pool_url() == "https://example.test/from-repo-env"


def test_env_falls_back_to_arbitrage_dot_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CBS_POOL_URL", raising=False)
    (tmp_path / "edge").mkdir()
    monkeypatch.setattr(pickem_cbs, "__file__", str(tmp_path / "edge" / "pickem_cbs.py"))
    monkeypatch.setattr(pickem_cbs.Path, "home", lambda: tmp_path)
    (tmp_path / "arbitrage").mkdir()
    (tmp_path / "arbitrage" / ".env").write_text("CBS_POOL_URL=https://example.test/from-arb\n")
    assert pool_url() == "https://example.test/from-arb"


def test_pool_url_is_none_with_nothing_set(tmp_path, monkeypatch):
    monkeypatch.delenv("CBS_POOL_URL", raising=False)
    (tmp_path / "edge").mkdir()
    monkeypatch.setattr(pickem_cbs, "__file__", str(tmp_path / "edge" / "pickem_cbs.py"))
    monkeypatch.setattr(pickem_cbs.Path, "home", lambda: tmp_path)
    assert pool_url() is None


def test_fetch_pool_text_refuses_a_missing_session_before_touching_playwright(tmp_path):
    """The existence check has to run BEFORE the playwright import, so a
    missing session reports plainly even on a machine without playwright
    installed -- this test asserts that ordering by never importing
    playwright at all and still getting a clean SessionExpired."""
    missing = tmp_path / "no_such_session.json"
    assert not missing.exists()
    with pytest.raises(SessionExpired, match="pickem_session_bootstrap"):
        fetch_pool_text("https://example.test/pool", session_path=missing)


class _FakePage:
    def __init__(self, url: str, text: str):
        self.url = url
        self._text = text

    def goto(self, *_a, **_k):
        pass

    def wait_for_timeout(self, *_a, **_k):
        pass

    def inner_text(self, *_a, **_k):
        return self._text


class _FakeContext:
    def __init__(self, page):
        self._page = page
        self.init_scripts = []

    def new_page(self):
        return self._page

    def add_init_script(self, script):
        self.init_scripts.append(script)


class _FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.contexts: list[_FakeContext] = []

    def new_context(self, **_k):
        ctx = _FakeContext(self._page)
        self.contexts.append(ctx)
        return ctx

    def close(self):
        pass


class _FakeChromium:
    def __init__(self, page):
        self._page = page
        self.launches: list[dict] = []
        self.browsers: list[_FakeBrowser] = []

    def launch(self, **kwargs):
        self.launches.append(kwargs)
        browser = _FakeBrowser(self._page)
        self.browsers.append(browser)
        return browser


class _FakeSyncPlaywright:
    def __init__(self, chromium):
        self._chromium = chromium

    def __enter__(self):
        return types.SimpleNamespace(chromium=self._chromium)

    def __exit__(self, *_a):
        return False


def _stub_playwright(monkeypatch, page: _FakePage) -> _FakeChromium:
    """Injects a fake playwright.sync_api module so fetch_pool_text's lazy
    `from playwright.sync_api import sync_playwright` resolves to a stand-in
    landing on `page`, with no real browser and no real dependency needed.
    Returns the fake chromium recorder so a test can inspect launch args and
    the context that was created -- e.g. to confirm a stealth patch actually
    got applied, not merely that it exists somewhere in the source."""
    chromium = _FakeChromium(page)
    fake_module = types.ModuleType("playwright.sync_api")
    fake_module.sync_playwright = lambda: _FakeSyncPlaywright(chromium)
    fake_pkg = types.ModuleType("playwright")
    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    return chromium


def test_fetch_pool_text_raises_session_expired_on_a_join_redirect(tmp_path, monkeypatch):
    """A dead session and a live one both come back HTTP 200 from CBS --
    redirected to /join instead of 401'd (this module's own top docstring) --
    so the landed-on URL is the only reliable tell a caller has."""
    session = tmp_path / "session.json"
    session.write_text("{}")
    _stub_playwright(monkeypatch, _FakePage(
        url="https://picks.cbssports.com/join?reason=expired", text="join a pool"))
    with pytest.raises(SessionExpired, match="pickem_session_bootstrap"):
        fetch_pool_text("https://picks.cbssports.com/football/pickem/pools/1/picks",
                        session_path=session)


def test_fetch_pool_text_returns_the_pages_text_on_a_live_session(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    session.write_text("{}")
    picks_text = "PATRIOTS 0-0 30% +3.5 AT -3.5 70% SEAHAWKS 0-0"
    _stub_playwright(monkeypatch, _FakePage(
        url="https://picks.cbssports.com/football/pickem/pools/1/picks", text=picks_text))
    got = fetch_pool_text("https://picks.cbssports.com/football/pickem/pools/1/picks",
                          session_path=session)
    assert got == picks_text


def test_fetch_pool_text_hides_the_automation_tell(tmp_path, monkeypatch):
    """REGRESSION. CBS blocked Playwright's own browser outright before a
    human ever reached the login form, on the FIRST real bootstrap attempt
    (2026-09-10) -- confirming it actually checks `navigator.webdriver`, not
    a theoretical risk. Pins that the launch flag and the init-script patch
    are both actually applied, not merely present somewhere in the source."""
    session = tmp_path / "session.json"
    session.write_text("{}")
    chromium = _stub_playwright(monkeypatch, _FakePage(
        url="https://picks.cbssports.com/football/pickem/pools/1/picks", text="ok"))
    fetch_pool_text("https://picks.cbssports.com/football/pickem/pools/1/picks",
                    session_path=session)

    assert chromium.launches, "chromium.launch was never called"
    assert pickem_cbs.STEALTH_ARGS[0] in chromium.launches[0].get("args", [])

    assert chromium.browsers and chromium.browsers[0].contexts, \
        "new_context was never called"
    scripts = chromium.browsers[0].contexts[0].init_scripts
    assert scripts, "no stealth init script was injected into the context"
    assert "webdriver" in scripts[0]


# ---------------------------------------------------------------------------
# save_cookie_session -- the fallback that never needs Playwright to log in
# ---------------------------------------------------------------------------
import json as _json

from edge.pickem_cbs import save_cookie_session


def test_save_cookie_session_parses_a_real_cookie_header(tmp_path):
    """The exact shape DevTools' Network tab shows for a Cookie REQUEST
    header: semicolon-separated name=value pairs, no quoting, no metadata
    (that only appears on Set-Cookie, which this deliberately does not ask
    for)."""
    path = tmp_path / "session.json"
    save_cookie_session("sid=abc123; auth_token=xyz789; other=val",
                        session_path=path)
    state = _json.loads(path.read_text())
    names = {c["name"]: c["value"] for c in state["cookies"]}
    assert names == {"sid": "abc123", "auth_token": "xyz789", "other": "val"}
    assert state["origins"] == []


def test_save_cookie_session_writes_the_shape_playwright_expects(tmp_path):
    """new_context(storage_state=...) needs domain/path/secure/etc on every
    cookie, not just name/value -- a partial dict is silently ignored by
    Playwright rather than erroring, which would look identical to a wrong
    cookie."""
    path = tmp_path / "session.json"
    save_cookie_session("sid=abc123", session_path=path, domain=".cbssports.com")
    cookie = _json.loads(path.read_text())["cookies"][0]
    for key in ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite"):
        assert key in cookie
    assert cookie["domain"] == ".cbssports.com"


def test_save_cookie_session_chmods_the_file(tmp_path):
    path = tmp_path / "session.json"
    save_cookie_session("sid=abc123", session_path=path)
    assert (path.stat().st_mode & 0o777) == 0o600


def test_save_cookie_session_refuses_a_curl_command():
    """The exact wrong thing someone might paste instead -- a full 'curl
    ...' invocation has no bare name=value pairs split on ';', so this
    fails loudly rather than writing a garbage session."""
    with pytest.raises(ValueError, match="Cookie REQUEST header"):
        save_cookie_session("curl 'https://picks.cbssports.com/' -H 'authority: cbssports.com'")


def test_save_cookie_session_refuses_an_empty_paste():
    with pytest.raises(ValueError):
        save_cookie_session("")


def test_save_cookie_session_parses_a_copied_cookie_table(tmp_path):
    """DevTools' Application/Storage -> Cookies table, tab-separated, extra
    columns (Domain, Path, Expires, Size, HttpOnly, Secure, SameSite)
    beyond Name/Value -- exactly what selecting rows and copying produces."""
    path = tmp_path / "session.json"
    table = (
        "sid\tabc123\t.cbssports.com\t/\tSession\t6\ttrue\ttrue\tLax\n"
        "auth_token\txyz789\t.cbssports.com\t/\t2027-01-01T00:00:00.000Z\t6\ttrue\ttrue\tLax"
    )
    save_cookie_session(table, session_path=path)
    state = _json.loads(path.read_text())
    names = {c["name"]: c["value"] for c in state["cookies"]}
    assert names == {"sid": "abc123", "auth_token": "xyz789"}


def test_save_cookie_session_is_not_fooled_by_base64_padding_in_a_table_value(tmp_path):
    """REGRESSION. A table row's VALUE column can itself contain '=' (JWT/
    base64 padding), and that row has no ';' -- so a naive "try the Cookie
    header parser first" would see the whole line as one part, find an '='
    inside the padding, and silently split there instead of on tabs. The
    tab in the line is what has to decide the format, not the presence of
    '='."""
    path = tmp_path / "session.json"
    table = "authToken\tabcDEF==\t.cbssports.com\t/\tSession\t9\ttrue\ttrue\tLax"
    save_cookie_session(table, session_path=path)
    state = _json.loads(path.read_text())
    assert state["cookies"] == [{
        "name": "authToken", "value": "abcDEF==", "domain": ".cbssports.com",
        "path": "/", "expires": -1, "httpOnly": True, "secure": True,
        "sameSite": "Lax",
    }]
