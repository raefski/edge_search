"""Append-only snapshot log -- the data that unblocks PICKEM_MODEL.md 5f.

Four experiments are currently impossible because the data was never
recorded. Each row written here is a down payment on one of them:

  * CBS post-offset isolation. There are NO historical CBS lines anywhere --
    the backtest's "pool line" is a sportsbook opener used as a proxy. To
    separate CBS's own house shading from genuine post-Tuesday drift we need
    CBS's number AND a market reading from the same moment. That is the
    `post` snapshot: cbs_line_home alongside market_line_home.
  * Line-movement velocity. Needs more than two readings per game. Every
    extra snapshot between post and lock builds that series.
  * Sharp-book agreement. The historical file is single-book. `book_lines`
    stores each book's number, so cross-book disagreement becomes
    measurable after the fact.
  * Public-pick fading. CBS community percentages exist nowhere
    historically. Captured here per game, per snapshot.

WHERE THIS LIVES, and why not in data/pickem/: everything written here is
public market information (lines, totals, CBS's community percentages), so
it is COMMITTED -- it needs to accumulate across a season, survive a
Streamlit Cloud rebuild, and be diffable. Adam's own picks, standings, and
money stay in data/pickem/, which is gitignored. Do not merge the two.

Append-only on purpose: a snapshot is a claim about what the world looked
like at one instant, and rewriting history would quietly destroy exactly
the drift signal this exists to measure.
"""
from __future__ import annotations

import csv
import datetime
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE_LOG = ROOT / "data" / "pickem_line_log.csv"

# 'post' = the moment CBS's line is first seen (Tuesday, the freeze).
# 'lock' = last reading before that day's picks deadline.
# anything else = a free-form mid-week reading, useful for velocity.
SNAPSHOTS = ("post", "lock")

FIELDS = [
    "season", "week", "snapshot", "captured_at",
    "away_team", "home_team", "kickoff_utc",
    "cbs_line_home", "comm_pct_away", "comm_pct_home",
    "market_line_home", "market_line_mean", "market_line_median",
    "market_total", "n_books", "book_disagreement", "book_lines_json",
]


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Snapshot:
    season: int
    week: int
    snapshot: str
    away_team: str
    home_team: str
    captured_at: str = ""
    kickoff_utc: str = ""
    cbs_line_home: float | None = None
    comm_pct_away: float | None = None
    comm_pct_home: float | None = None
    market_line_home: float | None = None
    market_line_mean: float | None = None
    market_line_median: float | None = None
    market_total: float | None = None
    n_books: int = 0
    book_disagreement: float | None = None
    book_lines: dict[str, float] | None = None

    def as_row(self) -> dict:
        d = {
            "season": self.season, "week": self.week, "snapshot": self.snapshot,
            "captured_at": self.captured_at or utcnow(),
            "away_team": self.away_team, "home_team": self.home_team,
            "kickoff_utc": self.kickoff_utc,
            "cbs_line_home": _num(self.cbs_line_home),
            "comm_pct_away": _num(self.comm_pct_away),
            "comm_pct_home": _num(self.comm_pct_home),
            "market_line_home": _num(self.market_line_home, 3),
            "market_line_mean": _num(self.market_line_mean, 3),
            "market_line_median": _num(self.market_line_median, 3),
            "market_total": _num(self.market_total, 3),
            "n_books": self.n_books,
            "book_disagreement": _num(self.book_disagreement, 2),
            "book_lines_json": json.dumps(self.book_lines, sort_keys=True) if self.book_lines else "",
        }
        return d


def _num(v, places: int = 2):
    return "" if v is None else round(float(v), places)


def append(snapshots: list[Snapshot], path: Path | str = LINE_LOG) -> int:
    """Append rows, creating the file with a header if needed.

    De-dupes on (season, week, snapshot, home_team): re-running a capture
    for a snapshot already recorded is a no-op rather than a second row, so
    an accidental double-run cannot corrupt the series. Use a distinct
    snapshot label (e.g. 'wed-am') for a genuinely new reading.
    """
    path = Path(path)
    existing = set()
    if path.exists():
        with path.open() as f:
            for r in csv.DictReader(f):
                existing.add((r["season"], r["week"], r["snapshot"], r["home_team"]))

    fresh = [s for s in snapshots
             if (str(s.season), str(s.week), s.snapshot, s.home_team) not in existing]
    if not fresh:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        for s in fresh:
            w.writerow(s.as_row())
    return len(fresh)


def load(path: Path | str = LINE_LOG) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def cbs_bias(season: int, week: int, home_team: str,
             path: Path | str = LINE_LOG) -> float | None:
    """CBS's own shading at post time: cbs_line_home - market_line_home.

    THE point of this whole module. Positive means CBS hung a number more
    favourable to the away side than the market did at that same instant.
    Subtracting it from a later edge leaves pure post-Tuesday drift:

        true_edge = (market_now - cbs_line) - cbs_bias

    Returns None until a `post` snapshot exists with both numbers, which is
    why PICKEM_MODEL.md 5f lists this experiment as blocked rather than
    failed -- there is nothing wrong with the idea, we just have no data yet.
    """
    for r in load(path):
        if (int(r["season"]) == season and int(r["week"]) == week
                and r["snapshot"] == "post" and r["home_team"] == home_team):
            if r["cbs_line_home"] and r["market_line_home"]:
                return float(r["cbs_line_home"]) - float(r["market_line_home"])
    return None
