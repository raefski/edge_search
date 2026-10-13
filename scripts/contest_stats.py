"""Small statistics helpers shared by the contest-DB analysis scripts
(gpp_meta.py, model_accuracy.py, buzz_trust.py). numpy only -- neither
scipy nor statsmodels is installed here, and nothing below needs them.

The one rule every caller follows: entries in the same contest share ONE
realisation of every player's score, so they are not independent. Every
interval here that involves entries resamples whole CLUSTERS (slates /
contests), never rows.
"""
from __future__ import annotations

import math

import numpy as np


def rankdata(x) -> np.ndarray:
    """Average ranks (ties share the mean rank), like scipy.stats.rankdata."""
    x = np.asarray(x, float)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=float)
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j) / 2
        i = j + 1
    return ranks


def spearman(a, b) -> float | None:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(rankdata(a), rankdata(b))[0, 1])


def summarize(pred, act) -> dict:
    """n, bias (pred - actual), MAE, RMSE, Spearman rho and Pearson r."""
    p, a = np.asarray(pred, float), np.asarray(act, float)
    ok = np.isfinite(p) & np.isfinite(a)
    p, a = p[ok], a[ok]
    n = len(p)
    if n == 0:
        return {"n": 0, "bias": None, "mae": None, "rmse": None, "rho": None, "r": None}
    e = p - a
    rho = spearman(p, a)
    r = float(np.corrcoef(p, a)[0, 1]) if n > 2 and p.std() > 0 and a.std() > 0 else None
    return {"n": n, "bias": float(e.mean()), "mae": float(np.abs(e).mean()),
            "rmse": float(np.sqrt((e ** 2).mean())), "rho": rho, "r": r}


def wilson(w: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def ols(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> np.ndarray:
    """(Weighted) least-squares coefficients."""
    if w is not None:
        sw = np.sqrt(w)[:, None]
        return np.linalg.lstsq(X * sw, y * sw[:, 0], rcond=None)[0]
    return np.linalg.lstsq(X, y, rcond=None)[0]


def demean_by(groups: np.ndarray, *cols: np.ndarray) -> list[np.ndarray]:
    """Subtract each group's mean (contest fixed effects)."""
    out = []
    _, inv = np.unique(groups, return_inverse=True)
    cnt = np.bincount(inv)
    for c in cols:
        s = np.bincount(inv, weights=c)
        out.append(c - (s / cnt)[inv])
    return out


def cluster_bootstrap(clusters: np.ndarray, fn, n_boot: int = 1000, seed: int = 0) -> tuple:
    """(estimate, lo95, hi95, draws) of fn(index array) resampling whole clusters.
    fn receives row indices and returns a float or a 1-d array."""
    rng = np.random.default_rng(seed)
    uniq = np.unique(clusters)
    rows = {c: np.flatnonzero(clusters == c) for c in uniq}
    est = np.asarray(fn(np.arange(len(clusters))), float)
    draws = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([rows[c] for c in pick])
        try:
            draws.append(np.asarray(fn(idx), float))
        except (np.linalg.LinAlgError, ValueError, ZeroDivisionError):
            continue
    d = np.array(draws)
    lo, hi = np.nanpercentile(d, 2.5, axis=0), np.nanpercentile(d, 97.5, axis=0)
    return est, lo, hi, d


def fmt(x, nd=2, pct=False) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    if pct:
        return f"{100 * x:.{nd}f}%"
    return f"{x:+.{nd}f}" if nd and x < 0 else f"{x:.{nd}f}"


def ci(est, lo, hi, nd=3, pct=False) -> str:
    if pct:
        return f"{100 * est:+.{nd}f} [{100 * lo:+.{nd}f}, {100 * hi:+.{nd}f}]"
    return f"{est:+.{nd}f} [{lo:+.{nd}f}, {hi:+.{nd}f}]"
