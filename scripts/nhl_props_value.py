#!/usr/bin/env python3
"""What is a DraftKings prop worth to an NHL skater projection?

    python3 scripts/nhl_props_value.py                 # every logged slate
    python3 scripts/nhl_props_value.py 154733 154763

Comparing prop players with no-prop players directly says little: no-prop
skaters are depth players with small projections, so their errors are small
too. The clean test is head to head on the SAME players: rebuild each logged
slate from its pinned pre-lock inputs (scripts/nhl_rebuild.py) twice --

  props     as shipped: shots / points / assists means from the DK lines
  fallback  props switched off: every skater on last season's per-game rates
            scaled by tonight's implied goals (what a no-prop player gets)

-- and grade both against DK points from the box score. Same board, same
simulation seed, same blocks (blocks never had a market). Also reported: the
no-prop players' own errors, against the prop players' at a matched
projection, which is how much worse a no-prop projection is to trust.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_run_nhl as R  # noqa: E402
from scripts import nhl_rebuild as RB  # noqa: E402
from scripts.contest_stats import cluster_bootstrap, spearman  # noqa: E402
from scripts.nhl_ownership_signal import box_key  # noqa: E402
from scripts.nhl_seven_d_compare import box_points  # noqa: E402


def board(gid: int, props: bool):
    real = R.player_props
    if not props:
        R.player_props = lambda *a, **k: ({}, [])
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            lock = RB.lock_time(gid)
            at = RB.utc(lock)
            _, draftables = RB.git_json(f"data/draftables_snapshot/{gid}.json", at)
            _, lines = RB.git_json("data/nhl_lines_snapshot.json", at)
            prof = RB.for_sport(R.SPORT, "dfs")
            scan_id, _ = RB.scan_before(at, prof.name)

            def offline(url):
                raise RuntimeError("pinned")
            RB.dfs._draftables_raw = lambda g: draftables
            RB.nhl._next_data = offline
            RB.nhl._snapshot = lambda: lines
            client = RB.ScrapedOddsClient(RB.OddsStore(), prof.name, scan_id=scan_id,
                                          main_line_only=not prof.full_ladders)
            pool, _, info = R.build_board(gid, {"gid": gid, "start": lock, "label": "props"}, client,
                                          n_sims=2000, seed=0)
    finally:
        R.player_props = real
    return pool, info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gids", nargs="*", type=int)
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    log = pd.read_csv(ROOT / "data" / "dfs_proj_log_nhl.csv")
    rows = []
    for gid in a.gids or sorted(log.gid.unique()):
        try:
            with_p, info = board(int(gid), True)
            without, _ = board(int(gid), False)
        except (SystemExit, Exception) as e:  # noqa: BLE001 -- a slate without pinned inputs is skipped
            print(f"{gid}: skipped ({e})", file=sys.stderr)
            continue
        fb = {p["name"]: p["proj"] for p in without}
        act = box_points(info["date"])
        for p in with_p:
            if p["pos"] == "G":
                continue
            y = act.get(box_key(p["team"], p["name"]))
            if y is None:
                continue
            rows.append({"gid": gid, "date": info["date"], "name": p["name"], "pos": p["pos"],
                         "salary": p["salary"], "props": not ({"shots", "points"} & set(p.get("imputed") or [])),
                         "proj": p["proj"], "fallback": fb.get(p["name"]), "actual": y})
        print(f"{gid} {info['date']}: {len(with_p)} on board", file=sys.stderr)
    d = pd.DataFrame(rows).dropna(subset=["fallback"])
    d = d.drop_duplicates(["date", "name"])          # an early and a main slate share a night's games
    days = d.date.unique()
    out = [f"# NHL props: what a DK prop line is worth ({len(days)} nights, {len(d):,} skater-games)\n"]

    def by_day(x, f):
        s = x.groupby("date").apply(f).dropna()
        arr = s.to_numpy()
        est, lo, hi, _ = cluster_bootstrap(np.arange(len(arr)), lambda i: arr[i].mean(), n_boot=a.boot, seed=7)
        return est, lo, hi, s

    P = d[d.props]
    out += ["## Same players, with and without their props (players who had props)\n",
            "| | skater-games | MAE | bias (proj - actual) | within-night rank corr |", "|---|---|---|---|---|"]
    for col, lab in (("proj", "props (as shipped)"), ("fallback", "last season x implied goals")):
        rho = P.groupby("date").apply(lambda x: spearman(x[col], x.actual)).mean()
        out.append(f"| {lab} | {len(P):,} | {(P[col] - P.actual).abs().mean():.3f} | "
                   f"{(P[col] - P.actual).mean():+.3f} | {rho:+.3f} |")
    e, lo, hi, s = by_day(P, lambda x: (x.proj - x.actual).abs().mean() - (x.fallback - x.actual).abs().mean())
    out.append(f"\nMAE, props minus fallback, per night: **{e:+.3f} [{lo:+.3f}, {hi:+.3f}]** "
               f"(props better on {(s < 0).sum()}/{len(s)} nights)")
    e, lo, hi, s = by_day(P, lambda x: spearman(x.proj, x.actual) - spearman(x.fallback, x.actual))
    out.append(f"Rank corr, props minus fallback: **{e:+.3f} [{lo:+.3f}, {hi:+.3f}]** ({(s > 0).sum()}/{len(s)} nights)")

    N = d[~d.props]
    out += ["\n## Players with no props: how far to trust them\n",
            "Compared at a matched projection (bins of our projection), since no-prop "
            "players project low.\n",
            "| projection bin | prop players: n / MAE / bias | no-prop players: n / MAE / bias |", "|---|---|---|"]
    bins = [0, 5, 7, 9, 12, 40]
    d["bin"] = pd.cut(d.proj, bins)
    for b, x in d.groupby("bin", observed=True):
        pp, nn = x[x.props], x[~x.props]
        cell = lambda z: (f"{len(z)} / {(z.proj - z.actual).abs().mean():.2f} / {(z.proj - z.actual).mean():+.2f}"  # noqa: E731
                          if len(z) else "-")
        out.append(f"| {b} | {cell(pp)} | {cell(nn)} |")
    out.append(f"\nShare of board skaters with props: {d.props.mean():.0%}; no-prop players' mean "
               f"projection {N.proj.mean():.2f} vs prop players' {P.proj.mean():.2f}.")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
