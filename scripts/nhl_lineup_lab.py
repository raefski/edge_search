#!/usr/bin/env python3
"""NHL lineups, held out: the PRODUCTION sim + optimizers replayed on every cached pre-lock
board, scored with DK's own points against the real contest fields.

    python3 scripts/nhl_lineup_lab.py parity                 # replay == build_board; logged lineups
    python3 scripts/nhl_lineup_lab.py report [--n 20] [--variant NAME ...]
    python3 scripts/nhl_lineup_lab.py rows OUT.json [--variant NAME ...]   # one row per set x contest
    python3 scripts/nhl_lineup_lab.py gate OUT.jsonl         # rows for harness/critique_gate.py

Why it exists (NHL critique cycle 13, 2026-11-10, target picked by the dfs-target-scout).
Twelve cycles scored skater DK points (scripts/nhl_skater_lab.py; since cycle 3 the gains are
thousandths of RMSE) while nothing scored the lineups we enter: GPP 8+ game slates ran -65%
(20 entries / 11 slates), 501-5,000 fields -55%, and every slate since 10/26 lost. NBA (11/8)
and NCAAB (cycle 4, 11/10) learned the same thing: player metrics fine, lineups not. This
scores what we enter, against the field that was actually there.

WHAT IS REPLAYED. The board is the skater lab's LOCK cache (data/nhl_cache/lab/lock/<gid>.json:
build_board's pool just before nhl_sim.simulate -- means, lines, PP, goalies, games with the
implied goals -- rebuilt from inputs pinned at lock: DK draftables + DailyFaceoff from git,
odds from the last dfs_nhl scan; `nhl_skater_lab.py cache` refreshes it with today's code).
Then, as build_board + build_slate + the phone page (2,000 sims, search effort 150):
nhl_sim.simulate (seed k) -> summarise -> add_ownership (buzz weight is 0, so buzz is not
re-attached), and three lineups:
  cash        dfs_nhl_field.optimize_cash (production's cash pick since 10/10, NHL_STATUS 6c)
  cash_floor  dfs_opt_nhl.optimize(mode="cash"): the 25th-percentile lineup logged beside it
  gpp         dfs_opt_nhl.optimize(mode="gpp", own_weight=0)
Seed 0 = the lineup the page shows ("single"); seeds 0..n-1 = SET_N independent builds
("set"), so one slate's optimizer luck does not decide anything.

TRUTH per lineup player: DK's own fantasy points for the draft group (contests.db
player_result -- what the field was scored with), else the NHL box score
(data/nhl_cache/box, nhl_skater_lab.box_by_date; starting goalies only), else 0 (did not play;
late swap is not modelled). SOURCE_COUNT says how often each was used.

SCORING. Each lineup is scored in every exported contest of ITS type on its draft group (cash
and cash_floor vs cash contests, gpp vs GPPs; never pooled): field percentile among live
entries, cashed (>= the last paid place), $ returned per $1 (contests.db payout table, ties
not split). Also scored: the lineups the live board logged ("as_run", data/dfs_lineups_nhl_*)
and Adam's own entries ("adam", contests.db is_adam, mean over his entries).

THE SPLIT. The slate is the unit (entries in one contest share one realisation). The skater
model was fitted on nights before 2026-10-13 (nhl_skater_lab.TEST_FROM), so `test` (10/13 on)
is held out for it; `late` (10/27 on) is the losing stretch the scout flagged. The optimizers
and cash-field weights were fitted on the first eight nights (10/10, train). A variant that
fits anything fits on TRAIN slates only (or leave-one-slate-out) and is judged on test.
Under ~5 slates in a cell nothing is called proven.

VARIANTS: VARIANTS[name] = {"board": fn(slate) -> slate (pre-sim pool edits, optional),
"build": fn(pool, sim, mode, seed, slate) -> lineup result | None (optional)}; missing keys fall
back to production. `report --variant NAME` prints it beside production.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from edge import dfs_nhl_field, dfs_nhl_theory as theory, dfs_opt_nhl, nhl_sim  # noqa: E402
from edge.names import norm                                                    # noqa: E402

LAB = ROOT / "data" / "nhl_cache" / "lab" / "lock"
LINEUPS = ROOT / "data"                          # dfs_lineups_nhl_<date>.csv
DB = ROOT / "data" / "contests.db"
N_SIMS, ITERS = 2000, 150                        # the NHL page's defaults (2,000 sims, effort 150)
SET_N = 20
WORKERS = max(1, (os.cpu_count() or 2) - 1)
MODES = ("cash", "cash_floor", "gpp")
CONTEST_TYPE = {"cash": "cash", "cash_floor": "cash", "gpp": "gpp"}
TEST_FROM = "2026-10-13"                         # nhl_skater_lab.TEST_FROM
LATE_FROM = "2026-10-27"


def split_of(date: str) -> str:
    return "train" if date < TEST_FROM else "test"


# ---------------------------------------------------------------------------
# Boards, fields, truth
# ---------------------------------------------------------------------------
_BOARDS: dict = {}


def boards() -> dict:
    """{gid: cached lock board} (nhl_skater_lab's cache)."""
    if not _BOARDS:
        for f in sorted(LAB.glob("*.json")):
            d = json.loads(f.read_text())
            _BOARDS[int(d["gid"])] = d
    return _BOARDS


_FIELDS: dict = {}


def fields() -> dict:
    """{gid: [(contest id, type, points desc, cash line)]}, NHL contests with a field."""
    if _FIELDS:
        return _FIELDS
    c = sqlite3.connect(DB)
    for cid, dg, typ, paid in c.execute("select contest_id, draft_group, type, places_paid from contest "
                                        "where sport='NHL' and places_paid > 0 order by contest_id"):
        pts = np.array(sorted((p for (p,) in c.execute(
            "select points from entry where contest_id=? and points is not null and n_players > 0",
            (cid,))), reverse=True))
        if len(pts) and dg:
            _FIELDS.setdefault(int(dg), []).append((cid, typ, pts, float(pts[min(paid, len(pts)) - 1])))
    return _FIELDS


_PAY: dict = {}


def payout(cid: int, field: np.ndarray, x: float) -> float:
    """$ returned per $1 entered for a lineup scoring x (its rank among the real field)."""
    if not _PAY:
        c = sqlite3.connect(DB)
        fee = dict(c.execute("select contest_id, entry_fee from contest where sport='NHL'"))
        for k, lo, hi, amt in c.execute("select contest_id, rank_lo, rank_hi, amount from payout"):
            if k in fee:
                _PAY.setdefault(k, {"fee": fee[k] or 0, "tiers": []})["tiers"].append((lo, hi, amt))
    pay = _PAY.get(cid)
    if not pay or not pay["fee"]:
        return 0.0
    rank = 1 + int((field > x).sum())
    return next((amt for lo, hi, amt in pay["tiers"] if lo <= rank <= hi), 0.0) / pay["fee"]


_DK: dict = {}
_BOX: list = []
SOURCE_COUNT: dict = defaultdict(int)


def _dk(gid: int) -> dict:
    if gid not in _DK:
        c = sqlite3.connect(DB)
        _DK[gid] = {pn: f for pn, f in c.execute(
            "select pr.player_norm, max(pr.fpts) from player_result pr join contest k using(contest_id) "
            "where k.draft_group=? and pr.fpts is not null group by pr.player_norm", (gid,))}
    return _DK[gid]


def player_points(gid: int, date: str, p: dict) -> float:
    dk = _dk(gid)
    k = norm(p["name"])
    if k in dk:
        SOURCE_COUNT["dk"] += 1
        return float(dk[k])
    if not _BOX:
        import nhl_skater_lab
        from nhl_ownership_signal import box_key
        _BOX[:] = [nhl_skater_lab.box_by_date()[0], box_key]
    box, box_key = _BOX
    bk = box_key(p["team"], p["name"])
    if p.get("pos") == "G":
        bk = ("G",) + bk
    if bk in box.get(date, {}):
        SOURCE_COUNT["box"] += 1
        return float(box[date][bk])
    SOURCE_COUNT["none (0)"] += 1
    return 0.0


def score(gid: int, date: str, lineup: list[dict]) -> float | None:
    return float(sum(player_points(gid, date, p) for p in lineup)) if len(lineup) == 9 else None


def logged_lineups() -> dict:
    """{(gid, mode): [{name, team, pos}]} from data/dfs_lineups_nhl_<date>.csv."""
    out = defaultdict(list)
    for f in sorted(LINEUPS.glob("dfs_lineups_nhl_*.csv")):
        with f.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("gid"):
                    out[(int(r["gid"]), r["mode"])].append(
                        {"name": r["player"], "team": r["team"], "pos": "G" if r["slot"] == "G" else "S"})
    return dict(out)


def adam_entries() -> dict:
    """{contest id: [points]} for Adam's own entries (contests.db is_adam)."""
    c = sqlite3.connect(DB)
    out = defaultdict(list)
    for cid, p in c.execute("select e.contest_id, e.points from entry e join contest k using(contest_id) "
                            "where k.sport='NHL' and e.is_adam=1 and e.points is not null"):
        out[cid].append(float(p))
    return dict(out)


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------
def production_build(pool, sim, mode, seed, slate=None):
    """build_slate's three lineups (edge/dfs_run_nhl.build_slate)."""
    if mode == "cash":
        try:
            return dfs_nhl_field.optimize_cash(pool, sim, iters=ITERS, seed=seed)
        except ValueError:                                  # build_slate falls back the same way
            return dfs_opt_nhl.optimize(pool, sim, mode="cash", iters=ITERS, seed=seed)
    if mode == "cash_floor":
        return dfs_opt_nhl.optimize(pool, sim, mode="cash", iters=ITERS, seed=seed)
    return dfs_opt_nhl.optimize(pool, sim, mode="gpp", iters=ITERS, seed=seed, own_weight=0.0)


VARIANTS: dict = {"shipped": {}}


def variant(name: str, board=None, build=None):
    VARIANTS[name] = {k: v for k, v in (("board", board), ("build", build)) if v}


def board_sim(slate: dict, seed: int) -> tuple[list[dict], np.ndarray]:
    """build_board's last three steps on the cached pre-sim board."""
    pool = copy.deepcopy(slate["pool"])
    sim = nhl_sim.simulate(pool, slate["games"], n_sims=N_SIMS, seed=seed)
    pool = nhl_sim.summarise(sim, pool)
    theory.add_ownership(pool)
    return pool, sim


def _slim(res) -> list[dict] | None:
    if not res:
        return None
    return [{"name": p["name"], "team": p["team"], "pos": p["pos"], "salary": p["salary"],
             "proj": p["proj"], "own": p.get("own")} for p in res["lineup"]]


def _job(args):
    vname, gid, seed = args
    v = VARIANTS[vname]
    slate = boards()[gid]
    if "board" in v:
        slate = v["board"](copy.deepcopy(slate))
    pool, sim = board_sim(slate, seed)
    build = v.get("build", production_build)
    return vname, gid, seed, {m: _slim(build(pool, sim, m, seed, slate)) for m in MODES}


def build(vnames: list[str], gids: list[int], n: int = SET_N, workers: int = WORKERS) -> dict:
    """{variant: {gid: {mode: [lineup for seed 0..n-1]}}} (seed 0 = the page's lineup)."""
    out = {v: {g: {m: [None] * n for m in MODES} for g in gids} for v in vnames}
    jobs = [(v, g, s) for v in vnames for g in gids for s in range(n)]
    with ProcessPoolExecutor(workers) as ex:
        for v, gid, seed, lus in ex.map(_job, jobs, chunksize=1):
            for m in MODES:
                out[v][gid][m][seed] = lus[m]
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def contest_rows(gid: int, mode: str, lineups: list, label: str) -> list[dict]:
    b = boards()[gid]
    pts = [x for x in (score(gid, b["date"], lu) for lu in lineups if lu) if x is not None]
    if not pts:
        return []
    out = []
    for cid, typ, field, line in fields().get(gid, []):
        if typ != CONTEST_TYPE[mode]:
            continue
        out.append({"variant": label, "gid": gid, "date": b["date"], "games": len(b["games"]),
                    "split": split_of(b["date"]), "late": b["date"] >= LATE_FROM,
                    "contest": cid, "mode": mode, "n": len(pts), "field_n": len(field), "line": line,
                    "points": float(np.mean(pts)),
                    "pctile": float(np.mean([(field < x).mean() for x in pts])),
                    "cashed": float(np.mean([x >= line for x in pts])),
                    "ret": float(np.mean([payout(cid, field, x) for x in pts]))})
    return out


def graded_gids() -> list[int]:
    B, F = boards(), fields()
    return sorted(g for g in B if g in F)


def all_rows(vnames: list[str], n: int = SET_N, extras: bool = True) -> list[dict]:
    gids = graded_gids()
    built = build(vnames, gids, n)
    out = []
    for v in vnames:
        for gid in gids:
            for mode in MODES:
                lus = built[v][gid][mode]
                out += contest_rows(gid, mode, lus[:1], f"{v}/single")
                out += contest_rows(gid, mode, lus, f"{v}/set{n}")
    if not extras:
        return out
    L = logged_lineups()
    for gid in gids:
        for mode in MODES:
            if (gid, mode) in L and len(L[(gid, mode)]) == 9:
                out += contest_rows(gid, mode, [L[(gid, mode)]], "as_run (logged)")
    adam = adam_entries()
    B = boards()
    for gid in gids:
        b = B[gid]
        for cid, typ, field, line in fields()[gid]:
            for x in adam.get(cid, []):
                out.append({"variant": "adam (entered)", "gid": gid, "date": b["date"],
                            "games": len(b["games"]), "split": split_of(b["date"]),
                            "late": b["date"] >= LATE_FROM, "contest": cid,
                            "mode": "cash" if typ == "cash" else "gpp", "n": 1, "field_n": len(field),
                            "line": line, "points": x, "pctile": float((field < x).mean()),
                            "cashed": float(x >= line), "ret": payout(cid, field, x)})
    return out


def _boot(cl, d, n=2000, seed=0):
    ids, inv = np.unique(cl, return_inverse=True)
    s = np.bincount(inv, weights=d)
    c = np.bincount(inv).astype(float)
    k = np.random.default_rng(seed).integers(0, len(ids), (n, len(ids)))
    b = s[k].sum(1) / c[k].sum(1)
    return float(d.mean()), float(np.percentile(b, 5)), float(np.percentile(b, 95))


def _line(label: str, s: list[dict]) -> str:
    m, lo, hi = _boot(np.array([r["gid"] for r in s]), np.array([r["pctile"] for r in s]))
    rm, rlo, rhi = _boot(np.array([r["gid"] for r in s]), np.array([r["ret"] for r in s]), seed=1)
    nsl = len({r["gid"] for r in s})
    return (f"| {label} | {len(s)} | {nsl}{'*' if nsl < 5 else ''} | {np.mean([r['n'] for r in s]):.0f} | "
            f"{m:.3f} [{lo:.3f}, {hi:.3f}] | {np.mean([r['cashed'] for r in s]):.0%} | "
            f"{np.mean([r['points'] for r in s]):.1f} | {rm:.2f} [{rlo:.2f}, {rhi:.2f}] |")


SLICES = {
    "all": lambda r: True,
    "train (< 10/13)": lambda r: r["split"] == "train",
    "test (10/13 on)": lambda r: r["split"] == "test",
    "late (10/27 on)": lambda r: r["late"],
    "8+ games": lambda r: r["games"] >= 8,
    "< 8 games": lambda r: r["games"] < 8,
    "field <= 500": lambda r: r["field_n"] <= 500,
    "field 501-5,000": lambda r: 500 < r["field_n"] <= 5000,
    "field > 5,000": lambda r: r["field_n"] > 5000,
}


def table(rows: list[dict], mode: str, slices=("all", "train (< 10/13)", "test (10/13 on)",
                                               "late (10/27 on)")) -> list[str]:
    out = ["| variant / slice | contests | slates | lineups | field pctile [90%, by slate] | cashed | "
           "mean pts | $ back per $1 [90%] |", "|---|---|---|---|---|---|---|---|"]
    for v in dict.fromkeys(r["variant"] for r in rows):
        for sl in slices:
            s = [r for r in rows if r["variant"] == v and r["mode"] == mode and SLICES[sl](r)]
            if s:
                out.append(_line(f"{v} / {sl}", s))
    return out


def report(vnames: list[str], n: int) -> str:
    rows = all_rows(vnames, n)
    gids = sorted({r["gid"] for r in rows})
    out = ["# NHL lineups replayed (production sim + optimizers), scored vs the real field", "",
           f"{len(gids)} slates with a contest field and a lock board; * = under 5 slates "
           "(not enough data).", "",
           "truth per lineup player: " + ", ".join(f"{k} {v:,}" for k, v in SOURCE_COUNT.items()), ""]
    for mode in MODES:
        out += [f"## {mode} (vs {CONTEST_TYPE[mode]} contests)", ""]
        out += table(rows, mode)
        out.append("")
    out += ["## gpp by slate size and field", ""]
    out += table(rows, "gpp", ("8+ games", "< 8 games", "field <= 500", "field 501-5,000",
                               "field > 5,000"))
    out += ["", "## per slate (gpp, set / as_run / adam)", ""]
    for r in sorted(rows, key=lambda r: (r["date"], r["contest"], r["variant"])):
        if r["mode"] == "gpp" and ("/set" in r["variant"] or "(" in r["variant"]):
            out.append(f"  {r['date']} {r['gid']} g{r['games']:<2} {r['contest']} field {r['field_n']:>5} "
                       f"{r['variant']:<20} pts {r['points']:6.1f} pctile {r['pctile']:.2f} "
                       f"cashed {r['cashed']:.0%} $ {r['ret']:.2f}")
    return "\n".join(out)


def parity() -> str:
    """(1) the replayed seed-0 projections vs what build_board produced on the same cached
    board (proj_shipped): any gap means the replay is not the model; (2) players shared between
    the replayed seed-0 lineups and the lineups the live board logged that night (live builds
    ran earlier in the day and on the code of that day, so this is information, not parity)."""
    out, B, L = [], boards(), logged_lineups()
    worst, stale = 0.0, []
    for gid in graded_gids():
        pool, _ = board_sim(B[gid], 0)
        d = [abs(p["proj"] - p["proj_shipped"]) for p in pool if p.get("proj_shipped") is not None]
        worst = max(worst, max(d) if d else 0.0)
        if d and max(d) > 0.006:                       # proj_shipped is rounded to 0.01
            stale.append(gid)
    out.append(f"projection parity, {len(graded_gids())} graded boards: max |replay - build_board| "
               f"{worst:.4f}" + (f" -- re-cache {stale} (nhl_skater_lab.py cache --state lock)"
                                 if stale else ""))
    gids = [g for g in graded_gids() if (g, "gpp") in L]
    built = build(["shipped"], gids, n=1)["shipped"]
    for gid in gids:
        parts = []
        for mode in MODES:
            if (gid, mode) in L and built[gid][mode][0]:
                got = {p["name"] for p in built[gid][mode][0]}
                parts.append(f"{mode} {len(got & {p['name'] for p in L[(gid, mode)]})}/9")
        out.append(f"  {gid} {B[gid]['date']}: " + ", ".join(parts))
    return "\n".join(out)


def gate(path: str, n: int = SET_N) -> int:
    """critique_gate rows: one per (mode, contest) for the shipped SET on TEST slates; pred = 1 -
    mean field percentile (a loss, 0 = beat everyone), actual = 0 (loss "abs"); state = mode
    (cash, gpp; cash_floor is not entered). Not wired into harness/critique_gate.py: Adam
    approves adding it."""
    rows = [r for r in all_rows(["shipped"], n, extras=False)
            if r["variant"] == f"shipped/set{n}" and r["split"] == "test" and r["mode"] != "cash_floor"]
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps({"key": f"{r['mode']}|{r['contest']}", "cluster": str(r["gid"]),
                                 "split": "primary", "state": r["mode"],
                                 "pred": 1.0 - r["pctile"], "actual": 0.0}) + "\n")
    print(f"{len(rows)} gate rows ({len({r['gid'] for r in rows})} slates) -> {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("parity")
    r = sub.add_parser("report")
    r.add_argument("--variant", action="append", default=[])
    r.add_argument("--n", type=int, default=SET_N)
    d = sub.add_parser("rows")
    d.add_argument("out")
    d.add_argument("--variant", action="append", default=[])
    d.add_argument("--n", type=int, default=SET_N)
    g = sub.add_parser("gate")
    g.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "parity":
        print(parity())
        return 0
    if a.cmd == "gate":
        return gate(a.out)
    names = ["shipped"] + [v for v in a.variant if v != "shipped"]
    if a.cmd == "rows":
        Path(a.out).write_text(json.dumps(all_rows(names, a.n)))
        print(f"rows -> {a.out}")
        return 0
    print(report(names, a.n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
