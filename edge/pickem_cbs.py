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
"""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass

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
