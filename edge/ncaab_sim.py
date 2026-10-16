"""College basketball game simulator for DK CBB Classic (edge/dfs_run_ncaab.py).

Written 2026-10-13 for both leagues; on 2026-10-16 the NBA kept its own fitted
model instead (edge/dfs_nba_theory.py: independent gammas, because NBA
residuals measured on 29,311 player-games barely co-move once minutes are
known), so this simulator is college's. The NBA's market-residual priors
(margin sd ~13.5, total sd ~18.6) were dropped with it.

WHY SIMULATE GAMES RATHER THAN ADD UP PLAYER MEANS
A basketball lineup's total is not a sum of independent players, and the
three ways it is not are exactly what separates a cash lineup from a GPP one:

  1. PACE IS SHARED. A fast game is more possessions for everyone on the
     floor, both teams -- the reason a GPP game-stacks the highest total.
  2. USAGE IS ZERO-SUM-ISH. A team scores about what the market says; when one
     player takes 30 shots, his teammates took fewer. Teammates' points are
     NEGATIVELY correlated given the team's total, which is why cash does not
     stack three players from one team.
  3. BLOWOUTS BENCH STARTERS. A 25-point game costs the starters on BOTH sides
     their fourth quarter. A close game is upside for every star in it; a
     lopsided spread is a minutes risk the props price only on average.

So whole games are sampled: a pace factor per game, an efficiency factor per
team (their product moves the team's points around the market's implied
total), the realised margin, minutes per player (with the blowout cut), a
usage share renormalised inside each team, and then the counting stats:

    points   = 2 x twos + 3 x threes + free throws   (each Poisson)
    rebounds   pace x minutes x the OPPONENT's misses (low opp. efficiency = more)
    assists    pace x efficiency x minutes x usage   (assists are made baskets)
    stl / blk / tov   pace x minutes

DK points come from the simulated box score, so the double-double and
triple-double bonuses are priced from the joint distribution -- a centre who
averages 11 and 9 gets his +1.5 in the share of games where both reach ten,
not in "neither mean is ten so never".

EVERY PLAYER'S SIMULATED MEAN EQUALS HIS MARKET MEAN. Each multiplicative
factor is renormalised to mean 1 per player across simulations, so the factors
shape CORRELATION and spread and never move a projection. The props already
price blowout risk on average; without this the sim would price it twice.

THE CONSTANTS ARE PRIORS, NOT FITS. No NBA or college game has been simulated
against outcomes yet (2026-10-13, preseason). They come from the
betting-market residuals every basketball handicapper quotes -- NBA margin vs
the closing spread sd ~13-14, game total vs the closing total sd ~18-19;
college ~11 and ~16 -- and league box-score shares (free-throw points ~15% of
NBA scoring, ~18% of college). Fit them on the first college slates' actuals;
NBA_STATUS.md (college section) tracks which have been checked.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

STATS = ("pts", "reb", "ast", "fg3", "stl", "blk", "tov")
_N = NormalDist()


@dataclass(frozen=True)
class League:
    name: str
    #: Average team points per game, for scaling no-prop fallbacks.
    team_points: float
    #: Game-level pace factor sd (shared by both teams), log scale.
    pace_sd: float
    #: Team efficiency factor sd (independent per team), log scale.
    eff_sd: float
    #: Per-player minutes noise sd, log scale.
    min_sd: float
    #: Per-player usage-share noise sd, log scale.
    usage_sd: float
    #: How much of a usage spike is taken from teammates (0 none, 1 all).
    renorm: float
    #: Free-throw share of a player's points when no FT market says otherwise.
    ft_share: float
    #: Rebounds scale with the opponent's efficiency to this power (negative:
    #: an opponent that misses more leaves more rebounds).
    reb_opp: float
    #: Blowout: margin beyond which the top-5 of a team's rotation lose minutes,
    #: the fraction lost per point beyond it, and the floor.
    blowout_start: float
    blowout_slope: float
    blowout_floor: float
    #: How many players per team count as "starters" for the blowout cut.
    core: int


#: College: shorter game (40 min), fewer possessions, wider talent gaps. Total
#: 145 -> sd ~16; margin sd 72 x 0.105 x sqrt(2) ~= 10.7. PRIORS, as above.
NCAAB = League("NCAAB", team_points=72.5, pace_sd=0.075, eff_sd=0.105, min_sd=0.11,
               usage_sd=0.09, renorm=0.6, ft_share=0.18, reb_opp=-0.5,
               blowout_start=15.0, blowout_slope=0.012, blowout_floor=0.72, core=5)

#: Count noise beyond Poisson for the market-inversion variance model (the
#: simulator's own spread is what the minutes/usage/team factors add on top).
_VAR_MULT = {"pts": 2.3, "reb": 1.0, "ast": 1.0, "fg3": 1.0, "stl": 1.0, "blk": 1.0,
             "tov": 1.0}


# ---------------------------------------------------------------------------
# Market -> means
# ---------------------------------------------------------------------------
def devig(over: float, under: float) -> float:
    io, iu = 1.0 / over, 1.0 / under
    return io / (io + iu)


def _poisson_sf(k: int, lam: float) -> float:
    if k <= 0:
        return 1.0
    term, cdf = math.exp(-lam), 0.0
    for i in range(k):
        cdf += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - cdf)


def _p_over(stat: str, line: float, mu: float, league: League) -> float:
    """P(X > line) for a stat with mean mu, under the variance the simulator
    implies for it (count noise x _VAR_MULT plus minutes/usage spread)."""
    if mu <= 0:
        return 0.0
    if line < 4.0 and stat != "pts":
        return _poisson_sf(int(math.floor(line)) + 1, mu)
    spread = league.min_sd ** 2 + league.usage_sd ** 2 + league.eff_sd ** 2
    var = _VAR_MULT.get(stat, 1.0) * mu + spread * mu * mu
    # continuity: a half-point line between integers
    return 1.0 - _N.cdf((math.floor(line) + 0.5 - mu) / math.sqrt(var))


def mean_from_market(stat: str, market: dict | None, league: League = NCAAB) -> float | None:
    """{'Over', 'Under', 'point'} (decimal prices) -> the stat's mean, or None
    without both sides. Inverted under the same variance model the simulator
    produces, so a 24.5 points line at even money comes back ~24.5 and a heavy
    over at 24.5 comes back above it by the right amount for points' spread."""
    if not market or not market.get("Over") or not market.get("Under") \
            or market.get("point") is None:
        return None
    line = float(market["point"])
    p = devig(float(market["Over"]), float(market["Under"]))
    lo, hi = 1e-3, max(4.0 * line + 10.0, 10.0)
    for _ in range(60):
        mid = (lo + hi) / 2
        if _p_over(stat, line, mid, league) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def implied_points(total: float, spread_home: float) -> tuple[float, float]:
    """(home, away) implied team points from a game total and the home spread
    (negative = home favoured)."""
    return total / 2.0 - spread_home / 2.0, total / 2.0 + spread_home / 2.0


# ---------------------------------------------------------------------------
# DK basketball scoring, vectorised
# ---------------------------------------------------------------------------
def dk_points(pts, reb, ast, fg3, stl, blk, tov) -> np.ndarray:
    """DK Classic points for arrays of box scores -- the same table as
    edge/nba.actual_points, which tests/test_dfs_nba.py holds this to."""
    base = pts + 0.5 * fg3 + 1.25 * reb + 1.5 * ast + 2.0 * stl + 2.0 * blk - 0.5 * tov
    tens = ((pts >= 10).astype(np.int8) + (reb >= 10) + (ast >= 10) + (blk >= 10)
            + (stl >= 10))
    return base + np.where(tens >= 3, 3.0, np.where(tens >= 2, 1.5, 0.0))


def fantasy_mean(means: dict) -> float:
    """Expected DK points ignoring bonuses -- for ranking before a sim exists."""
    g = lambda k: float(means.get(k) or 0.0)                 # noqa: E731
    return (g("pts") + 0.5 * g("fg3") + 1.25 * g("reb") + 1.5 * g("ast")
            + 2 * g("stl") + 2 * g("blk") - 0.5 * g("tov"))


# ---------------------------------------------------------------------------
# The simulation
# ---------------------------------------------------------------------------
def _lognormal(rng, sd: float, shape) -> np.ndarray:
    return np.exp(rng.normal(-0.5 * sd * sd, sd, shape))


def _unit_mean(x: np.ndarray) -> np.ndarray:
    """Rescale each column to mean 1 (see the module docstring)."""
    m = x.mean(axis=0, keepdims=True)
    return x / np.where(m > 0, m, 1.0)


def simulate(pool: list[dict], games: dict, n_sims: int = 2000, seed: int = 0,
             league: League = NCAAB, return_box: bool = False):
    """DK points, shape (n_sims, len(pool)), for every player in `pool`.

    pool rows need: team, means {pts, reb, ast, fg3, stl, blk, tov}.
    games: {key: {home, away, pts_home, pts_away}} -- implied team points.
    A player whose team is in no game is simulated off his means alone
    (league-average game), so a missing line costs correlation, not a player.
    """
    rng = np.random.default_rng(seed)
    n = len(pool)
    out = np.zeros((n_sims, n))
    box = {s: np.zeros((n_sims, n)) for s in STATS} if return_box else None
    team_game: dict = {}
    for key, g in games.items():
        team_game[g["home"]] = (key, "home")
        team_game[g["away"]] = (key, "away")

    pace = {k: _lognormal(rng, league.pace_sd, n_sims) for k in games}
    eff = {t: _lognormal(rng, league.eff_sd, n_sims) for t in team_game}
    margin = {}
    for k, g in games.items():
        ph = g["pts_home"] * pace[k] * eff[g["home"]]
        pa = g["pts_away"] * pace[k] * eff[g["away"]]
        margin[k] = ph - pa

    by_team: dict = {}
    for i, p in enumerate(pool):
        by_team.setdefault(p.get("team"), []).append(i)

    for t, idx in by_team.items():
        idx = np.array(idx)
        k, side = team_game.get(t, (None, None))
        if k is None:
            pc = np.ones(n_sims)
            e_own = np.ones(n_sims)
            e_opp = np.ones(n_sims)
            blow = np.zeros(n_sims)
        else:
            g = games[k]
            opp = g["away"] if side == "home" else g["home"]
            pc, e_own, e_opp = pace[k], eff[t], eff[opp]
            blow = np.maximum(np.abs(margin[k]) - league.blowout_start, 0.0)

        means = {s: np.array([float((pool[i].get("means") or {}).get(s) or 0.0) for i in idx])
                 for s in STATS}
        fpts = np.array([fantasy_mean(pool[i].get("means") or {}) for i in idx])
        core = np.zeros(len(idx), dtype=bool)
        core[np.argsort(-fpts)[:league.core]] = True

        m = len(idx)
        minutes = _lognormal(rng, league.min_sd, (n_sims, m))
        cut = np.clip(1.0 - league.blowout_slope * blow, league.blowout_floor, 1.0)
        # Starters lose the minutes, the bench gets them (half again, since
        # a bench player's base is smaller -- a PRIOR, like the slope).
        gain = 1.0 + 1.5 * (1.0 - cut)
        bf = np.where(core[None, :], cut[:, None], gain[:, None])
        minutes = minutes * bf
        usage = _lognormal(rng, league.usage_sd, (n_sims, m))
        w = means["pts"] + 1e-6
        share = minutes * usage
        team_share = (share * w).sum(axis=1, keepdims=True) / w.sum()
        renorm = team_share ** (-league.renorm)
        offense = _unit_mean(share * renorm * (pc * e_own)[:, None])
        play = _unit_mean(minutes * pc[:, None])
        boards = _unit_mean(minutes * (pc * e_opp ** league.reb_opp)[:, None])

        fg3 = rng.poisson(means["fg3"][None, :] * offense)
        ft_mu = league.ft_share * means["pts"]
        two_mu = np.maximum(means["pts"] - 3.0 * means["fg3"] - ft_mu, 0.0) / 2.0
        ft = rng.poisson(ft_mu[None, :] * offense)
        twos = rng.poisson(two_mu[None, :] * offense)
        pts = 2.0 * twos + 3.0 * fg3 + ft
        reb = rng.poisson(means["reb"][None, :] * boards)
        ast = rng.poisson(means["ast"][None, :] * offense)
        stl = rng.poisson(means["stl"][None, :] * play)
        blk = rng.poisson(means["blk"][None, :] * play)
        tov = rng.poisson(means["tov"][None, :] * offense)
        out[:, idx] = dk_points(pts, reb, ast, fg3, stl, blk, tov)
        if box is not None:
            for s, v in zip(STATS, (pts, reb, ast, fg3, stl, blk, tov)):
                box[s][:, idx] = v
    return (out, box) if return_box else out


def summarise(sim: np.ndarray, pool: list[dict], floor_pct: float = 25.0,
              ceil_pct: float = 90.0) -> list[dict]:
    for i, p in enumerate(pool):
        col = sim[:, i]
        p.update({"proj": round(float(col.mean()), 2), "sd": round(float(col.std()), 2),
                  "floor": round(float(np.percentile(col, floor_pct)), 2),
                  "ceil": round(float(np.percentile(col, ceil_pct)), 2),
                  "p99": round(float(np.percentile(col, 99)), 2)})
    return pool
