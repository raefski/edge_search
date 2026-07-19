# DK MLB DFS — Continuous-Improvement Plan

Written 2026-07-18, immediately after the §28 model audit and §29 simulator build
(DFS_METHODOLOGY.md is the record of what's been done; this file is the standing plan for
what gets done next and *on what cadence*). The plan is organized around the single lesson
that runs through the whole methodology doc: **validation data is the scarce resource.**
Every improvement that survived (EB shrinkage, home/away PA, platoon cells, 5-3 stacks,
ownership gammas) survived because there was enough real data to test it against; every
idea that got killed (per-player platoon twice, team_total, umpire, recency, component
mean model) died the same way. So the plan is a flywheel, not a feature list: make every
slate played produce more data, spend that data on the highest-EV layer, re-measure,
repeat.

## 0. Honest baseline (what "improved" is measured against)

| layer | current level | metric source |
|---|---|---|
| Hitter mean projection | corr ~0.18, MAE ~5.39 (test window) | §28; at free-data ceiling |
| Pitcher mean projection | corr 0.35 backtest, MAE ~7.2-10.3 by slate hardness | §3, §18 |
| Ownership model | hitter MAE ~3.7, corr ~0.35-0.44 pooled | §11, §19 |
| Construction (GPP replay) | 74-78% percentile-in-field (5-3 leverage) | §18 replay |
| Simulator | mechanics calibrated (corr/runs/anti-corr match measured reality); contest-level: field p50 bias +0.0 pooled but ±20 pts per-slate, n=10 | §29 |
| **Real-world bottom line** | ~60.5% avg percentile across 12 graded entries, **no demonstrated beat-the-rake edge**; model adds ~zero info beyond DK salary (incremental R² +0.0016) | §10 |

The last row is the one that matters. Everything below exists to move it, and it gets
re-measured on a schedule (§6) rather than argued about.

## 1. The data flywheel (highest leverage; mostly process, little code)

Every played slate should automatically bank four artifacts. Most of this now exists —
the gaps are the checklist items:

1. **Contest export, every contest, same night.** The standings CSV is the single most
   valuable file the system gets (field lineups, real ownership, real scores — it's what
   validated the ownership model, measured STACK_DIST, and validates the sim). Habit:
   download it right after games end, drop in `data/`, tag it in `contest_meta.json`.
2. **Payout structure into `contest_meta.json`** (NEW — small manual step, big unlock).
   DK's export has no entry fee or payout curve, which is the only reason ROI is
   percentile-only today (§10). Add per contest id: entry fee, field size, and the payout
   table (or even coarse: min-cash line, 1st-place prize, % of field paid). This turns
   both the ROI backtest and the simulator's output from percentiles into **dollars**,
   which is the actual objective.
3. **Sim predictions logged pre-lock, graded post-slate** (NEW — the sim's own
   forward-test loop, the exact mechanism that validated the mean model). At build time,
   log P(cash), P(top1%), median-percentile for the entered lineup; after grading, record
   the realized percentile. Over ~20-30 contests this produces the honest test of the
   simulator: are realized finishes distributed the way it predicted (PIT/calibration
   check), not just "was the pooled bias zero."
4. **Pitcher prop means in the proj log** (NEW, one-line change): `outs_mean`/`k_mean`
   now exist in the pool but aren't logged columns. Log them so past slates can be
   re-simulated faithfully (today's replays fall back to defaults for pitchers).

Also in this bucket: the stale-actuals-cache invalidation bug flagged in §18 ("worth a
real fix later") — a data-integrity item; mid-slate caches poison every downstream
calibration consumer silently.

## 2. Projection layer (bounded effort — it's near ceiling, don't over-invest)

The §28 audit says the mean models are close to their free-data ceiling. Remaining items,
in order:

- **Statcast experiment (the one untouched quality data source).** Baseball Savant
  xwOBA/barrel%/hard-hit% (hitters) and xwOBA-against/whiff% (pitchers) are free and are
  the industry-standard skill inputs this system has never ingested. Honest expectation:
  small (+0.005-0.01 corr at best — season DK-pts/PA already absorbs most skill signal),
  but it's the last known unexplored source. Run it exactly like §28: collect once into
  the lab harness, train/test eval, ship-or-kill in one bounded session. Do NOT ship on
  a train-window win alone.
- **Pitcher distribution measurement (feeds the sim more than the mean).** The sim
  currently guesses pitcher outs/K spread (sd 4.0/1.6). Measure the real distributions
  from the 1,002 cached 2025 starts (plog cache) — outs variance by pitch-count era,
  K-given-outs, ER tails — and validate pitcher score marginals by bucket the same way
  hitter marginals were validated in §29. GPPs are won in pitcher tails (9-K vs 4-K
  nights); getting the tails right matters more than the mean here.
- **Finish the hitter-gamma sweep below 1.0** (flagged in §23, never done: 1.0 beat 1.5
  on every date but was the edge of the swept range).
- **Small-slate ownership cap** (§19: real 91.3% ownership vs the model's 65% hard cap,
  2 slates of evidence) — revisit once ~5 small-slate exports exist.
- **Seasonal refit cadence**: PLATOON_CELL, HOME_QUALITY, SLOT_PA tables, PA_PMF, and
  STACK_DIST are 2025/early-2026 fits. Re-estimate each April on the prior full season
  (run environment, ball, and rule changes drift these).

Explicitly NOT worth more time (killed with data; don't re-litigate without new
evidence): per-player platoon splits, recency/hot-hand, umpire, team_total in the mean
model, full component decomposition of the mean model, K-matchup interaction,
PA-vs-opponent, weather in the mean model.

## 3. Simulator layer (newest piece, most headroom)

- **Vegas totals & weather belong HERE, not in the mean model.** Both were rejected as
  per-player mean multipliers (+0.003 and +0.001 corr) — but the sim has a team-run
  environment layer where they operate at the right altitude: shift a TEAM's run
  distribution and every correlated consequence (stack ceilings, opposing pitcher tails)
  follows mechanically. Totals for a full slate are a cheap live pull the user already
  has API budget for (h2h+totals, ~2 markets × 1 region); weather comes from the already-
  validated collector pattern (bt_weather.json for backtests, Open-Meteo forecast for
  live, dome/roof handled). Validate against the §29 replay harness: does conditioning
  team environments on totals shrink the ±20-pt per-slate field-quantile misses? That
  spread is exactly what an unconditional sim can't see, so this is the targeted fix
  for the sim's known weakest number.
- **Duplicate-lineup mass (real GPP economics).** Confirmed in the repo's own data: the
  2026-07-18 142-entry GPP had two IDENTICAL rank-1 lineups at 140.6 splitting the top
  prizes. Chalk builds duplicate; duplicated wins split payouts; leverage builds don't.
  Measure dupe rates from all exports (exact-lineup collision frequency vs field size),
  add expected-dupes to the equity calc, and payouts (from §1's metadata) become
  tie-adjusted dollars. This systematically REWARDS the leverage construction the system
  already prefers, and quantifies by how much.
- **Field-model upgrades from accumulating exports**: pitcher-pairing distribution,
  salary-left distribution, and stack-team choice vs Vegas totals (the field
  overwhelmingly stacks high-total teams — modeling that makes simulated fields sharper
  on exactly the slates where our leverage pick differs from the chalk stack).
- **Standing calibration loop**: `dfs_sim_validate.py` re-run as each new contest lands
  (fold into the §6 cadence); track field-quantile bias and the PIT histogram over time.
  The sim's credibility is a time series, not a one-off table.

## 4. Construction layer (where GPPs are actually won)

- **Sim-EV-driven lineup selection — the single biggest available upgrade.** Today the
  optimizer maximizes leverage (ceiling faded by ownership) and the sim only EVALUATES
  the result. Invert it: generate 100-300 diverse candidate lineups (the randomized
  search already produces them for free across restarts), score EACH against one shared
  simulated world-set + field sample (cheap: one sim, many lineup sums), pick by
  **expected dollar payout** (with §3's dupes + §1's payout tables), tie-broken by
  P(top 1%). Validate before shipping, exactly like §18's construction change: replay
  all logged slates, compare percentile-in-real-field vs the current 5-3 leverage
  builder, ship only if it wins across seeds. This converts every simulator improvement
  directly into lineup quality forever after — it's the piece that makes the whole
  flywheel compound.
- **Cash/GPP divergence should emerge, not be hardcoded**: max-EV under a flat ~2x
  payout curve naturally builds floor lineups; under a top-heavy curve it naturally
  builds correlated ceiling lineups. Long-term this replaces the hand-tuned
  floor/leverage objectives with one principled objective — but only after the sim-EV
  selector has beaten the incumbent in replay. The incumbent is measured and real;
  it stays until beaten.
- **Late-swap re-sim**: the swap tool (§20-era) suggests replacements by proj/ceiling;
  once sim-EV exists, re-rank swap candidates by marginal contest EV instead (a scratch
  in your stack is a correlation event, not just a points event).
- **Multi-entry portfolios** (only if the user starts playing 3-20 entry contests
  regularly): max-EV per entry ≠ max-EV portfolio; entries should be anti-correlated.
  Out of scope until single-entry EV selection is validated.

## 5. Contest selection & bankroll (zero modeling risk, immediate EV)

The ROI backtest says builds average ~60th percentile. Whether that wins money depends
almost entirely on WHERE it's entered: rake (8-15% by contest), field size, payout
curve, and field softness vary more than any model improvement moves the needle.
Once §1's payout metadata exists (~15-20 tagged contests):

- Compute realized $-EV per contest TYPE (small cash vs large GPP vs small GPP), and
  what the 60th-percentile skill level is WORTH in each (a 60th-percentile lineup loses
  money in a 44%-paid double-up… barely, wins in soft small fields, and is pure variance
  in a 1,189-field GPP).
- The small-slate signal (§17: 76.8% vs 48.6% cash percentile, n=3+3 — suggestive, not
  proof) gets an honest read automatically as those ns grow.
- Output: a one-line recommendation per slate — which contest types tonight's build
  profile actually earns in. This is the cheapest durable edge available: play where
  the field is weakest, sized to bankroll (fractional-Kelly on the measured edge, once
  a measured edge exists — not before).

## 6. Cadence (the "continuous" mechanism)

**Every played slate (5 min, mostly existing tools):** download contest export → tag
contest_meta (incl. payout) → `dfs_grade.py <date>` → sim prediction vs realized logged
automatically (§1.3).

**Weekly (~30 min):** `dfs_calibration.py` + dashboard refresh; gamma sweep re-run
(hitter gamma below 1.0 question resolves itself as dates accumulate); sim calibration
re-run (`dfs_sim_validate.py`); glance at pitcher forward MAE vs the 7.18/10.3 anchors.

**Monthly (or every ~10 new contests):** the two load-bearing tests from §10, re-run on
the grown sample: (1) salary-conditioned incremental R² — does the model now add
information beyond DK's salary? (2) ROI/percentile backtest, now in dollars via payout
metadata. Plus construction replay if any construction change is pending.

**Season boundary (each April):** refit all frozen constants (§2 last bullet) on the
completed season; re-run the full §28-style candidate ladder once — signals that were
sub-threshold (weather, Statcast if killed) get exactly one cheap re-test per year as
data doubles, then stay dead unless they clear the bar.

**Standing discipline (unchanged from day one):** train/test split for anything fitted;
ship only on held-out wins; every change lands in DFS_METHODOLOGY.md with its numbers;
killed ideas stay killed absent NEW evidence; small-n results get reported with their n.

## 7. Decision gates (so "continuous improvement" can't become continuous rationalization)

- **Sim-EV selector ships only if** it beats the 5-3 leverage builder in
  percentile-in-real-field replay across all logged slates and ≥3 optimizer seeds
  (the §18 bar).
- **Any new signal ships only if** test-window corr improves without MAE cost (or both
  improve) AND the incremental-regression t is significant — the §28 bar.
- **The system itself faces a gate**: if after ~25-30 tagged contests the
  salary-conditioned regression still shows ~zero incremental information AND realized
  $-ROI is negative net of rake across contest types, the honest conclusion per this
  project's own standards is that the durable edge at this effort level is contest
  selection + promos, not modeling — and the plan's remaining effort goes there. Write
  that down now, while nobody's attached to the answer.

## Priority order (expected GPP-$ per unit effort, descending)

1. **Data flywheel completion** (§1: payout metadata, sim forward-logging, pitcher-mean
   logging, actuals-cache fix) — compounds everything else; mostly checklist + tiny code.
2. **Sim-EV-driven construction** (§4) — biggest single lever; all prerequisites now exist.
3. **Duplicate modeling + payout-curve equity** (§3+§1) — turns percentiles into the
   dollars GPPs actually pay in; data already on disk.
4. **Vegas totals + weather into sim team environments** (§3) — targeted fix for the
   sim's known weakest number; cheap.
5. **Contest selection analytics** (§5) — zero modeling risk, immediate real-money EV.
6. **Pitcher distribution measurement** (§2) — sharpens the tails GPPs are won in.
7. **Statcast ship-or-kill experiment** (§2) — last unexplored data source; bounded.
8. **Housekeeping refits** (§2, §6) — gamma sweep, seasonal constant refits, small-slate cap.
