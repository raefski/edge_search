#!/usr/bin/env python3
"""DFS strategy design-of-experiments: which construction wins DOLLARS, per contest type.

    python3 scripts/dfs_strategy_doe.py run nba [--seeds 8] [--iters 100]   # build + score + report
    python3 scripts/dfs_strategy_doe.py report nba                           # report from the saved rows
    python3 scripts/dfs_strategy_doe.py forward nba                          # forward record of the leads

Why it exists (2026-11-14, after the independent system review, SYSTEM_REVIEW.md): every
lab and the nightly critique gate score stand-ins (MAE, field percentile, cash-line share)
and the review found none of them is tied to money. This scores what Adam is paid: each
lineup's DK payout in the REAL contest it would have been entered in (contests.db `payout`
table, rank among the real field's points), per $1 of entry fee.

It replaces paying for 150-entry dime GPPs as an experiment: the exported fields already hold
every opponent's score, so any lineup we could have built pre-lock can be "entered"
afterwards for free, in every graded contest at once.

Design
  - Board: the last pre-lock projection log per DK draft group, through production's pool
    transform (props blend + props-free shrink) with BOTH constants refit WITHOUT that date
    (nba_lineup_lab._lodo_names). A first run used production's all-dates constants and
    the replayed base GPP lineup made +22% where Adam's real entries in the same 40 contests
    made -17%: part of that gap was the blend having seen the night it was scoring.
  - Arms: a grid over the optimizer's construction knobs (percentile target, ownership
    tilt, team cap, game stack, minutes floor), one grid for cash, one for GPP.
  - Replicates: each (board, seed) draws ONE simulation that every arm is optimised on,
    so arms differ only by construction (common random numbers); seeds average out the
    optimizer's luck. One lineup per arm per seed = one single entry.
  - Score: $ returned per $1 in each contest of the matching type on that draft group
    (cash vs cash fields, GPP vs GPP fields, never pooled). ROI = mean - 1.
  - Unit: the slate date. Ranges are 90% bootstraps over dates; the arm-vs-baseline
    difference is paired by contest.
  - Honesty: the "pick the best arm" procedure is itself held out -- leave-one-date-out,
    the arm chosen on every other date is scored on the date left out. That number, not
    the best arm's in-sample ROI, is what a strategy change would earn.
  - Contest dimensions (field size, single vs multi entry, fee): ROI per bucket for the
    baseline arm, so contest selection is measured on the same footing.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

OUT = ROOT / "data" / "strategy_doe"
WORKERS = os.cpu_count() or 16

# ---- the arms -----------------------------------------------------------------------
# Production NBA (edge/dfs_nba_theory.py, 2026-11-14): cash = 30th pct, GPP = 90th pct,
# own_weight 0, no caps / stacks. Each grid holds production as "base".
CASH_ARMS: dict = {"base": {"pct": 30}}
for _p in (10, 20, 30, 50, 70):
    for _cap in (None, 3):
        for _mm in (0, 24):
            CASH_ARMS[f"p{_p}" + (f"_cap{_cap}" if _cap else "") + (f"_min{_mm}" if _mm else "")] = \
                {"pct": _p, "cap": _cap, "minmin": _mm}
CASH_ARMS["p30_chalk5"] = {"pct": 30, "own_weight": -5.0}
CASH_ARMS["p50_chalk5"] = {"pct": 50, "own_weight": -5.0}

GPP_ARMS: dict = {"base": {"pct": 90}}
for _p in (70, 80, 90, 95, 99):
    for _ow in (-5.0, 0.0, 5.0, 15.0):
        GPP_ARMS[f"p{_p}_own{_ow:+g}"] = {"pct": _p, "own_weight": _ow}
GPP_ARMS["p90_stack4"] = {"pct": 90, "stack": 4}
GPP_ARMS["p90_stack5"] = {"pct": 90, "stack": 5}
GPP_ARMS["p95_stack4_own5"] = {"pct": 95, "stack": 4, "own_weight": 5.0}
GPP_ARMS["p90_cap3"] = {"pct": 90, "cap": 3}
GPP_ARMS["p90_min24"] = {"pct": 90, "minmin": 24}

ARMS = {"cash": CASH_ARMS, "gpp": GPP_ARMS}

# ---- forward tracking (`forward`): decided 2026-11-14, counted only on nights after ----
# The NBA DOE's two leads, judged on nights nobody looked at when choosing them:
#  - production with the DK-props blend (critique cycle 27, live 2026-11-08): the replay put
#    it ~20 pts/lineup over the no-props model on 10/20-11/12, but the blend was chosen on
#    those nights, so only nights since it went live count;
#  - cash arm p70_cap3 (DOE 11/14: LODO pick +14% [-2,+33] over p30, 15 dates) -- PAPER only,
#    counted from the night after it was picked.
FORWARD = False
BACKDATE = False      # --backdate: props blend on pre-11/9 boards too (cycle 38: a what-if, not production)
FWD_START = {"production": "2026-11-08", "p70_cap3": "2026-11-15"}
FWD_ARMS = {"cash": {"base": CASH_ARMS["base"], "p70_cap3": CASH_ARMS["p70_cap3"]},
            "gpp": {"base": GPP_ARMS["base"]}}


# ---- sport adapters ---------------------------------------------------------------
class NBA:
    """Boards, sim, optimizer, truth and fields from scripts/nba_lineup_lab.py (the
    production replay), so this file adds only the experiment."""
    name = "nba"

    def __init__(self):
        import nba_lineup_lab as L
        self.L = L

    def boards(self) -> dict:
        F = self.L.fields()
        return {g: ps for g, ps in self.L.boards().items() if g in F}

    def fields(self) -> dict:
        return self.L.fields()

    def prepare(self, B: dict) -> None:
        """Register the per-date (leave-one-date-out) production variants. Runs in the parent
        before the pool forks, so workers inherit the registry and the market caches."""
        L = self.L
        reg = L.register_props(B)
        LODO.update(L._lodo_names(B, reg["rows"], None, L.shrink_rows(B)))

    def variant(self, ps: list[dict]) -> str:
        """The pool transform production actually ran on this board. Critique cycle 38
        (2026-11-21, 53 GPP entries / 25 dates): the props blend went live 11/9, and boards
        logged before it carry no `mkt`. Applying the (LODO) blend to those 17 dates replayed
        a model that never ran: +22% GPP ROI vs -32% for the faithful replay (model alone
        before 11/9, blend after) and -61% for the logged live lineup -- the whole "replay vs
        entered" gap of cycle 37. Default = faithful (same test as nba_lineup_lab.parity);
        BACKDATE=True (--backdate) keeps the old everywhere-blend as a what-if."""
        if FORWARD:
            return "shipped"
        if not BACKDATE and not any(p.get("mkt") is not None for p in ps):
            return "model_only"
        return LODO[ps[0]["date"]]

    def build(self, gid, ps, seed, iters, modes) -> dict:
        """{mode: {arm: [names]}} -- one sim per (board, seed), every arm optimised on it."""
        from edge import dfs_nba_theory as theory, dfs_opt_nba, dfs_opt_climb as climb
        # forward: production exactly as it ran (shipped constants, fitted before these nights)
        pool = self.L._set_pool(ps, self.variant(ps), gid)
        sim = theory.simulate(pool, n_sims=self.L.N_SIMS, seed=seed)
        theory.summarise(sim, pool)
        dfs_opt_nba.prepare(pool)
        out = {}
        for mode in modes:
            out[mode] = {}
            for arm, spec in (FWD_ARMS if FORWARD else ARMS)[mode].items():
                excl = {p["name"] for p in pool if spec.get("minmin") and (p.get("minutes") or 0) < spec["minmin"]}
                best = climb.search(pool, sim, dfs_opt_nba.NBA, spec["pct"],
                                    key="ceil" if mode == "gpp" else "floor", iters=iters, seed=seed,
                                    own_weight=spec.get("own_weight", 0.0), exclude=excl or None,
                                    extra_valid=_valid(spec))
                r = best is not None and climb.result(pool, sim, best, dfs_opt_nba.NBA, theory.describe)
                out[mode][arm] = [p["name"] for _, p in r["slots"]] if r else None
        return out

    def score(self, gid, ps, lineup) -> float | None:
        return self.L.score(gid, ps, lineup) if lineup else None

    def payout(self, cid, field, x) -> float:
        return self.L.payout(cid, field, x)


SPORTS = {"nba": NBA}
LODO: dict = {}          # {date: registered variant name}, filled by the adapter's prepare()
_PARENT = None           # the parent's adapter: forked workers inherit it, registry and all


def _valid(spec: dict):
    cap, stack = spec.get("cap"), spec.get("stack")
    if not cap and not stack:
        return None

    def ok(rows):
        if cap and max(Counter(r["team"] for r in rows).values()) > cap:
            return False
        return not stack or max(Counter(r["game"] for r in rows).values()) >= stack
    return ok


# ---- run ------------------------------------------------------------------------------
_S = None


def _job(args):
    global _S
    sport, gid, ps, seed, iters, modes = args
    if _S is None:
        _S = _PARENT or SPORTS[sport]()
    return gid, seed, _S.build(gid, ps, seed, iters, modes)


def _arms_key() -> str:
    import hashlib
    return hashlib.sha1(json.dumps(ARMS, sort_keys=True, default=str).encode()).hexdigest()[:8]


def run(sport: str, seeds: int, iters: int) -> list[dict]:
    global _PARENT
    S = _PARENT = SPORTS[sport]()
    B, F = S.boards(), S.fields()
    cache = OUT / f"cache_{sport}_{'lodo' if BACKDATE else 'asran'}_{_arms_key()}_i{iters}"
    cache.mkdir(parents=True, exist_ok=True)
    built, todo = {}, []
    for gid, ps in B.items():
        modes = sorted({t for _, t, _, _ in F[gid]})
        for s in range(seeds):
            f = cache / f"{gid}-{s}.json"
            if f.exists():
                built[(gid, s)] = json.loads(f.read_text())
            else:
                todo.append((sport, gid, ps, s, iters, modes))
    todo.sort(key=lambda t: -len(t[2]))
    if todo:
        S.prepare(B)
        print(f"doe: {len(todo)} (board, seed) jobs x {len(CASH_ARMS)} cash / {len(GPP_ARMS)} GPP arms "
              f"on {min(WORKERS, len(todo))} processes", file=sys.stderr, flush=True)
        with ProcessPoolExecutor(min(WORKERS, len(todo))) as ex:
            for i, (gid, s, lu) in enumerate(ex.map(_job, todo), 1):
                built[(gid, s)] = lu
                (cache / f"{gid}-{s}.json").write_text(json.dumps(lu))
                if i % 20 == 0:
                    print(f"  {i}/{len(todo)}", file=sys.stderr, flush=True)
    meta = contest_meta(sport)
    rows = []
    for (gid, s), lu in built.items():
        ps = B[gid]
        for cid, typ, field, line in F[gid]:
            for arm, names in lu.get(typ, {}).items():
                x = S.score(gid, ps, names)
                if x is None:
                    continue
                rows.append({"date": ps[0]["date"], "gid": gid, "contest": cid, "mode": typ, "arm": arm,
                             "seed": s, "points": x, "ret": S.payout(cid, field, x),
                             "cashed": bool(x >= line), "pctile": float((field < x).mean()), **meta.get(cid, {})})
    (OUT / f"rows_{sport}.json").write_text(json.dumps(rows))
    return rows


def contest_meta(sport: str) -> dict:
    c = sqlite3.connect(ROOT / "data" / "contests.db")
    out = {}
    for cid, field, fee, mx in c.execute("select contest_id, field, entry_fee, max_entries from contest "
                                         "where sport=?", (sport.upper(),)):
        out[cid] = {"field": field or 0, "fee": fee or 0, "single": mx == 1}
    return out


# ---- analysis -------------------------------------------------------------------------
def _boot(by_date: dict, n=4000, seed=0) -> tuple:
    """(mean, lo, hi) of the per-contest mean, resampling DATES (90%)."""
    dates = list(by_date)
    if not dates:
        return (float("nan"),) * 3
    rng = np.random.default_rng(seed)
    allv = np.concatenate([by_date[d] for d in dates])
    bs = []
    for _ in range(n):
        pick = rng.integers(0, len(dates), len(dates))
        bs.append(np.concatenate([by_date[dates[i]] for i in pick]).mean())
    return float(allv.mean()), float(np.percentile(bs, 5)), float(np.percentile(bs, 95))


def per_contest(rows, mode) -> dict:
    """{arm: {contest: (date, mean ret over seeds)}}."""
    acc = defaultdict(lambda: defaultdict(list))
    dates = {}
    for r in rows:
        if r["mode"] == mode:
            acc[r["arm"]][r["contest"]].append(r["ret"])
            dates[r["contest"]] = r["date"]
    return {a: {c: (dates[c], float(np.mean(v))) for c, v in cs.items()} for a, cs in acc.items()}


def _by_date(pc: dict, contests=None, minus: dict | None = None) -> dict:
    out = defaultdict(list)
    for c, (d, v) in pc.items():
        if contests is not None and c not in contests:
            continue
        if minus is not None:
            if c not in minus:
                continue
            v = v - minus[c][1]
        out[d].append(v)
    return {d: np.array(v) for d, v in out.items()}


def report(sport: str) -> dict:
    rows = json.loads((OUT / f"rows_{sport}.json").read_text())
    summary = {"sport": sport, "modes": {}}
    for mode in ("cash", "gpp"):
        pc = per_contest(rows, mode)
        if "base" not in pc:
            continue
        base = pc["base"]
        n_dates = len({d for d, _ in base.values()})
        print(f"\n=== {sport.upper()} {mode.upper()}: {len(base)} contests / {n_dates} dates, "
              f"{len({r['seed'] for r in rows})} seeds; ROI per $1, 90% range over dates ===")
        print(f"{'arm':22s} {'ROI':>8s} {'range':>18s}   {'vs base':>8s} {'range':>18s}  cash%")
        table = []
        for arm, cs in pc.items():
            m, lo, hi = _boot(_by_date(cs))
            dm, dlo, dhi = _boot(_by_date(cs, minus=base))
            cashed = np.mean([r["cashed"] for r in rows if r["mode"] == mode and r["arm"] == arm])
            table.append({"arm": arm, "roi": m - 1, "lo": lo - 1, "hi": hi - 1,
                          "diff": dm, "dlo": dlo, "dhi": dhi, "cash_rate": float(cashed)})
        table.sort(key=lambda t: -t["roi"])
        for t in table:
            flag = " *" if t["dlo"] > 0 else (" -" if t["dhi"] < 0 else "")
            print(f"{t['arm']:22s} {t['roi']:+8.1%} [{t['lo']:+7.1%},{t['hi']:+7.1%}]   "
                  f"{t['diff']:+8.1%} [{t['dlo']:+7.1%},{t['dhi']:+7.1%}]  {t['cash_rate']:.0%}{flag}")
        lodo = _lodo(pc)
        print(f"\nleave-one-date-out 'pick the best arm' ROI {lodo['roi']:+.1%} "
              f"[{lodo['lo']:+.1%},{lodo['hi']:+.1%}] vs base {lodo['base_roi']:+.1%}; "
              f"diff {lodo['diff']:+.1%} [{lodo['dlo']:+.1%},{lodo['dhi']:+.1%}]; picks {lodo['picks']}")
        dims = _dims(rows, mode, pc)
        print("contest dimensions (base arm and LODO pick):")
        for d in dims:
            print(f"  {d['dim']:8s} {d['bucket']:14s} n={d['n']:3d} c / {d['dates']:2d} d  base {d['roi']:+7.1%} "
                  f"[{d['lo']:+7.1%},{d['hi']:+7.1%}]  best-arm {d['best_arm']} {d['best_roi']:+.1%}")
        summary["modes"][mode] = {"contests": len(base), "dates": n_dates, "arms": table, "lodo": lodo, "dims": dims}
    (OUT / f"summary_{sport}.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def _lodo(pc: dict) -> dict:
    """Held-out arm choice: for each date, the arm with the best mean ret on the OTHER dates."""
    dates = sorted({d for d, _ in pc["base"].values()})
    got, base, picks = defaultdict(list), defaultdict(list), Counter()
    for d in dates:
        best = max(pc, key=lambda a: np.mean([v for (dd, v) in pc[a].values() if dd != d] or [-9]))
        picks[best] += 1
        for c, (dd, v) in pc[best].items():
            if dd == d and c in pc["base"]:
                got[d].append(v)
                base[d].append(pc["base"][c][1])
    g = {d: np.array(v) for d, v in got.items()}
    b = {d: np.array(v) for d, v in base.items()}
    m, lo, hi = _boot(g)
    bm = _boot(b)[0]
    dm, dlo, dhi = _boot({d: g[d] - b[d] for d in g})
    return {"roi": m - 1, "lo": lo - 1, "hi": hi - 1, "base_roi": bm - 1, "diff": dm, "dlo": dlo, "dhi": dhi,
            "picks": dict(picks.most_common())}


def _dims(rows, mode, pc) -> list[dict]:
    meta = {r["contest"]: r for r in rows if r["mode"] == mode}

    def bucket_field(n):
        return "<=25" if n <= 25 else "26-100" if n <= 100 else "101-500" if n <= 500 else "501+"
    dims = {"field": lambda m: bucket_field(m.get("field", 0)),
            "entry": lambda m: "single" if m.get("single") else "multi",
            "fee": lambda m: "<=$1" if m.get("fee", 0) <= 1 else "$1-5" if m.get("fee", 0) <= 5 else ">$5"}
    out = []
    for dim, f in dims.items():
        groups = defaultdict(set)
        for c, m in meta.items():
            groups[f(m)].add(c)
        for b, cs in sorted(groups.items()):
            bd = _by_date(pc["base"], cs)
            m, lo, hi = _boot(bd)
            best = max(pc, key=lambda a: np.mean([v for c, (_, v) in pc[a].items() if c in cs] or [-9]))
            bm = np.mean([v for c, (_, v) in pc[best].items() if c in cs])
            out.append({"dim": dim, "bucket": b, "n": len(cs), "dates": len(bd), "roi": m - 1, "lo": lo - 1,
                        "hi": hi - 1, "best_arm": best, "best_roi": float(bm - 1)})
    return out


def forward(sport: str, seeds: int, iters: int) -> dict:
    """Forward record since FWD_START: Adam's real entries, production as it ran, the paper
    cash arm, and late-swap misses (a 0-point player in Adam's entry: was he still projected
    on the app's last pre-lock board, i.e. news after the last build?)."""
    global FORWARD, _PARENT
    from edge.names import norm
    FORWARD = True
    S = _PARENT = SPORTS[sport]()
    start = min(FWD_START.values())
    B = {g: ps for g, ps in S.boards().items() if ps[0]["date"] >= start}
    F = S.fields()
    cache = OUT / f"cache_{sport}_fwd_i{iters}"
    cache.mkdir(parents=True, exist_ok=True)
    todo, built = [], {}
    for gid, ps in B.items():
        modes = sorted({t for _, t, _, _ in F[gid]})
        for s in range(seeds):
            f = cache / f"{gid}-{s}.json"
            if f.exists():
                built[(gid, s)] = json.loads(f.read_text())
            else:
                todo.append((sport, gid, ps, s, iters, modes))
    if todo:
        with ProcessPoolExecutor(min(WORKERS, len(todo))) as ex:
            for gid, s, lu in ex.map(_job, todo):
                built[(gid, s)] = lu
                (cache / f"{gid}-{s}.json").write_text(json.dumps(lu))
    c = sqlite3.connect(ROOT / "data" / "contests.db")
    nights = []
    for gid, ps in sorted(B.items(), key=lambda kv: kv[1][0]["date"]):
        on_board = {norm(p["name"]) for p in ps}
        truth = S.L.dk_points(gid)
        for cid, typ, field, _ in F[gid]:
            fee = c.execute("select entry_fee from contest where contest_id=?", (cid,)).fetchone()[0] or 0
            arms = defaultdict(list)
            for s in range(seeds):
                for arm, names in (built.get((gid, s)) or {}).get(typ, {}).items():
                    x = S.score(gid, ps, names)
                    if x is not None:
                        arms[arm].append(S.payout(cid, field, x))
            adam, misses = [], []
            for eid, pts in c.execute("select entry_id, points from entry where contest_id=? and is_adam=1", (cid,)):
                adam.append(S.payout(cid, field, pts))
                for (pn,) in c.execute("select player_norm from entry_player where contest_id=? and entry_id=?",
                                       (cid, eid)):
                    if not truth.get(pn):
                        misses.append({"player": pn, "on_last_board": pn in on_board})
            nights.append({"date": ps[0]["date"], "contest": cid, "mode": typ, "fee": fee,
                           "adam": adam, "arms": {a: float(np.mean(v)) for a, v in arms.items()},
                           "late_swap_misses": misses})
    print(f"=== {sport.upper()} forward record (ROI per $1; production since {FWD_START['production']}, "
          f"paper cash arm since {FWD_START['p70_cap3']}) ===")
    summary = {"sport": sport, "start": FWD_START, "nights": nights, "totals": {}}
    for mode in ("cash", "gpp"):
        ns = [n for n in nights if n["mode"] == mode]
        dates = sorted({n["date"] for n in ns})
        tot = {"dates": len(dates), "contests": len(ns),
               "adam": _roi([r for n in ns for r in n["adam"]]),
               "adam_entries": sum(len(n["adam"]) for n in ns),
               "production": _roi([n["arms"].get("base") for n in ns])}
        if mode == "cash":
            ps_ = [n for n in ns if n["date"] >= FWD_START["p70_cap3"]]
            tot["p70_cap3_paper"] = _roi([n["arms"].get("p70_cap3") for n in ps_])
            tot["production_same_nights"] = _roi([n["arms"].get("base") for n in ps_])
            tot["p70_cap3_dates"] = len({n["date"] for n in ps_})
        summary["totals"][mode] = tot
        print(f"{mode}: {tot['dates']} dates / {tot['contests']} contests"
              + ("  (not enough data: < 5 dates)" if tot["dates"] < 5 else ""))
        print(f"   Adam real {tot['adam']} on {tot['adam_entries']} entries | production replay {tot['production']}")
        if mode == "cash":
            print(f"   paper p70_cap3 {tot['p70_cap3_paper']} vs production {tot['production_same_nights']} "
                  f"on the same {tot['p70_cap3_dates']} dates")
    miss = [(n["date"], m["player"], m["on_last_board"]) for n in nights for m in n["late_swap_misses"]]
    print(f"late-swap misses (0-pt player in Adam's entry): {len(miss)}")
    for d, p, ob in sorted(set(miss)):
        print(f"   {d} {p}: " + ("still projected on the last board -> news after the last build" if ob
                                  else "already off the last board -> entered with a known OUT"))
    (OUT / f"forward_{sport}.json").write_text(json.dumps(summary, indent=1))
    return summary


def _roi(rets: list) -> str | None:
    v = [r for r in rets if r is not None]
    return f"{np.mean(v) - 1:+.0%} (n={len(v)})" if v else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("run", "report", "forward"))
    ap.add_argument("sport", choices=sorted(SPORTS))
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--iters", type=int, default=100)
    ap.add_argument("--backdate", action="store_true",
                    help="what-if: props blend on boards logged before it went live (pre-11/9)")
    a = ap.parse_args()
    global BACKDATE
    BACKDATE = a.backdate
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "forward":
        forward(a.sport, a.seeds, a.iters)
        return 0
    if a.cmd == "run":
        run(a.sport, a.seeds, a.iters)
    report(a.sport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
