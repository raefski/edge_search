#!/usr/bin/env python3
"""Do NHL skaters promoted up the lineup (a starter out, a line shuffle) beat our projection?

    python3 scripts/nhl_promotion.py

The NBA edge is a bench player promoted to starter: minutes jump, salary does
not (NBA_STATUS.md). The hockey version: a skater moved up a line or onto PP1
because someone is out. DailyFaceoff's snapshot at lock (pinned, as
scripts/nhl_rebuild.py reads it) has tonight's line and PP unit AND each
player's ice time over his last 5 games. So, per logged slate:

  slot TOI   median real ice time of each line label (F1..F4, D1..D3) and of
             PP1 membership, measured on these nights from the box score
  promotion  slot TOI tonight minus his last-5 TOI per game (DailyFaceoff):
             + = he is deployed above his recent role
  check 1    does he really play more? (box TOI minus last-5 TOI)
  check 2    did he beat OUR projection (logged, pre-lock)? If promoted
             skaters beat it, the props and our fallback lag the news.

Players with < 3 games in DailyFaceoff's last-5 are left out (no recent role).
Intervals resample nights.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402
from edge.nhl import skater_points  # noqa: E402
from scripts import nhl_rebuild as RB  # noqa: E402
from scripts.contest_stats import cluster_bootstrap  # noqa: E402
from scripts.nhl_ownership_signal import box_key  # noqa: E402

BOX = ROOT / "data" / "nhl_cache" / "box"


def toi_min(s: str | None) -> float:
    if not s or ":" not in s:
        return 0.0
    m, sec = s.split(":")[:2]
    return int(m) + int(sec) / 60


def box_night(date: str) -> dict:
    out = {}
    for f in BOX.glob("2026*.json"):
        b = json.loads(f.read_text())
        if b.get("date") != date:
            continue
        for s in b["skaters"]:
            out[box_key(s["team"], s["name"])] = {
                "dk": float(skater_points(s["goals"], s["assists"], s["shots"], s["blocks"])),
                "toi": toi_min(s.get("toi"))}
    return out


def load() -> pd.DataFrame:
    log = pd.read_csv(ROOT / "data" / "dfs_proj_log_nhl.csv")
    log = log[log.dk_pos != "G"]
    rows = []
    for (date, gid), g in log.groupby(["date", "gid"]):
        try:
            _, snap = RB.git_json("data/nhl_lines_snapshot.json", RB.utc(RB.lock_time(int(gid))))
        except SystemExit:
            continue
        lines = {(t, norm(n)): r for t, rs in (snap.get("lines") or {}).items() for n, r in rs.items()}
        box = box_night(date)
        for r in g.itertuples():
            ln = lines.get((r.team, norm(r.player)))
            b = box.get(box_key(r.team, r.player))
            if not ln or not b or b["toi"] <= 0:
                continue
            l5 = ln.get("last5") or {}
            gp = l5.get("gamesPlayed") or 0
            if gp < 3:
                continue
            recent = ((l5.get("toiMinutes") or 0) + (l5.get("toiSeconds") or 0) / 60) / gp
            rows.append({"date": date, "gid": gid, "name": r.player, "team": r.team,
                         "line": ln.get("line"), "pp1": ln.get("pp") == "PP1", "recent_toi": recent,
                         "toi": b["toi"], "actual": b["dk"], "proj": r.proj, "salary": r.salary,
                         "props": not ({"shots", "points"} & set(str(r.imputed or "").split("|")))})
    d = pd.DataFrame(rows).drop_duplicates(["date", "name"])
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    d = load()
    d = d[d.line.notna()]
    # slot norms measured on these nights: median TOI by line label, + PP1's extra
    base = d[~d.pp1].groupby("line").toi.median()
    pp_extra = (d[d.pp1].groupby("line").toi.median() - base).median()
    d["slot_toi"] = d.line.map(base) + np.where(d.pp1, pp_extra, 0.0)
    d["promotion"] = d.slot_toi - d.recent_toi
    d["toi_gain"] = d.toi - d.recent_toi
    d["res"] = d.actual - d.proj
    d["bucket"] = pd.cut(d.promotion, [-99, -2, 2, 99], labels=["demoted (<= -2 min)", "same role", "promoted (>= +2 min)"])

    def ci(x, col):
        s = x.groupby("date")[col].mean()
        arr = s.to_numpy()
        e, lo, hi, _ = cluster_bootstrap(np.arange(len(arr)), lambda i: arr[i].mean(), n_boot=a.boot, seed=8)
        star = " **" if lo > 0 or hi < 0 else ""
        return f"{e:+.2f} [{lo:+.2f}, {hi:+.2f}]{star} ({len(arr)} nights)"

    out = [f"# NHL promotions vs our projection: {d.date.nunique()} nights, {len(d):,} skater-games\n",
           "Slot TOI (median, these nights): " + ", ".join(f"{k} {v:.1f}" for k, v in base.items())
           + f"; PP1 adds {pp_extra:+.1f} min.\n",
           "| role tonight vs last 5 | skater-games | real TOI minus last-5 TOI | DK pts minus our projection | with props / without |",
           "|---|---|---|---|---|"]
    for b, x in d.groupby("bucket", observed=True):
        out.append(f"| {b} | {len(x):,} | {ci(x, 'toi_gain')} | {ci(x, 'res')} | "
                   f"{x.props.mean():.0%} / {1 - x.props.mean():.0%} |")
    pr = d[d.bucket == "promoted (>= +2 min)"]
    if len(pr):
        out += ["\nPromoted skaters, with vs without props (DK pts minus projection):",
                f"- with props: {ci(pr[pr.props], 'res')}" if pr.props.sum() >= 10 else "- with props: too few",
                f"- without props: {ci(pr[~pr.props], 'res')}" if (~pr.props).sum() >= 10 else "- without props: too few"]
    # slope: residual on promotion minutes, within night
    x = d.assign(pc=d.promotion - d.groupby("date").promotion.transform("mean"),
                 rc=d.res - d.groupby("date").res.transform("mean"))
    nights = x.date.unique()

    def slope(ix):
        z = x[x.date.isin(nights[ix])]
        return (z.pc * z.rc).sum() / (z.pc ** 2).sum()
    e, lo, hi, _ = cluster_bootstrap(np.arange(len(nights)), slope, n_boot=a.boot, seed=9)
    out.append(f"\nDK points vs projection per +1 min of promotion: {e:+.3f} [{lo:+.3f}, {hi:+.3f}]")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
