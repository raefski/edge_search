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
