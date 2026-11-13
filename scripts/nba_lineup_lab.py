#!/usr/bin/env python3
"""NBA lineups, held out: the PRODUCTION simulate + optimize replayed on every logged
pre-lock board, scored with box-score DK points against the real contest fields.

    python3 scripts/nba_lineup_lab.py parity                 # replayed lineups == the logged ones?
    python3 scripts/nba_lineup_lab.py report [--variant V]   # field percentile / cash line, by split
    python3 scripts/nba_lineup_lab.py field [--variant V]    # the field's lineups ranked by our proj
    python3 scripts/nba_lineup_lab.py sets [--n 20]          # 20-lineup sets per board, every date held out
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
  - a VARIANT (--variant) transforms the pool before the sim. "shipped" = today's production
    (the cycle-27 props blend on the model's projection); "model_only" = no props (pre-11/8).
Truth: DK's own points for the draft group (contests.db player_result: what the field was
scored with), else the box score (nba_salary_shrink_lab.box_lookup, alias-aware), else 0 =
did not play; `report` prints how many lineup players each source scored. Field: every exported contest on that draft group
(data/contests.db): the lineup's percentile among the field's live entries and whether it
clears the cash line (points of the last paid place). Cash lineup vs cash contests, GPP
lineup vs GPP contests, never pooled. Cluster: the slate date.
Split: TRAIN dates < 2026-10-31 <= TEST (same as nba_salary_shrink_lab.py).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

# one BLAS thread per process: the lab parallelises across processes (all cores), and
# numpy's own threads on top of 20 workers only oversubscribe
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

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
                # from cycle 27 the logged proj is the props blend: replay from the model's own
                # number (model_proj) so a variant never blends twice
                proj = float(r.get("model_proj") or r["proj"] or 0)
                sal = float(r["salary"] or 0)
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
                "recent": float(r["recent"]) if r.get("recent") not in (None, "") else None,
                "mkt": float(r["mkt"]) if r.get("mkt") not in (None, "") else None})
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
def _production(pool: list[dict]) -> None:
    """Today's production on a logged board: the props blend (edge/dfs_run_nba.blend_props,
    PROPS_BLEND) with the logged market number, else the one replayed from the scan the board
    could have seen (market_for_board), then the props-free shrink (cycle 33,
    R.shrink_props_free) on who had no prop at all on that scan. Boards are replayed from
    model_proj, so never twice."""
    from edge import dfs_run_nba as R
    mkt = {p["name"]: p["mkt"] for p in pool if p.get("mkt") is not None}
    priced: set = set()
    if pool and pool[0].get("_gid"):
        m2, priced = market_and_priced(pool[0]["_gid"], pool)
        mkt = mkt or m2
    R.blend_props(pool, mkt)
    R.shrink_props_free(pool, mkt, priced)


VARIANTS: dict = {"shipped": _production, "model_only": None}


def build(ps: list[dict], variant: str = "shipped", gid: int | None = None) -> dict:
    """{mode: [names]} for one board, production sim + optimizer."""
    pool = [dict(p, _gid=gid) for p in ps]
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
           "edge/nba_ceiling_quantiles.py", "edge/dfs_run_nba.py", "scripts/nba_lineup_lab.py")
# every core (Adam 11/9: "maximize my computer's hardware"; the desktop has 20). Work goes to
# ONE pool as small jobs -- a board for single builds, a single lineup for sets -- across every
# date and variant at once: pooling one date at a time (2-6 jobs a date) kept ~5 of 20 cores
# busy; flattened, the cycle-29 judge (4,160 seed lineups) ran in ~20 min at load ~20.
WORKERS = os.cpu_count() or 16


def _pool(fn, todo: list, workers: int = WORKERS):
    """Run fn over todo in one process pool (fork: the parent's registered VARIANTS and
    market caches are inherited), yielding results as they finish in order."""
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as ex:
        yield from ex.map(fn, todo, chunksize=1)


def _key(ps: list[dict], variant: str) -> str:
    import hashlib
    h = hashlib.sha1(variant.encode())
    for f in SOURCES:
        h.update((ROOT / f).read_bytes())
    # sets (a player's eligible slots) as sorted lists: str(set) follows PYTHONHASHSEED, so
    # every process hashed the same board differently and the cache never hit (NBA critic,
    # cycle 32, 2026-11-12: ~20 min of rebuilds on every run)
    h.update(json.dumps(ps, sort_keys=True,
                        default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o)).encode())
    return h.hexdigest()[:20]


def _build_one(args):
    gid, ps, variant = args
    return gid, build(ps, variant, gid)


def build_all(B: dict, variant: str = "shipped", workers: int = WORKERS) -> dict:
    """{gid: {mode: [names]}} for every board, cached and in parallel."""
    return build_all_many([(B, variant)], workers)[variant]


def build_all_many(jobs: list[tuple[dict, str]], workers: int = WORKERS) -> dict:
    """{variant: {gid: {mode: [names]}}} for every (boards, variant) job in ONE pool -- a
    leave-one-date-out run passes each date's boards with that date's variant."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out, todo = defaultdict(dict), []
    for B, variant in jobs:
        for gid, ps in B.items():
            f = CACHE / f"{gid}-{_key(ps, variant)}.json"
            if f.exists():
                out[variant][gid] = json.loads(f.read_text())
            else:
                todo.append((gid, ps, variant))
    # biggest boards first so one 2-minute board does not start last
    todo.sort(key=lambda t: -len(t[1]))
    for (gid, ps, variant), (_, lu) in zip(todo, _pool(_build_one, todo, workers)):
        out[variant][gid] = lu
        (CACHE / f"{gid}-{_key(ps, variant)}.json").write_text(json.dumps(lu))
    return out


_DK: dict = {}


def dk_points(gid: int) -> dict:
    """{norm name: DK's own points} for the draft group, from any exported contest on it
    (contests.db player_result): the truth the field was scored with. Critique cycle 26:
    box-score truth joined by name scored "Alexandre Sarr" (NBA.com "Alex Sarr") 0 in two
    of six test dates' lineups while the field got his real points."""
    if gid not in _DK:
        c = sqlite3.connect(SL.DB)
        _DK[gid] = {pn: f for pn, f in c.execute(
            "select pr.player_norm, max(pr.fpts) from player_result pr join contest k using(contest_id) "
            "where k.draft_group=? and pr.fpts is not null group by pr.player_norm", (gid,))}
    return _DK[gid]


SOURCE_COUNT: dict = defaultdict(int)


def player_points(gid: int, date: str, name: str) -> float:
    """DK's points for the draft group, else the box score (alias-aware), else 0 (did not play)."""
    got = dk_points(gid).get(norm(name))
    if got is not None:
        SOURCE_COUNT["dk"] += 1
        return float(got)
    got = SL.box_lookup(date, name)
    SOURCE_COUNT["box" if got is not None else "none (0)"] += 1
    return float(got or 0.0)


_FIELDS: dict = {}


def fields() -> dict:
    """{gid: [(contest id, type, sorted points desc, cash line)]}; read once per process."""
    if _FIELDS:
        return _FIELDS
    c = sqlite3.connect(SL.DB)
    out = _FIELDS
    for cid, dg, typ, paid in c.execute("select contest_id, draft_group, type, places_paid from contest "
                                        "where sport='NBA' and slate_date >= '2026-10-01' and places_paid > 0"):
        pts = np.array(sorted((p for (p,) in c.execute(
            "select points from entry where contest_id=? and points is not null", (cid,))), reverse=True))
        if len(pts):
            out.setdefault(dg, []).append((cid, typ, pts, pts[min(paid, len(pts)) - 1]))
    return out


def score(gid: int, ps: list[dict], lineup: list[str]) -> float | None:
    if len(lineup) != 8:
        return None
    d = ps[0]["date"]
    if not dk_points(gid) and not any(k[0] == d for k in SL.box_points()):
        return None                                  # no truth yet
    return float(sum(player_points(gid, d, n) for n in lineup))


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
    # a board logged before the props blend (no mkt column) was built by the model alone
    with_mkt = {g: ps for g, ps in B.items() if any(p.get("mkt") is not None for p in ps)}
    many = build_all_many([({g: ps for g, ps in B.items() if g not in with_mkt}, "model_only"),
                           (with_mkt, "shipped")])
    built = {**many.get("model_only", {}), **many.get("shipped", {})}
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
    print("truth per lineup player: " + ", ".join(f"{k} {v}" for k, v in SOURCE_COUNT.items()) + "\n")
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


# ---- DK props (critique cycle 27): the market's DK points as the board could have seen them
PROP_CUT_MIN = 25          # the last dfs_nba scan FINISHED >= 25 min before the slate's first
                           # commence time (DK locks ~10 min before the books' commence; the
                           # autolog builds ~12 min before lock): no post-lock price leaks in


def _market_code() -> str:
    """A hash of the code that turns a scan into market numbers and names: prices are history,
    the MATCHING is not. The key was gid + cut + spread until 11/13, so boards cached before a
    name-join fix kept the old join -- Jaylin Williams carried Jalen Williams's 33-point market
    on 10/20 and 10/25, looked prop-covered, and cycles 28-29's props-free rules never reached
    him (NBA critic, cycle 33)."""
    import hashlib
    import inspect
    from edge import dfs_project as DP
    from edge import dfs_run_nba as R
    from edge import dfs_slate
    h = hashlib.sha1((ROOT / "edge" / "names.py").read_bytes())
    for fn in (R.market_points, R.market_dk, R._sig, dfs_slate.player_markets_by_event,
               DP._pair, DP.implied_mean):
        h.update(inspect.getsource(fn).encode())
    h.update(repr(sorted(R.TEAM_NAME_TO_ABBR.items())).encode())
    h.update(repr((R.PROP_MARKETS, R.PROP_DK, R.PROP_REQUIRED, R.PROP_SD_K)).encode())
    return h.hexdigest()[:12]


def market_for_board(gid: int, ps: list[dict]) -> dict:
    """{name: market DK points} through production's edge.dfs_run_nba.market_points on the
    replayed scan; cached by board + the matching code (prices never change, joins do)."""
    return market_and_priced(gid, ps)[0]


def market_and_priced(gid: int, ps: list[dict]) -> tuple[dict, set]:
    """({name: market DK points}, {names with ANY of PROP_MARKETS posted}) on the scan the
    board could have seen -- the second is who R.shrink_props_free leaves alone."""
    from datetime import datetime, timedelta, timezone
    from edge import dfs_run_nba as R
    from edge.odds.source import ScrapedOddsClient
    from edge.odds.store import OddsStore
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"mkt2-{gid}-{PROP_CUT_MIN}-{_market_code()}.json"
    if f.exists():
        got = json.loads(f.read_text())
        return got["mkt"], set(got["priced"])
    store = OddsStore(ROOT / "data" / "odds.db")
    d0 = datetime.fromisoformat(ps[0]["date"] + "T14:00:00+00:00")
    scans = [(r["id"], r["finished_at"]) for r in store.conn.execute(
        "select id, finished_at from scan where profile='dfs_nba' and ok=1 and started_at >= ? and started_at <= ? "
        "order by id", (d0.isoformat(), (d0 + timedelta(hours=16)).isoformat()))]
    teams = {frozenset((p["team"], p["opp"])) for p in ps}
    out: dict = {}
    priced: set = set()
    if scans:
        # the night's FIRST scan lists tonight's games; later ones drop a game once it starts
        cl = ScrapedOddsClient(store, "dfs_nba", scan_id=scans[0][0])
        starts = [ev.get("commence_time") for ev in cl.get_events(R.SPORT)
                  if frozenset((R.TEAM_NAME_TO_ABBR.get(ev.get("home_team") or ""),
                                R.TEAM_NAME_TO_ABBR.get(ev.get("away_team") or ""))) in teams]
        if starts:
            first = min(datetime.fromisoformat(t.replace("Z", "+00:00")) for t in starts if t)
            cut = (first - timedelta(minutes=PROP_CUT_MIN)).astimezone(timezone.utc).isoformat()
            ok = [i for i, fin in scans if fin and fin <= cut]
            if ok:
                out = R.market_points(ScrapedOddsClient(store, "dfs_nba", scan_id=ok[-1]), ps, priced)
    f.write_text(json.dumps({"mkt": out, "priced": sorted(priced)}))
    return out, priced


def market_all(B: dict) -> dict:
    """{gid: {name: market}} for every board (8 processes: archived scans read from F:); the
    who-has-any-prop sets land in _PRICED."""
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=8) as ex:
        got = dict(zip(B, ex.map(market_and_priced, B, B.values())))
    _PRICED.update({g: v[1] for g, v in got.items()})
    return {g: v[0] for g, v in got.items()}


def props_rows(B: dict | None = None) -> list[dict]:
    """Player rows with a market number: date, proj, mkt, actual (DK's points, else box)."""
    B = B or {g: ps for g, ps in boards().items() if g in fields()}
    M = market_all(B)
    out = []
    for gid, ps in B.items():
        for p in ps:
            if p["name"] in M[gid]:
                out.append({"gid": gid, "date": p["date"], "proj": p["proj"], "mkt": M[gid][p["name"]],
                            "actual": player_points(gid, p["date"], p["name"])})
    return out


def props_fit(rows: list[dict]) -> tuple:
    X = np.c_[np.ones(len(rows)), [r["proj"] for r in rows], [r["mkt"] for r in rows]]
    return tuple(float(x) for x in np.linalg.lstsq(X, np.array([r["actual"] for r in rows]), rcond=None)[0])


def _props_variant(blend):
    def f(pool):
        from edge import dfs_run_nba as R
        gid = pool[0].get("_gid")
        mkt = _MKT.get(gid) or {}
        R.blend_props(pool, mkt, blend)
    return f


_MKT: dict = {}
_PRICED: dict = {}          # {gid: names with any DK prop} (market_all fills it before a fork)


def register_props(B: dict) -> dict:
    """Fit the blend on TRAIN dates and register variants 'props' (train-fitted blend) and
    'props_market' (the market alone, 0 + 0 proj + 1 mkt)."""
    M = market_all(B)
    _MKT.update(M)
    rows = props_rows(B)
    blend = props_fit([r for r in rows if r["date"] < SPLIT])
    VARIANTS["props"] = _props_variant(blend)
    VARIANTS["props_market"] = _props_variant((0.0, 0.0, 1.0))
    # the market runs low against DK points (turnovers / double-doubles are not in it, and
    # the 1.1 sqrt spread is a heuristic): one offset, fitted on TRAIN, keeps prop-covered
    # players level with the model's numbers for the uncovered ones
    tr = [r for r in rows if r["date"] < SPLIT]
    off = float(np.mean([r["actual"] - r["mkt"] for r in tr]))
    VARIANTS["props_market_off"] = _props_variant((off, 0.0, 1.0))
    return {"blend": blend, "rows": rows, "offset": off}


def props_report() -> int:
    """Cycle 27: the DK-props blend, fitted on TRAIN; player error, the field ranking, and
    leave-one-date-out (each date's blend fitted on every other date) for the thin cash set."""
    B = {g: ps for g, ps in boards().items() if g in fields()}
    reg = register_props(B)
    a, b, c = reg["blend"]
    rows = reg["rows"]
    print(f"# DK props blend (cycle 27): proj' = {a:+.3f} + {b:.3f} proj + {c:.3f} mkt  "
          f"(fit on {sum(r['date'] < SPLIT for r in rows)} train player rows)\n")
    te = [r for r in rows if r["date"] >= SPLIT]
    A = np.array([r["actual"] for r in te]); P = np.array([r["proj"] for r in te])
    M = np.array([r["mkt"] for r in te]); BL = np.maximum(0.1, a + b * P + c * M)
    cl = np.array([r["date"] for r in te])
    print(f"player level, test (n {len(te)}, {len(set(cl))} dates, prop-covered players):")
    for name, pr in (("model", P), ("market", M), ("blend", BL)):
        m, lo, hi = _boot(cl, np.abs(A - pr) - np.abs(A - P))
        print(f"  {name:7} MAE {np.abs(A - pr).mean():.3f}  bias {(A - pr).mean():+.2f}  "
              f"diff vs model {m:+.3f} [{lo:+.3f}, {hi:+.3f}]")
    print()
    print(f"market offset (train mean actual - mkt): {reg['offset']:+.3f}\n")
    field_report(["model_only", "shipped", "props", "props_market", "props_market_off"])
    # leave-one-date-out on the field ranking
    dates = sorted({ps[0]["date"] for ps in B.values()})
    lodo = {}
    for d in dates:
        bl = props_fit([r for r in rows if r["date"] != d])
        VARIANTS["_lodo"] = _props_variant(bl)
        Bd = {g: ps for g, ps in B.items() if ps[0]["date"] == d}
        lodo.update({(r["mode"], r["contest"]): r for r in field_rank("_lodo", Bd)})
    base = {(r["mode"], r["contest"]): r for r in field_rank("model_only", B)}
    print("\nleave-one-date-out, every date (blend refit without that date):\n")
    print("| mode | contests | dates | top-10% shipped -> props | diff [90%, by date] | Spearman shipped -> props | diff [90%] | dates better (rho) |")
    print("|---|---|---|---|---|---|---|---|")
    for mode in ("cash", "gpp"):
        ks = [k for k in lodo if k[0] == mode]
        cl = np.array([lodo[k]["date"] for k in ks])
        d10 = np.array([lodo[k]["top10"] - base[k]["top10"] for k in ks])
        drho = np.array([lodo[k]["rho"] - base[k]["rho"] for k in ks])
        m, lo, hi = _boot(cl, d10); m2, lo2, hi2 = _boot(cl, drho)
        by = defaultdict(list)
        for k, x in zip(ks, drho):
            by[lodo[k]["date"]].append(x)
        print(f"| {mode} | {len(ks)} | {len(set(cl))} | {np.mean([base[k]['top10'] for k in ks]):.3f} -> "
              f"{np.mean([lodo[k]['top10'] for k in ks]):.3f} | {m:+.3f} [{lo:+.3f}, {hi:+.3f}] | "
              f"{np.mean([base[k]['rho'] for k in ks]):.3f} -> {np.mean([lodo[k]['rho'] for k in ks]):.3f} | "
              f"{m2:+.3f} [{lo2:+.3f}, {hi2:+.3f}] | {sum(np.mean(v) > 0 for v in by.values())} of {len(by)} |")
    return 0


def props_lodo_lineups() -> int:
    """The honest lineup test for the train-fitted blend: for every date, refit the blend
    WITHOUT that date and replay that date's boards with it -- every cash contest held out."""
    B = {g: ps for g, ps in boards().items() if g in fields()}
    reg = register_props(B)
    rows, F = reg["rows"], fields()
    jobs = [(B, "model_only")]
    for d in sorted({ps[0]["date"] for ps in B.values()}):
        bl = props_fit([r for r in rows if r["date"] != d])
        name = "props[" + ",".join(f"{x:.4f}" for x in bl) + "]"
        VARIANTS[name] = _props_variant(bl)
        jobs.append(({g: ps for g, ps in B.items() if ps[0]["date"] == d}, name))
    built = build_all_many(jobs)
    base_lu = built["model_only"]
    lodo_lu = {g: lu for _, name in jobs[1:] for g, lu in built[name].items()}
    out = defaultdict(list)
    for gid, ps in B.items():
        for mode in ("cash", "gpp"):
            a_, b_ = score(gid, ps, base_lu[gid][mode]), score(gid, ps, lodo_lu[gid][mode])
            for cid, typ, field, line in F[gid]:
                if typ == mode and a_ is not None and b_ is not None:
                    out[mode].append({"date": ps[0]["date"], "a": float((field < a_).mean()), "b": float((field < b_).mean()),
                                      "ca": a_ >= line, "cb": b_ >= line, "pa": a_, "pb": b_})
    print("# Optimizer replay, props blend refit without each date (every contest held out)\n")
    print("| mode | contests | dates | field pctile model only -> props | diff [90%, by date] | cashed | points | dates better |")
    print("|---|---|---|---|---|---|---|---|")
    for mode, rs in out.items():
        cl = np.array([r["date"] for r in rs]); d = np.array([r["b"] - r["a"] for r in rs])
        m, lo, hi = _boot(cl, d)
        by = defaultdict(list)
        for r in rs:
            by[r["date"]].append(r["b"] - r["a"])
        print(f"| {mode} | {len(rs)} | {len(set(cl))} | {np.mean([r['a'] for r in rs]):.3f} -> {np.mean([r['b'] for r in rs]):.3f} | "
              f"{m:+.3f} [{lo:+.3f}, {hi:+.3f}] | {np.mean([r['ca'] for r in rs]):.0%} -> {np.mean([r['cb'] for r in rs]):.0%} | "
              f"{np.mean([r['pa'] for r in rs]):.1f} -> {np.mean([r['pb'] for r in rs]):.1f} | "
              f"{sum(np.mean(v) > 0 for v in by.values())} better, {sum(np.mean(v) < 0 for v in by.values())} worse of {len(by)} |")
    return 0


# ---- lineup SETS (Adam 2026-11-08, cycle 28's runner-up): one seeded lineup a board gives a
# date-level SE of ~.08-.15 in field percentile, so 16 dates could not confirm a +4-point
# change. Each board now gets SET_N lineups per mode through production's multi-entry builder
# (edge/dfs_opt_climb.portfolio: each new lineup bans the last one's top projection, at most
# 5 players shared): the mean over the set averages away which players happened to boom, and
# variants are compared on the same boards (paired). One lineup per pool job, every date and variant in one pool (build_sets_many).
SET_N = 20
SET_ITERS = 60                     # edge/dfs_opt_nba.portfolio's production default


SET_KIND = "seeds"
# Seed sets judged together (cycle 29 judge, 11/9): ONE 20-seed set read the same rule -.009 on
# seeds 0-19 and +.024 on seeds 1000-1019 (33 GPP contests / 16 dates); pooled 40 a board it was
# +.008 [-.016, +.031]. A lineup cycle is judged on both, pooled per contest.
SET_SEEDS = (0, 1000)


def build_set(ps: list[dict], variant: str, gid: int, mode: str, n: int = SET_N,
              kind: str = SET_KIND) -> list[list[str]]:
    """`kind` "seeds" (default): n INDEPENDENT best lineups -- sim seed k, climb seed k, no
    exclusions -- each one a lineup the single-entry build could have given; their mean is
    the expected result of our one entry with the optimizer's own luck averaged out.
    "portfolio": production's multi-entry builder (NHL / NCAAB / MMA pages). It bans each
    lineup's top projection cumulatively, so lineup 20 is built without the 19 best-projected
    players: on NBA (11/8, 33 GPP contests / 16 dates) the set averaged the 29th field
    percentile and 200 pts vs the 60th and ~260 for the single best lineup."""
    from functools import partial
    from edge import dfs_opt_climb as climb
    pool = _set_pool(ps, variant, gid)
    if kind == "portfolio":
        sim = theory.simulate(pool, n_sims=N_SIMS, seed=0)
        theory.summarise(sim, pool)
        res = climb.portfolio(partial(dfs_opt_nba.optimize, pool, sim, mode, SET_ITERS), n, max_overlap=5, seed=0)
        return [[p["name"] for p in r["lineup"]] for r in res]
    return [lu for lu in (seed_lineup(pool, mode, k) for k in range(n)) if lu]


def _set_pool(ps: list[dict], variant: str, gid: int) -> list[dict]:
    pool = [dict(p, _gid=gid) for p in ps]
    if VARIANTS.get(variant):
        VARIANTS[variant](pool)
    pool.sort(key=lambda p: (str(p.get("game") or ""), str(p.get("team") or ""), p["name"]))
    return pool


def seed_lineup(pool: list[dict], mode: str, seed: int, spec: dict | None = None) -> list[str] | None:
    """One seeded best lineup: sim seed k, climb seed k (a set's k-th lineup). `spec` (a
    CONSTRUCT entry) swaps production's optimize for climb.search with that construction."""
    sim = theory.simulate(pool, n_sims=N_SIMS, seed=seed)
    theory.summarise(sim, pool)
    if not spec:
        r = dfs_opt_nba.optimize(pool, sim, mode=mode, iters=SET_ITERS, seed=seed)
        return [p["name"] for _, p in r["slots"]] if r else None
    from edge import dfs_opt_climb as climb
    dfs_opt_nba.prepare(pool)
    pct = spec.get("pct") or (theory.GPP_PCT if mode == "gpp" else theory.CASH_PCT)
    excl = {p["name"] for p in pool if spec.get("minmin") and (p.get("minutes") or 0) < spec["minmin"]}
    best = climb.search(pool, sim, dfs_opt_nba.NBA, pct, key="ceil" if mode == "gpp" else "floor",
                        iters=SET_ITERS, seed=seed, own_weight=spec.get("own_weight", 0.0),
                        exclude=excl or None, extra_valid=_valid(spec))
    if best is None:
        return None
    r = climb.result(pool, sim, best, dfs_opt_nba.NBA, theory.describe)
    return [p["name"] for _, p in r["slots"]] if r else None


def _valid(spec: dict):
    cap, stack = spec.get("cap"), spec.get("stack")
    if not cap and not stack:
        return None

    def ok(rows):
        if cap and max(Counter(r["team"] for r in rows).values()) > cap:
            return False
        return not stack or max(Counter(r["game"] for r in rows).values()) >= stack
    return ok


def _set_job(args):
    """One pool job: a single seed lineup (kind seeds) or a whole portfolio set."""
    gid, ps, variant, mode, n, seed, kind = args
    if kind == "portfolio":
        return build_set(ps, variant, gid, mode, n, kind)
    return seed_lineup(_set_pool(ps, variant, gid), mode, seed, CONSTRUCT.get(variant))


#: {variant name: construction spec} -- {"modes", "pct", "own_weight", "cap", "stack", "minmin"};
#: see `construct` (props-era rerun #3). A spec'd variant builds only its modes.
CONSTRUCT: dict = {}


def _wanted(gid: int, variant: str, mode: str) -> bool:
    """Build a mode only where it is scored (a contest of that type on the board) and,
    for a construction variant, only the modes it changes."""
    spec = CONSTRUCT.get(variant)
    if spec and mode not in spec["modes"]:
        return False
    return any(t == mode for _, t, _, _ in fields().get(gid, []))


def _set_file(gid: int, ps: list[dict], variant: str, mode: str, n: int, seed0: int, kind: str) -> Path:
    # seed set 0 keeps its pre-11/9 name, so existing caches stay warm
    return CACHE / (f"set{kind}{n}-{mode}-{gid}-{_key(ps, variant)}"
                    + (f"-s{seed0}" if seed0 else "") + ".json")


def build_sets(B: dict, variant: str, n: int = SET_N, workers: int = WORKERS) -> dict:
    """{gid: {mode: [lineups]}}, cached per (board, variant, mode, n) like build_all."""
    return build_sets_many([(B, variant)], n, (0,), workers)[(variant, 0)]


def build_sets_many(jobs: list[tuple[dict, str]], n: int = SET_N, seeds: tuple = (0,),
                    workers: int = WORKERS, kind: str | None = None) -> dict:
    """{(variant, seed0): {gid: {mode: [lineups]}}} for every (boards, variant) job and seed
    set (seeds seed0 .. seed0+n-1), all in ONE pool of single-lineup jobs (kind seeds; a
    portfolio is sequential, so it stays one job per set and seed set 0 only)."""
    kind = kind or SET_KIND
    seeds = (0,) if kind == "portfolio" else tuple(seeds)
    CACHE.mkdir(parents=True, exist_ok=True)
    out, todo, sets_todo = defaultdict(lambda: defaultdict(dict)), [], []
    for B, variant in jobs:
        for s0 in seeds:
            for gid, ps in B.items():
                for mode in ("cash", "gpp"):
                    if not _wanted(gid, variant, mode):
                        continue
                    f = _set_file(gid, ps, variant, mode, n, s0, kind)
                    if f.exists():
                        out[(variant, s0)][gid][mode] = json.loads(f.read_text())
                        continue
                    sets_todo.append((variant, s0, gid, ps, mode, f))
                    if kind == "portfolio":
                        todo.append((gid, ps, variant, mode, n, 0, kind))
                    else:
                        todo += [(gid, ps, variant, mode, n, s0 + k, kind) for k in range(n)]
    if todo:
        print(f"lineup lab: {len(todo):,} {'lineups' if kind == 'seeds' else 'sets'} to build on "
              f"{min(workers, len(todo))} processes", file=sys.stderr, flush=True)
    todo.sort(key=lambda t: -len(t[1]))           # biggest boards first
    got = {(t[0], t[2], t[3], t[5]): r for t, r in zip(todo, _pool(_set_job, todo, workers))}
    for variant, s0, gid, ps, mode, f in sets_todo:
        if kind == "portfolio":
            lus = got[(gid, variant, mode, 0)]
        else:
            lus = [lu for lu in (got[(gid, variant, mode, s0 + k)] for k in range(n)) if lu]
        out[(variant, s0)][gid][mode] = lus
        f.write_text(json.dumps(lus))
    return {k: dict(v) for k, v in out.items()}


_PAY: dict = {}


def payout(cid: int, field: np.ndarray, x: float) -> float:
    """$ returned per $1 entry for a lineup scoring x in the contest (its rank among the real
    field's points; contests.db payout table). 0 when the payout table is missing."""
    if not _PAY:
        c = sqlite3.connect(SL.DB)
        fee = dict(c.execute("select contest_id, entry_fee from contest where sport='NBA'"))
        for k, lo, hi, amt in c.execute("select contest_id, rank_lo, rank_hi, amount from payout"):
            _PAY.setdefault(k, {"fee": fee.get(k) or 0, "tiers": []})["tiers"].append((lo, hi, amt))
    pay = _PAY.get(cid)
    if not pay or not pay["fee"]:
        return 0.0
    rank = 1 + int((field > x).sum())
    return next((amt for lo, hi, amt in pay["tiers"] if lo <= rank <= hi), 0.0) / pay["fee"]


def set_rows(built: dict, B: dict) -> dict:
    """{(mode, contest): {date, pctile, cashed, points, ret, n}}: the set's means per contest."""
    F = fields()
    out = {}
    for gid, ps in B.items():
        for mode in ("cash", "gpp"):
            pts = [x for x in (score(gid, ps, lu) for lu in built.get(gid, {}).get(mode, [])) if x is not None]
            if not pts:
                continue
            for cid, typ, field, line in F.get(gid, []):
                if typ == mode:
                    out[(mode, cid)] = {"date": ps[0]["date"], "n": len(pts),
                                        "pctile": float(np.mean([(field < x).mean() for x in pts])),
                                        "cashed": float(np.mean([x >= line for x in pts])),
                                        "points": float(np.mean(pts)),
                                        "ret": float(np.mean([payout(cid, field, x) for x in pts]))}
    return out


def _lodo_names(B: dict, rows: list[dict], free_rows: list[dict] | None,
                shrink: list[dict] | None = None) -> dict:
    """{date: variant name} -- the props blend (and the cycle-28 props-free calibration when
    free_rows is given, the cycle-33 props-free shrink when `shrink` rows are) refit WITHOUT
    that date, registered in VARIANTS. Production = blend + shrink."""
    names = {}
    for d in sorted({ps[0]["date"] for ps in B.values()}):
        bl = props_fit([r for r in rows if r["date"] != d])
        fr = sh = None
        if free_rows is not None:
            fr = _free_fit([r for r in free_rows if r["date"] != d])
        if shrink is not None:
            sh = _free_fit([r for r in shrink if r["date"] != d])
        name = "props[" + ",".join(f"{x:.4f}" for x in bl) + "]" + \
               ("" if fr is None else "free[" + ",".join(f"{x:.4f}" for x in fr) + "]") + \
               ("" if sh is None else "shrink[" + ",".join(f"{x:.4f}" for x in sh) + "]")
        VARIANTS[name] = _props_free_variant(bl, fr, sh)
        names[d] = name
    return names


def shrink_rows(B: dict) -> list[dict]:
    """Rows R.shrink_props_free would touch (no prop at all, game priced, model >= the floor):
    model proj, salary, actual -- what PROPS_FREE_SHRINK is fitted on (cycle 33)."""
    from edge import dfs_run_nba as R
    M = market_all(B)
    out = []
    for gid, ps in B.items():
        pool = [dict(p) for p in ps]
        R.shrink_props_free(pool, M[gid], _PRICED[gid], (0.0, 1.0, 0.0))      # identity: flags only
        for p in pool:
            if p.get("proj_source") == "no props: shrunk":
                out.append({"date": p["date"], "proj": p["model_proj"], "sal": p["salary"] / 1000,
                            "actual": player_points(gid, p["date"], p["name"])})
    return out


def free_rows(B: dict) -> list[dict]:
    """Props-free board rows (no market number on the replayed scan): model proj, salary,
    actual -- cycle 28's critic: they ran 3-4 pts high and the optimizer leaned on them."""
    M = market_all(B)
    out = []
    for gid, ps in B.items():
        for p in ps:
            if p["name"] not in M[gid]:
                out.append({"date": p["date"], "proj": p["proj"], "sal": p["salary"] / 1000,
                            "actual": player_points(gid, p["date"], p["name"])})
    return out


def _free_fit(rows: list[dict]) -> tuple:
    X = np.c_[np.ones(len(rows)), [r["proj"] for r in rows], [r["sal"] for r in rows]]
    return tuple(float(x) for x in np.linalg.lstsq(X, np.array([r["actual"] for r in rows]), rcond=None)[0])


def _props_free_variant(blend, free, shrink=None):
    def f(pool):
        from edge import dfs_run_nba as R
        gid = pool[0].get("_gid")
        mkt = _MKT.get(gid) or {}
        R.blend_props(pool, mkt, blend)
        R.shrink_props_free(pool, mkt, _PRICED.get(gid) or set(), shrink if shrink else ())
        if free:
            a, b, c = free
            for p in pool:
                if p["name"] not in mkt and p.get("proj"):
                    p["proj"] = round(max(0.1, a + b * p["proj"] + c * p["salary"] / 1000), 2)
    return f


def pool_rows(per_seed: list[dict]) -> dict:
    """Per-contest set rows from several seed sets pooled: the mean over every lineup."""
    out = {}
    for k in per_seed[0]:
        rs = [r[k] for r in per_seed if k in r]
        n = sum(r["n"] for r in rs)
        out[k] = {"date": rs[0]["date"], "n": n,
                  **{f: sum(r[f] * r["n"] for r in rs) / n for f in ("pctile", "cashed", "points", "ret")}}
    return out


def sets_lodo(with_free: bool = True, n: int = SET_N, seeds: tuple = SET_SEEDS) -> int:
    """Lineup SETS, every date held out: production (the props blend + the cycle-33 props-free
    shrink, each refit without the date) vs the blend alone (production before 11/13). Paired
    by contest, bootstrap by date."""
    B = {g: ps for g, ps in boards().items() if g in fields()}
    reg = register_props(B)
    rows = reg["rows"]
    base_names = _lodo_names(B, rows, None, shrink_rows(B))
    cand_names = _lodo_names(B, rows, None) if with_free else {}
    labelled = [(lab, nm) for lab, nm in (("production (blend + props-free shrink)", base_names),
                                          ("blend only (before cycle 33)", cand_names)) if nm]
    return compare_sets(B, labelled, n, seeds)


def compare_sets(B: dict, labelled: list[tuple[str, dict]], n: int = SET_N, seeds: tuple = SET_SEEDS,
                 title: str = "") -> int:
    """Build + score seed sets for labelled variants -- each {date: variant name}, the first
    is the baseline -- in one all-core pool; print the paired table (by date) incl. $ per $1."""
    by_date = lambda d: {g: ps for g, ps in B.items() if ps[0]["date"] == d}    # noqa: E731
    built = build_sets_many([(by_date(d), name) for _, nm in labelled for d, name in nm.items()], n, seeds)
    seeds = sorted({s for _, s in built})
    res = {}
    for label, nm in labelled:
        res[label] = pool_rows([set_rows({g: lu for name in nm.values() for g, lu in built.get((name, s), {}).items()}, B)
                                for s in seeds])
    labels = list(res)
    base = res[labels[0]]
    print(f"# {title or 'Lineup sets'} ({n} per board per mode x seed sets {', '.join(map(str, seeds))}, "
          f"kind={SET_KIND}), every date held out\n")
    print("| variant | split | mode | contests | dates | lineups | field pctile | cashed | points | $ per $1 | "
          f"pctile diff vs {labels[0]} [90%, by date] | dates better / worse | $ diff [90%] |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lab in labels:
        for split, keep in (("all", lambda d: True), ("test", lambda d: d >= SPLIT)):
            for mode in ("cash", "gpp"):
                ks = [k for k in res[lab] if k[0] == mode and k in base and keep(res[lab][k]["date"])]
                if not ks:
                    continue
                cl = np.array([res[lab][k]["date"] for k in ks])
                d = np.array([res[lab][k]["pctile"] - base[k]["pctile"] for k in ks])
                m, lo, hi = _boot(cl, d)
                mr, lor, hir = _boot(cl, np.array([res[lab][k]["ret"] - base[k]["ret"] for k in ks]))
                by = defaultdict(list)
                for k, x in zip(ks, d):
                    by[res[lab][k]["date"]].append(x)
                mean = lambda f: np.mean([res[lab][k][f] for k in ks])    # noqa: E731
                print(f"| {lab} | {split} | {mode} | {len(ks)} | {len(set(cl))} | {sum(res[lab][k]['n'] for k in ks):,} | "
                      f"{mean('pctile'):.3f} | {mean('cashed'):.0%} | {mean('points'):.1f} | {mean('ret'):.2f} | "
                      f"{m:+.3f} [{lo:+.3f}, {hi:+.3f}] | {sum(np.mean(v) > 1e-9 for v in by.values())} / "
                      f"{sum(np.mean(v) < -1e-9 for v in by.values())} | {mr:+.2f} [{lor:+.2f}, {hir:+.2f}] |")
    # how much the set tightens the date-level spread of the field percentile
    for mode in ("cash", "gpp"):
        ks = [k for k in base if k[0] == mode]
        dm = defaultdict(list)
        for k in ks:
            dm[base[k]["date"]].append(base[k]["pctile"])
        v = [np.mean(x) for x in dm.values()]
        print(f"\n{mode}: sd of the date-level field percentile across {len(v)} dates = {np.std(v, ddof=1):.3f} "
              f"(SE of the mean {np.std(v, ddof=1) / np.sqrt(len(v)):.3f})")
    return 0


# ---- sim spread on the props-blended projection (rerun #1 after the props blend, 11/9) ----
# The sim's spread (theory.sd_for x the bucket's error-shape table, edge/nba_ceiling_quantiles)
# was fitted on 2024-25 MODEL-ONLY errors. `spread` measures it against the blended projection's
# real misses and replays lineups with it rescaled, every ratio refit without the held-out date.
SPREAD_BUCKETS = (10, 20, 30, 45)                # lower edges; < 10 (n 17 prop rows) keeps 1.0


def _bucket_zsd(mu: float) -> float:
    from edge import nba_ceiling_quantiles as CQ
    b = max(0, min(len(CQ.BINS) - 2, int(np.searchsorted(CQ.BINS, mu, side="right")) - 1))
    return float(np.std(np.interp(np.linspace(0.0005, 0.9995, 2000), CQ.QGRID, CQ.ZQ[b])))


def spread_ratios(rows: list[dict], blend: tuple) -> dict:
    """{bucket lower edge: rms(actual - blended proj) / rms(sim sd)} on prop-covered rows."""
    a, b, c = blend
    acc = defaultdict(lambda: [0.0, 0.0, 0])
    for r in rows:
        mu = max(0.1, a + b * r["proj"] + c * r["mkt"])
        lo = max([x for x in SPREAD_BUCKETS if mu >= x], default=None)
        if lo is None:
            continue
        acc[lo][0] += (r["actual"] - mu) ** 2
        acc[lo][1] += (theory.sd_for(mu) * _bucket_zsd(mu)) ** 2
        acc[lo][2] += 1
    return {lo: float(np.sqrt(e / v)) for lo, (e, v, n) in acc.items() if n >= 20}


def _spread_variant(blend, scales: dict | None, everyone: float | None = None):
    def f(pool):
        from edge import dfs_run_nba as R
        gid = pool[0].get("_gid")
        mkt = _MKT.get(gid) or {}
        R.blend_props(pool, mkt, blend)
        # production since 11/13 (cycle 33), at its all-dates constants; `sets` refits them per date
        R.shrink_props_free(pool, mkt, _PRICED.get(gid) or set())
        for p in pool:
            if everyone is not None:
                p["sd_scale"] = everyone
            elif scales and p["name"] in mkt and p.get("proj"):
                lo = max([x for x in SPREAD_BUCKETS if p["proj"] >= x], default=None)
                if lo in scales:
                    p["sd_scale"] = round(scales[lo], 4)
    return f


def spread(n: int = SET_N, seeds: tuple = SET_SEEDS) -> int:
    B = {g: ps for g, ps in boards().items() if g in fields()}
    rows = register_props(B)["rows"]
    base = _lodo_names(B, rows, None)                       # production, blend refit without the date
    v45, vall, v08, fitted = {}, {}, {}, {}
    for d in base:
        tr = [r for r in rows if r["date"] != d]
        bl = props_fit(tr)
        rat = spread_ratios(tr, bl)
        fitted[d] = rat
        for out, scales, ev, tag in ((v45, {45: rat[45]} if 45 in rat else {}, None, "sd45"),
                                     (vall, rat, None, "sdall"), (v08, None, 0.8, "sd08")):
            name = f"{base[d]}{tag}[" + ",".join(f"{k}:{v:.3f}" for k, v in sorted((scales or {}).items())) + \
                   (f"{ev}" if ev else "") + "]"
            VARIANTS[name] = _spread_variant(bl, scales, ev)
            out[d] = name
    print("spread ratios refit without each date (bucket: min..max over folds; all dates):")
    allr = spread_ratios(rows, props_fit(rows))
    for lo in SPREAD_BUCKETS:
        v = [f[lo] for f in fitted.values() if lo in f]
        print(f"  {lo}+: {min(v):.3f}..{max(v):.3f}; all {allr.get(lo, float('nan')):.3f}")
    print()
    return compare_sets(B, [("production", base), ("prop players 45+ spread refit", v45),
                            ("prop players spread refit by bucket", vall), ("everyone x0.8 (cycle 28)", v08)],
                        n, seeds, title="Sim spread on the props blend")


# ---- lineup construction on the props blend (props-era rerun #3, 11/9) ----
# The 10/30 overnight study (scripts/nba_construction_backtest.py) and cycle 26 found nothing
# that beat shipped construction while our projection ranked lineups near chance (field top-5%
# at the 43-55th percentile). With the props blend shipped they are re-run on 40 seed lineups
# a board, the blend refit without each date. Each spec builds only the mode it changes.
# Fades/tilts use ownership recomputed on the blended projection (production's order since
# cycle 27; NBA_OWNERSHIP_CRITIQUE_LOG cycle 3: MAE 4.17 vs 4.72 on the model number) --
# without buzz, which the logged boards do not carry (weight 0.19).
CONSTRUCTIONS = {
    "cash: team cap 3": {"modes": {"cash"}, "cap": 3},
    "cash: team cap 4": {"modes": {"cash"}, "cap": 4},
    "cash: 50th pct": {"modes": {"cash"}, "pct": 50},
    "cash: 24+ min only": {"modes": {"cash"}, "minmin": 24},
    "gpp: fade own x5": {"modes": {"gpp"}, "own_weight": 5.0},
    "gpp: fade own x15": {"modes": {"gpp"}, "own_weight": 15.0},
    "gpp: chalk own x5": {"modes": {"gpp"}, "own_weight": -5.0},
    "gpp: 99th pct": {"modes": {"gpp"}, "pct": 99},
    "gpp: 4+ one game": {"modes": {"gpp"}, "stack": 4},
    "gpp: team cap 3": {"modes": {"gpp"}, "cap": 3},
}


def _construct_variant(blend, own: bool):
    def f(pool):
        from edge import dfs_run_nba as R
        gid = pool[0].get("_gid")
        mkt = _MKT.get(gid) or {}
        R.blend_props(pool, mkt, blend)
        # production since 11/13 (cycle 33), at its all-dates constants; `sets` refits them per date
        R.shrink_props_free(pool, mkt, _PRICED.get(gid) or set())
        if own:
            theory.add_ownership(pool)
    return f


def construct(n: int = SET_N, seeds: tuple = SET_SEEDS, only: list[str] | None = None) -> int:
    B = {g: ps for g, ps in boards().items() if g in fields()}
    rows = register_props(B)["rows"]
    base = _lodo_names(B, rows, None)
    labelled = [("production", base)]
    for label, spec in CONSTRUCTIONS.items():
        if only and not any(o in label for o in only):
            continue
        nm = {}
        for d in base:
            bl = props_fit([r for r in rows if r["date"] != d])
            name = f"{base[d]}|{label}"
            VARIANTS[name] = _construct_variant(bl, bool(spec.get("own_weight")))
            CONSTRUCT[name] = spec
            nm[d] = name
        labelled.append((label, nm))
    return compare_sets(B, labelled, n, seeds, title="Lineup construction on the props blend")


def field_rank(variant: str = "shipped", B: dict | None = None) -> list[dict]:
    """The lower-variance companion (critic, cycle 26): per exported contest, rank the REAL
    field's lineups by the variant's projection (sum over the 8 players; an entry with a
    player off our board is skipped) and score that ranking against the points the entries
    actually got: the mean field percentile of the top 5% by our ranking, and the Spearman
    rank correlation. Thousands of lineups a slate instead of our one; no optimizer run.
    It measures the projection's ordering of LINEUPS, where the optimizer works."""
    from scipy.stats import spearmanr
    F = fields()
    B = B if B is not None else boards()
    c = sqlite3.connect(SL.DB)
    out = []
    for gid, ps in B.items():
        if gid not in F:
            continue
        pool = [dict(p, _gid=gid) for p in ps]
        if VARIANTS.get(variant):
            VARIANTS[variant](pool)
        proj = {norm(p["name"]): p["proj"] for p in pool}
        for cid, typ, _field, _line in F[gid]:
            ent = defaultdict(list)
            pts = {}
            for eid, pn, points in c.execute(
                    "select ep.entry_id, ep.player_norm, e.points from entry_player ep join entry e "
                    "on e.contest_id=ep.contest_id and e.entry_id=ep.entry_id where ep.contest_id=? "
                    "and e.points is not null", (cid,)):
                ent[eid].append(pn)
                pts[eid] = points
            # a player off our board counts 0 -- what the board says, and the optimizer could
            # not pick him (cycle 27: skipping such entries dropped 891 of 1,149 lineups in
            # 10/26's cash contest, the night Jaylin Williams was 77% owned and off our board)
            ids = [e for e, pl in ent.items() if len(pl) == 8]
            if len(ids) < 20:
                continue
            pj = np.array([sum(proj.get(x, 0.0) for x in ent[e]) for e in ids])
            act = np.array([pts[e] for e in ids])
            pct = (np.argsort(np.argsort(act)) / (len(act) - 1))
            # ties in projection (duplicated lineups) share the cut: rank with a stable jitter-free
            # quantile; top 10% as well (a 23-entry cash field's top 5% is one lineup)
            top5, top10 = pj >= np.quantile(pj, 0.95), pj >= np.quantile(pj, 0.90)
            out.append({"contest": cid, "gid": gid, "date": ps[0]["date"], "mode": typ, "n": len(ids),
                        "top5": float(pct[top5].mean()), "top10": float(pct[top10].mean()),
                        "rho": float(spearmanr(pj, act)[0]),
                        "off_board": int(sum(any(x not in proj for x in ent[e]) for e in ids)),
                        "test": ps[0]["date"] >= SPLIT})
    return out


def field_report(variants: list[str]) -> int:
    res = {v: {(r["mode"], r["contest"]): r for r in field_rank(v)} for v in variants}
    base = res[variants[0]]
    print("# The field's real lineups ranked by each variant's projection (per contest)\n")
    print(f"| variant | split | mode | contests | dates | lineups | top-5% pctile | top-10% pctile | Spearman | "
          f"top-10% diff vs {variants[0]} [90%, by date] | Spearman diff [90%] |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for v, rs in res.items():
        for split in ("train", "test"):
            for mode in ("cash", "gpp"):
                s_ = [r for r in rs.values() if r["mode"] == mode and r["test"] == (split == "test")]
                if not s_:
                    continue
                cl = np.array([r["date"] for r in s_])
                m, lo, hi = _boot(cl, np.array([r["top10"] - base[(r["mode"], r["contest"])]["top10"] for r in s_]))
                m2, lo2, hi2 = _boot(cl, np.array([r["rho"] - base[(r["mode"], r["contest"])]["rho"] for r in s_]))
                print(f"| {v} | {split} | {mode} | {len(s_)} | {len(set(cl))} | "
                      f"{sum(r['n'] for r in s_):,} | {np.mean([r['top5'] for r in s_]):.3f} | "
                      f"{np.mean([r['top10'] for r in s_]):.3f} | {np.mean([r['rho'] for r in s_]):.3f} | "
                      f"{m:+.3f} [{lo:+.3f}, {hi:+.3f}] | {m2:+.3f} [{lo2:+.3f}, {hi2:+.3f}] |")
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
    sub.add_parser("props", help="cycle 27: the DK-props blend, train fit, test + leave-one-date-out")
    sub.add_parser("props-lineups", help="cycle 27: the optimizer replay with each props variant")
    sub.add_parser("props-lodo", help="cycle 27: optimizer replay, blend refit without each date")
    st = sub.add_parser("sets", help="20-lineup sets per board, every date held out (production vs the blend alone)")
    st.add_argument("--n", type=int, default=SET_N)
    st.add_argument("--base-only", action="store_true", help="production only (the baseline)")
    st.add_argument("--seeds", default=",".join(map(str, SET_SEEDS)),
                    help="first seed of each seed set, pooled (default %(default)s; '0' = one set)")
    st.add_argument("--kind", choices=["seeds", "portfolio"], default=SET_KIND,
                    help="seeds: n independent best lineups (single-entry); portfolio: production multi-entry")
    sp = sub.add_parser("spread", help="sim spread rescaled to the props-blended projection's misses")
    sp.add_argument("--seeds", default=",".join(map(str, SET_SEEDS)))
    cn = sub.add_parser("construct", help="lineup construction variants (caps, percentiles, fades, stacks)")
    cn.add_argument("--seeds", default=",".join(map(str, SET_SEEDS)))
    cn.add_argument("--only", action="append", help="substring of a construction label (repeatable)")
    fr = sub.add_parser("field", help="the field's lineups ranked by each variant (no optimizer)")
    fr.add_argument("--variant", action="append")
    r = sub.add_parser("report")
    r.add_argument("--variant", action="append", help="variants to compare (shipped is always first)")
    g = sub.add_parser("gate")
    g.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "parity":
        return parity()
    if a.cmd == "props-lodo":
        return props_lodo_lineups()
    if a.cmd == "sets":
        globals()["SET_KIND"] = a.kind
        return sets_lodo(with_free=not a.base_only, n=a.n, seeds=tuple(int(x) for x in a.seeds.split(",")))
    if a.cmd == "construct":
        return construct(seeds=tuple(int(x) for x in a.seeds.split(",")), only=a.only)
    if a.cmd == "spread":
        return spread(seeds=tuple(int(x) for x in a.seeds.split(",")))
    if a.cmd == "props-lineups":
        register_props({g: ps for g, ps in boards().items() if g in fields()})
        return report(["model_only", "shipped", "props", "props_market", "props_market_off"])
    if a.cmd == "gate":
        return gate(a.out)
    if a.cmd == "props":
        return props_report()
    if a.cmd == "field":
        return field_report(["shipped"] + [v for v in (a.variant or []) if v != "shipped"])
    return report(["shipped"] + [v for v in (a.variant or []) if v != "shipped"])


if __name__ == "__main__":
    raise SystemExit(main())
