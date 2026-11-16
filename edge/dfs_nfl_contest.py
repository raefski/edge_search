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

from edge import dfs_field
from edge import dfs_leverage as lev
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
#:
#: 2026-11-15: the resample is now WITHOUT replacement (below). With
#: replacement, the sharp share kept re-picking the same few best-projected
#: draws: 24-32% of simulated lineups were exact duplicates on the 8 Main-slate
#: GPP exports 9/13-11/08, against 3-7% in the real fields (NHL had the same
#: bug; dfs_leverage.tilt). Refitted leave-one-slate-out on those 8 exports
#: (stack shares + p50-p99 projection quantiles), SHARP_SHARE 0.45 was chosen
#: on 7 of 8 held-out slates, SHARP_BETA 0.4 on 7 of 8, stack rates unchanged.
#: Refitted again the same day jointly with FIELD_BRING_BACK (stack shares,
#: bring-back share, p50-p99 projection quantiles; grid .30/.45/.60): .60 on 7
#: of 8 held-out slates. .60 is the grid's top and the field's p99 projection
#: still sits ~4 pts under the real one, so the sharp end may want more.
SHARP_SHARE = 0.60

#: Draw rate of one opposing WR/TE (a "bring-back") into a field lineup, on
#: top of those ownership draws land by accident. Real Main-slate GPP fields
#: carry an opposing WR/TE of their QB in 26-48% of lineups (8 exports
#: 9/13-11/08); without this term the sim had 12-26% and paired opposing
#: players LESS than independent (lift .57-.79 vs real 1.07-1.35). Fitted
#: leave-one-slate-out on those 8 exports (grid 0-.4): 0.2 on 8 of 8, held-out
#: field-shape loss down 2-4x on every slate (11/08: 386 -> 92). Mass-150
#: replay on the same slates (scratch, 2026-11-15): EV ranking +65% -> +104%,
#: 29 -> 36 top-1% finishes.
FIELD_BRING_BACK = 0.2
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
        backs = by_team.get(pool[q].get("opp_team"), [])
        if FIELD_BRING_BACK and backs and rng.random() < FIELD_BRING_BACK:
            picked.append(rng.choices(backs, weights=[w[i] for i in backs])[0])
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
    if len(raw) <= n:
        # a pool too small for n distinct draws (tests, tiny showdown-like
        # slates): every draw once, the rest weighted with replacement
        extra = np.random.default_rng(seed).choice(len(raw), size=n - len(raw), p=w / w.sum())
        return list(raw) + [raw[i] for i in extra]
    # Gumbel top-k: a weighted draw WITHOUT replacement, so duplicates come
    # only from ownership draws that coincide, which is how real ones arise.
    keys = np.log(w) + np.random.default_rng(seed).gumbel(size=len(raw))
    return [raw[i] for i in np.argsort(-keys)[:n]]


#: The tail fit, payout smoothing and EV now live in edge/dfs_leverage.py,
#: shared by every sport; these names stay for callers and tests.
TAIL_K = lev.TAIL_K
PAY_BUCKET = lev.PAY_BUCKET


def share_above(ours: np.ndarray, totals: np.ndarray, k: int = TAIL_K) -> np.ndarray:
    """Per world, the share of the field (unsorted `totals`) scoring above
    `ours` -- see edge/dfs_leverage.share_above."""
    return lev.share_above(ours, np.sort(totals, axis=1), k)


def scores(pool: list, n_sims: int = 5000, seed: int = 1) -> np.ndarray:
    """Joint simulated DK points for every pool player (n_sims x len(pool)):
    the model's own mean, sd and measured same-game correlations."""
    return dfs_field.simulate(pool, opt._sd, theory.rho, n_sims=n_sims, seed=seed,
                              normal=lambda p: "DST" in p["pos"])


def rank(pool: list, candidates: list, field_size: int, payouts=None, entry_fee: float = 1.0,
         n_sims: int = 5000, n_field: int = 1000, seed: int = 1) -> list:
    """Expected payout of each candidate GPP result ({lineup: [(player, slot)]})
    in a `field_size`-entry contest, every candidate against the same worlds and
    the same field. One dict per candidate, same order: ev ($ per entry),
    p_cash (a paid finish), p_top1 (top 1% of the field), mean_pct."""
    ix = {(p["name"], p["team"]): i for i, p in enumerate(pool)}
    sims = scores(pool, n_sims=n_sims, seed=seed)
    field = sample_field(pool, n_field, seed=seed + 10)
    cands = [[ix[(p["name"], p["team"])] for p, _ in c["lineup"]] for c in candidates]
    out = lev.contest_ev(sims, cands, field, field_size, payouts, entry_fee or 1.0)
    return [{"ev": round(r["ev"], 2), "p_cash": round(r["p_cash"], 3),
             "p_top1": round(r["p_top1"], 4), "mean_pct": round(r["mean_pct"], 1)} for r in out]
