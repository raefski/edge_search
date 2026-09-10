"""scripts/pickem_pool_import.py -- the paste-to-CSV step, and the day headers.

WHY THIS FILE EXISTS
The importer had zero coverage and wrote two columns as the empty string on
every row:

    "kickoff_utc": "",  "tv": "",

...while putting the kickoff TEXT into `note`. data/pickem_current_week.csv is
the only source pages/4_🎯_Pickem.py has for a game's kickoff, and the page
groups the slate into DAY HEADERS from it. So the very next `--write` would
have collapsed all sixteen Week-1 cards under a single `TBD` header.

PICKEM_WEEKLY.md section 2: "missed-week scoring is zero, so an empty day is
the one truly fatal mistake." The TOO-GOODE pool's deadline is PER DAY -- a
slate with no days is a slate with no deadlines.

It also truncated: `OUT.open("w")` rewrote the file with exactly the rows the
paste covered, so a partial copy silently deleted the games it missed (and any
hand-made correction on them -- e.g. the DAL@NYG kickoff fix of 2026-09-09,
which was uncommitted at the time).
"""
from __future__ import annotations

import csv
import datetime
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import pickem_pool_import as pool_import  # noqa: E402

# Five real Week-1 games spanning FOUR kickoff slots and THREE Eastern days:
# Thu night, Sunday 1pm, Sunday 4:25pm, Sunday night, Monday night.
PICKS_TEXT = """Thu @ 8:35 PM NFLX
49ERS 0-0 19% +3.5 AT -3.5 81% RAMS 0-0
Sun @ 1:00 PM CBS
BILLS 0-0 78% -1.5 AT +1.5 22% TEXANS 0-0
Sun @ 4:25 PM CBS
PACKERS 0-0 60% +1.5 AT -1.5 40% VIKINGS 0-0
Sun @ 8:20 PM NBC
COWBOYS 0-0 65% -2.5 AT +2.5 35% GIANTS 0-0
Mon @ 8:15 PM ABC
BRONCOS 0-0 51% +2.5 AT -2.5 49% CHIEFS 0-0
"""

#: What the scraped board reports for those five games. The Rams carry the
#: MARKET's spelling ("Los Angeles Rams" -> LA); CBS spells them LAR, so an
#: unaliased lookup drops exactly this row -- the failure edge/pickem_week.py
#: was extracted to prevent.
BOARD_KICKOFFS = {
    ("Los Angeles Rams", "San Francisco 49ers"): "2026-09-11T00:35:00Z",
    ("Houston Texans", "Buffalo Bills"): "2026-09-13T17:00:00Z",
    ("Minnesota Vikings", "Green Bay Packers"): "2026-09-13T20:25:00Z",
    ("New York Giants", "Dallas Cowboys"): "2026-09-14T00:20:00Z",
    ("Kansas City Chiefs", "Denver Broncos"): "2026-09-15T00:15:00Z",
}


def _event(home: str, away: str, kickoff: str, home_point: float = -3.0) -> dict:
    return {
        "home_team": home, "away_team": away, "commence_time": kickoff,
        "bookmakers": [{"key": "draftkings", "markets": [
            {"key": "spreads", "outcomes": [
                {"name": home, "point": home_point},
                {"name": away, "point": -home_point}]},
        ]}],
    }


class _BoardClient:
    """A `ScrapedOddsClient` stand-in serving a fixed board."""

    def __init__(self, events):
        self.events = events

    def get_featured_odds(self, *_a, **_k):
        return self.events


def _board(extra: list[dict] | None = None) -> _BoardClient:
    evs = [_event(h, a, k) for (h, a), k in BOARD_KICKOFFS.items()]
    return _BoardClient(evs + (extra or []))


def _run(tmp_path, monkeypatch, argv, picks=PICKS_TEXT, board=None,
         existing: list[dict] | None = None):
    """Run the real `main()` with OUT redirected and the board stubbed."""
    import edge.odds.cli as odds_cli

    picks_file = Path(tmp_path) / "picks.txt"
    picks_file.write_text(picks)
    out = Path(tmp_path) / "pickem_current_week.csv"
    if existing is not None:
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=pool_import.FIELDS)
            w.writeheader()
            for r in existing:
                w.writerow({k: r.get(k, "") for k in pool_import.FIELDS})

    monkeypatch.setattr(pool_import, "OUT", out)
    monkeypatch.setattr(odds_cli, "scraped_client",
                        lambda *_a, **_k: board if board is not None else _board())
    monkeypatch.setattr(sys, "argv", ["pickem_pool_import.py", str(picks_file)] + argv)
    pool_import.main()
    if not out.exists():
        return []
    with out.open() as f:
        return list(csv.DictReader(f))


def _iso(v: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))


# ------------------------------------------------------------ the fix itself
def test_every_imported_row_carries_a_parseable_kickoff(tmp_path, monkeypatch):
    """REGRESSION. `"kickoff_utc": ""` on every row is a slate with no days.

    The page derives its per-day deadline headers from this column alone, and
    an empty one renders every game under a single `TBD`.
    """
    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"])

    assert len(rows) == 5
    for r in rows:
        assert r["kickoff_utc"], f'{r["away_abbr"]}@{r["home_abbr"]} has no kickoff'
        kick = _iso(r["kickoff_utc"])
        assert kick.tzinfo is not None, "a naive kickoff is an ambiguous deadline"

    by_home = {r["home_abbr"]: r["kickoff_utc"] for r in rows}
    assert _iso(by_home["LAR"]) == _iso("2026-09-11T00:35:00Z"), \
        "the Rams are LA on the board and LAR in the pool -- alias, don't drop"
    assert _iso(by_home["KC"]) == _iso("2026-09-15T00:15:00Z")


def test_the_imported_slate_spans_its_real_eastern_days(tmp_path, monkeypatch):
    """Three distinct ET days, and not one of them TBD.

    Same assertion the page-level test makes, made here on the data so a
    failure names the importer rather than the renderer.
    """
    from edge.pickem_week import EASTERN

    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"])
    days = {_iso(r["kickoff_utc"]).astimezone(EASTERN).strftime("%a %b %-d")
            for r in rows}
    assert days == {"Thu Sep 10", "Sun Sep 13", "Mon Sep 14"}


def test_rows_come_out_in_kickoff_order(tmp_path, monkeypatch):
    """The page groups by day in FILE order and never sorts.

    A carried-over row appended after the imported ones would split a day
    group in two, so the writer orders the week by kickoff.
    """
    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"])
    kicks = [_iso(r["kickoff_utc"]) for r in rows]
    assert kicks == sorted(kicks)


def test_the_kickoff_never_lands_in_the_note_column(tmp_path, monkeypatch):
    """`note` is for genuine caveats -- `provisional`, and what the page's
    banner counts. Filling it with "Thu @ 8:35 PM" made every row look
    annotated and rendered a time where a warning belongs."""
    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"])
    for r in rows:
        assert r["note"] == "", f'note polluted with {r["note"]!r}'


# ------------------------------------------------------------ the truncation
def test_a_partial_paste_does_not_delete_the_games_it_missed(tmp_path, monkeypatch):
    """REGRESSION. `OUT.open("w")` truncated the file to the paste.

    A copy that clipped the last two games silently deleted them -- together
    with the hand-made DAL@NYG kickoff correction that was sitting uncommitted
    when this was found.
    """
    existing = [
        {"week": 1, "away_abbr": "DAL", "home_abbr": "NYG", "away_name": "Cowboys",
         "home_name": "Giants", "cbs_line_home": 2.5,
         "kickoff_utc": "2026-09-14T00:20:00Z", "tv": "NBC",
         "comm_pct_away": 65, "comm_pct_home": 35, "note": "provisional"},
        {"week": 1, "away_abbr": "ATL", "home_abbr": "PIT", "away_name": "Falcons",
         "home_name": "Steelers", "cbs_line_home": -3.5,
         "kickoff_utc": "2026-09-13T17:00:00Z", "tv": "FOX",
         "comm_pct_away": 24, "comm_pct_home": 76, "note": "provisional"},
    ]
    # a paste covering only the Thursday game
    partial = "Thu @ 8:35 PM NFLX\n49ERS 0-0 19% +3.5 AT -3.5 81% RAMS 0-0\n"
    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"],
                picks=partial, existing=existing)

    homes = {r["home_abbr"] for r in rows}
    assert "LAR" in homes, "the imported game must be there"
    assert {"NYG", "PIT"} <= homes, "a game the paste missed must survive"
    kept = next(r for r in rows if r["home_abbr"] == "PIT")
    assert kept["kickoff_utc"] == "2026-09-13T17:00:00Z"
    assert kept["tv"] == "FOX"


def test_another_week_in_the_file_is_never_destroyed(tmp_path, monkeypatch):
    """Importing week 2 must not delete week 1's captured lines.

    The line log joins on (season, week, home_team) and the page's week
    selector offers every week; a file truncated to one week makes both lie.
    """
    existing = [
        {"week": 2, "away_abbr": "NE", "home_abbr": "MIA", "away_name": "Patriots",
         "home_name": "Dolphins", "cbs_line_home": -1.5,
         "kickoff_utc": "2026-09-20T17:00:00Z", "tv": "CBS",
         "comm_pct_away": 40, "comm_pct_home": 60, "note": ""},
    ]
    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"],
                existing=existing)
    assert any(r["week"] == "2" and r["home_abbr"] == "MIA" for r in rows), \
        "week 2 was truncated away by a week-1 import"
    assert sum(1 for r in rows if r["week"] == "1") == 5


def test_a_re_import_updates_the_line_it_covers(tmp_path, monkeypatch):
    """Carrying rows over must not shadow the numbers being re-imported."""
    existing = [
        {"week": 1, "away_abbr": "DEN", "home_abbr": "KC", "away_name": "Broncos",
         "home_name": "Chiefs", "cbs_line_home": -9.5,
         "kickoff_utc": "2026-09-15T00:15:00Z", "tv": "ABC",
         "comm_pct_away": 10, "comm_pct_home": 90, "note": "provisional"},
    ]
    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"],
                existing=existing)
    kc = next(r for r in rows if r["home_abbr"] == "KC")
    assert kc["cbs_line_home"] == "-2.5", "the fresh paste's line must win"
    assert kc["comm_pct_home"] == "49"
    assert kc["tv"] == "ABC", "and the TV column, which no board carries, survives"


# --------------------------------------------------------------- escape hatch
def test_no_enrich_still_works_but_says_what_it_costs(tmp_path, monkeypatch, capsys):
    """--no-enrich stays available for an offline import, and warns.

    Without the board there is no kickoff, and without a kickoff the page has
    no day headers -- which is the fatal mistake, so it cannot be silent.
    """
    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write", "--no-enrich"])
    out = capsys.readouterr().out.lower()
    assert rows, "--no-enrich must still write"
    assert "day" in out and ("no kickoff" in out or "without kickoff" in out
                            or "kickoff" in out)
    assert "--no-enrich" in out


def test_a_missing_kickoff_is_reported_loudly(tmp_path, monkeypatch, capsys):
    """A board that covers only some of the slate leaves the rest dateless."""
    thin = _BoardClient([_event("Kansas City Chiefs", "Denver Broncos",
                                "2026-09-15T00:15:00Z")])
    _run(tmp_path, monkeypatch, ["--week", "1", "--write"], board=thin)
    out = capsys.readouterr().out
    assert "TBD" in out or "no kickoff" in out.lower()
    assert "LAR" in out or "Rams" in out


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch):
    rows = _run(tmp_path, monkeypatch, ["--week", "1"])
    assert rows == []


def test_the_board_lookup_failing_is_not_fatal(tmp_path, monkeypatch, capsys):
    """A stale or absent odds store must not cost the whole import.

    The CBS numbers are the irreplaceable half -- they are behind a login and
    exist only in the paste. Losing them to an odds-store problem would be the
    expensive failure.
    """
    class _Boom:
        def get_featured_odds(self, *_a, **_k):
            raise RuntimeError("no committed scan for profile 'pickem_nfl'")

    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"], board=_Boom())
    assert len(rows) == 5, "the CBS lines must survive a board outage"
    assert "no committed scan" in capsys.readouterr().out


def test_the_board_is_never_allowed_to_write_the_pool_line(tmp_path, monkeypatch):
    """PICKEM_MODEL.md 5h: cbs_line_home comes from CBS or from nowhere.

    The whole model is the GAP between CBS's frozen number and the market's.
    A market number written into the CBS column makes that gap zero -- a
    fabricated 'the market agrees' on every game, undetectable afterwards.
    """
    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"])
    # the board fixture prices every game at -3.0; the paste says otherwise
    assert [r["cbs_line_home"] for r in rows] != ["-3.0"] * len(rows)
    by_home = {r["home_abbr"]: r["cbs_line_home"] for r in rows}
    assert by_home["HOU"] == "1.5" and by_home["KC"] == "-2.5"


# ---------------------------------------------------------------- the note
def test_a_re_import_clears_provisional_but_keeps_a_real_caveat(tmp_path, monkeypatch):
    """The paste IS the verification, so `provisional` stops being true.

    Everything else in the cell is Adam's. "CBS froze Texans favored -- watch
    for a flip" is the note that called the flip on the showcase game of the
    week; blanking it is the same shape of loss as truncating the file.
    """
    existing = [
        {"week": 1, "away_abbr": "BUF", "home_abbr": "HOU", "away_name": "Bills",
         "home_name": "Texans", "cbs_line_home": -1.5,
         "kickoff_utc": "2026-09-13T17:00:00Z", "tv": "CBS",
         "comm_pct_away": 78, "comm_pct_home": 22,
         "note": "provisional; CBS froze Texans favored -- watch for a flip"},
        {"week": 1, "away_abbr": "DEN", "home_abbr": "KC", "away_name": "Broncos",
         "home_name": "Chiefs", "cbs_line_home": -2.5,
         "kickoff_utc": "2026-09-15T00:15:00Z", "tv": "ABC",
         "comm_pct_away": 51, "comm_pct_home": 49,
         "note": "provisional -- re-verify Tue Sep 8 after 1pm ET"},
    ]
    rows = _run(tmp_path, monkeypatch, ["--week", "1", "--write"],
                existing=existing)
    by_home = {r["home_abbr"]: r["note"] for r in rows}
    assert by_home["HOU"] == "CBS froze Texans favored -- watch for a flip"
    assert by_home["KC"] == "", "a bare staleness marker leaves nothing behind"


def test_carry_note_is_pure_and_documented():
    from scripts.pickem_pool_import import carry_note

    assert carry_note("provisional") == ""
    assert carry_note("") == ""
    assert carry_note("weather: 25mph wind") == "weather: 25mph wind"
    assert carry_note("provisional; QB out") == "QB out"
