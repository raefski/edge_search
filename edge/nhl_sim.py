"""The NHL game simulator: whole games, sampled, so hockey's correlations are
a consequence of the game rather than a matrix someone had to fit.

WHY A SIMULATOR AND NOT mean +/- z*sd
A goal pays three people at once -- 8.5 to the scorer and 5 to each of up to
two assisters -- and those three are almost always linemates or power-play
unit-mates. That is the whole of hockey DFS strategy: a line stack scores
together or not at all, and the ceiling of a lineup lives in that joint tail.
It also has hard floors a normal cannot see: a goal is also a shot on goal, a
goalie's saves are the other team's shots minus its goals, and a goalie wins
exactly when his skaters outscore theirs.

So each simulated game samples, in order:

  1. each team's goals, Poisson around the market-implied mean (moneyline and
     total solved together -- implied_goals), a tie going to overtime/shootout,
     and an empty-net goal for a team leading late by one or two;
  2. for every goal, a SCORER drawn in proportion to each skater's goal rate,
     and up to two ASSISTERS drawn with a strong preference for the scorer's
     linemates and power-play unit-mates (LINE_AFFINITY / PP_AFFINITY) --
     reweighted so every skater's expected assists still equal his target;
  3. shots: every goal is a shot, plus a negative-binomial excess scaled by a
     per-team game factor, so a team's shooters rise and fall together;
  4. blocks: negative-binomial, scaled by the OPPONENT's shot factor;
  5. the starting goalie's saves from the shots his opponents actually took
     (rescaled to his saves prop when there is one), goals against excluding
     empty-netters, and win / OT loss / shutout / 35+ saves.

DK points follow from edge/nhl.py's scoring. The result is an
(n_sims x n_players) matrix the optimiser reads percentiles off, exactly as
the NASCAR build does.
"""
from __future__ import annotations

import math

import numpy as np

from edge import nhl

#: How much likelier a linemate is to assist than a random teammate with the
#: same assist rate. A PRIOR: box scores carry no lines, so this waits on a
#: season of DailyFaceoff lines joined to play-by-play (NHL_STATUS.md).
LINE_AFFINITY = 4.0
PP_AFFINITY = 3.0

# Everything below FITTED 2026-09-28 on the 2025-26 regular season, 1,311 games
# of NHL box scores (scripts/nhl_fit.py --sim).
#: Goals with no assist, and assisted goals with two. Assists per goal
#: measured 1.689 = (1 - P_UNASSISTED) * (1 + P_TWO_ASSISTS).
P_UNASSISTED = 0.06
P_TWO_ASSISTS = 0.80
ASSISTS_PER_GOAL = (1 - P_UNASSISTED) * (1 + P_TWO_ASSISTS)
#: Regulation is not independent Poisson: trailing teams push, and 24.9% of
#: games reach overtime where independent Poisson at the league rate gives
#: 16.8%. So a one-goal game is pulled level with TIE_PULL, regulation scoring
#: runs at REG_SCALE of the market mean to leave room for that and for OT and
#: empty-net goals, and a team up by one or two late scores into an empty net
#: with P_EMPTY_NET. Solved together by simulation to reproduce the season:
#: OT/SO share 0.250 (actual 0.249), empty-net goals 0.383 a game (0.378),
#: goals per team-game 3.142 (3.128).
REG_SCALE = 0.905
TIE_PULL = 0.25
P_EMPTY_NET = 0.82
#: Of games reaching overtime, 63.5% end on an OT goal; the rest in a shootout,
#: which DraftKings scores only as +1.5 per shootout goal (not modelled).
P_OT_GOAL = 0.635
#: Negative-binomial shapes (larger = closer to Poisson). Per player over the
#: season: shots 15.2, blocks 9.8, of which the per-team game factor (gamma
#: shape 70, from team shots per game) explains part -- the player-level
#: shapes below are what is left once it is applied.
SHOT_K = 19.4
BLOCK_K = 11.4
TEAM_SHOT_K = 70.0


# ---------------------------------------------------------------------------
# Market -> means
# ---------------------------------------------------------------------------
def devig(over: float, under: float) -> float:
    """P(over) from two decimal prices, multiplicative devig."""
    io, iu = 1.0 / over, 1.0 / under
    return io / (io + iu)


def _poisson_sf(k: int, lam: float) -> float:
    """P(N >= k) for Poisson(lam)."""
    if k <= 0:
        return 1.0
    term, cdf = math.exp(-lam), 0.0
    for i in range(k):
        cdf += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - cdf)


def poisson_mean(line: float, p_over: float) -> float:
    """The Poisson mean at which P(N > line) = p_over. Lines are half-integers
    (0.5, 1.5, 2.5, 3.5), so "over" is N >= floor(line)+1."""
    k = int(math.floor(line)) + 1
    lo, hi = 1e-4, 60.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _poisson_sf(k, mid) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def market_mean(market: dict | None) -> float | None:
    """{'Over', 'Under', 'point'} -> Poisson mean, or None without both sides."""
    if not market or not market.get("Over") or not market.get("Under") \
            or market.get("point") is None:
        return None
    return poisson_mean(float(market["point"]), devig(market["Over"], market["Under"]))


def implied_goals(total: float, p_over: float, p_home: float) -> tuple[float, float]:
    """(home, away) expected goals from the total and the moneyline.

    The combined mean is the Poisson rate at which P(goals > total) = p_over;
    the split is the one at which the home side wins p_home of games, a tie
    after regulation splitting in proportion to strength."""
    lam = poisson_mean(total, p_over)
    lo, hi = 0.05, 0.95
    for _ in range(50):
        share = (lo + hi) / 2
        if _win_prob(lam * share, lam * (1 - share)) < p_home:
            lo = share
        else:
            hi = share
    share = (lo + hi) / 2
    return lam * share, lam * (1 - share)


def _win_prob(lh: float, la: float) -> float:
    ph = [math.exp(-lh) * lh ** k / math.factorial(k) for k in range(20)]
    pa = [math.exp(-la) * la ** k / math.factorial(k) for k in range(20)]
    win = sum(ph[i] * sum(pa[:i]) for i in range(20))
    tie = sum(ph[i] * pa[i] for i in range(20))
    return win + tie * lh / (lh + la)


# ---------------------------------------------------------------------------
# The simulation
# ---------------------------------------------------------------------------
def _assist_matrix(team_players: list[dict], scorer_p: np.ndarray, targets: np.ndarray,
                   team_goals: float) -> np.ndarray:
    """Row i = P(teammate j is AN assister | i scored). Weights are solved so
    each skater's expected assists match his target given who scores."""
    n = len(team_players)
    aff = np.ones((n, n))
    for i, a in enumerate(team_players):
        for j, b in enumerate(team_players):
            if i == j:
                aff[i, j] = 0.0
                continue
            if a.get("line") and a.get("line") == b.get("line"):
                aff[i, j] *= LINE_AFFINITY
            if a.get("pp") and a.get("pp") == b.get("pp"):
                aff[i, j] *= PP_AFFINITY
    per_goal = ASSISTS_PER_GOAL
    # Targets must add up to what the team's goals can carry, or no weights fit.
    targets = targets * (team_goals * per_goal / max(targets.sum(), 1e-9))
    w = np.maximum(targets, 1e-4).copy()
    for _ in range(40):
        rows = aff * w[None, :]
        rows /= rows.sum(axis=1, keepdims=True).clip(1e-12)
        expected = team_goals * per_goal * (scorer_p @ rows)
        w *= np.where(expected > 1e-9, targets / expected.clip(1e-9), 1.0)
        w = np.maximum(w, 1e-6)
    rows = aff * w[None, :]
    return rows / rows.sum(axis=1, keepdims=True).clip(1e-12)


def _draw(rng, cum: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Categorical draws: row-wise cumulative probabilities, one uniform each."""
    return (u[:, None] > cum).sum(axis=1).clip(0, cum.shape[1] - 1)


def simulate(pool: list[dict], games: dict, n_sims: int = 2000, seed: int = 0) -> np.ndarray:
    """(n_sims x len(pool)) DK points.

    `pool` rows: {name, team, pos ('C'/'W'/'D'/'G'), line, pp, means: {goals,
    assists, shots, blocks, saves?}}; goalies are the projected starters only.
    `games`: {game_key: {home, away, lam_home, lam_away}}.
    """
    rng = np.random.default_rng(seed)
    out = np.zeros((n_sims, len(pool)))
    idx_by_team: dict = {}
    for i, p in enumerate(pool):
        idx_by_team.setdefault(p["team"], []).append(i)

    team_state: dict = {}
    for g in games.values():
        for side, other, lam in (("home", "away", g["lam_home"]), ("away", "home", g["lam_away"])):
            team_state[g[side]] = {"opp": g[other], "lam": lam}

    for g in games.values():
        h, a = g["home"], g["away"]
        lh, la = g["lam_home"], g["lam_away"]
        gh = rng.poisson(lh * REG_SCALE, n_sims)
        ga = rng.poisson(la * REG_SCALE, n_sims)
        m, u = gh - ga, rng.random(n_sims)
        ga = ga + ((m == 1) & (u < TIE_PULL))
        gh = gh + ((m == -1) & (u < TIE_PULL))
        tie = gh == ga
        ot_goal = tie & (rng.random(n_sims) < P_OT_GOAL)
        home_wins_tie = rng.random(n_sims) < lh / (lh + la)
        gh = gh + (ot_goal & home_wins_tie)
        ga = ga + (ot_goal & ~home_wins_tie)
        home_won = (gh > ga) | (tie & home_wins_tie)
        # Empty-netters: only in regulation, only for a team up by one or two.
        margin = gh - ga
        en_h = (~tie) & (margin >= 1) & (margin <= 2) & (rng.random(n_sims) < P_EMPTY_NET)
        en_a = (~tie) & (margin <= -1) & (margin >= -2) & (rng.random(n_sims) < P_EMPTY_NET)
        goals = {h: gh + en_h, a: ga + en_a}
        charged = {h: ga, a: gh}               # goals the team's goalie is charged with
        won = {h: home_won, a: ~home_won}
        ot_loss = {h: tie & ~home_won, a: tie & home_won}
        shot_factor = {t: rng.gamma(TEAM_SHOT_K, 1 / TEAM_SHOT_K, n_sims) for t in (h, a)}
        shots_for = {h: np.zeros(n_sims), a: np.zeros(n_sims)}

        for t in (h, a):
            members = [i for i in idx_by_team.get(t, []) if pool[i]["pos"] != "G"]
            if not members:
                continue
            m = [pool[i]["means"] for i in members]
            gm = np.array([max(x.get("goals", 0.0), 1e-4) for x in m])
            am = np.array([max(x.get("assists", 0.0), 1e-4) for x in m])
            scorer_p = gm / gm.sum()
            A = _assist_matrix([pool[i] for i in members], scorer_p, am, team_state[t]["lam"])
            cum_s = np.cumsum(scorer_p)[None, :]
            cum_a = np.cumsum(A, axis=1)
            n = len(members)
            G = np.zeros((n_sims, n))
            AS = np.zeros((n_sims, n))
            tg = goals[t]
            for j in range(int(tg.max()) if tg.size else 0):
                live = tg > j
                k = int(live.sum())
                if not k:
                    break
                rows = np.nonzero(live)[0]
                scorer = _draw(rng, np.repeat(cum_s, k, axis=0), rng.random(k))
                G[rows, scorer] += 1
                n_ast = np.where(rng.random(k) < P_UNASSISTED, 0,
                                 np.where(rng.random(k) < P_TWO_ASSISTS, 2, 1))
                first = _draw(rng, cum_a[scorer], rng.random(k))
                has1 = n_ast >= 1
                AS[rows[has1], first[has1]] += 1
                second = _draw(rng, cum_a[scorer], rng.random(k))
                clash = second == first
                second[clash] = _draw(rng, cum_a[scorer[clash]], rng.random(int(clash.sum())))
                ok2 = (n_ast == 2) & (second != first)
                AS[rows[ok2], second[ok2]] += 1
            sm = np.array([max(x.get("shots", 0.0) - x.get("goals", 0.0), 0.05) for x in m])
            mu = sm[None, :] * shot_factor[t][:, None]
            extra = rng.negative_binomial(SHOT_K, SHOT_K / (SHOT_K + mu))
            S = G + extra
            shots_for[t] = S.sum(axis=1)
            bm = np.array([max(x.get("blocks", 0.0), 0.02) for x in m])
            other = h if t == a else a
            bmu = bm[None, :] * shot_factor[other][:, None]
            B = rng.negative_binomial(BLOCK_K, BLOCK_K / (BLOCK_K + bmu))
            out[:, members] = nhl.skater_points(G, AS, S, B)

        for t in (h, a):
            opp = a if t == h else h
            for i in idx_by_team.get(t, []):
                if pool[i]["pos"] != "G":
                    continue
                ga_ = charged[t]
                sa = shots_for[opp].astype(float)
                target = pool[i]["means"].get("saves")
                if target:
                    expected_sa = sa.mean() or 1.0
                    sa = sa * (target + ga_.mean()) / expected_sa
                saves = np.maximum(np.rint(sa) - ga_, 0)
                out[:, i] = nhl.goalie_points(saves, ga_, won[t], ot_loss[t], ga_ == 0)
    return out


def summarise(sim: np.ndarray, pool: list[dict]) -> list[dict]:
    """Per-player simulated distribution onto the pool rows."""
    for i, p in enumerate(pool):
        col = sim[:, i]
        p.update({"proj": round(float(col.mean()), 2), "sd": round(float(col.std()), 2),
                  "floor": round(float(np.percentile(col, 25)), 2),
                  "ceil": round(float(np.percentile(col, 90)), 2),
                  "p99": round(float(np.percentile(col, 99)), 2)})
    return pool
