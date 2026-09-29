"""DK NHL Classic optimiser: C C W W W D D G UTIL, $50,000, 3+ teams, 2+ games.

The NASCAR search (edge/dfs_opt_nascar.py) -- a randomised fill plus a hill
climb whose candidate swaps are scored in one vectorised percentile pass over
the simulation -- with hockey's constraints added:

  * positions: exactly one goalie; at least 2 C, 3 W, 2 D among eight skaters
    (the eighth fills UTIL, so the counts are the whole slot test);
  * DraftKings' minimums: players from 3+ teams and 2+ games;
  * NO SKATER FACING YOUR GOALIE. His saves and wins come from that team
    failing to score; rostering its skaters bets against yourself. The
    simulation already prices this -- the correlation is strongly negative --
    but it is also a rule, because a percentile search can wander into it on
    a thin board and the cost of being wrong is a lineup that cannot win.

WHICH LINES to stack is left to the simulation -- a goal pays linemates
together, so a 95th-percentile objective finds them on its own. The SHAPE is a
rule for GPP: two team stacks of 3+ skaters (the 3-3-2 and 4-3-1 builds every
NHL DFS guide calls the bread and butter) and no more than 5 from one team.
Left alone on the 2026-09-29 board the search put SIX Oilers in one lineup:
defensible on a 4.06-goal implied total, but one team's night is then the whole
tournament. Cash has no shape rule at all.
"""
from __future__ import annotations

import random
from collections import Counter

import numpy as np

from edge import dfs_nhl_theory as theory, nhl

ROSTER_SIZE = 9
NEEDS = {"C": 2, "W": 3, "D": 2}
CANDIDATES = {"C": 24, "W": 34, "D": 26, "G": 12}


#: GPP stack shape: the two biggest team stacks must be at least this big.
GPP_STACKS = (3, 3)
MAX_FROM_TEAM = 5


def _valid(pool, idx, min_stack=()) -> bool:
    if len(set(idx)) != ROSTER_SIZE:
        return False
    rows = [pool[i] for i in idx]
    pos = Counter(r["pos"] for r in rows)
    if pos.get("G", 0) != 1 or any(pos.get(k, 0) < v for k, v in NEEDS.items()):
        return False
    if sum(r["salary"] for r in rows) > nhl.SALARY_CAP:
        return False
    # DK counts the 3-team minimum over SKATERS only -- a goalie's team does not
    # count ("NHL lineups require selecting skaters from at least 3 different
    # teams", DK's own rejection of a 2026-09-29 lineup that had two skater
    # teams plus a third team's goalie).
    if (len({r["team"] for r in rows if r["pos"] != "G"}) < nhl.MIN_TEAMS
            or len({r["game"] for r in rows}) < nhl.MIN_GAMES):
        return False
    goalie = next(r for r in rows if r["pos"] == "G")
    if any(r["team"] == goalie.get("opp") for r in rows if r["pos"] != "G"):
        return False
    if min_stack:
        sizes = sorted(Counter(r["team"] for r in rows if r["pos"] != "G").values(), reverse=True)
        if sizes[0] > MAX_FROM_TEAM or any(
                (sizes[k] if k < len(sizes) else 0) < need for k, need in enumerate(min_stack)):
            return False
    return True


def _fill(rng, pool, order_by_pos, min_stack):
    """A random legal lineup, biased toward the top of each position list."""
    for _ in range(200):
        g = rng.choice(order_by_pos["G"][:6])
        picked, spent = [g], pool[g]["salary"]
        need = ["C", "C", "W", "W", "W", "D", "D", rng.choice(["C", "W", "W", "D"])]
        rng.shuffle(need)
        ok = True
        for i, pos in enumerate(need):
            left = len(need) - i - 1
            budget = nhl.SALARY_CAP - spent - left * 2500
            cands = [j for j in order_by_pos[pos][:18] if j not in picked
                     and pool[j]["salary"] <= budget and pool[j]["team"] != pool[g].get("opp")]
            if not cands:
                ok = False
                break
            j = rng.choice(cands)
            picked.append(j)
            spent += pool[j]["salary"]
        if ok and _valid(pool, picked, min_stack):
            return picked
    return None


def _hill_climb(pool, sim, idx, cand, mode, own_weight, owns, min_stack):
    q = theory.GPP_PCT if mode == "gpp" else theory.CASH_PCT
    idx = list(idx)
    totals = sim[:, idx].sum(axis=1)

    def value(tot, lineup):
        v = float(np.percentile(tot, q))
        if own_weight and mode == "gpp":
            v -= own_weight * float(owns[lineup].sum()) / 100.0
        return v

    best = value(totals, idx)
    improved = True
    while improved:
        improved = False
        for s in range(ROSTER_SIZE):
            cur = idx[s]
            group = "G" if pool[cur]["pos"] == "G" else None
            pool_c = cand["G"] if group else cand["skaters"]
            trial_ids = []
            for j in pool_c:
                if j in idx:
                    continue
                trial = idx[:s] + [j] + idx[s + 1:]
                if _valid(pool, trial, min_stack):
                    trial_ids.append(j)
            if not trial_ids:
                continue
            arr = np.array(trial_ids)
            base = totals - sim[:, cur]
            scores = np.percentile(base[:, None] + sim[:, arr], q, axis=0)
            if own_weight and mode == "gpp":
                scores = scores - own_weight * (float(owns[idx].sum()) - owns[cur] + owns[arr]) / 100.0
            k = int(np.argmax(scores))
            if scores[k] > best + 1e-9:
                idx[s] = int(arr[k])
                totals = base + sim[:, idx[s]]
                best = float(scores[k])
                improved = True
    return idx, best


def optimize(pool: list, sim: np.ndarray, mode: str = "cash", iters: int = 250, seed: int = 0,
             own_weight: float = 0.0, min_stack: tuple | None = None,
             exclude: set | None = None) -> dict | None:
    """Best legal lineup under one theory, or None if none exists."""
    if min_stack is None:
        min_stack = GPP_STACKS if mode == "gpp" else ()
    rng = random.Random(seed)
    key = "ceil" if mode == "gpp" else "floor"
    usable = [i for i, p in enumerate(pool) if p.get("salary")
              and not (exclude and p["name"] in exclude)]
    order = {pos: sorted((i for i in usable if pool[i]["pos"] == pos),
                         key=lambda i: -(pool[i].get(key) or 0) / pool[i]["salary"] * 1000
                         - 0.15 * (pool[i].get(key) or 0))
             for pos in ("C", "W", "D", "G")}
    by_key = {pos: sorted((i for i in usable if pool[i]["pos"] == pos),
                          key=lambda i: -(pool[i].get(key) or 0)) for pos in ("C", "W", "D", "G")}
    cand = {"G": by_key["G"][:CANDIDATES["G"]]}
    cand["skaters"] = sorted({i for pos in ("C", "W", "D")
                              for i in by_key[pos][:CANDIDATES[pos]] + order[pos][:CANDIDATES[pos]]})
    owns = np.array([float(p.get("own") or 0.0) for p in pool])
    best, best_v = None, None
    for _ in range(iters):
        start = _fill(rng, pool, order, min_stack)
        if start is None:
            continue
        idx, v = _hill_climb(pool, sim, start, cand, mode, own_weight, owns, min_stack)
        if best_v is None or v > best_v:
            best, best_v = idx, v
    if best is None:
        return None
    return _result(pool, sim, best)


def assign_slots(rows: list[dict]) -> list[tuple[str, dict]]:
    """C C W W W D D G UTIL, the leftover skater in UTIL."""
    left = sorted(rows, key=lambda r: -r["proj"])
    out = []
    for slot in ("C", "C", "W", "W", "W", "D", "D", "G"):
        r = next(r for r in left if r["pos"] == slot)
        left.remove(r)
        out.append((slot, r))
    out.append(("UTIL", left[0]))
    return out


def _result(pool, sim, idx) -> dict:
    rows = [pool[i] for i in idx]
    stacks = Counter(r["team"] for r in rows if r["pos"] != "G")
    lines = Counter((r["team"], r.get("line")) for r in rows if r["pos"] != "G" and r.get("line"))
    return {"slots": assign_slots(rows), "lineup": rows,
            "salary": int(sum(r["salary"] for r in rows)),
            "own": round(sum(r.get("own") or 0.0 for r in rows), 1),
            "stacks": dict(stacks.most_common()),
            "line_stacks": {f"{t} {ln}": n for (t, ln), n in lines.most_common() if n >= 2},
            **theory.describe(sim, idx)}


def portfolio(pool: list, sim: np.ndarray, n: int, max_overlap: int = 5, mode: str = "gpp",
              iters: int = 150, seed: int = 0, **kw) -> list[dict]:
    """`n` GPP lineups that differ: at most `max_overlap` of 9 shared, and each
    new lineup bans the previous one's highest-projected skater."""
    out, banned = [], set()
    for i in range(n * 5):
        if len(out) >= n:
            break
        res = optimize(pool, sim, mode=mode, iters=iters, seed=seed + i, exclude=banned, **kw)
        if res is None:
            break
        names = {r["name"] for r in res["lineup"]}
        if any(len(names & {r["name"] for r in o["lineup"]}) > max_overlap for o in out):
            banned.add(max((r for r in res["lineup"] if r["pos"] != "G"),
                           key=lambda r: r["proj"])["name"])
            continue
        out.append(res)
        banned.add(max((r for r in res["lineup"] if r["pos"] != "G"),
                       key=lambda r: r["proj"])["name"])
    return out
