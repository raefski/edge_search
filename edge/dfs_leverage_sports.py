"""Per-sport adapters for edge/dfs_leverage.py: each sport's own optimiser,
simulation and GPP field, fed to the one leverage pipeline.

Every function takes the sport's build_slate() result and returns
dfs_leverage.analyze()'s dict: candidates from the chalk lineup out to deep
contrarian (each with its sport-shaped `result` for the page to render), the
frontier, a sample of the simulated field for the chart, and -- with a
contest -- expected payout in a field of that size.

FIELD MODELS. NFL's GPP field is fitted (edge/dfs_nfl_contest.py: real stack
rates and a sharp share, three 17,835-entry exports). MLB's stack-size mix is
measured (edge/dfs_sim.STACK_DIST). MMA's field is the lineup softmax fitted on
one export (edge/dfs_mma_theory.py). NHL's is drawn from projected GPP
ownership and resampled to the measured stack rate and sharp share
(NHL_STACK3, NHL_SHARP -- GPP_META.md). NCAAF and NASCAR fields are ownership
draws under the cap with dfs_leverage's sharp share as a prior; NCAAF's does
not stack yet, so its top tail is a little thin. The ORDER of candidates is
the signal, not the dollars.
"""
from __future__ import annotations

import random
from collections import Counter
from statistics import NormalDist

import numpy as np

from edge import dfs_leverage as lev

#: Simulated field lineups each candidate is ranked against.
N_FIELD = 1000
#: Raw ownership draws per field lineup, before the sharp resample.
RAW_MULT = 2


def _key_index(pool: list) -> dict:
    return {(p["name"], p["team"]): i for i, p in enumerate(pool)}


def _own(pool: list, key: str = "own") -> np.ndarray:
    return np.array([float(p.get(key) or 0.0) for p in pool])


def _tilted(pool: list, raw: list, n: int, seed: int, share: float = lev.SHARP_SHARE) -> list:
    proj = np.array([sum(float(pool[i].get("proj") or 0.0) for i in lu) for lu in raw])
    return lev.tilt(raw, proj, n, seed=seed, share=share)


#: Real NHL GPP fields (GPP_META.md, 8 exports, 2026-10-13): 3+ skaters from
#: one team in 88% [85, 90] of lineups on slates of 4 games or fewer and 73%
#: [69, 78] on 5+; sharp share 0.25 [0.24, 0.26].
NHL_STACK3 = (0.88, 0.73)
NHL_SHARP = 0.25


def simple_field(pool: list, size: int, cap: int, n: int, seed: int = 0,
                 valid=None, min_spend: int = 0) -> list:
    """`n` lineups of `size` drawn in proportion to GPP ownership under the
    cap -- for rosters with no positions (NASCAR) or none that matter here."""
    rng = random.Random(seed)
    w = [max(float(p.get("own") or 0.0), 0.0) for p in pool]
    ids = [i for i, x in enumerate(w) if x > 0 and pool[i].get("salary")]
    cheapest = sorted(pool[i]["salary"] for i in ids)
    out, tries = [], 0
    while len(out) < n and tries < n * 40:
        tries += 1
        picked, spent = [], 0
        for k in range(size):
            left = size - k - 1
            budget = cap - spent - sum(cheapest[:left])
            floor = (min_spend - spent) if left == 0 else 0
            c = [i for i in ids if i not in picked and floor <= pool[i]["salary"] <= budget]
            if not c:
                break
            j = rng.choices(c, weights=[w[i] for i in c])[0]
            picked.append(j)
            spent += pool[j]["salary"]
        if len(picked) == size and (valid is None or valid(picked)):
            out.append(picked)
    return out


# ── NHL ──────────────────────────────────────────────────────────────────────
def nhl(res: dict, contest: dict | None = None, iters: int = 50, seed: int = 0,
        n_field: int = N_FIELD) -> dict:
    from edge import dfs_nhl_field, dfs_opt_nhl, dfs_nhl_theory as theory
    pool, sim, chalk = res["pool"], res["sim"], res.get("gpp")
    if not chalk:
        return {"cands": [], "field": [], "best": None, "contest": contest}
    ix = _key_index(pool)

    def idx_of(r):
        return [ix[(p["name"], p["team"])] for p in r["lineup"]]

    def build(w):
        return dfs_opt_nhl.optimize(pool, sim, mode="gpp", iters=iters, seed=seed, own_weight=w)

    cands = lev.sweep(build, idx_of, chalk, chalk["ceil"], chalk["own"])
    for c in cands:
        c["tag"] = " ".join(f"{t}×{n}" for t, n in c["result"]["stacks"].items() if n >= 3)
    own = _own(pool)
    raw = dfs_nhl_field.sample_field(pool, own / 100.0, n=n_field * RAW_MULT, seed=seed + 7,
                                     rounds=1).tolist()
    games = len({p["game"] for p in pool})
    stacked = [max(Counter(pool[i]["team"] for i in lu if pool[i]["pos"] != "G").values()) >= 3
               for lu in raw]
    raw = lev.reshare(raw, stacked, NHL_STACK3[0] if games <= 4 else NHL_STACK3[1], seed + 9)
    field = _tilted(pool, raw, n_field, seed + 8, share=NHL_SHARP)
    return lev.analyze(sim, own, cands, field, theory.GPP_PCT, contest)


# ── NASCAR ───────────────────────────────────────────────────────────────────
def nascar(res: dict, contest: dict | None = None, iters: int = 120, seed: int = 0,
           n_field: int = N_FIELD) -> dict:
    from edge import dfs_opt_nascar as opt, dfs_nascar_theory as theory
    pool, sim, chalk = res["pool"], res["sim"], res.get("gpp")
    if not chalk:
        return {"cands": [], "field": [], "best": None, "contest": contest}
    ix = _key_index(pool)

    def idx_of(r):
        return [ix[(d["name"], d["team"])] for d in r["lineup"]]

    def build(w):
        return opt.optimize(pool, sim, mode="gpp", iters=iters, seed=seed, own_weight=w)

    cands = lev.sweep(build, idx_of, chalk, chalk["ceil"], chalk["own"])
    raw = simple_field(pool, opt.ROSTER_SIZE, opt.CAP, n_field * RAW_MULT, seed=seed + 7,
                       min_spend=46_000)
    field = _tilted(pool, raw, n_field, seed + 8)
    return lev.analyze(sim, _own(pool), cands, field, theory.GPP_PCT, contest)


# ── MMA ──────────────────────────────────────────────────────────────────────
def mma(res: dict, contest: dict | None = None, seed: int = 0, n_field: int = N_FIELD) -> dict:
    """MMA enumerates every legal lineup, so its frontier is exact: for each
    fade, the lineup maximising (normal-approx ceiling - penalty * own) over
    ALL legal lineups. Captain slates score the first slot 1.5x."""
    from edge import dfs_mma_theory as theory, dfs_opt_mma as opt, dfs_run_mma as R
    pool, pts, chalk = res["pool"], res["points"], res.get("gpp")
    captain = bool(res.get("captain"))
    if not chalk or "idx" not in chalk:
        return {"cands": [], "field": [], "best": None, "contest": contest}
    sal = np.array([d["salary"] for d in pool])
    if captain:
        cpt_sal = np.array([d.get("cpt_salary") or round(1.5 * d["salary"]) for d in pool])
        lineups = opt.legal_captain_lineups(sal, cpt_sal)
    else:
        lineups = opt.legal_lineups(sal)
    opp = R._opponents(pool)
    wts = np.ones(lineups.shape[1])
    if captain:
        wts[0] = opt.CPT_MULT
    own = _own(pool)
    own_cpt = _own(pool, "own_cpt") if captain else own
    # mean and sd of every legal lineup from the fighters' covariance
    x = np.zeros((len(lineups), len(pool)), dtype=np.float32)
    for c in range(lineups.shape[1]):
        x[np.arange(len(lineups)), lineups[:, c]] += wts[c]
    mu = x @ pts.mean(0)
    cov = np.cov(pts, rowvar=False)
    sd = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", x, cov, x), 0.0))
    ceil = mu + NormalDist().inv_cdf(0.9) * sd
    l_own = own[lineups[:, 1:]].sum(1) + (own_cpt if captain else own)[lineups[:, 0]]
    chalk_i = list(chalk["idx"])
    chalk_ceil, chalk_own = float(chalk["ceil"]), float(chalk["own"])

    def build(w):
        j = int(np.argmax(ceil - w * l_own / 100.0))
        r = {"idx": [int(i) for i in lineups[j]]}
        r.update(R._lineup_result(pool, pts, r["idx"], res["_lines"], captain))
        return r

    cands = lev.sweep(build, lambda r: r["idx"], {**chalk, "idx": chalk_i}, chalk_ceil, chalk_own)
    _o, fl, w = theory.field_model(pool, lineups, opp, theory.FIELD_TAU_GPP, captain)
    field = theory.sample_field(fl, w, n=n_field, seed=seed + 1).tolist()
    own_vec = own if not captain else own   # chart/frontier own: flex rates (captain noted)
    return lev.analyze(pts, own_vec, cands, field, 90.0, contest,
                       weights=wts if captain else None)


# ── NFL ──────────────────────────────────────────────────────────────────────
def nfl(res: dict, contest: dict | None = None, iters: int = 300, seed: int = 0,
        extra: list | None = None, n_sims: int = 3000, n_field: int = N_FIELD) -> dict:
    """`extra`: more GPP results to place on the chart (the page's per-QB stacks)."""
    from edge import dfs_nfl_contest as nc, dfs_nfl_theory as theory, dfs_opt_nfl as opt
    pool, chalk = res["pool"], res.get("gpp")
    if not chalk:
        return {"cands": [], "field": [], "best": None, "contest": contest}
    ix = _key_index(pool)

    def idx_of(r):
        return [ix[(p["name"], p["team"])] for p, _ in r["lineup"]]

    def build(w):
        return opt.optimize(pool, mode="gpp", iters=iters, seed=seed, own_weight=w)

    cands = lev.sweep(build, idx_of, chalk, chalk["ceil"], chalk["own"])
    _add_extra(cands, extra, idx_of)
    _qb_tags(cands)
    sims = nc.scores(pool, n_sims=n_sims, seed=seed + 1)
    field = nc.sample_field(pool, n_field, seed=seed + 11)
    return lev.analyze(sims, _own(pool), cands, field,
                       100 * NormalDist().cdf(theory.Z_GPP), contest)


def _qb_tags(cands: list) -> None:
    for c in cands:
        s = c["result"].get("stack") or {}
        if s.get("qb"):
            c["tag"] = f"{s['qb'].split()[-1]} ({s['team']}) stack"


def _add_extra(cands: list, extra, idx_of) -> None:
    seen = {tuple(sorted(c["idx"])) for c in cands}
    for r in extra or []:
        if not r:
            continue
        idx = idx_of(r)
        if tuple(sorted(idx)) not in seen:
            seen.add(tuple(sorted(idx)))
            cands.append({"idx": idx, "fade": None, "own_weight": None, "result": r})


# ── NCAAF ────────────────────────────────────────────────────────────────────
def ncaaf(res: dict, contest: dict | None = None, iters: int = 300, seed: int = 0,
          stack_n: int | None = None, extra: list | None = None, n_sims: int = 3000,
          n_field: int = N_FIELD) -> dict:
    """`extra`: more GPP results to place on the chart (the page's per-QB stacks)."""
    from edge import dfs_field, dfs_ncaaf_theory as theory, dfs_opt_ncaaf as opt
    pool, chalk = res["pool"], res.get("gpp")
    if not chalk:
        return {"cands": [], "field": [], "best": None, "contest": contest}
    ix = _key_index(pool)
    kw = {"stack_n": stack_n} if stack_n else {}

    def idx_of(r):
        return [ix[(p["name"], p["team"])] for p, _ in r["lineup"]]

    def build(w):
        return opt.optimize(pool, mode="gpp", iters=iters, seed=seed, own_weight=w, **kw)

    cands = lev.sweep(build, idx_of, chalk, chalk["ceil"], chalk["own"])
    _add_extra(cands, extra, idx_of)
    _qb_tags(cands)
    sims = dfs_field.simulate(pool, opt._sd, theory.rho, n_sims=n_sims, seed=seed + 1)
    own = _own(pool)
    raw = dfs_field.sample_field(pool, own, opt.SLOTS, opt._valid, n=n_field * RAW_MULT,
                                 seed=seed + 7, cap=opt.CAP)
    if len(raw) < n_field:          # a board too cheap to reach the usual min spend
        raw = dfs_field.sample_field(pool, own, opt.SLOTS, opt._valid, n=n_field * RAW_MULT,
                                     seed=seed + 7, cap=opt.CAP, min_spend=0)
    field = _tilted(pool, raw, n_field, seed + 8)
    return lev.analyze(sims, own, cands, field, 100 * NormalDist().cdf(theory.Z_GPP), contest)


# ── MLB ──────────────────────────────────────────────────────────────────────
def mlb(res: dict, contest: dict | None = None, iters: int = 300, seed: int = 0,
        n_sims: int = 3000, n_field: int = N_FIELD) -> dict:
    """MLB's GPP objective is already ceiling minus 0.3 x ownership (the
    production fade); the sweep runs the same 5-3 stack construction from no
    fade (pure ceiling) to deep, picking the stack teams by the same faded
    ceiling each time, and the app's own GPP lineup is added as a candidate."""
    from collections import defaultdict
    from edge import dfs_opt, dfs_sim
    pool, app_gpp = res["pool"], res.get("gpp")
    hitters = [p for p in pool if "P" not in p["pos"]]
    if not app_gpp or sum(1 for p in pool if "P" in p["pos"]) < 2 or len(hitters) < 8:
        return {"cands": [], "field": [], "best": None, "contest": contest}
    ix = {p["name"]: i for i, p in enumerate(pool)}
    saved = {p["name"]: p.get("lev") for p in pool}

    def idx_of(r):
        return [ix[p["name"]] for p, _ in r["lineup"]]

    def build(w):
        c = w / 100.0
        for p in pool:
            p["lev"] = round(p.get("ceiling", p["proj"]) - c * p.get("own", 0.0), 1)
        team = defaultdict(float)
        for h in hitters:
            team[h["team"]] += h.get("ceiling", h["proj"]) - c * h.get("own", 0.0)
        rank = sorted(team, key=team.get, reverse=True)
        return dfs_opt.optimize(pool, mode="gpp", stack_team=rank[0], stack_n=5, iters=iters,
                                seed=seed, stack2_team=rank[1] if len(rank) > 1 else None,
                                stack2_n=3)

    try:
        chalk = build(0.0)
        if not chalk:
            return {"cands": [], "field": [], "best": None, "contest": contest}
        ceil0 = sum(p.get("ceiling", p["proj"]) for p, _ in chalk["lineup"])
        own0 = sum(p.get("own", 0.0) for p, _ in chalk["lineup"])
        cands = lev.sweep(build, idx_of, chalk, ceil0, own0)
    finally:
        for p in pool:
            p["lev"] = saved[p["name"]]
    app_key = tuple(sorted(idx_of(app_gpp)))
    if not any(tuple(sorted(c["idx"])) == app_key for c in cands):
        cands.append({"idx": idx_of(app_gpp), "fade": None, "own_weight": 30.0,
                      "result": app_gpp})
    for c in cands:
        c["app"] = tuple(sorted(c["idx"])) == app_key
        teams = {}
        for p, _ in c["result"]["lineup"]:
            if "P" not in p["pos"]:
                teams[p["team"]] = teams.get(p["team"], 0) + 1
        tag = "-".join(f"{t}{n}" for t, n in sorted(teams.items(), key=lambda kv: -kv[1]) if n >= 3)
        c["tag"] = (tag + " · app GPP").strip(" ·") if c["app"] else tag
    sims = mlb_scores(pool, n_sims=n_sims, seed=seed + 1)
    field = dfs_sim.generate_field(pool, n_field, rng=np.random.default_rng(seed + 11))
    return lev.analyze(sims, _own(pool), cands, field, 95.0, contest)


def mlb_scores(pool: list, n_sims: int = 3000, seed: int = 1) -> np.ndarray:
    """Joint simulated DK points for the MLB pool (edge/dfs_sim.simulate_slate)."""
    from edge import dfs_sim
    sims, _m = dfs_sim.simulate_slate(pool, n_sims=n_sims, seed=seed,
                                      season_rates=_mlb_season_rates(pool))
    return sims


def _mlb_season_rates(pool: list) -> dict:
    """Season hitting rates for the simulator, as edge/dfs_run.gpp_sim_ev_lineup
    reads them; {} (league rates) if the cache is missing."""
    import json
    from pathlib import Path
    from edge import dfs
    try:
        raw = json.loads((Path(__file__).resolve().parents[1]
                          / "data/dfs_season_hitting.json").read_text())
    except (OSError, ValueError):
        return {}
    by_norm = {dfs.norm(k.split("|", 1)[1]): v for k, v in raw.items()
               if isinstance(v, dict) and "|" in k}
    return {p["name"]: by_norm.get(dfs.norm(p["name"])) for p in pool if "P" not in p["pos"]}
