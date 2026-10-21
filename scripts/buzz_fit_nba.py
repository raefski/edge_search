#!/usr/bin/env python3
"""Does NBA DFS buzz (YouTube shows + podcasts) improve GPP ownership? Held out by night.

    python3 scripts/buzz_fit_nba.py                 # after scripts/buzz_nba.py --contests
    python3 scripts/buzz_fit_nba.py --channels      # also: which shows carry the signal

Each NBA GPP night in data/contests.db is rebuilt as it stood
(scripts/nba_ownership_fit.rebuild), the night's buzz is attached from
data/buzz_nba_videos.csv, and edge/dfs_nba_theory.add_ownership -- the shipped
function -- is scored against the export's %Drafted with BUZZ_BETA on a grid,
leave one NIGHT out (fit on the others, score the held-out one). Three versions
of "buzz", because a show's words are not readable until its transcript exists:

  ready      only uploads, live streams that ENDED before lock, podcast
             episodes published before lock -- what a live build sees
  +podcast   ready, YouTube and podcasts (ready alone is YouTube + podcasts
             too; this splits YouTube-only out for comparison)
  all talk   everything said before lock, read afterwards -- the upper bound

Also when the talk happens (ET hour the show aired), the user's question: it
should be morning / early afternoon shows, since the rest air after lock.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_nba_theory as T  # noqa: E402
from edge.names import norm  # noqa: E402
from scripts.contest_stats import cluster_bootstrap, spearman  # noqa: E402
from scripts.nba_ownership_fit import rebuild  # noqa: E402

import numpy as np  # noqa: E402

ET = ZoneInfo("America/New_York")
VIDEOS = ROOT / "data" / "buzz_nba_videos.csv"
BETAS = [round(0.1 * i, 1) for i in range(0, 21)]


def score(pool, actual, beta):
    T.add_ownership(pool, buzz_beta=beta)
    pairs = [(p["own"], actual[norm(p["name"])]) for p in pool if norm(p["name"]) in actual]
    pred, act = zip(*pairs)
    return statistics.fmean(abs(a - b) for a, b in pairs), spearman(pred, act)


def attach(pool, mentions: dict):
    for p in pool:
        p["buzz"] = float(mentions.get(p["name"], 0))


def loo(nights, key: str):
    """Held-out MAE / rank per night with BUZZ_BETA fitted on the other nights."""
    for n in nights:
        n["tab"] = {}
        attach(n["pool"], n[key])
        for b in BETAS:
            n["tab"][b] = score(n["pool"], n["actual"], b)
    out = []
    for i, n in enumerate(nights):
        best = min(BETAS, key=lambda b: statistics.fmean(m["tab"][b][0] for j, m in enumerate(nights) if j != i))
        out.append((n["date"], best, *n["tab"][best], *n["tab"][0.0]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--channels", action="store_true")
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    rows = list(csv.DictReader(VIDEOS.open()))
    conn = sqlite3.connect(ROOT / "data" / "contests.db")
    cons = conn.execute("SELECT contest_id, draft_group, slate_date, live_entries FROM contest "
                        "WHERE sport='NBA' AND type='gpp' AND final=1 ORDER BY slate_date").fetchall()
    have = {r["date"] for r in rows}
    nights = []
    for cid, gid, d, field in cons:
        if d not in have:
            print(f"{d}: no buzz collected", file=sys.stderr)
            continue
        pool = rebuild(int(gid), d)
        actual = dict(conn.execute("SELECT player_norm, pct_drafted FROM player_result WHERE contest_id=?", (cid,)))
        night = [r for r in rows if r["date"] == d and r["player"]]
        def tally(keep):
            c = Counter()
            for r in night:
                if keep(r):
                    c[r["player"]] += int(r["mentions"])
            return c
        nights.append({"date": d, "cid": cid, "field": field, "pool": pool, "actual": actual, "rows": night,
                       "yt_ready": tally(lambda r: r["source"] == "youtube" and r["ready"] == "1"),
                       "ready": tally(lambda r: r["ready"] == "1"),
                       "all": tally(lambda r: True)})
    out = [f"# NBA buzz -> GPP ownership: {len(nights)} nights, leave one night out\n",
           "| buzz | held-out MAE | prior MAE | rank corr (prior) | nights better | BUZZ_BETA picked |",
           "|---|---|---|---|---|---|"]
    for key, lab in (("yt_ready", "YouTube, readable before lock"), ("ready", "YouTube + podcasts, readable before lock"),
                     ("all", "all pre-lock talk (upper bound)")):
        res = loo(nights, key)
        h, p = [r[2] for r in res], [r[4] for r in res]
        diff = np.array(h) - np.array(p)
        e, lo, hi, _ = cluster_bootstrap(np.arange(len(diff)), lambda i: diff[i].mean(), n_boot=a.boot, seed=11)
        out.append(f"| {lab} | {statistics.fmean(h):.3f} ({e:+.3f} [{lo:+.3f}, {hi:+.3f}]) | {statistics.fmean(p):.3f} | "
                   f"{statistics.fmean(r[3] for r in res):+.3f} ({statistics.fmean(r[5] for r in res):+.3f}) | "
                   f"{int((diff < 0).sum())}/{len(diff)} | {Counter(r[1] for r in res).most_common(2)} |")
    # coverage and timing
    out += ["\n## What was collected\n", "| night | sources ready / late | players named | mentions ready / all |",
            "|---|---|---|---|"]
    for n in nights:
        vids = {(r["source"], r["video_id"]): r["ready"] for r in n["rows"]}
        out.append(f"| {n['date']} | {sum(v == '1' for v in vids.values())} / {sum(v == '0' for v in vids.values())} | "
                   f"{len(n['all'])} | {sum(n['ready'].values())} / {sum(n['all'].values())} |")
    hours = Counter()
    for n in nights:
        for r in n["rows"]:
            hr = datetime.fromisoformat(r["aired"]).astimezone(ET).hour
            band = "night before" if datetime.fromisoformat(r["aired"]).astimezone(ET).date().isoformat() < n["date"] \
                else ("morning (<12)" if hr < 12 else "early afternoon (12-16)" if hr < 16 else "late afternoon/evening")
            hours[(band, r["ready"])] += int(r["mentions"])
    out += ["\n## When the talk airs (ET, mentions)\n", "| aired | readable before lock | only after |", "|---|---|---|"]
    for band in ("night before", "morning (<12)", "early afternoon (12-16)", "late afternoon/evening"):
        out.append(f"| {band} | {hours[(band, '1')]:,} | {hours[(band, '0')]:,} |")
    if a.channels:
        # which shows' picks run ahead of the prior's ownership miss
        out += ["\n## Shows: their mentions vs the prior's ownership miss (actual - prior), within night\n",
                "| show | nights | players named | rank corr with miss |", "|---|---|---|---|"]
        for n in nights:
            T.add_ownership(n["pool"], buzz_beta=0.0)
            n["miss"] = {p["name"]: n["actual"].get(norm(p["name"]), 0) - p["own"] for p in n["pool"]}
        chans = defaultdict(lambda: ([], []))
        nset = defaultdict(set)
        for n in nights:
            per = defaultdict(Counter)
            for r in n["rows"]:
                per[r["channel"]][r["player"]] += int(r["mentions"])
            for ch, c in per.items():
                nset[ch].add(n["date"])
                for name, miss in n["miss"].items():
                    chans[ch][0].append(np.log1p(c.get(name, 0)))
                    chans[ch][1].append(miss)
        for ch, (x, y) in sorted(chans.items(), key=lambda kv: -len(nset[kv[0]])):
            if len(nset[ch]) >= 3 and np.std(x) > 0:
                out.append(f"| {ch} | {len(nset[ch])} | {int(sum(1 for v in x if v > 0))} | {spearman(x, y):+.3f} |")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
