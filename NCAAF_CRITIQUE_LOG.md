# NCAAF critique log

Target metric: **DK points vs the pre-lock projection**, laddered players that
were logged (`data/dfs_proj_log_ncaaf.csv`) and drafted in a DK export on the
same draft group. Harness: `scripts/ncaaf_lab.py report [--cutoff]`.
Split: train = slates before 2026-10-11 (9/26 Night, 10/3 Main), test = every
slate since. The slate is the unit; 2 train slates is thin, say so with every fit.

Caveat on train: the 9/26 and 10/3 rows were logged by older code (pre suffix
fix, pre wrong-game fix). The log is what the app showed, so it is
live-faithful, but a correction fitted on train partly fits since-fixed bugs.

## Cycle 0 — baseline (2026-10-22), shipped ladder model

```
cutoff 2026-10-11: train 2 slates / 193 players, test 5 slates / 257 players
coverage (logged -> graded): 153951 68->63, 154161 136->130, 155010 128->127, 155105 17->16, 155109 30->28, 155112 27->27, 155122 74->59

TRAIN
  slice                     n     MAE    RMSE  bias(a-p)
  all                     193    7.85   10.50      +2.31
  QB                       35   10.72   12.62      +3.23
  RB                       48    8.37   11.75      +2.48
  WR                      110    6.72    9.08      +1.95
  $0k-5                    78    5.93    7.95      +1.48
  $5k-7                    81    8.10   10.97      +2.09
  $7k-+                    34   11.66   13.93      +4.76
  2026-09-26 153951        63    7.16   10.15      +3.88
  2026-10-03 154161       130    8.19   10.66      +1.55

TEST (held out)
  slice                     n     MAE    RMSE  bias(a-p)
  all                     257    6.17    8.15      +0.11
  QB                       49    6.60    8.48      -0.55
  RB                       61    7.14    9.51      +1.42
  WR                      147    5.63    7.39      -0.21
  $0k-5                    98    4.73    6.19      -0.14
  $5k-7                   108    6.67    8.56      +0.07
  $7k-+                    51    7.90   10.29      +0.67
  2026-10-17 155010       127    5.34    6.88      -0.57
  2026-10-14 155105        16    7.42    9.06      -2.36
  2026-10-15 155109        28    7.42   10.40      +2.27
  2026-10-16 155112        27    7.52    9.96      +2.62
  2026-10-17 155122        59    6.42    8.28      +0.07
```

## Cycle 1 — 2026-10-22: QB/RB projections too compressed — NOT SHIPPED

**Hole (critic):** for QB and RB the ladder pulls projections toward the
middle. The top third of RBs by projection beat it by +3.26 (train, n=16) and
+3.53 (test, n=21). Regressing actual on projection gives slopes above 1 in
both periods: QB 1.60 / 1.35, RB 1.00 / 1.30. WR is inconsistent (1.35 / 0.70).
Suspected cause: rush/rec TDs are imputed as a flat rate per yard
(`_ncaaf_impute`), which under-credits the highest-volume players.

**Fix tested:** `ncaaf_lab.py report --stretch` applies
p' = pivot + k(p − pivot) to QB and RB. The pivot is the train mean projection;
k is the slate-demeaned train OLS slope. Fitted values: QB (18.32, 1.62),
RB (12.02, 1.07).

| held out (5 slates) | n | MAE | RMSE | bias |
|---|---|---|---|---|
| all, shipped | 257 | 6.17 | 8.15 | +0.11 |
| all, stretch | 257 | 6.15 | 8.12 | +0.18 |
| $7k+, shipped | 51 | 7.90 | 10.29 | +0.67 |
| $7k+, stretch | 51 | 7.75 | 10.01 | −0.02 |

**Decision: not shipped.** The overall gain (−0.02 MAE) is inside the noise.
Two of the 5 test slates got worse (155105: 7.42→7.60, 155112: 7.52→7.65).
QB k = 1.62 rests on 35 train QBs logged by older code. A stronger hinge
version overshot on test (QB bias −1.68). The $7k+ result is a lead to recheck
once more slates are logged. The real fix belongs in the model: a TD rate that
rises with volume, refitted on cfbfastR.

**Runners-up:**
- Blending in DK FPPG (0.8 ladder + 0.2 FPPG): 5.68→5.65 leave-one-slate-out
  (n=186), but `dk_fppg` is missing on every train row, so it can't be fitted
  on train yet.
- Grading covers drafted players only (155122: 74 logged, 59 graded). Grade
  the undrafted from cfbfastR.
- Rush-attempt, pass+rush-yard and completion markets are collected in odds.db
  but not used. RB carries could shape the TD imputation. Needs a replay.

### Cycle 1 runner-ups, explored (2026-10-22) — all three NOT SHIPPED

Same split: train 9/26 + 10/3, test 5 slates from 10/14. The cfbfastR 2026
file was refreshed to week 7. Scripts are in the session scratchpad
(build/eval/pull/att.py) and are not committed, since nothing ships.

**Grading undrafted players from cfbfastR.** cfbfastR matches DK's FPTS to
MAE 0.81 on the 430 drafted players both sources have, so it is a valid stand-in.
A player whose team played but who has no cfbfastR row counts as 0.
Only 30 of 480 logged players were undrafted.

| | drafted only | all logged | undrafted |
|---|---|---|---|
| train | 193: MAE 7.85, bias +2.31 | 204: MAE 7.73, bias +1.91 | 11: MAE 5.63, bias −5.19 |
| test | 257: MAE 6.17, bias +0.11 | 275: MAE 6.08, bias +0.16 | 18: MAE 4.78, bias +0.84 |

Grading only drafted players does not flatter the model. The lab stays
DK-truth-only. The test undrafted bias of +0.84 rules out "laddered and
unpicked means a hidden zero".

**Blending in DK FPPG.** p = (1−w)·proj + w·FPPG. FPPG is DK's logged column
when present; otherwise it is rebuilt from cfbfastR as DK points per team game
before the slate week. The rebuild matches DK's column at r 0.961, MAE 0.97
(n=198). The rebuild makes a train fit possible: w = 0.35, train MAE
7.73→7.65.
Held out it **hurts**: 6.08→6.20, worse on 4 of 5 slates, and WR 5.55→5.78.
Leave-one-slate-out over all 7 slates is also worse: 6.79→6.81 (n=479,
w 0–0.2). The critic's leave-one-slate-out gain on test-only slates does not
survive an honest fit. Dead.

**Rush-attempt ladders for RBs.** DK's `player_rush_attempts` ladder was taken
from the last scan before kickoff. The feature is carries beyond what the
rush-yards ladder implies, att − (1.63 + 0.179·yds), fitted on train.
Correlation with the residual: train +0.035 (n=38, 2 slates), test −0.301
(n=41, 5 slates). The fitted term moves test MAE 7.81→7.85. Carries add nothing
the yards ladder hasn't already priced. This also weakens the critic's guess
that TDs-per-yard is the cause of the compression: if volume beyond yards
predicted TDs, extra carries would show up here.

## Cycle 2 (player name matching) — 2026-10-31: nicknames and slate-wide namesakes — SHIPPED

**Target:** the join from DK sportsbook ladder subjects ("Braylon McReynolds
(UAB)") to DK draftables, i.e. who the board can see at all. There was no
harness for this, so building it was part of the cycle:
`scripts/ncaaf_names_lab.py report [--cutoff] [--arch] [-v]`. It replays every
NCAAF draft group through production `join_ladders`, using the draftables as
of the last data-branch commit before lock and the last ok `dfs_ncaaf` scan
before lock (local copies of the F: archive for the older scans). The metric
per slate is join recall (joined / ladder subjects in slate-game events), the
misses by reason with projected points lost, and wrong joins (book team
suffix ≠ DK team). Split: train before 2026-10-11 (6 slates), test from then
on (19 slates; 155652 had not locked yet).

Two other joins were checked and are clean. Projection log → contest export:
every logged player missing from an export was undrafted, with no near-name
in the export. cfbfastR names feed grading and fitting only, not the board.

**Hole (critic):** the exact join keys on the normalised name across the
whole slate.
- **Nickname or full surname on DK's salary list:** the join misses
  (`no_key`). Cases: Bam / Braylon McReynolds, Rod Robinson II / Roderick,
  Ty Mims / Tiaquelin (UAB's backfield on 10/15, 10–16% owned), Makhilyn /
  MK Young, and Dominic Lee-Knicely / "Dominic Knicely". Lee-Knicely was a
  $6.8k RB projected 18.8 and 38.6% owned in the 10/16 GPP.
- **Namesake anywhere on the slate:** both players are dropped (Fredrick
  Moore of MSU and Alabama, ×3).

These two causes were 100% of the misses in both periods. Unpriced games
(13 game-slots across slates, 10 distinct games) have no DK ladders at all;
that is coverage, not names.

**Fix:** `dfs_run_ncaaf.alias_joins`, called by the new `join_ladders` that
`build_pool` and the lab share. It runs only on what the exact join left
out, and only inside the event's own game:
1. The book's team suffix is mapped to a DK team by vote of the exact joins.
   If one suffix and one DK team are both still unmatched, they are paired.
2. Last names must agree (a hyphen part counts).
3. Tier A: one first name is a prefix of the other (2+ letters). Tier B: same
   first initial, with one first name of ≤ `ALIAS_SHORT_FIRST` = 3 letters.
   Exactly one player on that team may pass.
4. A DK player who already joined exactly is never taken.
5. A passing-yards ladder must belong to a QB.
6. The aliases are listed on the page and in the CLI.

**Threshold decided on train (judgment call recorded):** the critic chose ≤ 3
after seeing the placebo on both periods. Redone on train only, the sole
initial-only true alias was MK (2 letters), and all 4 placebo false joins
had first names of 4+ letters (King/Kayne, Jeremy/James, Dakorien/Dante,
Dylan/Dayton). So 2 or 3 both pass train; 3 is the midpoint. On test, 2
would have lost Bam.

| | train (6 slates) | test (19 slates) |
|---|---|---|
| subjects | 489 | 931 |
| recall, shipped → fix | 0.9959 → **1.000** | 0.9925 → **1.000** |
| missed (no_key / ambiguous) | 2 (1 / 1) → 0 | 7 (5 / 2) → 0 |
| projected pts lost | 13.1 → 0 | 67.2 → 0 |
| wrong joins | 0 → 0 | 0 → 0 |
| placebo false joins (production code) | 0 / 462 | 0 / 949 |

Placebo = remove each exactly joined player's DK row and check whether the
alias step hands his ladder to someone else.

Exact joins are unchanged on every slate: the baseline join count plus the
aliases equals the new join count. The test misses fell on 5 of 19 slates.
Only 9 aliases exist in all, so that is the n.

**Runners-up:**
- The 50% rule in `event_games` counts only exact joins. An event with 2 or
  more aliases could fall below 50% and lose its whole game. Not observed
  (worst: UAB 3 of 16).
- Before this cycle, the lab's "nearest DK name" for a miss ignored the team.
  It now restricts to the suffix's team.
- The 13 unpriced game-slots: the only coverage lever is a second book
  (FanDuel's two-sided props, STATUS §10).

## Cycle 3 — 2026-11-07: DK points vs projection, new split

**Split re-drawn:** cycle 0 trained on 2 slates. With 11 graded draft groups
(log + DK export; 10/24 Main and 10/31 Main have no export), train = before
2026-10-17 (5 slates, 264 players), test = 10/17 on (6 slates, 351 players).

Baseline, shipped model (`ncaaf_lab.py report --cutoff 2026-10-17`):

```
cutoff 2026-10-17: train 5 slates / 264 players, test 6 slates / 351 players
coverage (logged -> graded): 153951 68->63, 154161 136->130, 155010 128->127, 155105 17->16, 155109 30->28, 155112 27->27, 155122 74->59, 155277 118->81, 155281 28->28, 156213 20->20, 156216 38->36

TRAIN
  slice                     n     MAE    RMSE  bias(a-p)
  all                     264    7.75   10.35      +2.06
  QB                       48   10.15   12.15      +2.51
  RB                       63    8.65   11.99      +3.10
  WR                      153    6.62    8.91      +1.49
  $0k-5                   108    6.03    8.10      +1.12
  $5k-7                   107    8.06   10.67      +2.14
  $7k-+                    49   10.83   13.57      +3.95
  2026-09-26 153951        63    7.16   10.15      +3.88
  2026-10-03 154161       130    8.19   10.66      +1.55
  2026-10-14 155105        16    7.42    9.06      -2.36
  2026-10-15 155109        28    7.42   10.40      +2.27
  2026-10-16 155112        27    7.52    9.96      +2.62

TEST (held out)
  slice                     n     MAE    RMSE  bias(a-p)
  all                     351    6.03    7.93      +0.30
  QB                       63    6.78    8.32      +0.54
  RB                       91    6.15    8.11      -0.16
  WR                      197    5.74    7.73      +0.44
  $0k-5                   129    4.67    5.93      +0.27
  $5k-7                   146    6.50    8.76      +0.48
  $7k-+                    76    7.45    9.14      +0.02
  2026-10-17 155010       127    5.34    6.88      -0.57
  2026-10-17 155122        59    6.42    8.28      +0.07
  2026-10-24 155277        81    6.77    8.71      +0.26
  2026-10-22 155281        28    4.64    6.55      +0.58
  2026-11-05 156213        20    7.55    9.82      +0.71
  2026-11-06 156216        36    6.43    8.77      +3.38
```

**Hole (critic):** a QB's rushing TDs are imputed as rush yards ÷ 110
(`edge/dfs_sport.py` `_ncaaf_impute`, `NCAAF_RUSH_TD_PER_YARD_QB`). The prior
for a QB with no rush ladder is 11.8 yds/g. College QB rush TDs follow the
goal-line role, not the yardage, so runners came out low and scramblers high.
This is part of the QB compression cycles 0–1 saw but could not explain.

Evidence: all 11 slates were replayed through production `build_pool` at the
last scan before lock. The replay reproduces the logged projection on 9 of
11; 9/26 and 10/3 were logged by older code. Season rush-TD points per team
game ran 4.32 vs 2.47 imputed on train and 3.82 vs 2.11 on test, and the gap
correlated with the residual in both periods (r +0.19 / +0.26). A train
regression put the QB residual on rush TDs (weight 1.03–1.11), not on rush
yards (0.17 / −0.76).

**Fix:** `ncaaf_lab.py report --qb-rush-td`. For a QB with ≥ 2 team games:
`pred = proj − imputed rush-TD pts + 6 × TDs/G`, where the rate is
`edge.ncaaf.qb_rush_td_rates` over the cfbfastR weeks before the slate (the
same function the build calls).
- **Slate week is read from the data:** the week whose box-score DK points
  match the export's FPTS (MAE 0.11–2.04). A slate after the file's last week
  takes last week + 1.
- **Names:** a name two players share is skipped. His team is the majority
  over his games.
- **cfbfastR refreshed** from week 7 to week 9 before the test, as production
  will be.

**Judgment calls, recorded:**
- k (shrinkage toward the imputed rate) was chosen on train from
  {0, 1, 2, 3, 4, 6, 8}: k = 0 was best (7.7019 vs 7.7036 at k=1).
- The critic suggested k = 1–2 because held-out QB bias overshoots (−0.34).
  That reasoning uses the test set, so it is not taken.
- My first version also dropped any QB cfbfastR showed on more than one team.
  That rule matched 18 of 48 train QBs, because cfbfastR's per-row team label
  is noisy for QBs. I replaced it with the majority-team rule before reading
  test numbers for the decision.

```
slate week (cfbfastR, MAE vs DK FPTS, n): 153951 w4 0.72/60, 154161 w5 0.55/122, 155010 w7 0.51/124, 155105 w7 0.48/15, 155109 w7 0.89/26, 155112 w7 2.04/25, 155122 w7 0.72/59, 155277 w8 0.62/79, 155281 w8 0.11/24, 156213 w10 nan/0, 156216 w10 nan/0
train MAE by k: 0: 7.7019, 1: 7.7036, 2: 7.7048, 3: 7.7056, 4: 7.7065, 6: 7.7088, 8: 7.7114 -> k=0
QBs re-rated: train 46 / 48, test 63 / 63
cutoff 2026-10-17: train 5 slates / 264 players, test 6 slates / 351 players
coverage (logged -> graded): 153951 68->63, 154161 136->130, 155010 128->127, 155105 17->16, 155109 30->28, 155112 27->27, 155122 74->59, 155277 118->81, 155281 28->28, 156213 20->20, 156216 38->36

TRAIN
  slice                     n     MAE    RMSE  bias(a-p)
  all                     264    7.70   10.26      +1.86
  QB                       48    9.92   11.71      +1.42
  RB                       63    8.65   11.99      +3.10
  WR                      153    6.62    8.91      +1.49
  $0k-5                   108    6.03    8.10      +1.12
  $5k-7                   107    8.09   10.63      +2.03
  $7k-+                    49   10.54   13.26      +3.12
  2026-09-26 153951        63    7.13   10.04      +3.54
  2026-10-03 154161       130    8.10   10.49      +1.30
  2026-10-14 155105        16    7.44    9.07      -2.51
  2026-10-15 155109        28    7.42   10.50      +2.29
  2026-10-16 155112        27    7.59   10.03      +2.77

TEST (held out)
  slice                     n     MAE    RMSE  bias(a-p)
  all                     351    5.93    7.86      +0.14
  QB                       63    6.19    7.94      -0.34
  RB                       91    6.15    8.11      -0.16
  WR                      197    5.74    7.73      +0.44
  $0k-5                   129    4.66    5.92      +0.29
  $5k-7                   146    6.42    8.69      +0.43
  $7k-+                    76    7.14    9.00      -0.66
  2026-10-17 155010       127    5.23    6.80      -0.70
  2026-10-17 155122        59    6.24    8.20      -0.10
  2026-10-24 155277        81    6.67    8.56      -0.01
  2026-10-22 155281        28    4.72    6.81      +0.66
  2026-11-05 156213        20    7.51    9.81      +0.65
  2026-11-06 156216        36    6.27    8.65      +3.19
```

With 10/17's two draft groups counted once (`--dedup`; 58 of 155122's 59
players are on 155010, one realisation), test n=293: MAE 6.01 → 5.92, QB
6.89 → 6.37.

| held out (6 slates) | n | before | after |
|---|---|---|---|
| all MAE / RMSE / bias | 351 | 6.03 / 7.93 / +0.30 | **5.93** / 7.86 / +0.14 |
| QB MAE / bias | 63 | 6.78 / +0.54 | **6.19** / −0.34 |
| $7k+ MAE | 76 | 7.45 | 7.14 |
| deduplicated, all | 293 | 6.01 | 5.92 |

Worse on one slate only: 155281, 4.64 → 4.72 (28 players). 156213 was
7.55 → 7.51.

**Decision: SHIPPED.**
- `dfs_run_ncaaf.QB_RUSH_TD_*` + `load_qb_rush_rates` (via repo_files);
  `project_player(rush_td_mean=)`.
- Each player records `rush_td_source`; the build's stats record
  `qb_rush_td_season` / `qb_rush_td_source`, and the page and CLI show them.
- The table comes from `scripts/ncaaf_qb_rates_publish.py`, run daily by
  `ncaaf-qb-rates.timer`.
- **Parity:** production `build_pool` with the week-10 table equals the lab's
  prediction on 156216 and 156213 (56 players, within the 0.1 rounding).
- **Tonight's Night slate (156227):** 6 of 46 players changed, all QBs.
  Chambliss went 20.5 → 24.5 (7 TD / 8 g); Mateer, Fette and Marchiol each lost
  0.7–1.2 (0 TD).
- **Tests:** tests/test_dfs_ncaaf.py, 4 new.

**Runners-up:**
- The lab double-counts same-day draft groups. `--dedup` was added to report
  it; the default is unchanged so cycles stay comparable.
- QB compression remains (slope ~1.45 after the fix). Retry the cycle-1
  stretch on top of this.
- RB receiving from each back's own season rate: no real gain (test
  6.08 → 6.03, n=71). Not worth it.

## Cycle 4 (QB) — 2026-11-07: quarterback DK points vs projection

**Target:** QB DK points vs the pre-lock projection, held out: MAE, RMSE,
bias, and Spearman rho per slate (a QB pick is mostly a ranking). Same split
as cycle 3 (train before 10/17: 5 slates, 48 QBs; test 6 slates, 63 QBs).
Baseline = the shipped model, which now includes cycle 3:
`ncaaf_lab.py report --cutoff 2026-10-17 --qb-rush-td 0 --pos QB`.
`--pos` filters after the predictor is built. Filtering first broke the
slate-week read on 3-QB slates and leaked: QB MAE showed 6.15 instead of 6.19.

```
cutoff 2026-10-17: train 5 slates / 48 players, test 6 slates / 63 players
coverage (logged -> graded): 153951 68->63, 154161 136->130, 155010 128->127, 155105 17->16, 155109 30->28, 155112 27->27, 155122 74->59, 155277 118->81, 155281 28->28, 156213 20->20, 156216 38->36

TRAIN
  slice                     n     MAE    RMSE  bias(a-p)    rho
  all                      48    9.92   11.71      +1.42   0.49
  QB                       48    9.92   11.71      +1.42   0.49
  $5k-7                    18   10.16   11.86      +1.06   0.20
  $7k-+                    30    9.77   11.62      +1.64   0.49
  2026-09-26 153951        12    9.02   10.37      +1.66   0.49
  2026-10-03 154161        23   11.01   12.63      +1.71   0.58
  2026-10-14 155105         3    9.24   11.21      -9.24   0.50
  2026-10-15 155109         6   10.59   13.16      +2.27   0.31
  2026-10-16 155112         4    5.81    7.25      +5.81  -1.00

TEST (held out)
  slice                     n     MAE    RMSE  bias(a-p)    rho
  all                      63    6.19    7.94      -0.34   0.67
  QB                       63    6.19    7.94      -0.34   0.67
  $0k-5                     5    5.45    5.52      -2.83   0.15
  $5k-7                    27    5.98    7.59      -1.02   0.44
  $7k-+                    31    6.50    8.54      +0.66   0.57
  2026-10-17 155010        23    4.98    6.75      -1.50   0.55
  2026-10-17 155122        13    5.50    7.59      -2.05   0.62
  2026-10-24 155277        14    7.83    8.73      -0.48   0.82
  2026-10-22 155281         4    8.90   12.21      +4.34   0.80
  2026-11-05 156213         3    5.01    6.01      -1.46   1.00
  2026-11-06 156216         6    7.29    8.14      +5.62  -0.09
```

**Hole (critic):** the passing part of a QB projection (pass yds + pass TD +
INT points) is spread about half as widely as outcomes between quarterbacks.
Slate-demeaned, actual on the joint passing and rushing projection: passing
2.00 (se 0.57) train / 1.63 (0.31) test; rushing 1.25 / 1.17. The held-out
error rises 0.6–0.9 points per point of projected passing above the slate
mean, and which part of the error carries it differs by period: rush TDs on
train, pass TDs on test. Mechanism: each ladder is devigged on its own (flat
`LADDER_OVERROUND`, edge/dfs_ladder.py) and the parts are summed
independently, so nothing carries team or offence strength across them.

**Fix tested:** `ncaaf_lab.py report --qb-rush-td 0 --qb-pass-stretch`:
pred = shipped + k (pass_pts − pivot), with k = the train slope of the
slate-demeaned residual on slate-demeaned pass_pts and pivot = the train mean.
Train (46 QBs, 5 slates): **k = 1.047 (se 0.60), pivot 13.50**. The critic's
numbers were reproduced exactly. The lab replay cache now keeps every
component, seeded from cycle 3's production replays.

```
QB pass stretch (train, 46 QBs / 5 slates): k=1.047 pivot=13.50
cutoff 2026-10-17: train 5 slates / 48 players, test 6 slates / 63 players
coverage (logged -> graded): 153951 68->63, 154161 136->130, 155010 128->127, 155105 17->16, 155109 30->28, 155112 27->27, 155122 74->59, 155277 118->81, 155281 28->28, 156213 20->20, 156216 38->36

TRAIN
  slice                     n     MAE    RMSE  bias(a-p)    rho
  all                      48    9.32   11.40      +1.42   0.60
  QB                       48    9.32   11.40      +1.42   0.60
  $5k-7                    18   10.07   11.99      +3.46   0.33
  $7k-+                    30    8.87   11.03      +0.20   0.66
  2026-09-26 153951        12    8.50    9.91      +0.46   0.62
  2026-10-03 154161        23   10.33   12.51      +2.19   0.65
  2026-10-14 155105         3    9.29   11.82      -9.21   0.50
  2026-10-15 155109         6    9.75   11.70      +3.10   0.43
  2026-10-16 155112         4    5.37    7.37      +5.37  -1.00

TEST (held out)
  slice                     n     MAE    RMSE  bias(a-p)    rho
  all                      63    6.01    7.77      -0.46   0.65
  QB                       63    6.01    7.77      -0.46   0.65
  $0k-5                     5    3.41    4.78      +0.54   0.15
  $5k-7                    27    6.23    8.06      -0.08   0.34
  $7k-+                    31    6.23    7.91      -0.96   0.65
  2026-10-17 155010        23    5.67    7.52      -1.52   0.53
  2026-10-17 155122        13    5.52    7.68      -1.62   0.55
  2026-10-24 155277        14    6.32    7.81      -0.16   0.86
  2026-10-22 155281         4    8.29   10.76      +5.05   0.80
  2026-11-05 156213         3    5.55    6.20      -5.55   1.00
  2026-11-06 156216         6    6.34    7.14      +4.23   0.43
```

| held out (QB) | n | MAE | RMSE | pooled rho |
|---|---|---|---|---|
| shipped (cycle 3) | 63 | 6.19 | 7.94 | 0.67 |
| + pass stretch | 63 | 6.01 | 7.77 | 0.65 |
| without 10/24 (155277), shipped → stretch | 49 | 5.72 → **5.92** | | |

**Decision: NOT SHIPPED.**
- **One slate carries the gain.** Better on 3 of 6 slates (3 of 5 independent
  nights). All of the improvement comes from 155277 (7.83 → 6.32, 14 QBs).
  Without it, held-out QB MAE gets worse: 5.72 → 5.92.
- **The ranking does not clearly improve.** Pooled rho falls 0.67 → 0.65. Per
  slate it rises on two (155277 0.82 → 0.86, 156216 −0.09 → 0.43), falls on
  two (155010 0.55 → 0.53, 155122 0.62 → 0.55) and is flat on two.
- **k is uncertain:** 1.7 se from zero, on 46 train QBs.
- **Same pattern as cycle 1's stretch** (−0.02 overall, 2 of 5 slates worse),
  which was also not shipped. Re-run `--qb-pass-stretch` when more graded
  slates exist. The structural version is a team-strength factor shared by the
  pass-TD and rush-TD parts, and it needs a working implied total (runner-up 3).

**Runners-up (critic):**
1. **QB rush-yards ladders run low.** Rungs priced at a devigged 0.40–0.60
   hit 66% on test (n=35) and 67% on 9/26 (n=15), against 47% expected.
   Laddered QBs' rush-yards points run +1.08 (test) / +1.65 (train) short.
   A flat +5 or +10 yards does not lower MAE (test 6.17–6.18, train worse)
   because the error is skewed. It needs a QB-specific rush overround or head
   fill, fitted on the rungs.
2. **`COUNT_TAIL_EXCESS["player_pass_tds"] = 0.000` is a fitting artefact.**
   Ladders stop at 3+ or 4+. In cfbfastR, the extra passing TDs beyond 3,
   given at least 3, average 0.41 (2024) and 0.48 (2025). Correct but small:
   test 6.191 → 6.188.
3. **The replayed pool's game `total` is not the full-game line** (21.5,
   25.5, 27.5 seen) and `spread_home` is None on every slate. It is display
   only, but it blocks any implied-total feature. Worth fixing before a
   team-strength term is tried.

Critic's files: scratchpad critic-ncaaf-4/ (pass_stretch_table.txt,
resid_slope_by_component.txt, rung_calibration.txt, passtd_tail.txt,
variants.txt, qb.csv).

### Cycle 4 runner-ups, tested (2026-11-07): one bug fix shipped, three modelling changes NOT SHIPPED

The lab can now rebuild every projection from each slate's raw ladders
(`--reproject`: production `project_player` on cached ladders, with today's
code and the cycle-3 QB rate). Cache: `data/harness/ncaaf_lab/ladders_<gid>.json`.
All 11 slates were regenerated from the scans at lock; F: archive copies are
read via `NCAAF_LAB_ARCH`. The re-projection reproduces the shipped
predictor within 0.1 on the 9 slates logged by current code. 9/26 and 10/3
differ because older code logged them, so these comparisons use the
re-projected baseline: test QB MAE 6.19, RMSE 7.93, rho 0.67 (n=63).

**1. "QB rush-yards ladders run low" was a measurement artefact.**
- cfbfastR leaves sacks out of QB rushing (its 4,076 sack rows have no
  rush_player). DK counts them, as NCAA stats do.
- Per QB (n=100), DK FPTS minus cfbfastR DK points: −0.108 per yard sacked
  (`sack_taken_stat` is yards lost), intercept −0.07. QBs who were not sacked:
  +0.20. RB +0.17 and WR −0.08 agree.
- With sack yards subtracted, rungs devigged near 0.50 hit 0.41 (train) and
  0.40 (test). Without them they looked like 0.63 / 0.68. If anything, QB rush
  ladders run high.
- A QB-only rush overround fitted on train rungs by Brier is 0.30 (test rungs
  alone: 0.21; shipped 0.102). Held-out QB MAE 6.19 → 6.21, RMSE 7.93 → 7.95,
  bias −0.33 → −0.11. **Not shipped:** flat.
- Caveat for every cfbfastR-graded QB number: cfbfastR QB points run about 1.4
  high against DK (2 sacks a game).

**2. Pass-TD tail.** Measured on 2024+2025 cfbfastR (QB-games with 10+
attempts), E[N−k | N≥k] = 0.59 / 0.44 / 0.27 / 0.12 at k = 2 / 3 / 4 / 5.
Weighted by the train ladders' top rungs (2: 9, 3: 19, 4: 18) that is 0.40,
against the shipped 0.000.
- Train QB MAE 9.89 → 9.84. Test 6.19 → 6.20, RMSE 7.93 → 7.96, bias −0.33 →
  −0.53.
- **Not shipped:** the constant is wrong on the data, but changing it does
  not help the held-out projection.

**3. Game lines (SHIPPED as a bug fix, f79d654f), then the implied team total (NOT SHIPPED).**
- `game_lines` took the first `totals` point from any book. With the
  full-ladder client that is an alternate rung (DK 36.5 on a 56.5 game), and
  the spread was never filled.
- `main_line()` now takes the pair priced closest to even, DraftKings first,
  with spreads paired at the negated point. Against the main-line client:
  104 of 106 equal; the other 2 are one point apart.
- Replays now show totals 40.5–72.5 and every spread.
- Implied team total as a QB term (`--qb-implied`): k = 0.501 per point,
  pivot 26.55, fitted on train (46 QBs). Held out: MAE 6.19 → **6.35**, RMSE
  7.93 → 8.20, rho 0.67 → 0.62; worse on 4 of 6 slates. **Not shipped:** the
  ladders already price the team's scoring.
- Caveat: the spread is read from the book's home team. At a neutral site the
  book and DK could disagree on home; not checked.

## Cycle 5 — 2026-11-07 afternoon: four parallel critics. Nothing new shipped, so the 3:30 and 7:30 slates use cycle 3 plus the game-line fix

Four agents, read-only, each in its own area. Same split as cycles 3–4
(train before 10/17: 5 slates; test from 10/17: 6 slates, of which 155010
and 155122 are one day). Baseline = `ncaaf_lab.py report --reproject`.

**RB/WR projections (dfs-model-critic). Null.** Test RB n=91 MAE 6.151,
WR n=197 MAE 5.743.
- **The scorecard's +1.1 under-projection is not a fixable hole.** It is
  period drift plus older logged code: RB/WR bias is +1.72 on train and +0.25
  on test, and the test WR median residual is −0.10. A train-fitted shift
  takes test RB MAE 6.15 → 6.75 and WR 5.74 → 5.93; a ×1.16 scale gives
  6.79 / 5.99.
- **WR R² 0.16 is near its ceiling.** Var(proj)/var(actual) is 0.13 / 0.21.
  The fitted slope flips (1.33 train, 0.81 test), and a salary term hurts
  (5.74 → 5.89).
- **Implied team total, game total and pace add nothing.** They flip sign
  between periods, with |r| ≤ 0.16, using the corrected lines.
- **Ladder overrounds refitted to outcomes:** RB 6.15 → 6.14, WR 5.74 → 5.79.
- **Closest miss: RB receiving.** Imputed receiving runs 1.1–1.2 pts per back
  low in both periods, but a level fix hurts held out (6.15 → 6.33) because RB
  rushing ran high on test. Blending in each back's own season receiving rate
  at w=0.3 gives test RB 6.151 → 6.047 (better on 4 of 6 slates, 2 flat), but
  the train curve is flat (2.641 vs 2.662) and train RMSE is slightly worse.
  Not shipped; re-test after 2–3 more graded slates.
- **Season TD rates for RB/WR** (cycle 3's idea): the sign flips. It does not
  carry over from QBs.
- **Where the error is:** the top 20 of 288 test misses carry 45% of squared
  error, all single big games.

**Availability (dfs-availability-critic). Real gap, but no measured gain on the board.**
- **The gap:** `build_pool` reads neither DK's injury flag (`dk_status`, which
  NBA, NHL and NCAAB use) nor the conference reports.
- **Play rate by report status**, established Power-4 skill players, train /
  test:
  - Update 2 Doubtful: 0/21 / 0/24
  - Update 2 Out: 6% / 6%
  - Game Day Out: 5% (n=153) / 5% (n=252)
  - Update 2 Questionable: 0.42 / 0.33
  - Game Day GTD: 0.45 / 0.27
  - Probable: 0.68 / 0.65
- **The Out/Doubtful zero rule moves nothing held out.** On the graded board
  it flags **0 of 264 train and 0 of 351 test rows**, whichever report day is
  used: books pull an Out player's ladder, so he never reaches the board. Test
  false actives (7, 57 projected pts) were 10 of 17 G5 or non-conference
  (no report) and mostly undrafted. Not shipped: it does not win held out.
- **An FPPG fallback for off-board players** (0.537 × FPPG, gated by DK status
  and the reports): off-board MAE 5.13 → 4.88 (n=47, 6 slates). The gate
  removed 21 of 21 non-players. But k is unstable (actual/FPPG 0.52 train vs
  0.78 test), and it changes the pool, not the graded metric. Not shipped.
- **Promoted chalk we could not see:** Trelain Maddox (GT, 10/17, 27–39%
  owned, 19.2 pts) behind Justice Haynes, who was Doubtful on Thursday and
  DK "D" 19 h before lock.
- **Post-lock Game Day reports:** Patterson 10/24 and Payne 11/6 were public
  1.5–2 h after lock and before kickoff. Late swap is not wired for NCAAF.
  We had 0 exposure both times.

**GPP ownership (dfs-model-critic, GPP only, largest field per draft group). Lead, not shipped.**
- **Where it fails:** on 2–3 game slates the field drafts by raw projection
  (studs at 68–90%), while our value softmax caps at 55% and over-owns cheap
  value.
- **The fit:** short-slate constants fitted on the 3 short train slates (value
  gamma 0, FPPG weight 0.4, projection weight 0.9, cap 70).
- **Held out:** the 3 short test slates go 0.574 → 0.531 misplaced, pool MAE
  10.5 → 9.1. Better on 2 of 3; 156216 is flat to worse (0.643 → 0.652).
- **Not enough data (3 test slates).** It also would not touch today's
  12-game and 7-game slates. Re-test as weekday slates are graded.
- **Small-field noise:** fields under ~150 put a sampling floor of 0.17–0.30
  under misplaced. Weight slates by field size in the next ownership refit.

**External expert signals (dfs-model-critic). Null.**
- **Laddered players:** pace, matchup pace, pass rate, DvP, yards per play
  for and against, a season rating minus the line (a Sagarin/SP+ proxy),
  usage, last-3 usage and the implied total each moved test MAE by −0.02 to
  +0.08. LOSO over 11 slates: within ±0.02.
- **Unladdered drafted players:** usage + salary scores 4.50 vs 5.80 for zero
  (n=255, all 6 test slates). Adding external signals changes that by ±0.01.
- **Not testable today:** SP+, FPI and FEI history (needs a CFBD key), weather
  history, and the paid PFF / ETR / Stokastic. The new captures (reports,
  depth charts, FanDuel props) start today.

**Collect next:**
- The missing DK exports for 10/24 Main and 10/31 Main: logged, but not
  gradable without them.
- Pre-lock draftables versions with DK status, per build: only 1–11 per draft
  group survive, so lead time is not measurable before 10/30.
- The new captures (availability reports, Ourlads, FanDuel props) through
  5+ slates.
- Short weekday slates' GPP exports, for the ownership lead.
- A CFBD key (Tier 1, $1/month) if weather or ratings history is wanted.


## Cycle 6 — 2026-11-08 (unattended): RB receiving from the back's own season — committed for the gate

**Split:** same as cycles 3–5. Train = before 2026-10-17 (5 slates, 264
players). Test = 10/17 on, now **7 slates / 474 players**. 155990 (11/7 Main,
123 players) is newly graded. 155010 and 155122 are the same day.

**Baseline** (`ncaaf_lab.py report --reproject --no-rb-rec --cutoff 2026-10-17`,
= main): train MAE 7.65 / RMSE 10.19; test MAE 6.03 / RMSE 7.97 / bias +0.39 /
rho 0.54. By position: QB 6.51 (n=85), RB 6.51 (n=123), WR 5.65 (n=266).

**Hole (dfs-model-critic):** 100 of 123 test backs and 58 of 63 train backs
have a rush ladder and no receiving ladder. For them, receptions and receiving
yards come from rush yards by one league line (`NCAAF_RB_REC_*`, R² 0.29 / 0.22).
For laddered backs that line runs ~40% low, and the bias is stable across
periods:

| period | imputed rec pts | actual rec pts | ratio | n backs | slates |
|---|---|---|---|---|---|
| train | 2.29 | 3.79 | 1.66× | 58 | 5 |
| test | 2.15 | 3.67 | 1.71× | 87 | 6 |

The residual tracks the back's own season receiving, slate-demeaned, with the
same sign in both periods:

| season feature | train r | test r |
|---|---|---|
| receiving yards per game | +0.12 | +0.30 |
| targets per game | +0.11 | +0.22 |
| red-zone targets per game | +0.10 | +0.34 |

A level fix (+1.51, or ×1.66) improves test RMSE but worsens test MAE
(6.03 → 6.07–6.08), as cycle 5 found. The role is the missing information, not
the level.

**Fix:** for an RB with no receiving ladder and ≥ 2 team games before the
slate's cfbfastR week:

    rec  = (G·rec/G  + K·imputed rec)  / (G+K)
    recy = (G·recy/G + K·imputed recy) / (G+K)

Receiving TDs follow the yards, as before. **K = 4**, from the train grid.
Production: `dfs_run_ncaaf.rb_receiving_means` / `RB_REC_SHRINK_GAMES`. The
season rows are `ncaaf.qb_rush_td_rates` (new `rec_rate` / `rec_yds_rate`), so
the lab and the build share one table and one week cut.

K grid, re-run by me (MAE / RMSE):

| K | train MAE | train RMSE | test MAE | test RMSE | test RB MAE |
|---|---|---|---|---|---|
| 0 | 7.682 | 10.214 | 5.962 | 7.839 | 6.260 |
| 1 | 7.663 | 10.208 | 5.969 | 7.852 | 6.286 |
| 2 | 7.652 | 10.203 | 5.975 | 7.864 | 6.310 |
| **4** | **7.646** | 10.200 | 5.984 | 7.881 | 6.346 |
| 8 | 7.647 | 10.197 | 5.996 | 7.905 | 6.389 |
| 16 | 7.650 | 10.195 | 6.005 | 7.928 | 6.426 |
| ∞ (main) | 7.653 | 10.191 | 6.027 | 7.974 | 6.511 |

Judgment calls:
- K = 4 is the train-MAE minimum, but K = 8 is a near-tie (7.6465).
- Train RMSE prefers no blend at all; the train evidence is weak.
- Test prefers K = 0. It was not used.
- The critic picked RB receiving after screening ~12 candidates on both
  periods: a test peek in choosing the target, flagged.

**Held out, before → after** (production through the lab; parity with the
critic's prototype is exact: 6.027 → 5.986):

| slice | n | MAE | RMSE | bias | rho |
|---|---|---|---|---|---|
| all | 474 | 6.03 → **5.98** | 7.97 → 7.88 | +0.39 → +0.37 | 0.54 → 0.55 |
| RB | 123 | 6.51 → **6.35** | 8.76 → 8.43 | +0.63 → +0.54 | 0.56 → 0.60 |
| QB / WR | 85 / 266 | unchanged | | | |
| 155010 10/17 | 127 | 5.23 → 5.16 | 6.80 → 6.71 | | |
| 155122 10/17 | 59 | 6.23 → 6.14 | | | |
| 155277 10/24 | 81 | 6.67 → 6.71 | | | |
| 155281 10/22 | 28 | 4.72 → 4.63 | | | |
| 155990 11/7 (new) | 123 | 6.31 → 6.24 | 8.28 → 8.08 | | |
| 156213 11/5 | 20 | 7.51 → 7.54 | | | |
| 156216 11/6 | 36 | 6.28 → 6.31 | | | |

- Better on 4 of 7 test slates. Each of the 3 losses is ≤ 0.04.
- Counting 10/17 once (n=415): 5.998 → 5.963.
- Train MAE 7.653 → 7.646. Better on the two large slates (9/26, 10/3), worse
  on the three small weekday ones.

**Gate preview** (`ncaaf_lab.py gate`: cutoff 10/11, 545 rows / 10 slates,
absolute error): −0.027 per row. The 90% slate-bootstrap interval is
[−0.054, +0.015], so this is **not beyond noise**, and I expect the gate to
fail it. The point estimate improves on both test metrics, so it is committed
and the gate decides. The 10/14–10/16 weekday slates, which are in the gate's
primary set, are each slightly worse (+0.06 to +0.10).

**Decision:** committed on the branch:
- production change, with provenance comments;
- the lab's `--reproject` now passes the season row;
- new flags `--no-rb-rec` and `--rb-rec-k`;
- tests;
- STATUS §13;
- the page and CLI show how many backs used their season.

Mirrors were NOT regenerated: unattended mode does not run mirror jobs. Run
`python3 -m harness run mirror-ncaaf` if this merges. The published rate
table carries no `rec_rate` until `ncaaf-qb-rates.timer` reruns. Until then
production keeps the old imputation (visible: "RB receiving from their
season: 0").

**Runner-ups (critic):**
1. **The gate scores MAE alone.** A mean correction (the level fix: RMSE
   better, MAE worse) can never pass. DFS lineups sum means, so the gate
   should also report RMSE. A person decides: the gate is a protected file.
2. **Cycle 4's QB pass stretch, rechecked with 155990.**
   - Refit on train: k = 0.60.
   - QB test MAE 6.506 → 6.444, but 155990 gets worse (7.42 → 7.71), and so
     does 155010.
   - Still one slate's gain (155277). Not shipped.
3. **The lab's `truth()` keys on the normalised name only.**
   - 155990 has two Austin Simmons, a QB (17.98) and a WR (0.0); whichever is
     read last wins.
   - Right today by luck (1 row). Key it on name + position or DK id. It is
     the judge's file, so this goes to Adam.

Null (|r| ≤ 0.2 or sign flips): implied team total as a TD multiplier; team
WR yards vs QB pass yards; season rush yards for QBs with no rush ladder; WR
receptions per yard from the player's season (−0.008); unpriced-head share;
rung calibration after overround removal.

Unattended gate (20261108-1255): **fail**

| split | state | n | clusters | main MAE | candidate MAE | loss diff [90%] | verdict |
|---|---|---|---|---|---|---|---|
| primary | all | 545 | 10 | 6.2173 | 6.1902 | -0.0272 [-0.0544, +0.0155] | noise |
- no primary state better beyond noise

## Cycle 7 (auto: cash lineups) — 2026-11-09 (unattended): built the cash-lineup harness; salary calibration NOT SHIPPED

**Target (dfs-target-scout):**

```json
{"target": "build cash lineup harness",
 "metric": "replayed production CASH lineup per held-out board, scored with DK FPTS vs that draft group's real double-up fields: cash rate (>= last paid score), field percentile; secondary mean points; GPP separately, never pooled",
 "harness": "build: scripts/ncaaf_lineup_lab.py (modelled on nba_lineup_lab.py), with parity vs the logged cash lineup and a lodo subcommand",
 "split": "leave one slate out over the cash draft groups with a logged board + cash export (154582 has no logged board: excluded)",
 "why": "cash ROI -63% [-98%, -27%], 12 entries / 9 slates, 25% cashed; Z_CASH and the cash objective never backtested; ncaaf_lab.py scores players only",
 "money_at_stake": "NCAAF cash net -$8.77 on 12 entries; 11/5-11/7 each lost 100%",
 "runner_ups": ["gate scores MAE only (protected file)", "QB projections low (bias +1.6), top-salary QBs without ladders", "GPP lineups in the same lab"]}
```

**The harness (this cycle's work):** `scripts/ncaaf_lineup_lab.py`.

- `parity`: the replay equals the logged lineup on **29 of 29 boards, cash and
  GPP**, at 700 and at 350 iterations.
- `report [--z]`: field percentile, cash line, $ at the rank taken, and Adam's
  real entry beside the replay.
- `lodo`: Z_CASH chosen leave-one-slate-out.
- `calibrate [--forward]`: the cycle's candidate.
- `gate OUT`: one row per (mode, contest), pred = 1 − field percentile, state =
  mode, cluster = date. Adam decides whether it joins `critique_gate`.

Builds are cached in `data/harness/ncaaf_lineup_lab/`, keyed on the board and
on the source of the modules the build runs.

**Baseline (Cycle 0 of this target):**

| lineup | contests | slates | cashed | field pctile | mean pts | ROI |
|---|---|---|---|---|---|---|
| shipped cash (z 0.75) | 11 | 8 | 18% | 0.253 | 134.1 | −69% |
| Adam's real cash entries | 11 | 8 | 18% | 0.257 | 137.4 | −69% |
| shipped GPP | 13 | 11 | 23% | 0.487 | 137.4 | +46% |
| Adam's real GPP entries | 13 | 11 | 23% | 0.556 | 146.1 | +172% |

Excluded: 10/10 (dg 154582), which has no logged board. Every lineup player
had a DK score. 11/7 holds 3 of the 11 cash contests on one lineup.

**Z_CASH is not the lever.**

- Chosen leave-one-slate-out from {0, .25, .5, .75, 1, 1.5}: pctile 0.253 →
  0.241 (−0.012 [−0.030, +0.000]).
- In-sample the grid is non-monotonic: 0.296 / 0.272 / 0.253 / 0.253 / 0.312 /
  0.274.

**Hole (dfs-model-critic):** the cash climb ranks players on the ladder
projection alone, and that projection is compressed against salary.

- Within position, (FPTS − proj) per $1k: QB **+1.80** (positive on 7 of 7
  dates), RB +0.35, WR +0.14.
- QBs over $8k run +4.2 and QBs under $5k run −4.1.
- Our picks run −0.24 actual − proj (n=72), against +1.24 for the pool (n=613).
- Against the cashing lineups in the same contest, we are −22.9 points at RB
  while spending $1.9k less there.

**Candidate:** `proj' = max(0, a_pos + b_pos·proj + c_pos·salary_k)`, least
squares on player rows (every logged board with an export), excluding the
held-out date. Implemented as `calibrate`; it reproduces the critic exactly.

| variant | contests / slates | cashed | field pctile | mean pts | ROI | pctile diff [90%, by slate] |
|---|---|---|---|---|---|---|
| shipped | 11 / 8 | 18% | 0.253 | 134.1 | −69% | |
| calibrated, fit on every other date | 11 / 8 | 18% | 0.344 | 144.4 | −69% | +0.091 [−0.011, +0.187] |
| calibrated, fit on **earlier dates only** (live-faithful) | 11 / 8 | **0%** | 0.286 | 144.2 | −100% | +0.033 [−0.118, +0.192] |

Per held-out date (every other date): 5 of 8 better, 2 worse (9/26, 10/22), 1 even.

The forward fit's QB salary slope is 0.10–0.65 through 11/6. It only reaches
1.2–1.7 once later dates are in. Using every other date leans on the future.

**Decision: NOT SHIPPED.**

- Cash rate and ROI are unchanged, or worse in the live-faithful version.
- Every interval includes zero, and optimizer seeds alone move the percentile
  by ±0.02.
- The critic measured player MAE worse (LODO 6.68 → 6.82, n=738), so the
  NCAAF gate (player MAE) would reject it anyway.
- 8 slates is not enough data. Conservative call (unattended).

**Runner-ups (critic):**

1. **Field chalk in cash.**
   - Adding w·own/100 to the projection, w chosen leave-one-slate-out: pctile
     0.318, 4 of 11 cashed.
   - But w jumps between 5 and 40, and combined with the calibration it
     collapses to 0.251.
   - On 9/26, 11/5 and 11/7, our summed ownership sat at the field's 9th–17th
     percentile.
2. **Players with no ladder that cash fields build around.** They take 3–36%
   of cash ownership: Easton Messer 96% (38.9 pts) on 11/5, Isaiah Marshall and
   Trelain Maddox on 10/17, Landon Sides on 11/6. Their points per $1k beat the
   pool's on only 5 of 11 contests.
3. **The 9/26 board predates the suffix fix** (Hawkins Jr. is missing), so the
   replay understates today's production on that date.

**For the next cycle:**

- Rerun `report` and `calibrate --forward` as cash exports arrive. The QB
  salary signal is the cleanest single piece (7 of 7 dates).
- `gate OUT` is ready for Adam to add to `critique_gate` as a lineup-level gate
  next to player MAE.

Unattended gate (20261109-0726): **not shipped (log / harness only, verified)**

## Cycle 8 (auto: unladdered players missing from the cash pool) — 2026-11-10 (unattended): target is not the hole — NOT SHIPPED

**Scout:**

```json
{"sport": "ncaaf", "target": "unladdered players missing from the cash pool",
 "metric": "replayed production CASH lineup per held-out draft group vs the real Double Up / 50-50 fields: field percentile, share cashed, $ per $1; mean points and the share of field cash ownership our pool can see. GPP separate.",
 "harness": "scripts/ncaaf_lineup_lab.py report + a candidate mode; scripts/ncaaf_lab.py gate must not get worse on laddered rows",
 "split": "leave one slate out over 8 cash slates / 11 contests; anything fitted on earlier dates only",
 "why": "cash ROI -63% [-98%, -27%] (12 entries / 9 slates); build_pool drops every player without a DK ladder, yet they hold 3-36% of cash-field ownership (Easton Messer 96% owned, 38.9 pts on 11/5)",
 "money_at_stake": "NCAAF cash net -$8.77 on 12 entries / 9 slates; 11/5, 11/6, 11/7 each lost 100%",
 "runner_ups": ["QB salary compression (cycle 7, failed live-faithful; retry after 11/15+ exports)",
                "GPP lineups in the same lab (+140% rests on two small-field wins)",
                "gate scores player MAE only; lineup gate OUT awaits Adam"]}
```

The 5 name suspects were checked: all are different players (Jaden/Jeremiah Smith,
Jai'Den/Ja'Cory Thomas, Tavorus/Tramell Jones). Nothing to fix in edge/names.py.

**Baseline** (`ncaaf_lineup_lab.py report`, unchanged since cycle 7): cash 11 contests /
8 slates, cashed 18%, field pctile 0.253, 134.1 pts, ROI −69%.

**Critic (dfs-model-critic):** the target is not where the hole is.

- *Pre-lock source.* The `data` branch history of `data/draftables_snapshot/<gid>.json`,
  last commit before the first kickoff. It covers 6 of the 9 cash boards; 9/26, 10/3 and
  10/16 have none. Each record has salary, DK FPPG (stat 174) and DK's injury status.
- *Field cash ownership our pool can see:* 64% (9/26, the pre-fix suffix bug), 97%, 77%,
  80%, 90%, 92%, 79% (11/5, Messer), 72% (11/6), 93% (11/7).
- *A missing ladder mostly means out or a small role.* Players with FPPG ≥ 8 on the 6
  boards: laddered with no status scored 295 of 302; unladdered with no status 57 of 86;
  DK OUT 0 of 42, D 0 of 5, Q 1 of 4.
- *Held-out replay* (added players at proj = k_pos × FPPG, k fitted on earlier dates only,
  ~0.75-0.91; FPPG alone is close to the ladder as a predictor, MAE WR 6.0 vs 5.7, QB 6.8
  vs 6.8, RB 6.8 vs 6.5, n=474):

| variant | contests / slates | cashed | field pctile | ROI | pctile diff vs shipped [90%, by slate] |
|---|---|---|---|---|---|
| shipped | 11 / 8 | 18% | 0.253 | −69% | |
| add every unladdered draftable | 11 / 8 | 9% | 0.187 | −85% | −0.066 [−0.170, +0.039] |
| … minus DK OUT / D / Q | 11 / 8 | 0% | 0.145 | −100% | −0.107 [−0.218, −0.001] |
| … × P(play) fitted on earlier dates | 11 / 8 | 27% | 0.324 | −23% | +0.071 (no added player is ever picked) |
| oracle: only unladdered players who scored (look-ahead) | 11 / 8 | 27% | 0.343 | −23% | +0.090 [+0.004, +0.233] |

  The live-faithful versions pick busts (Elston 0.0, Jordan Thompson 0.0, Kelley −0.4,
  Engleman 0.6). Messer on 11/5 lifts the lineup 127 → 144 points, still the 9th
  percentile. On 11/7 the oracle is worse.
- *Where the +0.07 comes from: optimizer search, not the pool.* Each cash board rebuilt
  with seeds 0-9 (verified by me, same numbers): seed 0 falls short of the best objective
  only on 11/6 (102.06 vs 103.16), and that better lineup scores 190.3 instead of 161.3
  (pctile 0.83 vs 0.04). Unpicked extra rows just knock the climb onto it. With the search
  effect removed, the oracle's ceiling is about +0.017 pctile and no extra cash.
- *The losses are in-pool chalk we skipped:* 10/3 Aiden Flora (52.1 pts, 77% owned) and
  Jeremiah Smith (46.6, 40%); 11/7 Caleb Hawkins (57.6, 89%); 11/6 Kevin Jennings (33.7,
  65%). All sat about $1k below their price on our projection per dollar, which is cycle 7's
  salary compression again.

**Critic's fix:** don't add unladdered players; restart the cash build (seeds 0-4, keep the
best objective); log unladdered draftables (proj blank, `dk_status`, `dk_fppg`) and show
high-FPPG unladdered players with a blank status in the app as "no ladder: check news".

**Decision: NOT SHIPPED (conservative, unattended).**

- Adding unladdered players loses held out in every live-faithful version.
- Restarts change 1 of 9 cash boards (11/6), where a 1.1-point objective gap flips 161 →
  190 points. One slate is luck in direction, not evidence. It also costs 5× build time on
  the phone page, and the NCAAF gate (player MAE) cannot see it.
- Logging unladdered rows changes the forward log every consumer reads. It is worth doing,
  but it is a format change for Adam to approve, not an unattended commit.

**For the next cycle:**

1. In-pool chalk / salary compression (Flora, Hawkins, Jennings) is the lead again. Retry
   cycle 7's `calibrate --forward` once the 11/14-11/15 cash exports arrive.
2. Cash restarts: re-measure seeds 0-9 per board as slates arrive. Ship only if
   best-objective beats seed 0 on several slates.
3. Pre-lock draftables are in `git log origin/data -- data/draftables_snapshot/<gid>.json`
   (6 of 9 cash boards). 10/16 had 23% of field cash ownership with no ladder and no record
   of why (Hunter Tipton 62%, Dominic Lee Knicely 38%).
4. A lab lineup player nobody drafted is scored 0. That is harmless for the shipped pool
   but biases any pool expansion (6 such players in "add every").
