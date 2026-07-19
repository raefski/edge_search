#!/usr/bin/env python3
"""Measure DUPLICATE-lineup mass in real DK contest fields (DFS_IMPROVEMENT_PLAN §3).

Duplicated lineups split prizes when they win -- chalk builds duplicate, leverage
builds mostly don't, so dupe mass is a real (and measurable) part of why leverage
construction earns more than its raw finish percentile suggests. This measures,
per contest export: entries, distinct lineups, dupe rate, the most-duplicated
lineup, and whether the WINNING lineup was duplicated.

Usage: python3 scripts/dfs_dupe_measure.py
"""
import csv
import glob
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.dfs_calibration import load_contest_meta  # noqa: E402


def main():
    print(f"{'contest':11} {'type':5} {'entries':>7} {'distinct':>8} {'dupe%':>6} "
          f"{'maxdup':>6} {'top-score dup':>13}")
    tot_e = tot_d = 0
    by_size = {"small(<=100)": [0, 0], "mid(101-500)": [0, 0], "large(>500)": [0, 0]}
    for f in sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv"))):
        cid = Path(f).stem.replace("contest-standings-", "")
        meta = load_contest_meta(f)
        lineups, best = {}, None
        with open(f, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                lu = (row.get("Lineup") or "").strip()
                eid = row.get("EntryId")
                if not lu or not eid or eid in lineups:
                    continue
                # normalize: order-independent player set, so the same 10 players
                # in different display order still count as the same lineup
                key = "|".join(sorted(lu.split()))
                lineups[eid] = key
                try:
                    pts = float(row.get("Points") or 0)
                except ValueError:
                    continue
                if best is None or pts > best[0]:
                    best = (pts, key)
        if not lineups:
            continue
        counts = Counter(lineups.values())
        n, d = len(lineups), len(counts)
        dupe_rate = 1 - d / n
        maxdup = counts.most_common(1)[0][1]
        windup = counts[best[1]] if best else 0
        tot_e += n; tot_d += d
        bucket = "small(<=100)" if n <= 100 else ("mid(101-500)" if n <= 500 else "large(>500)")
        by_size[bucket][0] += n; by_size[bucket][1] += d
        print(f"{cid:11} {meta['type']:5} {n:>7} {d:>8} {dupe_rate:>5.1%} "
              f"{maxdup:>6} {'YES x' + str(windup) if windup > 1 else 'no':>13}")
    print(f"\npooled: {tot_e} entries, {tot_d} distinct -> {1 - tot_d / tot_e:.1%} of entries are duplicates")
    for b, (n, d) in by_size.items():
        if n:
            print(f"  {b:14} {1 - d / n:.1%} dupes  ({n} entries)")


if __name__ == "__main__":
    main()
