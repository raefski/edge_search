"""Correlated slate simulator + field model + contest equity (the "modern DFS"
layer: how does OUR lineup do against a simulated FIELD, not just in mean
projection?).

Design (see DFS_METHODOLOGY — simulator section):
- Every hitter's game is generated from per-PA EVENT rates (1B/2B/3B/HR/BB/SB),
  anchored so the sim's mean equals the shipped projection (the validated mean
  model stays the authority on means; the sim adds SHAPE and CORRELATION).
- Teammate correlation comes from two shared mechanisms, both real:
  (1) a per-team-game environment factor z (hot/cold offense night) scaling
      hit-event rates, and (2) R/RBI allocated from the TEAM's simulated run
      total (runs are literally shared events: one run = one R + usually one
      RBI on the same play, so teammates' R/RBI co-move mechanically).
- Pitchers are tied to the OPPOSING team's simulated runs (ER is drawn as the
  earned share of the runs that team actually scored in that world), which
  produces the pitcher-vs-opposing-stack anti-correlation stacking exploits.
- The field is sampled from the ownership model (or real ownership when
  replaying a historical contest) with the primary-stack-size distribution
  measured from real DK contest exports, then our lineup's finish percentile
  is computed per simulated world.

Calibration constants (PA_PMF, R/RBI slot shares, correlation targets) are
measured from the 25,086-row 2025 lab dataset — see scripts/dfs_sim_validate.py
for the calibration/validation run that froze them.
"""
from __future__ import annotations

import numpy as np

# DK hitter scoring weights over event vector [1B, 2B, 3B, HR, BB+HBP, SB]
EVT_PTS = np.array([3.0, 5.0, 8.0, 10.0, 2.0, 5.0])

# league per-PA event rates (2023+2024 full seasons, lab population)
LG_EVT = np.array([0.1384, 0.0434, 0.0034, 0.0339, 0.0872 + 0.0112, 0.0147])

# empirical starter-PA pmf by (home, slot), counts of 0..7 PA (2025 lab rows)
PA_PMF = {
    (False, 1): [0.0, 0.0007, 0.0072, 0.038, 0.3709, 0.5222, 0.0603, 0.0007],
    (False, 2): [0.0, 0.0036, 0.0036, 0.0359, 0.4476, 0.4634, 0.0445, 0.0014],
    (False, 3): [0.0, 0.0022, 0.0079, 0.038, 0.5165, 0.4046, 0.0308, 0.0],
    (False, 4): [0.0, 0.0, 0.0093, 0.0581, 0.561, 0.3451, 0.0258, 0.0007],
    (False, 5): [0.0, 0.0036, 0.0165, 0.0897, 0.6069, 0.2626, 0.0201, 0.0007],
    (False, 6): [0.0, 0.0029, 0.0373, 0.1212, 0.6205, 0.2088, 0.0086, 0.0007],
    (False, 7): [0.0, 0.0022, 0.0459, 0.1944, 0.5933, 0.1571, 0.0072, 0.0],
    (False, 8): [0.0, 0.0014, 0.0782, 0.2747, 0.5251, 0.1141, 0.0057, 0.0007],
    (False, 9): [0.0, 0.0079, 0.107, 0.3137, 0.486, 0.0804, 0.0043, 0.0007],
    (True, 1): [0.0, 0.0014, 0.0043, 0.0387, 0.5158, 0.4103, 0.0287, 0.0007],
    (True, 2): [0.0, 0.0029, 0.0072, 0.0589, 0.5915, 0.3195, 0.0201, 0.0],
    (True, 3): [0.0, 0.0022, 0.0086, 0.0581, 0.6485, 0.2661, 0.0165, 0.0],
    (True, 4): [0.0, 0.0, 0.0086, 0.0696, 0.6994, 0.2095, 0.0129, 0.0],
    (True, 5): [0.0, 0.0014, 0.0223, 0.1249, 0.6971, 0.1457, 0.0086, 0.0],
    (True, 6): [0.0, 0.0036, 0.0345, 0.181, 0.6638, 0.1092, 0.0079, 0.0],
    (True, 7): [0.0, 0.0022, 0.0639, 0.2642, 0.5808, 0.0847, 0.0043, 0.0],
    (True, 8): [0.0, 0.0007, 0.0653, 0.3522, 0.5187, 0.061, 0.0022, 0.0],
    (True, 9): [0.0, 0.0036, 0.1047, 0.4455, 0.4139, 0.0316, 0.0007, 0.0],
}
_PA_PMF_DEFAULT = [0.0, 0.002, 0.03, 0.15, 0.55, 0.24, 0.027, 0.001]

# linear-weights run value per event [1B,2B,3B,HR,BB,SB] -> team runs
RUN_W = np.array([0.46, 0.76, 1.04, 1.40, 0.30, 0.18])

# calibration constants -- frozen by scripts/dfs_sim_validate.py against
# (a) real 2025 team-run distribution, (b) real per-player score marginals,
# (c) measured teammate correlation by batting-order distance (§18)
CFG = {
    "env_shape": 4.2,       # gamma shape for the team hot/cold factor z (std ~0.49)
    "bb_env_pow": 0.3,      # walks respond to z much less than hits do
    "run_scale": 0.62,      # linear-weights -> real runs mean calibration
    "run_noise": 1.2,       # extra team-run noise (sequencing luck)
    "po_outs_sd": 4.0,      # pitcher outs sd around props mean
    "po_k_sd": 1.6,         # pitcher K sd around outs-scaled mean
    "er_earned": 0.92,      # share of runs that are earned
    "chain": (0.60, 0.28, 0.12),   # adjacency weights, 1/2/3 slots away
}


def rng_for(seed):
    return np.random.default_rng(seed)


# --------------------------------------------------------------------------
# per-player event rates
# --------------------------------------------------------------------------
def hitter_event_rates(proj, slot, home, sr=None):
    """Per-PA event-rate vector [1B,2B,3B,HR,BB+HBP,SB] for one hitter, shaped
    from their real season component rates where available (sr = season_hitting
    stat dict), league shape otherwise -- then SCALED so the sim's expected DK
    points equals `proj` (the validated mean model stays authoritative).
    """
    pmf = PA_PMF.get((home, slot), _PA_PMF_DEFAULT)
    exp_pa = float(np.dot(pmf, np.arange(8)))
    ev = LG_EVT.copy()
    if sr and (sr.get("plateAppearances") or 0) >= 30:
        pa = sr["plateAppearances"]
        g = lambda k: (sr.get(k, 0) or 0)
        if "doubles" in sr:
            dbl, tpl = g("doubles"), g("triples")
        else:
            # season_hitting cache carries totalBases but not 2B/3B -- recover
            # doubles the same way _hitter_points does (triples folded in)
            dbl = max(0, g("totalBases") - g("hits") - 3 * g("homeRuns"))
            tpl = 0
        s1 = max(0, g("hits") - dbl - tpl - g("homeRuns"))
        raw = np.array([s1 / pa, dbl / pa, tpl / pa,
                        g("homeRuns") / pa, (g("baseOnBalls") + g("hitByPitch")) / pa,
                        g("stolenBases") / pa])
        w = pa / (pa + 120.0)   # EB blend toward league shape
        ev = w * raw + (1 - w) * LG_EVT
    # anchor mean: event pts + R/RBI expectation ~= proj
    # R/RBI expectation per PA is roughly 0.105 R + 0.11 RBI = ~0.43 pts/PA at
    # league; it scales with the same offense level as the events do, so fold
    # it into one multiplier on the whole vector.
    evt_pts_pa = float(EVT_PTS @ ev)
    rr_pts_pa = 2.0 * (0.115 + 0.115) * (evt_pts_pa / (EVT_PTS @ LG_EVT))
    target = proj / exp_pa if exp_pa > 0 else evt_pts_pa + rr_pts_pa
    kappa = target / (evt_pts_pa + rr_pts_pa)
    ev = ev * float(np.clip(kappa, 0.5, 1.8))
    return np.clip(ev, 1e-5, 0.45), pmf


# --------------------------------------------------------------------------
# slate world simulation
# --------------------------------------------------------------------------
def simulate_slate(pool, n_sims=4000, seed=1, season_rates=None):
    """Joint score simulation for every player in `pool`.

    pool: build_slate()-shaped dicts: hitters need team/slot/proj (+optionally
      a season stat dict via season_rates[name]), pitchers need team/opp_team/
      proj (+optional prop-implied 'outs_mean'/'k_mean').
    Returns (scores ndarray [n_sims x n_players], meta dict).
    """
    rng = rng_for(seed)
    n = len(pool)
    scores = np.zeros((n_sims, n))
    hitters_by_team = {}
    for i, p in enumerate(pool):
        if "P" not in p["pos"]:
            hitters_by_team.setdefault(p["team"], []).append(i)
    # one shared environment draw per team per world
    teams = sorted(set(list(hitters_by_team.keys())
                       + [p.get("opp_team") for p in pool if "P" in p["pos"] and p.get("opp_team")]))
    z = {t: rng.gamma(CFG["env_shape"], 1.0 / CFG["env_shape"], size=n_sims) for t in teams}
    team_runs = {t: np.zeros(n_sims) for t in teams}

    # ---- hitters: events, then team runs, then R/RBI allocation
    team_events = {}   # team -> per-hitter event count arrays
    for team, idxs in hitters_by_team.items():
        idxs = sorted(idxs, key=lambda i: pool[i].get("slot") or 9)
        ev_counts = []
        zt = z[team]
        for i in idxs:
            p = pool[i]
            sr = (season_rates or {}).get(p["name"])
            ev, pmf = hitter_event_rates(p["proj"], p.get("slot") or 7,
                                         bool(p.get("home", False)), sr)
            pa = rng.choice(8, size=n_sims, p=np.array(pmf) / sum(pmf))
            # env-scaled per-PA probabilities (hits scale with z, walks less)
            probs = np.empty((n_sims, 6))
            probs[:, :4] = ev[:4] * zt[:, None]
            probs[:, 4] = ev[4] * zt ** CFG["bb_env_pow"]
            probs[:, 5] = ev[5]
            probs = np.clip(probs, 0, 0.5)
            cnt = rng.binomial(pa[:, None], probs)
            # can't have more on-base events than PA: cap by scaling down rare overflows
            tot = cnt[:, :5].sum(1)
            over = tot > pa
            if over.any():
                keep = np.floor(cnt[over, :5] * (pa[over] / tot[over])[:, None]).astype(int)
                cnt[over, :5] = keep
            ev_counts.append(cnt)
        team_events[team] = (idxs, ev_counts)
        # team runs from linear weights + sequencing noise
        rc = sum((c * RUN_W).sum(1) for c in ev_counts)
        runs = rc * CFG["run_scale"] * (0.9 + 0.2 * zt) + rng.normal(0, CFG["run_noise"], n_sims)
        team_runs[team] = np.maximum(0, np.round(runs))

    # ---- R/RBI allocation per team
    # Weights use each world's REALIZED neighbor events (not static means):
    # your R chance rises when the batters BEHIND you actually hit in this
    # world; your RBI chance rises when the batters AHEAD of you actually got
    # on. That is the mechanism that makes adjacent teammates' scores co-move
    # more than distant ones (the §18-measured decay this sim must reproduce).
    w1, w2, w3 = CFG["chain"]
    for team, (idxs, ev_counts) in team_events.items():
        k = len(idxs)
        if k == 0:
            continue
        hrs = np.stack([c[:, 3] for c in ev_counts], 1).astype(float)       # (S,k)
        onbase = np.stack([c[:, :5].sum(1) - c[:, 3] for c in ev_counts], 1).astype(float)
        power = np.stack([(1.4 * c[:, 3] + 0.8 * c[:, 2] + 0.6 * c[:, 1]
                           + 0.35 * c[:, 0] + 0.05 * c[:, 4]) for c in ev_counts], 1)

        def chain(mat, direction):
            # direction -1: slots after you (wrap); +1: slots before you
            return (w1 * np.roll(mat, -1 * direction, axis=1)
                    + w2 * np.roll(mat, -2 * direction, axis=1)
                    + w3 * np.roll(mat, -3 * direction, axis=1))

        runs = team_runs[team].copy()
        hr_total = hrs.sum(1)
        # every HR scores its own run (self R + self RBI)
        self_r = np.minimum(hrs, runs[:, None])  # degenerate guard: runs >= hr normally
        runs_left = np.maximum(0, runs - hr_total)
        r_w = (onbase + 0.05) * (chain(power, -1) + 0.15)
        rbi_w = (power + 0.05) * (chain(onbase, +1) + 0.15)
        r_alloc = _alloc(rng, runs_left, r_w)
        rbi_alloc = _alloc(rng, runs_left, rbi_w)
        for j, i in enumerate(idxs):
            c = ev_counts[j]
            r_i = self_r[:, j] + r_alloc[:, j]
            rbi_i = self_r[:, j] + rbi_alloc[:, j]
            scores[:, i] = (c @ EVT_PTS) + 2.0 * r_i + 2.0 * rbi_i

    # ---- pitchers: tied to opposing team's simulated runs
    for i, p in enumerate(pool):
        if "P" not in p["pos"]:
            continue
        opp = p.get("opp_team")
        opp_runs = team_runs.get(opp)
        if opp_runs is None:
            # opponent not simulated (not in pool): draw a league run line
            opp_runs = np.maximum(0, np.round(rng.gamma(2.1, 2.2, n_sims)))
        mean_outs = p.get("outs_mean") or 17.0
        mean_k = p.get("k_mean")
        # opposing team's hot/cold factor drives the pitcher's whole line, not
        # just ER: cold opponents mean deeper outings, more Ks, fewer baserunners.
        # Coupling all three is what reproduces the measured real correlation of
        # -0.672 between an SP's DK score and the opposing lineup's total
        # (n=2,736 real 2025 team-games; an independent-K sim only got to -0.52).
        z_opp = z.get(opp)
        if z_opp is None:
            z_opp = np.ones(n_sims)
        outs = np.clip(np.round(rng.normal(mean_outs * z_opp ** -0.15,
                                           CFG["po_outs_sd"], n_sims)), 3, 27)
        if mean_k is None:
            mean_k = mean_outs * 0.32
        ks = np.clip(np.round(rng.normal(mean_k * (outs / mean_outs) * z_opp ** -0.35,
                                         CFG["po_k_sd"])), 0, outs)
        share = np.clip(outs / 27.0, 0.1, 1.0)
        er = rng.binomial(opp_runs.astype(int), share * CFG["er_earned"])
        hits_bb = np.maximum(0, np.round(rng.normal(outs * 0.42 * z_opp ** 0.5, 1.6)))
        own = p.get("team")
        own_runs = team_runs.get(own)
        if own_runs is None:
            own_runs = np.maximum(0, np.round(rng.gamma(2.1, 2.2, n_sims)))
        win = (own_runs > opp_runs) & (outs >= 15) & (rng.random(n_sims) < 0.85)
        base = 0.75 * outs + 2.0 * ks + 4.0 * win - 2.0 * er - 0.6 * hits_bb
        cg = (outs >= 27).astype(float)
        scores[:, i] = base + 2.5 * cg + 2.5 * cg * (er == 0)
        # anchor pitcher mean to the props-based projection (shape from sim,
        # level from the validated mean model)
        scores[:, i] += p["proj"] - scores[:, i].mean()

    return scores, {"team_runs": team_runs, "z": z}


def _alloc(rng, counts, weights):
    """Distribute integer `counts[s]` items across k bins with per-sim weights
    (S,k) -> (S,k) allocation. Vectorized categorical draws."""
    S, k = weights.shape
    out = np.zeros((S, k))
    cmax = int(counts.max()) if len(counts) else 0
    if cmax == 0:
        return out
    cw = np.cumsum(weights, 1)
    tot = cw[:, -1:]
    u = rng.random((S, cmax)) * tot
    # bin index for each draw: count of cum-weights below u
    idx = (u[:, :, None] > cw[:, None, :]).sum(2)          # (S, cmax)
    mask = np.arange(cmax)[None, :] < counts[:, None]
    for j in range(k):
        out[:, j] = ((idx == j) & mask).sum(1)
    return out


# --------------------------------------------------------------------------
# field generation
# --------------------------------------------------------------------------
ROSTER = {"P": 2, "C": 1, "1B": 1, "2B": 1, "3B": 1, "SS": 1, "OF": 3}
# primary-stack-size distribution of real DK MLB fields -- measured across
# 7,215 real entries in the repo's 15 contest exports (max hitters from one
# team per entry, scripts/dfs_sim_validate.py): 5-stacks are the biggest
# single bucket (0.29) but nearly half the field runs 3-or-fewer.
STACK_DIST = {1: 0.083, 2: 0.246, 3: 0.204, 4: 0.178, 5: 0.289}


def generate_field(pool, n_lineups, rng=None, own_key="own", cap=50000, max_team=5):
    """Sample plausible field lineups: ownership-driven, position-legal,
    salary-capped, with primary-stack sizes matching real fields. Returns a
    list of index-lists into pool. Imperfect entries (rare fill failures) are
    dropped, so the result can be slightly shorter than n_lineups."""
    rng = rng or np.random.default_rng(7)
    pitchers = [i for i, p in enumerate(pool) if "P" in p["pos"]]
    hitters = [i for i, p in enumerate(pool) if "P" not in p["pos"]]
    if not pitchers or len(hitters) < 8:
        return []
    pw = np.array([max(0.1, pool[i].get(own_key, 1.0)) for i in pitchers], float)
    hw = np.array([max(0.05, pool[i].get(own_key, 1.0)) for i in hitters], float)
    team_w = {}
    for i in hitters:
        team_w[pool[i]["team"]] = team_w.get(pool[i]["team"], 0.0) + pool[i].get(own_key, 1.0)
    teams = sorted(team_w)
    # floor: a pool logged with no ownership at all (own=0 everywhere, e.g. a
    # partial pre-lock build) must still sample teams rather than divide by 0
    tw = np.array([max(0.1, team_w[t]) for t in teams])
    stack_sizes = np.array(sorted(STACK_DIST))
    stack_p = np.array([STACK_DIST[s] for s in stack_sizes], float)
    stack_p /= stack_p.sum()

    out = []
    for _ in range(int(n_lineups * 1.25)):
        if len(out) >= n_lineups:
            break
        lu = _one_field_lineup(pool, rng, pitchers, pw, hitters, hw, teams, tw,
                               stack_sizes, stack_p, cap, max_team)
        if lu:
            out.append(lu)
    return out


def _one_field_lineup(pool, rng, pitchers, pw, hitters, hw, teams, tw,
                      stack_sizes, stack_p, cap, max_team):
    need = dict(ROSTER)
    picked, salary, team_ct = [], 0, {}

    def can(i):
        p = pool[i]
        if i in picked or salary + p["salary"] > cap:
            return None
        if "P" not in p["pos"] and team_ct.get(p["team"], 0) >= max_team:
            return None
        for s in p["pos"]:
            if need.get(s, 0) > 0:
                return s
        return None

    def take(i, slot):
        p = pool[i]
        picked.append(i)
        need[slot] -= 1
        nonlocal salary
        salary += p["salary"]
        if "P" not in p["pos"]:
            team_ct[p["team"]] = team_ct.get(p["team"], 0) + 1

    # pitchers
    p_idx = list(rng.choice(len(pitchers), size=min(2, len(pitchers)), replace=False,
                            p=pw / pw.sum()))
    for j in p_idx:
        i = pitchers[j]
        s = can(i)
        if s:
            take(i, s)
    if need["P"] > 0 and len(pitchers) > 2:
        for j in np.argsort(-pw):
            if need["P"] == 0:
                break
            s = can(pitchers[j])
            if s:
                take(pitchers[j], s)
    # primary stack
    st_team = teams[int(rng.choice(len(teams), p=tw / tw.sum()))]
    st_size = int(rng.choice(stack_sizes, p=stack_p))
    st_cands = [i for i in hitters if pool[i]["team"] == st_team]
    st_w = np.array([max(0.05, pool[i].get("own", 1.0)) for i in st_cands])
    order = list(np.argsort(-(st_w * rng.random(len(st_cands)))))
    got = 0
    for j in order:
        if got >= st_size:
            break
        s = can(st_cands[j])
        if s:
            take(st_cands[j], s)
            got += 1
    # fill the rest by ownership
    order = list(np.argsort(-(hw * rng.random(len(hitters)))))
    for j in order:
        if sum(need.values()) == 0:
            break
        i = hitters[j]
        s = can(i)
        if s:
            take(i, s)
    if sum(need.values()) > 0:
        return None
    return picked


# --------------------------------------------------------------------------
# contest equity
# --------------------------------------------------------------------------
def synthetic_gpp_payouts(field_size, entry_fee=5.0, rake=0.15, paid_frac=0.22):
    """A representative top-heavy DK GPP payout curve when the real one isn't
    in contest_meta.json: ~22% of the field paid, min-cash ~= 1.8x entry,
    winner ~12-15% of the prize pool, geometric decay between. Used to RANK
    lineups by EV (the top-heavy shape is what matters for ranking); real
    curves from contest metadata should override it whenever available.
    Returns [(rank_from, rank_to, payout)], total == field_size*fee*(1-rake)."""
    pool = field_size * entry_fee * (1 - rake)
    n_paid = max(1, int(round(field_size * paid_frac)))
    min_cash = 1.8 * entry_fee
    # geometric decay from the winner down to min-cash across paid ranks
    ranks = np.arange(1, n_paid + 1)
    decay = ranks ** -1.35
    raw = decay / decay.sum() * pool
    raw = np.maximum(raw, min_cash)
    raw *= pool / raw.sum()                     # renormalize to the pool
    out, i = [], 0
    while i < n_paid:                            # merge equal-payout runs
        j = i
        while j + 1 < n_paid and abs(raw[j + 1] - raw[i]) < 0.01:
            j += 1
        out.append((int(ranks[i]), int(ranks[j]), round(float(raw[i]), 2)))
        i = j + 1
    return out


def payout_for_rank(rank, payouts):
    for lo, hi, amt in payouts:
        if lo <= rank <= hi:
            return amt
    return 0.0


def contest_equity(scores, our_lineup, field_lineups, cash_line_pct=0.44, top_pct=0.01,
                   payouts=None, field_size=None, entry_fee=None):
    """Per-world percentile of OUR lineup vs the simulated field.

    Returns mean/median percentile, P(beat cash_line), P(top_pct), and the
    field score quantiles (for validation against real standings). When
    `payouts` ([(rank_from, rank_to, amt)]) and `field_size` (the REAL
    contest's entry count) are given, also returns expected dollar payout
    per entry (`ev_dollars`) by mapping each world's percentile to a rank in
    the real field -- and `roi` when entry_fee is given too."""
    ours = scores[:, our_lineup].sum(1)
    field = np.stack([scores[:, lu].sum(1) for lu in field_lineups], 1)  # (S, M)
    pct = (ours[:, None] > field).mean(1) + 0.5 * (ours[:, None] == field).mean(1)
    out = {
        "mean_pct": round(float(pct.mean()) * 100, 1),
        "median_pct": round(float(np.median(pct)) * 100, 1),
        "p_cash": round(float((pct >= 1 - cash_line_pct).mean()), 3),
        "p_top": round(float((pct >= 1 - top_pct).mean()), 3),
        "our_mean": round(float(ours.mean()), 1),
        "our_p95": round(float(np.percentile(ours, 95)), 1),
        "field_q": {q: round(float(np.percentile(field, q)), 1)
                    for q in (50, 75, 90, 95, 99)},
    }
    if payouts and field_size:
        ranks = np.round((1 - pct) * (field_size - 1)).astype(int) + 1
        pays = np.array([payout_for_rank(r, payouts) for r in ranks])
        out["ev_dollars"] = round(float(pays.mean()), 2)
        if entry_fee:
            out["roi"] = round(float(pays.mean() / entry_fee - 1), 3)
    return out


def pick_by_sim_ev(scores, candidates, field_lineups, payouts, field_size):
    """Rank candidate lineups (lists of pool indices) by expected payout under
    ONE shared simulated world-set + field sample -- the sim-EV construction
    selector. Sharing worlds across candidates removes sim noise from the
    COMPARISON (each candidate faces the exact same baseball).

    Returns (best_index, [per-candidate dicts sorted by candidate order])."""
    field = np.stack([scores[:, lu].sum(1) for lu in field_lineups], 1)   # (S, M)
    M = field.shape[1]
    results = []
    for lu in candidates:
        ours = scores[:, lu].sum(1)
        gt = (ours[:, None] > field).sum(1)
        eq = (ours[:, None] == field).sum(1)
        pct = (gt + 0.5 * eq) / M
        ranks = np.round((1 - pct) * (field_size - 1)).astype(int) + 1
        pays = np.array([payout_for_rank(r, payouts) for r in ranks])
        results.append({
            "ev": round(float(pays.mean()), 3),
            "mean_pct": round(float(pct.mean()) * 100, 1),
            "p_top1": round(float((pct >= 0.99).mean()), 4),
        })
    best = max(range(len(candidates)),
               key=lambda i: (results[i]["ev"], results[i]["p_top1"]))
    return best, results
