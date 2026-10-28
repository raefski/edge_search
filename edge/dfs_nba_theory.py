"""The two NBA game theories, the simulation, and the field model.

A player's DK points are drawn from a gamma with his projected mean and a spread
measured on 2025-26 (scripts/nba_projection_backtest.py's residuals, 29,311
player-games, 2026-10-15):

    sd = SD_A + SD_B x projection        5.9 + 0.20 x proj
         (bands: proj 0-10 sd 6.9 | 10-20 9.9 | 20-30 11.4 | 30-45 12.7 | 45+ 14.8)

CORRELATED BY ROLE (2026-10-22). The first build drew players independently,
because once the minutes a player ACTUALLY played are known, what is left barely
co-moves (teammates -0.037, opponents +0.001). But the projection does not know
tonight's minutes, and minutes are shared by the game script: a blowout sits both
starting fives, the bench plays exactly when the starters sit. Measured on the
PRE-GAME errors (pre-game projection, starters confirmed, z = error / sd_for),
scripts/nba_projection_backtest.py's rebuild, players projected >= 12:

                              2025-26             2024-25
    same team, starter-starter  +0.067 (22,527)     +0.043 (22,826)
    same team, starter-bench    -0.090 (37,250)     -0.088 (35,600)
    same team, bench-bench      -0.024              -0.011
    opponents, starter-starter  +0.019 (28,040)     +0.010 (28,409)
    opponents, starter-bench    -0.035              -0.039
    opponents, bench-bench      -0.016              -0.024

So a game's players are drawn jointly (a Gaussian copula with ROLE_CORR below;
each player's gamma marginal is unchanged). Independent draws made the cash
(30th) and GPP (90th) objectives chase the same max-projection lineup -- with
no correlation a lineup's spread barely depends on who is in it. The gamma keeps
the upside the data shows (8.0% of games land 1.5 sd above the projection vs
6.7% for a normal).

    cash  = the CASH_PCT percentile of the lineup's simulated total
    gpp   = the GPP_PCT percentile, minus an optional ownership tilt

THE FIELD MODEL IS A PRIOR: a softmax over value and salary per position, plus a
bump for a newly promoted starter (the field chases late news too). Twelve NBA
GPP exports from 2024-25 sit in data/contests.db; fit it on them (GPP_META.md: NBA
fields rewarded chalk on all 10 slates measured).
"""
from __future__ import annotations

import math

import numpy as np

CASH_PCT = 30.0
GPP_PCT = 90.0
SD_A, SD_B = 5.91, 0.202

#: Ownership prior (UNFITTED -- see the docstring).
VALUE_WEIGHT = 1.0
SALARY_WEIGHT = 0.4
PROMOTED_BONUS = 0.6
Z_CLIP = 2.5
MAX_OWN = 75.0
#: Lineups' worth of ownership per primary position (8 slots: 5 + G, F, UTIL).
GROUP_SLOTS = {"PG": 1.7, "SG": 1.5, "SF": 1.5, "PF": 1.6, "C": 1.7}


def sd_for(proj: float) -> float:
    return SD_A + SD_B * max(0.0, proj)


#: Correlation of pre-game errors by (same team?, role pair), S = starter, B = bench;
#: the mean of the two seasons in the docstring table.
ROLE_CORR = {(True, "SS"): 0.055, (True, "BS"): -0.089, (True, "BB"): -0.018,
             (False, "SS"): 0.015, (False, "BS"): -0.037, (False, "BB"): -0.020}


def _starters(pool: list) -> set:
    """Indices of tonight's starters: NBA.com's five where announced, else each
    team's five most projected minutes (else projection)."""
    teams: dict = {}
    for j, p in enumerate(pool):
        teams.setdefault(p.get("team"), []).append(j)
    out = set()
    for js in teams.values():
        named = [j for j in js if pool[j].get("starter")]
        if len(named) == 5:
            out |= set(named)
            continue
        key = lambda j: -(pool[j].get("minutes") or pool[j].get("proj") or 0)  # noqa: E731
        out |= set(sorted(js, key=key)[:5])
    return out


def _game_corr(pool: list, js: list, starters: set) -> np.ndarray:
    k = len(js)
    c = np.eye(k)
    for a in range(k):
        for b in range(a + 1, k):
            pa, pb = pool[js[a]], pool[js[b]]
            role = "".join(sorted(("S" if js[a] in starters else "B", "S" if js[b] in starters else "B")))
            c[a, b] = c[b, a] = ROLE_CORR[(pa.get("team") == pb.get("team"), role)]
    w, v = np.linalg.eigh(c)                    # nearest valid correlation matrix
    if w.min() < 1e-6:
        c = v @ np.diag(np.clip(w, 1e-6, None)) @ v.T
        d = np.sqrt(np.diag(c))
        c = c / np.outer(d, d)
    return c


def simulate(pool: list, n_sims: int = 3000, seed: int = 0) -> np.ndarray:
    """(n_sims, len(pool)) DK points: each player a gamma with his mean and sd,
    the players of one game drawn jointly with ROLE_CORR (Gaussian copula)."""
    from scipy.stats import gamma as gamma_dist, norm

    rng = np.random.default_rng(seed)
    out = np.zeros((n_sims, len(pool)))
    games: dict = {}
    for j, p in enumerate(pool):
        games.setdefault(p.get("game") or f"solo{j}", []).append(j)
    starters = _starters(pool)
    for js in games.values():
        z = rng.standard_normal((n_sims, len(js)))
        if len(js) > 1:
            z = z @ np.linalg.cholesky(_game_corr(pool, js, starters)).T
        u = np.clip(norm.cdf(z), 1e-9, 1 - 1e-9)
        for col, j in enumerate(js):
            mu = max(0.1, float(pool[j]["proj"]))
            sd = sd_for(mu)
            out[:, j] = gamma_dist.ppf(u[:, col], (mu / sd) ** 2, scale=sd * sd / mu)
    return out


def summarise(sim: np.ndarray, pool: list) -> list:
    for j, p in enumerate(pool):
        col = sim[:, j]
        p["sd"] = round(float(col.std()), 2)
        p["floor"] = round(float(np.percentile(col, CASH_PCT)), 2)
        p["ceil"] = round(float(np.percentile(col, GPP_PCT)), 2)
    return pool


def lineup_scores(sim: np.ndarray, idx) -> np.ndarray:
    return sim[:, list(idx)].sum(axis=1)


def describe(sim: np.ndarray, idx) -> dict:
    t = lineup_scores(sim, idx)
    return {"proj": round(float(t.mean()), 1), "sd": round(float(t.std()), 1),
            "floor": round(float(np.percentile(t, CASH_PCT)), 1),
            "ceil": round(float(np.percentile(t, GPP_PCT)), 1),
            "p99": round(float(np.percentile(t, 99)), 1)}


def _z(xs):
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5 or 1.0
    return [max(-Z_CLIP, min(Z_CLIP, (x - m) / sd)) for x in xs]


def _normalise(weights, target, cap):
    owns, free = list(weights), [True] * len(weights)
    for _ in range(12):
        room = target - sum(o for o, f in zip(owns, free) if not f)
        total = sum(o for o, f in zip(owns, free) if f)
        if total <= 0 or room <= 0:
            break
        owns = [o * room / total if f else o for o, f in zip(owns, free)]
        over = [i for i, (o, f) in enumerate(zip(owns, free)) if f and o > cap]
        if not over:
            break
        for i in over:
            owns[i], free[i] = cap, False
    return owns


#: YouTube / podcast BUZZ (scripts/buzz_nba.py -> data/buzz_nba.csv): how often
#: the day's NBA DFS shows named a player before lock, as one more softmax term,
#: z-scored log(1 + mentions) within position -- the NFL's form (dfs_nfl_theory
#: BUZZ_BETA). Used only when the pool carries `buzz`; a night with no collection
#: runs the value + salary prior exactly as before.
#:
#: Fitted 2026-10-21, scripts/buzz_fit_nba.py: the 10 NBA GPP nights in contests.db
#: (2025-12-25 .. 2026-03-01) rebuilt as they stood, buzz backfilled with only
#: what was READABLE before lock (uploads, live streams that had ended, podcast
#: episodes published before lock), leave one night out, ownership MAE per player:
#:   prior 5.355 -> 5.080 held out (-0.275 [-0.481, -0.079]), better 7/10,
#:   rank corr +0.709 -> +0.735. Folds picked 0.4 (6) / 0.3 (4). YouTube alone
#:   -0.238; all pre-lock talk read afterwards (upper bound) -0.374 at 0.4.
#: REFIT 2026-10-21 under the v2 minutes model (edge/nba_minutes.project), whose
#: better projections make the value prior itself better: prior 5.355 -> 4.913,
#: + buzz 4.713 (-0.200 [-0.292, -0.111]), better 9/10 nights, rank +0.776 -> +0.792.
#: Folds picked 0.2 (6) / 0.3 (4).
BUZZ_BETA = 0.2


#: Whole-slate ownership (ownership critique cycle 1, 2026-10-28,
#: NBA_OWNERSHIP_CRITIQUE_LOG.md). One softmax over the slate, not per position:
#:   w = exp(A ln(v/v_max) + B ln(salary/5000) + C promoted + D z(log1p buzz) + E ln(rv/v_max))
#: v = proj per $1K, rv = RECENT DK points per $1K (`recent`: his last 3 games this
#: season, else his last 10 of last season; proj when he has none) -- the field
#: prices what a player has been scoring, not only our projection. Normalised to
#: 800% (8 slots), cap MAX_OWN. Fitted on the 4 live train slates 10/20-10/24
#: (676 players, Powell on player MAE, scripts/nba_ownership_lab.py): train MAE
#: 3.671 -> see log; held out 10/25-10/27 (5 slates / 10 GPPs / 549 players)
#: 4.950 -> ~3.96, better on 5/5 slates. Per-position buckets trapped
#: multi-eligible cheap players. Thin: 9 slates -- refit weekly.
SLATE_OWN = (3.77, 1.42, 0.58, 0.19, 1.65)
#: Cheap STARTER bonus (ownership critique cycle 2, 2026-10-28): + G when NBA.com
#: lists him starting before lock and his salary <= $6,000 -- the field drafts the
#: "starting" label on cheap players far past what minutes and value imply. Fitted on
#: the same 4 train slates with SLATE_OWN fixed (grid 0.3: 3.135, 0.5: 3.129, 0.7:
#: 3.158). Held out 5 slates: MAE 3.962 -> see NBA_OWNERSHIP_CRITIQUE_LOG.md. A
#: teammate-Out / minutes-gain term was tried and did not help (the field FADES our
#: minutes-boosted bench players).
CHEAP_STARTER = (0.48, 6000)


def recent_dk(history, cur_n, n_pre: int = 0) -> float | None:
    """Mean DK points of his last 3 PLAYED games this season, else his last 10 played
    (last season). `history` = the build's [(min, starter, pts)]: its newest cur_n
    real entries are this season's (DNPs included); a rookie's preseason rows, n_pre
    of them, are appended AFTER them (scripts/nba_history_publish.py) and are skipped."""
    h = list(history or [])
    if n_pre:
        h = h[:-n_pre]
    cur = [p for m, _, p in h[len(h) - min(cur_n, len(h)):] if m > 0] if cur_n else []
    pts = cur[-3:] if cur else [p for m, _, p in h if m > 0][-10:]
    return sum(pts) / len(pts) if pts else None


def add_ownership(pool: list, weights: tuple | None = None, buzz_beta: float | None = None,
                  slate: tuple | None = None) -> list:
    """Annotate `own` (percent) and `leverage` on every row. Default: the whole-slate
    model (SLATE_OWN; `slate` overrides it for the lab). `weights` = (value, salary)
    runs the old per-position prior instead -- the 2025-26 fit scripts
    (nba_ownership_fit.py, buzz_fit_nba.py) still pass it."""
    if weights is None and buzz_beta is None:
        return _slate_ownership(pool, slate or SLATE_OWN)
    vw, sw = weights or (VALUE_WEIGHT, SALARY_WEIGHT)
    bb = BUZZ_BETA if buzz_beta is None else buzz_beta
    has_buzz = bool(bb) and any(p.get("buzz") is not None for p in pool)
    groups: dict = {}
    for p in pool:
        groups.setdefault(p["pos"], []).append(p)
    for pos, players in groups.items():
        zv = _z([p["proj"] / (p["salary"] / 1000.0) if p.get("salary") else 0.0 for p in players])
        zs = _z([float(p["salary"]) for p in players])
        zb = _z([math.log1p(float(p.get("buzz") or 0.0)) for p in players]) if has_buzz else [0.0] * len(players)
        w = [math.exp(vw * a + sw * b + bb * c + (PROMOTED_BONUS if p.get("promoted") else 0))
             for p, a, b, c in zip(players, zv, zs, zb)]
        for p, own in zip(players, _normalise(w, 100.0 * GROUP_SLOTS.get(pos, 1.6), MAX_OWN)):
            p["own"] = round(own, 1)
        n = len(players)
        pr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["proj"]))}
        orr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["own"]))}
        for p in players:
            p["leverage"] = round(100.0 * (pr[id(p)] - orr[id(p)]) / max(1, n - 1), 1)
    return pool


def _slate_ownership(pool: list, k: tuple) -> list:
    a, b, c, d, e = k
    if not pool:
        return pool
    v = [max(p["proj"], 0.1) / (p["salary"] / 1000.0) if p.get("salary") else 0.1 for p in pool]
    rv = [max(p.get("recent") if p.get("recent") is not None else p["proj"], 0.1) / (p["salary"] / 1000.0)
          if p.get("salary") else 0.1 for p in pool]
    vmax = max(v)
    has_buzz = any(p.get("buzz") is not None for p in pool)
    zb = _z([math.log1p(float(p.get("buzz") or 0.0)) for p in pool]) if has_buzz else [0.0] * len(pool)
    w = [math.exp(a * math.log(x / vmax) + b * math.log(max(float(p.get("salary") or 5000), 1) / 5000.0)
                  + (c if p.get("promoted") else 0.0) + d * z + e * math.log(r / vmax)
                  + (CHEAP_STARTER[0] if p.get("starter") and float(p.get("salary") or 0) <= CHEAP_STARTER[1] else 0.0))
         for p, x, r, z in zip(pool, v, rv, zb)]
    for p, own in zip(pool, _normalise(w, 800.0, MAX_OWN)):
        p["own"] = round(own, 1)
    groups: dict = {}
    for p in pool:
        groups.setdefault(p["pos"], []).append(p)
    for players in groups.values():
        n = len(players)
        pr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["proj"]))}
        orr = {id(p): i for i, p in enumerate(sorted(players, key=lambda q: q["own"]))}
        for p in players:
            p["leverage"] = round(100.0 * (pr[id(p)] - orr[id(p)]) / max(1, n - 1), 1)
    return pool
