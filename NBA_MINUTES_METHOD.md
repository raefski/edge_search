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

## Step 2: who dresses tonight

- **Inactive:** on NBA.com's inactive list, or flagged OUT by DK.
- **Before NBA.com posts the inactive list:** a team dresses 13 but DK lists
  about 20. Keep each team's 13 largest roles (step-1 base), with NBA.com's
  announced starters always kept first, and treat the rest as not dressing.

## Step 3: redistribute the minutes of missing regulars

- **Missing minutes:** `M` = the sum of roles of inactive players with
  role ≥ `REGULAR_MIN` (10).
- **Lost by position:** record how much of `M` each position group lost.
- **Share out:**

  ```
  w = max(role, 1) ^ 0.25 × (1 + 0.25 × min(1, pos_share))     # ROLE_POW_V2, POS_W_V2
  minutes = role + M × w / Σw          (cap 48)
  ```

## Step 4: hold the team to 240

When at least 8 players are active, share 240 among them by:

```
weight = minutes ^ ALLOC_POW × (1 + STARTER_TILT × starting)    # ALLOC_POW = 1.0, STARTER_TILT = 0.05
```

- `starting` is 1/0 once the five is announced, otherwise his start rate.
- It applies both when the roles overflow 240 and when they fall short.
- Anyone past 48 is capped and the rest re-shared.

## Step 5: DK salary nudge

```
minutes = −2.22 + 1.031 × minutes + 0.365 × salary($k)      (clipped to 0–48)
```

That's about +1.4 minutes at $10,000 and −1.1 at $3,000. It was fitted leaving
one night out across the 11 nights with cached DK salaries (2025-26 contest
nights plus 10/20/26, 1,517 player-games): MAE 5.141 → 4.973, RMSE 6.546 →
6.491. On top of v1 it added nothing. The sample is small; refit as nights
accrue.

## Step 6: "usual" minutes and the news delta

Run steps 1–5 a second time, with no starting five announced and no inactive
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

| slice | v1, confirmed | **v2, confirmed** | v1, inactives | **v2, inactives** |
|---|---|---|---|---|
| RMSE, all | 7.76 | **6.81** | 8.15 | **7.65** |
| all | 5.74 (−0.1) | **5.26 (−0.1)** | 5.97 | **5.78** |
| opening week (team games 1–5) | 6.21 | **5.30** | 6.54 | **5.41** |
| rookies / no NBA history | 6.30 (−0.2) | **5.46 (−0.6)** | 6.75 | **5.99** |
| lottery rookies (75) | 7.32 (−3.7) | **5.17 (−1.4)** | 8.61 | **6.10** |
| new team, first 5 games | 7.11 | **6.21** | 7.50 | **6.27** |
| first game back from 5+ out | 6.59 (+3.0) | **5.99 (−0.6)** | 6.96 | **6.34** |
| teammates of a scratch | 5.80 | **5.34** | 6.05 | **5.88** |
| promoted starters | 5.71 (+0.4) | **5.20 (+1.3)** | — | — |
| starters, roles > 1.15 × 240 | 5.65 (−4.1) | **4.68 (−2.2)** | 5.77 (−4.4) | 5.70 (−4.4) |

**Legacy protocol** (2025-26 held out, 29,311 player-games, players with 5+
games that season):

| | v1 | v2 |
|---|---|---|
| all, confirmed | 5.601 | **5.123** |
| teammates of a scratch, confirmed | 5.681 | **5.216** |
| promoted starters, confirmed | 5.653 (+0.9) | **5.346 (+1.5)** |
| all, inactives only | 5.831 | **5.624** |

### What each change bought (2025-26, confirmed, RMSE / MAE)

| step | RMSE / MAE |
|---|---|
| v1 | 7.76 / 5.74 |
| weighted role + last-season prior | 7.38 / 5.50 |
| + start/bench split | 6.87 / 5.19 |
| + 240 power share | 6.84 / 5.25 |
| + return ramp and rookie draft prior | 6.83 / 5.24 |
| + redistribution refit (= v2) | 6.81 / 5.26 |

The power share is a squared-error trade: slightly better RMSE, slightly worse
MAE.

### Tried and not kept (no held-out gain on 2024-25)

- back-to-back adjustment;
- blowout adjustment from each team's average margin to date;
- a starter tilt that grows with overflow, with or without a threshold;
- splitting the 240 excess between a starter pool and a bench pool;
- a fitted per-game discount on last-season games (it read last season's final
  week at season start).

## Known weaknesses (open as of 2026-10-21)

1. **This season's opening week still squeezes stars.** After an unusually busy
   offseason, the 13 kept roles sum to 267–321 minutes against 240.
   - **Opening night (10/20/26, 3 games, 103 players):** players with a role of
     28+ ran about 8 minutes low under both v1 and v2. v2's MAE there is 6.73 vs
     v1's 6.52, which is noise-level at that n.
   - **The overflow tilt that fixes those 3 games doesn't help 2024-25 or
     2025-26**, so it isn't shipped. Recheck once more 2026-27 nights are in.
2. **Promoted starters run about +1.3 minutes high.** The split's 12-minute
   gap suits the average bench-to-starter jump; MAE still improved.
3. **The salary nudge rests on 11 nights.**
4. **New-team players** have the highest error of any common slice (6.2), with
   no bias to correct.
5. **Blowouts and overtime** aren't projected (no historical lines), only
   simulated through role correlations in `edge/dfs_nba_theory.py`.

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
        if p.since_return in (0, 1, 2):
            base *= (0.8, 0.9, 1.0)[p.since_return]
        p.role, p.starting = base, (float(p.starts_tonight) if starters_announced else sr)
    if not inactive_list_posted:
        healthy = sorted((p for p in players if not p.out),
                         key=lambda p: (not p.announced_starter, -p.role))
        for p in healthy[13:]:
            p.out = True                                      # not dressing
    missing = [p for p in players if p.out and p.role >= 10]
    M = sum(p.role for p in missing)
    lost = per_group_sum(missing)
    active = [p for p in players if not p.out]
    for p in active:
        pos_share = sum(lost[g] for g in p.groups) / M if M else 0
        p.w = max(p.role, 1) ** 0.25 * (1 + 0.25 * min(1, pos_share))
    W = sum(p.w for p in active)
    for p in active:
        p.min = min(48, p.role + M * p.w / W)
    if len(active) >= 8:
        weight = {p: p.min ** 1.0 * (1 + 0.05 * p.starting) for p in active}
        share_240_with_cap_48(active, weight)
    for p in active:
        p.min = clip(-2.22 + 1.031 * p.min + 0.365 * p.salary / 1000, 0, 48)
    return {p.name: p.min for p in active}
```

## Where to look in the repo

| file | contents |
|---|---|
| `edge/nba_minutes.py` | `role_v2`, `project`, `games_since_return`, every constant with its provenance; `project_v1` |
| `edge/dfs_run_nba.py` (`build_board`) | joins, who dresses, the 13-man rule, usual vs tonight |
| `scripts/nba_history_publish.py` | the history file and its `cur_n` / `n_pre` / `pick` / `since_return` fields |
| `scripts/nba_minutes_lab.py` | `diagnose`, `ablate`, `fit`, `salary`: the live-faithful backtest |
| `scripts/nba_minutes_backtest.py` | the legacy protocol |
| `NBA_STATUS.md` | results, open items |
