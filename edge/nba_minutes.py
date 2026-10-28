"""NBA minutes when the rotation changes: who absorbs a scratched player's minutes.

Adam, 2026-10-15: the NBA DFS edge is a late scratch -- a cheap bench player
promoted to starter. DK's salary is fixed the night before, so his minutes (and
points) jump while his price does not. This module turns tonight's lineup news
(edge/nba_lineups.py: starters, inactives) into a minutes projection for every
player on the team. `project` is the model rebuilt 2026-10-21 (NBA_MINUTES_METHOD.md,
scripts/nba_minutes_lab.py):

  1. role       an EXPONENTIALLY WEIGHTED mean of his active games this season
                (DECAY per game), against a PRIOR worth PRIOR_KAPPA games: last
                season's role as a whole. Split into a starting role and a bench
                role (SPLIT_K, SPLIT_GAP); once the five is announced he gets the
                one that matches tonight. A rookie (< ROOKIE_GAMES games) starts
                from his draft pick (ROOKIE_PRIOR); a player back from a 5+ game
                absence is ramped in (RAMP).
  2. 240        G = 240 - the sum of the active players' roles; the player at role
                rank k moves a(k) + b(k) x G (rank_gap, coefficients fitted per rank
                and sign of G in edge/nba_minutes_gap.py): stars barely move, the
                8th-12th men absorb the gap (critique cycle 1). Fewer than 8 active:
                the missing minutes are shared by role^ROLE_POW x position, no 240.

`project_v1` is the 2026-10-15 model (fixed windows, promotion lift, EXCESS_POW),
kept for the record. Minutes only; points come from DK points per minute.
"""
from __future__ import annotations

#: Recent games that define a player's role.
WINDOW = 10
#: A player must average this many minutes to count as a scratched regular.
REGULAR_MIN = 10.0
#: Minutes a team plays in regulation.
TEAM_MIN = 240.0
#: Active players needed before the team is rescaled to TEAM_MIN.
FULL_ROSTER = 8
#: A player with fewer than this many games this season gets a longer role window
#: (SEASON_START_WINDOW): last season's final 10 games are rest days and tanking.
#: 82 (a full season) replaced 25 on 2026-10-20: replaying 2025-26's opening week
#: (150 team-games, each team's first 5, roles from 2024-25 only, NBA.com actives
#: and starters as known at the time) minutes mean |error| by window --
#:   window              10     25     40     60     82
#:   before lineups     7.03   6.76   6.55   6.49   6.42
#:   starters confirmed 6.45   6.31   6.21   6.14   6.10
#: Monotone, one knob, only for players with < 5 games this season. One opening
#: week is all the cached history allows (no 2023-24 logs), so it is not held out.
#: Starters still run ~3 min low before lineups post (-3.5 at 82): the roles of 13
#: players from last season's teams sum to 267-321 on 2026-10-20, over 240.
SEASON_START_WINDOW = 82
#: Fitted by scripts/nba_minutes_backtest.py on 2024-25 (2,470 team-games, grid over
#: role power 0-2, position weight 0-2, promotion weight 0-3, lift 0-1.25), scored on
#: 2025-26 (2,466 team-games), 2026-10-15. Mean |error| in minutes, held out:
#:                                   all (29,311)  teammates of a scratch  promoted starters (1,816)
#:   recent minutes, no change          5.89            5.99                 9.58 (bias -8.6)
#:   proportional share                 5.89            5.99                 8.63 (bias -6.8)
#:   these weights, starters confirmed  5.66            5.72                 5.48 (bias +0.1)
#:   these weights, inactives only      5.85            5.93                 8.37 (bias -6.8)
#: A bench player who starts plays ~8.6 more minutes than his recent average; the
#: confirmed starting five (30 min before tip) is what reveals it.
ROLE_POW = 0.5
POS_W = 0.5
PROMO_W = 1.0
PROMO_LIFT = 1.0
#: When the roles add to more than 240, the 240 is shared by role^EXCESS_POW.
#: All player-games, mean |error| (2024-25 fit / 2025-26 held out): plain
#: proportional 5.703 / 5.659, 1.15 -> 5.621 / 5.601, 1.3 -> 5.669 / 5.662. Teams over
#: 260 alone wanted ~1.5 (5.70 -> 5.20) but a power that grew with the overflow
#: hurt overall (5.79 at its best), so it is one constant.
EXCESS_POW = 1.15
#: Typical minutes of a starter (refined per team from history when available).
STARTER_MIN = 31.0

# ── the 2026-10-21 model (project) ────────────────────────────────────────────
# Fitted by scripts/nba_minutes_lab.py on 2024-25 (2,470 team-games; squared error,
# because DFS needs MEAN minutes -- expected points -- not medians), scored on
# 2025-26 (2,466 team-games, 32,224 active player-games) with histories carried
# across seasons as the live build does. Held out, mean |error| (bias) in minutes:
#
#                                  starters confirmed          inactives only
#                                  v1 (10/15)   this model     v1        this model
#   all                            5.74 (-0.1)  5.26 (-0.1)    5.97      5.78
#   RMSE all                       7.76         6.81           8.15      7.65
#   opening week (team games 1-5)  6.21         5.30           6.54      5.41
#   rookies / no NBA history       6.30         5.46           6.75      5.99
#   lottery rookies (75)           7.32 (-3.7)  5.17 (-1.4)    8.61      6.10
#   new team, first 5 games        7.11         6.21           7.50      6.27
#   first game back from 5+ out    6.59 (+3.0)  5.99 (-0.6)    6.96      6.34
#   teammates of a scratch         5.80         5.34           6.05      5.88
#   promoted starters              5.71 (+0.4)  5.20 (+1.3)    --        --
#   starters, roles > 1.15 x 240   5.65 (-4.1)  4.68 (-2.2)    5.77      5.70
# Legacy protocol (scripts/nba_minutes_backtest.py, 29,311 player-games): 5.601 -> 5.123
# confirmed, 5.831 -> 5.624 inactives only. `scripts/nba_minutes_lab.py ablate` regenerates all of it.
# Tried and NOT kept (no held-out gain): back-to-back and blowout (expected
# margin) adjustments, an overflow-dependent starter tilt, splitting the 240
# excess between starter and bench pools, a flat-power 240 share.
#: Weight per game of this season's games, newest = 1 (0.8: about the last 5).
DECAY = 0.8
#: Last season's role (all of it, flat: PRIOR_DECAY 1.0) as a prior worth this many
#: games. Night one = last season as a whole; ten games in, mostly this season.
#: kappa 0.5 / 1 / 2 on 2025-26: 5.24 / 5.26 / 5.33 all, 5.38 / 5.30 / 5.29 opening.
PRIOR_KAPPA = 1.0
PRIOR_DECAY = 1.0
#: Starting and bench roles, each shrunk toward the other +/- SPLIT_GAP minutes with
#: SPLIT_K pseudo-games: a bench player who starts tonight is projected ~SPLIT_GAP up.
SPLIT_K = 0.5
SPLIT_GAP = 12.0
#: Critique cycle 5 (2026-10-22): the STARTING role (ms) has its own, longer memory --
#: this season's games decay 0.9 per game (about the last 10, vs 5 for the bench role
#: and the pre-lineup role) and last season counts as 3 games (vs 1). A starter's
#: minutes are stable, so one hot or cold week is mostly noise; a bench role really
#: does move week to week (critic #4: bench decay 0.7 / 0.9 both worse). Grid 0.8-0.97
#: x 1-8 on 2024-25 ONLY, rank-gap coefficients refitted per cell
#: (scripts/nba_minutes_lab.py cycle5): 0.9 / 3 best (6.9245, both modes; 0.93 / 3 ties).
#: Held out 2025-26 (32,224 player-games), starters confirmed: RMSE 6.586 -> 6.550,
#: MAE 4.929 -> 4.891; live state 6.634 -> 6.598; Expected five (25% wrong) 6.955 ->
#: 6.939; inactives only unchanged (7.396). 2026-27 so far (387): 7.582 -> 7.290.
MS_DECAY = 0.9
MS_KAPPA = 3.0
#: Redistribution of missing minutes (refitted with the split).
ROLE_POW_V2 = 0.25
POS_W_V2 = 0.25
#: (Deleted 2026-10-28: ALLOC_POW / STARTER_TILT, v2's 240 share. Under the rank-gap step
#: it was unreachable -- a full team returns from rank_gap first -- and removing it changed
#: no output on 3,000 team-games. The lab keeps its own copy for the v2 ablation chain.)
#: First, second, third game back from a 5+ team-game absence (fitted 0.75 / 0.85;
#: 0.8 / 0.9 held out better on 2025-26: first game back bias +3.0 -> -0.6).
RAMP = (0.8, 0.9, 1.0)
#: Fewer NBA games than this: role from the draft pick, minutes = a + b ln(pick)
#: (2024-25, 611 first games of players with < 5 NBA games; preseason minutes add
#: nothing on top: -0.003 per minute), shrunk with ROOKIE_K pseudo-games of his own.
ROOKIE_GAMES = 5
ROOKIE_PRIOR = (22.19, -4.28)
ROOKIE_K = 0.5
#: Undrafted / unknown pick.
PICK_UNDRAFTED = 70
#: (Deleted 2026-10-28: SALARY_NUDGE. DK salary on top of the model helped v2 only because
#: v2's 240 step squeezed stars; after the rank-gap step it added nothing, 4.830 -> 4.834
#: MAE, and had been OFF since. (-2.22, 1.031, 0.365) was the v2 fit.)
#: Critique cycle 1 (NBA_MINUTES_CRITIQUE_LOG.md): the team's gap to 240 is closed by ROLE
#: RANK. v2 added every inactive regular's minutes back (most of them already out for days,
#: their absence already in teammates' roles) and then shared 240 in proportion to role, so
#: stars moved as much as the bench. In the data stars barely move and ranks 8-12 absorb the
#: gap. Held out 2025-26, starters confirmed: RMSE 6.810 -> 6.692, MAE 5.26 -> 5.03;
#: inactives only 7.649 -> 7.474. Coefficients: edge/nba_minutes_gap.py (generated).
#: A full team (FULL_ROSTER+ active) always takes it; a short-handed one keeps the
#: redistribution below (ROLE_POW_V2 / POS_W_V2).
#: Critique cycle 2: is the coach playing him RIGHT NOW? A non-starter's role (bench role
#: once the five is announced; start rate < 0.5 before) moves RECENCY_LAM of the way to his
#: last game's minutes (a DNP counts 0), if his start rate < RECENCY_TAU; a regular starter
#: (start rate >= DROP_TAU) who is active but NOT in the announced five gets DROP_Q of his
#: role (they play 0 in 60-67% of cases). Fitted on 2024-25 (lambda 0.1-0.3 x q 0-0.5, the
#: rank-gap coefficients refitted for each): lambda 0.25, q 0.2. Guards: this season's games
#: >= 1 (>= 3 for the drop) and not back from an absence -- otherwise the "last game" is
#: last season's finale or weeks old. The guards cost 0.008 RMSE on 2024-25 but without them
#: 2025-26 opening week 5.01 -> 5.18 and 2026-27's night one 6.98 -> 8.54 (a judgment call,
#: recorded in NBA_MINUTES_CRITIQUE_LOG.md). Held out 2025-26: RMSE 6.692 -> 6.586
#: (confirmed), 7.474 -> 7.396 (inactives only).
RECENCY_LAM = 0.25
RECENCY_TAU = 0.8
DROP_Q = 0.2
DROP_TAU = 0.8
#: Critique cycle 4 (2026-10-21): until a team CONFIRMS its five (~30 min before tip),
#: NBA.com's starters are *Expected* -- its guess -- and the last Expected five missed a
#: starter in 7 of 28 regular-season team-games on 2026-10-20/21 (8 of 140 starters;
#: data/nba_lineup_log.csv; 50% in preseason). The live build applied the start/bench
#: split and the dropped-starter cut to it as if confirmed. Now minutes = EXPECTED_W x
#: project(Expected five) + (1 - EXPECTED_W) x project(no five). Chosen on 2024-25 ONLY
#: (scripts/nba_minutes_lab.py cycle4: every team-game projected with its five right,
#: with one miss -- the lowest-start-rate starter swapped for the highest-start-rate
#: active non-starter -- and with no five) by minimax regret over error rates 10-30%
#: (25% measured on opening nights; CI ~11-45%): w = 0.70, regret <= 0.018 RMSE; the
#: closed-form optimum is 0.82 at 10%, 0.66 at 25%. Held out 2025-26 (32,224
#: player-games), RMSE as confirmed -> blend: 10% wrong 6.800 -> 6.744, 25% wrong
#: 7.108 -> 6.955 (live state: 7.155 -> 6.994); cost when the five is right 6.586 ->
#: 6.599, promoted starters' bias +0.9 -> -1.3 until the five is confirmed.
EXPECTED_W = 0.70
#: Critique cycle 6 (2026-10-22): the live Out list misses scratches. ESPN's Out feed
#: lists injuries, not two-way / G-League players or most Day-To-Day sits: of the 47
#: role-10+ players who did not dress on 2026-10-20/21, ESPN's 10/22 feed had 18 Out,
#: 16 Day-To-Day and 11 not at all. A player the board keeps who did NOT dress for his
#: team's latest game (history `missed_last`; announced starters excepted) is STALE:
#:   teammates   minutes = AVAIL_W x (stale players in) + (1 - AVAIL_W) x (stale out,
#:               the next-largest roles kept)
#:   himself     minutes = AVAIL_P[role < AVAIL_ROLE, role >= AVAIL_ROLE] x his minutes if in
#:               (expected minutes: DFS scores a non-dresser 0)
#: Chosen on 2024-25 ONLY (scripts/nba_minutes_lab.py cycle6), per information state, by
#: minimax regret of the board RMSE -- every player the board projects, a non-dresser as
#: 0 -- over Out-list miss rates 0 / 25 / 50% (a role-10+ scratch missed at random).
#: Held out 2025-26, board RMSE shipped -> soft at 0 / 25 / 50% missed:
#:   five announced  6.385 -> 6.319, 6.766 -> 6.459, 7.078 -> 6.578
#:   no five         7.168 -> 7.206, 8.334 -> 7.889, 9.219 -> 8.442
#: (no five costs 0.04 if the list were complete; 11 of 47 unlisted puts it above ~23%).
#: For stale players under role 10 the fitted P(dress) is 0.22-0.27 whatever the miss rate.
AVAIL_ROLE = 10.0
AVAIL_W = {"confirmed": 0.4, "inactives": 0.4}
AVAIL_P = {"confirmed": (0.2, 0.5), "inactives": (0.25, 0.7)}
#: Critique cycle 7 (2026-10-22): the NBA's official injury report (edge/nba_injury_report.py)
#: is the Out list; a kept player who is Doubtful / Questionable / Probable (Available =
#: Probable) or not listed but "stale" (sat his team's latest game this season) dresses with
#: P = REPORT_P[mode][(category, role >= REPORT_ROLE)] -- the share who dressed among such
#: kept players in 2024-25 (report ~60 min before tip, n in brackets), applied by
#: project_uncertain. Held out 2025-26, squared error of the board (non-dressers = 0) per
#: active player-game, ESPN-like Out + cycle 6's stale hedge -> official + these:
#: five announced 6.624 -> 6.438, no five 7.357 -> 7.077 (active RMSE 6.298 -> 6.261,
#: 6.721 -> 6.674); phantoms 4,297 -> 1,894 (scripts/nba_minutes_lab.py cycle7).
#: Doubtful is thin (n 14-15). Without a report (fetch failed) the stale p fall back to AVAIL_P.
REPORT_ROLE = 10.0
REPORT_P = {
    "confirmed": {("Doubtful", False): 0.20, ("Doubtful", True): 0.36,          # n 15 / 14
                  ("Questionable", False): 0.72, ("Questionable", True): 0.80,  # n 36 / 113
                  ("Probable", False): 0.95, ("Probable", True): 0.99,          # n 477 / 597
                  ("stale", False): 0.45, ("stale", True): 0.51},               # n 1,255 / 427
    "inactives": {("Doubtful", False): 0.20, ("Doubtful", True): 0.40,          # n 15 / 15
                  ("Questionable", False): 0.72, ("Questionable", True): 0.88,  # n 36 / 188
                  ("Probable", False): 0.95, ("Probable", True): 1.00,          # n 498 / 1,866
                  ("stale", False): 0.45, ("stale", True): 0.68},               # n 1,258 / 664
}
#: History kept per player (a full season).
HIST_GAMES = 82


def groups(position: str | None) -> set:
    """'G-F' -> {'G', 'F'}; unknown -> empty."""
    return set((position or "").replace("-", " ").split())


def role(history: list, window: int | None = None) -> dict:
    """{"base", "start_rate", "games"} from [(minutes, started), ...] newest last,
    restricted to games the player was active for."""
    recent = history[-(window or WINDOW):]
    if not recent:
        return {"base": 0.0, "start_rate": 0.0, "games": 0}
    return {"base": sum(m for m, _ in recent) / len(recent),
            "start_rate": sum(1 for _, s in recent if s) / len(recent), "games": len(recent)}


def project_v1(team: list, params: dict | None = None) -> dict:
    """The 2026-10-15 model (fixed windows, promotion lift, EXCESS_POW). Minutes for tonight. `team` = [{"id", "position", "inactive": bool,
    "starter": bool | None (None = not announced), "history": [(min, started), ...]}].
    Returns {id: {"minutes", "base", "promoted", "share"}} for active players."""
    p = {"ROLE_POW": ROLE_POW, "POS_W": POS_W, "PROMO_W": PROMO_W, "PROMO_LIFT": PROMO_LIFT,
         "STARTER_MIN": STARTER_MIN, "EXCESS_POW": EXCESS_POW, **(params or {})}
    roles = {pl["id"]: role(pl["history"], pl.get("window")) for pl in team}
    out_pl = [pl for pl in team if pl["inactive"] and roles[pl["id"]]["base"] >= REGULAR_MIN]
    missing = sum(roles[pl["id"]]["base"] for pl in out_pl)
    lost_groups: dict = {}
    for pl in out_pl:
        for g in groups(pl.get("position")):
            lost_groups[g] = lost_groups.get(g, 0.0) + roles[pl["id"]]["base"]
    active = [pl for pl in team if not pl["inactive"]]
    weights, promoted = {}, {}
    for pl in active:
        r = roles[pl["id"]]
        promo = bool(pl.get("starter")) and r["start_rate"] < 0.5
        promoted[pl["id"]] = promo
        pos_share = 0.0
        if lost_groups:
            mine = groups(pl.get("position"))
            pos_share = sum(v for g, v in lost_groups.items() if g in mine) / max(missing, 1e-9)
        w = (max(r["base"], 1.0) ** p["ROLE_POW"]) * (1 + p["POS_W"] * min(1.0, pos_share)) \
            * (1 + p["PROMO_W"] * promo)
        weights[pl["id"]] = w
    total_w = sum(weights.values()) or 1.0
    mins = {}
    for pl in active:
        r = roles[pl["id"]]
        m = r["base"] + missing * weights[pl["id"]] / total_w
        if promoted[pl["id"]]:
            m += p["PROMO_LIFT"] * max(0.0, p["STARTER_MIN"] - m)
        mins[pl["id"]] = min(48.0, max(0.0, m))
    s = sum(mins.values())
    # Only a full roster can be held to 240: preseason files (and a team whose bench is
    # not listed yet) carry the starting five alone, which would each be pushed to 48.
    if s > 0 and len(active) >= FULL_ROSTER:
        if s > TEAM_MIN and p["EXCESS_POW"] != 1.0:
            # More roles than minutes: share the 240 by role^EXCESS_POW, so a star
            # keeps more of his and the 11th-13th men give theirs back.
            w = {i: m ** p["EXCESS_POW"] for i, m in mins.items()}
            ws = sum(w.values())
            mins = {i: min(48.0, TEAM_MIN * w[i] / ws) for i in mins}
        else:
            k = TEAM_MIN / s
            mins = {i: min(48.0, m * k) for i, m in mins.items()}
    return {pl["id"]: {"minutes": round(mins[pl["id"]], 1), "base": round(roles[pl["id"]]["base"], 1),
                       "promoted": promoted[pl["id"]],
                       "share": round(weights[pl["id"]] / total_w, 3)} for pl in active}


def role_v2(history: list, cur_n: int | None = None, n_pre: int = 0, params: dict | None = None) -> dict:
    """Weighted role from [(minutes, started), ...] newest last. `cur_n` = how many of
    the newest REAL entries are this season (None = all); the last `n_pre` entries are
    preseason games (kept for the points rate, not the minutes role)."""
    p = {"DECAY": DECAY, "PRIOR_KAPPA": PRIOR_KAPPA, "PRIOR_DECAY": PRIOR_DECAY,
         "SPLIT_K": SPLIT_K, "SPLIT_GAP": SPLIT_GAP, "MS_DECAY": MS_DECAY, "MS_KAPPA": MS_KAPPA,
         **(params or {})}
    real = list(history[: len(history) - n_pre]) if n_pre else list(history)
    real = real[-HIST_GAMES:]
    n = len(real)
    cur = n if cur_n is None else max(0, min(int(cur_n), n))
    w = [0.0] * n
    for j in range(n - cur, n):
        w[j] = p["DECAY"] ** (n - 1 - j)
    n_prior = n - cur
    if n_prior:
        pw = [p["PRIOR_DECAY"] ** (n_prior - 1 - j) for j in range(n_prior)]
        tot = sum(pw) or 1.0
        for j in range(n_prior):
            w[j] = p["PRIOR_KAPPA"] * pw[j] / tot
    W = sum(w)
    if W <= 0:
        return {"base": 0.0, "start_rate": 0.0, "ms": 0.0, "mb": 0.0, "ns": 0.0, "nb": 0.0, "games": n}
    base = sum(wi * m for wi, (m, _) in zip(w, real)) / W
    srate = sum(wi * bool(s) for wi, (_, s) in zip(w, real)) / W
    ns = sum(wi for wi, (_, s) in zip(w, real) if s)
    nb = sum(wi for wi, (_, s) in zip(w, real) if not s)
    ms_raw = sum(wi * m for wi, (m, s) in zip(w, real) if s) / ns if ns > 0 else 0.0
    mb_raw = sum(wi * m for wi, (m, s) in zip(w, real) if not s) / nb if nb > 0 else 0.0
    k, gap = p["SPLIT_K"], p["SPLIT_GAP"]
    ms = (ns * ms_raw + k * (mb_raw + gap if nb > 0 else base + gap * (1 - srate))) / (ns + k)
    mb = (nb * mb_raw + k * (ms_raw - gap if ns > 0 else base - gap * srate)) / (nb + k)
    if p["MS_DECAY"] is not None:
        # critique cycle 5: the starting role reads further back (MS_DECAY, MS_KAPPA)
        ms = role_v2(history, cur_n, n_pre, {**p, "DECAY": p["MS_DECAY"], "PRIOR_KAPPA": p["MS_KAPPA"],
                                              "MS_DECAY": None})["ms"]
    return {"base": base, "start_rate": srate, "ms": ms, "mb": mb, "ns": ns, "nb": nb, "games": n}


def project(team: list, params: dict | None = None) -> dict:
    """Minutes for tonight (the 2026-10-21 model). `team` = [{"id", "position",
    "inactive": bool, "starter": bool | None (None = not announced), "history":
    [(min, started), ...]}] plus, when known: "cur_n" (this season's entries),
    "n_pre" (trailing preseason entries), "since_return", "pick", "salary".
    A starter row with "confirmed": False makes the five NBA.com's *Expected* guess:
    minutes are then EXPECTED_W x this five + (1 - EXPECTED_W) x no five (critique
    cycle 4); a missing "confirmed" means the five is known (backtests, the lab).
    Returns {id: {"minutes", "base", "promoted", "share"}} for active players."""
    import math

    p = {"ROLE_POW": ROLE_POW_V2, "POS_W": POS_W_V2,
         "RAMP": RAMP, "ROOKIE_PRIOR": ROOKIE_PRIOR, "ROOKIE_K": ROOKIE_K,
         "RECENCY_LAM": RECENCY_LAM, "DROP_Q": DROP_Q, "EXPECTED_W": EXPECTED_W,
         **(params or {})}
    if p["EXPECTED_W"] is not None and any(pl.get("starter") and pl.get("confirmed") is False for pl in team):
        w, known = p["EXPECTED_W"], {**p, "EXPECTED_W": None}
        hard = project([{**pl, "confirmed": None} for pl in team], known)
        soft = project([{**pl, "starter": None} for pl in team], known)
        return {i: {**v, "minutes": round(w * v["minutes"] + (1 - w) * soft.get(i, v)["minutes"], 1)}
                for i, v in hard.items()}
    roles_, start_, promoted = {}, {}, {}
    for pl in team:
        hist = pl["history"]
        n_pre = int(pl.get("n_pre") or 0)
        r = role_v2(hist, pl.get("cur_n"), n_pre, p)
        b, s = r["base"], r["start_rate"]
        real = hist[: len(hist) - n_pre] if n_pre else hist
        n_real = len(real)
        season = pl.get("cur_n") if pl.get("cur_n") is not None else n_real
        special = False
        if n_pre and sum(1 for m, _ in real if m > 0) < 5:
            # the live rule for a player with < 5 real games: his preseason counts
            seq = [m for m, _ in hist][-(SEASON_START_WINDOW if season < 5 else WINDOW):]
            b, s = (sum(seq) / len(seq) if seq else 0.0), 0.0
            special = True
        if n_real < ROOKIE_GAMES and p["ROOKIE_PRIOR"]:
            special = True
            a0, a1 = p["ROOKIE_PRIOR"]
            prior = max(0.0, a0 + a1 * math.log(max(1, pl.get("pick") or PICK_UNDRAFTED)))
            own = sum(m for m, _ in real) / n_real if n_real else 0.0
            b = (n_real * own + p["ROOKIE_K"] * prior) / (n_real + p["ROOKIE_K"])
        if pl.get("starter") is not None and (r["ns"] + r["nb"]) > 0:
            b = r["ms"] if pl["starter"] else r["mb"]
        # critique cycle 2: last game for a non-starter; a regular starter left out of the five
        sr0, back = r["start_rate"], pl.get("since_return")
        announced = pl.get("starter") is not None
        benchish = (announced and not pl["starter"]) or (not announced and sr0 < 0.5)
        if (p["RECENCY_LAM"] and benchish and not special and sr0 < RECENCY_TAU and real
                and season >= 1 and back is None):
            b = b + p["RECENCY_LAM"] * (real[-1][0] - b)
        if (p["DROP_Q"] is not None and announced and not pl["starter"] and sr0 >= DROP_TAU
                and back is None and season >= 3):
            b *= p["DROP_Q"]
        sr = pl.get("since_return")
        if sr is not None and 0 <= sr < len(p["RAMP"]):
            b *= p["RAMP"][sr]
        # cycle 17: a limited Doubtful / Questionable player's role x K (project_uncertain);
        # the 240 step then re-shares what he gives up
        roles_[pl["id"]] = max(0.0, b) * float(pl.get("role_scale", 1.0))
        start_[pl["id"]] = (1.0 if pl["starter"] else 0.0) if pl.get("starter") is not None else s
        promoted[pl["id"]] = bool(pl.get("starter")) and r["start_rate"] < 0.5
    active = [pl for pl in team if not pl["inactive"]]
    if len(active) >= FULL_ROSTER:
        from edge.nba_minutes_gap import GAP_COEF
        mode = "confirmed" if any(pl.get("starter") is not None for pl in team) else "inactives"
        mins, slope = rank_gap({pl["id"]: roles_[pl["id"]] for pl in active}, GAP_COEF[mode])
        ss = sum(slope.values()) or 1.0
        return {pl["id"]: {"minutes": round(mins[pl["id"]], 1), "base": round(roles_[pl["id"]], 1),
                           "promoted": promoted[pl["id"]], "share": round(slope[pl["id"]] / ss, 3)}
                for pl in active}
    out_pl = [pl for pl in team if pl["inactive"] and roles_[pl["id"]] >= REGULAR_MIN]
    missing = sum(roles_[pl["id"]] for pl in out_pl)
    lost: dict = {}
    for pl in out_pl:
        for g in groups(pl.get("position")):
            lost[g] = lost.get(g, 0.0) + roles_[pl["id"]]
    weights = {}
    for pl in active:
        share = sum(v for g, v in lost.items() if g in groups(pl.get("position"))) / missing if missing else 0.0
        weights[pl["id"]] = max(roles_[pl["id"]], 1.0) ** p["ROLE_POW"] * (1 + p["POS_W"] * min(1.0, share))
    tw = sum(weights.values()) or 1.0
    mins = {pl["id"]: min(48.0, max(0.0, roles_[pl["id"]] + missing * weights[pl["id"]] / tw)) for pl in active}
    return {pl["id"]: {"minutes": round(mins[pl["id"]], 1), "base": round(roles_[pl["id"]], 1),
                       "promoted": promoted[pl["id"]], "share": round(weights[pl["id"]] / tw, 3)}
            for pl in active}



def project_available(team: list, stale: set, mode: str, keep: set | None = None,
                      params: dict | None = None) -> tuple[dict, dict]:
    """Critique cycle 6: `project` when some dressing players may not dress after all.
    `stale` = ids of players who did not dress for their team's latest game and are not
    on the Out list (nor announced starters); `keep` = the ids kept if they all sit (the
    roster cut re-run without them; None = everyone not inactive). Teammates get
    AVAIL_W x (stale in) + (1 - AVAIL_W) x (stale out); a stale player gets his EXPECTED
    minutes, AVAIL_P (by role < / >= AVAIL_ROLE) x his minutes if he plays. Mirrors
    scripts/nba_minutes_lab.avail_score. Returns (minutes as project(), {id: P(dress)})."""
    a = project(team, params)
    if not stale:
        return a, {}
    b = project([{**pl, "inactive": pl["id"] in stale or (pl["inactive"] if keep is None else pl["id"] not in keep)}
                 for pl in team], params)
    w, (lo, hi) = AVAIL_W[mode], AVAIL_P[mode]
    out, avail = {}, {}
    for pl in team:
        i = pl["id"]
        ai, bi = a.get(i), b.get(i)
        if i in stale and ai:
            big = role_v2(pl["history"], pl.get("cur_n"), int(pl.get("n_pre") or 0))["base"] >= AVAIL_ROLE
            avail[i] = hi if big else lo
            out[i] = {**ai, "minutes": round(avail[i] * ai["minutes"], 1)}
        elif ai or bi:                          # bi alone: dresses only if the stale players sit
            out[i] = {**(ai or bi), "minutes": round(w * (ai or {}).get("minutes", 0.0)
                                                    + (1 - w) * (bi or {}).get("minutes", 0.0), 1)}
    return out, avail


def project_uncertain(team: list, unc: dict, mode: str, keeps: dict | None = None,
                      probs: dict | None = None, params: dict | None = None) -> tuple[dict, dict]:
    """Critique cycle 7: expected minutes when some dressing players may sit. `unc` =
    {id: category} -- "Doubtful" / "Questionable" / "Probable" on the NBA's official
    injury report, or "stale" (not listed, did not dress for his team's latest game this
    season); announced starters are never uncertain. `keeps` = {id: ids kept if he sits}
    (the roster cut re-run; None = everyone not inactive). With A = all dress and B_k =
    k sits, and P(dress) p by (category, role >= REPORT_ROLE):
        m_i = A_i + sum over uncertain k != i of (1 - p_k) (B_k,i - A_i),   x p_i if i is uncertain
    on his limit for a Doubtful / Questionable player: role x REPORT_K, the rest re-shared
    by the 240 step (cycles 16-17).
    Mirrors scripts/nba_minutes_lab.report_score. Returns (minutes as project(), {id: p})."""
    a = project(team, params)
    if not unc:
        return recency(a, team, mode), {}
    probs = probs or REPORT_P[mode]
    by_id = {pl["id"]: pl for pl in team}
    p = {}
    for k, c in unc.items():
        pl = by_id[k]
        big = role_v2(pl["history"], pl.get("cur_n"), int(pl.get("n_pre") or 0))["base"] >= REPORT_ROLE
        p[k] = probs[(c, big)]
    B = {}
    for k in unc:
        kept = None if keeps is None else keeps.get(k)
        B[k] = project([{**pl, "inactive": pl["id"] == k or (pl["inactive"] if kept is None else pl["id"] not in kept)}
                        for pl in team], params)
    # cycle 17: L_k = k dresses on his limit (role x REPORT_K), the 240 step re-sharing the rest
    kq = REPORT_K.get(mode, {})
    Lk = {k: project([{**pl, "role_scale": kq[c]} if pl["id"] == k else pl for pl in team], params)
          for k, c in unc.items() if c in kq}
    out = {}
    for i in set(a) | {i for b in B.values() for i in b}:
        ai = a.get(i, {}).get("minutes", 0.0)
        sits = sum((1 - p[k]) * (B[k].get(i, {}).get("minutes", 0.0) - ai) for k in unc if k != i)
        if i in Lk:                  # his own minutes: on his limit, as re-shared
            m = (Lk[i].get(i, {}).get("minutes", 0.0) + sits) * p[i]
        else:
            m = ai + sits + sum(p[k] * (Lk[k].get(i, {}).get("minutes", 0.0) - ai) for k in Lk if k != i)
            if i in unc:
                m *= p[i]
        base = a.get(i) or next(B[k][i] for k in unc if i in B[k])
        out[i] = {**base, "minutes": round(max(0.0, m), 1)}
    return recency(out, team, mode, p), p

#: Critique cycle 18 (2026-10-28): the role trails the last few games. What is left of the
#: error after the whole pipeline (report replay, 240 step, REPORT_K re-share) still
#: tracks how far a player's last 3 dressed games THIS season (DNP zeros counted) sit from
#: his projection. Correction c x (last3 - m) by (state, starter), least squares through
#: zero on 2024-25 only (n 31,388 dressed rows per state); then the team's corrections are
#: made zero-sum, so the 240 total is untouched. "Starter" = the posted five when it is
#: posted, else start_rate >= 0.5 (the actual start is not known before lock). Held out
#: 2025-26, report replay, zero-sum over every kept player (who dresses is unknown
#: pre-lock), n 31,218 dressed: confirmed RMSE/MAE 6.2704/4.7069 -> 6.2142/4.6673, no five
#: 6.5672/4.9033 -> 6.5599/4.8916 (NBA_MINUTES_CRITIQUE_LOG.md cycle 18;
#: scripts/critic/nba_recency_fix/).
RECENT_C = {"confirmed": (0.080, 0.168), "inactives": (0.144, 0.051)}   # (starter, bench)
RECENT_N = 3


def recency(out: dict, team: list, mode: str, p: dict | None = None) -> dict:
    """Critique cycle 18: shift each dressing player toward his last RECENT_N games this
    season (RECENT_C), zero-sum within the team. An uncertain player is corrected on his
    minutes IF he dresses (m / p), then scaled back by p. Scored by scripts/critic/nba_recency_fix/."""
    cs, cb = RECENT_C.get(mode, (0.0, 0.0))
    p = p or {}
    by_id = {pl["id"]: pl for pl in team}
    corr = {}
    for i, o in out.items():
        pl = by_id.get(i)
        pi = p.get(i, 1.0)
        if pl is None or pl.get("inactive") or pi <= 0 or o["minutes"] <= 0:
            continue
        n_pre = int(pl.get("n_pre") or 0)
        real = list(pl["history"][: len(pl["history"]) - n_pre]) if n_pre else list(pl["history"])
        cur = len(real) if pl.get("cur_n") is None else max(0, min(int(pl["cur_n"]), len(real)))
        last = [m for m, _ in real[len(real) - cur:]][-RECENT_N:]
        if not last:
            corr[i] = 0.0
            continue
        if mode == "confirmed" and pl.get("starter") is not None:
            st = bool(pl.get("starter"))
        else:
            st = role_v2(pl["history"], pl.get("cur_n"), n_pre)["start_rate"] >= 0.5
        corr[i] = (cs if st else cb) * (sum(last) / len(last) - o["minutes"] / pi)
    if not corr:
        return out
    mean = sum(corr.values()) / len(corr)
    res = dict(out)
    for i, c in corr.items():
        pi = p.get(i, 1.0)
        res[i] = {**out[i], "minutes": round(max(0.0, (out[i]["minutes"] / pi + c - mean) * pi), 1)}
    return res


#: Critique cycle 16 (2026-10-28): a Doubtful / Questionable player who DOES dress plays on
#: a limit -- the cycle 7 hedge only priced whether he dresses. His minutes given he dresses
#: are K x the model's. Fitted on 2024-25 (OLS through 0 on dressed players, official-report
#: replay, phantoms out): confirmed Doubtful 0.0 (n 8), Questionable 0.688 (n 116); no five
#: Doubtful 0.202 (n 9), Questionable 0.766 (n 191). Held out 2025-26, active RMSE / MAE:
#: confirmed 6.301/4.724 -> 6.271/4.707, no five 6.641/4.919 -> 6.575/4.909 (board 6.291 ->
#: 6.269, 6.844 -> 6.795); held-out Doubtful dressers n 39: bias +21.0 -> +4.3 (no five),
#: Questionable n 378 +4.2 -> -1.6. Probable / stale fit ~1.0 and did not help: left at 1.
#: Cycle 17 (same day): those minutes are RE-SHARED through the 240 step (L_k in
#: project_uncertain) and confirmed Questionable K refit under it on 2024-25 (grid x0.8-1.2:
#: x1.1 -> 0.757; no five kept x1.0). Held out 2025-26 vs cycle 16, active RMSE / MAE:
#: confirmed 6.2713/4.7070 -> 6.2704/4.7068, no five 6.5748/4.9089 -> 6.5672/4.9033, board
#: 6.795 -> 6.786, no-five bias -0.34 -> -0.25 (train confirmed 6.0516 -> 6.0524).
REPORT_K = {"confirmed": {"Doubtful": 0.0, "Questionable": 0.757},
            "inactives": {"Doubtful": 0.202, "Questionable": 0.766}}


#: Role ranks with their own allocation cell; rank 14 and below share the last one.
GAP_RANKS = 13


def rank_gap(roles: dict, coef: dict) -> tuple[dict, dict]:
    """Close a team's gap to 240 by role rank (critique cycle 1). G = 240 - sum of the
    active players' roles; the player at rank k moves a(k, G > 0) + b(k, G > 0) x G, then
    the remaining difference is shared by the slopes, nobody outside 0-48.
    Returns (minutes, slope) by player id. Mirrors scripts/nba_minutes_lab._rank_gap."""
    G = TEAM_MIN - sum(roles.values())
    order = sorted(roles.items(), key=lambda x: -x[1])
    out, slope = {}, {}
    for rk, (i, b) in enumerate(order):
        a0, a1 = coef[(min(rk, GAP_RANKS), G > 0)]
        out[i], slope[i] = b + a0 + a1 * G, max(a1, 1e-3)
    for _ in range(5):
        miss = TEAM_MIN - sum(out.values())
        free = {i: slope[i] for i in out if (miss > 0 and out[i] < 48.0) or (miss < 0 and out[i] > 0.0)}
        if abs(miss) < 1e-6 or not free:
            break
        ss = sum(free.values())
        for i in free:
            out[i] += miss * free[i] / ss
        out = {i: min(48.0, max(0.0, m)) for i, m in out.items()}
    return out, slope


def games_since_return(tail: list, team: str, next_game: int, season_games: int,
                       last_team: str | None, gap: int = 5, cap: int = 10) -> int | None:
    """Games a player has played since coming back from a `gap`+ team-game absence
    this season, counting tonight's as 0 when tonight is the first game back; None
    when there is no such absence (or it is more than `cap` games ago).

    `tail` = his ACTIVE games this season, oldest first, as (team, team_game_no);
    `next_game` = tonight's team game number; `season_games` = len of his games this
    season; `last_team` = the team of his last game (any season). A player who missed
    the season's first `gap`+ games and is back with the same team counts too.
    Exactly the walk scripts/nba_minutes_lab.py fits the return ramp on."""
    prev_no, k, found = next_game, 0, None
    for t, no in reversed(tail):
        if t != team:
            break
        if prev_no - no - 1 >= gap:
            found = k
            break
        prev_no, k = no, k + 1
    if found is None and last_team == team and k == season_games and prev_no - 1 >= gap:
        found = k
    return found if found is not None and found <= cap else None


def load_history(seasons=("2024-25", "2025-26", "2026-27"), before: str | None = None,
                 meta: dict | None = None) -> dict:
    """{player_id: [(minutes, started, dk_points), ...]} oldest first, the same
    definition the backtest fits on: every game he was ACTIVE for (from the cached
    NBA.com lineups, data/nba_lineups/), with 0 for a DNP-coach's-decision. A day
    with no cached lineup falls back to the box score alone. `before` = 'YYYY-MM-DD'.
    `meta`, if given, is filled with {player_id: [(date, team), ...]} aligned with
    the entries (scripts/nba_history_publish.py: this season's games, absences)."""
    import json
    from collections import defaultdict
    from pathlib import Path

    from edge.nba import actual_points
    from edge.nba_lineups import CACHE, parse

    gt = Path(__file__).resolve().parents[1] / "data" / "nba_ground_truth"
    by_day: dict = defaultdict(list)
    for s in seasons:
        path = gt / f"player_gamelog_{s}.json"
        if path.exists():
            for r in json.loads(path.read_text()):
                by_day[r["GAME_DATE"]].append(r)
    hist: dict = defaultdict(list)
    for d in sorted(by_day):
        if before and d >= before:
            break
        box = {(str(r["PLAYER_ID"]), r["GAME_ID"]): r for r in by_day[d]}
        f = CACHE / f"{d.replace('-', '')}.json"
        if f.exists():
            from edge.nba_availability import apply as availability
            from edge.nba_injury_report import out_at_tip
            from edge.names import norm
            for g in parse(json.loads(f.read_text())):
                # NBA.com stopped flagging inactives in 2026-27: the box score's comments
                # (edge/nba_availability.py) say who was really out -- critique cycle 3
                out = out_at_tip(g["game_id"])
                for p in availability(g["players"], g["game_id"]):
                    if p["inactive"]:
                        continue
                    pid = str(p["id"])
                    r = box.get((pid, g["game_id"]))
                    # listed Out by the official report yet flagged active, 0 minutes:
                    # not a role game (critique cycle 8, -0.055 / -0.124 board RMSE)
                    if out and not (r and float(r["MIN"] or 0)) and norm(p["name"]) in out:
                        continue
                    hist[pid].append((float(r["MIN"] or 0), p["starter"], actual_points(r))
                                     if r else (0.0, p["starter"], 0.0))
                    if meta is not None:
                        meta.setdefault(pid, []).append((d, p["team"]))
        else:
            for (pid, _), r in box.items():
                hist[pid].append((float(r["MIN"] or 0), False, actual_points(r)))
                if meta is not None:
                    meta.setdefault(pid, []).append((d, r["TEAM_ABBREVIATION"]))
    return dict(hist)


def points_per_minute(history: list, window: int = 30) -> float:
    """DK points per minute over his recent games (the rate a minutes change scales)."""
    recent = [(m, p) for m, _, p in history[-window:] if m > 0]
    mins = sum(m for m, _ in recent)
    return sum(p for _, p in recent) / mins if mins >= 30 else 0.0
