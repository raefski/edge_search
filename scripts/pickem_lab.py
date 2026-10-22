#!/usr/bin/env python3
"""Held-out harness for the pick'em model, by WEEK -- the dfs-critique loop's
measuring stick for pages/4_🎯_Pickem.py (PICKEM_CRITIQUE_LOG.md).

THE METRIC (Adam, 2026-10-22)
  1. how often the pick beats the pool line: W / (W + L), Wilson 95% CI
  2. points per week against a random picker: model wins minus the random
     picker's expectation (half of every non-push game), averaged over weeks,
     with its standard error. A week is the unit: sixteen games share one
     Sunday, one weather map, one set of injury news.

THE SPLITS -- why these and not 2023-24
  train    2014-2019  (edge.pickem_data TRAIN)  -- the only seasons a fix may
                       be fitted on
  test     2020-2022  (VALIDATE)  -- the critique loop's held-out seasons.
                       They were looked at during the Aug-2026 feature rounds
                       (PICKEM_MODEL.md section 3), so they are "held out from
                       this loop's fits", not virgin. Said here so nobody
                       reads them as more than that.
  live     2026 weeks graded from data/pickem_line_log.csv: the REAL CBS line
                       and the last market reading before the pick deadline
                       (scripts/pickem_grade.readings), nflverse scores. Tiny
                       (n weeks printed), but it is the only data where the
                       pool line is CBS's own number rather than the opener
                       proxy -- both must improve to ship.
  2023-24 is the spent holdout (pickem_data refuses it without
  spend_the_holdout) and this harness never reads it.

The pick comes from the SHIPPED edge.pickem.make_pick unless a variant is
named: variants are the parameters a critique cycle adds (VARIANTS below),
each a pick function with the same signature, so production == harness is a
one-line comparison.

Run:  python3 scripts/pickem_lab.py report [--variant NAME] [--no-live]
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from edge.pickem import ats_result, make_pick  # noqa: E402
from edge.pickem_data import Split, load  # noqa: E402

#: name -> pick function(away, home, pool_line, live_line, total_open,
#: total_close) -> Pick. "shipped" is always production.
VARIANTS = {"shipped": make_pick}


def history(split: Split) -> list[dict]:
    return [dict(season=g.season, week=g.week, away=g.away, home=g.home,
                 pool=g.pool_line, live=g.live_line, margin=g.margin,
                 total_open=g.total_open, total_close=g.total_close)
            for g in load(split)]


def live_games() -> list[dict]:
    """2026 from the line log, graded games only. Network: nflverse."""
    import pickem_grade as pg
    games = pg.readings()
    sched = pg.fetch_schedule({g["season"] for g in games})
    out = []
    for g in pg.grade(games, sched):
        out.append(dict(season=g["season"], week=g["week"], away=g["away"],
                        home=g["home"], pool=g["cbs"], live=g["lock"],
                        margin=g["home_score"] - g["away_score"],
                        total_open=g["total_open"],
                        total_close=g["total_close"]))
    return out


def wilson(w: int, l: int, z: float = 1.96) -> tuple[float, float]:
    n = w + l
    if not n:
        return (0.0, 0.0)
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def score(games: list[dict], pick=make_pick) -> dict:
    weeks = defaultdict(lambda: [0, 0, 0])       # W L P
    sig = [0, 0]
    for g in games:
        pk = pick(g["away"], g["home"], g["pool"], g["live"],
                  g["total_open"], g["total_close"])
        r = ats_result(g["margin"], g["pool"], pk.side)
        weeks[(g["season"], g["week"])]["WLP".index(r)] += 1
        if pk.tier != "COIN FLIP" and r != "P":
            sig[r == "L"] += 1
    w = sum(v[0] for v in weeks.values())
    l = sum(v[1] for v in weeks.values())
    diffs = [v[0] - (v[0] + v[1]) / 2 for v in weeks.values()]
    pts = [v[0] for v in weeks.values()]
    n = len(diffs)
    return dict(
        weeks=n, games=w + l + sum(v[2] for v in weeks.values()),
        w=w, l=l, pct=w / (w + l) if w + l else float("nan"),
        ci=wilson(w, l), pts_wk=statistics.mean(pts) if pts else 0.0,
        vs_random=statistics.mean(diffs) if diffs else 0.0,
        se=statistics.stdev(diffs) / math.sqrt(n) if n > 1 else float("nan"),
        weeks_ahead=sum(d > 0 for d in diffs),
        weeks_behind=sum(d < 0 for d in diffs),
        signal_pct=sig[0] / sum(sig) if sum(sig) else float("nan"),
    )


def table(rows: list[tuple[str, dict]]) -> str:
    out = ["| split | weeks | games | W-L | win% (95% CI) | pts/wk | vs random/wk (se) | weeks +/- | signal win% |",
           "|---|---|---|---|---|---|---|---|---|"]
    for name, s in rows:
        out.append(
            f"| {name} | {s['weeks']} | {s['games']} | {s['w']}-{s['l']} | "
            f"{s['pct']:.1%} ({s['ci'][0]:.1%}-{s['ci'][1]:.1%}) | "
            f"{s['pts_wk']:.2f} | {s['vs_random']:+.2f} ({s['se']:.2f}) | "
            f"{s['weeks_ahead']}/{s['weeks_behind']} | {s['signal_pct']:.1%} |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report"])
    ap.add_argument("--variant", default="shipped", choices=sorted(VARIANTS))
    ap.add_argument("--no-live", action="store_true",
                    help="skip the 2026 live weeks (needs nflverse)")
    a = ap.parse_args()
    pick = VARIANTS[a.variant]
    rows = [("train 2014-19", score(history(Split.TRAIN), pick)),
            ("test 2020-22", score(history(Split.VALIDATE), pick))]
    if not a.no_live:
        rows.append(("live 2026 (CBS)", score(live_games(), pick)))
    print(f"variant: {a.variant}\n")
    print(table(rows))


if __name__ == "__main__":
    main()
