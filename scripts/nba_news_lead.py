#!/usr/bin/env python3
"""Who reports an NBA scratch first? (Adam, 2026-10-29: "we need to find out who is
the fastest to reporting who's out.")

For every player a source called Out (or Doubtful, with --doubtful) on a night, the
first time each source said so, polled by the edge-nba-scratch timer:

  rotowire   RotoWire's lineups page            data/nba_news_lead_log.csv
  official   the NBA's official injury report   data/nba_news_lead_log.csv
  dk         DraftKings' player status OUT      data/nba_lineup_log.csv
  nbacom     NBA.com's inactive list            data/nba_lineup_log.csv

The night is the ET date of the sighting. A player counts once per night; a source
that never flagged him is "missed". Lead = minutes ahead of the LAST source to flag
him; the table gives per source: n first (ties shared), median minutes behind the
first, and how often it missed. The timer polls every ~2 min, so ties within one
poll are ties. A week (~50 nights' outs) before choosing a source; fewer is not
enough data.

    python3 scripts/nba_news_lead.py
    python3 scripts/nba_news_lead.py --since 2026-10-30 --doubtful --list
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm   # noqa: E402

ET = ZoneInfo("America/New_York")
SOURCES = ("rotowire", "official", "dk", "nbacom")


def sightings(statuses: set[str]) -> dict:
    """{(night, player norm): {source: first datetime}}"""
    first: dict = defaultdict(dict)

    def see(src, when, name):
        t = datetime.fromisoformat(when).astimezone(ET)
        k = (t.date().isoformat(), norm(name))
        if src not in first[k] or t < first[k][src]:
            first[k][src] = t

    f = ROOT / "data" / "nba_news_lead_log.csv"
    if f.exists():
        for r in csv.DictReader(f.open()):
            if r["status"] in statuses:
                see(r["source"], r["seen_at"], r["player"])
    f = ROOT / "data" / "nba_lineup_log.csv"
    if f.exists():
        for r in csv.DictReader(f.open()):
            if r["dk_status"] == "OUT":
                see("dk", r["seen_at"], r["player"])
            if r["inactive"] == "True":
                see("nbacom", r["seen_at"], r["player"])
    return first


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-10-30", help="first night (RotoWire logged from 10/29 late)")
    ap.add_argument("--doubtful", action="store_true", help="count Doubtful as well as Out")
    ap.add_argument("--list", action="store_true", help="print each player's sightings")
    a = ap.parse_args()
    first = {k: v for k, v in sightings({"Out", "Doubtful"} if a.doubtful else {"Out"}).items()
             if k[0] >= a.since}
    nights = {k[0] for k in first}
    print(f"{len(first)} player-nights flagged, {len(nights)} nights from {a.since}"
          + ("  (not enough data: < 5 nights)" if len(nights) < 5 else ""))
    wins, behind, missed = defaultdict(float), defaultdict(list), defaultdict(int)
    for k, s in sorted(first.items()):
        t0 = min(s.values())
        best = [src for src, t in s.items() if (t - t0).total_seconds() < 150]   # within one poll
        for src in SOURCES:
            if src not in s:
                missed[src] += 1
            else:
                behind[src].append((s[src] - t0).total_seconds() / 60)
        for src in best:
            wins[src] += 1 / len(best)
        if a.list:
            print(f"  {k[0]} {k[1]:24} " + "  ".join(f"{src} {s[src]:%H:%M}" if src in s else f"{src} -"
                                                      for src in SOURCES))
    print(f"\n| source | first (ties shared) | median min behind first | missed |\n|---|---|---|---|")
    for src in SOURCES:
        med = f"{st.median(behind[src]):.0f}" if behind[src] else "-"
        print(f"| {src} | {wins[src]:.1f} | {med} | {missed[src]} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
