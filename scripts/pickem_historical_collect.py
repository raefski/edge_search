#!/usr/bin/env python3
"""Build data/pickem_odds_history.csv -- open+close NFL spreads, leak-free.

Two free sources, joined and cross-checked:
  1. aussportsbetting.com's historical NFL workbook -- open/close spreads,
     moneylines, totals. The site itself is Cloudflare-blocked from this
     sandbox; the Wayback Machine mirror isn't (see FETCH_URL below). This is
     the ONE fragile step here -- if this specific snapshot ever disappears,
     that's exactly why the output is a committed CSV, not a live re-fetch.
  2. nflverse/nfldata's games.csv (github, always live, stable, free) -- used
     only to cross-check final scores match before trusting a merged row.

Stdlib only: an .xlsx is a zip of XML, no openpyxl needed (this sandbox has
no pip). Playoff games are dropped (pool doesn't include postseason).

Run: python3 scripts/pickem_historical_collect.py
Output: data/pickem_odds_history.csv (committed -- free, non-sensitive, and
the aussportsbetting source is fragile enough that a re-fetch isn't a safe
assumption; treat this file the same as MLB's committed park_factors.json).
"""
from __future__ import annotations

import csv
import datetime
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "pickem_odds_history.csv"

# A known-good Wayback Machine snapshot of the source workbook (2025-08-27
# capture -- covers through the 2024 season / Super Bowl LIX), pinned to an
# exact timestamp with the `id_` modifier so it serves the raw file with no
# toolbar/JS injection (the un-suffixed URL serves an HTML-wrapped replay
# even for an exact-timestamp match -- id_ is the documented way around
# that). If this ever 404s, browse
# https://web.archive.org/web/*/aussportsbetting.com/historical_data/nfl.xlsx
# for a newer capture's timestamp and swap it in below.
FETCH_URL = "https://web.archive.org/web/20250827041320id_/https://www.aussportsbetting.com/historical_data/nfl.xlsx"
NFLVERSE_GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

KEEP = {
    "Date": "date_serial", "Home Team": "home_name", "Away Team": "away_name",
    "Home Score": "home_score", "Away Score": "away_score",
    "Playoff Game?": "playoff",
    "Home Line Open": "home_line_open", "Home Line Close": "home_line_close",
    "Home Odds Open": "home_ml_open", "Home Odds Close": "home_ml_close",
    "Away Odds Open": "away_ml_open", "Away Odds Close": "away_ml_close",
    "Total Score Open": "total_open", "Total Score Close": "total_close",
}

NICK_OF_ABBR = {
    "ARI": "cardinals", "ATL": "falcons", "BAL": "ravens", "BUF": "bills",
    "CAR": "panthers", "CHI": "bears", "CIN": "bengals", "CLE": "browns",
    "DAL": "cowboys", "DEN": "broncos", "DET": "lions", "GB": "packers",
    "HOU": "texans", "IND": "colts", "JAX": "jaguars", "KC": "chiefs",
    "LA": "rams", "LAC": "chargers", "LV": "raiders", "MIA": "dolphins",
    "MIN": "vikings", "NE": "patriots", "NO": "saints", "NYG": "giants",
    "NYJ": "jets", "OAK": "raiders", "PHI": "eagles", "PIT": "steelers",
    "SD": "chargers", "SEA": "seahawks", "SF": "49ers", "STL": "rams",
    "TB": "buccaneers", "TEN": "titans", "WAS": "washington",
}
NICKS = sorted(set(NICK_OF_ABBR.values()))


def _nick(name: str) -> str | None:
    low = name.lower()
    if "washington" in low:
        return "washington"
    return next((n for n in NICKS if n in low), None)


def _col(ref: str) -> str:
    return re.match(r"([A-Z]+)", ref).group(1)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _parse_workbook(raw: bytes) -> list[dict]:
    z = zipfile.ZipFile(__import__("io").BytesIO(raw))
    shared = ["".join(t.text or "" for t in si.iter(NS + "t"))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si")]

    def cells(row):
        out = {}
        for c in row.iter(NS + "c"):
            v = c.find(NS + "v")
            if v is None:
                continue
            out[_col(c.get("r"))] = shared[int(v.text)] if c.get("t") == "s" else v.text
        return out

    rows_iter = ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(NS + "row")
    header = cells(next(rows_iter))
    colmap = {letter: KEEP[name] for letter, name in header.items() if name in KEEP}

    epoch = datetime.date(1899, 12, 30)
    out = []
    for row in rows_iter:
        vals = cells(row)
        if "A" not in vals:
            continue
        rec = {field: vals.get(letter, "") for letter, field in colmap.items()}
        try:
            serial = int(float(rec.pop("date_serial")))
        except (ValueError, TypeError):
            continue
        rec["date"] = (epoch + datetime.timedelta(days=serial)).isoformat()
        out.append(rec)
    return out


def _f(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def main() -> None:
    print(f"fetching odds workbook from Wayback Machine ...")
    aus_rows = _parse_workbook(_fetch(FETCH_URL))
    print(f"  parsed {len(aus_rows)} rows from the workbook")

    print("fetching nflverse games.csv (fresh, stable source) ...")
    nfl_rows = list(csv.DictReader(_fetch(NFLVERSE_GAMES_URL).decode().splitlines()))
    nfl_idx = {(r["gameday"], NICK_OF_ABBR.get(r["home_team"])): r for r in nfl_rows}

    merged, dropped_playoff, dropped_no_line, dropped_no_match, dropped_mismatch = [], 0, 0, 0, 0
    for r in aus_rows:
        if r.get("playoff") == "Y":
            dropped_playoff += 1
            continue
        lo, lc = _f(r.get("home_line_open")), _f(r.get("home_line_close"))
        if lo is None or lc is None:
            dropped_no_line += 1
            continue
        hn, an = _nick(r["home_name"]), _nick(r["away_name"])
        d = datetime.date.fromisoformat(r["date"])
        m = None
        for dd in (0, -1, 1):  # source's date can be off by a day at TZ boundaries
            cand = nfl_idx.get(((d + datetime.timedelta(days=dd)).isoformat(), hn))
            if cand and NICK_OF_ABBR.get(cand["away_team"]) == an and cand["game_type"] == "REG":
                m = cand
                break
        if m is None:
            dropped_no_match += 1
            continue
        if int(m["home_score"]) != int(r["home_score"]) or int(m["away_score"]) != int(r["away_score"]):
            dropped_mismatch += 1
            continue
        merged.append({
            "season": m["season"], "week": m["week"], "date": r["date"],
            "away_team": m["away_team"], "home_team": m["home_team"],
            "home_score": r["home_score"], "away_score": r["away_score"],
            "home_line_open": lo, "home_line_close": lc,
            "home_ml_open": r.get("home_ml_open", ""), "home_ml_close": r.get("home_ml_close", ""),
            "away_ml_open": r.get("away_ml_open", ""), "away_ml_close": r.get("away_ml_close", ""),
            "total_open": r.get("total_open", ""), "total_close": r.get("total_close", ""),
        })

    merged.sort(key=lambda r: (r["season"], r["week"], r["date"]))
    fields = ["season", "week", "date", "away_team", "home_team", "home_score", "away_score",
              "home_line_open", "home_line_close", "home_ml_open", "home_ml_close",
              "away_ml_open", "away_ml_close", "total_open", "total_close"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(merged)

    by_season = {}
    for r in merged:
        by_season[r["season"]] = by_season.get(r["season"], 0) + 1
    print(f"\nwrote {len(merged)} regular-season games with open+close lines -> {OUT}")
    print(f"dropped: {dropped_playoff} playoff, {dropped_no_line} missing a line, "
          f"{dropped_no_match} no schedule match, {dropped_mismatch} score mismatch")
    print(f"seasons: {dict(sorted(by_season.items()))}")


if __name__ == "__main__":
    main()
