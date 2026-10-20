#!/usr/bin/env python3
"""Before/after check for the 11 F / 7 D adjustment (edge/dfs_run_nhl.SEVEN_D_SCALE).

    python3 scripts/nhl_seven_d_compare.py              # every logged slate with a 7-D team at lock
    python3 scripts/nhl_seven_d_compare.py 154733 154763

Each slate is rebuilt twice from its pinned pre-lock inputs (scripts/nhl_rebuild.py:
draftables, odds scan and DailyFaceoff snapshot as they stood at lock, seed 0),
once with seven_d_teams() switched off (the old board) and once as shipped.
Graded on what happened:

  players  the defencemen on 7-D teams: projection vs DK points from the box
           score (edge/nhl.skater_points) -- error and bias, old vs new
  lineups  the cash and GPP lineup each build picks, scored with DK's own
           FPTS from the contest export on that draft group (contests.db),
           and placed in that contest's real field (percentile, cleared the
           cash line). Slates without an export get player-level grades only.

The 7-D flag comes from the snapshot the build read (no hindsight): a d4 row,
or exactly 11 forwards listed. Nothing is logged.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_run_nhl as R  # noqa: E402
from edge.names import norm  # noqa: E402
from edge.nhl import skater_points  # noqa: E402
from scripts import nhl_rebuild as RB  # noqa: E402
from scripts.nhl_ownership_signal import box_key  # noqa: E402

BOX = ROOT / "data" / "nhl_cache" / "box"


def md(df: pd.DataFrame) -> str:
    """Markdown table without the optional tabulate dependency."""
    cell = lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else (  # noqa: E731
        f"{v:.3f}" if isinstance(v, float) else str(v))
    rows = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    rows += ["| " + " | ".join(cell(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(rows)


def pinned_seven_d(gid: int) -> set:
    _, snap = RB.git_json("data/nhl_lines_snapshot.json", RB.utc(RB.lock_time(gid)))
    lines = {(t, norm(n)): row for t, rows in (snap.get("lines") or {}).items() for n, row in rows.items()}
    return R.seven_d_teams(lines)


def box_points(date: str) -> dict:
    out = {}
    for f in BOX.glob("2026*.json"):
        b = json.loads(f.read_text())
        if b.get("date") != date:
            continue
        for s in b["skaters"]:
            out[box_key(s["team"], s["name"])] = float(skater_points(s["goals"], s["assists"], s["shots"], s["blocks"]))
    return out


def contest_for(conn, gid: int):
    """The largest final contest on this draft group: (id, type, field points,
    {player_norm: fpts}, cash line points)."""
    row = conn.execute("SELECT contest_id, type FROM contest WHERE draft_group=? AND final=1 "
                       "ORDER BY live_entries DESC LIMIT 1", (gid,)).fetchone()
    if not row:
        return None
    cid, typ = row
    pts = np.array([r[0] for r in conn.execute("SELECT points FROM entry_feature WHERE contest_id=? AND live=1", (cid,))])
    fp = dict(conn.execute("SELECT player_norm, fpts FROM player_result WHERE contest_id=?", (cid,)))
    paid = [r[0] for r in conn.execute("SELECT points FROM entry_feature WHERE contest_id=? AND live=1 AND cash=1", (cid,))]
    return cid, typ, pts, fp, (min(paid) if paid else None)


def build(gid: int, teams: set | None, sims: int, iters: int):
    """Pinned rebuild (scripts/nhl_rebuild.py inputs) with the 7-D scale applied
    to `teams` -- None = as shipped (the snapshot's own 7-D teams), set() = the
    old board, any other set = a placebo."""
    lock = RB.lock_time(gid)
    at = RB.utc(lock)
    _, draftables = RB.git_json(f"data/draftables_snapshot/{gid}.json", at)
    _, lines = RB.git_json("data/nhl_lines_snapshot.json", at)
    prof = RB.for_sport(R.SPORT, "dfs")
    scan_id, _ = RB.scan_before(at, prof.name)

    def offline(url):
        raise RuntimeError("pinned to the snapshot")
    RB.dfs._draftables_raw = lambda g: draftables
    RB.nhl._next_data = offline
    RB.nhl._snapshot = lambda: lines
    client = RB.ScrapedOddsClient(RB.OddsStore(), prof.name, scan_id=scan_id, main_line_only=not prof.full_ladders)
    real = R.seven_d_teams
    if teams is not None:
        R.seven_d_teams = lambda _lines: set(teams)
    try:
        pool, sim, info = R.build_board(gid, {"gid": gid, "start": lock, "label": "compare"}, client,
                                        n_sims=sims, seed=0)
    finally:
        R.seven_d_teams = real
    lu = {m: [p for _, p in (RB.dfs_opt_nhl.optimize(pool, sim, mode=m, iters=iters, seed=0) or {}).get("slots", [])]
          for m in ("cash", "gpp")}
    return pool, info, lu


def grade(lu: list, c) -> dict:
    if not c:
        return {}
    pts = sum(c[3].get(norm(p["name"]), 0.0) for p in lu)
    return {"pts": pts, "pct": float((c[2] < pts).mean()), "cash": None if c[4] is None else pts >= c[4]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gids", nargs="*", type=int)
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--iters", type=int, default=150)
    ap.add_argument("--placebo", type=int, default=0,
                    help="also rebuild N times with the scale on random NON-7-D teams (same count): "
                         "how much a same-sized nudge moves the lineups by chance")
    a = ap.parse_args()
    log = pd.read_csv(ROOT / "data" / "dfs_proj_log_nhl.csv")
    gids = a.gids or sorted(log.gid.unique())
    conn = sqlite3.connect(ROOT / "data" / "contests.db")
    prow, lrow, placebo = [], [], []
    for gid in gids:
        try:
            sd = pinned_seven_d(int(gid))
        except SystemExit as e:
            print(f"{gid}: {e}", file=sys.stderr)
            continue
        old_pool, info, old_l = build(int(gid), set(), a.sims, a.iters)
        sd &= {p["team"] for p in old_pool}
        if not sd:
            continue
        new_pool, info, new_l = build(int(gid), None, a.sims, a.iters)
        date = info["date"]
        act = box_points(date)
        old_proj = {p["name"]: p["proj"] for p in old_pool}
        for p in new_pool:
            if p["pos"] == "D" and p["team"] in sd:
                y = act.get(box_key(p["team"], p["name"]))
                prow.append({"gid": gid, "date": date, "team": p["team"], "name": p["name"],
                             "old": old_proj.get(p["name"]), "new": p["proj"], "actual": y})
        c = contest_for(conn, int(gid))
        for mode in ("cash", "gpp"):
            o, n = old_l[mode], new_l[mode]
            changed = sorted({p["name"] for p in n} ^ {p["name"] for p in o})
            rec = {"gid": gid, "date": date, "seven_d": " ".join(sorted(sd)), "mode": mode,
                   "swaps": len(changed) // 2, "changed": ", ".join(changed)}
            for tag, lu in (("old", o), ("new", n)):
                rec[f"{tag}_proj"] = sum(p["proj"] for p in lu)
                rec.update({f"{tag}_{k}": v for k, v in grade(lu, c).items()})
            if c:
                rec["contest"] = f"{c[0]} ({c[1]})"
            lrow.append(rec)
        others = sorted({p["team"] for p in old_pool} - sd)
        rng = np.random.default_rng(int(gid))
        for k in range(a.placebo):
            fake = set(rng.choice(others, size=min(len(sd), len(others)), replace=False))
            _, _, pl = build(int(gid), fake, a.sims, a.iters)
            for mode in ("cash", "gpp"):
                o, n = old_l[mode], pl[mode]
                changed = {p["name"] for p in n} ^ {p["name"] for p in o}
                rec = {"gid": gid, "date": date, "mode": mode, "draw": k, "teams": " ".join(sorted(fake)),
                       "swaps": len(changed) // 2}
                go, gn = grade(o, c), grade(n, c)
                if go:
                    rec.update({"d_pts": gn["pts"] - go["pts"], "d_pct": gn["pct"] - go["pct"]})
                placebo.append(rec)
        print(f"{gid} {date}: 7-D {sorted(sd)}; cash swaps {lrow[-2]['swaps']}, gpp swaps {lrow[-1]['swaps']}",
              file=sys.stderr)
    P, L = pd.DataFrame(prow), pd.DataFrame(lrow)
    out = [f"# 7-D adjustment, before vs after ({P.gid.nunique() if len(P) else 0} slates with a 7-D team at lock)\n"]
    g = P.dropna(subset=["actual"])
    if len(g):
        per = g.assign(eo=(g.old - g.actual).abs(), en=(g.new - g.actual).abs(),
                       bo=g.old - g.actual, bn=g.new - g.actual).groupby("date")[["eo", "en", "bo", "bn"]].mean()
        out += ["## Defencemen on 7-D teams: projection vs DK points\n",
                "| | D-games | MAE | bias (proj - actual) | nights better |", "|---|---|---|---|---|",
                f"| old board | {len(g)} | {(g.old - g.actual).abs().mean():.3f} | {(g.old - g.actual).mean():+.3f} | |",
                f"| with 7-D scale | {len(g)} | {(g.new - g.actual).abs().mean():.3f} | {(g.new - g.actual).mean():+.3f} | "
                f"{int((per.en < per.eo).sum())}/{len(per)} |",
                f"\nunmatched to a box score: {P.actual.isna().sum()} of {len(P)}\n"]
    if len(L):
        cols = [c for c in ["date", "gid", "mode", "seven_d", "swaps", "old_proj", "new_proj", "old_pts", "new_pts",
                            "old_pct", "new_pct", "old_cash", "new_cash", "contest", "changed"] if c in L]
        out += ["## Lineups each build picks\n", md(L[cols])]
        h = L.dropna(subset=["old_pts"]) if "old_pts" in L else L.iloc[:0]
        ch = h[h.swaps > 0]
        if len(ch):
            out.append(f"\nChanged lineups with a contest export: {len(ch)} ({ch.date.nunique()} nights). "
                       f"Real DK points new minus old: {(ch.new_pts - ch.old_pts).mean():+.2f} per lineup "
                       f"({int((ch.new_pts > ch.old_pts).sum())} better / {int((ch.new_pts < ch.old_pts).sum())} worse); "
                       f"field percentile {(ch.new_pct - ch.old_pct).mean():+.3f}.")
    if placebo:
        Pl = pd.DataFrame(placebo)
        real = L[L.swaps > 0].dropna(subset=["old_pts"]) if "old_pts" in L else L.iloc[:0]
        pc = Pl.dropna(subset=["d_pts"]) if "d_pts" in Pl else Pl.iloc[:0]
        pch = pc[pc.swaps > 0]
        out += ["\n## Placebo: the same scale on random teams that did NOT dress 7 D\n",
                "| | lineups | mean swaps | lineups changed | real pts new - old (changed) | sd | better / worse |",
                "|---|---|---|---|---|---|---|",
                f"| 7-D teams (the fix) | {len(L)} | {L.swaps.mean():.1f} | {(L.swaps > 0).sum()} | "
                f"{(real.new_pts - real.old_pts).mean():+.2f} | {(real.new_pts - real.old_pts).std():.1f} | "
                f"{int((real.new_pts > real.old_pts).sum())} / {int((real.new_pts < real.old_pts).sum())} |",
                f"| placebo teams | {len(Pl)} | {Pl.swaps.mean():.1f} | {(Pl.swaps > 0).sum()} | "
                f"{pch.d_pts.mean():+.2f} | {pch.d_pts.std():.1f} | "
                f"{int((pch.d_pts > 0).sum())} / {int((pch.d_pts < 0).sum())} |"]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
