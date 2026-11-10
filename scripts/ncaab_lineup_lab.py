#!/usr/bin/env python3
"""College basketball lineups, held out: the PRODUCTION sim + optimizer replayed on every
logged pre-lock board, scored with DK / box-score points against the real contest fields.

    python3 scripts/ncaab_lineup_lab.py parity                 # replayed lineups vs the logged ones
    python3 scripts/ncaab_lineup_lab.py report [--n 20]        # field pctile / cashed / $ return, by mode
    python3 scripts/ncaab_lineup_lab.py rows OUT.json          # one row per lineup x contest (critics)
    python3 scripts/ncaab_lineup_lab.py gate OUT.jsonl         # rows for harness/critique_gate.py

Why it exists (NCAAB critique cycle 4, 2026-11-10, target picked by the dfs-target-scout).
Cycles 1-3 improved player DK-points MAE (scripts/ncaab_lab.py, pooled 6.35 -> 6.09) while
college cash lost -67% (6 entries / 4 slates, beat 30% of 17-23 entrant Double Ups) and
nothing scored the lineups we enter. NBA learned the same lesson on 11/8 (cycle 27: player
metrics looked fine while the lineups sat 35 points under their projection). This scores
what we enter, against the field that was actually there.

What is replayed, per draft group in data/dfs_proj_log_ncaab.csv (the last pre-lock build,
edge/dfs_forward_log.py) that has an exported contest (data/contests.db):
  - the pool in LOG ORDER (= build_board's pool order, which fixes the sim's random draws),
    salary, dk_pos -> slots / group, team / opp; `game` = DK's competition id from
    contests.db slate_player (production's key), else the two teams;
  - the projection of a VARIANT (below), turned into a box-score split with production's
    means_from_total on the position profile, then rescaled once so the 2,000-sim mean equals
    the variant's projection (the logged proj IS a sim mean; the split is not logged);
  - edge.ncaab_sim.simulate with the logged implied team points (or
    data/ncaab_implied_backfill.json, else league average), summarise, add_ownership, and
    edge.dfs_opt_ncaab.optimize for cash and GPP (150 iters, own_weight 0: build_slate's
    defaults).
NOT replayed exactly: props players' own stat split, a player's Torvik split (neither is
logged), and each game's home / away side (the log has no matchup; the RNG order of team
efficiencies follows it). `parity` says how close that gets: players shared with the logged
lineup, per draft group.

Variants (a projection per logged row):
  - "as_logged": the logged proj, i.e. the board as it ran that night (11/2-11/6 without
    LAST_GAME, 11/7 with cycle 1's, 11/8 on cycle 2's);
  - "shipped" (the baseline, today's production): scripts/ncaab_lab.py's pre-LAST_GAME base +
    production apply_last_game with LAST_GAME REFIT ON THE OTHER SLATES (leave one slate out,
    as ncaab_lab's honest table). Needs the slate's ESPN box (data/ncaab_box/<gid>.json).
A critic adds a variant to VARIANTS: fn(gid, rows, data) -> {name|team: proj}.

Two measurements per variant and board:
  - "single": the one lineup build_slate gives (sim seed 0, climb seed 0);
  - "set": SET_N independent best lineups (sim seed k, climb seed k, k < SET_N): the
    optimizer's own luck averaged out, which one lineup per slate on 4 slates cannot do.
Truth per lineup player: DK's own points for the draft group (contests.db player_result: what
the field was scored with), else the ESPN box (scripts/ncaab_box_grade join), else 0 (did not
play). Each lineup is scored in every exported contest of ITS type on its draft group (cash
lineup vs Double Ups, GPP vs GPPs, never pooled): field percentile among live entries, cashed
(>= the last paid place's points), $ returned per $1 (contests.db payout table). Also scored:
the lineups the board logged ("as_run") and Adam's own entries (contests.db is_adam).
Cluster: the slate (draft group). Every slate is held out for "shipped" (LOSO); under ~5
slates nothing here is called proven.
"""
from __future__ import annotations

import argparse
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
from edge import dfs_ncaab_theory as theory, dfs_opt_ncaab, ncaab, ncaab_sim  # noqa: E402
from edge import dfs_run_ncaab as RC                                          # noqa: E402
from edge.names import norm                                                   # noqa: E402

LOG = ROOT / "data" / "dfs_proj_log_ncaab.csv"
LINEUPS = ROOT / "data"                         # dfs_lineups_ncaab_<date>.csv
DB = ROOT / "data" / "contests.db"
BACKFILL = ROOT / "data" / "ncaab_implied_backfill.json"
N_SIMS, ITERS = 2000, 150                       # build_slate's defaults
SET_N = 20
WORKERS = max(1, (os.cpu_count() or 2) - 1)
MODES = ("cash", "gpp")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Boards, fields, truth
# ---------------------------------------------------------------------------
def boards() -> dict:
    """{gid: [logged rows in log order]} (the last pre-lock build per draft group)."""
    by = defaultdict(list)
    with LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            by[int(r["gid"])].append(r)
    return dict(by)


_FIELDS: dict = {}


def fields() -> dict:
    """{gid: [(contest id, type, points desc, cash line)]}, NCAAB contests with a field."""
    if _FIELDS:
        return _FIELDS
    c = sqlite3.connect(DB)
    for cid, dg, typ, paid in c.execute("select contest_id, draft_group, type, places_paid from contest "
                                        "where sport='NCAAB' and places_paid > 0 order by contest_id"):
        pts = np.array(sorted((p for (p,) in c.execute(
            "select points from entry where contest_id=? and points is not null and n_players > 0",
            (cid,))), reverse=True))
        if len(pts):
            _FIELDS.setdefault(dg, []).append((cid, typ, pts, float(pts[min(paid, len(pts)) - 1])))
    return _FIELDS


_PAY: dict = {}


def payout(cid: int, field: np.ndarray, x: float) -> float:
    """$ returned per $1 entered for a lineup scoring x (its rank among the real field)."""
    if not _PAY:
        c = sqlite3.connect(DB)
        fee = dict(c.execute("select contest_id, entry_fee from contest where sport='NCAAB'"))
        for k, lo, hi, amt in c.execute("select contest_id, rank_lo, rank_hi, amount from payout"):
            if k in fee:
                _PAY.setdefault(k, {"fee": fee[k] or 0, "tiers": []})["tiers"].append((lo, hi, amt))
    pay = _PAY.get(cid)
    if not pay or not pay["fee"]:
        return 0.0
    rank = 1 + int((field > x).sum())
    return next((amt for lo, hi, amt in pay["tiers"] if lo <= rank <= hi), 0.0) / pay["fee"]


_TRUTH: dict = {}
SOURCE_COUNT: dict = defaultdict(int)


def truth(gid: int) -> tuple[dict, dict]:
    """({norm name: DK points}, {norm name: box points}) for a draft group."""
    if gid not in _TRUTH:
        c = sqlite3.connect(DB)
        dk = {pn: f for pn, f in c.execute(
            "select pr.player_norm, max(pr.fpts) from player_result pr join contest k using(contest_id) "
            "where k.draft_group=? and pr.fpts is not null group by pr.player_norm", (gid,))}
        box = {}
        try:
            import ncaab_lab
            for r in ncaab_lab.load(gid):
                box[norm(r["player"])] = float(r["act"])
        except FileNotFoundError:
            pass
        _TRUTH[gid] = (dk, box)
    return _TRUTH[gid]


def player_points(gid: int, name: str) -> float:
    dk, box = truth(gid)
    k = norm(name)
    if k in dk:
        SOURCE_COUNT["dk"] += 1
        return float(dk[k])
    if k in box:
        SOURCE_COUNT["box"] += 1
        return box[k]
    SOURCE_COUNT["none (0)"] += 1
    return 0.0


def score(gid: int, lineup: list[str]) -> float | None:
    return float(sum(player_points(gid, n) for n in lineup)) if len(lineup) == 8 else None


def logged_lineups() -> dict:
    """{(gid, mode): [names]} from data/dfs_lineups_ncaab_<date>.csv."""
    out = defaultdict(list)
    for f in sorted(LINEUPS.glob("dfs_lineups_ncaab_*.csv")):
        with f.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("gid"):
                    out[(int(r["gid"]), r["mode"])].append(r["player"])
    return dict(out)


def adam_entries() -> dict:
    """{contest id: points} for Adam's own entries (contests.db is_adam)."""
    c = sqlite3.connect(DB)
    return {cid: p for cid, p in c.execute(
        "select e.contest_id, max(e.points) from entry e join contest k using(contest_id) "
        "where k.sport='NCAAB' and e.is_adam=1 group by e.contest_id")}


# ---------------------------------------------------------------------------
# Variants: a projection per logged row
# ---------------------------------------------------------------------------
def _key(r: dict) -> str:
    return f"{r['player']}|{r['team']}"


def _as_logged(gid: int, rows: list[dict], data: dict) -> dict:
    return {_key(r): _f(r["proj"]) or 0.0 for r in rows}


_LAB: dict = {}


def _lab_rows() -> dict:
    """{gid: scripts/ncaab_lab.load rows} for every slate with a box (LOSO training data)."""
    if not _LAB:
        import ncaab_lab
        for g in ncaab_lab.slates():
            _LAB[g] = ncaab_lab.load(g)
    return _LAB


def _shipped(gid: int, rows: list[dict], data: dict) -> dict:
    """Today's production, held out: the pre-LAST_GAME base + LAST_GAME refit without this slate."""
    import ncaab_lab
    lab = _lab_rows()
    if gid not in lab:
        raise KeyError(f"{gid}: no ESPN box (harness run ncaab-box-grade -- {gid})")
    train = [r for g, rs in lab.items() if g != gid for r in rs]
    proj = ncaab_lab.shipped(lab[gid], ncaab_lab.fit_last_game(train))
    return {_key(r): p for r, p in zip(lab[gid], proj)}


VARIANTS: dict = {"shipped": _shipped, "as_logged": _as_logged}


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------
_GAMES: dict = {}


def _comp_ids() -> dict:
    if not _GAMES:
        c = sqlite3.connect(DB)
        for dg, t, g in c.execute("select distinct draft_group, team, game from slate_player "
                                  "where game is not null and game != ''"):
            _GAMES[(dg, t)] = g
    return _GAMES


def make_pool(gid: int, rows: list[dict], proj: dict) -> tuple[list[dict], dict]:
    """(pool, sim games) for one board under a variant's projections, in log order."""
    comp = _comp_ids()
    backfill = json.loads(BACKFILL.read_text()).get(str(gid), {}) if BACKFILL.exists() else {}
    pool = []
    for r in rows:
        sal = _f(r["salary"])
        slots = ncaab.slots_for(r["dk_pos"])
        pos = RC.positions(r["dk_pos"])
        fp = proj.get(_key(r))
        if not sal or not slots or not pos or fp is None:
            continue
        group = pos[0]
        imp = _f(r.get("implied")) or backfill.get(r["team"])
        game = comp.get((gid, r["team"])) or "@".join(sorted((r["team"], r["opp"] or "")))
        pool.append({"name": r["player"], "team": r["team"], "opp": r["opp"], "game": game,
                     "salary": int(sal), "dk_pos": r["dk_pos"], "group": group, "slots": slots,
                     "target": max(float(fp), 0.0), "implied": imp,
                     "means": RC.means_from_total(max(float(fp), 0.0), RC.PROFILES[group])})
    games, seen = {}, {}
    for p in pool:
        if p["game"] in seen or not p["opp"]:
            continue
        seen[p["game"]] = 1
        o = next((q for q in pool if q["team"] == p["opp"]), None)
        a, h = p["team"], p["opp"]
        games[p["game"]] = {"home": h, "away": a,
                            "pts_home": (o or {}).get("implied") or RC.LEAGUE.team_points,
                            "pts_away": p["implied"] or RC.LEAGUE.team_points}
    return pool, games


def simulate(pool: list[dict], games: dict, seed: int) -> np.ndarray:
    """Production's sim, means rescaled once so each player's sim mean = his projection."""
    sim = ncaab_sim.simulate(pool, games, n_sims=N_SIMS, seed=seed, league=RC.LEAGUE)
    mean = sim.mean(axis=0)
    for i, p in enumerate(pool):
        k = p["target"] / mean[i] if mean[i] > 0 else 0.0
        p["means"] = {s: v * k for s, v in p["means"].items()}
    sim = ncaab_sim.simulate(pool, games, n_sims=N_SIMS, seed=seed, league=RC.LEAGUE)
    ncaab_sim.summarise(sim, pool, theory.CASH_PCT, theory.GPP_PCT)
    theory.add_ownership(pool)
    return sim


def lineup(pool: list[dict], games: dict, mode: str, seed: int) -> list[str]:
    pool = [dict(p, means=dict(p["means"])) for p in pool]
    sim = simulate(pool, games, seed)
    res = dfs_opt_ncaab.optimize(pool, sim, mode=mode, iters=ITERS, seed=seed)
    return [p["name"] for _, p in (res or {}).get("slots") or []]


def _job(args):
    gid, pool, games, mode, seed = args
    return gid, mode, seed, lineup(pool, games, mode, seed)


def build(variant: str, gids: list[int], n: int = SET_N, workers: int = WORKERS) -> dict:
    """{gid: {mode: [lineup for seed 0..n-1]}} (seed 0 = build_slate's lineup)."""
    B, out, jobs = boards(), defaultdict(dict), []
    for gid in gids:
        pool, games = make_pool(gid, B[gid], VARIANTS[variant](gid, B[gid], B))
        for mode in MODES:
            out[gid][mode] = [None] * n
            jobs += [(gid, pool, games, mode, s) for s in range(n)]
    with ProcessPoolExecutor(workers) as ex:
        for gid, mode, seed, lu in ex.map(_job, jobs, chunksize=1):
            out[gid][mode][seed] = lu
    return dict(out)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def contest_rows(gid: int, mode: str, lineups: list[list[str]], date: str, label: str) -> list[dict]:
    pts = [x for x in (score(gid, lu) for lu in lineups if lu) if x is not None]
    if not pts:
        return []
    out = []
    for cid, typ, field, line in fields().get(gid, []):
        if typ != mode:
            continue
        out.append({"variant": label, "gid": gid, "date": date, "contest": cid, "mode": mode,
                    "n": len(pts), "field_n": len(field), "line": line,
                    "points": float(np.mean(pts)),
                    "pctile": float(np.mean([(field < x).mean() for x in pts])),
                    "cashed": float(np.mean([x >= line for x in pts])),
                    "ret": float(np.mean([payout(cid, field, x) for x in pts]))})
    return out


def graded_gids() -> list[int]:
    B, F = boards(), fields()
    return sorted(g for g in B if g in F)


def all_rows(variants: list[str], n: int = SET_N) -> list[dict]:
    B, gids = boards(), graded_gids()
    out = []
    for v in variants:
        built = build(v, gids, n)
        for gid in gids:
            d = B[gid][0]["date"]
            for mode in MODES:
                lus = built[gid][mode]
                out += contest_rows(gid, mode, lus[:1], d, f"{v}/single")
                out += contest_rows(gid, mode, lus, d, f"{v}/set{n}")
    L = logged_lineups()
    for gid in gids:
        for mode in MODES:
            if (gid, mode) in L:
                out += contest_rows(gid, mode, [L[(gid, mode)]], B[gid][0]["date"], "as_run (logged)")
    adam = adam_entries()
    for gid in gids:
        for cid, typ, field, line in fields()[gid]:
            if cid in adam:
                x = adam[cid]
                out.append({"variant": "adam (entered)", "gid": gid, "date": B[gid][0]["date"],
                            "contest": cid, "mode": typ, "n": 1, "field_n": len(field), "line": line,
                            "points": float(x), "pctile": float((field < x).mean()),
                            "cashed": float(x >= line), "ret": payout(cid, field, x)})
    return out


def _boot(cl, d, n=2000, seed=0):
    ids, inv = np.unique(cl, return_inverse=True)
    s = np.bincount(inv, weights=d)
    c = np.bincount(inv).astype(float)
    k = np.random.default_rng(seed).integers(0, len(ids), (n, len(ids)))
    b = s[k].sum(1) / c[k].sum(1)
    return float(d.mean()), float(np.percentile(b, 5)), float(np.percentile(b, 95))


def table(rows: list[dict]) -> None:
    print("| variant | mode | contests | slates | lineups/contest | field pctile [90%, by slate] | "
          "cashed | mean pts | $ back per $1 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for v in dict.fromkeys(r["variant"] for r in rows):
        for mode in MODES:
            s = [r for r in rows if r["variant"] == v and r["mode"] == mode]
            if not s:
                continue
            m, lo, hi = _boot(np.array([r["gid"] for r in s]), np.array([r["pctile"] for r in s]))
            print(f"| {v} | {mode} | {len(s)} | {len({r['gid'] for r in s})} | "
                  f"{np.mean([r['n'] for r in s]):.0f} | {m:.3f} [{lo:.3f}, {hi:.3f}] | "
                  f"{np.mean([r['cashed'] for r in s]):.0%} | {np.mean([r['points'] for r in s]):.1f} | "
                  f"{np.mean([r['ret'] for r in s]):.2f} |")


def report(variants: list[str], n: int) -> int:
    rows = all_rows(variants, n)
    gids = sorted({r["gid"] for r in rows})
    print(f"# NCAAB lineups replayed (production sim + optimizer), scored vs the real field\n")
    print(f"{len(gids)} slates with a contest field: {', '.join(map(str, gids))}"
          + ("  -- not enough data (under 5 slates)" if len(gids) < 5 else "") + "\n")
    print("truth per lineup player: " + ", ".join(f"{k} {v}" for k, v in SOURCE_COUNT.items()) + "\n")
    table(rows)
    print("\nper slate (cash):")
    for r in rows:
        if r["mode"] == "cash":
            print(f"  {r['date']} {r['contest']} field {r['field_n']:>3} line {r['line']:6.2f}  "
                  f"{r['variant']:<18} pts {r['points']:6.1f} pctile {r['pctile']:.2f} "
                  f"cashed {r['cashed']:.0%} $ {r['ret']:.2f}")
    return 0


def parity() -> int:
    """Replayed seed-0 lineups (as_logged) vs the lineups the board logged."""
    L, gids = logged_lineups(), sorted(g for g in boards() if (g, "cash") in logged_lineups())
    built = build("as_logged", gids, n=1)
    for gid in gids:
        parts = []
        for mode in MODES:
            if (gid, mode) in L:
                got = set(built[gid][mode][0] or [])
                parts.append(f"{mode} {len(got & set(L[(gid, mode)]))}/8")
        print(f"  {gid}: " + ", ".join(parts))
    return 0


def gate(path: str, n: int = SET_N) -> int:
    """critique_gate rows: one per (mode, contest) for the shipped SET; pred = 1 - mean field
    percentile (a loss, 0 = beat everyone), actual = 0 (loss "abs"). State = mode. Every slate
    is held out (LOSO), so every row is primary. Not wired into harness/critique_gate.py yet:
    Adam approves adding it (under 5 slates the gate refuses anyway)."""
    rows = [r for r in all_rows(["shipped"], n) if r["variant"] == f"shipped/set{n}"]
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
    r.add_argument("--variant", action="append", help="extra variants (shipped, as_logged always)")
    r.add_argument("--n", type=int, default=SET_N)
    d = sub.add_parser("rows")
    d.add_argument("out")
    d.add_argument("--n", type=int, default=SET_N)
    g = sub.add_parser("gate")
    g.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "parity":
        return parity()
    if a.cmd == "gate":
        return gate(a.out)
    if a.cmd == "rows":
        Path(a.out).write_text(json.dumps(all_rows(["shipped", "as_logged"], a.n)))
        print(f"rows -> {a.out}")
        return 0
    extra = [v for v in (a.variant or []) if v not in ("shipped", "as_logged")]
    return report(["shipped", "as_logged"] + extra, a.n)


if __name__ == "__main__":
    raise SystemExit(main())
