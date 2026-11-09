#!/usr/bin/env python3
"""NBA lineups, held out: the PRODUCTION simulate + optimize replayed on every logged
pre-lock board, scored with box-score DK points against the real contest fields.

    python3 scripts/nba_lineup_lab.py parity                 # replayed lineups == the logged ones?
    python3 scripts/nba_lineup_lab.py report [--variant V]   # field percentile / cash line, by split
    python3 scripts/nba_lineup_lab.py gate OUT               # rows for harness/critique_gate.py

Why it exists (critique cycle "nba lineups", 2026-11-08). Adam's NBA entries lost -57%
(model era, 58 entries / 16 nights) while every player-level metric looked fine: the
lineups our optimizer builds sat at the field's 96th-99th percentile by our projection and
scored ~35 points under it (NBA_STATUS.md, "Why NBA loses"). Player MAE does not see
that. This harness scores what we actually enter.

What is replayed, per NBA Classic draft group in data/dfs_proj_log_nba.csv (the last
pre-lock build, edge/dfs_forward_log.py):
  - the pool exactly as logged (proj, salary, dk_pos -> eligibility, team / opp, starter
    flag, own); `game` = DK's competition id from contests.db slate_player (production's
    key; it orders the sim's random draws), else the two teams; pool sorted as build_slate;
  - edge.dfs_nba_theory.simulate (3,000 sims, seed 0) and edge.dfs_opt_nba.optimize for
    cash and GPP (200 iters, seed 0, own_weight 0: production defaults);
  - a VARIANT (--variant) transforms the pool before the sim; "shipped" leaves it alone.
Truth: DK points from the NBA box score (nba_salary_shrink_lab.dk); a logged player with
no box row did not play = 0. Field: every exported contest on that draft group
(data/contests.db): the lineup's percentile among the field's live entries and whether it
clears the cash line (points of the last paid place). Cash lineup vs cash contests, GPP
lineup vs GPP contests, never pooled. Cluster: the slate date.
Split: TRAIN dates < 2026-10-31 <= TEST (same as nba_salary_shrink_lab.py).
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import nba_salary_shrink_lab as SL                  # noqa: E402
from edge import dfs_nba_theory as theory           # noqa: E402
from edge import dfs_opt_nba                        # noqa: E402
from edge.names import norm                         # noqa: E402

LOG = ROOT / "data" / "dfs_proj_log_nba.csv"
LINEUPS = ROOT / "data"                             # dfs_lineups_nba_<date>.csv
SPLIT = SL.SPLIT
N_SIMS, ITERS = 3000, 200


def boards() -> dict:
    """{gid: [pool rows]} from the projection log (the last pre-lock build per gid)."""
    by = defaultdict(list)
    with LOG.open() as fh:
        for r in csv.DictReader(fh):
            try:
                proj, sal = float(r["proj"] or 0), float(r["salary"] or 0)
            except ValueError:
                continue
            elig = dfs_opt_nba.eligible(r["dk_pos"])
            if proj <= 0 or not elig or sal <= 0:
                continue
            by[int(r["gid"])].append({
                "name": r["player"], "team": r["team"], "opp": r["opp"], "date": r["date"],
                "game": "@".join(sorted((r["team"], r["opp"]))), "salary": sal, "dk_pos": r["dk_pos"],
                "elig": elig, "pos": sorted(elig, key=["PG", "SG", "SF", "PF", "C"].index)[0],
                "proj": proj, "own": float(r["own"] or 0), "starter": r.get("starter") == "1",
                "lineup": r.get("lineup") or "", "promoted": r.get("promoted") == "1",
                "minutes": float(r["minutes"] or 0) if r.get("minutes") else 0.0,
                "recent": float(r["recent"]) if r.get("recent") not in (None, "") else None})
    # DK's competition id (production's `game`, which fixes the sim's random-number order)
    # where contests.db has the slate's players (every board with an exported contest)
    c = sqlite3.connect(SL.DB)
    comp = {(dg, t): g for dg, t, g in c.execute("select distinct draft_group, team, game from slate_player")}
    for gid, ps in by.items():
        for p in ps:
            if (gid, p["team"]) in comp:
                p["game"] = comp[(gid, p["team"])]
    return {g: ps for g, ps in by.items() if len({p["game"] for p in ps}) >= 2 and len(ps) >= 16}


def logged_lineups() -> dict:
    """{(gid, mode): set of names} from data/dfs_lineups_nba_<date>.csv."""
    out = defaultdict(set)
    for f in LINEUPS.glob("dfs_lineups_nba_*.csv"):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                if r.get("gid"):                     # pre-10/20 files have no gid column
                    out[(int(r["gid"]), r["mode"])].add(r["player"])
    return out


# ---- variants: a pool transform applied before the sim (the cycle's candidates) ----
VARIANTS: dict = {"shipped": None}


def build(ps: list[dict], variant: str = "shipped") -> dict:
    """{mode: [names]} for one board, production sim + optimizer."""
    pool = [dict(p) for p in ps]
    if VARIANTS.get(variant):
        VARIANTS[variant](pool)
    pool.sort(key=lambda p: (str(p.get("game") or ""), str(p.get("team") or ""), p["name"]))
    sim = theory.simulate(pool, n_sims=N_SIMS, seed=0)
    theory.summarise(sim, pool)
    out = {}
    for mode in ("cash", "gpp"):
        res = dfs_opt_nba.optimize(pool, sim, mode=mode, iters=ITERS, seed=0)
        out[mode] = [p["name"] for _, p in (res or {}).get("slots") or []]
    return out


# ---- speed (Adam, 11/8: "make replays faster"): a board's build is deterministic (seeded
# sim + climb), so builds run in parallel, one board per process, and are cached on disk
# keyed by the board's rows + the variant + the SOURCE of every module the build runs
# (theory / optimizer / climb / ceiling quantiles / this file's variants): editing any of
# them misses the cache by construction. Median board ~15 s, the 312-player one ~2 min;
# 16 workers + the cache take a full report from ~25 min to ~2 min cold, seconds warm.
CACHE = ROOT / "data" / "nba_lineup_lab_cache"
SOURCES = ("edge/dfs_nba_theory.py", "edge/dfs_opt_nba.py", "edge/dfs_opt_climb.py",
           "edge/nba_ceiling_quantiles.py", "scripts/nba_lineup_lab.py")
WORKERS = 16


def _key(ps: list[dict], variant: str) -> str:
    import hashlib
    h = hashlib.sha1(variant.encode())
    for f in SOURCES:
        h.update((ROOT / f).read_bytes())
    h.update(json.dumps(ps, sort_keys=True, default=str).encode())
    return h.hexdigest()[:20]


def _build_one(args):
    gid, ps, variant = args
    return gid, build(ps, variant)


def build_all(B: dict, variant: str = "shipped", workers: int = WORKERS) -> dict:
    """{gid: {mode: [names]}} for every board, cached and in parallel."""
    from concurrent.futures import ProcessPoolExecutor
    CACHE.mkdir(parents=True, exist_ok=True)
    out, todo = {}, []
    for gid, ps in B.items():
        f = CACHE / f"{gid}-{_key(ps, variant)}.json"
        if f.exists():
            out[gid] = json.loads(f.read_text())
        else:
            todo.append((gid, ps, variant))
    if todo:
        # biggest boards first so one 2-minute board does not start last
        todo.sort(key=lambda t: -len(t[1]))
        with ProcessPoolExecutor(max_workers=min(workers, len(todo))) as ex:
            for gid, lu in ex.map(_build_one, todo):
                out[gid] = lu
                (CACHE / f"{gid}-{_key(B[gid], variant)}.json").write_text(json.dumps(lu))
    return out


def truth() -> dict:
    return {(d, n): v for (d, n), v in _box().items()}


_BOX: dict = {}


def _box() -> dict:
    if not _BOX:
        for r in json.loads(SL.BOX.read_text()):
            _BOX[(r["GAME_DATE"], norm(r["PLAYER_NAME"]))] = SL.dk(r)
    return _BOX


def fields() -> dict:
    """{gid: [(contest id, type, sorted points desc, cash line)]}."""
    c = sqlite3.connect(SL.DB)
    out = defaultdict(list)
    for cid, dg, typ, paid in c.execute("select contest_id, draft_group, type, places_paid from contest "
                                        "where sport='NBA' and slate_date >= '2026-10-01' and places_paid > 0"):
        pts = np.array(sorted((p for (p,) in c.execute(
            "select points from entry where contest_id=? and points is not null", (cid,))), reverse=True))
        if len(pts):
            out[dg].append((cid, typ, pts, pts[min(paid, len(pts)) - 1]))
    return out


def score(gid: int, ps: list[dict], lineup: list[str]) -> float | None:
    if len(lineup) != 8:
        return None
    d = ps[0]["date"]
    box = _box()
    if not any(k[0] == d for k in box):
        return None                                  # box not collected yet
    return float(sum(box.get((d, norm(n)), 0.0) for n in lineup))


def rows(variant: str = "shipped") -> list[dict]:
    """One row per (lineup mode, contest of the same type) on a graded board."""
    B, F = boards(), fields()
    B = {g: ps for g, ps in B.items() if g in F}          # only boards with a contest field
    built = build_all(B, variant)
    out = []
    for gid, ps in sorted(B.items(), key=lambda kv: kv[1][0]["date"]):
        lu = built[gid]
        for mode in ("cash", "gpp"):
            pts = score(gid, ps, lu[mode])
            if pts is None:
                continue
            for cid, typ, field, line in F[gid]:
                if typ != mode:
                    continue
                out.append({"gid": gid, "contest": cid, "date": ps[0]["date"], "mode": mode,
                            "points": pts, "pctile": float((field < pts).mean()),
                            "cashed": bool(pts >= line), "line": float(line), "field_n": len(field),
                            "test": ps[0]["date"] >= SPLIT})
    return out


def parity() -> int:
    B, L = boards(), logged_lineups()
    B = {g: ps for g, ps in B.items() if (g, "cash") in L}
    built = build_all(B)
    same = {"cash": [0, 0], "gpp": [0, 0]}
    for gid, ps in B.items():
        lu = built[gid]
        for mode in ("cash", "gpp"):
            if (gid, mode) in L:
                same[mode][1] += 1
                same[mode][0] += set(lu[mode]) == L[(gid, mode)]
    for mode, (a, n) in same.items():
        print(f"{mode}: replay == logged lineup on {a} of {n} draft groups")
    return 0


def _boot(cl, d, n=2000, seed=0):
    ids, inv = np.unique(cl, return_inverse=True)
    s = np.bincount(inv, weights=d); c = np.bincount(inv).astype(float)
    k = np.random.default_rng(seed).integers(0, len(ids), (n, len(ids)))
    b = s[k].sum(1) / c[k].sum(1)
    return float(d.mean()), float(np.percentile(b, 5)), float(np.percentile(b, 95))


def report(variants: list[str]) -> int:
    res = {v: rows(v) for v in variants}
    base = {(r["mode"], r["contest"]): r for r in res[variants[0]]}
    print(f"# NBA lineups replayed (production sim + optimizer), scored vs the real field\n")
    print("| variant | split | mode | contests | dates | mean field pctile | cashed | mean points | "
          f"pctile diff vs {variants[0]} [90%, by date] |")
    print("|---|---|---|---|---|---|---|---|---|")
    for v, rs in res.items():
        for split in ("train", "test"):
            for mode in ("cash", "gpp"):
                s = [r for r in rs if r["mode"] == mode and r["test"] == (split == "test")]
                if not s:
                    continue
                d = np.array([r["pctile"] - base[(r["mode"], r["contest"])]["pctile"] for r in s])
                m, lo, hi = _boot(np.array([r["date"] for r in s]), d)
                print(f"| {v} | {split} | {mode} | {len(s)} | {len({r['date'] for r in s})} | "
                      f"{np.mean([r['pctile'] for r in s]):.3f} | {np.mean([r['cashed'] for r in s]):.0%} | "
                      f"{np.mean([r['points'] for r in s]):.1f} | {m:+.3f} [{lo:+.3f}, {hi:+.3f}] |")
    return 0


def gate(path: str) -> int:
    """critique_gate rows: one per (mode, contest); pred = 1 - field percentile (a loss: 0 =
    beat everyone), actual = 0, so a lower loss = a better lineup (use loss "abs"). State =
    mode; split = primary on TEST dates, guard on train dates (a candidate fits on train)."""
    n = 0
    with open(path, "w") as fh:
        for r in rows("shipped"):
            fh.write(json.dumps({"key": f"{r['mode']}|{r['contest']}", "cluster": r["date"],
                                 "split": "primary" if r["test"] else "guard",
                                 "state": r["mode"], "pred": 1.0 - r["pctile"], "actual": 0.0}) + "\n")
            n += 1
    print(f"{n} gate rows -> {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("parity")
    r = sub.add_parser("report")
    r.add_argument("--variant", action="append", help="variants to compare (shipped is always first)")
    g = sub.add_parser("gate")
    g.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "parity":
        return parity()
    if a.cmd == "gate":
        return gate(a.out)
    return report(["shipped"] + [v for v in (a.variant or []) if v != "shipped"])


if __name__ == "__main__":
    raise SystemExit(main())
