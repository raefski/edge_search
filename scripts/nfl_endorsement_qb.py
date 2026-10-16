#!/usr/bin/env python3
"""Do the most-endorsed quarterbacks win more than the market expects, or
beat the spread? (Adam, 2026-10-12)

ENDORSED: each season's top tier of quarterback off-field earners. Published
figures anchor 2014-2016 (Forbes: P. Manning, Brees, E. Manning, Rodgers,
Wilson, Brady, Newton), 2021 (Forbes: Brady $45M, Mahomes $22M, Prescott and
Wilson $12M, Rodgers $11M), 2024 (Sportico: Mahomes $27M, Prescott $14M,
Allen $12M, Herbert $11M, Lawrence $10M) and 2025 (Sportico: Mahomes $30M,
then Allen, Herbert, Prescott). The years between are carried from their
neighbours, since the tier changes slowly -- an approximation, labelled so.

A game counts only when an endorsed QB actually STARTED (nflverse
home_qb_name / away_qb_name) against a non-endorsed one. Measured three ways:
  * straight-up record (they are good, so this is high and says little);
  * wins ABOVE what the closing moneyline implied (no-vig) -- the real test
    of "win more often";
  * against the closing spread.
2013-2022 and 2025; 2023-2024 not read (the pick'em holdout).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.nfl_script_backtest import HOLDOUT, ats, load_games, national, rec  # noqa: E402

_BASE_10S = ["Peyton Manning", "Drew Brees", "Eli Manning", "Aaron Rodgers", "Tom Brady"]
ENDORSED = {
    2013: _BASE_10S, 2014: _BASE_10S,
    2015: _BASE_10S + ["Russell Wilson", "Cam Newton"],
    2016: ["Tom Brady", "Drew Brees", "Aaron Rodgers", "Russell Wilson", "Cam Newton", "Eli Manning"],
    2017: ["Tom Brady", "Drew Brees", "Aaron Rodgers", "Russell Wilson", "Cam Newton", "Eli Manning"],
    2018: ["Tom Brady", "Drew Brees", "Aaron Rodgers", "Russell Wilson", "Cam Newton", "Eli Manning"],
    2019: ["Tom Brady", "Drew Brees", "Aaron Rodgers", "Russell Wilson", "Cam Newton", "Patrick Mahomes"],
    2020: ["Tom Brady", "Drew Brees", "Aaron Rodgers", "Russell Wilson", "Patrick Mahomes", "Dak Prescott"],
    2021: ["Tom Brady", "Patrick Mahomes", "Dak Prescott", "Russell Wilson", "Aaron Rodgers"],
    2022: ["Tom Brady", "Patrick Mahomes", "Dak Prescott", "Russell Wilson", "Aaron Rodgers", "Josh Allen"],
    2025: ["Patrick Mahomes", "Josh Allen", "Justin Herbert", "Dak Prescott", "Trevor Lawrence"],
}
PERIODS = {"2013-19": range(2013, 2020), "2020-22": range(2020, 2023), "2025": range(2025, 2026)}


def novig_home(r) -> float | None:
    try:
        h, a = float(r["home_moneyline"]), float(r["away_moneyline"])
    except (TypeError, ValueError):
        return None
    imp = lambda m: 100 / (m + 100) if m > 0 else -m / (-m + 100)  # noqa: E731
    ph, pa = imp(h), imp(a)
    return ph / (ph + pa)


def main() -> None:
    games = [g for g in load_games() if g["season"] not in HOLDOUT and g["season"] in ENDORSED]
    print("=" * 92)
    print("MOST-ENDORSED QBs (started, vs a non-endorsed QB) -- 2023-24 not read")
    print("=" * 92)
    for label, filt in (("all games", lambda r, s: True),
                        ("as favourite", lambda r, s: (r["spread"] > 0) == (s == "home")),
                        ("as underdog", lambda r, s: (r["spread"] > 0) != (s == "home") and r["spread"] != 0),
                        ("primetime", lambda r, s: national(r))):
        print(f"\n{label}")
        for p, yrs in PERIODS.items():
            su, exp, n, res = 0.0, 0.0, 0, []
            for r in games:
                if r["season"] not in yrs:
                    continue
                e = set(ENDORSED[r["season"]])
                h, a = r["home_qb_name"] in e, r["away_qb_name"] in e
                if h == a:
                    continue
                side = "home" if h else "away"
                if not filt(r, side):
                    continue
                ph = novig_home(r)
                if ph is None:
                    continue
                m = r["margin"] if side == "home" else -r["margin"]
                su += 1 if m > 0 else .5 if m == 0 else 0
                exp += ph if side == "home" else 1 - ph
                n += 1
                res.append(ats(r, side))
            if n:
                print(f"  {p:<8} n={n:<4} won {su / n:5.1%} vs market-expected {exp / n:5.1%} "
                      f"({(su - exp):+5.1f} wins)   ATS {rec(res)}")


if __name__ == "__main__":
    main()
