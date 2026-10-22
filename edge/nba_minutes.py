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
#: Redistribution of missing minutes (refitted with the split).
ROLE_POW_V2 = 0.25
POS_W_V2 = 0.25
#: The 240 share: m^ALLOC_POW x (1 + STARTER_TILT x starting) (starting = the
#: announced flag, else the start rate).
ALLOC_POW = 1.0
STARTER_TILT = 0.05
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
#: DK salary on top of the model: minutes = a + b x model + c x salary ($k). It helped v2
#: (leave one night out, 11 salary nights, 1,517 player-games: MAE 5.141 -> 4.973) only
#: because v2's 240 step squeezed stars; after critique cycle 1's rank-gap allocation it
#: adds nothing (4.830 -> 4.834), so it is OFF. (-2.22, 1.031, 0.365) was the v2 fit.
SALARY_NUDGE = None
#: Critique cycle 1 (NBA_MINUTES_CRITIQUE_LOG.md): the team's gap to 240 is closed by ROLE
#: RANK. v2 added every inactive regular's minutes back (most of them already out for days,
#: their absence already in teammates' roles) and then shared 240 in proportion to role, so
#: stars moved as much as the bench. In the data stars barely move and ranks 8-12 absorb the
#: gap. Held out 2025-26, starters confirmed: RMSE 6.810 -> 6.692, MAE 5.26 -> 5.03;
#: inactives only 7.649 -> 7.474. Coefficients: edge/nba_minutes_gap.py (generated).
ALLOC = "rank_gap"
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
         "SPLIT_K": SPLIT_K, "SPLIT_GAP": SPLIT_GAP, **(params or {})}
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
    return {"base": base, "start_rate": srate, "ms": ms, "mb": mb, "ns": ns, "nb": nb, "games": n}


def project(team: list, params: dict | None = None) -> dict:
    """Minutes for tonight (the 2026-10-21 model). `team` = [{"id", "position",
    "inactive": bool, "starter": bool | None (None = not announced), "history":
    [(min, started), ...]}] plus, when known: "cur_n" (this season's entries),
    "n_pre" (trailing preseason entries), "since_return", "pick", "salary".
    Returns {id: {"minutes", "base", "promoted", "share"}} for active players."""
    import math

    p = {"ROLE_POW": ROLE_POW_V2, "POS_W": POS_W_V2, "ALLOC_POW": ALLOC_POW, "STARTER_TILT": STARTER_TILT,
         "RAMP": RAMP, "ROOKIE_PRIOR": ROOKIE_PRIOR, "ROOKIE_K": ROOKIE_K, "SALARY_NUDGE": SALARY_NUDGE,
         "RECENCY_LAM": RECENCY_LAM, "DROP_Q": DROP_Q,
         **(params or {})}
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
        roles_[pl["id"]] = max(0.0, b)
        start_[pl["id"]] = (1.0 if pl["starter"] else 0.0) if pl.get("starter") is not None else s
        promoted[pl["id"]] = bool(pl.get("starter")) and r["start_rate"] < 0.5
    active = [pl for pl in team if not pl["inactive"]]
    if p.get("ALLOC", ALLOC) == "rank_gap" and len(active) >= FULL_ROSTER:
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
    if sum(mins.values()) > 0 and len(active) >= FULL_ROSTER:
        w = {i: max(m, 0.0) ** p["ALLOC_POW"] * (1 + p["STARTER_TILT"] * start_[i]) for i, m in mins.items()}
        free, fixed, trial = dict(w), {}, {}
        for _ in range(5):                      # nobody past 48: cap and re-share the rest
            room = TEAM_MIN - sum(fixed.values())
            ws = sum(free.values()) or 1.0
            trial = {i: room * x / ws for i, x in free.items()}
            over = [i for i, m in trial.items() if m > 48.0]
            if not over:
                break
            for i in over:
                fixed[i] = 48.0
                free.pop(i)
        mins = {**trial, **fixed}
    nudge = p["SALARY_NUDGE"]
    if nudge and active and all(pl.get("salary") for pl in active):
        a0, a1, a2 = nudge
        mins = {pl["id"]: min(48.0, max(0.0, a0 + a1 * mins[pl["id"]] + a2 * pl["salary"] / 1000.0))
                for pl in active}
    return {pl["id"]: {"minutes": round(mins[pl["id"]], 1), "base": round(roles_[pl["id"]], 1),
                       "promoted": promoted[pl["id"]], "share": round(weights[pl["id"]] / tw, 3)}
            for pl in active}


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
            for g in parse(json.loads(f.read_text())):
                for p in g["players"]:
                    if p["inactive"]:
                        continue
                    pid = str(p["id"])
                    r = box.get((pid, g["game_id"]))
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
