#!/usr/bin/env python3
"""Held-out harness for the college build's target metric: DK points vs the
pre-lock projection, per laddered player.

    python3 scripts/ncaaf_lab.py report                 # shipped model, every slate
    python3 scripts/ncaaf_lab.py report --cutoff 2026-10-11
    python3 scripts/ncaaf_lab.py dump out.csv           # the joined rows, for a critic

WHY LOGGED PROJECTIONS AND NOT A REPLAY
`data/dfs_proj_log_ncaaf.csv` is what the app actually showed before lock, so
grading it is live-faithful by construction: no leak from a later odds scan,
no rebuild with today's code. A candidate fix is evaluated as a transform of
these logged rows (plus whatever else was knowable pre-lock), fitted on slates
before `--cutoff` and reported on the slates after it -- the slate is the unit.

TRUTH
DK's own FPTS from the contest exports in `data/contests.db`, joined by DRAFT
GROUP (never "the nearest slate"; the old calibration script's Jaccard match is
exactly the trap CLAUDE.md warns about). The export only lists drafted players,
so the graded population is "logged AND drafted by someone in a contest on that
draft group". `report` prints the share of logged players that survive, so the
selection is visible: an undrafted laddered player who scored 0 is missing,
which biases the graded set slightly toward players who played.
"""
from __future__ import annotations

import argparse
import collections
import csv
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.names import norm  # noqa: E402
from edge.ncaaf import base_position  # noqa: E402

PROJ_LOG = ROOT / "data" / "dfs_proj_log_ncaaf.csv"
CONTESTS = ROOT / "data" / "contests.db"

#: 10/11 splits the first three Saturdays (9/26, 10/3; 10/10 Main was never
#: logged) from everything since. Chosen before looking at any error.
DEFAULT_CUTOFF = "2026-10-11"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def truth() -> dict:
    """{draft_group: {norm_name: fpts}} from every NCAAF export, cash and GPP.
    A player's FPTS is the same in every contest on one draft group."""
    out: dict = collections.defaultdict(dict)
    con = sqlite3.connect(CONTESTS)
    q = ("select c.draft_group, p.name, p.fpts from player_result p "
         "join contest c using(contest_id) where c.sport='NCAAF' "
         "and c.draft_group is not null")
    for dg, name, fpts in con.execute(q):
        if fpts is not None:
            out[int(dg)][norm(name)] = float(fpts)
    return dict(out)


def joined() -> tuple[list[dict], dict]:
    """Logged rows that have a truth, plus {gid: (logged, graded)} coverage."""
    tr = truth()
    rows, cover = [], collections.Counter(),
    logged = collections.Counter()
    graded = collections.Counter()
    with PROJ_LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                gid = int(r["gid"])
            except (TypeError, ValueError):
                continue
            if gid not in tr or _f(r.get("proj")) is None:
                continue
            logged[gid] += 1
            act = tr[gid].get(norm(r["player"]))
            if act is None:
                continue
            graded[gid] += 1
            rows.append({
                "date": r["date"], "gid": gid, "player": r["player"],
                "team": r["team"], "opp": r["opp_team"],
                "pos": base_position(r["dk_pos"]), "salary": _f(r["salary"]),
                "proj": _f(r["proj"]), "sd": _f(r["sd"]),
                "band": _f(r.get("band_points")), "dk_fppg": _f(r.get("dk_fppg")),
                "games": _f(r.get("games")), "actual": act,
            })
    return rows, {g: (logged[g], graded[g]) for g in logged}


def stats(pairs: list[tuple[float, float]]) -> dict:
    n = len(pairs)
    if not n:
        return {"n": 0}
    err = [a - p for p, a in pairs]
    return {"n": n, "mae": sum(map(abs, err)) / n,
            "rmse": math.sqrt(sum(e * e for e in err) / n),
            "bias": sum(err) / n}


def table(title: str, groups: dict) -> None:
    print(f"\n{title}")
    print(f"  {'slice':<22}{'n':>5}{'MAE':>8}{'RMSE':>8}{'bias(a-p)':>11}")
    for k, pairs in groups.items():
        s = stats(pairs)
        if s["n"]:
            print(f"  {str(k):<22}{s['n']:>5}{s['mae']:>8.2f}{s['rmse']:>8.2f}"
                  f"{s['bias']:>+11.2f}")


def report(rows: list[dict], cover: dict, cutoff: str, pred=None) -> None:
    pred = pred or (lambda r: r["proj"])
    train = [r for r in rows if r["date"] < cutoff]
    test = [r for r in rows if r["date"] >= cutoff]
    print(f"cutoff {cutoff}: train {len({r['gid'] for r in train})} slates / "
          f"{len(train)} players, test {len({r['gid'] for r in test})} slates / "
          f"{len(test)} players")
    print("coverage (logged -> graded):",
          ", ".join(f"{g} {a}->{b}" for g, (a, b) in sorted(cover.items())))
    for name, part in (("TRAIN", train), ("TEST (held out)", test)):
        groups = {"all": [(pred(r), r["actual"]) for r in part]}
        for pos in ("QB", "RB", "WR"):
            groups[pos] = [(pred(r), r["actual"]) for r in part if r["pos"] == pos]
        for lo, hi in ((0, 5000), (5000, 7000), (7000, 99999)):
            groups[f"${lo//1000}k-{hi//1000 if hi < 99999 else '+'}"] = [
                (pred(r), r["actual"]) for r in part if lo <= (r["salary"] or 0) < hi]
        for gid in sorted({r["gid"] for r in part}):
            d = next(r["date"] for r in part if r["gid"] == gid)
            groups[f"{d} {gid}"] = [(pred(r), r["actual"]) for r in part
                                   if r["gid"] == gid]
        table(name, groups)


def stretch_pred(rows: list[dict], cutoff: str):
    """Critique cycle 1 (not shipped): p' = pivot + k (p - pivot) for QB/RB,
    pivot = train mean projection, k = OLS slope of actual on proj after
    removing each train slate's mean from both (so the old-code level bias
    on train is not refitted). Fitted on train only."""
    train = [r for r in rows if r["date"] < cutoff]
    params = {}
    for pos in ("QB", "RB"):
        part = [r for r in train if r["pos"] == pos]
        by = collections.defaultdict(list)
        for r in part:
            by[r["gid"]].append(r)
        xs, ys = [], []
        for g in by.values():
            mp = sum(r["proj"] for r in g) / len(g)
            ma = sum(r["actual"] for r in g) / len(g)
            xs += [r["proj"] - mp for r in g]
            ys += [r["actual"] - ma for r in g]
        k = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
        params[pos] = (sum(r["proj"] for r in part) / len(part), k)
    print("stretch (pivot, k):", {p: tuple(round(v, 2) for v in t)
                                  for p, t in params.items()})

    def pred(r):
        if r["pos"] not in params:
            return r["proj"]
        pivot, k = params[r["pos"]]
        return pivot + k * (r["proj"] - pivot)
    return pred


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    rp.add_argument("--stretch", action="store_true",
                    help="cycle 1 candidate: stretch QB/RB around the train "
                         "position mean by the slate-demeaned train OLS slope")
    dp = sub.add_parser("dump")
    dp.add_argument("out")
    a = ap.parse_args()
    rows, cover = joined()
    if a.cmd == "report":
        report(rows, cover, a.cutoff,
               stretch_pred(rows, a.cutoff) if a.stretch else None)
    else:
        with open(a.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"{len(rows)} rows -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
