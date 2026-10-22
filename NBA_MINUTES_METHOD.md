# How we project NBA player minutes for DraftKings DFS

This file describes the minutes model in `edge/nba_minutes.py` (`project`, the
model rebuilt 2026-10-21) and how a slate feeds it (`edge/dfs_run_nba.py`). It is
written so a language model can reproduce the method, reason about it, or critique
it. Every number is measured on real games, fitted on the 2024-25 season and
scored on 2025-26 unless marked otherwise. The previous model (`project_v1`,
2026-10-15) is kept in the code for comparison.

## Why minutes are the core

A player's DK points are modelled as **minutes × DK points per minute**. Over
29,311 player-games in 2025-26, held out:

| projection | mean absolute error (DK points) | correlation |
|---|---|---|
| v1 minutes × our rate | 7.65 | 0.765 |
| player's last-10-game average | 7.88 | 0.743 |
| our rate × the minutes he **actually** played | 4.54 | 0.909 |

Even with a perfect points-per-minute rate, most of the error is minutes.
**The DFS edge is late news:** a bench player promoted to the starting five
plays about 8.6 more minutes, while his DK salary was set the day before.

**Fitting criterion: squared error.** DFS needs the *mean* of a player's
minutes, because expected points = expected minutes × rate. Fitting on absolute
error pulls projections toward medians. Bench minutes are lopsided (a coach's
DNP is a 0), so median-chasing systematically shortchanged starters. All
constants below were fitted on squared error. Tables report mean absolute
error (MAE) and bias as well.

## Inputs

1. **DK draftables** for the slate: name, team, positions, salary, start time,
   and DK's status flag (`OUT` / `O` removes the player).
2. **NBA.com daily lineups JSON** for the date. For each game it lists the
   starting five, marked *Expected* and later *Confirmed* about 30 minutes
   before tip, and the inactive list, which also posts about 30 minutes before
   tip.
3. **Player history** (`data/nba_player_history.json`, built by
   `scripts/nba_history_publish.py`): every game the player was *active* for,
   oldest first, as `(minutes, started, dk_points)`. A coach's-decision DNP
   counts as 0 minutes; inactive games are absent. History runs across
   seasons, up to the last 82 games. Per player it also stores:
   - `cur_n`: how many of the newest real entries are this season;
   - `n_pre`: how many trailing entries are this preseason's games, scaled
     ×0.66 (added only for players with < 5 real games, and used for the points
     rate, not the minutes role);
   - `pick`: overall draft pick, 70 if undrafted;
   - `since_return`: for the team's next game, games played since coming back
     from a 5+ team-game absence (0 = the next game is the first one back;
     `None` if there was no such absence);
   - `season_games`, `team`, `pos`.
4. **Positions** from NBA.com, collapsed to the groups G, F and C (a "G-F"
   player belongs to both).

### Joining names (DK ↔ NBA.com)

1. Match on a normalised full name: accents folded, Jr./Sr./II/III dropped,
   letters and digits only.
2. If that fails, match on **(team, normalised last name, first initial)**, and
   only when exactly one player fits. DK's "Alexandre Sarr" is NBA.com's
   "Alex Sarr".
3. A name shared by two history players resolves only to the one on this DK
   team, never to a guess.

## Step 1: each player's role

### 1a. Weighted role with a last-season prior

Weights over his real (non-preseason) entries, newest last:

- **This season's games:** `DECAY ^ age`, with `age` = 0 for the newest game
  and `DECAY = 0.8`. That's roughly the last 5 games.
- **Earlier seasons' games:** together they form a **prior worth
  `PRIOR_KAPPA = 1.0` games**, spread flat over them
  (`PRIOR_DECAY = 1.0`), i.e. last season as a whole.

`base = Σ w·minutes / Σ w`, and `start_rate = Σ w·started / Σ w`.

On night one the role is last season's average. A few games in, it's mostly
this season.

Why this form: a fixed per-game discount on last season's games had to be
small to stay accurate in-season, which left early-season roles reading only
the last week of last season (rest days, tanking). Treating last season as one
prior with a fixed weight removes that trade-off. On 2025-26: κ = 0.5 / 1 / 2
→ MAE 5.24 / 5.26 / 5.33 overall and 5.38 / 5.30 / 5.29 in opening week.

### 1b. Starting role and bench role

With the same weights:

- `ns`, `nb` = weighted counts of starts and bench games.
- `ms_raw`, `mb_raw` = weighted mean minutes in starts and in bench games.

Each side is shrunk toward the other side ± a gap:

```
ms = (ns·ms_raw + K·(mb_raw + GAP  if nb > 0 else base + GAP·(1 − start_rate))) / (ns + K)
mb = (nb·mb_raw + K·(ms_raw − GAP  if ns > 0 else base − GAP·start_rate))       / (nb + K)
K = SPLIT_K = 0.5,  GAP = SPLIT_GAP = 12 minutes
```

**Once the starting five is announced**, a starter's role is `ms` and a bench
player's is `mb`. This replaces v1's hand-set promotion lift: a bench player who
starts tonight gets his starting role, which with no starts of his own is his
bench role + 12.

### 1c. Rookies: draft-pick prior

With fewer than `ROOKIE_GAMES = 5` NBA games:

```
prior = 22.19 − 4.28 · ln(pick)              # pick 1: 22.2, pick 10: 12.3, pick 30: 7.6, undrafted (70): 4.0
role  = (n·own_mean + ROOKIE_K·prior) / (n + ROOKIE_K)        # ROOKIE_K = 0.5, n = his NBA games
```

Fitted on 611 first games of 2024-25 players with < 5 NBA games. Preseason
minutes add nothing on top (−0.003 per minute), so they don't enter the role.

There's also the live rule for a player with < 5 real games and preseason
games: his role is the mean of real + preseason ×0.66. It still applies for
players the rookie rule doesn't cover.

### 1d. Return ramp

On the first, second and third game back from a 5+ team-game absence, multiply
the role by `RAMP = (0.8, 0.9, 1.0)`. Without it, the first game back ran +3.0
minutes high; with it, −0.6.

### 1e. Is the coach playing him right now? (critique cycle 2)

Both rules apply after the split (1b) and before the return ramp (1d).

**Recency blend.** A player who isn't in tonight's announced five (or, before the
five is announced, has start rate < 0.5) and whose start rate is < 0.8 moves a
quarter of the way to his last game's minutes, with a coach's-decision DNP
counting as 0:

```
role = role + 0.25 · (last_game_minutes − role)
```

**Dropped regular starter.** A regular starter (start rate ≥ 0.8) who is active
but not in tonight's announced five gets 0.2 × his role. In the data such players
play 0 minutes in 60–67% of cases.

**Guards.** Both rules need at least 1 game this season (3 for the drop) and no
return from an absence. Otherwise the "last game" is last season's finale or
weeks old.

**Fitting.** λ = 0.25 and q = 0.2 were chosen on 2024-25 (grid 0.1–0.3 × 0–0.5,
rank-gap coefficients refitted for each). The guards are a judgment call:
2024-25 alone slightly prefers no guards (RMSE 6.9355 vs 6.9439), but without
them 2025-26 opening week goes 5.01 → 5.18 and 2026-27's night one goes 6.98 →
8.54 (rotation players cut to 4 minutes on last season's finale).

## Step 2: who dresses tonight

- **Inactive:** on NBA.com's inactive list, or flagged OUT by DK.
- **Before NBA.com posts the inactive list:** a team dresses 13 but DK lists
  about 20. Keep each team's 13 largest roles (step-1 base), with NBA.com's
  announced starters always kept first, and treat the rest as not dressing.

## Step 3: close the team's gap to 240 by role rank (critique cycle 1)

When at least 8 players are active:

1. **The gap:** `G = 240 − Σ role` over the active players. It's positive when the
   team is short (players missing), negative when roles overflow.
2. **Rank and move:** rank the active players by role (ranks 1–13 each get a
   cell; 14 and below share the last one). The player at rank k moves
   `a(k, G > 0) + b(k, G > 0) × G`.
3. **Close to 240:** share whatever is left by the slopes `b`, clip to 0–48, and
   re-share what clipping moved.

The coefficients (`edge/nba_minutes_gap.py`, generated by `scripts/nba_minutes_lab.py
cycle1 --write`) are least-squares fits of `(actual − role) = a + b·G` in each
cell, on 2024-25 (about 32,500 player-games), separately for the two information
states (starters announced or not).

What the data says: **stars barely move** (rank-1 slope about 0.02), and the
8th–12th men absorb most of any gap. When a starter sits, the rotation shifts
down a rank, and the biggest gains go to the players around the rotation
cutoff.

This replaced v2's two steps:

- "add back every inactive regular's minutes, then share them by role";
- "share 240 in proportion to role".

v2's version double-counted absences. Most inactive regulars had been out for
days, so their absence was already inside teammates' recent roles: active roles
already summed to about 240, yet step 3 added 49–56 "missing" minutes per
team-game. Step 4 then squeezed about 18% back out, mostly from starters.

**Fewer than 8 active** (preseason-style files): inactive regulars' minutes are
shared by `max(role, 1) ^ 0.25 × (1 + 0.25 × position share)`, with no 240 step.

## Step 4: DK salary (off)

v2 nudged minutes by DK salary: `−2.22 + 1.031 × minutes + 0.365 × salary($k)`.
It helped v2 (leave one night out, 11 nights: MAE 5.141 → 4.973) only because
v2's 240 step squeezed stars. With the rank-gap allocation it adds nothing (MAE
4.830 → 4.834), so `SALARY_NUDGE = None`.

## Step 5: "usual" minutes and the news delta

Run steps 1–3 a second time, with no starting five announced and no inactive
news. The result is `usual` minutes; `tonight − usual` is what the news
changed. The app's "Promoted / changed only" filter is built from it.

## Evaluation

### Protocol

`scripts/nba_minutes_lab.py` replays every night of 2024-25 and 2025-26 (plus
2026-27 so far) the way the live build sees it:

- histories carry across seasons (2023-24 on), from stats.nba.com game logs;
- NBA.com lineups give starters and inactives;
- draft history gives picks;
- every active player is scored, including rookies, opening week and returns
  from absence.

The older `scripts/nba_minutes_backtest.py` restarts history each season and
scores only players with 5+ games that season, so it never sees those cases.
Its numbers are reported too, for continuity.

Two information states are scored:

- **confirmed:** the starting fives and inactive lists are known (about 30
  minutes before tip);
- **inactives:** inactive lists only.

### Held out on 2025-26 (32,224 active player-games): MAE (bias) in minutes

| slice | v1, confirmed | v2, confirmed | cycle 1, confirmed | **current, confirmed** | v1, inactives | v2, inactives | **current, inactives** |
|---|---|---|---|---|---|---|---|
| RMSE, all | 7.76 | 6.81 | 6.69 | **6.59** | 8.15 | 7.65 | **7.40** |
| all | 5.74 (−0.1) | 5.26 (−0.1) | 5.03 (−0.1) | **4.93 (−0.1)** | 5.97 | 5.78 | **5.41** |
| opening week (team games 1–5) | 6.21 | 5.30 | 5.06 | **5.01** | 6.54 | 5.41 | **5.05** |
| rookies / no NBA history | 6.30 (−0.2) | 5.46 (−0.6) | 5.15 (−0.9) | **5.09 (−0.8)** | 6.75 | 5.99 | **5.51** |
| lottery rookies (75) | 7.32 (−3.7) | 5.17 (−1.4) | 4.90 (−1.0) | **4.89 (−1.0)** | 8.61 | 6.10 | **5.35** |
| new team, first 5 games | 7.11 | 6.21 | 6.00 | **5.91** | 7.50 | 6.27 | **5.95** |
| first game back from 5+ out | 6.59 (+3.0) | 5.99 (−0.6) | 5.98 (−0.9) | **5.97 (−0.9)** | 6.96 | 6.34 | **6.32** |
| teammates of a scratch | 5.80 | 5.34 | 5.11 | **5.00** | 6.05 | 5.88 | **5.52** |
| promoted starters | 5.71 (+0.4) | 5.20 (+1.3) | 5.13 (+0.9) | **5.12 (+0.9)** | — | — | — |
| high role (28+) | 5.22 (+0.2) | 4.63 (−0.5) | 4.44 (+0.2) | **4.33 (−0.0)** | 5.31 | 5.10 | **4.73** |
| starters, roles > 1.15 × 240 | 5.65 (−4.1) | 4.68 (−2.2) | 4.26 (−0.0) | **4.27 (+0.2)** | 5.77 (−4.4) | 5.70 (−4.4) | **4.76 (−1.9)** |

"current" = v2 + critique cycle 1 (rank-gap allocation, salary nudge off) + cycle 2
(recency blend, dropped regular starter); NBA_MINUTES_CRITIQUE_LOG.md has every cycle.

**Legacy protocol** (2025-26 held out, 29,311 player-games, players with 5+
games that season):

| | v1 | v2 | cycle 1 | current |
|---|---|---|---|---|
| all, confirmed | 5.601 | 5.123 | 4.929 | **4.845** |
| teammates of a scratch, confirmed | 5.681 | 5.216 | 5.026 | **4.928** |
| promoted starters, confirmed | 5.653 (+0.9) | 5.346 (+1.5) | 5.227 (+1.1) | **5.224 (+1.1)** |
| all, inactives only | 5.831 | 5.624 | 5.394 | **5.332** |

### What each change bought (2025-26, confirmed, RMSE / MAE)

| step | RMSE / MAE |
|---|---|
| v1 | 7.76 / 5.74 |
| weighted role + last-season prior | 7.38 / 5.50 |
| + start/bench split | 6.87 / 5.19 |
| + 240 power share | 6.84 / 5.25 |
| + return ramp and rookie draft prior | 6.83 / 5.24 |
| + redistribution refit (= v2) | 6.81 / 5.26 |
| + critique cycle 1: rank-gap allocation, salary off | 6.69 / 5.03 |
| + critique cycle 2: recency blend, dropped regular starter | 6.59 / 4.93 |

The power share is a squared-error trade: slightly better RMSE, slightly worse
MAE.

### Tried and not kept (no held-out gain on 2024-25)

- back-to-back adjustment;
- blowout adjustment from each team's average margin to date;
- a starter tilt that grows with overflow, with or without a threshold;
- splitting the 240 excess between a starter pool and a bench pool;
- a fitted per-game discount on last-season games (it read last season's final
  week at season start).

## Known weaknesses (open as of 2026-10-21, after critique cycle 2)

1. **This season's opening night still runs stars low:** −4.7 minutes for roles
   28+ (it was −8.0 before the critique loop). Only 3 games, 103 players.
2. **The rank-gap allocation is position-blind.** v2's position weighting of
   missing minutes went with the old step 3.
3. **Promoted starters run about +0.9 minutes high.**
4. **The dropped-starter factor drifts between seasons.** The share playing 0 went
   from 67% to 60%, so q = 0.2 over-corrects 2025-26 (bias −1.6). Live, DK's OUT
   flags remove some of these players first.
5. **Critic #2's runners-up:**
   - days since the player's last game (a bigger signal than the back-to-back flag);
   - the lab never replays the live state before the inactive list posts (expected
     starters, DK OUT flags, the 13-man cut; 26% of 2025-26 team-games dressed
     14–15 players);
   - end of season: April MSE is 60 against 45 for the season, and standings are
     worth something.
6. **Headroom:** a gradient-boosted residual model still reaches RMSE about 6.41
   on 2025-26.
7. **Blowouts and overtime** aren't projected.

## Pseudocode

```python
def role(history, cur_n, n_pre, decay=0.8, kappa=1.0, k=0.5, gap=12.0):
    real = history[:len(history) - n_pre][-82:]           # newest last
    n, cur = len(real), (len(real) if cur_n is None else min(cur_n, len(real)))
    w = [0.0] * n
    for j in range(n - cur, n):
        w[j] = decay ** (n - 1 - j)                       # this season
    for j in range(n - cur):
        w[j] = kappa / (n - cur)                          # last season, flat, worth kappa games
    base = wmean(minutes, w); start_rate = wmean(started, w)
    ns, nb = sum(w where started), sum(w where not started)
    ms_raw, mb_raw = wmean(minutes | started), wmean(minutes | bench)
    ms = (ns*ms_raw + k*(mb_raw + gap if nb else base + gap*(1 - start_rate))) / (ns + k)
    mb = (nb*mb_raw + k*(ms_raw - gap if ns else base - gap*start_rate)) / (nb + k)
    return base, start_rate, ms, mb, ns + nb

def project_team(players, starters_announced, inactive_list_posted):
    for p in players:
        base, sr, ms, mb, nw = role(p.history, p.cur_n, p.n_pre)
        if p.real_games < 5:                                  # rookie / no history
            prior = max(0, 22.19 - 4.28 * ln(p.pick or 70))
            base = (p.real_games * p.own_mean + 0.5 * prior) / (p.real_games + 0.5)
        if starters_announced and nw > 0:
            base = ms if p.starts_tonight else mb
        bench = (not p.starts_tonight) if starters_announced else sr < 0.5
        if bench and sr < 0.8 and p.games_this_season >= 1 and p.since_return is None and not rookie:
            base += 0.25 * (p.last_game_minutes - base)          # cycle 2: recency
        if starters_announced and not p.starts_tonight and sr >= 0.8 and p.games_this_season >= 3 \
                and p.since_return is None:
            base *= 0.2                                          # cycle 2: dropped regular starter
        if p.since_return in (0, 1, 2):
            base *= (0.8, 0.9, 1.0)[p.since_return]
        p.role, p.starting = base, (float(p.starts_tonight) if starters_announced else sr)
    if not inactive_list_posted:
        healthy = sorted((p for p in players if not p.out),
                         key=lambda p: (not p.announced_starter, -p.role))
        for p in healthy[13:]:
            p.out = True                                      # not dressing
    active = [p for p in players if not p.out]
    coef = GAP_COEF["confirmed" if starters_announced else "inactives"]
    G = 240 - sum(p.role for p in active)
    for rank, p in enumerate(sorted(active, key=lambda p: -p.role)):
        a, b = coef[(min(rank, 13), G > 0)]
        p.min, p.slope = p.role + a + b * G, max(b, 1e-3)
    close_to_240_by_slopes_clip_0_48(active)                  # share the rest by p.slope
    return {p.name: p.min for p in active}
```

## Where to look in the repo

| file | contents |
|---|---|
| `edge/nba_minutes.py` | `role_v2`, `project`, `rank_gap`, `games_since_return`, every constant with its provenance; `project_v1` |
| `edge/nba_minutes_gap.py` | the rank-gap coefficients (generated; do not edit) |
| `NBA_MINUTES_CRITIQUE_LOG.md` | the critique loop: each cycle's hole, fix and numbers |
| `edge/dfs_run_nba.py` (`build_board`) | joins, who dresses, the 13-man rule, usual vs tonight |
| `scripts/nba_history_publish.py` | the history file and its `cur_n` / `n_pre` / `pick` / `since_return` fields |
| `scripts/nba_minutes_lab.py` | `diagnose`, `ablate`, `fit`, `salary`: the live-faithful backtest |
| `scripts/nba_minutes_backtest.py` | the legacy protocol |
| `NBA_STATUS.md` | results, open items |
