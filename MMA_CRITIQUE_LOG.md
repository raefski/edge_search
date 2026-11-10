# MMA critique log

One `/dfs-critique mma` cycle per entry. Harness: `scripts/mma_lab.py` (`report`,
`parity`, `gate OUT`). Metric: DK points per fighter-fight against the pre-lock
projection, plus the STAT half (DK points minus win bonus), split by whether the fighter
had pre-lock Significant Strikes and Takedowns O/U lines. Split: leave one card out,
with the card as the unit and a 90% range from bootstrapping cards.

## Cycle 0: baseline (2026-11-09)

Shipped model rebuilt in the lab from DK Sportsbook's archived captures. For each card
the book is the last capture before the first bell. Truth is UFCStats rescored with DK
rules. The UFCStats mirror ends 10/31, so 11/07 is not graded yet; the lab never
refreshes it.

| slice | n | stat MAE | stat bias | DK MAE | DK bias |
|---|---|---|---|---|---|
| fighters with sig+td lines | 90 | 16.16 | +3.05 | 36.15 | -1.84 |
| fighters without | 60 | 14.82 | -0.85 | 34.06 | -2.39 |
| all | 150 | 15.62 | +1.49 | 35.32 | -2.06 |

That is 6 cards: 9/26, 10/03, 10/10, 10/17, 10/24 and 10/31.
**Parity** with the live board log: n=97 fighters on 4 logged cards, lab minus live mean
-0.02, MAE 0.24, corr 1.000. The lab is the live model.

## Cycle 1 (auto: sig-strike / takedown props unused), 2026-11-09, unattended

Scout JSON:
```json
{"sport": "mma", "target": "sig-strike / takedown props unused",
 "metric": "Held-out MAE and bias of each fighter's STAT points (DK minus win bonus), and of total DK points, per fighter-fight, on fighters with a pre-lock DK Sig Strikes / Takedowns O/U line; shipped vs props blended in. Truth UFCStats rescored with DK rules. Secondary: total-points MAE on every logged fighter.",
 "harness": "build: last pre-first-bell capture per card from data/mma_odds_history, joined to the shipped projection (recomputed through edge/mma_sim.py for 10/3 and 10/10), graded vs UFCStats; names via edge.names.norm",
 "split": "leave one card out over the 7 cards, blend weight fitted on 6; 90% ranges by card; 'not enough data' unless the gain holds on >= 5 of 7 cards",
 "why": "stats are 32.0 of 58.9 average DK points and the props price exactly that, but MMA_STATUS section 9 says they are captured and NOT used; archived on all 7 cards",
 "money_at_stake": "every lineup; cash +$8 (6 entries / 4 slates), GPP -$3 (3 entries / 3 slates), both too thin to target directly",
 "runner_ups": ["build lineup harness (only 2 replayable slates; revisit at 5+)", "Classic field ownership refit (1 Classic slate)", "Round-and-Method grid for finish timing"]}
```

**The cycle's work was building the harness** (MMA had no held-out lab):
`scripts/mma_lab.py` with `report`, `parity` and `gate OUT`, plus tests in
`tests/test_mma_lab.py`. No critic was spawned. The scout's target was itself the
hypothesis, so it was tested directly in the new lab.

**How the props are turned into numbers.** From the O/U line and the devigged P(over):
- Expected sig strikes: normal distribution with sd = 0.5 x mean. That 0.5 (`SIG_CV`) is
  a judgment call, not fitted. Every DK sig line in the archive is priced at -115/-115,
  so it has no effect.
- Expected takedowns: Poisson.
- Converted to DK stat points with a count-to-points map, fitted by OLS on UFCStats
  2020 to 9/25/2026 (history only, no props): 6.21 + 0.420 x sig + 7.93 x td.

**Results, leave one card out, fighters with props (n=90, 6 cards):**

| variant | stat MAE | stat bias | DK MAE | change vs shipped [90%] | cards better |
|---|---|---|---|---|---|
| shipped | 16.16 | +3.05 | 36.15 | - | - |
| props (weight w fitted on train, clipped to [0,1]) | 16.29 | +3.01 | 36.32 | +0.16 [-0.00, +0.39] | 1/6 |
| props_ols (intercept + slope on train) | 15.98 | +0.12 | 36.31 | +0.16 [-0.02, +0.34] | 2/6 |
| props_full (props replace the model's stats) | 16.70 | +2.49 | 37.12 | +0.97 [-0.02, +1.82] | 2/6 |

**Why the props don't help.** Their implied stat points correlate 0.50 with actual stat
points; the model's correlate 0.57. The two correlate 0.87 with each other, so the props
add nothing the model does not already have. The sig lines also run high: implied 41.4
sig strikes per fighter against an actual 34.7. They look like a full-fight median, and
finishes cut fights short. The fitted weight is 0 to 0.26 and its sign flips across folds
in props_ols.

**Decision: NOT SHIPPED.** No variant improves held-out DK MAE, and none is better on 5
of 6 cards. MMA_STATUS section 9 is updated: the props are now *measured* and add
nothing.

**Gate.** `python3 scripts/mma_lab.py gate OUT` writes 150 rows (key = card|fighter,
cluster = card, split primary, state props / no_props, pred = production's projection,
actual = DK points). Adam has to approve adding MMA to `harness/critique_gate.py`. The
gate reads `edge.mma_sim.prop_adjust`, if it exists, so a future production prop
adjustment would be measured by it.

**Runner-ups for the next cycle:**
- Stat bias of +3.05 on fighters with props versus -0.85 without. The model
  over-projects stats on main-card / featured fighters (n=90, a hint only).
- Overall DK bias of -2.06: the model under-projects total points, so the win-bonus side
  runs low.
- Grade 11/07 once the UFCStats mirror has it (a 7th card).

Unattended gate (20261109-0843): **not shipped (log / harness only, verified)**

## Cycle 2 (auto: field model refit, GPP/cash ownership + the field's lines), 2026-11-10, unattended

Scout JSON:
```json
{"sport": "mma",
 "target": "field model refit (GPP/cash ownership + the field's lines) on the unused exports",
 "metric": "Leave-one-slate-out error of the simulated field against each real DK field, cash and GPP kept separate. Primary: per-fighter ownership MAE (points of ownership) per contest. Secondary: the simulated cash line (field 56th percentile) and GPP top-1% line against the real contest's line, in DK points. Shipped (tau 16 GPP / 8 cash, FPPF weight 0.15) against the refit.",
 "harness": "build: extend scripts/mma_calibration.py --fit-ownership into a held-out mode; contest type from contests.db; rebuild the 10/03 and 10/10 boards (gid 154125, 154571) with mma_lab's archived-book path; grid tau and FPPF weight on n-1 slates, score the held-out slate.",
 "split": "Leave one slate out over 4 slates: 9/26 Captain (154126), 10/03 (154125), 10/10 (154571), 11/07 (156055). GPP 4 contests, cash 6 contests on 4 slates. 'Not enough data' (<5 slates); ship only if better on at least 3 of 4 held-out slates in each type.",
 "why": "Both objectives score lineups against a simulated field whose two temperatures were fitted on ONE 118-entry Captain slate; 3 Classic GPP fields (2,935 entries) and 4 more cash fields have come in and been used for nothing.",
 "money_at_stake": "GPP -$3 (-100%, 3 entries / 3 slates, beat 34%). Cash +$8 (+100%, 6 entries / 4 slates). Both thin.",
 "runner_ups": ["build lineup harness: replay production cash/GPP lineups against the 4 real fields (revisit at 5+ slates)", "Grade 11/07 as a 7th lab card when the UFCStats mirror has it", "DK total bias -2.06 and stat bias +3.05 on fighters with props"]}
```

**The cycle's work was building the harness**, as the scout specified. Ownership had
no held-out lab. The new script is `scripts/mma_own_lab.py`, with `report`, `fppf`,
`rebuild` and `gate OUT`, and tests in `tests/test_mma_own_lab.py`.
- Contests and contest types come from contests.db, as tagged by DK. The scout noted
  that `--fit-ownership` reads 11/07 as "unknown" from contest_meta.json; this lab
  never uses that file.
- 9/26 and 11/07 use the logged live board, with salary from the log, because
  slate_player holds the 1.5x CPT price on a Captain slate.
- 10/03 and 10/10 are rebuilt with `mma_lab.load_rows`, which matched 28/28 and
  24/24 DK names. Their DK FPPF is the UFC career mean before the card. The
  rebuilt FPPF against DK's logged FPPF: n=180, mean +0.10, MAE 0.61, corr 0.996.
- The secondary metric scores the field model's weighted lineup distribution on DK's
  realised FPTS, at the contest's own paid fraction (cash) or top 1% (GPP).

As in cycle 1, **no critic was spawned**. The scout's target was itself the hypothesis
(refit the three constants), so it was tested directly in the new lab. This was the
conservative choice for an unattended run: 8 of the last 9 MMA loop runs died on the
usage limit.

**Results, leave one slate out.** 4 slates, so every row is **not enough data**.

| type | variant | own MAE | own bias | mean line err (sim - real) | Δ MAE vs shipped | slates better |
|---|---|---|---|---|---|---|
| cash (6 contests, 371 entries) | shipped (0.15, τ 8) | 10.79 | +0.11 | -25.3 | - | - |
| | tau refit | 10.79 | +0.11 | -25.3 | +0.00 | 0/4 (picks 8 every fold) |
| | joint (FPPF weight + τ) | 10.79 | +0.11 | -25.3 | +0.00 | 0/4 |
| GPP (4 contests, 2,053 entries) | shipped (0.15, τ 16) | 7.50 | +0.18 | +8.7 | - | - |
| | tau refit | 8.04 | +0.18 | +8.4 | +0.53 | 0/4 |
| | joint | 8.04 | +0.18 | +8.4 | +0.53 | 0/4 |

Per held-out GPP slate, shipped vs refit: 10/03 4.69 vs 5.78 (refit τ 20); 9/26 9.80
for both; 10/10 7.22 vs 8.26 (τ 12); 11/07 8.31 for both. Pooled in-sample, cash
(0.15, τ 8) = 10.79 is the grid minimum. GPP (0.15, τ 16) = 7.50; the in-sample
minimum is (0.3, τ 16) = 7.36, a gap of 0.14 that does not survive leave-one-out.

**Decision: NOT SHIPPED.** The one-slate fit from 9/26 generalises to the three
Classic cards. No refit beats it on any held-out slate.

**New finding, not addressed: the simulated cash line runs low on every slate.**
Scored on the realised points, the modelled field's cash line sits below the real
contest's line by 11, 31, 49 and 10 pts (10/03, 9/26, 10/10, 11/07), mean -25. The
real cash field builds better lineups than a softmax on our public projection, even
though its ownership MAE is fine. The cash objective therefore aims at a bar about
25 pts too low. That favours safe lineups which clear the modelled line and miss the
real one. GPP top-1% has no consistent sign (+39, +26, +3, -34).

**Gate.** `python3 scripts/mma_own_lab.py gate OUT` writes 248 rows: key =
contest|fighter, cluster = draft group, split primary, state cash / gpp, pred =
production's field ownership from the module constants, actual = %Drafted. Adam
has to approve adding it to `harness/critique_gate.py` as a second MMA gate. It
needs no UFCStats refresh.

**Runner-ups for the next cycle:**
- The cash line bias of -25 (4/4 slates). Target: the cash objective's line against
  the real cash line, held out by slate. A fix could be a field-skill term or a line
  shift fitted on train.
- The lineup harness: replay production's cash and GPP lineups against the 4 real
  fields (scout runner-up). Revisit at 5+ slates.
- Grade 11/07 as a 7th projection card once the UFCStats mirror has it.

Unattended gate (20261110-1345): **not shipped (log / harness only, verified)**
