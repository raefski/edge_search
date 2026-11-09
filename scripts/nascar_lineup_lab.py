#!/usr/bin/env python3
"""NASCAR lineups, held out: the PRODUCTION optimizer replayed on every logged Cup board
(rebuilt as it stood at the green flag), scored with DK's own points against the real
contest fields.

    python3 scripts/nascar_lineup_lab.py parity            # replay == logged / Adam's lineup?
    python3 scripts/nascar_lineup_lab.py report            # cash rate / field pctile / $ per contest
    python3 scripts/nascar_lineup_lab.py lodo              # CASH_PCT / GPP_PCT chosen leave-one-slate-out
    python3 scripts/nascar_lineup_lab.py gate OUT          # rows for harness/critique_gate.py

Why it exists (critique cycle 5, 2026-11-09, target picked by dfs_target_scout). NASCAR
cash lost -50% [-100%, +32%] and GPP -54% [-100%, +18%] (4 entries / 4 slates each,
data/dfs_scorecard.json) while cycles 1-4 scored PLAYER finish / DK points
(scripts/nascar_lab.py, 66 held-out races). Player error does not see the lineup we
enter, and neither percentile objective (dfs_nascar_theory.CASH_PCT 25 / GPP_PCT 90,
argued from the simulated distribution) had ever been scored against a real field.

What is replayed, per Cup draft group in data/dfs_proj_log_nascar.csv (Trucks / Xfinity
boards are skipped: rebuild_board looks the race up on the Cup schedule):
  - edge.dfs_run_nascar.rebuild_board(logged rows): the logged salaries, the REAL
    qualifying grid and form from races before this one only -- the board the phone app
    builds after qualifying (NASCAR has no late swap, so that is the build that is
    entered). Simulated with the CURRENT model, so a model change on a branch shows up
    here: this is what the gate compares.
  - edge.dfs_opt_nascar.optimize, cash and GPP, at the page's defaults: 1,500 simulated
    races, search effort 250, seed 0, own_weight 0 (pages/6_NASCAR_DFS.py).
Truth: DK's own FPTS (contests.db player_result, max over the draft group's exports: the
number the field was scored with) where an export exists; otherwise edge.nascar's
recomputed DK points from NASCAR's results feed (joined by driver_id; validated against
DK FPPG, NASCAR_STATUS.md section 2). `report` prints how the two agree on the exported
races.
Field: every exported contest on the draft group. Per contest: the lineup's percentile
among entries, whether it reaches the cash line (points of the last paid place), and the
dollars it would have won at the rank it would have taken (payout table; entry fee
subtracted). Cash lineup vs cash contests, GPP vs GPP, never pooled. Cluster: the race.
Adam's real entry is printed beside the replay so hand edits are visible.

THIN: 3 Cup races have both a logged board and exports (9/27, 10/25, 11/1). 10/4
(dg 154134) has exports but no logged board -- no salaries on this machine -- and is
excluded, not guessed. Everything here is "not enough data" (< 5 slates) until the 2027
season adds races; boards without exports (10/11, 10/18, 11/8) are still scored on
lineup DK points (no field).
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from edge import dfs_nascar_theory as theory          # noqa: E402
from edge import dfs_opt_nascar as opt                # noqa: E402
from edge import dfs_run_nascar as R                  # noqa: E402
from edge import nascar                               # noqa: E402
from edge.names import norm                           # noqa: E402

LOG = ROOT / "data" / "dfs_proj_log_nascar.csv"
DB = ROOT / "data" / "contests.db"
#: the phone page's defaults (pages/6_NASCAR_DFS.py select_sliders), 2026-11-09
N_SIMS = 1500
ITERS = 250
#: lodo grids (production: CASH_PCT 25, GPP_PCT 90)
CASH_GRID = (10.0, 25.0, 40.0, 50.0)
GPP_GRID = (75.0, 90.0, 95.0)
#: a Cup board is a full field; single-row log entries are partial builds
MIN_DRIVERS = 30


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def logged() -> dict:
    """{gid: [log rows]} for every slate in the projection log with a full field."""
    by = defaultdict(list)
    with LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("gid", "").isdigit():
                by[int(r["gid"])].append(r)
    return {g: rs for g, rs in by.items() if len(rs) >= MIN_DRIVERS}


# ---- boards: rebuild_board is network + 1,500 sims; cache on disk, keyed by the logged
# rows and the source of every module the rebuild runs, so a model edit misses the cache
CACHE = ROOT / "data" / "harness" / "nascar_lineup_lab"
SOURCES = ("edge/nascar_sim.py", "edge/dfs_run_nascar.py", "edge/dfs_nascar_theory.py",
           "edge/nascar.py", "edge/dfs_opt_nascar.py")


def _key(*parts) -> str:
    h = hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode())
    for f in SOURCES:
        h.update((ROOT / f).read_bytes())
    return h.hexdigest()[:20]


def board(gid: int, rows: list[dict]):
    """(pool, sim, info) as rebuilt at the green flag; (None, None, info) if not Cup."""
    CACHE.mkdir(parents=True, exist_ok=True)
    k = _key(gid, rows, N_SIMS)
    fj, fn = CACHE / f"{gid}-{k}.json", CACHE / f"{gid}-{k}.npy"
    if fj.exists():
        d = json.loads(fj.read_text())
        return d["pool"], (np.load(fn) if fn.exists() else None), d["info"]
    with contextlib.redirect_stdout(io.StringIO()):
        pool, sim, info = R.rebuild_board(rows, n_sims=N_SIMS, seed=0)
    pool = [{k2: v for k2, v in p.items() if isinstance(v, (int, float, str, bool, type(None)))}
            for p in (pool or [])]
    fj.write_text(json.dumps({"pool": pool, "info": info}, default=str))
    if sim is not None:
        np.save(fn, sim)
    return pool, sim, info


def boards() -> dict:
    """{gid: (date, pool, sim, info)} for the Cup boards that rebuilt."""
    out = {}
    for gid, rows in sorted(logged().items(), key=lambda kv: kv[1][0]["date"]):
        pool, sim, info = board(gid, rows)
        if sim is not None and pool:
            out[gid] = (rows[0]["date"], pool, sim, info)
    return out


def build(pool, sim, mode: str, cash_pct: float = None, gpp_pct: float = None) -> list[str]:
    """Production optimize() at the page defaults, with the objective percentiles
    optionally swapped (restored after). Cached: one climb is ~10 s at 250 iters."""
    k = _key(pool, mode, cash_pct, gpp_pct, ITERS, float(sim.sum()), sim.shape)
    f = CACHE / f"lu-{mode}-{k}.json"
    if f.exists():
        return json.loads(f.read_text())
    lu = _build(pool, sim, mode, cash_pct, gpp_pct)
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(lu))
    return lu


def _build(pool, sim, mode, cash_pct, gpp_pct) -> list[str]:
    old = theory.CASH_PCT, theory.GPP_PCT
    try:
        if cash_pct is not None:
            theory.CASH_PCT = cash_pct
        if gpp_pct is not None:
            theory.GPP_PCT = gpp_pct
        res = opt.optimize(pool, sim, mode=mode, iters=ITERS, seed=0, own_weight=0.0)
    finally:
        theory.CASH_PCT, theory.GPP_PCT = old
    return [d["name"] for d in (res or {}).get("lineup") or []]


# ---- truth and fields ----
def dk_points() -> dict:
    """{gid: {norm name: DK FPTS}}, max over every export on the draft group."""
    c = sqlite3.connect(DB)
    out = defaultdict(dict)
    for dg, pn, f in c.execute(
            "select k.draft_group, pr.player_norm, max(pr.fpts) from player_result pr "
            "join contest k using(contest_id) where k.sport='NASCAR' and pr.fpts is not null "
            "group by k.draft_group, pr.player_norm"):
        out[int(dg)][pn] = float(f)
    return dict(out)


def feed_points(pool: list[dict], info: dict, date: str) -> dict:
    """{norm name: DK points} recomputed from NASCAR's results feed (driver_id join)."""
    season = int(date[:4])
    race = next((r for r in nascar.schedule(season)
                 if (r.get("race_date") or "").startswith(date)
                 and (r.get("race_name") or "").strip() == info.get("race")), None)
    if race is None:
        return {}
    pts = {r["driver_id"]: r["dk"] for r in nascar.race_rows(race["race_id"], season)
           if r.get("dk") is not None}
    return {norm(p["name"]): pts[p["driver_id"]] for p in pool if p.get("driver_id") in pts}


def fields() -> dict:
    """{gid: [contest dict]}: points sorted desc, cash line, payouts, fee, Adam's entry."""
    c = sqlite3.connect(DB)
    out = defaultdict(list)
    for cid, dg, typ, paid, fee, date in c.execute(
            "select contest_id, draft_group, type, places_paid, entry_fee, slate_date from contest "
            "where sport='NASCAR' and places_paid > 0 and draft_group is not null"):
        pts = np.array(sorted((p for (p,) in c.execute(
            "select points from entry where contest_id=? and points is not null and n_players > 0",
            (cid,))), reverse=True))
        if not len(pts):
            continue
        pay = c.execute("select rank_lo, rank_hi, amount from payout where contest_id=?", (cid,)).fetchall()
        adam = c.execute("select points, lineup_raw from entry where contest_id=? and is_adam=1 "
                         "and points is not null order by points desc", (cid,)).fetchall()
        out[int(dg)].append({"contest": cid, "type": typ, "pts": pts, "fee": float(fee or 0),
                             "line": float(pts[min(paid, len(pts)) - 1]), "payout": pay,
                             "adam": adam, "date": date})
    return dict(out)


def won(contest: dict, points: float) -> float:
    """Dollars at the rank a lineup scoring `points` takes (ties to the better place)."""
    rank = int((contest["pts"] > points).sum()) + 1
    return float(sum(a for lo, hi, a in contest["payout"] if lo <= rank <= hi))


def parse_lineup(raw: str) -> list[str]:
    """'D Denny Hamlin D Kyle Larson ...' -> names."""
    return [s.strip() for s in re.split(r"(?:^|\s)D\s", " " + (raw or "")) if s.strip()]


UNSCORED: dict = defaultdict(int)


def score(truth: dict, lineup: list[str]) -> float | None:
    if len(lineup) != 6 or not truth:
        return None
    tot = 0.0
    for n in lineup:
        v = truth.get(norm(n))
        if v is None:
            UNSCORED["lineup drivers with no known score (scored 0)"] += 1
        tot += v or 0.0
    return tot


def truths(B: dict, T: dict) -> dict:
    """{gid: {norm: pts}}: DK's FPTS where exported, else the results feed."""
    out = {}
    for gid, (date, pool, sim, info) in B.items():
        out[gid] = T.get(gid) or feed_points(pool, info, date)
    return out


def rows(cash_pct=None, gpp_pct=None, modes=("cash", "gpp"), B=None, F=None, TT=None) -> list[dict]:
    """One row per (lineup mode, contest of that type) on a board with an export."""
    B = B if B is not None else boards()
    F = F if F is not None else fields()
    TT = TT if TT is not None else truths(B, dk_points())
    out = []
    for gid, (date, pool, sim, info) in B.items():
        if gid not in F:
            continue
        for mode in modes:
            lu = build(pool, sim, mode, cash_pct, gpp_pct)
            pts = score(TT[gid], lu)
            if pts is None:
                continue
            for k in F[gid]:
                if k["type"] != mode:
                    continue
                a = k["adam"][0][0] if k["adam"] else None
                out.append({"gid": gid, "contest": k["contest"], "date": date, "mode": mode,
                            "track": info.get("track_type"), "points": pts,
                            "pctile": float((k["pts"] < pts).mean()),
                            "cashed": bool(pts >= k["line"]), "line": k["line"], "fee": k["fee"],
                            "net": won(k, pts) - k["fee"], "field_n": len(k["pts"]), "lineup": lu,
                            "adam": a, "adam_lineup": parse_lineup(k["adam"][0][1]) if a is not None else None,
                            "adam_pctile": float((k["pts"] < a).mean()) if a is not None else None,
                            "adam_net": (won(k, a) - k["fee"]) if a is not None else None})
    return out


def _boot(cl, d, n=2000, seed=0):
    """Mean with a 90% interval, resampling clusters (races)."""
    ids, inv = np.unique(cl, return_inverse=True)
    s = np.bincount(inv, weights=d)
    c = np.bincount(inv).astype(float)
    k = np.random.default_rng(seed).integers(0, len(ids), (n, len(ids)))
    b = s[k].sum(1) / c[k].sum(1)
    return float(d.mean()), float(np.percentile(b, 5)), float(np.percentile(b, 95))


HEAD = ("| lineup | contests | races | cashed | mean field pctile | mean points | ROI ($ at the "
        "rank taken) | pctile diff vs shipped [90%, by race] |\n|---|---|---|---|---|---|---|---|")


def summary(rs: list[dict], label: str, base: dict | None = None) -> str:
    if not rs:
        return f"| {label} | 0 | | | | | | |"
    cl = np.array([r["date"] for r in rs])
    fee = sum(r["fee"] for r in rs)
    roi = sum(r["net"] for r in rs) / fee if fee else float("nan")
    diff = ""
    if base:
        d = np.array([r["pctile"] - base[r["contest"]]["pctile"] for r in rs])
        m, lo, hi = _boot(cl, d)
        diff = f"{m:+.3f} [{lo:+.3f}, {hi:+.3f}]"
    return (f"| {label} | {len(rs)} | {len(set(cl))} | {np.mean([r['cashed'] for r in rs]):.0%} | "
            f"{np.mean([r['pctile'] for r in rs]):.3f} | {np.mean([r['points'] for r in rs]):.1f} | "
            f"{roi:+.0%} | {diff} |")


def logged_lineups() -> dict:
    """{(gid, mode): set of names} from data/dfs_lineups_nascar_<date>.csv."""
    out = defaultdict(set)
    for f in sorted(ROOT.glob("data/dfs_lineups_nascar_*.csv")):
        for r in csv.DictReader(f.open(newline="")):
            if (r.get("gid") or "").isdigit():
                out[(int(r["gid"]), r["mode"])].add(r["driver"])
    return dict(out)


def parity() -> int:
    """How close the replay is to what was logged on this machine and what Adam entered.
    Neither is a strict parity target: the log holds the LAST local build (often before
    qualifying, or on an older model), and Adam builds on the phone after qualifying and
    may hand-edit; the replay runs today's model on the real grid."""
    B, F, L = boards(), fields(), logged_lineups()
    print("| date | dg | track | mode | replay | overlap w/ last local build | overlap w/ Adam's entry |"
          "\n|---|---|---|---|---|---|---|")
    for gid, (date, pool, sim, info) in B.items():
        for mode in ("cash", "gpp"):
            lu = build(pool, sim, mode)
            lg = L.get((gid, mode))
            adam = next((parse_lineup(k["adam"][0][1]) for k in F.get(gid, [])
                         if k["type"] == mode and k["adam"]), None)
            ov = lambda o: "" if not o else f"{len(set(lu) & set(o))}/6"  # noqa: E731
            print(f"| {date} | {gid} | {info.get('track_type')} | {mode} | {', '.join(lu)} | "
                  f"{ov(lg)} | {ov(adam)} |")
    return 0


def report() -> int:
    B, F = boards(), fields()
    T = dk_points()
    TT = truths(B, T)
    base_rows = rows(B=B, F=F, TT=TT)
    no_board = sorted({f"{k['date']} dg {g}" for g, ks in F.items() if g not in B for k in ks})
    print("# NASCAR lineups replayed (production optimizer, rebuilt post-qualifying board), "
          "scored vs the real field\n")
    print(f"Cup boards rebuilt: {len(B)}; with exports: {sum(g in F for g in B)}. "
          f"Exports with no logged board (excluded): {', '.join(no_board) or 'none'}")
    # truth agreement: DK FPTS vs results-feed recompute, on exported races
    diffs = []
    for gid, (date, pool, sim, info) in B.items():
        if gid in T:
            fp = feed_points(pool, info, date)
            diffs += [abs(T[gid][n] - v) for n, v in fp.items() if n in T[gid]]
    if diffs:
        print(f"truth check, DK FPTS vs results-feed recompute: {len(diffs)} drivers, "
              f"max |diff| {max(diffs):.2f}, mean {np.mean(diffs):.3f}")
    print("unscored: " + (", ".join(f"{k} {v}" for k, v in UNSCORED.items()) or "none"))
    print("NOT ENOUGH DATA: fewer than 5 races with a field; read every number below as a "
          "description, not a verdict.\n")
    for mode in ("cash", "gpp"):
        print(f"## {mode.upper()}\n\n{HEAD}")
        s = [r for r in base_rows if r["mode"] == mode]
        base = {r["contest"]: r for r in s}
        print(summary(s, f"shipped ({mode} pct {theory.CASH_PCT if mode == 'cash' else theory.GPP_PCT:g})"))
        a = [dict(r, points=r["adam"], pctile=r["adam_pctile"], net=r["adam_net"],
                  cashed=r["adam"] >= r["line"]) for r in s if r["adam"] is not None]
        print(summary(a, "Adam's real entry", base))
        print()
    print("## per contest (shipped)\n\n| date | dg | track | mode | contest | field | line | replay pts | "
          "pctile | cashed | $ net | Adam pts | Adam pctile |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in base_rows:
        ap = "" if r["adam"] is None else f"{r['adam']:.1f}"
        app = "" if r["adam_pctile"] is None else f"{r['adam_pctile']:.2f}"
        print(f"| {r['date']} | {r['gid']} | {r['track']} | {r['mode']} | {r['contest']} | {r['field_n']} | "
              f"{r['line']:.1f} | {r['points']:.1f} | {r['pctile']:.2f} | {'Y' if r['cashed'] else '-'} | "
              f"{r['net']:+.2f} | {ap} | {app} |")
    # boards with no field: lineup points vs the board's best possible 6 under the cap
    print("\n## every Cup board, lineup DK points (no field needed)\n\n| date | dg | track | "
          "cash pts | gpp pts | optimal lineup pts (hindsight, under cap) |\n|---|---|---|---|---|---|")
    for gid, (date, pool, sim, info) in B.items():
        t = TT[gid]
        c, g = (score(t, build(pool, sim, m)) for m in ("cash", "gpp"))
        best = _hindsight(pool, t)
        print(f"| {date} | {gid} | {info.get('track_type')} | {c if c is None else round(c, 1)} | "
              f"{g if g is None else round(g, 1)} | {best:.1f} |")
    return 0


def _hindsight(pool: list[dict], truth: dict) -> float:
    """Best 6 actual scores under the $50k cap: the climb on a 1-row 'sim' of the truth."""
    act = np.array([[truth.get(norm(p["name"]), 0.0) for p in pool]])
    # the fill and the candidate list rank on "floor": give them the truth too
    pool = [dict(p, floor=float(a), ceil=float(a)) for p, a in zip(pool, act[0])]
    res = opt.optimize(pool, act, mode="cash", iters=200, seed=0)
    return float(sum(truth.get(norm(d["name"]), 0.0) for d in (res or {}).get("lineup") or []))


def lodo() -> int:
    """For each race with a field: choose CASH_PCT (GPP_PCT) from its grid on the OTHER
    races' contests (mean field percentile, ties to production), score it on this race."""
    B, F = boards(), fields()
    TT = truths(B, dk_points())
    for mode, grid, prod in (("cash", CASH_GRID, theory.CASH_PCT), ("gpp", GPP_GRID, theory.GPP_PCT)):
        kw = (lambda q: {"cash_pct": q}) if mode == "cash" else (lambda q: {"gpp_pct": q})
        by_q = {q: rows(modes=(mode,), B=B, F=F, TT=TT, **kw(q)) for q in grid}
        dates = sorted({r["date"] for r in by_q[prod]})
        print(f"# {mode}: objective percentile chosen leave-one-race-out\n")
        print("| held-out race | pct chosen on the rest | contests | shipped pctile | chosen pctile |"
              "\n|---|---|---|---|---|")
        held, shipped = [], []
        for d in dates:
            def fit(q):
                rs = [r for r in by_q[q] if r["date"] != d]
                return (np.mean([r["pctile"] for r in rs]) if rs else 0.0, q == prod)
            qb = max(grid, key=fit)
            h = [r for r in by_q[qb] if r["date"] == d]
            s = [r for r in by_q[prod] if r["date"] == d]
            held += h
            shipped += s
            print(f"| {d} | {qb:g} | {len(h)} | {np.mean([r['pctile'] for r in s]):.3f} | "
                  f"{np.mean([r['pctile'] for r in h]):.3f} |")
        base = {r["contest"]: r for r in shipped}
        print(f"\n{HEAD}")
        print(summary(shipped, "shipped"))
        print(summary(held, "chosen leave-one-race-out", base))
        print("\nIn-sample by percentile (every race; NOT held out, for reading only):\n")
        print(HEAD)
        for q in grid:
            print(summary(by_q[q], f"{mode} pct {q:g}", base))
        print()
    return 0


def gate(path: str) -> int:
    """critique_gate rows: one per (mode, contest); pred = 1 - field percentile (a loss:
    0 = beat everyone), actual = 0, so lower = better (loss "abs"). State = mode, cluster =
    race date, split primary (no constant here is fitted on these rows)."""
    n = 0
    with open(path, "w") as fh:
        for r in rows():
            fh.write(json.dumps({"key": f"{r['mode']}|{r['contest']}", "cluster": r["date"],
                                 "split": "primary", "state": r["mode"],
                                 "pred": 1.0 - r["pctile"], "actual": 0.0}) + "\n")
            n += 1
    print(f"{n} gate rows -> {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("parity")
    sub.add_parser("report")
    sub.add_parser("lodo")
    g = sub.add_parser("gate")
    g.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "parity":
        return parity()
    if a.cmd == "lodo":
        return lodo()
    if a.cmd == "gate":
        return gate(a.out)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
