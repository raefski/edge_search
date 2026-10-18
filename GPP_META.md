# GPP meta and model accuracy: what the contest exports say

Built 2026-10-13 from every DK contest export on disk (108 contests: 61 GPP + 47
cash, 201,928 live entries, 1.78M lineup slots), joined to the **pre-lock**
projection logs by draft group. Everything below is reproducible:

```bash
python3 scripts/contest_db.py            # build / update data/contests.db (idempotent)
python3 scripts/model_accuracy.py        # section B
python3 scripts/gpp_meta.py              # sections A and D (add --sport X, --no-sharp)
python3 scripts/buzz_trust.py            # section C
```

The rule that runs through all of it: **entries in one contest share one
realisation of the slate**, so the independent unit is the slate, not the
entry. Every interval resamples whole slates, every contest weighs the same,
and every claim carries n slates / contests / entries. Fewer than 5 slates
gets point estimates only and the words "not enough data".

---

## TL;DR

1. **Chalk vs contrarian.** In every sport but MMA, cashers carried *more*
   ownership than the field. That is mostly because chalk projects well. **Holding our
   pre-lock projection fixed, ownership has no detectable effect on cash rate
   or ROI in MLB (20 slates) or NHL (8 slates).** The only reliable contrarian
   effect is small and at the very top: in NHL, lower ownership for the same
   projection raised the top-1% rate (-0.56 pp per SD of log-product
   ownership, 95% CI [-1.00, -0.10], on a 1.0% base), but that did not carry
   into ROI (-0.10 [-0.34, +0.16]). No sport shows a field-size gradient you
   can trust. **For single-entry 500-1,500-entry GPPs: maximise projection
   first. Use ownership only as a tie-break between lineups with near-equal
   projections, and don't pay projection to get it.**
2. **MLB is the exception that matters most.** Our MLB projection does *not*
   separate GPP lineups. Alone it moves cash rate by +2.0 pp per SD [-0.4, +4.3].
   Next to ownership it moves it by -0.25 pp [-3.0, +2.6]. Field ownership
   predicts lineup points better than our projection does (+4.0 pp points
   percentile per SD at fixed projection [+1.0, +6.8]). At the player level the
   hitter model does not beat DK's own FPPG on MAE at any position (catcher
   is a tie). The MLB
   edge has to come from the projection, not from fading.
3. **Accuracy.** Points MAE: NFL 5.17 (rho 0.59), NHL 5.14 (0.36), MLB 5.71
   (0.21), NCAAF 7.85 (0.47, biased -2.3 low). GPP ownership MAE: NHL 2.23,
   NFL 2.74, MLB 3.78, NCAAF 4.33. Cash ownership is consistently 1.2-1.6x worse,
   because the model predicts a GPP-shaped field. **Against Dan on the same
   players, our NFL points are better on all 3 slates (4.70-5.71 vs 5.03-5.88)
   and his ownership is better on all 3 (1.51-2.04 vs 2.53-2.88).** NCAAF 10/03
   is a tie on points; our ownership is better there.
4. **Buzz.** NFL YouTube DFS buzz is real signal: held-out ownership MAE
   improves by +0.22 (GPP) and +0.27 (cash) on all 3 slates. 20 channels
   add value on top of the rest; 5 don't. NHL buzz is noise (-0.03 held-out on
   8 GPP slates; the channels are betting-pick shows). **Pick'em touts are
   indistinguishable from coin-flipping channels.** All tout picks went 48.3%
   (2025, n=2,234) and 46.5% (2026, n=512) against the closing line. The
   between-channel dispersion test gives p = 0.34. Year-to-year persistence is
   r = +0.03 [-0.30, +0.36]. Drop them all.
5. **Simulator.** Measured field parameters with CIs are in section D. The
   sharp share is consistent across sports at about 0.20-0.25 (MLB 0.20, NHL
   0.25, NFL 0.25, which agrees with the shipped NFL 0.30).

---

## Data and method

**`data/contests.db`** (gitignored like every `data/*.db`; Dan's paid sheet lives in it):

| table | what |
|---|---|
| `contest` | id, sport, type, name, field, live entries, fee, places paid, pool, draft group, slate date, max entries, `final` (0 = exported mid-contest) |
| `entry` / `entry_player` | every entry's rank, points, entrant, Adam flag (`gorillabiscuit`), parsed lineup (slot, player) |
| `player_result` / `player_slot` | the ownership board, %Drafted summed over slots and per slot, CPT share, FPTS |
| `projection` | our pre-lock logs (all sports, plus 992 recovered MLB rows, see below) and Dan's sheets (`source='dan'`) |
| `slate_player` | salary / team / opponent / game per draft group, from DK draftables snapshots in **git history** (27 slates), else the log; NHL line + PP, MMA bout |
| `contest_match` | which log graded which contest and why, coverage, ok_points / ok_own, note |
| `payout` | DK's table (6 cached), copied by template (26), flat double-up (45), name-flat (1), or rank-scaled (29) |
| `entry_feature` | own_sum, log-product own, pre-lock proj (and salary-imputed fill), salary, dup count, payout, cash / top-1% / win, stack shape |

**Matching is by draft group, never "nearest slate".** MLB's main log has no
draft group, so it matches on DK's slate date. The match is then *checked*:
the board's ownership must be at least 80% covered, and if the log carries
teams nobody on the board drafted, it was a bigger slate and its ownership is
not graded. That check rejected the 7/17 Night contests (main-slate log, 24%
coverage), 7/06 (44%) and 7/02 (a partial 83-player pool, 79.9%).
`python3 scripts/contest_db.py --report` prints every match.

**Payouts.** Three DK lookups (spaced 2 s) fetched the $1K / $750 / $500
Daily Dollar tables. Those cover 26 more contests by template, and the 3 NFL
$15K tables were already cached. The 29 contests with no table get the closest
DK table's shape, stretched to their paid places, with DK's min-cash (2x the
fee under $1, 1.5x from $1). These were checked against Adam's DK
entry-history export: template and flat payouts are exact on all 30 of his
finishes the export covers. Scaled payouts are exact on 99 of 105, and $10.52 vs $10.50 in total.
ROI on scaled contests is approximate; cash and top-1% are exact everywhere.

**Recovered MLB slates.** `data/dfs_proj_log.csv` lost its 7/25-7/31 rows.
The live calibration dashboard artifact still embeds them. Its values equal
the log **exactly on all 2,172 player-dates both hold**, so its 992 rows for
7/25, 7/27-7/31 are now in `data/dfs_proj_log_mlb_recovered.csv`. They contain
board players only, so they grade points and lineups but never ownership.
This took MLB from 14 to 20 GPP slates with a pre-lock projection. The
dashboard was read, not republished.

**Two NBA exports were downloaded before their contests finished**
(188461450, 188494894). Their ownership is final but their ranks are not, so
they are flagged `final=0` and excluded from every outcome table.

**MLB suffix bug.** Keying the log with `edge.dfs.norm` would drop 94 of 3,173
board joins, which is 3.8% of joined ownership across 14 players (Witt Jr.,
Tatis Jr., Chisholm Jr., Lombard Jr.). It is fixed in 8336706a;
`scripts/dfs_calibration.py` now keys with `edge.names.norm`.

**Cross-checks against the existing graders (exact):** NHL 10/10 n=274, bias
+0.39, MAE 5.19 (`nhl_calibration.py`'s skater and goalie rows recombined).
MLB 7/21 and 9/18 points MAE 5.51 / 5.74 and ownership 2.82, as in
`data/dfs_calibration.json`. NCAAF 9/26 n=63, bias -3.88, MAE 7.16
(NCAAF_STATUS.md). NFL GPP ownership 2.53 / 2.88 / 2.80, which is the "value
only (shipped)" column in `edge/dfs_nfl_theory.py`. NFL QB stack 72% / 19% on
9/13 (FIELD_STACK docs).

---

## A. What finishes in the money: chalk or contrarian?

### A1. Descriptive (selected on outcome, survivorship-prone)

Within-contest percentiles (0.50 = the field). own = summed ownership; proj =
our pre-lock projection, on contests that have one.

| sport | final GPPs / entries | cashers own pctile | top-1% own pctile | winners own pctile (n) | cashers proj pctile |
|---|---|---|---|---|---|
| MLB | 30 / 49,758 | 0.56 | 0.52 | 0.57 (35) | 0.52 |
| NFL | 5 / 67,360 | 0.50 | 0.46 | 0.18 (5) | 0.62 |
| NHL | 8 / 5,755 | 0.60 | 0.57 | 0.59 (8) | 0.63 |
| NCAAF | 3 / 2,134 | 0.64 | 0.57 | 0.60 (3) | 0.68 |
| NASCAR | 2 / 2,063 | 0.53 | 0.51 | 0.29 (2) | 0.35 |
| MMA | 2 / 1,298 | 0.46 | 0.50 | 0.48 (2) | 0.65 |
| NBA | 10 / 59,362 | **0.67** | **0.68** | 0.64 (18) | - |

Cashers lean chalky everywhere except MMA. "Winners were contrarian" holds
only for NFL's 5 winners and NASCAR's 2, which is exactly the
survivorship-sized sample to ignore.

### A2. Ex-ante: holding our projection fixed (`gpp_meta.py --section exante`)

Contest-fixed-effects linear probability / OLS on every entry, contest-equal
weights, 95% CI from a slate bootstrap. Coefficients are per +1
within-contest SD. Own = log-product ownership; summed ownership agrees in
sign throughout. ** = interval excludes 0.

| sport | slates / entries | proj effect on cash | **own effect on cash** | own on top-1% | own on ROI | proj alone on ROI |
|---|---|---|---|---|---|---|
| MLB | 20 / 37,650 | -0.25 pp [-3.0, +2.6] | +3.6 pp [-0.4, +7.5] | +0.01 pp [-0.36, +0.31] | +0.03 [-0.19, +0.22] | -0.01 [-0.17, +0.13] |
| NHL | 8 / 5,755 | **+10.6 pp [+7.4, +14.2]** | -0.2 pp [-3.9, +3.9] | **-0.56 pp [-1.00, -0.10]** | -0.10 [-0.34, +0.16] | **+0.48 [+0.21, +0.75]** |
| NFL | 3 / 53,432 | +12.7 pp | -2.0 pp | +0.01 pp | -0.07 | +0.45 |
| NCAAF | 2 / 1,779 | +12.2 pp | +2.9 pp | -0.38 pp | -0.01 | +0.51 |
| NASCAR | 1 / 882 | -9.0 pp | +10.8 pp | +0.77 pp | +0.46 | - |
| MMA | 1 / 118 | +16.5 pp | -4.4 pp | +1.03 pp | +0.01 | - |

Base rates: cash about 24%, top-1% about 1%, ROI about -0.13 (the rake).
**NFL, NCAAF, NASCAR and MMA have 1-3 slates: not enough data to conclude
anything; those rows are one or two realisations each.**

**Ownership quintile after projection.** Within each contest, residualise
log-product ownership on projection and cut it into quintiles. Q1 is the
lowest ownership for its projection. Q1 and Q5 have matched projection
percentiles (MLB 0.47 / 0.47, NHL 0.48 / 0.46). (k+/m-) = slates where Q1
beat / trailed Q5.

| sport | Q1 - Q5 cash | Q1 - Q5 top-1% | Q1 - Q5 ROI | Q1 - Q5 points pctile |
|---|---|---|---|---|
| MLB (20) | -6.4 pp [-14.4, +1.8] (8+/11-) | +0.12 pp [-0.50, +0.74] (10+/7-) | +0.00 [-0.39, +0.38] (11+/9-) | **-7.4 pp [-13.8, -1.7]** (6+/14-) |
| NHL (8) | -0.4 pp [-7.6, +8.0] (3+/5-) | **+1.21 pp [+0.28, +2.24]** (6+/2-) | +0.10 [-0.48, +0.77] (4+/4-) | -2.7 pp [-7.9, +3.2] (3+/5-) |
| NFL (3) | +4.7 pp (2+/1-) | +0.09 pp (1+/2-) | +0.17 (1+/2-) | +5.0 pp (3+/0-) |

**By field size.** No sport shows a gradient you can trust.

- **MLB, per doubling of the field:** the ownership slope on cash moves +1.2 pp
  [-1.6, +5.2], and on ROI +0.05 [-0.10, +0.25].
- **MLB Q1 - Q5 ROI by band:**

  | field band | slates | Q1 - Q5 ROI |
  |---|---|---|
  | <=600 | 4 | +0.09 (2+/2-) |
  | 601-1,500 (Adam's single-entry Daily Dollars) | 12 | +0.28 [-0.16, +0.60] (8+/4-) |
  | >1,500 (8-9k Solo Shot / mini-MAX / Dime Time) | 4 | -0.90 (1+/3-) |

  On the first 14 slates the 601-1,500 band looked significant (+0.47
  [+0.20, +0.75]). The 6 recovered slates diluted it. That is a reminder of
  how fragile a 9-12-slate result is.
- **NHL:** the ownership-x-field interaction is positive (top-1% +2.05 pp per
  doubling [+0.58, +3.05]). That would mean contrarian helped in the smaller
  (473-594) fields and chalk in the about-890 ones: Q1 - Q5 top-1% was +2.28 pp
  (4+/0-) at <=600 and +0.14 pp (2+/2-) at 601-1,500. But field size here is
  4 slates vs 4 slates with one contest each, so it is confounded with which
  nights they were. **Not established.**

**Without projection control** (every final GPP; this biases toward "chalk is
good", because chalk projects well):

| sport | slates | log-prod own slope on cash | log-prod own slope on ROI |
|---|---|---|---|
| MLB | 30 | +5.0 pp [+2.6, +7.5] | +0.09 [-0.03, +0.22] |
| NHL | 8 | +5.9 pp [+0.9, +10.6] | +0.22 [-0.11, +0.52] |
| NBA | 10 | **+13.7 pp [+10.7, +16.8]** | **+0.42 [+0.31, +0.55]** |
| NFL | 5 | +1.2 pp [-5.7, +7.3] | - |

NBA's top-1% slope is +0.74 pp [+0.31, +1.41]. NBA fields rewarded chalk
strongly on all 10 slates; that matters for the NBA build.

**Adam's own entries** (percentiles within each contest):

| sport | entries | own pctile | proj pctile | cash rate | ROI |
|---|---|---|---|---|---|
| MLB | 20 | 0.22 (contrarian) | 0.47 | 20% | -0.50 |
| NHL | 8 | 0.74 (chalky) | 0.99 | 62% | +0.06 |
| NFL | 3 | 0.81 (chalky) | 0.98 | 3 of 3 | +1.83 |

His MLB lineups were contrarian without being better-projected. That is the
combination the data says doesn't pay.

**Robustness.** Restricted to lineups whose every player was in the log (MLB,
`--full-cov`), the conclusions are unchanged: own on ROI -0.03 [-0.25, +0.15],
Q1 - Q5 ROI +0.14 [-0.20, +0.48]. Summed vs log-product ownership agree in
sign everywhere.

### A3. The answer, per sport

- **MLB (20 slates):** the chalk-vs-contrarian lever is worth about zero ROI
  at fixed projection. The real problem is that our projection is not a better
  guide than the crowd's. Don't fade for its own sake.
- **NHL (8 slates):** projection is the edge (+0.48 ROI per SD). Lower
  ownership at the same projection buys a bit more top-1% equity, but no
  measurable ROI or cash. Use it as a tie-break.
- **NFL / NCAAF / NASCAR / MMA:** not enough data (1-3 slates each). Keep
  exporting; the earliest sport to become decidable is NFL, at about 5 main
  slates with logs.
- **NBA:** projection is untestable (no logs yet), but chalk cashed and
  top-1%'d more on all 10 slates. Start the NBA build assuming chalk is
  usually right.

---

## B. Model accuracy (`model_accuracy.py --per-slate`)

### B1. Points: pre-lock projection vs actual DK points (per slate)

| sport | slates | n | bias | MAE | rho | notes |
|---|---|---|---|---|---|---|
| MLB | 20 | 3,307 | +0.48 | 5.71 | +0.21 | live log 5.80 (n=2,259), recovered 5.42 (989) |
| NFL | 3 | 596 | -0.93 | 5.17 | +0.59 | |
| NHL | 9 | 1,776 | +0.22 | 5.14 | +0.36 | live 5.15 / rebuild 5.14: rebuilds are not flattering |
| NCAAF | 2 | 193 | **-2.31** | 7.85 | +0.47 | biased low at every position |
| NASCAR | 1 | 36 | +0.40 | 20.68 | +0.09 | one race |
| MMA | 1 | 16 | -3.83 | 39.63 | +0.20 | one card |

**By position:**

| sport | pos | n | bias | MAE | rho | vs baseline |
|---|---|---|---|---|---|---|
| MLB | P | 267 | +1.01 | 8.37 | +0.29 | DK FPPG 8.41 vs ours 8.29 on same players: about a tie |
| MLB | hitters (C/1B/2B/3B/SS/OF) | 2,114 | -0.41 to +0.74 | 5.03-5.80 | +0.03 to +0.23 | **DK FPPG beats us at 1B/2B/3B/SS/OF** (e.g. SS 5.28 vs 5.45); tie at C |
| NFL | QB | 73 | -1.04 | 6.42 | +0.25 | |
| NFL | RB | 130 | -0.89 | 5.00 | +0.62 | |
| NFL | WR | 218 | -0.94 | 5.07 | +0.56 | |
| NFL | TE | 99 | -1.43 | 5.33 | +0.37 | |
| NFL | DST | 76 | -0.19 | 4.31 | +0.20 | |
| NHL | C | 372 | +0.06 | 5.25 | +0.39 | |
| NHL | W | 725 | +0.10 | 5.31 | +0.38 | |
| NHL | D | 564 | +0.40 | 4.22 | +0.32 | |
| NHL | G | 115 | +0.66 | 8.22 | **+0.09** | goalie projection barely ranks |
| NCAAF | QB | 35 | -3.23 | 10.72 | +0.51 | |
| NCAAF | RB | 48 | -2.48 | 8.37 | +0.34 | |
| NCAAF | WR | 110 | -1.95 | 6.72 | +0.42 | |
| MMA | F | 16 | -3.83 | 39.63 | +0.20 | DK FPPF 31.1 vs ours 34.6 on same fighters |

Only players someone drafted have an actual. 95-99% of logged players were
graded in MLB, NFL and NCAAF, and 82% in NHL (its logs carry the full pool).

### B2. Ownership: per contest, cash and GPP never pooled

The universe is the logged pool; a player missing from the board counts as
0%. "MAE >=5%" covers players the model or the field put at 5% or more.

| sport | type | contests | n | bias | MAE | rho | MAE >=5% (n) | field top-owned vs model top (median) |
|---|---|---|---|---|---|---|---|---|
| MLB | gpp | 12 | 1,982 | +0.28 | 3.78 | +0.45 | 5.63 (1,085) | 42% vs 50% |
| MLB | cash | 10 | 1,605 | +0.27 | 5.97 | +0.46 | 9.52 (797) | **74% vs 50%** |
| NFL | gpp | 3 | 597 | +0.15 | 2.74 | +0.74 | 5.17 (236) | 47% vs 24% |
| NFL | cash | 3 | 597 | +0.19 | 3.50 | +0.62 | 6.64 (232) | 62% vs 24% |
| NHL | gpp | 8 | 2,006 | +0.08 | **2.23** | +0.74 | 5.18 (573) | 39% vs 38% |
| NHL | cash | 9 | 2,158 | +0.10 | 3.31 | +0.63 | 8.30 (606) | **76% vs 46%** |
| NCAAF | gpp | 2 | 204 | +0.71 | 4.33 | +0.72 | 7.19 (107) | 53% vs 42% |
| NCAAF | cash | 2 | 204 | +1.12 | 6.46 | +0.57 | 12.10 (94) | 87% vs 42% |
| NASCAR | gpp | 1 | 36 | +0.17 | 15.84 | **-0.43** | | prior anti-ranks |
| NASCAR | cash | 1 | 36 | +0.07 | 18.92 | -0.32 | | |
| MMA | gpp | 1 | 16 | 0.00 | 11.05 | +0.79 | | |
| MMA | cash | 1 | 16 | +0.32 | 16.26 | +0.71 | | uses own_cash |

**Weak spots by position (GPP):**

- MLB pitchers: MAE 6.44, rho 0.71. Hitters: 3.06-3.79.
- NFL DST: MAE 3.75 but rho 0.16; DST ownership barely ranks. QB: rho 0.50.
- NHL goalies: MAE 4.44.

Cash fields concentrate far harder than the model's single GPP-shaped
prediction, in every sport. A separate cash ownership model (or a
sharpening exponent per contest type) is the cheapest accuracy win available.

### B3. Dan's Projections, same players

| slate | n | points MAE ours / Dan | own MAE GPP ours / Dan | own MAE cash ours / Dan | own >=5% GPP ours / Dan |
|---|---|---|---|---|---|
| NFL 9/13 | 184 | **5.71** / 5.88 | 2.53 / **1.51** | 3.70 / **3.07** | 4.64 / **2.83** |
| NFL 9/20 | 200 | **4.70** / 5.03 | 2.88 / **1.88** | 3.42 / **2.54** | 5.21 / **3.50** |
| NFL 9/27 | 212 | **5.13** / 5.34 | 2.80 / **2.04** | 3.41 / **2.73** | 5.15 / **4.08** |
| NCAAF 10/03 | 130-136 | 8.19 / 8.23 | **3.47** / 3.70 | **4.73** / 5.21 | **6.15** / 6.76 |

- NFL by position: we beat Dan most at RB on 9/20 (3.41 vs 4.33). We trail him
  at RB on 9/13 (6.80 vs 6.64), where our RBs ran -3.7 low, the known
  TD-imputation gap.
- The shipped NFL buzz model is not in these logs. Its held-out MAE was
  2.15 / 2.42 / 2.40 (edge/dfs_nfl_theory.py), still behind Dan's
  1.51 / 1.88 / 2.04.
- Dan's 10/11 NFL sheet has no matching contest export yet.

---

## C. Buzz and tout sources (`buzz_trust.py`)

### C1. DFS ownership buzz (held-out, leave-one-slate-out)

Baseline = our logged, value-only ownership. The held-out slate's prediction
is tilted by exp(b x log1p(mentions)), with b fitted on the other slates, and
renormalised. "Marginal" is a channel's gain **on top of all other channels**.
That is the keep/drop test, since alone almost any DFS channel "helps" by
naming chalk.

| feed | slates | all channels pooled, held-out MAE gain | verdict |
|---|---|---|---|
| NFL GPP | 3 | +0.20 / +0.28 / +0.18 (base 2.73) | **keep the feed** |
| NFL cash | 3 | +0.27 / +0.32 / +0.23 (base 3.51) | keep |
| NHL GPP | 8 | mean -0.03 (positive on 3 of 8) | **drop** (BUZZ_BETA stays 0) |
| NHL cash | 9 | mean +0.04 (positive on 5 of 9) | noise |
| NCAAF | 1 | - | not enough data |

**NFL channels, marginal held-out gain in GPP and in cash (3 slates):**

| recommendation | channels (GPP marginal / cash marginal) |
|---|---|
| **KEEP** (positive in both, on 2-3 of 3 slates) | Chris Pennell +0.107 / +0.318 · Ship It Nation +0.087 / +0.146 · One Week Season +0.087 / +0.185 · Fantasy Alarm +0.075 / +0.153 · Mayo Media Network +0.071 / +0.165 · RotoGrinders +0.060 / +0.142 · Peter Overzet +0.056 / +0.085 · Occupy Fantasy +0.054 / +0.224 · Kunath +0.062 / +0.129 · SlyDogg +0.041 / +0.181 · Footballguys The Audible +0.032 / +0.087 |
| **downweight / watch** (small or mixed) | Fantasy Football Consultants +0.024 / +0.199 · Heat Seekr +0.025 / +0.036 · DFS Army +0.019 / +0.045 (despite the most videos) · Fantasy Six Pack +0.018 / +0.045 · Tory Langley +0.015 / +0.058 · Fantasy Points DFS +0.008 / +0.027 · CamATL +0.040 / +0.112 · Liam Murphy +0.032 / +0.024 · Neil Orfield +0.007 / +0.055 · Davis Mattek +0.007 / -0.003 |
| **DROP** (negative marginal in GPP) | Stokastic DFS -0.002 / +0.033 · Run Pure Sports -0.004 / -0.014 · Ibe's DFS -0.012 / +0.013 · **DK DFS -0.018 / -0.007** · **Swolecast -0.034 / -0.024** · Splash Play -0.062 / +0.024 |
| not enough data (1 slate) | Dr Roto · 925 Sports · Fantasy Football Today |

All on 3 slates. Revisit after 3 more NFL GPP exports with a log. The
existing in-sample ledger (`data/source_ledger.csv`) agrees on the scale:
the largest drop-one gain is +0.011.

**NHL channels:** 7 of 9 are DROP on the GPP marginal test. None is a DFS
show; they are betting-pick channels (PickDawgz, Cash Out Sports Picks, Tony's
Picks, Doc's Sports). Either find NHL DFS channels or stop collecting.

### C2. Pick'em touts against the closing line

| evidence | result |
|---|---|
| every tout pick, 2025 | 1,078-1,156 = **48.3%** [46.2, 50.3], 25 channels |
| every tout pick, 2026 | 238-274 = **46.5%** [42.2, 50.8], 56 channels |
| dispersion, 2025 (24 channels with >= 20 picks) | chi2 26.4 on 23 df, **p = 0.34**: the spread of channel records is what coin-flipping channels produce; true-skill SD about 2.9% |
| persistence 2025 -> 2026 (23 channels) | r = **+0.03** [-0.30, +0.36] |
| follow the consensus (>= 3 channels, >= 60% one side) | 2025 46.3% [39.6, 53.2]; 2026 43.1% [30.5, 56.7]. "Fade" is mostly "take the dog" (pickem_media_backtest.py 2b) |
| best empirical-Bayes rate | Money Mitch 52.6% (n=182 picks, raw 54.9% [48, 62]) |

**Verdict: drop every tout as a pick signal.** Adam's "noisy" is, if
anything, generous: tout picks ran *below* 50%.

- No channel's combined interval clears 50%.
- Two clear the other way: HalfmoonsPicks 43.0% (n=207) and OddsShopper 34.8%
  (n=89). With 25 channels tested, two such channels is what multiple testing
  produces. They are not fade signals either.
- `data/tout_picks.csv` (9 picks) is too small to grade.
- The one pick'em lead worth tracking is not a tout: CBS public majority went
  40.5% (32-47) in 2026, so "fade the public" was 59.5% [48, 70].

---

## D. Field construction (for the in-app contest simulator)

GPP only. Rates are averaged over contests; 95% CI from resampling contests
(`gpp_meta.py --section sim`). Stack rates use only contests where every
lineup's teams are known (DK draftables from git history). The July MLB slates
lack them.

| sport | contests | dup >=2 | stack parameters |
|---|---|---|---|
| **MLB** | 30 (stacks: 9) | 10% [8, 13] | team hitters >=4: 62% [56, 67] · 5-stack: 41% [35, 48] · 2nd stack >=3: 25% [22, 27] · hitter vs own P: 15% [9, 24] (n=6). Shapes: 5-2 19%, 5-3 13%, 3-2 12%, 2-2 12%, 5-1 9%, 4-2 8%, 4-3 6% |
| **NFL main** | 4 main slates | 3.4-4.3% (2-game primetime: 59%) | QB + >=1 own WR/TE: 72 / 75 / 73 / 78%. QB + >=2: 19 / 20 / 23 / 25%. Bring-back >=1: 37 / 35 / 41%. Shipped FIELD_STACK_1/2 (draw rates 0.65/0.15, landing at 72/20) is still right |
| **NHL** | 8 | 8% [6, 9] | team skaters >=3: 81% [75, 86] (<=4 games **88%** [85, 90], >=5 games **73%** [69, 78]) · >=4: 43% [39, 48] · same F-line >=2: 80% [76, 84] · >=3: 31% [28, 34] · PP unit >=3: 54% [50, 59] · skater vs own G: 14% [8, 20] (<=4 games 21%, >=5 games 7%) |
| **NCAAF** | 3 | 10% [7, 13] | QB + >=1 WR: 60% [52, 65] · QB + >=2: 13% · bring-back: 46% [31, 58] |
| **NASCAR** | 2 | 28% (no CI) | - |
| **MMA** | 3 | 25% [5, 40] | both fighters of one bout: 7% [0, 20] |
| **NBA** | 11 | **29% [25, 33]** | teams unknown (no draftables for those slates) |

**Sharp share.** We draw ownership-only fields slot by slot from each slot's
real %Drafted ($48k-$50k spend, no stacking). A share s is then re-weighted by
exp(beta x proj), and (s, beta) are fitted to the real field's p50-p99 of our
projection (`--section sharp`).

| sport | contests | s median (IQR) | beta per point | real minus ownership-only, p50 / p99 |
|---|---|---|---|---|
| MLB | 14 | **0.20** (0.15-0.20) | about 0.7-0.8 | +0.4 to +1.9 / +1.9 to +6.4 |
| NHL | 8 | **0.25** (0.24-0.26) | about 0.65 | +0.8 to +2.0 / +2.7 to +8.1 |
| NFL | 3 | **0.25** (0.23-0.28) | about 0.60 | +2.7 to +2.9 / +5.5 to +8.2 |
| NCAAF | 2 | 0.40 / 0.75 (unstable) | - | - |
| NASCAR | 1 | 0.05 | - | meaningless: our projection has no rank signal there |

The NFL fit lands on the shipped SHARP_SHARE 0.30 / SHARP_BETA 0.4 (which
was fitted with a stacking sampler), so the method transfers. For NHL and MLB
use s about 0.25 / 0.20 and refit beta with the app's own sampler, because
stacking widens the field's projection spread.

**Summed ownership, field vs cashers** (median own_sum). This scales mostly
with slate size, not field size. The NHL GPP is 132-172% on 3-4-game slates
and 66-90% on 8-13 games.

| sport | GPP field / cashers | cash field / cashers |
|---|---|---|
| MLB | 124% / 130% (<=600: 181 / 188; 601-1.5k: 142 / 151; >1.5k: 111 / 117) | 326% / 363% |
| NFL | 121% / 128% | 181% / 210% |
| NHL | 114% / 129% (<=600: 109 / 113; 601-1.5k: 121 / 147) | 217% / 259% |
| NCAAF | 164% / 194% | 340% / 380% |
| NASCAR | 158% / 162% | 247% / 268% |
| MMA | 185% / 172% | 264% / 282% |
| NBA | 201% / 240% | - |

Salary: the median GPP lineup leaves $100 unspent; only 1-4% leave $1,000 or more.

---

## E. Data gaps

| gap | contests |
|---|---|
| **No pre-lock log** | MLB 7/04, 7/05, 7/10, 7/26, 8/01, 8/02, 9/17, 9/22 · NFL 9/27 primetime (153774), 10/04 (154078) · NCAAF 10/10 (154582; autolog built 154800/155006 instead) · NASCAR 10/04 · MMA 10/03, 10/10 · all 12 NBA |
| **Rejected log matches** | MLB 7/02 (partial pool), 7/06 (other slate), 7/17 Night x2 (main-slate log) |
| **Logs with no export yet** | NFL 10/11 (154468-154473) and 10/12 (154520) · NHL 154718, 154721, 154727 · MLB 154908, 154985, 155015 · NCAAF 153831, 154800, 155006 · NASCAR 154568 |
| **Ownership not gradeable** | MLB 6/30 and 7/01 (the log had no own column) · 6 recovered July slates (board players only) |

Every Windows Downloads export is already imported. If Adam played any of the
"logs with no export" slates, downloading those exports is the cheapest
evidence available.

---

## F. How to keep this current

After every contest you enter (same night):

```bash
python3 -m harness run import-contests       # data/contest_inbox/<sport>/<cash|gpp>/ + Downloads -> data/,
                                              # tags from DK (1 lookup per new contest), updates contests.db, grades
python3 scripts/contest_db.py --report        # check it MATCHED its draft group; "no log" = autolog missed it
```

Weekly, or after about 3 new slates in a sport:

```bash
python3 scripts/model_accuracy.py --per-slate           # points + ownership, Dan where his sheet is in the root
python3 scripts/gpp_meta.py --sport NHL                 # ex-ante chalk/contrarian + field params for one sport
python3 scripts/buzz_trust.py                           # channel keep/drop, pick'em touts
python3 -m pytest tests/test_contest_db.py -q
```

- **A new payout template** (a contest type not seen before): run
  `python3 scripts/contest_db.py --fetch-payouts 1`. That makes one DK call,
  is spaced, and is cached in `data/cache/dk_payouts/`. Never more than a few
  per run (DRAFTKINGS_ACCESS.md).
- **When the conclusions can move:**

  | sport | needs |
  |---|---|
  | NFL | 5+ main-slate GPPs with logs |
  | NHL / MLB | 4+ slates per field band for a band split |
  | NBA | its first logged slates (the build starts 10/14) |

- **Before republishing the MLB calibration dashboard:** its July data now
  lives in `data/dfs_proj_log_mlb_recovered.csv` too.
