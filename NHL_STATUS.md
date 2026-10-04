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

## 6b. Second night 2026-09-29: GPP and cash (main slate re-entry)

Exports `196218438` (GPP, 892 entries) and `196218453` (cash, 209 entries).
`scripts/nhl_calibration.py --fit-ownership`:

| | contest | n | bias | MAE | rank corr | below floor (25) | above 95th (5) |
|---|---|---|---|---|---|---|---|
| skaters | GPP | 16 | +1.34 | 5.73 | −0.226 | 31% | **12%** |
| skaters | cash | 14 | +0.96 | 5.98 | −0.560 | 29% | **14%** |

**Ownership fits:** only 17–14 logged skaters on the board per contest (small
sample). Prior MAE 5.57–5.61; best fit:
- **GPP:** VALUE 0.0 / SALARY 0.7 / PP1 0.8 → MAE 5.30
- **Cash:** VALUE 0.0 / SALARY 1.0 / PP1 0.0 → MAE 5.26

Current model (VALUE 0.8 / SALARY 0.5 / PP1 0.4) runs middle. Pattern is
consistent with prior contests: ownership tracks salary + power-play strength.
Hold current weights until 4–6 contests establish the field's stable shape.

## 6c. Third night 2026-10-04: GPP and cash (main slate)

Exports `196302148` (GPP) and `196302159` (cash).
`scripts/nhl_calibration.py --fit-ownership`:

| | contest | n | bias | MAE | rank corr | below floor (25) | above 95th (5) |
|---|---|---|---|---|---|---|---|
| skaters | GPP | 86 | +1.03 | 4.45 | +0.307 | 31% | 6% |
| skaters | cash | 47 | +0.18 | 4.87 | +0.313 | 25% | 9% |

86 logged players matched GPP (strong coverage); ownership well-fit. Prior MAE 4.89–5.25; best fit:
- **GPP:** VALUE 0.3 / SALARY 0.2 / PP1 0.8 → MAE 4.73 (−0.16 gain)
- **Cash:** VALUE 0.6 / SALARY 0.3 / PP1 0.8 → MAE 5.17 (−0.08 gain)

**Buzz test:** Tested on 10/4 contests. Buzz increased ownership MAE by 1.6–2.0 pts — no signal. Defer until pattern emerges across more slates.

## 7. Nightly workflow

```bash
python3 scripts/dfs_lineups_nhl.py --list          # slates
python3 scripts/dfs_lineups_nhl.py                 # main slate, cash + GPP (logs the build)
python3 scripts/dfs_lineups_nhl.py --gpp 5         # five different GPP lineups
# ~1-2h before puck drop, when goalies are confirmed: rebuild (late swap is allowed)
# after the slate: drop exports into data/, then per contest:
python3 scripts/nhl_calibration.py data/contest-standings-<id>.csv --fit-ownership
```

Timers (deploy/, systemd --user): `odds-collect-dfs-nhl` / `odds-publish-dfs-nhl`
(props), `nhl-lines-publish` (DailyFaceoff snapshot for the cloud app),
`buzz-nhl-publish` (YouTube buzz), plus NHL in `draftables-publish`.
Refit: `python3 scripts/nhl_fit.py --download --sim`.
