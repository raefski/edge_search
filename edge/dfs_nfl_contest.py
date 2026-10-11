"""Pick the NFL GPP lineup for the contest you are actually in.

The GPP tab's best stack maximises `mean + z*sd` and never looks at the field,
so on 2026-10-11 it stacked the slate's chalk game from both sides. Whether
that is wrong depends on the contest: in a 100-entry single-entry GPP the
chalk stack is usually fine, in a 150,000-entry one a booming chalk game puts
thousands of lineups level with yours. So candidates are ranked by EXPECTED
PAYOUT in that contest:

  * joint outcomes for every player: edge/dfs_field.simulate on the model's
    own mean, sd and measured same-game correlations;
  * a simulated field drawn from GPP ownership, stacked the way the real NFL
    GPP field stacks (FIELD_STACK_1 / FIELD_STACK_2 below);
  * each world's finish mapped to a rank in a field of the contest's size and
    paid off from DK's own payout table (dfs_sim.pick_by_sim_ev), or from a
    synthetic top-heavy curve when only a field size is known.

Every candidate faces the same worlds and the same field, so the comparison
between stacks is not sim noise.
"""
from __future__ import annotations

import random

import numpy as np

from edge import dfs_field, dfs_sim
from edge import dfs_nfl_theory as theory
from edge import dfs_opt_nfl as opt

#: Share of real NFL GPP lineups whose QB has >= 1 / >= 2 of his own WR/TE.
#: 72/71/72% and 19/18/22% in the 17,835-entry Main-slate exports of 9/13,
#: 9/20 and 9/27 (193028246, 195661382, 195921991; ~17,700 lineups each).
#: Small cash fields stack far less (46-52%), so this is GPP-only. These are
#: the DRAW rates that land the sampled field there: ownership draws also pair
#: a QB with his own receiver by accident, so drawing at 72/20 came out 77/25.
FIELD_STACK_1 = 0.65
FIELD_STACK_2 = 0.15

#: Lineups drawn from ownership alone have about the real field's MEDIAN
#: projection but far too thin a top: on those three slates the real field's
#: 99th percentile (scored with our own projections) was 7-8 points higher.
#: The top is where GPP money is, and a field without it says our optimised
#: lineup wins 7% of 150,000-entry contests. So a SHARP share of the field is
#: resampled from the raw draws with weight exp(SHARP_BETA * proj); fitted on
#: the p50-p99 quantiles of the same three exports (sse 372 -> 48, every
#: quantile within ~3 points). RAW_MULT raw draws per field lineup.
SHARP_SHARE = 0.30
SHARP_BETA = 0.4
RAW_MULT = 6


def _draws(pool: list, n: int, rng: random.Random) -> list:
    """`n` legal lineups (pool indices) drawn slot by slot from ownership, QB
    first, then 0/1/2 of his own pass-catchers at the measured rates."""
    w = [max(float(p.get("own") or 0.0), 0.0) for p in pool]
    qbs = [i for i, p in enumerate(pool) if "QB" in p["pos"] and w[i] > 0]
    by_team: dict = {}
    for i, p in enumerate(pool):
        if p["pos"] & opt.CATCHERS and w[i] > 0:
            by_team.setdefault(p["team"], []).append(i)
    rest = [s for s in opt.SLOTS if s != "QB"]
    elig = {s: [i for i, p in enumerate(pool) if s in p["pos"] and w[i] > 0] for s in rest}
    order = sorted(rest, key=lambda s: len(elig[s]))
    p2 = FIELD_STACK_2 / FIELD_STACK_1
    out, tries = [], 0
    while len(out) < n and tries < n * 60:
        tries += 1
        q = rng.choices(qbs, weights=[w[i] for i in qbs])[0]
        picked = [q]
        mates = list(by_team.get(pool[q]["team"], []))
        k = (1 + (rng.random() < p2)) if rng.random() < FIELD_STACK_1 else 0
        for _ in range(min(k, len(mates))):
            j = rng.choices(mates, weights=[w[i] for i in mates])[0]
            mates.remove(j)
            picked.append(j)
        open_slots = list(order)
        for j in picked[1:]:      # a stacked catcher takes his own slot, else FLEX
            s = next((s for s in open_slots if s in pool[j]["pos"] and s != "FLEX"), "FLEX")
            if s not in open_slots:
                break
            open_slots.remove(s)
        else:
            spent = sum(pool[j]["salary"] for j in picked)
            ok = True
            for step, s in enumerate(open_slots):
                left = len(open_slots) - step - 1
                budget = opt.CAP - spent - left * 3000
                floor = dfs_field.MIN_SPEND - spent if left == 0 else 0
                cands = [i for i in elig[s] if i not in picked
                         and floor <= pool[i]["salary"] <= budget]
                if not cands:
                    ok = False
                    break
                j = rng.choices(cands, weights=[w[i] for i in cands])[0]
                picked.append(j)
                spent += pool[j]["salary"]
            if ok and opt._valid([pool[i] for i in picked]):
                out.append(picked)
    return out


def sample_field(pool: list, n: int, seed: int = 0) -> list:
    """`n` GPP field lineups: ownership draws, with SHARP_SHARE of them
    resampled toward projection (see SHARP_BETA)."""
    rng = random.Random(seed)
    raw = _draws(pool, n * RAW_MULT, rng)
    proj = np.array([sum(pool[i]["proj"] or 0.0 for i in lu) for lu in raw])
    tilt = np.exp(SHARP_BETA * (proj - proj.max()))
    w = (1 - SHARP_SHARE) / len(raw) + SHARP_SHARE * tilt / tilt.sum()
    pick = np.random.default_rng(seed).choice(len(raw), size=n, p=w / w.sum())
    return [raw[i] for i in pick]


#: Field lineups the tail fit uses in each world (see share_above).
TAIL_K = 20

#: Payouts are averaged over rank buckets this share of the field wide before
#: the EV is taken. A 150,000-entry contest's first prize is a 1-in-150,000
#: event that 5,000 simulated worlds never resolve; unsmoothed, the best stack
#: there changed with the random seed on 2026-10-11 (Maye, Goff, Purdy on
#: three seeds). Spread over its top 0.5%, the money is estimable.
PAY_BUCKET = 0.005


def share_above(ours: np.ndarray, totals: np.ndarray, k: int = TAIL_K) -> np.ndarray:
    """Per world, the share of the field scoring above `ours`.

    Counted directly from the sampled field, except in its top `k`, where an
    exponential tail fitted to that world's top k takes over. Without it, a
    lineup that beats all 1,000 sampled lineups is scored as 1st of 150,000.
    """
    srt = np.sort(totals, axis=1)
    m = srt.shape[1]
    above = (totals > ours[:, None]).sum(1) + 0.5 * (totals == ours[:, None]).sum(1)
    x_k = srt[:, m - k]
    excess = np.maximum((srt[:, m - k + 1:] - x_k[:, None]).mean(1), 1e-6)
    tail = k / m * np.exp(-(ours - x_k) / excess)
    return np.where(ours > x_k, tail, above / m)


def rank(pool: list, candidates: list, field_size: int, payouts=None, entry_fee: float = 1.0,
         n_sims: int = 5000, n_field: int = 1000, seed: int = 1) -> list:
    """Expected payout of each candidate GPP result ({lineup: [(player, slot)]})
    in a `field_size`-entry contest, every candidate against the same worlds and
    the same field. One dict per candidate, same order: ev ($ per entry),
    p_cash (a paid finish), p_top1 (top 1% of the field), mean_pct."""
    if not payouts:
        payouts = dfs_sim.synthetic_gpp_payouts(field_size, entry_fee=entry_fee or 1.0)
    paid = max(hi for _, hi, _ in payouts)
    table = np.zeros(field_size + 1)
    for lo, hi, amt in payouts:
        table[lo:min(hi, field_size) + 1] = amt
    width = max(1, int(field_size * PAY_BUCKET))
    for lo in range(1, field_size + 1, width):
        table[lo:lo + width] = table[lo:lo + width].mean()
    ix = {(p["name"], p["team"]): i for i, p in enumerate(pool)}
    scores = dfs_field.simulate(pool, opt._sd, theory.rho, n_sims=n_sims, seed=seed,
                                normal=lambda p: "DST" in p["pos"])
    field = sample_field(pool, n_field, seed=seed + 10)
    totals = np.stack([scores[:, lu].sum(1) for lu in field], 1)
    out = []
    for c in candidates:
        lu = [ix[(p["name"], p["team"])] for p, _ in c["lineup"]]
        share = share_above(scores[:, lu].sum(1), totals)
        ranks = np.clip(np.round(share * (field_size - 1)).astype(int) + 1, 1, field_size)
        out.append({"ev": round(float(table[ranks].mean()), 2),
                    "p_cash": round(float((ranks <= paid).mean()), 3),
                    "p_top1": round(float((share <= 0.01).mean()), 4),
                    "mean_pct": round(float(100 * (1 - share).mean()), 1)})
    return out
