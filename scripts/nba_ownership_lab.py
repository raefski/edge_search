#!/usr/bin/env python3
"""Live-faithful NBA GPP ownership harness: the boards we actually built before lock.

    python3 scripts/nba_ownership_lab.py report      # shipped model, train / test tables + slices
    python3 scripts/nba_ownership_lab.py parity      # replayed own vs the own we logged

scripts/nba_ownership_fit.py rebuilds 2025-26 nights (no logged boards then). Since
2026-10-20 every NBA build is logged to contests.db `projection` (proj, salary, DK
position, minutes, promoted, NBA.com lineup state, own_pred) -- so a slate can be
replayed exactly as it stood: the LAST pre-lock log of its draft group, plus that
date's pre-lock buzz (data/buzz_nba.csv), through dfs_nba_theory.add_ownership.
Scored against the GPP exports' %Drafted for the same draft group (CLAUDE.md: by
draft group, never the nearest slate; cash never pooled). Several GPPs on one draft
group are averaged into one field per player (one slate = one realisation).

Split by DATE (the slate is the unit): train = slates before TEST_FROM, test = on or
after. 2026-10-28: 9 slates on 8 dates -- 4 train, 5 test. Below ~5 a number is a
hypothesis (CLAUDE.md).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_nba_theory as T, dfs_opt_nba as O       # noqa: E402
from edge.names import norm                                  # noqa: E402

DB = ROOT / "data" / "contests.db"
TEST_FROM = "2026-10-25"
CHEAP = 5500          # "cheap" for the slice Adam named (2026-10-28): <= $5,500
CHALK = 20.0          # field %Drafted that counts as chalk


def _starters(date: str) -> set:
    """(norm name, team) of NBA.com's starters that day (the day's final file -- used
    to SLICE the error, never as a model input)."""
    from datetime import date as D
    from edge import nba_lineups
    try:
        raw = nba_lineups.fetch(D.fromisoformat(date))
    except Exception:                                          # noqa: BLE001
        return set()
    return {(norm(p["name"]), p["team"]) for g in nba_lineups.parse(raw) for p in g["players"] if p["starter"]}


_LOGS: dict = {}


def _recent(name: str, date: str) -> float | None:
    """dfs_nba_theory.recent_dk from the box scores dated before `date` -- what the
    build's history file held at lock (games before tonight only, no leak)."""
    if not _LOGS:
        from edge.nba import actual_points
        gt = ROOT / "data" / "nba_ground_truth"
        for season, cur in (("2025-26", False), ("2026-27", True)):
            for r in json.load(open(gt / f"player_gamelog_{season}.json")):
                if float(r.get("MIN") or 0) > 0:
                    _LOGS.setdefault(norm(r["PLAYER_NAME"]), []).append(
                        (r["GAME_DATE"][:10], actual_points(r), cur))
        for v in _LOGS.values():
            v.sort()
    g = [x for x in _LOGS.get(norm(name), []) if x[0] < date]
    cur_n = sum(1 for x in g if x[2])
    return T.recent_dk([(1, 0, x[1]) for x in g], cur_n)


def _prelock_starters(date: str, at: str) -> set | None:
    """(norm name, team) NBA.com listed as starting in the newest
    data/nba_lineups_snapshot.json committed to the `data` branch at or before
    the board's logged_at, for that date -- what build_board read (`starter`).
    None when no snapshot of that date existed yet (the term is then 0)."""
    import subprocess
    from datetime import datetime
    from edge import nba_lineups
    git = ["git", "-C", str(ROOT)]
    log = subprocess.run(git + ["log", "--format=%H %cI", "origin/data", "--",
                                "data/nba_lineups_snapshot.json"], capture_output=True, text=True).stdout
    cut = datetime.fromisoformat(at)
    for line in log.splitlines():                  # newest first
        h, t = line.split()
        if datetime.fromisoformat(t) > cut:
            continue
        body = json.loads(subprocess.run(git + ["show", f"{h}:data/nba_lineups_snapshot.json"],
                                         capture_output=True, text=True).stdout or "{}")
        if body.get("date", "").replace("-", "") == date.replace("-", ""):
            return {(norm(p["name"]), p["team"]) for g in nba_lineups.parse(body) for p in g["players"] if p["starter"]}
    return None


def slates(conn) -> list[dict]:
    """[{date, gid, pool, field}] for every NBA GPP draft group with a logged board."""
    out = []
    q = """select draft_group, min(slate_date), group_concat(contest_id) from contest
           where sport='NBA' and type='gpp' and slate_date>='2026-10-01' group by draft_group"""
    for gid, date, cids in conn.execute(q):
        last = conn.execute("select max(logged_at) from projection where sport='NBA' and draft_group=? "
                            "and own_pred is not null", (gid,)).fetchone()[0]
        if not last:
            continue
        pool = []
        for name, team, pos, sal, proj, own, extra in conn.execute(
                "select name,team,pos,salary,proj,own_pred,extra from projection where draft_group=? "
                "and logged_at=?", (gid, last)):
            x = json.loads(extra or "{}")
            elig = O.eligible(pos)
            pool.append({"name": name, "team": team, "dk_pos": pos, "salary": float(sal or 0),
                         "proj": float(proj or 0), "own_logged": own, "elig": elig,
                         "pos": sorted(elig, key=["PG", "SG", "SF", "PF", "C"].index)[0],
                         "minutes": float(x.get("minutes") or 0), "promoted": x.get("promoted") == "1",
                         "lineup": x.get("lineup", "")})
        starters = _starters(date)
        pre = _prelock_starters(date, last) or set()
        for p in pool:
            p["starter_final"] = (norm(p["name"]), p["team"]) in starters   # slicing only
            p["starter"] = (norm(p["name"]), p["team"]) in pre               # model input, pre-lock
            p["recent"] = _recent(p["name"], date)
        field = defaultdict(list)
        ids = [int(c) for c in cids.split(",")]
        for cid in ids:
            for pn, pct in conn.execute("select player_norm,pct_drafted from player_result where contest_id=?", (cid,)):
                field[pn].append(float(pct or 0))
        field = {k: sum(v) / len(ids) for k, v in field.items()}
        out.append({"date": date, "gid": gid, "pool": pool, "field": field, "contests": len(ids)})
    return sorted(out, key=lambda s: (s["date"], s["gid"]))


def attach_buzz(pool, date):
    T_buzz = ROOT / "data" / "buzz_nba.csv"
    from edge.dfs_run_nba import attach_buzz as ab
    ab(pool, date, T_buzz)


def score(sl, own_fn=None) -> list[dict]:
    """Per-player rows {slate, own, field, salary, starter, promoted, ...} for scoring."""
    rows = []
    for s in sl:
        pool = [dict(p) for p in s["pool"]]
        attach_buzz(pool, s["date"])
        (own_fn or T.add_ownership)(pool)
        for p in pool:
            rows.append({"slate": (s["date"], s["gid"]), "own": p["own"], "field": s["field"].get(norm(p["name"]), 0.0),
                         "salary": p["salary"], "starter": p["starter_final"], "promoted": p["promoted"],
                         "proj": p["proj"], "name": p["name"], "logged": p["own_logged"]})
    return rows


SLICES = {
    "all": lambda r: True,
    f"cheap starter (<=${CHEAP})": lambda r: r["starter"] and r["salary"] <= CHEAP,
    f"field chalk (>={CHALK:.0f}%)": lambda r: r["field"] >= CHALK,
    f"cheap + chalk": lambda r: r["salary"] <= CHEAP and r["field"] >= CHALK,
}


def table(rows) -> dict:
    out = {}
    for lab, f in SLICES.items():
        sel = [r for r in rows if f(r)]
        if not sel:
            continue
        e = np.array([r["own"] - r["field"] for r in sel])
        out[lab] = {"n": len(sel), "mae": float(np.abs(e).mean()), "bias": float(e.mean())}
    by = defaultdict(list)
    for r in rows:
        by[r["slate"]].append(r)
    out["slate MAE"] = [round(float(np.mean([abs(r["own"] - r["field"]) for r in v])), 3) for _, v in sorted(by.items())]
    from scipy.stats import spearmanr
    out["rank"] = float(np.mean([spearmanr([r["own"] for r in v], [r["field"] for r in v])[0] for v in by.values()]))
    return out


def report(own_fn=None, label="shipped"):
    conn = sqlite3.connect(DB)
    sl = slates(conn)
    for part, ss in (("train", [s for s in sl if s["date"] < TEST_FROM]), ("test", [s for s in sl if s["date"] >= TEST_FROM])):
        t = table(score(ss, own_fn))
        print(f"\n{label} -- {part}: {len(ss)} slates, {sum(s['contests'] for s in ss)} GPPs, "
              f"dates {sorted({s['date'] for s in ss})}")
        print("| slice | n | MAE | bias |\n|---|---|---|---|")
        for lab in SLICES:
            if lab in t:
                print(f"| {lab} | {t[lab]['n']} | {t[lab]['mae']:.3f} | {t[lab]['bias']:+.2f} |")
        print(f"per-slate MAE {t['slate MAE']}  mean rank corr {t['rank']:+.3f}")


def parity():
    conn = sqlite3.connect(DB)
    for s in slates(conn):
        rows = score([s])
        d = np.array([r["own"] - (r["logged"] or 0) for r in rows])
        print(s["date"], s["gid"], "n", len(rows), "max |replay-logged| %.2f mean %.2f" % (np.abs(d).max(), np.abs(d).mean()))


# ---- ownership on the props-blended projection (props-era rerun #2, 2026-11-09) ----
# SLATE_OWN / CHEAP_STARTER were fitted 10/28 on 4 slates of MODEL-ONLY projections; since
# cycle 27 (11/8) production feeds add_ownership the props blend. Four variants, every slate
# scored with constants (and blend) fitted WITHOUT its date:
#   A blended proj, shipped constants (production now)   B model proj, shipped constants
#   C blended proj, constants refit                       D model proj, constants refit (control:
#     the gain from refitting on 4x the slates alone)
def _own_with(pool, k):
    a, b, c, d, e, g = k
    old = T.CHEAP_STARTER
    T.CHEAP_STARTER = (g, old[1])
    try:
        T._slate_ownership(pool, (a, b, c, d, e))
    finally:
        T.CHEAP_STARTER = old
    return pool


def _mae(pools, k):
    err, n = 0.0, 0
    for pool, field in pools:
        for p in _own_with(pool, k):
            err += abs(p["own"] - field.get(norm(p["name"]), 0.0))
            n += 1
    return err / max(n, 1)


def _fit(args):
    from scipy.optimize import minimize
    pools, k0 = args
    r = minimize(lambda k: _mae(pools, k), k0, method="Powell",
                 options={"xtol": 1e-3, "ftol": 1e-5, "maxfev": 4000})
    return tuple(float(x) for x in r.x)


def props_report(workers: int | None = None):
    import os
    from concurrent.futures import ProcessPoolExecutor
    sys.path.insert(0, str(ROOT / "scripts"))
    import nba_lineup_lab as L
    from edge import dfs_run_nba as R
    conn = sqlite3.connect(DB)
    sl = slates(conn)
    B = L.boards()
    LB = {g: ps for g, ps in B.items() if g in L.fields()}
    rows = L.register_props(LB)["rows"]                   # blend-fitting rows (16 dates)
    M = L.market_all({s["gid"]: B[s["gid"]] for s in sl})
    for s in sl:
        attach_buzz(s["pool"], s["date"])
    dates = sorted({s["date"] for s in sl})
    k_ship = (*T.SLATE_OWN, T.CHEAP_STARTER[0])

    def pools_for(d, blended):
        out = {}
        bl = L.props_fit([r for r in rows if r["date"] != d])
        for s in sl:
            pool = [dict(p) for p in s["pool"]]
            if blended:
                R.blend_props(pool, M.get(s["gid"]) or {}, bl)
            out[s["date"], s["gid"]] = (pool, s["field"])
        return out

    P = {(d, bl): pools_for(d, bl) for d in dates for bl in (False, True)}
    cov = np.mean([p.get("proj_source") == "props" for (_, bl), ps in P.items() if bl
                   for pool, _ in ps.values() for p in pool])
    jobs = [([v for (dd, _), v in P[(d, bl)].items() if dd != d], k_ship) for d in dates for bl in (False, True)]
    print(f"{len(sl)} GPP slates / {len(dates)} dates / {sum(s['contests'] for s in sl)} GPPs; "
          f"{cov:.0%} of board players carry props; {len(jobs)} leave-one-date-out refits on "
          f"{workers or os.cpu_count()} processes", flush=True)
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as ex:
        fits = dict(zip([(d, bl) for d in dates for bl in (False, True)], ex.map(_fit, jobs)))
    res = defaultdict(dict)            # variant -> (date, gid) -> {mae, rank, n}
    from scipy.stats import spearmanr
    for d in dates:
        for lab, bl, k in (("A blended, shipped", True, k_ship), ("B model, shipped", False, k_ship),
                           ("C blended, refit", True, fits[(d, True)]), ("D model, refit", False, fits[(d, False)])):
            for key, (pool, field) in P[(d, bl)].items():
                if key[0] != d:
                    continue
                pool = _own_with([dict(p) for p in pool], k)
                o = np.array([p["own"] for p in pool]); f = np.array([field.get(norm(p["name"]), 0.0) for p in pool])
                chalk = f >= CHALK
                res[lab][key] = {"date": d, "mae": float(np.abs(o - f).mean()), "n": len(pool),
                                 "rank": float(spearmanr(o, f)[0]),
                                 "chalk": float(np.abs(o - f)[chalk].mean()) if chalk.any() else np.nan}

    def boot(cl, x, n=2000):
        ids, inv = np.unique(cl, return_inverse=True)
        s_, c_ = np.bincount(inv, weights=x), np.bincount(inv).astype(float)
        k_ = np.random.default_rng(0).integers(0, len(ids), (n, len(ids)))
        b_ = s_[k_].sum(1) / c_[k_].sum(1)
        return float(x.mean()), float(np.percentile(b_, 5)), float(np.percentile(b_, 95))

    base = res["A blended, shipped"]
    print("\n| variant | split | slates / dates | player MAE (pts of %Drafted) | diff vs A [90%, by date] | "
          "slates better / worse | rank corr | MAE on field chalk >=20% |")
    print("|---|---|---|---|---|---|---|---|")
    for lab, r in res.items():
        for split, keep in (("all", lambda d: True), ("test >= 10/31", lambda d: d >= "2026-10-31")):
            ks = [k for k in r if keep(k[0])]
            cl = np.array([k[0] for k in ks]); x = np.array([r[k]["mae"] - base[k]["mae"] for k in ks])
            m, lo, hi = boot(cl, x)
            print(f"| {lab} | {split} | {len(ks)} / {len(set(cl))} | {np.mean([r[k]['mae'] for k in ks]):.3f} | "
                  f"{m:+.3f} [{lo:+.3f}, {hi:+.3f}] | {int((x < -1e-9).sum())} / {int((x > 1e-9).sum())} | "
                  f"{np.mean([r[k]['rank'] for k in ks]):.3f} | {np.nanmean([r[k]['chalk'] for k in ks]):.2f} |")
    allk = {bl: _fit(([v for v in P[(dates[-1], bl)].values()], k_ship)) for bl in (False, True)}
    print("\nall-slate refits (a value, b salary, c promoted, d buzz, e recent, g cheap starter):")
    print("  shipped        ", tuple(round(x, 3) for x in k_ship))
    print("  blended refit  ", tuple(round(x, 3) for x in allk[True]))
    print("  model refit    ", tuple(round(x, 3) for x in allk[False]))
    for bl in (True, False):
        v = [fits[(d, bl)] for d in dates]
        print(f"  fold range ({'blended' if bl else 'model'}): " +
              ", ".join(f"{min(x[i] for x in v):.2f}..{max(x[i] for x in v):.2f}" for i in range(6)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("report", "parity", "props"))
    a = ap.parse_args()
    if a.cmd == "props":
        props_report()
    else:
        report() if a.cmd == "report" else parity()
