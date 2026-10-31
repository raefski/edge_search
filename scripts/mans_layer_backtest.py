#!/usr/bin/env python3
"""Jeff Mans's lineup-construction rules as a layer ON TOP of our optimizer,
replayed on every 2026 NFL slate we have a pre-lock board and a real DK field for.

Adam, 2026-10-31: "after we assign projected points and ownerships and stacks, can we
use Jeff Mans' higher-level thinking as the neocortex of lineup construction ...
see if this would result in more total points per lineup and/or helps us get into the
money."

scripts/mans_dfs_rules.py asked the FIELD question (do real lineups with feature X do
better at fixed projection?). This asks the BUILD question: take the exact pool the app
had ~10 minutes before lock (`projection` rows in data/contests.db, one pre-lock snapshot
per draft group; 10/4 rebuilt by scripts/mans_claims.py), build the shipped cash and GPP
lineups, then rebuild with each rule imposed as a constraint, and score every lineup
with what the players actually scored (DK `player_result.fpts`) against the real
contests on that draft group: the Double Up's cash line, the single-entry GPP's cash
line, top 1%, and percentile.

The rules (MANS_METHODOLOGY.md for eps 39/86/89/227; ep 326 "What A Day", 2026-10-28,
16:35-32:00 for the week-7 recap that prompted this):
    core4    cash: lock the most-owned QB, RB, WR and TE ("our core four was Love,
             Gibbs, Golden, Kittle ... all massively owned, 75% or more"), build the
             rest.
    punt     force the best low-owned salary saver (<= $4,000, <= 10% projected owned)
             and let the optimizer spend what it frees ("Jacobi Lane allowed us to use
             Bijan with Gibbs"). The best of the top 3 such punts by the mode's own
             objective is kept, so the optimizer still picks WHICH punt.
    fade1    GPP: drop the slate's most-owned non-DST player (ep 86 "fade the
             80%-owned player").
    pivot    GPP: fade1 and force his best-projected skill teammate -- "play the one who
             benefits when he fails" (ep 326: Amon-Ra at 10% instead of Gibbs at 90%;
             McCaffrey instead of Kittle, "if Kittle sucked it's because McCaffrey took
             his touchdown").
    stack1   cash allowed a QB + 1 pass-catcher (the open item in MANS_METHODOLOGY.md:
             the field said stacks helped cash 5/5).
    mans     cash = core4 + punt; GPP = pivot + punt.
    ow30     GPP with own_weight 30 -- the existing leverage frontier's systematic fade,
             for comparison with Jeff's targeted one.

Each lineup is ONE realisation per slate. Variant-vs-baseline differences are reported
per slate and as sign counts; the main slates are the independent units (Early-only and
Afternoon-only boards reuse the same games, so they are shown but not counted as new
evidence). Below ~5 slates of agreement, a difference is noise.

    python3 scripts/mans_layer_backtest.py               # all slates, writes JSON
    python3 scripts/mans_layer_backtest.py --iters 150   # quicker
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from edge import dfs_nfl_theory as theory   # noqa: E402
from edge import dfs_opt_nfl as opt         # noqa: E402
from edge.dfs_roster import assign_slots    # noqa: E402
from edge.names import norm                 # noqa: E402

DB = ROOT / "data" / "contests.db"
REBUILT = ROOT / "data" / "cache" / "mans_rebuilt_pool_{gid}.json"
OUT = ROOT / "data" / "cache" / "mans_layer_backtest.json"
# (label, draft group, NFL week, main slate?, cash contest, GPP contest). The single-entry
# Daily Dollar and its Double Up on each board; the 9/27 primetime board has no log.
SLATES = (
    ("9/13", 151307, 1, True, 195614469, 193028246),
    ("9/20", 153428, 2, True, 195659767, 195661382),
    ("9/27", 153769, 3, True, 195920333, 195921991),
    ("10/4", 154078, 4, True, 196170291, 196169741),
    ("10/11", 154468, 5, True, 196601669, 196420207),
    ("10/18", 154941, 6, True, 196673344, 196672044),
    ("10/25", 155198, 7, True, 196927327, 196924833),
    ("10/18 early", 154942, 6, False, 196710086, 196877519),
    ("10/18 late", 154944, 6, False, 196742625, 196743647),
    ("10/25 early", 155199, 7, False, 196979048, 196978086),
)
PUNT_SALARY = 4000     # "salary saver": DK's cheapest tier at WR/TE/RB is $3,000-4,000
PUNT_OWN = 10.0        # projected GPP-owned % at or under which a punt is "low-owned"
PUNT_TRIES = 3
DK_TO_NFLVERSE = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}


# --- the board as the app had it --------------------------------------------------------

def load_pool(con, gid: int) -> list[dict]:
    sp = {n: dict(name=nm, team=t, opp_team=o, dk_pos=p, salary=s, game=g)
          for n, nm, t, o, p, s, g in con.execute(
              "select player_norm, name, team, opp, position, salary, game from slate_player "
              "where draft_group=?", (gid,))}
    rows = con.execute("select player_norm, proj, own_pred, extra from projection "
                       "where draft_group=? and proj is not null order by logged_at", (gid,)).fetchall()
    logged = {}
    if rows:
        for n, proj, own, extra in rows:
            ex = json.loads(extra or "{}")
            logged[n] = dict(proj=proj, own=own or 0.0, leverage=float(ex.get("leverage") or 0.0))
    else:
        for p in json.loads(Path(str(REBUILT).format(gid=gid)).read_text()):
            logged[norm(p["name"])] = dict(proj=p["proj"], own=p.get("own") or 0.0, leverage=None)
    pool = []
    for n, v in logged.items():
        info = sp.get(n)
        if info is None or not info["salary"]:
            continue
        pool.append(dict(info, norm=n, pos=opt.eligible_slots(info["dk_pos"]), **v))
    if any(p["leverage"] is None for p in pool):
        # The rebuilt 10/4 pool carries no leverage; recompute it the way add_ownership
        # does (projection percentile minus ownership percentile) so the GPP search
        # tries quarterbacks in the same order it would have live.
        rk = lambda key: {id(p): i / max(1, len(pool) - 1)
                          for i, p in enumerate(sorted(pool, key=lambda q: q[key]))}
        pr, ow = rk("proj"), rk("own")
        for p in pool:
            p["leverage"] = round(100 * (pr[id(p)] - ow[id(p)]), 1)
    return pool


def actual_points(con, gid: int, cids) -> dict:
    out = {}
    for cid in cids:
        for n, f in con.execute("select player_norm, fpts from player_result where contest_id=?",
                                (cid,)):
            if f is not None:
                out[n] = f
    return out


def field(con, cid: int) -> dict:
    pts = sorted(p for (p,) in con.execute(
        "select points from entry_feature where contest_id=? and live=1", (cid,)))
    cash = con.execute("select min(points) from entry_feature where contest_id=? and live=1 "
                       "and cash=1", (cid,)).fetchone()[0]
    top1 = con.execute("select min(points) from entry_feature where contest_id=? and live=1 "
                       "and top1=1", (cid,)).fetchone()[0]
    adam = con.execute("select points from entry where contest_id=? and is_adam=1",
                       (cid,)).fetchone()
    return dict(points=pts, cash=cash, top1=top1, n=len(pts), adam=adam[0] if adam else None)


def pctile(pts: list, x: float) -> float:
    import bisect
    lo, hi = bisect.bisect_left(pts, x), bisect.bisect_right(pts, x)
    return 100.0 * (lo + 0.5 * (hi - lo)) / len(pts)


# --- the shipped search, with players forced in ---------------------------------------

def build(pool, mode, force=(), exclude=(), stack_n=2, bring_back=1, iters=300, seed=0,
          own_weight=0.0, z_cash=theory.Z_CASH, z_gpp=theory.Z_GPP):
    """dfs_opt_nfl.optimize with `force` (names locked into every lineup) and `exclude`.

    With nothing forced this IS opt.optimize. With players forced it runs the same
    two-stage search (cash: restarts + climb; GPP: every QB's stack screened, the best
    GPP_FINALISTS searched in full) with the forced players added to each locked group,
    so a rule changes the constraint and nothing else about how the lineup is found."""
    players = [dict(p) for p in pool if p["name"] not in set(exclude)]
    if not force:
        return opt.optimize(players, mode=mode, stack_n=stack_n, bring_back=bring_back,
                            iters=iters, seed=seed, own_weight=own_weight,
                            z_cash=z_cash, z_gpp=z_gpp)
    rng = random.Random(seed)
    for p in players:
        p.pop("_sd", None)
        p["_pv"] = (p["proj"] + z_gpp * opt._sd(p) - own_weight * p.get("own", 0.0) / 100.0) \
            if mode == "gpp" else (p["proj"] - z_cash * opt._sd(p))
    by = {p["name"]: p for p in players}
    forced = [by[n] for n in force if n in by]
    if len(forced) != len(force) or assign_slots(forced, opt.SLOTS) is None:
        return None
    index = opt._candidate_index(players)

    def search(group, n_iters):
        locked = frozenset(q["name"] for q in group)
        best, best_key = None, None
        for _ in range(n_iters):
            lu = opt._fill(players, rng, group, "_pv")
            if not lu or not opt._valid(lu):
                continue
            lu, key = opt._hill_climb(lu, players, rng, locked=locked, mode=mode,
                                      z_cash=z_cash, z_gpp=z_gpp, index=index,
                                      own_weight=own_weight)
            if best_key is None or key > best_key:
                best, best_key = lu, key
        return best, best_key

    if mode != "gpp":
        best, _ = search(forced, iters)
        return opt._result(best, None, z_cash, z_gpp) if best else None

    forced_qb = [p for p in forced if "QB" in p["pos"] and not (p["pos"] - {"QB"})]
    qbs = forced_qb or [p for p in players if "QB" in p["pos"]]
    groups = {}
    for qb in qbs:
        rest = [p for p in players if p["name"] not in {f["name"] for f in forced}]
        need = stack_n - sum(1 for f in forced if f["team"] == qb["team"]
                             and f["pos"] & opt.CATCHERS and f is not qb)
        g = [qb] + [f for f in forced if f is not qb]
        g += opt.stack_candidates(rest, qb, max(0, need), "_pv")
        if not any(f["team"] == qb.get("opp_team") and f["pos"] & opt.CATCHERS for f in forced):
            g += [b for b in opt.bring_back_candidates(rest, qb, bring_back, "_pv")]
        if sum(1 for p in g if p["team"] == qb["team"] and p["pos"] & opt.CATCHERS
               and p is not qb) < stack_n or assign_slots(g, opt.SLOTS) is None:
            continue
        if sum(p["salary"] for p in g) > opt.CAP:
            continue
        groups[qb["name"]] = (qb, g)
    if not groups:
        return None
    screened = []
    for name, (qb, g) in groups.items():
        lu, key = search(g, max(20, iters // 8))
        if lu:
            screened.append((key, name))
    screened.sort(reverse=True)
    best, best_key, best_qb = None, None, None
    for _, name in screened[:opt.GPP_FINALISTS]:
        qb, g = groups[name]
        lu, key = search(g, iters)
        if lu and (best_key is None or key > best_key):
            best, best_key, best_qb = lu, key, qb
    return opt._result(best, best_qb, z_cash, z_gpp) if best else None


def objective(res, mode):
    return res["ceil"] if mode == "gpp" else res["floor"]


# --- the rules --------------------------------------------------------------------------

def top_owned(pool, pos=None):
    c = [p for p in pool if "DST" not in p["pos"] and (pos is None or pos in p["pos"])]
    return max(c, key=lambda p: p["own"]) if c else None


def punts(pool, mode, exclude=()):
    key = (lambda p: p["proj"] + theory.Z_GPP * opt._sd(p)) if mode == "gpp" else \
        (lambda p: p["proj"] - theory.Z_CASH * opt._sd(p))
    c = [p for p in pool if p["salary"] <= PUNT_SALARY and p["own"] <= PUNT_OWN
         and not p["pos"] & {"DST", "QB"} and p["name"] not in set(exclude)]
    return sorted(c, key=key, reverse=True)[:PUNT_TRIES]


def with_punt(pool, mode, force=(), exclude=(), **kw):
    best = None
    for pt in punts(pool, mode, exclude):
        if pt["name"] in force:
            continue
        r = build(pool, mode, force=tuple(force) + (pt["name"],), exclude=exclude, **kw)
        if r and (best is None or objective(r, mode) > objective(best, mode)):
            best = r
            best["punt"] = pt["name"]
    return best


def pivot_of(pool):
    chalk = top_owned(pool)
    mates = [p for p in pool if p["team"] == chalk["team"] and p["name"] != chalk["name"]
             and p["pos"] & {"RB", "WR", "TE"}]
    return chalk, max(mates, key=lambda p: p["proj"]) if mates else None


def variants(pool, iters):
    kw = dict(iters=iters)
    out = {}
    out["cash"] = build(pool, "cash", **kw)
    core = [top_owned(pool, q)["name"] for q in ("QB", "RB", "WR", "TE")]
    out["cash core4"] = build(pool, "cash", force=core, **kw)
    out["cash punt"] = with_punt(pool, "cash", **kw)
    out["cash stack1"] = build(pool, "gpp", stack_n=1, bring_back=0, z_gpp=-theory.Z_CASH, **kw)
    out["cash mans"] = with_punt(pool, "cash", force=core, **kw)
    out["gpp"] = build(pool, "gpp", **kw)
    chalk, mate = pivot_of(pool)
    out["gpp fade1"] = build(pool, "gpp", exclude=[chalk["name"]], **kw)
    out["gpp pivot"] = build(pool, "gpp", exclude=[chalk["name"]],
                             force=[mate["name"]] if mate else (), **kw)
    out["gpp punt"] = with_punt(pool, "gpp", **kw)
    out["gpp mans"] = with_punt(pool, "gpp", exclude=[chalk["name"]],
                                force=[mate["name"]] if mate else (), **kw)
    out["gpp ow30"] = build(pool, "gpp", own_weight=30.0, **kw)
    meta = dict(core=core, chalk=chalk["name"], chalk_own=chalk["own"],
                pivot=mate["name"] if mate else None)
    return out, meta


# --- one slate ---------------------------------------------------------------------------

def run_slate(args):
    label, gid, week, main, cash_cid, gpp_cid, iters = args
    con = sqlite3.connect(DB)
    pool = load_pool(con, gid)
    fp = actual_points(con, gid, (gpp_cid, cash_cid))
    fc, fg = field(con, cash_cid), field(con, gpp_cid)
    res, meta = variants(pool, iters)
    rows = {}
    for name, r in res.items():
        if not r:
            rows[name] = None
            continue
        players = [p for p, _ in r["lineup"]]
        missing = [p["name"] for p in players if norm(p["name"]) not in fp]
        act = sum(fp.get(norm(p["name"]), 0.0) for p in players)
        rows[name] = dict(
            players=[(s, p["name"], p["team"], p["salary"], p["proj"], p["own"],
                      fp.get(norm(p["name"]))) for p, s in r["lineup"]],
            proj=r["proj"], floor=r["floor"], ceil=r["ceil"], own=r["own"],
            salary=r["salary"], actual=round(act, 2), missing=missing,
            punt=r.get("punt"), stack=r.get("stack"),
            cash_line=fc["cash"], gpp_cash_line=fg["cash"], top1_line=fg["top1"],
            cashed_du=act >= fc["cash"], cashed_gpp=act >= fg["cash"], top1=act >= fg["top1"],
            pct_gpp=round(pctile(fg["points"], act), 1), pct_du=round(pctile(fc["points"], act), 1))
    return dict(label=label, gid=gid, week=week, main=main, meta=meta, rows=rows,
                field=dict(du_n=fc["n"], gpp_n=fg["n"], du_cash=fc["cash"], gpp_cash=fg["cash"],
                           top1=fg["top1"], adam_du=fc["adam"], adam_gpp=fg["adam"]))


# --- the field's view of kickoff windows (ep 326: "people love the late games") ----------

def window_residuals(con):
    """Actual GPP ownership minus our pre-lock prediction, by kickoff window, per main slate."""
    kick = {}
    with (ROOT / "data" / "nflverse_games.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r["season"] == "2026" and r["game_type"] == "REG" and r["weekday"] == "Sunday":
                kick[(int(r["week"]), frozenset((r["away_team"], r["home_team"])))] = r["gametime"]
    out = []
    for label, gid, week, main, _, gpp_cid in SLATES:
        if not main:
            continue
        pool = load_pool(con, gid)
        act = {n: o for n, o in con.execute(
            "select player_norm, pct_drafted from player_result where contest_id=?", (gpp_cid,))}
        by = defaultdict(list)
        for p in pool:
            key = (week, frozenset(DK_TO_NFLVERSE.get(t, t) for t in (p["team"], p["opp_team"])))
            t = kick.get(key)
            if t is None or "DST" in p["pos"]:
                continue
            w = "early" if t < "15:00" else "late"
            by[w].append((act.get(p["norm"], 0.0) or 0.0) - p["own"])
        out.append(dict(label=label, **{w: dict(n=len(v), bias=round(sum(v) / len(v), 2),
                                                    total=round(sum(v), 1))
                                        for w, v in by.items()}))
    return out


def run_placebo(args):
    """GPP with the k-th most-owned player removed, k = 1..PLACEBO_K: if dropping the
    top chalk is special, k=1 should beat k=2..K; if it is just 'any swap moves the
    total by +/-15 on one realisation', it will not."""
    label, gid, week, main, cash_cid, gpp_cid, iters = args
    con = sqlite3.connect(DB)
    pool = load_pool(con, gid)
    fp = actual_points(con, gid, (gpp_cid, cash_cid))
    fg = field(con, gpp_cid)
    base = build(pool, "gpp", iters=iters)
    score = lambda r: sum(fp.get(norm(p["name"]), 0.0) for p, _ in r["lineup"])
    in_base = {p["name"] for p, _ in base["lineup"]}
    ranked = sorted((p for p in pool if "DST" not in p["pos"]), key=lambda p: -p["own"])
    out = []
    for k, p in enumerate(ranked[:PLACEBO_K], 1):
        r = build(pool, "gpp", exclude=[p["name"]], iters=iters) if p["name"] in in_base else base
        out.append(dict(k=k, name=p["name"], own=p["own"], in_base=p["name"] in in_base,
                        d_actual=round(score(r) - score(base), 2), d_proj=round(r["proj"] - base["proj"], 1),
                        cashed=score(r) >= fg["cash"]))
    return dict(label=label, main=main, base=round(score(base), 2), base_cashed=score(base) >= fg["cash"],
                rows=out)


PLACEBO_K = 8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--placebo", action="store_true",
                    help="fade the k-th most-owned player for k=1..8 instead of the rule set")
    a = ap.parse_args()
    if a.placebo:
        jobs = [s + (a.iters,) for s in SLATES if s[3]]
        with ProcessPoolExecutor(a.workers) as ex:
            res = list(ex.map(run_placebo, jobs))
        (OUT.parent / "mans_layer_placebo.json").write_text(json.dumps(res, indent=1))
        for s in res:
            print(f"{s['label']:6s} base {s['base']:6.1f} " + " ".join(
                f"{r['k']}:{r['d_actual']:+6.1f}{'' if r['in_base'] else '*'}" for r in s["rows"]))
        for k in range(1, PLACEBO_K + 1):
            v = [r for s in res for r in s["rows"] if r["k"] == k]
            d = [r["d_actual"] for r in v]
            print(f"k={k}: in base lineup {sum(r['in_base'] for r in v)}/{len(v)}  mean d actual "
                  f"{sum(d)/len(d):+6.1f}  better {sum(x > 0 for x in d)} worse {sum(x < 0 for x in d)}"
                  f"  GPP cash {sum(r['cashed'] for r in v)}")
        return
    jobs = [s + (a.iters,) for s in SLATES]
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(run_slate, jobs))
    con = sqlite3.connect(DB)
    out = dict(iters=a.iters, slates=res, windows=window_residuals(con))
    OUT.write_text(json.dumps(out, indent=1, default=list))
    report(out)


def report(out):
    names = [n for n in out["slates"][0]["rows"]]
    print(f"{'slate':12s} " + " ".join(f"{n:>12s}" for n in names))
    for s in out["slates"]:
        print(f"{s['label']:12s} " + " ".join(
            f"{(r['actual'] if r else float('nan')):12.1f}" for r in
            (s["rows"][n] for n in names)))
    print()
    for base, group in (("cash", [n for n in names if n.startswith("cash ")]),
                        ("gpp", [n for n in names if n.startswith("gpp ")])):
        mains = [s for s in out["slates"] if s["main"]]
        b = [s["rows"][base] for s in mains]
        print(f"{base:12s} proj {sum(r['proj'] for r in b)/len(b):6.1f}  actual "
              f"{sum(r['actual'] for r in b)/len(b):6.1f}  DU cash {sum(r['cashed_du'] for r in b)}"
              f"/{len(b)}  GPP cash {sum(r['cashed_gpp'] for r in b)}/{len(b)}  top1 "
              f"{sum(r['top1'] for r in b)}  pct {sum(r['pct_gpp'] for r in b)/len(b):5.1f}")
        for n in group:
            v = [s["rows"][n] for s in mains]
            pairs = [(x, y) for x, y in zip(v, b) if x and y]
            d = [x["actual"] - y["actual"] for x, y in pairs]
            dp = [x["proj"] - y["proj"] for x, y in pairs]
            print(f"  {n:12s} n {len(pairs)}  d proj {sum(dp)/len(dp):+5.1f}  d actual "
                  f"{sum(d)/len(d):+6.1f}  better {sum(x > 0 for x in d)}/{len(d)}  "
                  f"DU cash {sum(x['cashed_du'] for x, _ in pairs)}  GPP cash "
                  f"{sum(x['cashed_gpp'] for x, _ in pairs)}  top1 {sum(x['top1'] for x, _ in pairs)}"
                  f"  pct {sum(x['pct_gpp'] for x, _ in pairs)/len(pairs):5.1f}")
    print("\nownership miss by kickoff window (actual - predicted, pp per player):")
    for w in out["windows"]:
        print(f"  {w['label']:6s} " + "  ".join(f"{k} {v['bias']:+.2f} (n {v['n']})"
                                               for k, v in w.items() if k != "label"))


if __name__ == "__main__":
    main()
