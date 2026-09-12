#!/usr/bin/env python3
"""Collect historical DraftKings NFL salaries, for the lineup backtest.

WHY THIS IS NEEDED AND WHY IT LOOKS LIKE THIS
A projection backtest (scripts/nfl_skill_backtest.py) needs actuals. A LINEUP
backtest needs actuals *and salaries*, because a lineup is a constrained choice
and without the cap there is no choice to evaluate -- "did the model pick the
right players" is meaningless if it could pick all of them.

DraftKings does not serve historical draftables. `dfs.fetch_draftables` only
answers for current and upcoming draft groups, which is why
data/HISTORICAL_COLLECTION_README.md records DK salary as not backfillable.
The paid historical props that would have supplied a parallel board are gone
(DFS_STATUS.md: the 2026-07-24 pull is not on this machine and the key expired
the same day).

RotoGuru has published a free weekly DK salary + DK points table for years, and
it is the only source on the open web that survives for these seasons. What it
costs is COVERAGE: probed 2026-09-12, it returns rows for 2019, 2020 and 2021
and an empty table for 2022 onward. So the backtest's usable seasons are the
intersection of

    RotoGuru salaries          2019-2021
    nflverse player_week       2020+ (2019 is a 404 from nflverse's own
                               detailed pipeline)

which is 2020 and 2021, and that is a constraint of the data rather than a
choice. Two full seasons is ~34 slates, which is enough to compare two lineup
CONSTRUCTIONS against each other and nowhere near enough to estimate an ROI.
Say the first, never the second.

WHAT IS AND IS NOT TRUSTED FROM THIS FILE
Trusted: salary, position, team, opponent, home/away. These are DK's own slate
facts and have no other source.
NOT trusted as ground truth: the "DK points" column. Actuals come from nflverse
through edge/nfl.py, the same scorer the rest of the repo uses, so the backtest
cannot be flattered by a second scoring convention. The column IS read, and
used as a CHECK -- a disagreement between RotoGuru's DK points and ours is a
join or scoring bug, and the backtest prints the correlation between them.

    python3 scripts/nfl_dk_salary_collect.py                  # 2020 + 2021
    python3 scripts/nfl_dk_salary_collect.py --seasons 2021
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "nfl_dk_salaries"
URL = "http://rotoguru1.com/cgi-bin/fyday.pl?week={week}&year={year}&game=dk&scsv=1"
UA = {"User-Agent": "Mozilla/5.0 (edge-search research use)"}

#: RotoGuru's team codes -> nflverse/DraftKings. Only the ones that disagree.
#: Left as data because getting one wrong silently drops a whole team from
#: every slate it appears in, which reads as a thin board rather than a bug.
TEAM_FIX = {
    "gnb": "GB", "kan": "KC", "nor": "NO", "nwe": "NE", "sfo": "SF",
    "tam": "TB", "sdg": "LAC", "lac": "LAC", "lar": "LA", "ram": "LA",
    "stl": "LA", "oak": "LV", "lvr": "LV", "rai": "LV", "was": "WAS",
    "jac": "JAX", "ari": "ARI", "hou": "HOU", "ten": "TEN", "bal": "BAL",
    "ind": "IND", "cle": "CLE", "pit": "PIT", "cin": "CIN", "buf": "BUF",
    "mia": "MIA", "nyj": "NYJ", "nyg": "NYG", "phi": "PHI", "dal": "DAL",
    "chi": "CHI", "det": "DET", "min": "MIN", "atl": "ATL", "car": "CAR",
    "sea": "SEA", "den": "DEN", "nej": "NYJ",
}

#: RotoGuru position -> DK position. "Def" is DK's DST slot.
POS_FIX = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "Def": "DST"}


def team(code: str) -> str:
    c = (code or "").strip().lower()
    return TEAM_FIX.get(c, c.upper())


def flip_name(name: str) -> str:
    """'Allen, Josh' -> 'Josh Allen'. Defence rows carry a city, not a person."""
    name = (name or "").strip()
    if "," not in name:
        return name
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}".strip()


def fetch_week(year: int, week: int, retries: int = 3) -> list[dict]:
    """One week's rows, or [] when RotoGuru has nothing for that season.

    An empty table is a real answer here (2022+), not a failure -- it is how
    the coverage limit in this module's docstring was established -- so it
    returns empty rather than raising.
    """
    url = URL.format(year=year, week=week)
    body = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            body = urllib.request.urlopen(req, timeout=60).read().decode(
                "latin-1", "replace")
            break
        except Exception as exc:                       # network, not logic
            if attempt == retries - 1:
                print(f"  {year} wk{week}: {exc}", file=sys.stderr)
                return []
            time.sleep(2 * (attempt + 1))

    block = re.search(r"Week;Year;.*?</pre>", body, re.S)
    if not block:
        return []
    out = []
    for line in block.group(0).splitlines():
        parts = line.split(";")
        if len(parts) < 10 or not parts[0].isdigit():
            continue
        wk, yr, _gid, name, pos, tm, ha, opp, pts, salary = parts[:10]
        if pos not in POS_FIX or not salary.strip():
            continue
        try:
            out.append({
                "season": int(yr), "week": int(wk),
                "name": flip_name(name), "position": POS_FIX[pos],
                "team": team(tm), "opp": team(opp), "home": ha.strip() == "h",
                "salary": int(float(salary)),
                "rotoguru_points": float(pts) if pts.strip() else None,
            })
        except ValueError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", default="2020,2021")
    ap.add_argument("--weeks", default="1-17")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="courtesy delay between requests (default: %(default)s)")
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.weeks.split("-"))
    OUT.mkdir(parents=True, exist_ok=True)
    grand = 0
    for season in (int(x) for x in args.seasons.split(",")):
        rows: list[dict] = []
        for week in range(lo, hi + 1):
            got = fetch_week(season, week)
            rows.extend(got)
            print(f"  {season} wk{week:>2}: {len(got):>4} priced players")
            time.sleep(args.sleep)
        if not rows:
            print(f"{season}: NOTHING -- RotoGuru does not carry this season. "
                  f"Probed 2026-09-12: 2022 onward is empty.", file=sys.stderr)
            continue
        path = OUT / f"dk_salaries_{season}.json"
        path.write_text(json.dumps(rows))
        weeks = len({r["week"] for r in rows})
        print(f"{season}: {len(rows):,} rows over {weeks} weeks -> {path.name}")
        grand += len(rows)

    print(f"\n{grand:,} player-weeks total in {OUT}")
    print("Salary/position/team are DK's own. The DK-points column is a CHECK "
          "on the nflverse join, never the backtest's ground truth.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
