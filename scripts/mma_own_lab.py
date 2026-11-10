#!/usr/bin/env python3
"""Held-out harness for the MMA FIELD model (ownership + the field's score lines).
Built 2026-11-10, MMA critique cycle 2 (MMA_CRITIQUE_LOG.md).

    python3 scripts/mma_own_lab.py report               # every variant, leave-one-slate-out
    python3 scripts/mma_own_lab.py report --variant tau
    python3 scripts/mma_own_lab.py fppf                 # rebuilt FPPF vs the logged DK FPPF
    python3 scripts/mma_own_lab.py gate OUT             # one JSON line per fighter-contest

WHAT IS MEASURED. Both MMA objectives (edge/dfs_mma_theory.py) score our lineups
against a simulated field: a softmax over every legal lineup on the PUBLIC
projection (ours mixed with DK's lobby FPPF, weight PUBLIC_FPPF_WEIGHT), with
temperature FIELD_TAU_CASH / FIELD_TAU_GPP. Those three numbers were fitted on
ONE 118-entry Captain slate (2026-09-26). This lab scores the field model against
every real DK MMA field in data/contests.db:
  * PRIMARY  per-fighter ownership MAE (%Drafted points) per contest, averaged
             over a slate's contests, then over slates. Cash and GPP are never
             pooled (CLAUDE.md); contest type comes from contests.db (DK-tagged),
             never the file name.
  * SECONDARY the field's score line under the REALISED fighter points (DK's own
             FPTS): the simulated field's weighted quantile at the contest's own
             paid fraction (cash) or top 1% (GPP), against the real contest's
             line. A field model that is too sharp puts the line too high.

THE BOARD THE FIELD SAW. 9/26 (Captain, gid 154126) and 11/07 (gid 156055) use
the logged live board (data/dfs_proj_log_mma.csv, last logged build: the same
row mma_calibration --fit-ownership uses). 10/03 (154125) and 10/10 (154571)
were never logged, so they are rebuilt with scripts/mma_lab.load_rows (the
archived DK Sportsbook book at lock through production's pieces; parity with the
live board corr 1.000, MAE 0.24 pts on 4 cards). Salaries come from contests.db
slate_player; DK's FPPF for a rebuilt card is the fighter's UFC career mean DK
points before the card from UFCStats (`fppf` measures that against the logged
DK FPPF on the logged cards). A debut has no FPPF (DK prints none either).

THE SPLIT: leave one SLATE out. Every constant a variant fits is fitted on the
other slates' contests of that type (the shared FPPF weight on both types) and
scored on the held-out slate. 4 slates -> "not enough data" (< 5) on every
verdict; 'better on >= 3 of 4 held-out slates in each type' is the ship bar the
scout set (MMA_CRITIQUE_LOG.md cycle 2).
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np                                    # noqa: E402

from edge import dfs_mma_theory as theory, dfs_opt_mma, mma   # noqa: E402
from edge.names import norm                           # noqa: E402

DB = ROOT / "data" / "contests.db"
PROJ_LOG = ROOT / "data" / "dfs_proj_log_mma.csv"
CACHE = ROOT / "data" / "cache" / "mma_own_lab_boards.json"
MATCH = 0.72                                 # = edge/dfs_run_mma.BOUT_MATCH
MIN_SLATES = 5
ALPHAS = (0.0, 0.15, 0.3, 0.5, 0.7, 1.0)
TAUS = (3, 5, 8, 12, 16, 20, 25, 30, 40, 60)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def contests() -> list[dict]:
    """Every MMA contest in contests.db with its field's %Drafted and FPTS."""
    c = sqlite3.connect(DB)
    out = []
    for cid, ctype, name, field, dg, date, paid in c.execute(
            "select contest_id, type, name, field, draft_group, slate_date, places_paid "
            "from contest where sport='MMA' and type in ('cash','gpp') order by slate_date"):
        pr = {pn: (pct or 0.0, fp) for pn, pct, fp in c.execute(
            "select player_norm, pct_drafted, fpts from player_result where contest_id=?", (cid,))}
        pts = [p for (p,) in c.execute(
            "select points from entry where contest_id=? and points is not null "
            "order by points desc", (cid,))]
        out.append({"cid": cid, "type": ctype, "name": name, "field": field or len(pts),
                    "dg": int(dg), "date": date, "paid": paid, "own": pr, "points": pts})
    return out


def _logged_boards() -> dict:
    """{gid: {norm(name): row}} -- last logged build per fighter."""
    out: dict = collections.defaultdict(dict)
    if PROJ_LOG.exists():
        for r in csv.DictReader(PROJ_LOG.open(newline="")):
            out[int(r["gid"])][norm(r["fighter"])] = r
    return out


def _career_fppf(fights: list, before: dt.date) -> dict:
    """{fighter_key: mean DK points over UFC fights before `before`}."""
    acc = collections.defaultdict(list)
    for f in fights:
        if f["date"] and f["date"] < before and not f["nc"]:
            for me in f["f"]:
                acc[me["key"]].append(me["dk"])
    return {k: float(np.mean(v)) for k, v in acc.items()}


def _rebuilt_board(dg: int, date: str, slate: list, lab_rows: list, fppf: dict) -> list | None:
    """Board for a card with no live log: lab projection matched 1:1 to DK names."""
    lr = [r for r in lab_rows if r["card"] == date]
    pool, used = [], set()
    for pn, name, sal, bout in slate:
        best = max(((mma.name_similarity(name, r["fighter"]), i) for i, r in enumerate(lr)
                    if i not in used), default=(0.0, None))
        if best[1] is None or best[0] < MATCH:
            return None                       # a fighter the book never priced: skip the card
        used.add(best[1])
        r = lr[best[1]]
        pool.append({"norm": pn, "name": name, "salary": int(sal), "proj": float(r["proj"]),
                     "dk_fppf": fppf.get(r["key"]), "bout": bout})
    return pool


def boards(refresh: bool = False) -> dict:
    """{gid: (source, pool)}: the board each DK field saw (see the docstring)."""
    if CACHE.exists() and not refresh:
        return {int(k): tuple(v) for k, v in json.loads(CACHE.read_text()).items()}
    c = sqlite3.connect(DB)
    logged = _logged_boards()
    lab_rows = fights = None
    out = {}
    for dg, date in c.execute("select distinct draft_group, slate_date from contest "
                              "where sport='MMA' order by slate_date"):
        slate = c.execute("select player_norm, name, salary, bout from slate_player "
                          "where draft_group=?", (dg,)).fetchall()
        if dg in logged:
            lb = logged[dg]
            pool = []
            for pn, name, sal, bout in slate:
                r = lb.get(norm(name))
                if r is None:
                    continue                  # scratched before the last build
                # the log's salary, not slate_player's: on a Captain slate
                # slate_player holds the CPT price (1.5x)
                pool.append({"norm": pn, "name": name, "salary": int(float(r["salary"])),
                             "proj": float(r["proj"] or 0.0),
                             "dk_fppf": float(r["dk_fppf"]) if r["dk_fppf"] else None,
                             "bout": r["bout"]})
            out[dg] = ("live log", pool)
            continue
        if lab_rows is None:
            import mma_lab
            lab_rows = mma_lab.load_rows()
            fights = mma.fights()
        pool = _rebuilt_board(dg, date, slate, lab_rows,
                              _career_fppf(fights, dt.date.fromisoformat(date)))
        if pool:
            # bout = the fighter's opponent pairing; slate_player carries none for
            # MMA, so pair by the book's rows (same card date, same lab bout).
            out[dg] = ("rebuilt from book", _pair_bouts(pool, lab_rows, date))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({str(k): list(v) for k, v in out.items()}))
    return out


def _pair_bouts(pool: list, lab_rows: list, date: str) -> list:
    """lab rows come two per bout, in order: give both corners one bout id."""
    lr = [r for r in lab_rows if r["card"] == date]
    bout_of = {}
    for i in range(0, len(lr) - 1, 2):
        b = f"{lr[i]['fighter']} vs {lr[i + 1]['fighter']}"
        bout_of[lr[i]["fighter"]] = bout_of[lr[i + 1]["fighter"]] = b
    for d in pool:
        best = max(bout_of, key=lambda n: mma.name_similarity(d["name"], n))
        d["bout"] = bout_of[best]
    return pool


# ---------------------------------------------------------------------------
# The field model, exactly as production computes it
# ---------------------------------------------------------------------------
class Slate:
    def __init__(self, dg: int, source: str, pool: list, captain: bool):
        self.dg, self.source, self.pool, self.captain = dg, source, pool, captain
        sal = np.array([d["salary"] for d in pool])
        self.lineups = (dfs_opt_mma.legal_captain_lineups(
            sal, np.round(dfs_opt_mma.CPT_MULT * sal).astype(int)) if captain
            else dfs_opt_mma.legal_lineups(sal))
        opp = np.full(len(pool), -1)
        by = collections.defaultdict(list)
        for i, d in enumerate(pool):
            by[d["bout"]].append(i)
        for ids in by.values():
            if len(ids) == 2:
                opp[ids[0]], opp[ids[1]] = ids[1], ids[0]
        self.opp = opp
        self._memo: dict = {}

    def field(self, alpha: float, tau: float):
        k = (alpha, tau)
        if k not in self._memo:
            keep = theory.PUBLIC_FPPF_WEIGHT
            theory.PUBLIC_FPPF_WEIGHT = alpha
            try:
                self._memo[k] = theory.field_model(self.pool, self.lineups, self.opp,
                                                   tau, self.captain)
            finally:
                theory.PUBLIC_FPPF_WEIGHT = keep
        return self._memo[k]


def _wquantile(x: np.ndarray, w: np.ndarray, q: float) -> float:
    o = np.argsort(x)
    cw = np.cumsum(w[o])
    return float(x[o][min(len(x) - 1, np.searchsorted(cw, q * cw[-1]))])


def score_contest(s: Slate, ct: dict, alpha: float, tau: float) -> dict:
    own, fl, w = s.field(alpha, tau)
    act = np.array([ct["own"].get(d["norm"], (0.0, None))[0] for d in s.pool])
    fp = np.array([ct["own"].get(d["norm"], (0.0, 0.0))[1] or 0.0 for d in s.pool])
    tot = fp[fl].sum(axis=1)
    if s.captain:
        tot = tot + (dfs_opt_mma.CPT_MULT - 1.0) * fp[fl[:, 0]]
    pts = ct["points"]
    if ct["type"] == "cash":
        frac = (ct["paid"] or 0) / max(1, ct["field"]) or theory.CASH_PAID_FRACTION
    else:
        frac = theory.GPP_TOP_FRACTION
    k = max(1, int(round(frac * len(pts))))
    real_line = pts[k - 1] if pts else float("nan")
    sim_line = _wquantile(tot, w, 1.0 - frac)
    return {"own_mae": float(np.mean(np.abs(own - act))),
            "own_bias": float(np.mean(own - act)),
            "line_err": sim_line - real_line, "pred": own, "actual": act}


# ---------------------------------------------------------------------------
# Variants: fit(train contests by type) -> {type: (alpha, tau)}
# ---------------------------------------------------------------------------
def _shipped(_train, _slates):
    return {"cash": (theory.PUBLIC_FPPF_WEIGHT, theory.FIELD_TAU_CASH),
            "gpp": (theory.PUBLIC_FPPF_WEIGHT, theory.FIELD_TAU_GPP)}


def _loss(cts, slates, alpha, tau) -> float:
    """Mean over slates of the slate's mean contest ownership MAE."""
    by = collections.defaultdict(list)
    for ct in cts:
        by[ct["dg"]].append(score_contest(slates[ct["dg"]], ct, alpha, tau)["own_mae"])
    return float(np.mean([np.mean(v) for v in by.values()]))


def _tau_only(train, slates):
    """tau per contest type on train; FPPF weight stays at the shipped value."""
    a = theory.PUBLIC_FPPF_WEIGHT
    out = {}
    for t in ("cash", "gpp"):
        cts = [c for c in train if c["type"] == t]
        out[t] = (a, min(TAUS, key=lambda tau: _loss(cts, slates, a, tau))) if cts \
            else _shipped(None, None)[t]
    return out


def _joint(train, slates):
    """One FPPF weight (production has one) + tau per type, all fitted on train:
    the weight minimises cash + GPP loss with each type's best tau."""
    best = None
    for a in ALPHAS:
        taus, tot = {}, 0.0
        for t in ("cash", "gpp"):
            cts = [c for c in train if c["type"] == t]
            l, tau = min((_loss(cts, slates, a, tau), tau) for tau in TAUS)
            taus[t], tot = tau, tot + l
        if best is None or tot < best[0]:
            best = (tot, a, taus)
    _, a, taus = best
    return {t: (a, taus[t]) for t in taus}


VARIANTS = {"shipped": _shipped, "tau": _tau_only, "joint": _joint}


def load() -> tuple[list, dict]:
    cts = contests()
    bs = boards()
    slates = {}
    for dg, (src, pool) in bs.items():
        captain = any(dg == c["dg"] and "Captain" in c["name"] for c in cts)
        slates[dg] = Slate(dg, src, pool, captain)
    return [c for c in cts if c["dg"] in slates], slates


def loso(cts, slates, variant: str) -> list[dict]:
    out = []
    for dg in sorted({c["dg"] for c in cts}):
        train = [c for c in cts if c["dg"] != dg]
        params = VARIANTS[variant](train, slates)
        for ct in (c for c in cts if c["dg"] == dg):
            a, tau = params[ct["type"]]
            r = score_contest(slates[dg], ct, a, tau)
            out.append({**r, "cid": ct["cid"], "dg": dg, "date": ct["date"],
                        "type": ct["type"], "field": ct["field"], "alpha": a, "tau": tau})
    return out


def _by_slate(res, t, col="own_mae"):
    by = collections.defaultdict(list)
    for r in res:
        if r["type"] == t:
            by[r["dg"]].append(r[col])
    return {k: float(np.mean(v)) for k, v in by.items()}


def report(variant: str | None) -> None:
    cts, slates = load()
    for dg, s in sorted(slates.items()):
        print(f"slate {dg}: {s.source}, {len(s.pool)} fighters, "
              f"{'Captain' if s.captain else 'Classic'}, {len(s.lineups):,} legal lineups")
    names = [variant] if variant else list(VARIANTS)
    res = {v: loso(cts, slates, v) for v in (set(names) | {"shipped"})}
    base = res["shipped"]
    for t in ("cash", "gpp"):
        n_ct = sum(1 for c in cts if c["type"] == t)
        n_sl = len({c["dg"] for c in cts if c["type"] == t})
        n_e = sum(c["field"] for c in cts if c["type"] == t)
        print(f"\n{t.upper()}: {n_ct} contests / {n_sl} slates / {n_e:,} entries"
              + ("  -- NOT ENOUGH DATA (< 5 slates)" if n_sl < MIN_SLATES else ""))
        print(f"  {'variant':<9}{'own MAE':>9}{'bias':>7}{'|line err|':>11}"
              f"{'line bias':>10}{'Δ vs shipped':>14}{'slates better':>15}")
        b0 = _by_slate(base, t)
        for v in names if "shipped" in names else ["shipped"] + names:
            rs = res[v]
            m = _by_slate(rs, t)
            le = _by_slate(rs, t, "line_err")
            bias = _by_slate(rs, t, "own_bias")
            d = {k: m[k] - b0[k] for k in m}
            better = sum(1 for x in d.values() if x < -1e-9)
            print(f"  {v:<9}{np.mean(list(m.values())):9.2f}{np.mean(list(bias.values())):+7.2f}"
                  f"{np.mean(np.abs(list(le.values()))):11.1f}{np.mean(list(le.values())):+10.1f}"
                  f"{np.mean(list(d.values())):+14.2f}{better:>11}/{len(d)}")
        print("  per held-out slate (own MAE; line err; fitted alpha/tau):")
        for dg in sorted(b0):
            cells = []
            for v in names if "shipped" in names else ["shipped"] + names:
                r = [x for x in res[v] if x["dg"] == dg and x["type"] == t]
                cells.append(f"{v} {np.mean([x['own_mae'] for x in r]):5.2f} "
                             f"{np.mean([x['line_err'] for x in r]):+6.1f} "
                             f"({r[0]['alpha']}/{r[0]['tau']:g})")
            print(f"    {dg} {r[0]['date']}: " + " | ".join(cells))


def fppf_check() -> None:
    """Rebuilt FPPF (UFC career mean before the card) vs DK's logged FPPF."""
    fights = mma.fights()
    d = []
    for r in csv.DictReader(PROJ_LOG.open(newline="")):
        if not r["dk_fppf"]:
            continue
        cf = _career_fppf(fights, dt.date.fromisoformat(r["date"]))
        if r["key"] in cf:
            d.append((cf[r["key"]], float(r["dk_fppf"])))
    a = np.array(d)
    print(f"n={len(a)} logged fighter-boards: rebuilt - DK mean {np.mean(a[:, 0] - a[:, 1]):+.2f}, "
          f"MAE {np.mean(np.abs(a[:, 0] - a[:, 1])):.2f}, corr {np.corrcoef(a.T)[0, 1]:.3f}")


def gate(path: str) -> None:
    """Rows for harness/critique_gate.py: PRODUCTION's field ownership (the
    module constants as they stand) per fighter-contest, clustered by slate,
    state = contest type."""
    cts, slates = load()
    params = _shipped(None, None)
    n = 0
    with open(path, "w") as fh:
        for ct in cts:
            a, tau = params[ct["type"]]
            r = score_contest(slates[ct["dg"]], ct, a, tau)
            for d, p, y in zip(slates[ct["dg"]].pool, r["pred"], r["actual"]):
                fh.write(json.dumps({"key": f"{ct['cid']}|{d['norm']}", "cluster": str(ct["dg"]),
                                     "split": "primary", "state": ct["type"],
                                     "pred": float(p), "actual": float(y)}) + "\n")
                n += 1
    print(f"{n:,} gate rows -> {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["report", "fppf", "gate", "rebuild"])
    ap.add_argument("out", nargs="?")
    ap.add_argument("--variant", choices=sorted(VARIANTS))
    a = ap.parse_args()
    if a.cmd == "gate":
        if not a.out:
            ap.error("gate needs an output path")
        gate(a.out)
    elif a.cmd == "fppf":
        fppf_check()
    elif a.cmd == "rebuild":
        bs = boards(refresh=True)
        print({k: (v[0], len(v[1])) for k, v in bs.items()})
    else:
        report(a.variant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
