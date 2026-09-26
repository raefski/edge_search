"""The DK MMA fight simulator: sample whole fights, score both corners.

WHY A SIMULATOR (the same reason as NASCAR, a different constraint)
The two fighters in a bout share one outcome: exactly one of them wins, by one
method, at one moment. The win bonus -- most of a DK MMA score -- is a function
of that single draw, and so is the length of time both men spend accumulating
strikes, control time and takedowns. A first-round knockout ends both stat
lines at once. A correlation matrix can only approximate that; sampling the
fight enforces it exactly, and gives the optimiser the true joint distribution
of a lineup's total, which is lumpy (a lineup's score is mostly "how many of my
six won, and how early") in a way no mean and standard deviation describe.

ONE SIMULATED FIGHT, IN THREE STEPS

1. WHO AND HOW -- the calibrated seven-cell table from edge/mma_market.py
   (A or B, by KO / SUB / DEC, or a draw).

2. WHEN. Competing-risks hazards on 30-second bins. Each finish cell c has
   hazard  lambda_c * g_method(t)  where g is a shared SHAPE (per round, plus a
   first-minute factor) fitted on 5,300+ priced fights, and lambda_c is solved
   per fight so that cell c's total probability is exactly the market's. So
   the market decides how LIKELY each finish is and history decides WHEN it
   comes, which is the division of labour each is actually good at. The
   decision cells are the mass that survives to the final bell.

   Measured, and it matters to DK because the bonus falls 90 -> 70 -> 45 by
   round: the per-round finish hazard in a three-round fight runs 25.7%, 21.3%,
   13.8%; a knockout is 2.5x as likely as a submission to come inside 60
   seconds of round one (19.0% vs 7.4% of round-one finishes), which is the
   +25 quick-win bonus.

3. HOW MUCH. Each corner's in-fight stat points are

       (burst_outcome + slope_outcome * minutes) * off_i^a * def_j^b * noise

   burst/slope by the corner's own outcome (a KO winner collects ~11 points of
   knockdown and finishing strikes at once, then ~3.1/min; a KO loser ~2.1/min
   from zero). off_i and def_j are the fighter's and opponent's STYLE indices:
   actual stat points over what an average fighter would have scored with the
   same outcomes and durations, shrunk toward 1. Measured relative to outcome
   on purpose -- raw per-minute rates would count a fighter's wins twice, once
   here and once in the market's win probability.
"""
from __future__ import annotations

import collections
import math

import numpy as np

from edge import mma
from edge.mma_market import CELLS

BIN_SEC = 30
BINS_PER_ROUND = mma.ROUND_SECONDS // BIN_SEC

# ---------------------------------------------------------------------------
# Step 2: WHEN. Fitted by scripts/mma_fit.py --timing
# ---------------------------------------------------------------------------
#: Relative finish hazard per round, round 1 = 1. KO and SUB separately: a
#: submission needs the fight on the mat first, so its hazard is flatter.
#:
#: Maximum likelihood on priced fights, the market setting each fight's own
#: overall finish rate (scripts/mma_fit.py --what timing). Validated on
#: fights before 2022 -> 2022-26 held out: finish share by round predicted
#: [0.247, 0.156, 0.086] vs actual [0.256, 0.165, 0.076] over 1,548
#: three-round fights. SHIPPED values are refitted on 2019-26 only, because
#: the first-minute rate drifted (below).
HAZARD_SHAPE = {
    "KO": (1.0, 0.794, 0.483, 0.475, 0.475),
    "SUB": (1.0, 0.878, 0.572, 0.421, 0.421),
}
#: Multiplier on the hazard in the first two 30-second bins of round one --
#: which is exactly the window the +25 quick-win bonus pays on. A submission
#: needs a takedown and a scramble first; a knockout needs one punch.
#: Finishes inside 60 seconds DRIFTED: 4.6% of fights 2012-16, 4.0% 2017-21,
#: 3.2% 2022-26, while the overall finish rate did not move. An all-history
#: fit predicted 4.1% on 2022-26 against 3.0% actual; fitted on 2019-26 it
#: predicts 3.39% against 3.54%.
FIRST_MINUTE = {"KO": 0.698, "SUB": 0.266}


def bin_shape(method: str, sched: int) -> np.ndarray:
    g = np.repeat(np.asarray(HAZARD_SHAPE[method][:sched], float), BINS_PER_ROUND)
    g[:2] *= FIRST_MINUTE[method]
    return g


def timing(cells: dict, sched: int, iters: int = 60) -> dict:
    """Per-bin probability mass for each finish cell, for one fight.

    Returns {cell: np.ndarray(n_bins)} for A_KO, A_SUB, B_KO, B_SUB, and
    "DEC_TOTAL": the mass that reaches the final bell. Each finish array sums
    to that cell's probability (to 1e-6).
    """
    out = timing_batch([cells], sched, iters)
    return {k: v[0] for k, v in out.items()}


_FIN = ("A_KO", "A_SUB", "B_KO", "B_SUB")
_FIN_METHOD = ("KO", "SUB", "KO", "SUB")


def timing_batch(cell_list: list[dict], sched: int, iters: int = 60,
                 shape: dict | None = None, first: dict | None = None) -> dict:
    """Vectorised timing() over many fights with the same scheduled length.

    lambda_c is solved by a damped fixed point: scale each cell's rate by the
    ratio of its target probability to the probability the current rates
    produce. Converges in well under 60 iterations for every fight in the
    2013-2026 sample.
    """
    shape = shape or HAZARD_SHAPE
    first = first or FIRST_MINUTE
    n = len(cell_list)
    nb = sched * BINS_PER_ROUND
    G = np.zeros((4, nb))
    for c, m in enumerate(_FIN_METHOD):
        g = np.repeat(np.asarray(shape[m][:sched], float), BINS_PER_ROUND)
        g[:2] *= first[m]
        G[c] = g
    target = np.array([[max(1e-9, cl[c]) for c in _FIN] for cl in cell_list])
    tot = target.sum(axis=1, keepdims=True)
    # Start from a flat hazard that gives the right total finish probability.
    lam = target / tot * (-np.log(np.clip(1 - tot, 1e-6, 1)) / nb)
    for _ in range(iters):
        mass = _masses(lam, G)
        got = mass.sum(axis=2)
        lam = lam * np.clip(target / np.maximum(got, 1e-12), 0.2, 5.0) ** 0.9
    mass = _masses(lam, G)
    out = {c: mass[:, i, :] for i, c in enumerate(_FIN)}
    out["DEC_TOTAL"] = 1 - mass.sum(axis=(1, 2))
    return out


def _masses(lam: np.ndarray, G: np.ndarray) -> np.ndarray:
    """(n, 4, nb) probability that the fight ends in bin k by cell c."""
    rate = lam[:, :, None] * G[None, :, :]               # (n, 4, nb)
    tot = rate.sum(axis=1)                               # (n, nb)
    end_here = 1 - np.exp(-tot)
    surv = np.exp(-np.cumsum(tot, axis=1))
    surv_before = np.concatenate([np.ones((tot.shape[0], 1)), surv[:, :-1]], axis=1)
    share = rate / np.maximum(tot[:, None, :], 1e-15)
    return surv_before[:, None, :] * end_here[:, None, :] * share


# ---------------------------------------------------------------------------
# Step 3: HOW MUCH. Fitted by scripts/mma_fit.py --stats
# ---------------------------------------------------------------------------
#: (burst, per-minute slope) of a corner's in-fight DK stat points, by that
#: corner's outcome, for an AVERAGE fighter (style indices 1.0). A decision
#: always lasts the full 15 or 25 minutes, so for the DEC rows only the two
#: end points are identified and the line is drawn through them.
#:
#: Fitted on 2019-26 (scripts/mma_fit.py --what stats). NOT on all history:
#: stat volume per fight ran ~7% BELOW today's through 2019 (actual/baseline
#: 0.91-0.94 every year 2010-2019, 0.98-1.04 every year 2020-26), and pooling
#: would under-project every current fighter.
#:
#: Read it as: a KO winner banks ~11 points at the moment of the finish (the
#: knockdown is 10 by itself) and 3.2 a minute before it; a loser banks about
#: 2.3 a minute whatever the method; a decision winner ends near 51 stat
#: points in three rounds and the loser near 33.
BASELINE = {
    "W-KO": (11.35, 3.201), "L-KO": (0.00, 2.280),
    "W-SUB": (3.17, 3.606), "L-SUB": (0.00, 2.270),
    "W-DEC": (3.17, 3.217), "L-DEC": (9.19, 1.557),
    "D-DEC": (0.00, 3.038),
}
#: Style-index exponents and shrinkage (pseudo-fights toward 1.0). Exponents
#: 0.8/0.8 are best on training fights (2016-21) AND on held-out 2022-26:
#: with outcome and duration known, MAE 10.51 with no style -> 9.82, corr
#: 0.777 -> 0.809. Both halves carry signal: who the fighter is AND who he is
#: facing.
OFF_EXP = 0.8
DEF_EXP = 0.8
K_OFF = 3.0
K_DEF = 5.0
#: The typical per-fight expected stat points the shrinkage is denominated in.
E_TYPICAL = 32.0
#: Index for a fighter with no UFC history. Measured, not assumed: 1,245 UFC
#: debuts scored 0.986 of an average fighter's stat points for their outcome.
DEBUT_OFF = 1.0
DEBUT_DEF = 1.0
#: Multiplicative noise on a corner's stat points: gamma, shape 4.49 (the
#: actual/expected ratio has sd 0.475 around 1.007). The two corners' ratios
#: are correlated -0.12 -- NEGATIVELY, because given the result, one man
#: out-landing his expectation is usually the other being out-landed. The
#: simulator draws them independently, which is conservative for a lineup that
#: holds both corners (it understates how much they cancel) and irrelevant for
#: the rest.
NOISE_SHAPE = 4.49
NOISE_SHARED = 0.0

#: DOMINANCE: stat points scale by exp(c * logit(own win probability)), with
#: c depending on whether the corner won or lost. Given the result and the
#: length of the fight, a favourite still out-produces an average fighter and
#: an underdog under-produces -- and it is sharpest in DEFEAT: on held-out
#: 2022-26 fights a heavy favourite who LOSES scores 1.23x the stat points
#: his outcome implies, a heavy underdog who loses 0.86x. The loser of a
#: mismatch was dominated for however long it lasted; the favourite who got
#: caught was usually winning until he was not. Fitted on 2016-21 priced
#: fights; held-out MAE (outcome known) 9.908 -> 9.863.
DOM_WIN = 0.02
DOM_LOSS = 0.10


def outcome_key(won: bool, draw: bool, method: str) -> str:
    return ("D" if draw else ("W" if won else "L")) + "-" + method


def expected_stats(outcome: str, minutes, off: float = 1.0, dfn: float = 1.0,
                   p_win: float | None = None):
    """E[stat points] for one corner. `p_win` is the corner's own calibrated
    win probability, for the dominance term; None (no market) means none."""
    b, s = BASELINE.get(outcome, (0.0, 2.1))
    dom = 1.0
    if p_win is not None:
        lp = math.log(min(max(p_win, 1e-4), 1 - 1e-4) / (1 - min(max(p_win, 1e-4), 1 - 1e-4)))
        dom = math.exp((DOM_LOSS if outcome.startswith("L") else DOM_WIN) * lp)
    return (b + s * np.asarray(minutes)) * (off ** OFF_EXP) * (dfn ** DEF_EXP) * dom


def win_probs(cells: dict) -> tuple[float, float]:
    """(P(A wins), P(B wins)) given it is not a draw, from an outcome table."""
    pa = cells["A_KO"] + cells["A_SUB"] + cells["A_DEC"]
    pb = cells["B_KO"] + cells["B_SUB"] + cells["B_DEC"]
    return pa / (pa + pb), pb / (pa + pb)


def style_indices(before=None, fights=None) -> dict:
    """{fighter key: {"off", "def", "n", "minutes"}} from UFC fights before
    `before`. off = the fighter's own stat points / baseline expectation given
    his outcomes; def = his OPPONENTS' stat points / theirs. Both shrunk."""
    fights = fights if fights is not None else mma.fights()
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0, 0.0])
    for f in fights:
        if before is not None and (f["date"] is None or f["date"] >= before):
            continue
        if f["nc"]:
            continue
        mins = f["elapsed"] / 60.0
        for i, me in enumerate(f["f"]):
            opp = f["f"][1 - i]
            e = float(expected_stats(outcome_key(me["won"], f["draw"], f["method"]), mins))
            a = acc[me["key"]]
            a[0] += me["dk_stats"]
            a[1] += e
            a[4] += 1
            a[5] += mins
            b = acc[opp["key"]]
            b[2] += me["dk_stats"]
            b[3] += e
    out = {}
    for k, a in acc.items():
        out[k] = {"off": (a[0] + K_OFF * E_TYPICAL) / (a[1] + K_OFF * E_TYPICAL),
                  "def": (a[2] + K_DEF * E_TYPICAL) / (a[3] + K_DEF * E_TYPICAL),
                  "n": a[4], "minutes": a[5]}
    return out


# ---------------------------------------------------------------------------
# Expected points, analytically (for the backtest and the board)
# ---------------------------------------------------------------------------
def _bin_minutes(nb: int) -> np.ndarray:
    return (np.arange(nb) + 0.5) * BIN_SEC / 60.0


def expected_points(cells: dict, p_draw: float, sched: int, tim: dict,
                    idx_a: tuple, idx_b: tuple,
                    dominance: bool = True) -> tuple[float, float]:
    """E[DK points] for corner A and corner B of one fight, exactly (no
    sampling). idx_* = (off, def) of that corner."""
    nb = sched * BINS_PER_ROUND
    mins = _bin_minutes(nb)
    rnd = np.arange(nb) // BINS_PER_ROUND + 1
    sec_in_round = (np.arange(nb) % BINS_PER_ROUND + 0.5) * BIN_SEC
    bonus = np.array([mma.WIN_BONUS.get(int(r), 40.0) for r in rnd])
    quick = (rnd == 1) & (sec_in_round <= mma.QUICK_WIN_SECONDS)
    bonus = bonus + np.where(quick, mma.QUICK_WIN_BONUS, 0.0)
    full = sched * mma.ROUND_SECONDS / 60.0
    ea = eb = 0.0
    oa, da = idx_a
    ob, db = idx_b
    pws = win_probs(cells) if dominance else (None, None)
    for side, (me_off, opp_def) in ((0, (oa, db)), (1, (ob, da))):
        pw_ = pws[side]
        tot = 0.0
        for meth in ("KO", "SUB"):
            win_mass = tim[("A_" if side == 0 else "B_") + meth]
            lose_mass = tim[("B_" if side == 0 else "A_") + meth]
            tot += float((win_mass * (bonus + expected_stats(
                f"W-{meth}", mins, me_off, opp_def, pw_))).sum())
            tot += float((lose_mass * expected_stats(
                f"L-{meth}", mins, me_off, opp_def, pw_)).sum())
        pw = cells["A_DEC" if side == 0 else "B_DEC"]
        pl = cells["B_DEC" if side == 0 else "A_DEC"]
        tot += pw * (mma.DECISION_BONUS + float(expected_stats("W-DEC", full, me_off, opp_def, pw_)))
        tot += pl * float(expected_stats("L-DEC", full, me_off, opp_def, pw_))
        tot += p_draw * float(expected_stats("D-DEC", full, me_off, opp_def, pw_))
        if side == 0:
            ea = tot
        else:
            eb = tot
    return ea, eb


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
def simulate(bouts: list[dict], n_sims: int = 5000, seed: int = 0) -> dict:
    """Sample every bout on a card.

    `bouts` is a list of {"cells", "p_draw", "sched", "idx": [(off,def) A,
    (off,def) B]} dicts in card order. Returns

        points   (n_sims, 2*len(bouts))   DK points, column 2i = A, 2i+1 = B
        winner   (n_sims, len(bouts))     0 = A, 1 = B, -1 = draw
        method   (n_sims, len(bouts))     0 KO, 1 SUB, 2 DEC
        rnd      (n_sims, len(bouts))
        minutes  (n_sims, len(bouts))
    """
    rng = np.random.default_rng(seed)
    nf = len(bouts)
    pts = np.zeros((n_sims, 2 * nf))
    winner = np.zeros((n_sims, nf), dtype=np.int8)
    method = np.zeros((n_sims, nf), dtype=np.int8)
    rnd_out = np.zeros((n_sims, nf), dtype=np.int8)
    mins_out = np.zeros((n_sims, nf))
    for j, bt in enumerate(bouts):
        sched = int(bt.get("sched") or 3)
        nb = sched * BINS_PER_ROUND
        tim = bt.get("timing") or timing(bt["cells"], sched)
        # One categorical over every (cell, bin) plus the three full-time ends.
        probs, labels = [], []
        for c in _FIN:
            probs.append(tim[c])
            labels.extend((c, k) for k in range(nb))
        cells = bt["cells"]
        dec_total = max(0.0, float(tim["DEC_TOTAL"]))
        dsum = cells["A_DEC"] + cells["B_DEC"] + bt["p_draw"]
        ends = np.array([cells["A_DEC"], cells["B_DEC"], bt["p_draw"]]) / dsum * dec_total
        probs.append(ends)
        labels.extend((("A_DEC", nb), ("B_DEC", nb), ("DRAW", nb)))
        p = np.concatenate(probs)
        p = np.clip(p, 0, None)
        p = p / p.sum()
        draw_idx = rng.choice(len(p), size=n_sims, p=p)

        cell_of = np.array([lab[0] for lab in labels])[draw_idx]
        bin_of = np.array([lab[1] for lab in labels])[draw_idx]
        fin = bin_of < nb
        # Uniform time inside the 30-second bin; a decision runs the full fight.
        sec = np.where(fin, (bin_of + rng.random(n_sims)) * BIN_SEC,
                       sched * mma.ROUND_SECONDS)
        minutes = sec / 60.0
        rnd = np.where(fin, (sec // mma.ROUND_SECONDS).astype(int) + 1, sched)
        rnd = np.minimum(rnd, sched)
        sec_in_round = sec - (rnd - 1) * mma.ROUND_SECONDS

        a_won = np.char.startswith(cell_of.astype(str), "A_")
        b_won = np.char.startswith(cell_of.astype(str), "B_")
        is_draw = cell_of == "DRAW"
        meth = np.where(np.char.endswith(cell_of.astype(str), "KO"), 0,
                        np.where(np.char.endswith(cell_of.astype(str), "SUB"), 1, 2))
        winner[:, j] = np.where(a_won, 0, np.where(b_won, 1, -1))
        method[:, j] = meth
        rnd_out[:, j] = rnd
        mins_out[:, j] = minutes

        bonus = np.array([mma.WIN_BONUS.get(int(r), 40.0) for r in range(1, 6)])[rnd - 1]
        bonus = np.where(meth == 2, mma.DECISION_BONUS, bonus)
        bonus = bonus + np.where((meth != 2) & (rnd == 1)
                                 & (sec_in_round <= mma.QUICK_WIN_SECONDS),
                                 mma.QUICK_WIN_BONUS, 0.0)

        shared = (rng.gamma(NOISE_SHAPE / max(NOISE_SHARED, 1e-9),
                            max(NOISE_SHARED, 1e-9) / NOISE_SHAPE, n_sims)
                  if NOISE_SHARED > 0 else np.ones(n_sims))
        own_shape = NOISE_SHAPE / max(1e-9, 1 - NOISE_SHARED)
        (oa, da), (ob, db) = bt["idx"]
        pws = win_probs(cells) if bt.get("dominance", True) else (None, None)
        for side, won, me_off, opp_def in ((0, a_won, oa, db), (1, b_won, ob, da)):
            lost = ~won & ~is_draw
            meth_name = np.array(["KO", "SUB", "DEC"])[meth]
            e = np.zeros(n_sims)
            for m_i, m_name in enumerate(("KO", "SUB", "DEC")):
                sel = meth == m_i
                if not sel.any():
                    continue
                for flag, pre in ((won, "W"), (lost, "L"), (is_draw, "D")):
                    s2 = sel & flag
                    if s2.any():
                        e[s2] = expected_stats(f"{pre}-{m_name}", minutes[s2],
                                               me_off, opp_def, pws[side])
            del meth_name
            noise = rng.gamma(own_shape, 1.0 / own_shape, n_sims) * shared
            pts[:, 2 * j + side] = e * noise + np.where(won, bonus, 0.0)
    return {"points": pts, "winner": winner, "method": method, "rnd": rnd_out,
            "minutes": mins_out}
