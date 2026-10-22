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
market anywhere and always come from last season. Since 2026-10-22 every mean last
season sets (no-prop goals / assists / shots, everyone's blocks) is scaled by this
season's ice time over last season's (§6j). The board is who is DRESSING:
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
| **Ownership** | GPP: FITTED 10/17 on 12 GPPs (value 0.6 / salary 0.6 / PP1 0.6), see §6d. Cash field: its own fit (§6c). |
| **Linemate affinity** (4x line, 3x PP) | a PRIOR — box scores carry no lines. Needs a season of DailyFaceoff lines joined to play-by-play. |
| **Buzz → ownership** | collected nightly from day one, shown on the board, NOT in the model until exports can fit it (the NFL needed 3 slates). |
| D pair ↔ forward line on-ice correlation | not modelled (only same-label lines and PP units). |
| Short-handed points, shootout goals | not simulated (small; see §2). |
| Depth players without props | last season's rates, not current role. |

## 6a. First contest: opening night 2026-09-29 (main, 229-entry cash)

Export `195958188`, graded against the build logged at 6:35 PM ET (after the
skater-team fix below). `scripts/nhl_calibration.py --fit-ownership`:

| | n | bias | MAE | rank corr | below floor (25) | above 90th (10) |
|---|---|---|---|---|---|---|
| skaters | 113 | +0.47 | 5.14 | +0.23 | 26% | **11%** |
| goalies | 8 | −0.34 | 7.18 | +0.26 | | |

- The floor is calibrated; **the upper tail is too thin** — 11% of skaters
  beat their 95th percentile against 5% expected. One slate, so not tuned yet;
  **Corrected 2026-10-17:** the logged ceiling is the 90th percentile (edge/nhl_sim.summarise,
  since 9/28), so the target is 10%, not 5% -- the grader mislabelled it. On every live build
  through 10/17, 9.1% of skaters (123/1,350) and 9.3% of goalies beat it: the tail is calibrated.
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
  **Corrected 2026-10-17:** the logged ceiling is the 90th percentile (edge/nhl_sim.summarise,
  since 9/28), so the target is 10%, not 5% -- the grader mislabelled it. On every live build
  through 10/17, 9.1% of skaters (123/1,350) and 9.3% of goalies beat it: the tail is calibrated.

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

## 6c. Field-aware cash objective (the app's cash lineup since 2026-10-10)

The floor lineup is still built and logged next to it (`mode=cash_floor` in
`data/dfs_lineups_nhl_<date>.csv`) so the switch keeps being graded.

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

## 6d. GPP ownership fitted (2026-10-17)

12 GPP exports, 12 slates (9/30-10/17), each graded against its own draft
group's pre-lock log. Grid over value / salary / PP1 weights, leave one slate
out (fit on 11, score the 12th), on mean absolute error:

| | MAE | RMSE | rank corr |
|---|---|---|---|
| prior 0.8 / 0.5 / 0.4 | 3.13 | 5.45 | +0.68 |
| held-out fit | **2.86** | **4.76** | **+0.75** |

Better on 11 of 12 slates; 11 of 12 folds chose 0.6 / 0.6 / 0.6, which shipped.
**Forward check (2026-10-19):** on the 5 GPPs filed after the fit (10/17-10/18, fields
19-891, 3 slates), new vs old weights: MAE 3.51 vs 3.87, better on 5/5, rank up on 4/5.
GPP `own_weight` is still 0, so this changes the ownership and leverage shown,
not the lineups built.

Cash field (13 double-ups, same method, squared error as in §6c): shipped
(1.0, 0.5, 0.8) RMSE 7.77 vs held-out (0.8, 0.6, 0.8) 7.51, better on 9 of 13.
NOT shipped: cash ownership drives the field-aware cash lineup, and the gain is
small enough that it needs `scripts/nhl_cash_field.py` (8 nights hard-coded)
extended to all 13 nights first.

## 6e. Goalie points: what the market can and cannot predict (2026-10-17)

Adam asked whether the paid-tool approaches (SaberSim's game simulation,
FantasyLabs' Vegas/saves model, starter-news adjusters) would fix goalies. Our
build already does the first two (edge/nhl_sim.py: Poisson goals from the
moneyline + total, saves from simulated opponent shots rescaled to the DK saves
prop) and the third (DailyFaceoff starters + edge-monitor goalie-change alerts).
Tested on every graded goalie: 171 on 13 slates (9/29-10/17), box scores from
the NHL API, pre-lock saves lines and no-vig moneylines from data/odds.db
(165/171 had both). Leave one slate out:

| predictor of DK points | held-out MAE | corr |
|---|---|---|
| a constant (mean) | 8.10 | -- |
| our projection | 8.12 | +0.05 |
| moneyline P(win) | 8.09 | -0.02 |
| P(win) + saves line | 8.13 | -0.08 |

The market predicts the PARTS -- P(win) vs wins +0.33 (Brier 0.227 vs 0.25),
saves line vs saves +0.32 (ours +0.02) -- but they offset: a favourite faces
fewer shots, an underdog more, and goals against (sd 5.9 of a 9.9-point total)
is noise at this n. One extra save on the line is worth -0.14 points net. So
no median model, ours or a rebuilt "Vegas" one, beats a constant yet; NOT
shipped, no code changed.

What is worth watching:
- **Ceiling games skew to busy underdogs** (top 10%, >= 21.4 pts, ~17 games):
  rate by saves-line tercile 5 / 13 / 14%, by P(win) tercile 16 / 11 / 5%.
  Too few games to fit; recheck at ~30 slates. It matters for GPP only.
- **The goalie tail is calibrated** (corrected the same day): 9% beat the logged
  ceiling, which is the 90th percentile, target 10%. Floor 26% (target 25). No
  simulator change.
- **Wrong starters cost the most per case:** 4 of 171 (2%) projected starters
  did not play (0 points); 7 more were pulled.

## 6f. Ownership refits re-checked with whole days held out (2026-10-19)

Adam plays early, main and late slates; an early slate's games are also on that
day's main slate, so folds now hold out a whole DAY, not a draft group.

- **GPP** (17 contests, 13 days): prior 0.8/0.5/0.4 MAE 3.35 -> held-out refit
  3.05, RMSE 5.60 -> 5.00, rank +0.67 -> +0.73, better on 16/17; 12/13 folds
  picked 0.6/0.6/0.6 -- the 10/17 ship stands.
- **Cash field** (15 nights; 154747 has no rebuildable draftables snapshot),
  `scripts/nhl_cash_field.py --all --compare`, field-aware lineups on real
  points: shipped CASH_WEIGHTS cashed 9/15 (mean pct 57%, 110.1 pts, own RMSE
  8.06); day-held-out refit 10/15 (58%, 111.1, own RMSE 8.34). Ownership worse
  held out, one extra cash on one night (10/6): NOT changed.

## 6g. Field ownership as a projection signal: tested, not shipped (2026-10-19)

GPP_META.md G found NHL skaters the crowd over-owned (vs. salary + our
projection) beat projection by +0.45 pts per SD. Is it usable before lock?
`scripts/nhl_ownership_signal.py`: 15 slates / 13 days / 2,714 skaters,
leave one day out, MAE vs a recalibrated projection (M0 4.799):

| added to the projection | held-out MAE change [95% CI] | days better |
|---|---|---|
| our predicted-ownership excess (pre-lock) | -0.009 [-0.033, +0.022] | 9/13 |
| actual %Drafted excess (hindsight ceiling) | -0.012 [-0.034, +0.010] | 9/13 |
| form + role (pre-lock) | -0.019 [-0.043, +0.005] | 8/13 |

Even perfect knowledge of the field's ownership is worth 0.01 points of MAE:
the crowd's information is real but small. Its surprises (actual minus our
predicted ownership) track recent form (+0.18 rank corr, 15/15 slates) and
top-six role (+0.20, 15/15). So the crowd is mostly reading recent form, and
the form itself is the thing to test, not the ownership.

Pre-lock biases worth a fix once more days land (per day, day bootstrap):
- **Cold skaters are over-projected:** bottom form quintile (season-to-date DK
  pts/game, shrunk to last season at k=5 games, minus tonight's projection)
  scored -0.83 [-1.55, -0.15] vs projection (4/13 days). Hot minus cold
  +1.15 [+0.26, +2.24] (11/13). Our prop-based means don't fully price form.
- **Depth skaters (not F1/F2/D1, not PP1) are over-projected** -0.51 [-0.82,
  -0.11], 1/13 days above. They average 1.4% owned, so GPP lineups barely touch
  them, but they are cash value plays.

Not shipped: 13 days and several cuts looked at. Recheck both at ~25 days.
If they hold, the fix belongs in edge/nhl_sim.py's means (form blend, depth
shrink), not in ownership.

## 6h. Context features (not box-score stats, not book prices): what moves a skater's night (2026-10-19)

Adam asked what makes the best skaters other than stats and books.
`scripts/nhl_context_features.py` (`--fetch` caches NHL API referees, coaches,
start times and venues beside the box scores). Target: DK points minus the
player's own leave-one-out mean (shrunk to last season, k=10) minus the
opponent's allowed, so identity carries skill and only context is left. Fit on
2025-26 (1,312 games), checked on 2026-27 (139 games), intervals resample
games. About 30 features were tried, so expect one or two false positives at 5%.

**Ship candidate: 11 F / 7 D lineups, unpriced and visible before lock.**
DailyFaceoff lists the 7th defenceman as group `d4` (COL, DET, LAK, TBL had one
on 10/19). `LINE_GROUPS` in edge/nhl.py has no `d4`, so the board drops him and
projects the other six at full minutes. In 7-D games (111 team-games 2025-26),
every pair loses 1.5-1.9 min, 0.6-0.7 DK pts and 0.14-0.29 blocks. The top
three forwards gain about 1 min (+0.40 pts, +0.14 shots). On our own logged
2026-27 nights the six projected D fell **-0.88 [-1.55, -0.25] pts short of
our projection** (151 skater-games, 10 days), the size the 2025-26 fit predicts
(-0.73).

**Built 2026-10-20** (edge/nhl.py reads `d4`; `edge/dfs_run_nhl.seven_d_teams` flags
a team from a d4 row or exactly 11 listed forwards; each of its D gets
`SEVEN_D_SCALE`: blocks x0.855, shots x0.933, goals x0.92, assists x0.97; the
app names the teams). Forwards are left alone: they gain ~1 min, but DK points
x1.017 [0.99, 1.06]. The pre-lock signal at lock time: 16 of 19 flagged teams
dressed 7 D; it caught 16 of 25.

Before/after (`scripts/nhl_seven_d_compare.py --placebo 2`): 11 logged slates with a
7-D team at lock, each rebuilt from pinned inputs with and without the scale:

| | D-games | MAE | bias | nights better |
|---|---|---|---|---|
| old board | 110 | 4.135 | +0.98 | |
| 7-D scale | 110 | **3.975** | **+0.35** | 9/10 |

Lineups: any nudge to a few D reshuffles 3-4 of 9 slots (hill-climb path
dependence), so the lineup grade is noise at this n. The 15 changed lineups with
an export scored -13.7 real pts vs the old (sd 24, 4 better / 11 worse). The
placebo (same scale on random teams that did NOT dress 7 D, 29 changed
lineups) scored -8.8 (sd 25, 11/18). The difference, -4.9 +/- ~8, is
indistinguishable. The old lineups happened to hit on these nights. The
decision rests on the projection, which is better held out.

**Real in 2025-26 but probably already in the books' prices (they move team
totals, props and the starter):** back-to-back -0.27 [-0.49, -0.06] (shots);
a back-to-back after last night's OT/SO -0.55 [-0.92, -0.16]; home +0.23
[+0.07, +0.39]; opponent starts its backup +0.24 [+0.07, +0.40] (goals).
Against our projection on 18 logged days these are inside the noise.

**Candidates without enough forward data yet:**
- first game back after missing 3+ team games: -0.50 [-0.79, -0.21] (shots
  and blocks; DailyFaceoff shows returns);
- a top-2-minutes teammate forward out: the other forwards +0.36 [+0.11, +0.68],
  with extra shots (usage vacuum);
- **rink scorer effect on blocks**: visitors' recorded blocks run 0.81x (CHI)
  to 1.19x (COL); split-half corr +0.63, 2025-26 to 2026-27 +0.34. As a blocks
  adjustment (deflate last season by his rinks, inflate by tonight's) it moves
  a D about +/-0.27 pts per game including the 3+ block bonus. Forward
  blocks MSE -0.003 [-0.009, +0.002] (4,482 skater-games), not yet
  significant. Blocks have no market, so this is the one rink channel books
  can't price. Recheck at ~400 2026-27 games. Shots by rink 0.84 (CAR) to 1.14
  (TOR), split-half +0.70, but shots have props.

**Nothing there:** referee crews (crew PP rate predicts PP chances +0.10 in
2025-26, -0.00 in 2026-27: no persistence), travel distance, time-zone shifts,
body clock, matinees, altitude, NBA-shared ice, division games, a new coach,
a new team, facing a former team. Published work agrees on contract years
(IZA 2024: no average effect).

## 6i. Props vs no props, and promoted skaters (2026-10-20)

**What a prop is worth** (`scripts/nhl_props_value.py`): 17 logged nights rebuilt from
pinned inputs twice, with the DK props and with them switched off (last season x
implied goals), and graded on the SAME 1,485 prop-player games: MAE 5.576 vs 5.755,
**-0.186 [-0.292, -0.089], better on 14/17 nights**; bias +0.29 vs +0.45; rank corr
+0.304 vs +0.276 (+0.028 [-0.004, +0.063]). Props help, by about 3% of MAE. At a
matched projection, no-prop players (57% of the board, mean projection 5.2) are
not worse: MAE 4.18 vs 4.25 at 5-7 pts, 4.25 vs 5.42 at 7-9. Their errors are about the
same; they are mostly cheap depth.

**Promoted skaters are not hockey's NBA edge** (`scripts/nhl_promotion.py`): a
skater DailyFaceoff lists 2+ minutes above his last-5 ice time (a line or PP1
promotion, usually because someone is out). 512 games, 17 nights:
- he does play more, **+0.85 min** [+0.58, +1.09], against about +8.6 for an NBA
  bench player who starts;
- he **misses our projection, -0.55 [-1.06, -0.05]**. With props it's -1.66
  [-3.15, -0.27]: the books over-price line promotions. Without props it's -0.08;
- per +1 min of promotion, -0.061 [-0.104, -0.018] pts vs projection.
The 2025-26 effect of a top-2 forward being out (+0.36 for teammates vs their own
baseline, 6h) is real but smaller than what the prop lines already add. Candidate,
NOT shipped: shrink prop means for promoted skaters toward their last-5 role.
Recheck at ~30 nights.

## 6j. This season's ice time in the last-season means (2026-10-22, critique cycle 1)

The first `/dfs-critique nhl` cycle (NHL_CRITIQUE_LOG.md) built the held-out harness,
`scripts/nhl_skater_lab.py`: every logged slate rebuilt from pinned inputs at lock and
at lock − 3 h, graded on box-score DK points, TRAIN 9/29-10/12 (11 nights), TEST
10/13-10/21 (8 nights; 10/15 has no draftables snapshot). An independent critic
found that nothing from 2026-27 reached the skater means: 57% of the board (no props)
and every skater's blocks ran on last season's per-game rates, whatever his role now.

Shipped (`edge/dfs_run_nhl.role_ratio` / `scale_role`, `edge/nhl.skater_game_toi`):
r = (5·toi_last + n·mean(last 5 toi)) / (5 + n) / toi_last, n = games this season;
no-prop goals, assists, shots × r², everyone's blocks × r^2.5; prop means untouched.
One NHL stats API call per build (game-level ice-time report); if it fails the
board runs on last season and the page says so (`info["role_source"]`).
Fitted on TRAIN only (RMSE 6.107 -> 6.093); the surface is flat, so the powers are
loose. TEST:

| state | MAE | RMSE | bias | rank corr |
|---|---|---|---|---|
| lock, before -> after | 4.509 -> 4.470 | 5.991 -> 5.962 | +0.14 -> +0.14 | +0.353 -> +0.375 |
| early, before -> after | 4.510 -> 4.478 | 6.003 -> 5.981 | +0.12 -> +0.13 | +0.342 -> +0.368 |

Squared error per night −0.60 [−1.24, −0.05] at lock (6/8 nights), −0.57 [−1.11,
−0.10] early (5/8). It moves depth skaters most (F4 bias +0.42 -> +0.28, D3 +0.63 ->
+0.26), so it helps cash value plays more than stars; the 6.5k+ skaters' −0.4 bias
is untouched (a critic runner-up: the assist draw under-feeds stars).

## 6k. Last season's rates were missing 34 skaters' stats (2026-10-22, critique cycle 2)

`edge/nhl.season_skater_stats` paged the NHL stats API 100 rows at a time with no
sort key; the pages overlapped, so the 2025-26 cache held 940 rows but 929 / 917
distinct players (summary / realtime). A player missing from a report got 0.0, not
"unknown": 27 board skaters ran on zero blocks (Dahlin, Robertson, Chiarot ...) and
10 on zero goals/assists/shots (Fox, Coyle, Teravainen ...) since 9/28. Fixed: one
`limit=-1` request per report, refused unless one row per player matching the API's
total, a duplicated cache refetches itself, and `_season_rates` leaves a missing field
missing (board default + `imputed`). No parameters. Lab, 4 seeds averaged, test:

| state | RMSE | bias | rank corr |
|---|---|---|---|
| lock, before -> after | 5.959 -> 5.946 | +0.135 -> +0.174 | +0.376 -> +0.385 |
| early, before -> after | 5.975 -> 5.961 | +0.124 -> +0.160 | +0.370 -> +0.381 |

MAE unchanged (4.469 / 4.475). The skaters it moved (> 0.25 pts, 113 at lock) went
from −0.73 to −0.08 bias. Their under-projection had been hiding a general
over-projection, now about +0.17 per skater.

**Lab noise:** one simulation seed moves train RMSE by ~0.01, as much as some changes
judged; `scripts/nhl_skater_lab.py report --seeds 4` averages four, and decisions
are now taken at 4 seeds with per-night paired differences. Re-checked that way,
cycle 1's ice-time scaling still holds: test squared error −0.58 [−1.13, −0.09] per
night at lock, −0.56 [−1.08, −0.10] early.

## 6l. Critique runner-ups, implemented and tested (2026-10-22)

Every open runner-up from cycles 1-2 (NHL_CRITIQUE_LOG.md), each a lab variant fitted
on TRAIN at 4 seeds and judged on TEST in both states.

Shipped:
- **Exact assist draw** (`edge/nhl_sim._assist_matrix`): the weight solve assumed two
  independent assist draws; simulate() redraws a clashing second assister once. High-q
  assisters came out short: $7.5k+ skaters given 0.706 assists simulated 0.656 (now 0.690).
- **Points-prop stretch** (`POINTS_REF` 0.556, `POINTS_STRETCH` 1.1 in
  edge/dfs_run_nhl.py): actual points rise ~1.3x as fast as the market's points mean.
  Alone the stretch fit 1.3 on TRAIN; refitted with the exact draw it is 1.1.
- **Nickname joins** (`name_aliases`): DK's Nicholas Robertson, Zack Bolduc, Will
  Borgen, Joe Veleno and Jeffrey Viel matched to last season's stats through this
  season's games (unique team + initial + last name; brothers never matched).
- **Grading** (lab, `box_key`): 'J.Kulich' / 'D.Jiricek' had no space, so their games
  were counted as scratches.

Together, TEST, 4 seeds:

| state | MAE | RMSE | bias | rank corr | $7.5k+ bias |
|---|---|---|---|---|---|
| lock, before -> after | 4.461 -> 4.449 | 5.938 -> 5.929 | +0.181 -> +0.181 | +0.386 -> +0.387 | −1.51 -> −0.91 |
| early, before -> after | 4.467 -> 4.455 | 5.954 -> 5.945 | +0.167 -> +0.168 | +0.381 -> +0.382 | −1.47 -> −0.89 |

Per night the gain is not uniform (better on 5/8 test nights in both states, pooled
dMSE −0.12 [−0.27, +0.10] at lock). It concentrates on the stars.

Not shipped: **7-D blocks factor** (0.855 -> 0.95, the TRAIN argmin): TRAIN RMSE moved
0.0002; TEST worse per night at lock (+0.047 [−0.018, +0.141], 3/8). The critic's
gap came mostly from TEST nights; recheck at ~30 nights.

**Fixed the same day (at Adam's request): the dropped second assist.** simulate()
redrew a second assister once on a clash with the first and dropped the assist on a
second clash, so teams ran below the fitted 1.689 assists per goal (1.666 on the
12-game 10/20 slate, stars the most short at 0.975 of target). The second assister is
now drawn from the other teammates, and the solve uses P(j second | i) = q_ij Σ_{k≠j}
q_ik / (1 − q_ik). Now: 1.692 per goal, every salary tier within 0.5% of target. Lab,
4 seeds, test: RMSE 5.929 -> 5.928 (lock), 5.945 -> 5.943 (early); MAE 4.449 -> 4.453;
rank flat; bias +0.18 -> +0.20 (projections +0.023 per skater). A correctness fix,
neutral on held-out error. It leaves the general over-projection a little more exposed.

## 6m. Critique cycle 3: the test nights are the limit (2026-10-22)

Nothing shipped (NHL_CRITIQUE_LOG.md cycle 3). Skater error is now at the simulator's
own spread (test MSE 35.2 vs simulated variance 38.3), the books' shots / points lines
are calibrated, and no player or team miss persists from train to test except blocks.
The best mean change found (this season's blocks + rink scorer) was better on TRAIN and
worse on pooled TEST; 8,000 sims instead of 2,000 was mixed held out and doubles build
time. Rule from here: mean changes to unpriced components get validated on 2025-26 box
scores first (harness to build), since 8 test nights cannot certify gains under ~0.02
RMSE. Candidate next focus: the simulated spread (3-8% wider than realised errors),
which sets the cash floor and GPP ceiling.

## 6n. This season's production in the unpriced means (2026-10-22, critique cycle 4)

New harness `scripts/nhl_season_lab.py`: the 2025-26 season (47,231 skater-games, 167
dates) replayed through `skater_means` (the board's recipe, now one function) and the
simulator, on the no-prop path; H1 to 12/31 fits, H2 checks. Shipped: every mean last
season sets is blended with this season's per-game production (`blend_season`, K points
30 / shots 7 / blocks 20; `nhl.skater_season_to_date`, two stats-API requests a build).
Season lab H2: RMSE 6.262 -> 6.119, bias +0.30 -> +0.15, rank +0.393 -> +0.423 (78/86
dates better). 2026-27 test: not worse (lock RMSE 5.928 -> 5.926, early 5.943 -> 5.939);
the gain grows as games accumulate. Details: NHL_CRITIQUE_LOG.md cycle 4.
