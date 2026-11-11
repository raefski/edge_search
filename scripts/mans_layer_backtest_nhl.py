#!/usr/bin/env python3
"""Jeff Mans's lineup-construction rules as a layer ON TOP of the NHL optimizer,
replayed on every 2026-27 NHL night we have a real DK field for.

Adam, 2026-11-05, after the NFL layer (scripts/mans_layer_backtest.py): "port the
backtest and lets see how it performs" on hockey. No NHL DFS show states rules the way
Jeff does, so the question is whether HIS rules carry over to a different sport.

The board: every night is rebuilt as it stood at lock from its pinned inputs
(scripts/nhl_rebuild.py: draftables, the last dfs_nhl odds scan and the DailyFaceoff
snapshot before the first puck drop, seed 0) -- the NHL optimizer searches the player
SIMULATION, which the projection log does not keep, so the logged rows cannot be used
the way the NFL replay uses them. Shipped cash is the field-aware lineup
(edge/dfs_nhl_field.optimize_cash), shipped GPP the 95th-percentile search with two
3+ stacks.

The rules, translated to hockey (MANS_METHODOLOGY.md; ep 86 / ep 326):
    core4    cash: lock the most CASH-owned C, W, D and G ("our core four ... all
             massively owned, 75% or more"), build the rest.
    punt     force the best low-owned salary saver (<= $3,000 skater, <= 10% projected
             GPP-owned; DK's NHL floor is $2,500) -- the best of the top 3 by the mode's
             own objective, so the optimizer still picks WHICH punt.
    stack1   cash with one 3+ skater team stack (NFL: QB + 1; NHL cash has no shape rule).
    fade1    GPP: drop the most-owned SKATER (ep 86 "fade the 80%-owned player").
    fadeG    GPP: drop the most-owned goalie -- in hockey he is often the night's chalk.
    pivot    GPP: fade1 and force his best-projected teammate on ANOTHER line, "the one
             who benefits when he fails" (ep 326: Amon-Ra for Gibbs). A linemate is the
             wrong translation: a goal pays the scorer and up to two linemates, so a
             linemate fails WITH him.
    mans     cash = core4 + punt; GPP = pivot + punt.
    ow30     GPP with own_weight 30, the leverage frontier's systematic fade.

A rule is imposed by seeding the search with the forced players and adding a constant
to their simulated points (and to the field's cash line), so every lineup the climb
compares contains them and the ranking among those lineups is the shipped objective's.
Lineups are scored with DK's FPTS from the contest exports on that draft group, or the
NHL box score for a player nobody in those contests drafted.

The slate is the unit; one board per date counts (the biggest), the Early / Late /
Turbo boards on the same night reuse its games and are shown, not counted. GPPs are
scored in dollars per $1 entry (Adam, 11/1), cash by clearing the Double Up line.

    python3 scripts/mans_layer_backtest_nhl.py                 # rebuild + rules, writes JSON
    python3 scripts/mans_layer_backtest_nhl.py --placebo       # drop the k-th most-owned skater
    python3 scripts/mans_layer_backtest_nhl.py --dollars       # cached lineups in $ per $1
    python3 scripts/mans_layer_backtest_nhl.py --field-tail    # real lineups without vs with
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import io
import json
import pickle
import random
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from edge import dfs_nhl_field as field_mod  # noqa: E402
from edge import dfs_opt_nhl as opt  # noqa: E402
from edge import nhl  # noqa: E402
from edge.names import norm  # noqa: E402
from scripts.nhl_ownership_signal import box_key  # noqa: E402

DB = ROOT / "data" / "contests.db"
CACHE = ROOT / "data" / "cache"
POOL = CACHE / "mans_nhl_board_{gid}.pkl"
OUT = CACHE / "mans_layer_backtest_nhl.json"
PLACEBO_OUT = CACHE / "mans_layer_placebo_nhl.json"
PUNT_SALARY = 3000
PUNT_OWN = 10.0
PUNT_TRIES = 3
BONUS = 1000.0
PLACEBO_K = 8


# --- the nights --------------------------------------------------------------------------

def slates(con) -> list[dict]:
    """One row per NHL draft group with a final GPP: the GPP (single-entry first, then the
    biggest), the biggest Double Up, and whether it is that date's main board."""
    rows = con.execute(
        "SELECT draft_group, slate_date, type, contest_id, live_entries, entry_fee, max_entries, name "
        "FROM contest WHERE sport='NHL' AND final=1").fetchall()
    by = defaultdict(lambda: {"gpp": [], "cash": []})
    for gid, date, typ, cid, n, fee, mx, name in rows:
        by[gid]["date"] = date
        by[gid][typ].append((mx == 1, n or 0, cid, fee, name))
    size = dict(con.execute("SELECT draft_group, count(*) FROM slate_player GROUP BY draft_group"))
    out = []
    for gid, v in by.items():
        if not v["gpp"]:
            continue
        g = max(v["gpp"])
        c = max(v["cash"], key=lambda r: r[1]) if v["cash"] else None
        out.append(dict(gid=gid, date=v["date"], gpp=g[2], gpp_fee=g[3], gpp_name=g[4].strip(),
                        cash=c[2] if c else None, players=size.get(gid, 0)))
    best = {}
    for s in out:
        if s["date"] not in best or s["players"] > best[s["date"]]["players"]:
            best[s["date"]] = s
    for s in out:
        s["main"] = best[s["date"]] is s
    return sorted(out, key=lambda s: (s["date"], not s["main"], s["gid"]))


def board(gid: int, sims: int):
    """(pool, sim) as at lock, cached: the rebuild takes ~20 s and is deterministic."""
    path = Path(str(POOL).format(gid=gid))
    if path.exists():
        return pickle.loads(path.read_bytes())
    from scripts import nhl_seven_d_compare as C
    with contextlib.redirect_stdout(io.StringIO()):
        lock = C.RB.lock_time(gid)
        at = C.RB.utc(lock)
        _, draftables = C.RB.git_json(f"data/draftables_snapshot/{gid}.json", at)
        _, lines = C.RB.git_json("data/nhl_lines_snapshot.json", at)
        prof = C.RB.for_sport(C.R.SPORT, "dfs")
        scan_id, _ = C.RB.scan_before(at, prof.name)

        def offline(url):
            raise RuntimeError("pinned to the snapshot")
        C.RB.dfs._draftables_raw = lambda g: draftables
        C.RB.nhl._next_data = offline
        C.RB.nhl._snapshot = lambda: lines
        client = C.RB.ScrapedOddsClient(C.RB.OddsStore(), prof.name, scan_id=scan_id,
                                        main_line_only=not prof.full_ladders)
        pool, sim, info = C.R.build_board(gid, {"gid": gid, "start": lock, "label": "mans"}, client,
                                          n_sims=sims, seed=0)
    res = (pool, sim, {"date": info["date"], "games": info["games"], "scan": scan_id})
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(res))
    return res


def actual_points(con, gid: int, date: str) -> tuple[dict, dict]:
    """{player_norm: DK FPTS} from every export on the draft group, and a box-score
    fallback keyed by box_key for players nobody drafted."""
    fp = {}
    for n, f in con.execute("SELECT r.player_norm, r.fpts FROM player_result r JOIN contest c "
                            "USING (contest_id) WHERE c.draft_group=?", (gid,)):
        if f is not None:
            fp[n] = f
    box = {}
    for attempt in range(4):
        try:
            games = nhl.schedule(date)
            break
        except Exception:                                   # noqa: BLE001
            if attempt == 3:
                raise
            import time
            time.sleep(2 + 3 * attempt)
    for g in games:
        try:
            b = nhl.boxscore(g["id"])
        except Exception:                                   # noqa: BLE001
            continue
        for s in b["skaters"]:
            box[box_key(s["team"], s["name"])] = float(nhl.skater_points(
                s["goals"], s["assists"], s["shots"], s["blocks"]))
    return fp, box


def score(players, fp, box) -> tuple[float, list]:
    tot, missing = 0.0, []
    for p in players:
        v = fp.get(norm(p["name"]))
        if v is None and p["pos"] != "G":
            v = box.get(box_key(p["team"], p["name"]))
        if v is None:
            missing.append(p["name"])
            v = 0.0
        tot += v
    return round(tot, 2), missing


def contest_field(con, cid):
    if cid is None:
        return None
    rows = con.execute("SELECT points, payout FROM entry_feature WHERE contest_id=? AND live=1 "
                       "ORDER BY points", (cid,)).fetchall()
    fee = con.execute("SELECT entry_fee FROM contest WHERE contest_id=?", (cid,)).fetchone()[0] or 1.0
    cash = con.execute("SELECT min(points) FROM entry_feature WHERE contest_id=? AND live=1 AND cash=1",
                       (cid,)).fetchone()[0]
    top1 = con.execute("SELECT min(points) FROM entry_feature WHERE contest_id=? AND live=1 AND top1=1",
                       (cid,)).fetchone()[0]
    return dict(points=[r[0] for r in rows], pay=[(r[1] or 0.0) / fee for r in rows],
                cash=cash, top1=top1, n=len(rows))


def paid(f, x: float) -> float:
    """Per $1: the prize of the best real entry at or below this score."""
    i = bisect.bisect_right(f["points"], x) - 1
    return f["pay"][i] if i >= 0 else 0.0


def pctile(pts, x):
    lo, hi = bisect.bisect_left(pts, x), bisect.bisect_right(pts, x)
    return 100.0 * (lo + 0.5 * (hi - lo)) / len(pts)


# --- the shipped search, with players forced in -----------------------------------------

def _fill_forced(rng, pool, order, forced, min_stack):
    """opt._fill, starting from the forced players."""
    has_g = [i for i in forced if pool[i]["pos"] == "G"]
    for _ in range(400):
        g = has_g[0] if has_g else rng.choice(order["G"][:6])
        picked = list(dict.fromkeys(forced + [g]))
        spent = sum(pool[i]["salary"] for i in picked)
        need = ["C", "C", "W", "W", "W", "D", "D", rng.choice(["C", "W", "W", "D"])]
        for i in picked:
            if pool[i]["pos"] != "G":
                if pool[i]["pos"] in need:
                    need.remove(pool[i]["pos"])
                else:
                    need.pop()
        rng.shuffle(need)
        ok = True
        for k, pos in enumerate(need):
            budget = nhl.SALARY_CAP - spent - (len(need) - k - 1) * 2500
            cands = [j for j in order[pos][:18] if j not in picked
                     and pool[j]["salary"] <= budget and pool[j]["team"] != pool[g].get("opp")]
            if not cands:
                ok = False
                break
            j = rng.choice(cands)
            picked.append(j)
            spent += pool[j]["salary"]
        if ok and opt._valid(pool, picked, min_stack):
            return picked
    return None


def build(pool, sim, mode, force=(), exclude=(), own_weight=0.0, min_stack=None, line=None,
          iters=150, seed=0):
    """dfs_opt_nhl.optimize with `force` (names in every lineup) and `exclude`."""
    if min_stack is None:
        min_stack = opt.GPP_STACKS if mode == "gpp" else ()
    if not force:
        return opt.optimize(pool, sim, mode=mode, iters=iters, seed=seed, own_weight=own_weight,
                            min_stack=min_stack, exclude=set(exclude) or None, line=line)
    names = {p["name"]: i for i, p in enumerate(pool)}
    if any(n not in names for n in force) or set(force) & set(exclude):
        return None
    forced = [names[n] for n in force]
    sim_b = sim.copy()
    sim_b[:, forced] += BONUS
    line_b = None if line is None else line + BONUS * len(forced)
    rng = random.Random(seed)
    key = "ceil" if mode == "gpp" else "floor"
    usable = [i for i, p in enumerate(pool) if p.get("salary") and p["name"] not in set(exclude)]
    order = {pos: sorted((i for i in usable if pool[i]["pos"] == pos),
                         key=lambda i: -(pool[i].get(key) or 0) / pool[i]["salary"] * 1000
                         - 0.15 * (pool[i].get(key) or 0)) for pos in ("C", "W", "D", "G")}
    by_key = {pos: sorted((i for i in usable if pool[i]["pos"] == pos),
                          key=lambda i: -(pool[i].get(key) or 0)) for pos in ("C", "W", "D", "G")}
    cand = {"G": by_key["G"][:opt.CANDIDATES["G"]]}
    cand["skaters"] = sorted({i for pos in ("C", "W", "D")
                              for i in by_key[pos][:opt.CANDIDATES[pos]] + order[pos][:opt.CANDIDATES[pos]]})
    owns = np.array([float(p.get("own") or 0.0) for p in pool])
    best, best_v = None, None
    for _ in range(iters):
        start = _fill_forced(rng, pool, order, forced, min_stack)
        if start is None:
            continue
        idx, v = opt._hill_climb(pool, sim_b, start, cand, mode, own_weight, owns, min_stack, line_b)
        if not set(forced) <= set(idx):
            continue
        if best_v is None or v > best_v:
            best, best_v = idx, v
    return opt._result(pool, sim, best) if best else None


def objective(res, pool, sim, mode, line):
    if mode == "gpp":
        return res["ceil"]
    idx = [pool.index(r) for r in res["lineup"]]
    return float((sim[:, idx].sum(axis=1) >= line).mean())


# --- the rules --------------------------------------------------------------------------

def top_owned(pool, pos=None, key="own", skip=()):
    c = [p for p in pool if (pos is None and p["pos"] != "G" or p["pos"] == pos)
         and p["name"] not in skip]
    return max(c, key=lambda p: p.get(key) or 0.0) if c else None


def punts(pool, mode, exclude=()):
    k = "ceil" if mode == "gpp" else "floor"
    c = [p for p in pool if p["pos"] != "G" and p["salary"] <= PUNT_SALARY
         and (p.get("own") or 0) <= PUNT_OWN and p["name"] not in set(exclude)]
    return sorted(c, key=lambda p: -(p.get(k) or 0))[:PUNT_TRIES]


def with_punt(pool, sim, mode, line, force=(), exclude=(), **kw):
    best, best_v = None, None
    for pt in punts(pool, mode, exclude):
        if pt["name"] in force:
            continue
        r = build(pool, sim, mode, force=tuple(force) + (pt["name"],), exclude=exclude, line=line, **kw)
        if r:
            v = objective(r, pool, sim, mode, line)
            if best_v is None or v > best_v:
                best, best_v = r, v
                best["punt"] = pt["name"]
    return best


def pivot_of(pool):
    chalk = top_owned(pool)
    mates = [p for p in pool if p["team"] == chalk["team"] and p["pos"] != "G"
             and p["name"] != chalk["name"] and p.get("line") != chalk.get("line")]
    return chalk, max(mates, key=lambda p: p["proj"]) if mates else None


def variants(pool, sim, iters):
    kw = dict(iters=iters)
    own = field_mod.cash_ownership(pool)                   # sets p["cash_own"]
    line = field_mod.cash_line(sim, field_mod.sample_field(pool, own, n=field_mod.FIELD_SIZE, seed=0))
    out = {}
    out["cash"] = build(pool, sim, "cash", line=line, **kw)
    core = [top_owned(pool, q, key="cash_own")["name"] for q in ("C", "W", "D", "G")]
    out["cash core4"] = build(pool, sim, "cash", force=core, line=line, **kw)
    out["cash punt"] = with_punt(pool, sim, "cash", line, **kw)
    out["cash stack1"] = build(pool, sim, "cash", min_stack=(3,), line=line, **kw)
    out["cash mans"] = with_punt(pool, sim, "cash", line, force=core, **kw)
    out["gpp"] = build(pool, sim, "gpp", **kw)
    chalk, mate = pivot_of(pool)
    goalie = top_owned(pool, "G")
    out["gpp fade1"] = build(pool, sim, "gpp", exclude=[chalk["name"]], **kw)
    out["gpp fadeG"] = build(pool, sim, "gpp", exclude=[goalie["name"]], **kw)
    out["gpp pivot"] = build(pool, sim, "gpp", exclude=[chalk["name"]],
                             force=[mate["name"]] if mate else (), **kw)
    out["gpp punt"] = with_punt(pool, sim, "gpp", None, **kw)
    out["gpp mans"] = with_punt(pool, sim, "gpp", None, exclude=[chalk["name"]],
                                force=[mate["name"]] if mate else (), **kw)
    out["gpp ow30"] = build(pool, sim, "gpp", own_weight=30.0, **kw)
    meta = dict(core=core, chalk=chalk["name"], chalk_own=chalk["own"], chalk_line=chalk.get("line"),
                goalie=goalie["name"], goalie_own=goalie["own"], pivot=mate["name"] if mate else None)
    return out, meta


# --- one night ---------------------------------------------------------------------------

def rebuildable(s, sims):
    """The board, or None when a pinned input is gone (no draftables commit before lock)."""
    try:
        return board(s["gid"], sims)
    except (SystemExit, Exception) as exc:                  # noqa: BLE001
        print(f"  {s['date']} {s['gid']}: cannot rebuild ({exc}), skipped", file=sys.stderr)
        return None


def run_slate(args):
    s, iters, sims = args
    con = sqlite3.connect(DB)
    b = rebuildable(s, sims)
    if b is None:
        return dict(s, skipped=True)
    pool, sim, info = b
    fp, box = actual_points(con, s["gid"], info["date"])
    fg, fc = contest_field(con, s["gpp"]), contest_field(con, s["cash"])
    res, meta = variants(pool, sim, iters)
    rows = {}
    for name, r in res.items():
        if not r:
            rows[name] = None
            continue
        act, missing = score(r["lineup"], fp, box)
        rows[name] = dict(
            players=[(slot, p["name"], p["team"], p.get("line"), p["salary"], p["proj"], p["own"],
                      fp.get(norm(p["name"]))) for slot, p in r["slots"]],
            proj=round(sum(p["proj"] for p in r["lineup"]), 2), floor=r.get("floor"),
            ceil=r.get("ceil"), own=r["own"], salary=r["salary"], actual=act, missing=missing,
            punt=r.get("punt"), stacks=r.get("stacks"),
            cashed_du=None if fc is None else act >= fc["cash"],
            cashed_gpp=act >= fg["cash"], top1=act >= fg["top1"],
            pct_gpp=round(pctile(fg["points"], act), 1), paid=round(paid(fg, act), 2))
    return dict(s, **info, meta=meta, rows=rows,
                field=dict(gpp_n=fg["n"], gpp_cash=fg["cash"], top1=fg["top1"],
                           du_n=fc["n"] if fc else None, du_cash=fc["cash"] if fc else None))


def run_placebo(args):
    """GPP with the k-th most-owned skater removed, k = 1..PLACEBO_K."""
    s, iters, sims = args
    con = sqlite3.connect(DB)
    b = rebuildable(s, sims)
    if b is None:
        return dict(s, label=s["date"], skipped=True)
    pool, sim, info = b
    fp, box = actual_points(con, s["gid"], info["date"])
    fg = contest_field(con, s["gpp"])
    base = build(pool, sim, "gpp", iters=iters)
    b_act, _ = score(base["lineup"], fp, box)
    in_base = {p["name"] for p in base["lineup"]}
    ranked = sorted((p for p in pool if p["pos"] != "G"), key=lambda p: -p["own"])
    out = []
    for k, p in enumerate(ranked[:PLACEBO_K], 1):
        r = build(pool, sim, "gpp", exclude=[p["name"]], iters=iters) if p["name"] in in_base else base
        act, _ = score(r["lineup"], fp, box)
        out.append(dict(k=k, name=p["name"], own=p["own"], in_base=p["name"] in in_base,
                        d_actual=round(act - b_act, 2), paid=round(paid(fg, act), 2)))
    return dict(label=s["date"], gid=s["gid"], main=s["main"], base=b_act,
                base_paid=round(paid(fg, b_act), 2), rows=out)


# --- reports -----------------------------------------------------------------------------

def report(out):
    sl = [s for s in out["slates"] if not s.get("skipped")]
    for s in out["slates"]:
        if s.get("skipped"):
            print(f"skipped {s['date']} {s['gid']}: board cannot be rebuilt")
    names = list(sl[0]["rows"])
    mains = [s for s in sl if s["main"]]
    print(f"{len(mains)} main nights ({len(sl)} boards); GPP entries {sum(s['field']['gpp_n'] for s in mains):,}")
    print(f"{'night':16s} " + " ".join(f"{n[-7:]:>7s}" for n in names))
    for s in sl:
        print(f"{s['date'][5:]} {s['gid']}{'' if s['main'] else '*'} " + " ".join(
            f"{(r['actual'] if r else float('nan')):7.1f}" for r in (s["rows"][n] for n in names)))
    for base, group in (("cash", [n for n in names if n.startswith("cash ")]),
                        ("gpp", [n for n in names if n.startswith("gpp ")])):
        b = [s["rows"][base] for s in mains]
        du = [r["cashed_du"] for r in b if r["cashed_du"] is not None]
        print(f"\n{base:12s} proj {np.mean([r['proj'] for r in b]):6.1f}  actual "
              f"{np.mean([r['actual'] for r in b]):6.1f}  DU cash {sum(du)}/{len(du)}  GPP cash "
              f"{sum(r['cashed_gpp'] for r in b)}/{len(b)}  top1 {sum(r['top1'] for r in b)}  "
              f"paid ${sum(r['paid'] for r in b):.2f}/{len(b)}")
        for n in group:
            pairs = [(s["rows"][n], s["rows"][base]) for s in mains if s["rows"][n] and s["rows"][base]]
            d = [x["actual"] - y["actual"] for x, y in pairs]
            dp = [x["proj"] - y["proj"] for x, y in pairs]
            same = sum({q[1] for q in x["players"]} == {q[1] for q in y["players"]} for x, y in pairs)
            du = [x["cashed_du"] for x, _ in pairs if x["cashed_du"] is not None]
            print(f"  {n:12s} n {len(pairs)} (same lineup {same})  d proj {np.mean(dp):+5.1f}  d actual "
                  f"{np.mean(d):+6.1f}  better {sum(x > 0 for x in d)} worse {sum(x < 0 for x in d)}  "
                  f"DU cash {sum(du)}/{len(du)}  GPP cash {sum(x['cashed_gpp'] for x, _ in pairs)}  "
                  f"top1 {sum(x['top1'] for x, _ in pairs)}  paid ${sum(x['paid'] for x, _ in pairs):.2f}")
    miss = sum(len(r["missing"]) for s in sl for r in s["rows"].values() if r)
    print(f"\nunscored players (0 pts): {miss}")


def report_placebo(res):
    mains = [s for s in res if s["main"] and not s.get("skipped")]
    print(f"{len(mains)} main nights; base paid ${sum(s['base_paid'] for s in mains):.2f}")
    for k in range(1, PLACEBO_K + 1):
        v = [next(r for r in s["rows"] if r["k"] == k) for s in mains]
        d = [r["d_actual"] for r in v]
        print(f"drop k={k}: in base {sum(r['in_base'] for r in v)}/{len(v)}  mean d actual "
              f"{np.mean(d):+6.1f}  better {sum(x > 0 for x in d)} worse {sum(x < 0 for x in d)}  "
              f"paid ${sum(r['paid'] for r in v):.2f}")


def field_tail(con, sl):
    """Real entries WITHOUT vs WITH the k-th most-owned skater on our pre-lock board,
    inside the night's projection quintiles, per $1 and top-1% / top-0.1% rates."""
    mains = [s for s in sl if s["main"]]
    res = defaultdict(lambda: defaultdict(list))
    for s in mains:
        b = rebuildable(s, 2000)
        if b is None:
            continue
        pool = b[0]
        proj = {norm(p["name"]): p["proj"] for p in pool}
        ranked = [norm(p["name"]) for p in sorted((p for p in pool if p["pos"] != "G"),
                                                  key=lambda p: -p["own"])][:PLACEBO_K]
        ranked.append(norm(max((p for p in pool if p["pos"] == "G"), key=lambda p: p["own"])["name"]))
        ent = defaultdict(set)
        for e, n in con.execute("SELECT entry_id, player_norm FROM entry_player WHERE contest_id=?",
                                (s["gpp"],)):
            ent[e].add(n)
        fee = con.execute("SELECT entry_fee FROM contest WHERE contest_id=?", (s["gpp"],)).fetchone()[0] or 1
        rows = con.execute("SELECT entry_id, rank, payout FROM entry_feature WHERE contest_id=? AND live=1",
                           (s["gpp"],)).fetchall()
        ps = np.array([sum(proj.get(n, 0) for n in ent[r[0]]) for r in rows])
        rk = np.array([r[1] for r in rows])
        pay = np.array([(r[2] or 0.0) / fee for r in rows])
        bins = np.searchsorted(np.quantile(ps, [.2, .4, .6, .8]), ps)
        for k, n in enumerate(ranked, 1):
            has = np.array([n in ent[r[0]] for r in rows])
            for name, y in (("ret", pay), ("top1", (rk <= .01 * len(rows)) * 100.0),
                            ("top01", (rk <= max(1, .001 * len(rows))) * 100.0)):
                a, b, w = [], [], []
                for q in range(5):
                    h, o = (bins == q) & has, (bins == q) & ~has
                    if h.sum() >= 10 and o.sum() >= 10:
                        a.append(y[o].mean())
                        b.append(y[h].mean())
                        w.append(min(h.sum(), o.sum()))
                if w:
                    res[k][name].append((np.average(a, weights=w), np.average(b, weights=w)))
    for name, title in (("ret", "$ per $1"), ("top1", "top 1% pp"), ("top01", "top .1% pp")):
        for k in range(1, PLACEBO_K + 2):
            v = res[k][name]
            d = [x - y for x, y in v]
            tag = f"k={k}" if k <= PLACEBO_K else "top G"
            print(f"{title:11s} {tag:5s} n {len(v)}  without {np.mean([x for x, _ in v]):.3f} with "
                  f"{np.mean([y for _, y in v]):.3f}  diff {np.mean(d):+.3f}  pos {sum(x > 0 for x in d)}/{len(d)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--iters", type=int, default=150)
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--placebo", action="store_true")
    ap.add_argument("--report", action="store_true", help="print the cached results")
    ap.add_argument("--field-tail", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    sl = slates(con)
    if a.field_tail:
        return field_tail(con, sl)
    if a.report:
        report(json.loads(OUT.read_text()))
        if PLACEBO_OUT.exists():
            print()
            report_placebo(json.loads(PLACEBO_OUT.read_text()))
        return
    jobs = [(s, a.iters, a.sims) for s in (sl if not a.placebo else [s for s in sl if s["main"]])]
    with ProcessPoolExecutor(a.workers) as ex:
        res = []
        for r in ex.map(run_placebo if a.placebo else run_slate, jobs):
            print(f"  {r.get('date', r.get('label'))} {r['gid']} done", file=sys.stderr)
            res.append(r)
    if a.placebo:
        PLACEBO_OUT.write_text(json.dumps(res, indent=1))
        return report_placebo(res)
    out = dict(iters=a.iters, sims=a.sims, slates=res)
    OUT.write_text(json.dumps(out, indent=1, default=list))
    report(out)


if __name__ == "__main__":
    main()
