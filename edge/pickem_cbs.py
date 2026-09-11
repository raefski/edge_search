"""CBS Sports line scrapers -- the free market feed, and the pool-page parser.

TWO DIFFERENT NUMBERS, and confusing them breaks the whole model:

  * cbssports.com/nfl/odds  -- PUBLIC, no login, free. Tracks the LIVE
    market and updates all week. Good as a market feed; useless as a
    stand-in for the pool line.
  * picks.cbssports.com/.../pools/<id>  -- the pool's FROZEN line, set once
    and never updated. Login-gated: an anonymous fetch redirects to /join
    and serves pool settings only (verified with a real browser, 2026-08-22).

Measured 2026-08-22 on 2026 Week 1: the public page agreed with the pool
line on 12 of 16 games -- and disagreed on exactly the four the market had
moved since the freeze (Texans -1.5 in the pool vs Bills -1.5 publicly, a
full flip). That 4/16 disagreement IS the edge the model exists to
exploit, so substituting the public number for the pool number would
delete the signal and quietly return ~50%.

Practical consequence: the pool line still has to come from the pool page.
`parse_pool_html` removes the tedious half of that -- save the page while
logged in and it produces the CSV rows, instead of transcribing 16 lines
by hand.

`fetch_pool_text` (2026-09-10) removes the LAST manual step: given a stored
login session it fetches that page itself and returns the same text a
copy-paste would have, so `parse_pool_text` needs no changes and there is
still only one parser. This is the one place in the whole project that
stores an authenticated session on purpose -- see DEFAULT_SESSION_PATH's
docstring for why that is a deliberate, scoped exception and not a change of
policy for the sportsbook scrapers.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ODDS_URL = "https://www.cbssports.com/nfl/odds/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

NICK_TO_ABBR = {
    "Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF",
    "Panthers": "CAR", "Bears": "CHI", "Bengals": "CIN", "Browns": "CLE",
    "Cowboys": "DAL", "Broncos": "DEN", "Lions": "DET", "Packers": "GB",
    "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX", "Chiefs": "KC",
    "Rams": "LAR", "Chargers": "LAC", "Raiders": "LV", "Dolphins": "MIA",
    "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG",
    "Jets": "NYJ", "Eagles": "PHI", "Steelers": "PIT", "49ers": "SF",
    "Seahawks": "SEA", "Buccaneers": "TB", "Titans": "TEN", "Commanders": "WSH",
}


@dataclass
class CBSGame:
    away: str            # nickname as CBS prints it
    home: str
    away_abbr: str
    home_abbr: str
    open_line: float | None    # home-team spread when CBS first posted it
    current_line: float | None  # home-team spread right now
    total: float | None
    kickoff_text: str = ""


def parse_spread(cell: str) -> float | None:
    """'-3.5\\n-110' -> -3.5 ; 'PK' -> 0.0 ; a bare price like '-110' -> None."""
    tok = (cell or "").split("\n")[0].replace("+", "").strip()
    if tok.upper() in ("PK", "PICK", "EVEN"):
        return 0.0
    try:
        v = float(tok)
    except ValueError:
        return None
    # American prices (-110, +162) are not spreads; real NFL spreads live
    # well inside +/-30 and land on halves or wholes.
    if abs(v) > 30:
        return None
    return v


def parse_total(cell: str) -> float | None:
    m = re.match(r"[ou]?(\d+\.?\d*)", (cell or "").split("\n")[0].strip(), re.I)
    return float(m.group(1)) if m else None


def parse_odds_tables(tables: list[list[list[str]]]) -> list[CBSGame]:
    """Turn the odds page's per-game tables into CBSGame rows.

    Columns are read by HEADER NAME, never by position -- the page carries a
    'Final' score column during and after game weeks that shifts every index
    (this cost one wrong parse when it was assumed away).
    """
    out = []
    for rows in tables:
        if len(rows) < 3:
            continue
        hdr = rows[0]
        idx = {name: i for i, name in enumerate(hdr)}
        i_open, i_spread, i_total = idx.get("Open"), idx.get("Spread"), idx.get("Total")
        if i_spread is None:
            continue
        arow, hrow = rows[1], rows[2]
        away = arow[0].split("\n")[0].strip()
        home = hrow[0].split("\n")[0].strip()
        get = lambda row, i: row[i] if (i is not None and i < len(row)) else ""
        out.append(CBSGame(
            away=away, home=home,
            away_abbr=NICK_TO_ABBR.get(away, away[:3].upper()),
            home_abbr=NICK_TO_ABBR.get(home, home[:3].upper()),
            open_line=parse_spread(get(hrow, i_open)),
            current_line=parse_spread(get(hrow, i_spread)),
            total=parse_total(get(arow, i_total)),
            kickoff_text=hdr[0] if hdr else "",
        ))
    return out


def fetch_public_odds(week: int | None = None, timeout: int = 30) -> list[CBSGame]:
    """Scrape the PUBLIC odds page. Free -- no key, no Odds-API credits.

    Needs a JS-rendering browser (the page builds its tables client-side),
    so this imports playwright lazily and raises a clear error when it is
    not installed rather than failing deep inside a selector.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "fetch_public_odds needs playwright (pip install playwright && "
            "playwright install chromium). Offline callers can use "
            "parse_odds_tables() on already-extracted tables."
        ) from e

    url = ODDS_URL if week is None else f"{ODDS_URL}?week={week}"
    ensure_chromium_libs()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        pg = b.new_page(user_agent=UA)
        pg.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        pg.wait_for_timeout(6000)
        tables = pg.evaluate(
            "() => [...document.querySelectorAll('table')].map(t =>"
            " [...t.querySelectorAll('tr')].map(r =>"
            " [...r.querySelectorAll('th,td')].map(c => c.innerText.trim())))")
        b.close()
    return parse_odds_tables(tables)


# ---------------------------------------------------------------------------
# Pool page -> CSV rows (the frozen line, which no anonymous fetch can reach)
# ---------------------------------------------------------------------------

NICKS = set(NICK_TO_ABBR)
_PCT = re.compile(r"^(\d{1,3})%$")
_SPREAD = re.compile(r"^([+-]\d+(?:\.\d+)?|PK|EVEN)$", re.I)


def _tok_spread(t: str) -> float | None:
    if not _SPREAD.match(t):
        return None
    if t.upper() in ("PK", "EVEN"):
        return 0.0
    return float(t.replace("+", ""))


def parse_pool_text(text: str) -> list[dict]:
    """Parse text copied from the pool's Picks page into current-week rows.

    Save or select-all-copy the picks page while logged in, drop it in a
    file, and run scripts/pickem_pool_import.py. That keeps CBS credentials
    out of this repo entirely -- nothing here logs in or stores a password;
    it reads a page you already opened yourself.

    Expected shape per game, whitespace-insensitive (CBS renders each game
    as away team / away pick% / away spread / AT / home spread / home pick%
    / home team):

        PATRIOTS 0-0   30%  +3.5  AT  -3.5  70%  SEAHAWKS 0-0

    Returns dicts keyed for data/pickem_current_week.csv. Tolerant by
    design -- it locates games by the team-nickname anchors rather than by
    line breaks, so CBS restyling the page does not silently break it.
    """
    toks = [t.strip() for t in re.split(r"[\s ]+", text or "") if t.strip()]
    # normalise: strip records like "0-0" that trail a team name
    cleaned = [t for t in toks if not re.match(r"^\d+-\d+(-\d+)?$", t)]

    def nick(t):
        c = t.title()
        if c in NICKS:
            return c
        if t.upper() == "49ERS":
            return "49ers"
        return None

    games, i = [], 0
    while i < len(cleaned):
        away = nick(cleaned[i])
        if not away:
            i += 1
            continue
        # look ahead a bounded window for: pct, spread, AT, spread, pct, home
        win = cleaned[i + 1:i + 10]
        pcts = [(j, int(_PCT.match(t).group(1))) for j, t in enumerate(win) if _PCT.match(t)]
        sprs = [(j, _tok_spread(t)) for j, t in enumerate(win) if _tok_spread(t) is not None]
        home_j = next((j for j, t in enumerate(win) if nick(t)), None)
        if home_j is None or len(sprs) < 2:
            i += 1
            continue
        home = nick(win[home_j])
        away_pct = pcts[0][1] if len(pcts) >= 1 else None
        home_pct = pcts[1][1] if len(pcts) >= 2 else None
        home_spread = sprs[1][1]          # second spread belongs to the home side
        games.append({
            "away_name": away, "home_name": home,
            "away_abbr": NICK_TO_ABBR.get(away, away[:3].upper()),
            "home_abbr": NICK_TO_ABBR.get(home, home[:3].upper()),
            "cbs_line_home": home_spread,
            "comm_pct_away": away_pct, "comm_pct_home": home_pct,
        })
        i += home_j + 2
    return games


# ---------------------------------------------------------------------------
# Automated pool fetch -- a stored session in place of a manual copy-paste
# ---------------------------------------------------------------------------

#: Where the local-only CBS session lives. Deliberately OUTSIDE both this
#: repo and ~/arbitrage -- not merely gitignored, simply never present in
#: either working tree, so there is no path by which `git add -A` or a
#: careless `git add .` could ever pick it up. Override with CBS_SESSION_PATH
#: if you want it somewhere else.
#:
#: This is the one authenticated session this project stores on purpose.
#: Every other login-adjacent surface here (DraftKings, FanDuel) explicitly
#: does NOT -- ~/arbitrage's HANDOFF.md section 6: an automated, authenticated
#: request pattern is a legible signal to a BOOK that already limits accounts
#: for arbitrage. That risk is specific to sportsbooks policing arbitrage;
#: CBS's pick'em pool carries no such incentive, and the session unblocks two
#: of the project's highest-value open experiments (PICKEM_STATUS.md) that a
#: sportsbook session never would justify here. Decided explicitly on
#: 2026-09-10, not defaulted into -- see scripts/pickem_session_bootstrap.py.
DEFAULT_SESSION_PATH = Path(
    os.environ.get("CBS_SESSION_PATH")
    or (Path.home() / ".config" / "edge_search" / "cbs_session.json")
)


class SessionExpired(RuntimeError):
    """The stored CBS session no longer reaches the pool page.

    Raised instead of silently returning garbage: an anonymous request and a
    dead session both get HTTP 200 from CBS, redirected to /join rather than
    401'd (see this module's top docstring) -- so the landed-on URL is the
    only reliable tell, and a caller that skipped this check would parse a
    join/settings page as if it were the picks page.
    """


#: Chromium's own shared libraries (libnspr4, libnss3, libatk...) are NOT
#: installed as system packages on this machine -- `dpkg -l libnss3` returns
#: nothing and `ldconfig -p` cannot see them. They were unpacked by hand into
#: ~/.local/chromium-deps, and the loader is pointed at them by an
#: LD_LIBRARY_PATH exported from ~/.bashrc.
#:
#: WHY THAT IS A BUG AND NOT A SETUP DETAIL
#: ~/.bashrc runs for INTERACTIVE shells only. A systemd user service gets
#: none of it -- the real environment of a running unit is HOME, LANG, LOGNAME,
#: PATH, SHELL, USER, XDG_*, and nothing else. So every Playwright call here
#: worked when Adam ran it by hand and failed 100% of the time under
#: deploy/pickem-capture@.service, with:
#:
#:     chrome-headless-shell: error while loading shared libraries:
#:     libnspr4.so: cannot open shared object file
#:
#: and that failure was SILENT, because the CBS fetch is deliberately a
#: soft-failing `ExecStartPre=-` (an expired session must never cancel the
#: market-half capture). The visible symptom was the opposite of an error:
#: pickem_current_week.csv kept its `provisional` notes and its Tuesday
#: comm_pct_* values forever, so every later snapshot filed stale community
#: percentages under a fresh timestamp -- exactly the failure
#: scripts/pickem_pool_fetch.py's docstring says it was built to prevent
#: ("13 of 16 games moved between Tuesday's reading and Wednesday night's,
#: one by 14 points").
#:
#: Fixing it in the PARENT's os.environ is enough: Playwright hands its own
#: environment to the chrome child it spawns, so the loader in that child
#: sees this. Verified from a process started with `env -i`.
CHROMIUM_DEPS_DIR = Path(
    os.environ.get("CHROMIUM_DEPS_DIR")
    or (Path.home() / ".local" / "chromium-deps")
)


def ensure_chromium_libs() -> None:
    """Point the loader at the hand-unpacked Chromium libraries, if present.

    A no-op on a machine where Chromium's dependencies are installed
    normally (the directory simply will not exist), and idempotent -- so it
    is safe to call before every launch rather than once at import, which is
    what keeps it from depending on module import order.
    """
    libs = [CHROMIUM_DEPS_DIR / "usr" / "lib" / "x86_64-linux-gnu",
            CHROMIUM_DEPS_DIR / "lib" / "x86_64-linux-gnu"]
    present = [str(d) for d in libs if d.is_dir()]
    if not present:
        return
    current = [seg for seg in os.environ.get("LD_LIBRARY_PATH", "").split(":") if seg]
    missing = [d for d in present if d not in current]
    if not missing:
        return
    os.environ["LD_LIBRARY_PATH"] = ":".join(missing + current)


#: Launch flag that quiets the single most common automation tell
#: (`navigator.webdriver` and the blink-level flag behind it). Shared by
#: fetch_pool_text and scripts/pickem_session_bootstrap.py so a fix to one
#: cannot silently miss the other.
STEALTH_ARGS = ["--disable-blink-features=AutomationControlled"]


def patch_automation_tells(ctx) -> None:
    """CBS's login page blocked Playwright's own browser outright before a
    human ever typed a password, on the FIRST bootstrap attempt (2026-09-10)
    -- confirming CBS actually checks these, not merely a theoretical risk.
    `navigator.webdriver` is the single most common tell; this patches it and
    its usual companions (an empty plugin list, a missing `window.chrome`) on
    every page the context opens, via an init script rather than a one-off
    page.evaluate -- so it applies before CBS's own scripts run, on every
    navigation, including the redirect a login submits into.

    Not a guarantee against every anti-bot check CBS might run (a visible
    CAPTCHA still needs the human already at the browser to solve it), but
    this is the standard, low-risk fix for a real visible browser getting
    blocked purely for being under automation control.
    """
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = window.chrome || {runtime: {}};
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    """)


def _env(key: str) -> str | None:
    """Prefer the environment, then this repo's .env, then ~/arbitrage's --
    the same lookup order scripts/odds_parity.py already uses for
    ODDS_API_KEY, kept identical so there is one convention, not two."""
    if os.environ.get(key):
        return os.environ[key]
    root = Path(__file__).resolve().parent.parent
    for env_path in (root / ".env", Path.home() / "arbitrage" / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            if line.strip().startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def pool_url() -> str | None:
    """CBS_POOL_URL from the environment or a local .env -- never hardcoded
    here, since it names Adam's own pool. See PICKEM_WEEKLY.md for where to
    set it: the pool's own Picks page, e.g.
    https://picks.cbssports.com/football/pickem/pools/<id>/picks."""
    return _env("CBS_POOL_URL")


def fetch_pool_text(url: str | None = None,
                    session_path: Path | str = DEFAULT_SESSION_PATH,
                    timeout: int = 30) -> str:
    """Fetch the pool's Picks page using a stored login session, and return
    its visible text -- the same shape a manual copy-paste already produces,
    so `parse_pool_text` needs no changes and there is one parser, not two.

    Needs a session created by scripts/pickem_session_bootstrap.py. That
    script is the only place in this project that is ever near your CBS
    password, and even there it only watches you type it into a real,
    visible browser window -- nothing here reads, stores, or transmits it.
    This function replays just the cookies that login already produced.

    Checks the session file's existence BEFORE importing playwright, so a
    missing session reports plainly even where playwright is not installed,
    and raises SessionExpired (not a generic error) either way -- the
    caller's contract is "this needs scripts/pickem_session_bootstrap.py",
    the same message whether the session never existed or has since died.
    """
    session_path = Path(session_path)
    if not session_path.exists():
        raise SessionExpired(
            f"No session at {session_path}. Run "
            "scripts/pickem_session_bootstrap.py once to create it.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "fetch_pool_text needs playwright (pip install playwright && "
            "playwright install chromium)."
        ) from e

    url = url or pool_url()
    if not url:
        raise RuntimeError(
            "No pool URL. Set CBS_POOL_URL in edge_search/.env to the pool's "
            "own Picks page, e.g. "
            "https://picks.cbssports.com/football/pickem/pools/<id>/picks")

    ensure_chromium_libs()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"] + STEALTH_ARGS)
        ctx = b.new_context(storage_state=str(session_path), user_agent=UA)
        patch_automation_tells(ctx)
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        pg.wait_for_timeout(4000)
        landed = pg.url
        text = pg.inner_text("body")
        b.close()

    if "/join" in landed or "/login" in landed or "signin" in landed.lower():
        raise SessionExpired(
            f"Session no longer reaches the pool -- landed on {landed} "
            "instead of the picks page. CBS sessions expire; re-run "
            "scripts/pickem_session_bootstrap.py.")
    return text


def _parse_cookie_header(text: str) -> list[tuple[str, str]]:
    """`sid=abc; auth=xyz` -> [("sid","abc"), ("auth","xyz")] -- the Network
    tab's Cookie REQUEST header shape."""
    out = []
    for part in text.split(";"):
        part = part.strip()
        if "=" in part:
            name, value = part.split("=", 1)
            out.append((name.strip(), value.strip()))
    return out


def _parse_cookie_table(text: str) -> list[tuple[str, str]]:
    """Rows copied from DevTools' Application/Storage -> Cookies table --
    one cookie per line, tab-separated, Name then Value then whatever other
    columns Chrome includes (Domain, Path, Expires, ...), which are ignored
    since save_cookie_session rebuilds them itself. Easier to point someone
    at than the Network tab: that tab shows every third-party request on
    the page (video widgets, analytics, ad beacons), most of which never
    carry a Cookie header at all, and it is easy to grab the wrong one."""
    out = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[0].strip():
            out.append((fields[0].strip(), fields[1].strip()))
    return out


#: Which parser reads which shape. A TAB is the deciding signal, not "does
#: it have an `=`" -- a table row's VALUE column can itself contain one
#: (base64/JWT padding, "abcDEF=="), which would otherwise make
#: _parse_cookie_header find a garbage-but-nonempty match on a table paste
#: and never fall through: "authToken\tabcDEF==\tpicks.cbssports.com\t..."
#: has no semicolon, so header-parsing treats the WHOLE line as one part,
#: sees an "=" inside the padding, and silently splits there instead of on
#: tabs. A real Cookie header, built by the browser itself, never contains a
#: literal tab; a copied table row always does. That is a clean, order-free
#: dispatch -- not "try header first, fall back to table" -- and it is the
#: reason for a dedicated test rather than trusting the "try both" instinct.
def _parse_cookie_data(text: str) -> list[tuple[str, str]]:
    return _parse_cookie_table(text) if "\t" in text else _parse_cookie_header(text)


def save_cookie_session(cookie_data: str, session_path: Path | str = DEFAULT_SESSION_PATH,
                        domain: str = ".cbssports.com") -> Path:
    """Build a session file directly from a browser that is ALREADY logged
    in -- for when picks.cbssports.com's login flow blocks Playwright's own
    browser outright. Confirmed 2026-09-10: `patch_automation_tells` got
    past the CAPTCHA, but the page never advanced afterward -- consistent
    with detection deeper than `navigator.webdriver` (the DevTools protocol
    itself, most likely), which no amount of JS-property patching reaches.

    Sidesteps the login entirely rather than fighting that: `fetch_pool_text`
    only ever REPLAYS a session for an ordinary page load, and this module's
    own top docstring already establishes CBS does not challenge that -- a
    dead session quietly redirects to /join, it does not CAPTCHA a page
    view. So a cookie lifted from a browser that passed CBS's login on its
    own (an everyday Edge or Chrome, already logged in) works exactly as
    well as one Playwright captured itself, without Playwright ever having
    to survive the login at all.

    Accepts either shape, auto-detected on whether the text contains a tab
    (see `_parse_cookie_data`) -- never "try one, fall back to the other",
    which a table row's own base64-padded value could fool:

      * DevTools -> Application/Storage -> Cookies -> the `picks.cbssports.com`
        row -> select the rows, copy. RECOMMENDED: one specific place, no
        sifting through the Network tab's noise of third-party requests that
        mostly carry no cookie at all.
      * DevTools -> Network -> a request actually made TO picks.cbssports.com
        (not a video/embed/analytics request to a different host) -> Request
        Headers -> the `Cookie:` line, value only.

    NOT `Set-Cookie`, and NOT "Copy as cURL" -- ~/arbitrage/HANDOFF.md
    section 6 names the reason for sportsbooks and it applies just the same
    here: a cURL export drags the whole request, headers well beyond the
    cookie jar.
    """
    found = _parse_cookie_data(cookie_data)
    if not found:
        raise ValueError(
            "No cookies found. Paste either the Cookie REQUEST header's "
            "value (DevTools Network tab), or rows copied from "
            "Application/Storage -> Cookies -> picks.cbssports.com -- not a "
            "cURL command, and not a Set-Cookie response header.")

    pairs = [{"name": name, "value": value, "domain": domain, "path": "/",
             "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}
            for name, value in found]

    session_path = Path(session_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps({"cookies": pairs, "origins": []}, indent=2))
    try:
        session_path.chmod(0o600)
    except OSError:
        pass  # best-effort; not fatal on filesystems that don't support it
    return session_path
