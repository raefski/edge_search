#!/usr/bin/env python3
"""Held-out harness for the NASCAR finishing-position model -- the dfs-critique
loop's measuring stick (NASCAR_CRITIQUE_LOG.md).

THE METRIC: finishing position, per driver-race, from the simulator's mean
finish (DNFs included, exactly as the lineup sees them): MAE, RMSE, bias,
mean per-race Spearman rank correlation, and place differential MAE
(start - finish, the DK component the build is really paid for). Sliced by
track type, because the four track types are four different games
(NASCAR_STATUS.md section 4).

THE SPLIT, by RACE (a race is one realisation; every driver in it shares the
wrecks): fit on 2022-2024 Cup, report on 2025-2026. Only the finish model
(FINISH_MODEL) and the DNF table (DNF_RATE) are refitted on train -- they are
everything that moves a finish. The shipped constants were fitted on
2022-2026, i.e. on the test seasons too, so the "shipped as-is" row is
in-sample and printed only as a reference; the honest baseline is "refit on
train".

LIVE-FAITHFUL: E[finish] and P(DNF) come from production
(edge.nascar_sim.expected_finish / dnf_probability) with the train-fitted
tables swapped in; only the ranking step of simulate() is repeated here, the
same three lines (latent + noise, DNFs to the back, argsort).

Variants (critique cycles) are VARIANTS entries: a function
(train_rows) -> predictor(race_rows) -> array of mean finishes.

DK POINTS (`dk`, added 10/26): the quantity the lineup is actually paid on.
Runs production nascar_sim.simulate() per test race -- finish + place
differential + laps led + fastest laps -- with the train-fitted FINISH_MODEL
and DNF_RATE swapped in, and scores each driver's MEAN simulated DK points
against his real `dk`. Race inputs are what the live build passes
(dfs_run_nascar.build_slate): laps = the race distance (the loop data's
actual_laps stands in for scheduled_laps; they differ only in overtime) and
no green_laps, as live has no caution count before the race (simulate() then
sizes the fastest-lap pool with FAST_FRAC; before cycle 4 both passed the race
distance -- variant pre_cycle4). LAP_MODEL (lap-led/fastest-lap concentration) is production's as-is,
measured on 2022-2026 -- in-sample on the test seasons, stated, not refitted.
Reports MAE/RMSE/bias/per-race Spearman of DK points, the same split by
component (finish+PD / laps-led pts / fastest-lap pts), and the 10-90% band
and 25th/90th floor/ceiling coverage.

Run:  python3 scripts/nascar_lab.py report [--variant baseline|running_pos]
      python3 scripts/nascar_lab.py dk      [--variant ...]   # DK points
      python3 scripts/nascar_lab.py fit     # production FINISH_MODEL, 2022-2026
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import nascar_fit as F  # noqa: E402
from edge import nascar_sim as S  # noqa: E402

TRAIN = (2022, 2023, 2024)
TEST = (2025, 2026)
#: Noise scale on the non-DNF latent, chosen on the inner 2022-23 -> 2024
#: split. Cycle 2 picked 0.7 by MAE (the median's loss); cycle 3 re-chose it
#: by the DK 10-90% band hitting 80% (inner 78.4 / 80.3 / 81.7% at k 0.7 /
#: 0.85 / 1.0), because lineups sum MEANS and read floors/ceilings off the
#: spread -- 0.7 left top-10 starters ~1.5 places too good.
COND_K = 0.85


def load_all() -> list[dict]:
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        raw = F.load(list(TRAIN + TEST))
    add_running_form(raw)
    return F.add_form(raw)


def add_running_form(rows: list[dict]) -> None:
    """Attach production running-position form (nascar_sim.running_form) to
    each row from the driver's EARLIER races, over the same seasons the live
    build loads (dfs_run_nascar.FORM_SEASONS: this one and the one before).
    Prior-only, every earlier race counts -- including those before he has a
    4-race finish form. In place; rows in date order."""
    from edge.dfs_run_nascar import FORM_SEASONS
    hist: dict = collections.defaultdict(list)
    for r in rows:
        prev = [(x, t) for x, t, yr in hist[r["driver_id"]]
                if yr > r["season"] - FORM_SEASONS]
        f = S.running_form(prev)
        r["ew12"] = f.get("ew12")
        r["ewtt"] = (f.get("ewtt_by_type") or {}).get(r["track_type"], r["ew12"])
        if r.get("avg_pos") is not None:
            hist[r["driver_id"]].append(
                (float(r["avg_pos"]), r["track_type"], r["season"]))


def fit_tables(train: list[dict]) -> tuple[dict, dict]:
    """FINISH_MODEL and DNF_RATE refitted on train, shipped specification
    (superspeedway reduced to start + form_finish, as in nascar_sim)."""
    fm = {}
    for t in F.TRACK_TYPES:
        sel = [r for r in train if r["track_type"] == t]
        if t == "superspeedway":
            X = np.array([[1.0, r["start"], r["form_finish"]] for r in sel])
        else:
            X = np.array([[1.0, r["start"], r["form_finish"], r["form_led"],
                           r["form_dnf"]] for r in sel])
        y = np.array([float(r["finish"]) for r in sel])
        b, *_ = np.linalg.lstsq(X, y, rcond=None)
        res = y - X @ b
        b = list(b) + [0.0] * (5 - len(b))
        fm[t] = (*[float(v) for v in b], float(res.std()),
                 float(1 - res.var() / y.var()))
    dnf = {}
    for t in F.TRACK_TYPES:
        dnf[t] = {}
        for lo, hi in F.START_BUCKETS:
            sel = [r for r in train if r["track_type"] == t and lo <= r["start"] <= hi]
            dnf[t][(lo, hi)] = (sum(bool(r["dnf"]) for r in sel) / len(sel)
                                if sel else 0.15)
    return fm, dnf


@contextlib.contextmanager
def tables(fm, dnf):
    old = S.FINISH_MODEL, S.DNF_RATE
    S.FINISH_MODEL, S.DNF_RATE = fm, dnf
    try:
        yield
    finally:
        S.FINISH_MODEL, S.DNF_RATE = old


def _old_expected(d, track):
    """The pre-cycle-1 E[finish] (form_finish, no running position) -- kept so
    the baseline row stays reproducible after production moved on."""
    c, bs, bf, bl, bd, _sd, _r2 = S.FINISH_MODEL[track]
    return c + bs * d["start"] + bf * d["form_finish"] + bl * d["form_led"] + bd * d["form_dnf"]


def sim_mean_finish(drivers: list[dict], track: str, n_sims: int, seed: int = 7,
                    expected=None):
    """simulate() steps 1-2, returning each driver's mean simulated finish."""
    rng = np.random.default_rng(seed)
    n = len(drivers)
    expected = expected or S.expected_finish
    mu = np.array([expected(d, track) for d in drivers])
    p = np.array([S.dnf_probability(int(d["start"]), track, d.get("form_dnf"))
                  for d in drivers])
    sd = S.FINISH_MODEL.get(track, S.FINISH_MODEL[S.DEFAULT_TRACK])[-2]
    dnf = rng.random((n_sims, n)) < p
    latent = mu + rng.normal(0.0, sd, size=(n_sims, n)) + dnf * 1000.0
    finish = np.argsort(np.argsort(latent, axis=1), axis=1) + 1
    return finish.mean(axis=0)


def baseline_variant(train):
    """Cycle 0: finish ~ start + form_finish (+ form_led, form_dnf), refit."""
    fm, dnf = fit_tables(train)

    def predict(race_rows, n_sims):
        with tables(fm, dnf):
            return sim_mean_finish(race_rows, race_rows[0]["track_type"], n_sims,
                                   expected=_old_expected)
    return predict


def fit_running(rows: list[dict], cond: bool = False, k: float = 1.0) -> dict:
    """FINISH_MODEL in production's shape, fitted on `rows`:
    finish ~ 1, start, ew12, ewtt, form_led, form_dnf per track type.

    cond / k (cycle 2): fit the latent on drivers still running at the end
    and scale its residual sd by k. simulate() sends its own DNFs to the
    back, so a latent fitted on every finish counted each wreck twice."""
    fm = {}
    for t in F.TRACK_TYPES:
        sel = [r for r in rows if r["track_type"] == t
               and not (cond and r["dnf"])]
        X = np.array([[1.0, r["start"],
                       r["ew12"] if r["ew12"] is not None else r["form_finish"],
                       r["ewtt"] if r["ewtt"] is not None else
                       (r["ew12"] if r["ew12"] is not None else r["form_finish"]),
                       r["form_led"], r["form_dnf"]] for r in sel])
        y = np.array([float(r["finish"]) for r in sel])
        if t == "superspeedway":
            X = X[:, :4]       # reduced, as shipped: led/dnf are noise there
        b, *_ = np.linalg.lstsq(X, y, rcond=None)
        res = y - X @ b
        b = list(b) + [0.0] * (6 - len(b))
        fm[t] = (*[round(float(v), 4) for v in b], round(k * float(res.std()), 3),
                 round(float(1 - res.var() / y.var()), 4))
    return fm


def running_variant(train, cond=False, k=1.0):
    """Cycle 1: production expected_finish with the table fitted on train.
    cond/k: cycle 2 (see fit_running)."""
    fm = fit_running(train, cond, k)
    _fm, dnf = fit_tables(train)

    def predict(race_rows, n_sims):
        with tables(fm, dnf):
            return sim_mean_finish(race_rows, race_rows[0]["track_type"], n_sims)
    return predict


VARIANTS = {
    "baseline": baseline_variant,
    "running_pos": running_variant,
    "cond_latent": lambda train: running_variant(train, cond=True, k=COND_K),
}


def _spearman(a, b) -> float:
    ra = np.argsort(np.argsort(a))
    rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def evaluate(rows, predictor, n_sims) -> dict:
    by_race = collections.defaultdict(list)
    for r in rows:
        by_race[r["race_id"]].append(r)
    out = collections.defaultdict(lambda: {"err": [], "pd": [], "rho": [], "races": 0})
    for race in by_race.values():
        pred = predictor(race, n_sims)
        fin = np.array([r["finish"] for r in race], float)
        st = np.array([r["start"] for r in race], float)
        for key in ("all", race[0]["track_type"]):
            o = out[key]
            o["err"].extend(pred - fin)
            o["pd"].extend((st - pred) - (st - fin))
            o["rho"].append(_spearman(pred, fin))
            o["races"] += 1
    res = {}
    for k, o in out.items():
        e = np.array(o["err"])
        res[k] = dict(races=o["races"], n=len(e), mae=float(np.abs(e).mean()),
                      rmse=float(math.sqrt((e ** 2).mean())), bias=float(e.mean()),
                      rho=float(np.mean(o["rho"])),
                      pd_mae=float(np.abs(np.array(o["pd"])).mean()))
    return res


#: DK variants (target: DK points). Each takes train rows and returns
#: predict(race_rows, n_sims) -> (n_sims, n_drivers) DK points matrix, the
#: same shape production simulate() returns. Fit only on train.
def dk_cond_latent(train, front_a=None, old_green=False):
    """Production simulate() with train-fitted tables. front_a=0 and
    old_green=True reproduce the pre-cycle-4 model (no front-row lead boost,
    fastest-lap pool = race distance); otherwise production's FRONT_A and
    FAST_FRAC (FAST_FRAC is fitted on 2022-24 = train)."""
    fm = fit_running(train, cond=True, k=COND_K)
    _fm, dnf = fit_tables(train)

    def predict(race_rows, n_sims):
        r0 = race_rows[0]
        meta = {"track_type": r0["track_type"], "actual_laps": r0["actual_laps"]}
        if old_green:
            meta["green_laps"] = r0["actual_laps"]
        old_a = S.FRONT_A
        if front_a is not None:
            S.FRONT_A = front_a
        try:
            with tables(fm, dnf):
                return S.simulate(race_rows, meta, n_sims=n_sims, seed=7)
        finally:
            S.FRONT_A = old_a
    return predict


DK_VARIANTS = {"shipped": dk_cond_latent,
               "pre_cycle4": lambda train: dk_cond_latent(train, front_a=0.0, old_green=True),
               "front_only": lambda train: dk_cond_latent(train, old_green=True)}


def dk_components(race_rows) -> np.ndarray:
    """Actual (finish+PD, laps-led pts, fast-lap pts) per driver."""
    from edge.nascar import finish_points
    return np.array([[finish_points(int(r["finish"])) + r["start"] - r["finish"],
                      0.25 * float(r["laps_led"] or 0), 0.45 * float(r["fast_laps"] or 0)]
                     for r in race_rows])


def evaluate_dk(rows, predictor, n_sims) -> dict:
    """Mean simulated DK points vs real, per driver-race; sliced by track type."""
    by_race = collections.defaultdict(list)
    for r in rows:
        by_race[r["race_id"]].append(r)
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for race in by_race.values():
        sims = predictor(race, n_sims)
        mean = sims.mean(axis=0)
        act = np.array([float(r["dk"]) for r in race])
        lo10, p25, p90 = np.percentile(sims, [10, 25, 90], axis=0)
        for key in ("all", race[0]["track_type"]):
            o = out[key]
            o["err"].extend(mean - act)
            o["rho"].append(_spearman(mean, act))
            o["band"].extend((act >= lo10) & (act <= p90))
            o["below_floor"].extend(act < p25)
            o["above_ceil"].extend(act > p90)
            o["races"].append(1)
            o["act"].extend(act)
    res = {}
    for k, o in out.items():
        e = np.array(o["err"])
        res[k] = dict(races=len(o["races"]), n=len(e), mae=float(np.abs(e).mean()),
                      rmse=float(math.sqrt((e ** 2).mean())), bias=float(e.mean()),
                      rho=float(np.mean(o["rho"])), band=float(np.mean(o["band"])),
                      below=float(np.mean(o["below_floor"])),
                      above=float(np.mean(o["above_ceil"])),
                      sd_act=float(np.std(o["act"])))
    return res


def dk_table(res: dict) -> str:
    lines = ["| slice | races | driver-races | DK MAE | RMSE | bias | race Spearman | 10-90 band | <floor(25) | >ceil(90) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for k in ("all",) + F.TRACK_TYPES:
        if k in res:
            r = res[k]
            lines.append(f"| {k} | {r['races']} | {r['n']} | {r['mae']:.2f} | {r['rmse']:.2f} | "
                         f"{r['bias']:+.2f} | {r['rho']:.3f} | {100*r['band']:.1f}% | "
                         f"{100*r['below']:.1f}% | {100*r['above']:.1f}% |")
    return "\n".join(lines)


def table(res: dict) -> str:
    lines = ["| slice | races | driver-races | finish MAE | RMSE | bias | mean race Spearman |",
             "|---|---|---|---|---|---|---|"]
    for k in ("all",) + F.TRACK_TYPES:
        if k in res:
            r = res[k]
            lines.append(f"| {k} | {r['races']} | {r['n']} | {r['mae']:.2f} | "
                         f"{r['rmse']:.2f} | {r['bias']:+.2f} | {r['rho']:.3f} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report", "dk", "fit"])
    ap.add_argument("--variant", default=None,
                    help=f"report: {sorted(VARIANTS)} (running_pos); dk: {sorted(DK_VARIANTS)} (shipped)")
    ap.add_argument("--sims", type=int, default=400)
    a = ap.parse_args()
    rows = load_all()
    if a.cmd == "fit":
        # Production table: every season, as the shipped constants always were.
        for t, v in fit_running(rows, cond=True, k=COND_K).items():
            print(f"    {t!r}: {v},")
        return
    train = [r for r in rows if r["season"] in TRAIN]
    test = [r for r in rows if r["season"] in TEST]
    if a.cmd == "dk":
        v = a.variant or "shipped"
        pred = DK_VARIANTS[v](train)
        print(f"DK points, variant {v}: train {TRAIN} ({len(train)} driver-races), "
              f"test {TEST}\n")
        print(dk_table(evaluate_dk(test, pred, a.sims)))
        return
    a.variant = a.variant or "running_pos"
    pred = VARIANTS[a.variant](train)
    print(f"variant {a.variant}: train {TRAIN} ({len(train)} driver-races), "
          f"test {TEST}\n")
    print(table(evaluate(test, pred, a.sims)))
    print("\n(place differential MAE equals finish MAE by construction: start "
          "is known, so the error is the finish error.)")


if __name__ == "__main__":
    main()
