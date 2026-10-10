"""Field-aware cash for the sports without a game simulator (NFL, NCAAF).

NHL showed (NHL_STATUS.md 6c) that the cash lineup should be the one most
likely to clear a SIMULATED double-up field's cash line, not the one with the
best floor of its own. That needs joint simulated outcomes for every player.
NHL, NASCAR, MMA and MLB simulate games; NFL and NCAAF are analytic -- a mean,
a measured spread, and measured same-game correlations -- so this draws joint
outcomes from exactly those three things:

    Gaussian copula with the model's own pairwise rho, mapped to a lognormal
    with the model's mean and sd (DK points are skewed and floored near 0);
    defences stay normal (they can go negative).

The field is drawn slot by slot in proportion to cash ownership, salary-capped
and checked by the sport's own legality test; the cash line in each world is
the (1 - PAY_FRAC) quantile of the field.
"""
from __future__ import annotations

import random

import numpy as np

PAY_FRAC = 0.44
MIN_SPEND = 48_000


def simulate(pool: list, sd_fn, rho_fn, n_sims: int = 2000, seed: int = 0,
             normal=lambda p: False) -> np.ndarray:
    """(n_sims, len(pool)) joint DK points."""
    n = len(pool)
    corr = np.eye(n)
    by_game: dict = {}
    for i, p in enumerate(pool):
        by_game.setdefault(p.get("game"), []).append(i)
    for g, idx in by_game.items():
        if g is None:
            continue
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                r = rho_fn(pool[idx[a]], pool[idx[b]])
                corr[idx[a], idx[b]] = corr[idx[b], idx[a]] = r
    vals, vecs = np.linalg.eigh(corr)
    corr = (vecs * np.clip(vals, 1e-6, None)) @ vecs.T
    d = np.sqrt(np.diag(corr))
    corr = corr / np.outer(d, d)
    z = np.random.default_rng(seed).standard_normal((n_sims, n)) @ np.linalg.cholesky(corr).T
    out = np.zeros((n_sims, n))
    for i, p in enumerate(pool):
        m, s = float(p.get("proj") or 0.0), float(sd_fn(p))
        if normal(p):
            out[:, i] = m + s * z[:, i]
        elif m > 0.1:
            sig = np.sqrt(np.log1p((s / m) ** 2))
            out[:, i] = np.exp(np.log(m) - sig * sig / 2 + sig * z[:, i])
    return out


def sharpen(pool: list, k: float, groups, own_key: str = "own", out_key: str = "own_cash",
            cap: float = 95.0) -> np.ndarray:
    """own**k renormalised to the same total within each group (a function of a row),
    capped. Cash fields concentrate far harder than the GPP-fitted ownership."""
    keyed: dict = {}
    for i, p in enumerate(pool):
        keyed.setdefault(groups(p), []).append(i)
    for idx in keyed.values():
        base = np.array([max(float(pool[i].get(own_key) or 0.0), 0.01) for i in idx])
        total = base.sum()
        own = base ** k / (base ** k).sum() * total
        for _ in range(10):
            over = own > cap
            if not over.any():
                break
            spare = (own[over] - cap).sum()
            own[over] = cap
            free = own < cap
            own[free] += spare * own[free] / own[free].sum()
        for i, o in zip(idx, own):
            pool[i][out_key] = float(o)
    return np.array([pool[i][out_key] / 100.0 for i in range(len(pool))])


def sample_field(pool: list, w: np.ndarray, slots: list, valid, n: int = 300,
                 seed: int = 0, cap: int = 50_000, min_spend: int = MIN_SPEND) -> list:
    """`n` legal lineups (lists of pool indices), each slot drawn in proportion
    to `w` among players eligible for it (`slot in p["pos"]`). Flexible slots
    are filled last; the last pick must reach `min_spend`."""
    rng = random.Random(seed)
    order = sorted(range(len(slots)), key=lambda k: sum(slots[k] in p["pos"] for p in pool))
    out, tries = [], 0
    while len(out) < n and tries < n * 60:
        tries += 1
        picked, spent, ok = [], 0, True
        for step, k in enumerate(order):
            left = len(order) - step - 1
            budget = cap - spent - left * 3000
            floor = min_spend - spent if left == 0 else 0
            cands = [i for i, p in enumerate(pool) if slots[k] in p["pos"] and i not in picked
                     and floor <= p["salary"] <= budget and w[i] > 0]
            if not cands:
                ok = False
                break
            j = rng.choices(cands, weights=[w[i] for i in cands])[0]
            picked.append(j)
            spent += pool[j]["salary"]
        if ok and valid([pool[i] for i in picked]):
            out.append(picked)
    return out


def cash_line(scores: np.ndarray, field: list, pay_frac: float = PAY_FRAC) -> np.ndarray:
    totals = np.stack([scores[:, lu].sum(1) for lu in field], 1)
    return np.quantile(totals, 1.0 - pay_frac, axis=1)
