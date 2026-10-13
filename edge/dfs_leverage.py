"""GPP leverage for every DFS sport: from the chalkiest lineup to the most
contrarian one that still scores, and which of them a given contest pays for.

THE QUESTION. Each sport's GPP optimiser finds the lineup with the best
ceiling. In a 100-entry contest that is usually the right lineup; in a
100,000-entry one the same players are in thousands of field lineups, so the
nights it booms it shares first place. Whether to give up ceiling for lower
ownership depends on the contest -- the NFL page learned this on 2026-10-11
(edge/dfs_nfl_contest.py), and this module does the same for every sport.

THREE STEPS, identical for every sport:

1. CANDIDATES. The sport's own GPP optimiser re-run with an ownership penalty
   swept from none to heavy (FADES). Each sport keeps its own rules (stacks,
   positions, goalie-vs-skater, captain) because its own search does the work.
   The penalty is scaled to the slate: at fade f, the top-ceiling lineup would
   pay f of its own ceiling for its own summed ownership.
2. FRONTIER. A candidate is on it when no other candidate has both a higher
   ceiling and lower summed ownership -- "contrarian but not a stupid play".
3. CONTEST EV. Every candidate against the SAME simulated worlds and the SAME
   simulated GPP field; each world's finish is mapped to a rank in a field of
   the contest's size and paid from DK's payout table (or a top-heavy curve
   pinned to the real prize pool), smoothed over 0.5%-of-field buckets with an
   exponential tail beyond the sampled field. The comparison between
   candidates is not sim noise; the absolute dollars flatter every lineup
   equally because the sim takes our projections as truth.

The sport adapters live in edge/dfs_leverage_sports.py, the phone UI in
edge/dfs_leverage_ui.py.
"""
from __future__ import annotations

import numpy as np

#: Share of the top-ceiling lineup's ceiling its own ownership would cost, per
#: sweep step. 0 is the optimiser's own lineup; 0.45 is deep contrarian.
FADES = (0.0, 0.04, 0.08, 0.13, 0.2, 0.3, 0.45)

#: Field lineups the tail fit uses per world (see share_above).
TAIL_K = 20

#: Payouts averaged over rank buckets this share of the field wide: a
#: 150,000-entry first prize is a 1-in-150,000 event that a few thousand
#: simulated worlds never resolve (edge/dfs_nfl_contest.py, 2026-10-11).
PAY_BUCKET = 0.005

#: A sharp share of every GPP field is optimised, not drawn from ownership.
#: Fitted for NFL (edge/dfs_nfl_contest.py: 30% tilted by exp(0.4*proj) on
#: three 17,835-entry exports, field proj sd ~10 pts). GPP_META.md (2026-10-13)
#: measured 0.25 for NFL (n=3), NHL (n=8) and 0.20 for MLB (n=14); the tilt
#: strength is carried to the other sports in sd units as a prior.
SHARP_SHARE = 0.30
SHARP_BETA_Z = 4.0

#: DK's typical GPP rake and paid share, for contests with no payout table.
RAKE = 0.15
PAID_FRAC = 0.22


def own_weights(chalk_ceil: float, chalk_own: float, fades=FADES) -> list[float]:
    """Ownership penalty per fade, in points per 100% of summed ownership --
    the unit every optimiser's `own_weight` already uses."""
    return [100.0 * f * chalk_ceil / max(chalk_own, 1.0) for f in fades]


def sweep(build, idx_of, chalk, chalk_ceil: float, chalk_own: float, fades=FADES) -> list[dict]:
    """[{idx, fade, own_weight, result}] from the chalk lineup out to the
    deepest fade, duplicates dropped (a lineup keeps its smallest fade).

    `build(own_weight)` returns a sport result or None; `idx_of(result)` its
    pool indices. `chalk` is the optimiser's own (fade 0) result."""
    out, seen = [], set()
    for f, w in zip(fades, own_weights(chalk_ceil, chalk_own, fades)):
        res = chalk if f == 0 else build(w)
        if not res:
            continue
        idx = [int(i) for i in idx_of(res)]       # slot order kept: a captain is idx[0]
        if tuple(sorted(idx)) in seen:
            continue
        seen.add(tuple(sorted(idx)))
        out.append({"idx": idx, "fade": f, "own_weight": round(w, 2), "result": res})
    return out


def lineup_stats(scores: np.ndarray, lineups, own: np.ndarray, ceil_pct: float,
                 weights=None) -> dict:
    """proj (mean), ceil (`ceil_pct` percentile) and summed ownership of each
    lineup, all from the same simulated worlds so field and candidates are
    measured alike. `weights` (same shape as a lineup) scale the points of a
    captain slot; `own` should already carry the captain's own rate."""
    lus = np.asarray(lineups, dtype=int)
    if lus.ndim == 1:
        lus = lus[None, :]
    w = np.ones(lus.shape[1]) if weights is None else np.asarray(weights, float)
    totals = np.stack([scores[:, lu] @ w for lu in lus], 1)
    return {"proj": totals.mean(0), "ceil": np.percentile(totals, ceil_pct, axis=0),
            "own": own[lus].sum(1)}


def pareto(points: list[dict], y: str = "ceil") -> list[bool]:
    """Frontier flags: no other point has a higher `y` AND lower `own`."""
    out = []
    for a in points:
        out.append(not any(b is not a and b[y] >= a[y] and b["own"] <= a["own"]
                           and (b[y] > a[y] or b["own"] < a["own"]) for b in points))
    return out


def tilt(raw: list, proj: np.ndarray, n: int, seed: int = 0,
         share: float = SHARP_SHARE, beta_z: float = SHARP_BETA_Z) -> list:
    """`n` field lineups chosen from `raw` ownership draws, a sharp `share` of
    the weight on exp(beta_z * z(proj)) toward the projected top.

    Chosen WITHOUT replacement (Gumbel top-k): resampling with replacement
    put 145 and 101 copies of the two best-projected draws into a 1,000-lineup
    NHL field on 2026-10-13 -- a quarter of the field on two lineups, where
    real NHL fields duplicate ~8% (GPP_META.md). Duplicates now come only
    from ownership draws that coincide, which is how real ones arise. Needs
    len(raw) > n; with fewer it returns every draw."""
    if not raw:
        return []
    if len(raw) <= n:
        return list(raw)
    proj = np.asarray(proj, float)
    sd = float(proj.std()) or 1.0
    t = np.exp(beta_z * (proj - proj.max()) / sd)
    w = (1 - share) / len(raw) + share * t / t.sum()
    keys = np.log(w) + np.random.default_rng(seed).gumbel(size=len(raw))
    return [raw[i] for i in np.argsort(-keys)[:n]]


def payout_tiers(field_size: int, payouts=None, entry_fee: float = 1.0,
                 prize_pool: float | None = None) -> list:
    """[(rank_from, rank_to, $)]: DK's own table, or without it a top-heavy
    curve whose total is the real prize pool if known."""
    if not payouts:
        from edge import dfs_sim
        rake = RAKE
        if prize_pool and field_size and entry_fee:
            rake = min(max(1.0 - prize_pool / (field_size * entry_fee), 0.0), 0.5)
        payouts = dfs_sim.synthetic_gpp_payouts(field_size, entry_fee=entry_fee or 1.0,
                                                rake=rake, paid_frac=PAID_FRAC)
    return [tuple(t) for t in payouts]


def payout_table(field_size: int, tiers: list) -> np.ndarray:
    """$ by finishing rank (index = rank, 1-based), bucket-smoothed."""
    table = np.zeros(field_size + 1)
    for lo, hi, amt in tiers:
        if lo <= field_size:
            table[lo:min(hi, field_size) + 1] = amt
    width = max(1, int(field_size * PAY_BUCKET))
    for lo in range(1, field_size + 1, width):
        table[lo:lo + width] = table[lo:lo + width].mean()
    return table


def share_above(ours: np.ndarray, srt: np.ndarray, k: int = TAIL_K) -> np.ndarray:
    """Per world, the share of the field scoring above `ours`; `srt` is the
    field's totals sorted ascending per world. Counted directly from the
    sample except in its top `k`, where an exponential tail fitted to that
    world's top k takes over -- without it a lineup that beats every sampled
    lineup is scored as 1st of 150,000."""
    m = srt.shape[1]
    above = (srt > ours[:, None]).sum(1) + 0.5 * (srt == ours[:, None]).sum(1)
    x_k = srt[:, m - k]
    excess = np.maximum((srt[:, m - k + 1:] - x_k[:, None]).mean(1), 1e-6)
    tail = k / m * np.exp(-np.maximum(ours - x_k, 0.0) / excess)
    return np.where(ours > x_k, tail, above / m)


def contest_ev(scores: np.ndarray, candidates: list, field: list, field_size: int,
               payouts=None, entry_fee: float = 1.0, prize_pool: float | None = None,
               weights=None) -> list[dict]:
    """Expected payout of each candidate (a list of pool indices) in a
    `field_size`-entry GPP, all against the same worlds and field lineups.
    One dict per candidate: ev ($/entry), rel (100 = best here), p_cash,
    p_top1 (top 1% of the field), mean_pct (average finish percentile)."""
    field_size = max(int(field_size), 2)
    tiers = payout_tiers(field_size, payouts, entry_fee, prize_pool)
    table = payout_table(field_size, tiers)
    paid = max((hi for _, hi, amt in tiers if amt > 0), default=0)
    w = None if weights is None else np.asarray(weights, float)
    tot = lambda lu: scores[:, lu].sum(1) if w is None else scores[:, lu] @ w  # noqa: E731
    srt = np.sort(np.stack([tot(lu) for lu in field], 1), axis=1)
    out = []
    for lu in candidates:
        share = share_above(tot(lu), srt)
        ranks = np.clip(np.round(share * (field_size - 1)).astype(int) + 1, 1, field_size)
        out.append({"ev": float(table[ranks].mean()),
                    "p_cash": float((ranks <= paid).mean()),
                    "p_top1": float((share <= 0.01).mean()),
                    "mean_pct": float(100 * (1 - share).mean())})
    top = max((r["ev"] for r in out), default=0.0) or 1.0
    for r in out:
        r["rel"] = 100.0 * r["ev"] / top
    return out


def analyze(scores: np.ndarray, own: np.ndarray, cands: list[dict], field: list,
            ceil_pct: float, contest: dict | None = None, n_field_dots: int = 400,
            weights=None) -> dict:
    """The page's whole leverage view for one slate.

    `cands` from sweep() (plus any extra candidates a sport adds, same shape);
    `field` a list of simulated GPP field lineups (pool indices); `contest`
    {"field", "fee", "payouts", "prize_pool"} or None. Returns plain data:
    {"cands": [..., with proj/ceil/own/frontier], "field": [{proj, ceil, own}]
    (a sample for the chart), "field_idx" (every field lineup, for
    with_contest), "best": the top-ceiling candidate} -- and with a contest,
    with_contest()'s ev/rel/p_cash/p_top1 per candidate and best by EV."""
    st = lineup_stats(scores, [c["idx"] for c in cands], own, ceil_pct, weights)
    rows = []
    for c, p, ce, o in zip(cands, st["proj"], st["ceil"], st["own"]):
        rows.append({**c, "proj": round(float(p), 1), "ceil": round(float(ce), 1),
                     "own": round(float(o), 1)})
    for r, on in zip(rows, pareto(rows)):
        r["frontier"] = on
    dots = []
    if field:
        fs = lineup_stats(scores, field[:n_field_dots], own, ceil_pct, weights)
        dots = [{"proj": round(float(p), 1), "ceil": round(float(c), 1), "own": round(float(o), 1)}
                for p, c, o in zip(fs["proj"], fs["ceil"], fs["own"])]
    out = {"cands": rows, "field": dots, "field_idx": [list(map(int, lu)) for lu in field],
           "best": max(range(len(rows)), key=lambda i: rows[i]["ceil"]) if rows else None,
           "contest": None, "ceil_pct": ceil_pct,
           "weights": None if weights is None else [float(x) for x in weights]}
    return with_contest(out, scores, contest) if contest else out


def with_contest(analysis: dict, scores: np.ndarray, contest: dict | None) -> dict:
    """`analysis` re-ranked for one contest: every candidate's expected payout
    in it, and `best` moved to the best EV. The candidates and field are
    reused, so changing contests costs one EV pass, not a rebuild."""
    out = {**analysis, "cands": [dict(c) for c in analysis["cands"]], "contest": contest}
    if not contest or not analysis.get("field_idx") or not out["cands"]:
        return out
    evs = contest_ev(scores, [c["idx"] for c in out["cands"]], analysis["field_idx"],
                     contest["field"], contest.get("payouts"), contest.get("fee") or 1.0,
                     contest.get("prize_pool"), analysis.get("weights"))
    for r, e in zip(out["cands"], evs):
        r.update({k: round(v, 4) for k, v in e.items()})
    out["best"] = max(range(len(out["cands"])), key=lambda i: out["cands"][i]["ev"])
    return out
