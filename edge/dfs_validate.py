"""Validation methodology, formalized after an external review caught that this
project's numbers were pooled Pearson with no uncertainty attached, no baseline
comparison, and (in one case) a claimed number that didn't reconcile with its
own source data. Every future calibration report should go through here rather
than re-deriving ad-hoc statistics inline.

Key lesson this module encodes: a pooled correlation across a handful of slates
overstates precision (the rows within a slate aren't independent draws), and a
correlation number in isolation says nothing about whether the model beats a
free baseline (DK salary, or DK's own posted FPPG). Both checks are cheap and
should run by default.
"""
from __future__ import annotations

import math
import statistics


def pearson(xs, ys) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    return cov / (sx * sy) if sx and sy else float("nan")


def spearman(xs, ys) -> float:
    """Pearson on ranks (average rank for ties) -- robust to the skew that
    inflates Pearson on right-skewed data like ownership."""
    def rank(v):
        idx = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and v[idx[j + 1]] == v[idx[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[idx[k]] = avg_rank
            i = j + 1
        return r
    return pearson(rank(xs), rank(ys))


def fisher_ci(r: float, n: int, level: float = 0.95) -> tuple[float, float]:
    """95% CI for a Pearson r via Fisher z-transform. NOT slate-clustered --
    treats n as independent draws. Use cross_slate_summary for the honest
    version when rows come from a handful of slates."""
    if n < 4 or abs(r) >= 1:
        return (float("nan"), float("nan"))
    z = 0.5 * math.log((1 + r) / (1 - r))
    z_crit = 1.96 if level == 0.95 else abs(statistics.NormalDist().inv_cdf((1 + level) / 2))
    se = 1 / math.sqrt(n - 3)
    lo, hi = z - z_crit * se, z + z_crit * se
    return (math.tanh(lo), math.tanh(hi))


def cross_slate_summary(rows: list[dict], date_key: str, x_key: str, y_key: str,
                        method="pearson") -> dict:
    """The honest version of a multi-slate correlation: compute the statistic
    PER SLATE, then treat each slate's number as one independent observation.
    Reports the pooled number alongside the per-slate mean/SE so both the
    "how much data" and "how much of that data is really independent" questions
    are visible at once. With few slates this SE is itself rough -- report it
    anyway; a rough honest SE beats a precise dishonest one.
    """
    corr_fn = spearman if method == "spearman" else pearson
    by_date: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        if r.get(x_key) is not None and r.get(y_key) is not None:
            by_date.setdefault(r[date_key], []).append((r[x_key], r[y_key]))

    per_slate = {}
    for d, pairs in by_date.items():
        if len(pairs) < 3:
            continue
        xs, ys = zip(*pairs)
        per_slate[d] = {"n": len(pairs), "corr": round(corr_fn(list(xs), list(ys)), 3)}

    all_pairs = [p for pairs in by_date.values() for p in pairs]
    pooled_xs, pooled_ys = zip(*all_pairs) if all_pairs else ([], [])
    pooled_corr = corr_fn(list(pooled_xs), list(pooled_ys)) if all_pairs else float("nan")

    slate_corrs = [v["corr"] for v in per_slate.values()]
    out = {
        "method": method,
        "n_slates": len(per_slate),
        "n_rows": len(all_pairs),
        "pooled_corr": round(pooled_corr, 3),
        "pooled_ci_naive": tuple(round(x, 3) for x in fisher_ci(pooled_corr, len(all_pairs))),
        "per_slate": per_slate,
    }
    if len(slate_corrs) >= 2:
        out["cross_slate_mean"] = round(statistics.mean(slate_corrs), 3)
        out["cross_slate_se"] = round(statistics.stdev(slate_corrs) / math.sqrt(len(slate_corrs)), 3)
    return out


def incremental_baseline_test(y: list[float], baseline_x: list[float], model_x: list[float]) -> dict:
    """Does model_x carry information about y beyond baseline_x? Fits
    y ~ a + b*baseline_x (R2_base) and y ~ a + b*baseline_x + c*model_x
    (R2_full), reports model_x's coefficient, its SE/t-stat, and the
    incremental R2. This is the direct test of "does the model beat a free
    baseline" -- a corr number for model_x alone says nothing about this,
    since model_x and baseline_x can be correlated with each other too.

    Requires numpy (only import point in this module, so the rest stays
    dependency-free like the rest of edge/).
    """
    import numpy as np
    y, bx, mx = np.array(y, dtype=float), np.array(baseline_x, dtype=float), np.array(model_x, dtype=float)
    n = len(y)

    X1 = np.column_stack([np.ones(n), bx])
    b1, *_ = np.linalg.lstsq(X1, y, rcond=None)
    r2_base = 1 - np.sum((y - X1 @ b1) ** 2) / np.sum((y - y.mean()) ** 2)

    X2 = np.column_stack([np.ones(n), bx, mx])
    b2, *_ = np.linalg.lstsq(X2, y, rcond=None)
    resid2 = y - X2 @ b2
    r2_full = 1 - np.sum(resid2 ** 2) / np.sum((y - y.mean()) ** 2)
    dof = n - 3
    sigma2 = np.sum(resid2 ** 2) / dof if dof > 0 else float("nan")
    se = np.sqrt(np.diag(sigma2 * np.linalg.inv(X2.T @ X2))) if dof > 0 else [float("nan")] * 3

    return {
        "n": n, "r2_baseline_only": round(float(r2_base), 4), "r2_with_model": round(float(r2_full), 4),
        "incremental_r2": round(float(r2_full - r2_base), 4),
        "model_coef": round(float(b2[2]), 4), "model_se": round(float(se[2]), 4),
        "model_t": round(float(b2[2] / se[2]), 2) if se[2] else float("nan"),
        "significant_at_5pct": abs(b2[2] / se[2]) > 1.96 if se[2] else False,
    }
