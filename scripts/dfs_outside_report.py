"""Whose optimizer do we (and the field) resemble? NBA, from the captures in
data/dfs_outside_nba.csv + data/dfs_outside_lineups_nba.csv (edge/dfs_outside.py) and the
contest exports, matched by draft group.

    python3 scripts/dfs_outside_report.py [--json OUT]

Per source, slate = unit (n slates printed; under ~5 slates it is not enough data):
  projection   MAE vs DK points on players every compared source projects 10+ (ours too)
  vs ours      players shared between the source's optimal lineup and ours, cash and GPP
  vs field     players shared with the field's most-copied lineup in each export of that
               type, and how often the source's lineup IS that lineup
  result       the source lineup's DK points and field percentile in each contest
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from edge import dfs_outside  # noqa: E402
from edge.names import norm  # noqa: E402

SLOT = re.compile(r"\b(PG|SG|SF|PF|C|G|F|UTIL)\s")


def exports() -> dict:
    """{gid: [(contest id, type, [(points, frozenset lineup)], {norm name: DK pts})]}."""
    meta = json.loads((ROOT / "data" / "contest_meta.json").read_text())
    out: dict = {}
    for cid, m in meta.items():
        if m.get("sport") != "NBA" or m.get("type") not in ("cash", "gpp"):
            continue
        f = ROOT / "data" / f"contest-standings-{cid}.csv"
        if not f.exists():
            continue
        lus, pts = [], {}
        for r in csv.reader(f.open(encoding="utf-8-sig")):
            if not r or r[0] == "Rank":
                continue
            if len(r) > 10 and r[7]:
                pts[norm(r[7])] = float(r[10] or 0)
            if r[5]:
                parts = SLOT.split(" " + r[5] + " ")
                lus.append((float(r[4] or 0), frozenset(norm(x.strip()) for x in parts[2::2])))
        if lus:
            out.setdefault(str(m.get("draft_group")), []).append((cid, m["type"], lus, pts))
    return out


def lineups(path: Path) -> dict:
    """{gid: {(source, mode): frozenset(norm names)}}."""
    out: dict = {}
    if not path.exists():
        return out
    for r in csv.DictReader(path.open()):
        out.setdefault(r["gid"], {}).setdefault((r["source"], r["mode"]), set()).add(norm(r["player"]))
    return {g: {k: frozenset(v) for k, v in d.items()} for g, d in out.items()}


def ours(gid: str, date: str) -> dict:
    f = ROOT / "data" / f"dfs_lineups_nba_{date}.csv"
    out: dict = {}
    if f.exists():
        for r in csv.DictReader(f.open()):
            if r.get("gid") == gid and r.get("mode") in ("cash", "gpp"):
                out.setdefault(r["mode"], set()).add(norm(r["player"]))
    return {k: frozenset(v) for k, v in out.items()}


def our_proj(gid: str) -> dict:
    out = {}
    for r in csv.DictReader((ROOT / "data" / "dfs_proj_log_nba.csv").open()):
        if r.get("gid") == gid:
            out[norm(r["player"])] = float(r["proj"] or 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    cap = dfs_outside.load()
    lus = lineups(dfs_outside.LINEUPS)
    ex = exports()
    acc = collections.defaultdict(list)        # source -> [slate MAE]
    rel = collections.defaultdict(list)        # (source, mode, what) -> [values]
    slates = []
    for gid, srcs in sorted(cap.items()):
        if gid not in ex:
            continue
        date = next(iter(next(iter(srcs.values())).values()))["date"]
        truth = {}
        for _, _, _, pts in ex[gid]:
            truth.update(pts)
        projs = {"ours": our_proj(gid)}
        projs.update({s: {norm(n): float(r["proj"]) for n, r in rows.items()} for s, rows in srcs.items()})
        slates.append(date)
        for s, pr in projs.items():
            common = [n for n in pr if n in truth and n in projs["ours"] and projs["ours"][n] >= 10]
            if len(common) >= 15:
                acc[s].append((sum(abs(pr[n] - truth[n]) for n in common) / len(common), len(common)))
        mine = ours(gid, date)
        for (src, mode), lu in lus.get(gid, {}).items():
            if mode in mine:
                rel[(src, mode, "shared with ours")].append(len(lu & mine[mode]))
            for cid, typ, field, pts in ex[gid]:
                if typ != mode:
                    continue
                top, n = collections.Counter(l for _, l in field).most_common(1)[0]
                rel[(src, mode, "shared with field's most-copied")].append(len(lu & top))
                rel[(src, mode, "IS the most-copied")].append(int(lu == top))
                sc = sum(pts.get(p, 0.0) for p in lu)
                rel[(src, mode, "field pctile")].append(sum(f < sc for f, _ in field) / len(field))
        for mode, lu in mine.items():
            for cid, typ, field, pts in ex[gid]:
                if typ == mode:
                    top, _ = collections.Counter(l for _, l in field).most_common(1)[0]
                    rel[("ours", mode, "shared with field's most-copied")].append(len(lu & top))
                    rel[("ours", mode, "IS the most-copied")].append(int(lu == top))
    n = len(set(slates))
    print(f"# Whose optimizer do we resemble? NBA, {n} graded slate(s)"
          + (" -- NOT ENOUGH DATA (under 5 slates)" if n < 5 else ""))
    print("\n## Projection MAE vs DK points (players we project 10+, slate mean)\n")
    print("| source | slates | MAE | players/slate |\n|---|---|---|---|")
    for s, v in sorted(acc.items(), key=lambda kv: sum(x for x, _ in kv[1]) / len(kv[1])):
        print(f"| {s} | {len(v)} | {sum(x for x, _ in v) / len(v):.2f} | {sum(c for _, c in v) / len(v):.0f} |")
    print("\n## Lineups (8 players)\n")
    print("| source | mode | measure | mean | n |\n|---|---|---|---|---|")
    for (s, m, w), v in sorted(rel.items()):
        print(f"| {s} | {m} | {w} | {sum(v) / len(v):.2f} | {len(v)} |")
    if a.json:
        Path(a.json).write_text(json.dumps({"slates": n, "mae": {s: v for s, v in acc.items()},
                                            "lineups": {"|".join(k): v for k, v in rel.items()}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
