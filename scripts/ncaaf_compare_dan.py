#!/usr/bin/env python3
"""Benchmark this build's NCAAF projections + ownership against Dan's sheet.

    python3 scripts/ncaaf_compare_dan.py Dans_cfb-projections-all-2026-10-03.csv
    python3 scripts/ncaaf_compare_dan.py <file> --date 2026-10-03 --gid 154161

Adam's goal is an in-house college projection + ownership model that does not
depend on Dan's paid sheet. Before the games are played the honest test is a
head-to-head on the same board: (1) COVERAGE -- which players Dan projects
that this build's pool lacks, weighted by how much Dan thinks they matter,
because a missing player is a player the optimizer can never roster; (2)
AGREEMENT on the overlap, by position; (3) OWNERSHIP. Once the games are
final, `--grade <contest export>` scores both against real points.

THIS FILE IS NEVER COMMITTED. Dan's sheet is a paid product and the repo is
public (.gitignore). The join is on edge.names.norm (suffix-free) plus team,
so "Michael Hawkins Jr." and "Michael Hawkins" are one player.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402

LOG = ROOT / "data" / "dfs_proj_log_ncaaf.csv"
#: Dan's team codes vs DK's, only where they differ.
TEAM_ALIAS: dict = {}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_dan(path: str) -> list[dict]:
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            name = (r.get("Name") or r.get("Player") or "").strip()
            if not name:
                continue
            team = (r.get("Team") or "").strip()
            out.append({"name": name, "pos": (r.get("Position") or r.get("Pos") or "").strip(),
                        "team": TEAM_ALIAS.get(team, team), "salary": _f(r.get("Salary")),
                        "proj": _f(r.get("Proj")), "own": _f(r.get("Ownership")),
                        "floor": _f(r.get("Floor")), "ceil": _f(r.get("Ceiling")),
                        "key": norm(name)})
    return out


def load_ours(date: str | None, gid: str | None):
    with LOG.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    date = date or max(r["date"] for r in rows)
    rows = [r for r in rows if r["date"] == date and (gid is None or r["gid"] == gid)]
    if not rows:
        raise SystemExit(f"no rows for {date} in {LOG.name}")
    gid = rows[0]["gid"]
    return date, gid, [r for r in rows if r["gid"] == gid]


def index(rows, name, team):
    """{(key, team): row} with a key-only fallback when the key is unique."""
    by_kt, by_k = {}, defaultdict(list)
    for r in rows:
        by_kt[(norm(r[name]), r[team])] = r
        by_k[norm(r[name])].append(r)
    return by_kt, by_k


def stats(pairs):
    if len(pairs) < 3:
        return None
    a, b = [p[0] for p in pairs], [p[1] for p in pairs]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = sum((x - ma) ** 2 for x in a) ** 0.5
    sb = sum((y - mb) ** 2 for y in b) ** 0.5
    corr = sum((x - ma) * (y - mb) for x, y in pairs) / (sa * sb) if sa and sb else float("nan")
    return {"n": len(pairs), "corr": corr, "bias": ma - mb,
            "mae": statistics.fmean(abs(x - y) for x, y in pairs)}


def show(tag, pairs):
    s = stats(pairs)
    print(f"  {tag:10} " + ("too few" if not s else
          f"n={s['n']:3}  corr {s['corr']:.2f}  ours-minus-Dan {s['bias']:+.2f}  MAE {s['mae']:.2f}"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dan_file")
    ap.add_argument("--date")
    ap.add_argument("--gid")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-dan", type=float, default=8.0,
                    help="Dan projection above which a missing player counts as a real gap")
    args = ap.parse_args()

    dan_all = load_dan(args.dan_file)
    # Dan lists the whole board and zeroes the players he does not project
    dan = [d for d in dan_all if (d["proj"] or 0) > 0]
    date, gid, ours = load_ours(args.date, args.gid)
    by_kt, by_k = index(ours, "player", "team")
    print(f"Dan: {len(dan)} projected of {len(dan_all)} listed | ours: {len(ours)} projected | slate {gid} ({date})\n")

    pairs, own_pairs, per_pos, missing = [], [], defaultdict(list), []
    matched = []
    for d in dan:
        r = by_kt.get((d["key"], d["team"]))
        if r is None and len(by_k.get(d["key"], [])) == 1:
            r = by_k[d["key"]][0]
        if r is None:
            missing.append(d)
            continue
        matched.append((d, r))
        o, dp = _f(r["proj"]), d["proj"]
        if o is not None and dp is not None:
            pairs.append((o, dp))
            per_pos[d["pos"]].append((o, dp))
        if _f(r["own"]) is not None and d["own"] is not None:
            own_pairs.append((_f(r["own"]), d["own"]))
    seen = {id(r) for _, r in matched}
    only_ours = [r for r in ours if id(r) not in seen]
    print(f"matched {len(seen)} of Dan's {len(dan)} projected; "
          f"we project {len(only_ours)} he does not: "
          + ", ".join(f"{r['player']} ({r['dk_pos']} {_f(r['proj']):.1f})" for r in only_ours[:8]))

    dan_live = [d for d in dan if (d["proj"] or 0) >= args.min_dan]
    gap = [d for d in missing if (d["proj"] or 0) >= args.min_dan]
    tot = sum(d["proj"] for d in dan_live) or 1
    print(f"COVERAGE: Dan has {len(dan_live)} players projected >= {args.min_dan:g}; "
          f"we are missing {len(gap)} of them "
          f"({sum(d['proj'] for d in gap) / tot:.0%} of that projected mass)")
    by_pos = defaultdict(lambda: [0, 0])
    for d in dan_live:
        by_pos[d["pos"]][0] += 1
    for d in gap:
        by_pos[d["pos"]][1] += 1
    print("  missing by position: " + ", ".join(f"{p} {m}/{n}" for p, (n, m) in sorted(by_pos.items())))
    print(f"  biggest gaps (Dan proj, own, salary):")
    for d in sorted(gap, key=lambda d: -d["proj"])[:args.top]:
        print(f"    {d['pos']:3} {d['name'][:24]:24} {d['team']:5} ${d['salary'] or 0:>6,.0f}  "
              f"{d['proj']:5.1f}  {d['own'] if d['own'] is not None else float('nan'):5.1f}%")

    print("\nPROJECTION on the overlap (ours vs Dan):")
    show("all", pairs)
    for p in sorted(per_pos):
        show(p, per_pos[p])
    print("\nOWNERSHIP on the overlap (ours vs Dan, % of field):")
    show("all", own_pairs)
    print("\nlargest projection disagreements:")
    rows = sorted(((abs(_f(r["proj"]) - d["proj"]), d, r) for d, r in matched
                   if _f(r["proj"]) is not None and d["proj"] is not None), key=lambda t: -t[0])
    print(f"    {'player':24} {'pos':3} {'team':5} {'salary':>7} {'ours':>6} {'Dan':>6}   own ours/Dan")
    for _, d, r in rows[:args.top]:
        print(f"    {d['name'][:24]:24} {d['pos']:3} {d['team']:5} {d['salary'] or 0:7,.0f} "
              f"{_f(r['proj']):6.1f} {d['proj']:6.1f}   {_f(r['own']) or 0:4.1f}/{d['own'] or 0:4.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
