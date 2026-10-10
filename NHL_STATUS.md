# DK NHL DFS — status, methodology, and what is not validated

Start here for anything NHL. Built 2026-09-28, the night before the 2026-27
opener (5 games on 9/29, DK main slate 4 games at 7:00 PM ET). Everything below
was measured on this machine that day unless it says otherwise.

## 1. The contest (DK Classic, gametype 125, read live)

| | |
|---|---|
| Roster | C, C, W, W, W, D, D, G, UTIL (UTIL = any skater) |
| Cap | $50,000 |
| Minimums | players from **3+ teams**, **2+ games** |
| Late swap | **allowed** (unlike NASCAR) |
| Positions on the board | C, LW, RW, D, G — both wings fill W |

## 2. Scoring — validated against DraftKings' own FPPG

skater: goal 8.5 · assist 5 · shot on goal 1.5 · blocked shot 1.3 · SH point +2 ·
shootout goal +1.5 · bonuses +3 each for hat trick, 5+ shots, 3+ blocks, 3+ points.
goalie: win 6 · save 0.7 · goal against −3.5 · shutout +4 · OT loss +2 · 35+ saves +3.

`scripts/nhl_fit.py --scoring --gid <live group>` recomputes every player's 2025-26
DK points per game from the NHL's 1,311 box scores and compares to DK's FPPG
(draftStatAttributes id 341, added to edge/dfs.py):

| | n | bias | MAE | within 0.25 |
|---|---|---|---|---|
| skaters (20+ GP) | 160 | −0.025 | 0.177 | 86% |
| goalies, table above | 17 | −0.23 | 0.385 | |
| goalies, OT loss 1 | 17 | −0.36 | 0.456 | |
| goalies, save 0.8 / GA −3 | 17 | +3.57 | 3.57 | |

The residual runs slightly low by construction: box scores carry no short-handed
points or shootout goals. The worst skater misses are a name collision (two Elias
Petterssons) and players traded mid-season.

## 3. Data (all free)

| source | gives |
|---|---|
| DraftKings sportsbook, categories 1189/1675/1676/1064 | Shots O/U, Points O/U, Assists O/U, Saves O/U — verified live, ~110 players a night |
| game lines (DK/FD/Fanatics) | moneyline + total → each team's implied goals |
| NHL API (api-web.nhle.com, api.nhle.com/stats) | schedule, box scores, season reports (blocked shots) |
| DailyFaceoff | forward lines F1–F4, D pairs, PP1/PP2, injuries, starting goalies + confirmation |
| YouTube (packages/transcripts) | tonight's NHL DFS videos → player mentions (scripts/buzz_nhl.py) |

Only DraftKings posts NHL player props through our scrapers today; FanDuel and
Fanatics returned none on 2026-09-28.

## 4. The model: a game simulator (edge/nhl_sim.py)

Hockey DFS is correlation: a goal pays a scorer 8.5 and up to two linemates 5 each.
So whole games are sampled — team goals from the market, a scorer per goal by
goal rate, assisters with a strong pull toward linemates and PP unit-mates
(reweighted so each skater's expected assists still match his prop), shots with
a shared team factor (every goal is a shot), blocks scaled by the opponent's
shot volume, and the starting goalie's saves from his opponents' actual shots.
Line stacks, PP stacks and goalie-vs-opposing-skater anti-correlation come out
of the simulation; the tests check linemates correlate more than non-linemates
and a goalie correlates negatively with the skaters he faces.

**Fitted on 2025-26 (1,311 games):** goals per team-game 3.128; assists per goal
1.689; 24.9% of games reach OT/SO (independent Poisson says 16.8% — so a
one-goal game is pulled level 25% of the time) and 63.5% of those end on an OT
goal; empty-net goals 0.378/game; shots NB shape 15.2, blocks 9.8, team shot
factor ~70.

**Means:** Poisson inversion of the two-sided lines (P(shots ≥ 4) at the posted
price → mean). Goals = points − assists. No prop → last season per game, scaled
by tonight's implied goals over 3.128, flagged `*` on the board. Blocks have no
market anywhere and always come from last season. The board is who is DRESSING:
DailyFaceoff's projected lineup plus anyone a book priced, and only the projected
starting goalie — a priced healthy scratch or IR player is dropped (and DK's
own OUT/IR status is honoured).

## 5. The two theories (edge/dfs_nhl_theory.py, edge/dfs_opt_nhl.py)

- **cash** = 25th percentile of the lineup's own simulated total. No stack rule.
- **gpp** = 95th percentile (not NASCAR's 90th: a line stack's upside is a JOINT
  tail). Shape rule: two team stacks of 3+ skaters (the 3-3-2 / 4-3-1 builds),
  max 5 from one team — left alone it put six Oilers in one lineup.
- Both: no skater facing your goalie; DK's 3-team / 2-game minimums.

## 6. What is NOT validated

| thing | status |
|---|---|
| **Ownership** | a PRIOR (value + salary + PP1). Fit on the first export: `scripts/nhl_calibration.py <export> --fit-ownership`, one contest at a time. |
| **Linemate affinity** (4x line, 3x PP) | a PRIOR — box scores carry no lines. Needs a season of DailyFaceoff lines joined to play-by-play. |
| **Buzz → ownership** | collected nightly from day one, shown on the board, NOT in the model until exports can fit it (the NFL needed 3 slates). |
| D pair ↔ forward line on-ice correlation | not modelled (only same-label lines and PP units). |
| Short-handed points, shootout goals | not simulated (small; see §2). |
| Depth players without props | last season's rates, not current role. |

## 6a. First contest: opening night 2026-09-29 (main, 229-entry cash)

Export `195958188`, graded against the build logged at 6:35 PM ET (after the
skater-team fix below). `scripts/nhl_calibration.py --fit-ownership`:

| | n | bias | MAE | rank corr | below floor (25) | above 95th (5) |
|---|---|---|---|---|---|---|
| skaters | 113 | +0.47 | 5.14 | +0.23 | 26% | **11%** |
| goalies | 8 | −0.34 | 7.18 | +0.26 | | |

- The floor is calibrated; **the upper tail is too thin** — 11% of skaters
  beat their 95th percentile against 5% expected. One slate, so not tuned yet;
  the first suspects are the linemate-affinity prior and per-player goal
  variance. Watch it on the next exports.
- Props-priced skaters ran +0.87 high, last-season estimates +0.11.
- Ownership (cash field): prior MAE 4.28, rank +0.67; best fit on this
  contest VALUE 1.5 / SALARY 0.8 / PP1 0.8 (MAE 3.84). NOT shipped — the model's
  ownership serves GPP, and cash concentrates differently.
- YouTube buzz (77 players collected) showed no relationship with cash
  ownership (rank corr −0.00, −0.21 against the prior's residual). One cash
  slate; revisit with GPP exports.
- The corrected cash lineup would have scored 170.8 and **won the contest**
  (1st of 229; the winner had 167.4) — Bouchard 60.4 did much of it.
- **Bug found and fixed on the night:** DK counts the 3-team minimum over
  SKATERS only. The first GPP lineup (EDM + BOS skaters, TOR goalie) was
  rejected by DraftKings; `_valid` counted the goalie's team.

## 6b. Seven nights graded (2026-09-29 to 10-07)

**Correction (2026-10-08).** Earlier versions of this section graded every
export after 9/29 against the 9/29 board: phone builds never reach the local
log, and `nhl_calibration.py` silently picks the best-overlapping logged
slate, which is any night with the same players. Those numbers are void. The
missing nights were rebuilt with `scripts/nhl_rebuild.py` (DK draftables and
the DailyFaceoff snapshot from git, odds from the store's last pre-lock scan;
logged as `source=rebuild`). Checked against the live-logged 10/07 build: 112
of 112 players, projection mean |diff| 0.12, ownership 0.73. Every export now
matches its own slate exactly (all field players on the board) and its summed
ownership is ~880% of a 900% roster.

| night | gid | games | contest | skaters | bias | MAE | rho | below p25 | above p95 |
|---|---|---|---|---|---|---|---|---|---|
| 9/29 | 153977 | 4 | cash | 113 | +0.47 | 5.14 | +0.23 | 26% | 11% |
| 9/30 | 154305 | 3 | gpp / cash | 104 / 86 | −0.26 / +0.12 | 5.42 / 5.40 | +0.08 / +0.09 | 30% | 17% / 13% |
| 10/1 | 154311 | 8 | gpp / cash | 234 / 89 | −0.58 / −0.42 | 5.16 / 5.18 | +0.34 / +0.42 | 18% / 15% | 14% / 10% |
| 10/3 | 154332 | 13 | gpp / cash | 354 / 173 | +0.48 / +0.57 | 4.76 / 4.82 | +0.37 / +0.44 | 24% | 9% |
| 10/4 | 154342 | 4 | gpp / cash | 121 / 69 | +0.11 / +0.25 | 4.43 / 4.66 | +0.36 / +0.34 | 20% / 19% | 6% |
| 10/6 | 154356 | 9 | gpp / cash | 265 / 187 | −0.46 / −0.90 | 5.04 / 5.72 | +0.38 / +0.34 | 18% | 12% / 14% |
| 10/7 | 154690 | 3 | gpp / cash | 99 / 63 | +0.53 / +0.19 | 4.68 / 5.34 | +0.45 / +0.46 | 26% / 29% | 10% / 13% |
| 10/9 | 154704 | 4 | gpp / cash | 116 / 91 | +1.50 / +1.43 | 4.68 / 4.84 | +0.26 / +0.14 | 23% | 6% / 5% |

- **The mean is unbiased** (−0.9 to +0.6 every night).
- **The upper tail is too thin, every night**: 6–17% of skaters beat their
  simulated 95th percentile against 5%. Seven nights, one direction. This is
  the simulator's main open problem and it matters most for GPP, which
  selects on the 95th. Suspects unchanged: linemate-affinity prior,
  per-player goal variance.

**Ownership by slate size** (top owned / sum of top three, field vs model):

| contest | games | field top | model top | field top-3 | model top-3 |
|---|---|---|---|---|---|
| cash | 3, 3, 4, 4 | 76–88% | 48–60% | 185–194% | 102–131% |
| cash | 8, 9, 13 | 84%, 40%, 68% | 30%, 24%, 10% | 223%, 97%, 159% | 74%, 57%, 29% |
| gpp | 3, 3, 4 | 60%, 40%, 44% | 51%, 48%, 51% | 136%, 107%, 104% | 131%, 118%, 102% |
| gpp | 8, 9, 13 | 39%, 19%, 29% | 30%, 24%, 10% | 95%, 53%, 76% | 74%, 57%, 29% |

- **Cash concentrates hard at every slate size**, not just small ones: the
  top player is 68–88% on six of seven nights. The field model is a GPP model
  and cash ownership drives nothing (the cash objective ignores it), so this is
  a display problem until a separate cash model is wanted.
- **GPP small slates are already about right; big slates are too flat.** The
  NFL's fix (a cap that rises as games fall) is the wrong shape for hockey.
- Per-contest best fits all want **PP1 0.6–0.8** (shipped 0.4); VALUE and
  SALARY move around. Next: one pooled GPP fit across the seven nights with a
  slate-size term, scored out of sample.
- Ownership changes no lineup today: `own_weight` is 0 in the app and the CLI.

**Buzz:** the earlier "no signal" runs used the wrong night's board too, so
they are void as well. Re-run `scripts/buzz_fit_nhl.py` on the rebuilt nights.

## 6c. Field-aware cash objective (built 2026-10-10, NOT the app default yet)

The floor objective maximises a cash lineup's own 25th percentile and ignores
the field -- the wrong question on a small slate, where 75-88% of a double-up
rosters the same top player and the cash line is "how the chalk did".
`edge/dfs_nhl_field.py` samples 300 field lineups from a CASH ownership model,
takes the score the field needs to cash in each simulation (top 44%), and
`dfs_opt_nhl.optimize(..., line=...)` maximises the (smoothed) chance of
clearing it.

Cash ownership, fitted on squared error over the eight graded double-ups:
VALUE 1.0, SALARY 0.5, PP1 0.8, cap 90 (RMSE 7.23 vs 7.55 for the GPP model).

`scripts/nhl_cash_field.py`, leave one night out, real DK points vs the real
cash line:

| | cashed | mean finish percentile | mean points |
|---|---|---|---|
| floor (shipped) | 3 / 8 | 51% | 105.5 |
| field-aware | 5 / 8 | 63% | 118.3 |

Field-aware finished higher on 7 of 8 nights (one-sided sign test p = 0.035).
But the model's OWN estimate of the gain is small -- P(clear) +1 to +5 points
per night -- so most of the realised +12 points is noise from two different
lineups on eight nights; read it as "not worse, probably better", not as a
measured 25-point cash-rate gain. The difference concentrates on small slates
(on the 10-game 10/10 slate the two lineups shared 7 of 9 players, 75% vs 76%).

## 7. Nightly workflow

```bash
python3 scripts/dfs_lineups_nhl.py --list          # slates
python3 scripts/dfs_lineups_nhl.py                 # main slate, cash + GPP (logs the build)
python3 scripts/dfs_lineups_nhl.py --gpp 5         # five different GPP lineups
# ~1-2h before puck drop, when goalies are confirmed: rebuild (late swap is allowed)
# after the slate: drop exports into data/. If the night was built only on the
# phone, it is not in the log -- rebuild it from pinned pre-lock inputs first:
python3 scripts/nhl_rebuild.py <gid>
python3 scripts/nhl_calibration.py data/contest-standings-<id>.csv --fit-ownership
# CHECK the "-> <date> draft group <gid>" line is the contest's own night.
```

Timers (deploy/, systemd --user): `odds-collect-dfs-nhl` / `odds-publish-dfs-nhl`
(props), `nhl-lines-publish` (DailyFaceoff snapshot for the cloud app),
`buzz-nhl-publish` (YouTube buzz), plus NHL in `draftables-publish`.
Refit: `python3 scripts/nhl_fit.py --download --sim`.
