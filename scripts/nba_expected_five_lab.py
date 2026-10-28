#!/usr/bin/env python3
"""How far to trust NBA.com's EXPECTED starting five, by time before tip.

    python3 scripts/nba_expected_five_lab.py report       # accuracy by lead time, train / test
    python3 scripts/nba_expected_five_lab.py --show       # also each promoted starter's first listing

edge/nba_minutes.EXPECTED_W (0.70) was chosen 2026-10-21 on SIMULATED wrong fives
(no history of Expected fives existed). Since the 2026-27 opener the snapshot
timer commits data/nba_lineups_snapshot.json to the `data` branch every time
NBA.com's lineups change, so the five as it stood at any moment is in git: the
state at time t = the last commit at or before t. Ground truth = the day's final
file (data/nba_lineups/<yyyymmdd>.json, edge/nba_lineups.fetch).

Per team-game and lead bucket: is the Expected five exactly the final five, how
many of the five are right, and -- the Reed / Ajay case -- is a PROMOTED starter
(in the final five, did not start his team's previous game) already listed.
Regular season only (from 2026-10-20). Split by date: train before TEST_FROM.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import date as D, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import nba_lineups                     # noqa: E402

SNAP = "data/nba_lineups_snapshot.json"
FIRST = "2026-10-20"
TEST_FROM = "2026-10-25"
#: hours before tip
BUCKETS = ((6, 99, "6h+"), (3, 6, "3-6h"), (1.5, 3, "1.5-3h"), (0.75, 1.5, "45-90m"), (0, 0.75, "<45m"))
GIT = ["git", "-C", str(ROOT)]


def snapshots() -> list[tuple[datetime, dict]]:
    log = subprocess.run(GIT + ["log", "--format=%H %cI", "origin/data", "--", SNAP],
                         capture_output=True, text=True).stdout.split("\n")
    out = []
    for line in log:
        if not line:
            continue
        h, t = line.split()
        body = json.loads(subprocess.run(GIT + ["show", f"{h}:{SNAP}"], capture_output=True, text=True).stdout or "{}")
        d = body.get("date", "").replace("-", "")
        body["date"] = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else ""
        if body["date"] >= FIRST:
            out.append((datetime.fromisoformat(t), body))
    return sorted(out, key=lambda x: x[0])


def tip(game: dict) -> datetime | None:
    t = game.get("gameTimeUTC") or game.get("gameDateTimeUTC")
    if t:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    return None


def fives(body: dict) -> dict:
    """{(game_id, team): (frozenset starters, confirmed?)}"""
    out = {}
    for g in nba_lineups.parse(body):
        by = defaultdict(list)
        for p in g["players"]:
            if p["starter"]:
                by[p["team"]].append(p)
        for t, ps in by.items():
            out[(g["game_id"], t)] = (frozenset(str(p["id"]) for p in ps), all(p["confirmed"] for p in ps))
    return out


def tips(body: dict) -> dict:
    out = {}
    for g in body.get("games", []):
        t = None
        txt = g.get("gameStatusText", "")
        try:
            hm = datetime.strptime(txt.replace(" ET", "").strip().upper(), "%I:%M %p")
            d0 = D.fromisoformat(body["date"])
            # ET -> UTC: EDT (UTC-4) through 11/1, EST after
            off = 4 if d0 < D(2026, 11, 1) else 5
            t = datetime(d0.year, d0.month, d0.day, hm.hour, hm.minute) + timedelta(hours=off)
        except ValueError:
            pass
        if t:
            out[g["gameId"]] = t
    return out


def build():
    snaps = snapshots()
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    dates = sorted({b["date"] for _, b in snaps if b["date"] < today})     # final files only
    rows = []
    prev_start: dict = {}           # team -> starters of its previous game
    for d in dates:
        final = fives(nba_lineups.fetch(D.fromisoformat(d)))
        day = [(t, b) for t, b in snaps if b["date"] == d]
        tp = {}
        for _, b in day:
            tp.update(tips(b))
        for (gid, team), (truth, _) in final.items():
            if len(truth) != 5 or gid not in tp:
                continue
            promoted = truth - prev_start.get(team, truth)
            seen = {}
            for t, b in day:
                lead = (tp[gid] - t.astimezone(timezone.utc).replace(tzinfo=None)).total_seconds() / 3600
                if lead < 0:
                    continue
                f = fives(b).get((gid, team))
                if not f or len(f[0]) != 5:
                    continue
                for lo, hi, lab in BUCKETS:
                    if lo <= lead < hi:
                        seen[lab] = f       # latest state inside the bucket
            for lab, (five, conf) in seen.items():
                rows.append({"date": d, "team": team, "bucket": lab, "exact": five == truth,
                             "right": len(five & truth), "confirmed": conf,
                             "promoted": len(promoted), "promoted_listed": len(promoted & five)})
        for (gid, team), (truth, _) in final.items():
            if len(truth) == 5:
                prev_start[team] = truth
    return rows


def report(rows):
    for part, sel in (("train", lambda r: r["date"] < TEST_FROM), ("test", lambda r: r["date"] >= TEST_FROM)):
        rs = [r for r in rows if sel(r)]
        print(f"\n{part}: {len({(r['date'], r['team']) for r in rs})} team-games, "
              f"dates {sorted({r['date'] for r in rs})[0]}..{sorted({r['date'] for r in rs})[-1]}")
        print("| hours before tip | team-games | five exactly right | starters right | wrong starters / 5 "
              "| promoted starters listed | confirmed |")
        print("|---|---|---|---|---|---|---|")
        for _, _, lab in BUCKETS:
            b = [r for r in rs if r["bucket"] == lab]
            if not b:
                continue
            pr = sum(r["promoted"] for r in b)
            print(f"| {lab} | {len(b)} | {100 * sum(r['exact'] for r in b) / len(b):.0f}% "
                  f"| {sum(r['right'] for r in b) / len(b):.2f} | {100 * (1 - sum(r['right'] for r in b) / (5 * len(b))):.1f}% "
                  f"| {sum(r['promoted_listed'] for r in b)}/{pr} "
                  f"| {100 * sum(r['confirmed'] for r in b) / len(b):.0f}% |")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="report")
    a = ap.parse_args()
    report(build())
