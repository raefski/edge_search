"""DK MMA Classic optimizer: six fighters, $50,000 -- scored EXHAUSTIVELY.

A card is 20-28 fighters, so there are C(24,6) = 134,596 to C(28,6) = 376,740
six-fighter lineups before the cap removes any. That is small enough to score
every one of them against every simulated card, which the other sports here
cannot do and NASCAR (C(37,6) = 2.3M) chose not to. So there is no hill
climb, no randomised fill and no "the heuristic reached the same lineup the
exhaustive search did" caveat: the lineup returned is the argmax over the
whole legal space, to within simulation noise.

Simulation noise is handled in two stages. Every legal lineup is screened on
the first SCREEN_SIMS simulated cards; the best FINAL_K are re-scored on all
of them. P(top 1%) is a rare event and a lineup ranked on 3,000 draws of it
has a standard error of about 0.3 points of probability, which is the same
size as the gaps between the best few hundred lineups.

WHAT IS DELIBERATELY NOT A RULE HERE
Both corners of one fight are ALLOWED. Every MMA strategy source says not to
roster them, and for GPP the simulation agrees on its own (one of the two is
guaranteed to lose, which caps the lineup's ceiling). But it is a result of
the objective, not a constraint: a cash lineup that holds a cheap underdog
AND his favourite opponent is guaranteed one winner, and whether that is worth
it depends on the prices. The optimizer is allowed to find out.
"""
from __future__ import annotations

import itertools

import numpy as np

from edge import dfs_mma_theory as theory

ROSTER_SIZE = 6
CAP = 50000
SCREEN_SIMS = 3000
FINAL_K = 400
CHUNK = 8192


def legal_lineups(salaries: np.ndarray, cap: int = CAP,
                  locked: list[int] | None = None,
                  banned: list[int] | None = None) -> np.ndarray:
    """(m, 6) every set of six distinct fighters under the cap."""
    n = len(salaries)
    banned = set(banned or [])
    locked = [i for i in (locked or []) if i not in banned]
    rest = [i for i in range(n) if i not in banned and i not in locked]
    need = ROSTER_SIZE - len(locked)
    if need < 0:
        raise ValueError("more than six fighters locked")
    combos = np.fromiter(itertools.chain.from_iterable(
        itertools.combinations(rest, need)), dtype=np.int16).reshape(-1, need)
    if locked:
        combos = np.hstack([np.tile(np.array(locked, dtype=np.int16),
                                    (len(combos), 1)), combos])
    sal = salaries[combos].sum(axis=1)
    return combos[sal <= cap]


def _indicator(n: int, lineups: np.ndarray) -> np.ndarray:
    ind = np.zeros((n, len(lineups)), dtype=np.float32)
    cols = np.arange(len(lineups))
    for c in range(lineups.shape[1]):
        ind[lineups[:, c], cols] = 1.0
    return ind


def _score(points: np.ndarray, lineups: np.ndarray, line: np.ndarray) -> np.ndarray:
    """P(lineup total > line) per lineup, over the rows of `points`. A matrix
    product per chunk -- (sims x fighters) @ (fighters x lineups) -- because
    the gather it replaced cost 5.6s a theory on a 101,701-lineup card."""
    pts = points.astype(np.float32)
    line = line.astype(np.float32)[:, None]
    out = np.empty(len(lineups))
    for s in range(0, len(lineups), CHUNK):
        chunk = lineups[s:s + CHUNK]
        tot = pts @ _indicator(pts.shape[1], chunk)
        out[s:s + CHUNK] = (tot > line).mean(axis=0)
    return out


def _mean(points: np.ndarray, lineups: np.ndarray) -> np.ndarray:
    means = points.mean(axis=0)
    return means[lineups].sum(axis=1)


def optimize(points: np.ndarray, salaries: np.ndarray, lines: dict,
             mode: str = "cash", locked=None, banned=None,
             lineups: np.ndarray | None = None) -> dict:
    """The best legal lineup for one theory.

    `lines` = {"cash": (n_sims,), "gpp": (n_sims,)} from
    dfs_mma_theory.thresholds -- computed on the SAME simulated cards as
    `points`, which is the whole point.
    """
    if lineups is None:
        lineups = legal_lineups(salaries, locked=locked, banned=banned)
    if not len(lineups):
        return {"error": "no legal lineup under the cap"}
    line = lines[mode]
    n_screen = min(SCREEN_SIMS, len(points))
    screen = _score(points[:n_screen], lineups, line[:n_screen])
    # Tie-break on the mean so a flat screen (rare in cash, common in a thin
    # GPP) still orders sensibly.
    key = screen + 1e-6 * _mean(points, lineups)
    top = np.argsort(-key)[:FINAL_K]
    final = _score(points, lineups[top], line)
    final = final + 1e-6 * _mean(points, lineups[top])
    best = top[int(np.argmax(final))]
    ranked = top[np.argsort(-final)]
    return {"idx": [int(i) for i in lineups[best]], "objective": float(final.max()),
            "runners_up": [[int(i) for i in lineups[j]] for j in ranked[1:6]],
            "n_legal": int(len(lineups)), "mode": mode}


def portfolio(points: np.ndarray, salaries: np.ndarray, lines: dict, n: int,
              mode: str = "gpp", max_overlap: int = 4) -> list[dict]:
    """`n` lineups for one theory, greedily, each sharing at most
    `max_overlap` fighters with every lineup already taken."""
    lineups = legal_lineups(salaries)
    line = lines[mode]
    n_screen = min(SCREEN_SIMS, len(points))
    screen = _score(points[:n_screen], lineups, line[:n_screen])
    top = np.argsort(-(screen + 1e-6 * _mean(points, lineups)))[:FINAL_K * 3]
    final = _score(points, lineups[top], line)
    order = top[np.argsort(-final)]
    score_of = dict(zip(top.tolist(), final.tolist()))
    picked: list[set] = []
    out = []
    for j in order:
        s = set(int(i) for i in lineups[j])
        if all(len(s & p) <= max_overlap for p in picked):
            picked.append(s)
            out.append({"idx": sorted(s), "objective": float(score_of[j])})
        if len(out) >= n:
            break
    return out
