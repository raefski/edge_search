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
