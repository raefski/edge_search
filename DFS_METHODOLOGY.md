# DK MLB DFS System — Methodology Writeup

Prepared for external review. This is a straight account of what was built, what was
measured, what got thrown out, and what's sitting in the code unused — with the reasoning
behind each call. Numbers are from real backtests and forward-tested contest results, not
projections of projections.

## 1. Premise

DraftKings salaries are **frozen for the whole slate** — no live re-pricing the way a
sportsbook line moves. Sportsbook player props, by contrast, are near-real-time and sharp.
The thesis: price players off the props/skill data (which updates), and look for salary
that hasn't caught up. We are not trying to out-project Vegas — we're trying to exploit a
stale DK salary using inputs the field mostly also has, plus construction edges (ownership
leverage, stacking, contest selection) that don't depend on the projection being brilliant.

This followed a full sports-betting phase (WNBA props, niche leagues, internal consistency,
SGP correlation, a forecasting model) that was **exhaustively falsified** — DraftKings is
sharp on every angle we tried; the only durable +EV there is promos/boosts, not pricing. DFS
was the pivot because salary staleness is a structural crack that betting lines don't have.

**Update, §10:** this premise has now been directly tested (`actual ~ salary + model_proj`)
and a real-field rank backtest run, both for the first time. Neither currently supports the
premise as demonstrated — see §10 before treating anything above as a proven edge.

## 2. Architecture

- `edge/dfs_run.py::build_slate()` is the single pipeline both interfaces call — the CLI
  (`scripts/dfs_lineups.py`) and a Streamlit phone app (`app.py`), so they never diverge in
  logic (only in randomized-search outcome — see §7).
- Pitchers: projected from **live sportsbook props** (K's, outs, ER, hits, BB, win prob),
  converted line→implied mean→DK points.
- Hitters: projected from a **prop-free structural model** (see §3) — batter props were
  tried first and performed far worse (§4).
- Ownership: a heuristic power-softmax model, tuned against real contest exports (§3).
- Optimizer: randomized greedy + hill-climb, CASH (maximize mean) and GPP (forced
  consecutive-batting-order stack + ceiling faded by modeled ownership).
- Forward-test loop: every build logs its own projections (`data/dfs_proj_log.csv`); after
  games finish, `scripts/dfs_grade.py` pulls real box scores (free, statsapi) and grades
  proj vs actual. This is the primary validation method — see §8 on why backtesting was
  abandoned for hitters specifically.

## 3. What Works (in production, measured)

**Pitcher projection (props-based).** Backtest: 1,002 cached 2025 starts, corr **+0.35**,
MAE 7.18 (naive baseline 7.60). Forward-tested corr has ranged 0.29–0.62 slate to slate
(small-n, high variance — anchor on the 0.35 backtest, not any single slate). Honest read:
this is **field parity, not an edge** — everyone building off Vegas props gets roughly the
same number. The projection isn't the edge; the construction is.

**Hitter projection**, `skill × opportunity × park × matchup`:
- `skill` = pooled DK-pts-per-PA over prior completed seasons **plus the current
  in-progress season** (fixed 2026-07-08 — was frozen on the two seasons *before* the
  current one, silently missing all of this year's form; see §6).
- `opportunity` = expected PA from batting-order slot (leadoff 4.65 → 9-hole 3.85).
- `park` = 3-year rolling park run-index.
- `matchup` = 60% opposing starter's K/9, 40% opposing bullpen's K/9 (bullpen blend added
  2026-07-08; see §6).
- Backtest (19,332 cached 2025 hitter-games): corr **+0.16** vs the old prop-based model's
  0.02, monotonic ranking by projection quintile. Forward-tested clean (near-lock, full
  pool) slates: 0.114 / 0.185 / 0.253 — averaging right around the 0.16 backtest. One slate
  (7/2) hit −0.119, but that build was built early with a contaminated partial pool
  (72 of ~160 hitters) — discounted, not a real signal.
- Honest verdict: **modest**, and it should be — single-game hitter outcomes are genuinely
  high-variance. 0.15–0.20 corr is real signal, not noise, but nobody should expect it to
  feel like a strong predictive model in any one lineup.

**Ownership model** — power-softmax over projected value, normalized to (roster
slots × 100%), tuned against 5 real DK contest exports:
- `pitcher_gamma=7.0`, hitter `gamma=1.5` (pitchers concentrate ownership far harder than
  hitters at the same value gap — field jams 1–2 arms). Both re-tuned in §11 after the
  hitter softmax was found running too hot; see §11 for current values and evidence.
- Batting-order term (`(SLOT_PA[slot]/4.2)^3`) — the field orders a stack by *batting
  slot*, not by our value estimate; without this the model had the ownership order of a
  stack's hitters backwards. Cross-validated on held-out 6/30 data, then confirmed
  out-of-sample twice more (7/2: corr 0.385→0.600; 7/3: corr 0.474→0.595). This is the
  single most validated piece of the whole system — 3-for-3 out-of-sample.
- Net: ownership corr lands **~0.5–0.6** against real contests — meaningfully better
  signal than either projection model, and the part of the system I'd trust most.
  **Correction (see §10):** those 0.5–0.6 numbers came from an earlier, unsaved ad-hoc
  calculation, not the calibration pipeline described above. The pipeline's numbers,
  computed the same way every time and reproducible, are lower — pooled hitter-ownership
  corr 0.351, and even the *same date* (7/3) recomputes to 0.444 rather than the claimed 0.595.
  The pipeline's number is now the one to trust. Pitcher ownership corr (0.892 pooled)
  also doesn't hold up under closer inspection — see §11.

**Optimizer.** Cash = maximize mean. GPP = force a consecutive-batting-order 4-stack
(measured real but small: matched-mean comparison shows +4% ceiling std, +1 P95, +3 P99 —
kept because it's free, not because it's a big lever), fade by modeled ownership for
leverage. **Superseded 2026-07-11 (§18):** GPP now forces a 5-stack + secondary 3-stack
with the stack team picked by leverage rather than raw projection, and the optimizer
enforces DK's max-5-hitters-per-team rule it previously didn't know about. Fixed this session: the optimizer had **no concept of which side of a game a
player is on** — nothing stopped it from stacking a team's hitters while also rostering
that same game's *opposing* pitcher (caught live: a White Sox stack + the Boston starter
facing them). Now a hard constraint. Worth flagging precisely because the *first* fix I
shipped for this didn't actually work (§9) — the corrected version is verified against live
data, not just its own unit test.

**Bullpen blend.** Backtested leak-free on 5,146 held-out 2025 hitter-games: adding 40%
weight on the opposing bullpen's K/9 (vs 100% starter-only) dropped hitter MAE 5.577→5.547
with correlation unchanged (0.181→0.179 — no ranking cost). Real, if modest.

**Season freshness.** Same backtest: including the current season alongside priors (vs
freezing on the two seasons before it) dropped MAE 5.604→5.577 *and* raised corr
0.166→0.181 — both metrics improved together, the cleanest possible signal. This was a
staleness bug, not a design choice, and it went unnoticed for a while — see §9.

**Infrastructure that's now solid:** projected-lineup fallback for teams whose batting
order hasn't posted yet (fixes an early-poster stacking bias); a late-swap tool that
detects when a projected player gets ruled OUT and suggests same-position replacements;
doubleheader handling (a finished game 1 was leaking its lineup into game 2's build);
crash resilience (one bad Odds-API event used to kill the entire live build); two-way
pinned-entry + forward-test-log redundancy between the CLI and phone app; and, as of this
session, a **calibration dashboard** joining real contest %Drafted/FPTS against our own
logged predictions — closing a loop that previously relied on manually pasted screenshots.

## 4. What Was Tried and Failed (rejected, with reasons)

**Original prop-based hitter model.** Projected hitters from batter props the same way
pitchers are projected. Backtest corr: **0.02** — statistically no signal. Diagnosis:
single-game batter prop lines barely spread player-to-player, so the projection was
structurally flat (proj std 1.16 vs actual std 6.31). Replaced entirely by the skill model
in §3. The old functions (`project_hitter`, `allocate_hitter`, `player_markets` for
hitters) still exist in `edge/dfs.py` — dead code, not called anywhere in the pipeline
(§5).

**Stronger ownership team-stack multiplier.** After round-2 ownership analysis found
stacks under-modeled (~4x actual concentration vs ~1.3x modeled), a stronger team-level
exponent was tried. It **failed verification** — MAE on held-out 6/30 anchors got *worse*
(4.3→7.7). Root cause: the multiplier amplified within-stack ordering by our own *value*
estimate, but the field orders a stack by batting slot/name (a real slate: field owned the
2-3 hitters more than the 8-9 hitters even when our model liked the 8-9 guys' matchup
better). Reverted; the batting-order term (which *did* work) replaced it.

**Platoon signal** (batter/pitcher handedness). Backtested leak-free, same 5,146-game
harness as bullpen: MAE improved a lot (5.577→5.436) but **correlation got worse**
(0.181→0.155, below even the frozen-season baseline). That divergence is the tell — a
50/50 blend with a single-season, 40-PA-minimum split rate is noisy enough to mostly
shrink outliers toward the mean (which lowers MAE mechanically) without adding real
ranking signal (which would raise corr too). A real platoon effect should improve both.
Dropped rather than shipped on a metric that doesn't match the failure mode. This doesn't
mean platoons don't matter for real hitters — it means *this specific* implementation
(small sample, too-heavy blend weight) wasn't disciplined enough to trust. Worth
re-attempting with a larger PA floor and a much smaller/shrunk blend weight if this
matters to you.

**Umpire zone-tendency signal.** Collected real HP-umpire assignment + strikeout/walk data
across 1,624 games (May–Aug 2025) specifically to test this. The raw effect is real —
umpires span roughly ±6–10% in K-rate at n≈20 games each. But shrunk properly for that
small per-umpire sample size and threaded through the projection chain, the effect on
hitter MAE/corr was **negligible** (Δ < 0.002 on both). Not shipped — the data-collection
burden (one boxscore call per game, no bulk endpoint) isn't worth carrying for zero
measured benefit at this scale. The collection script is kept (§6) in case a larger sample
or a different application of the signal is worth trying later.

**team_total factor** (Vegas-implied team run total as a hitter multiplier). Measured on
2,000 credits / 3,501 hitter-games: best-case lift was **+0.003 corr** (statistically zero
at that n, SE≈0.017) — park + matchup already absorb most of the run-environment signal.
Not wired in. The parameter still exists in `project_hitter_skill`'s signature
(unit-tested, unused) — see §5.

## 5. Kept, But Benign (harmless, not worth removing)

- **Old prop-based hitter functions** (`project_hitter`, `allocate_hitter`,
  `player_markets` used for hitters) in `edge/dfs.py` — dead code, zero call sites in the
  production pipeline. Kept for reference in case anyone wants to see what the failed
  approach looked like.
- **`team_total` parameter** in `project_hitter_skill()` — implemented, unit-tested,
  never called with a real value in production. Harmless surface area.
- **Orphaned season-specific cache files** (`data/dfs_skill_2024_2025.json`,
  `data/dfs_pitch_k9_2025.json`) — superseded by season-aware filenames
  (`dfs_skill_2024_2025_2026.json` etc.) after the staleness fix. Nothing reads the old
  files anymore; they just sit there.

## 6. Built, But Not Wired Into Production (with rationale)

- **`scripts/dfs_hitter_backtest.py`** — the leak-free backtest harness built this
  session (per-player game-log date-cutting, no lookahead) that validated/rejected
  season-freshness, platoon, bullpen, and umpire. Kept as reusable infrastructure for
  testing future signal ideas, not a production dependency.
- **`scripts/collect_umpire_data.py`** — the umpire boxscore collector. Kept for the same
  reason; the *signal* wasn't worth shipping, but the *collection tooling* is real and
  reusable if a bigger sample or different framing is worth trying later.
- **Platoon-adjustment code path** — built and backtested inside the harness above, not
  ported into `edge/dfs.py`'s production model, per the §4 rejection.

## 7. Known, Accepted Limitations

- **Hitter correlation is inherently modest** (~0.15–0.20). This is not a bug to fix — DFS
  hitter outcomes in a single game are genuinely high-variance, and no projection model at
  this level of effort is going to feel like a strong predictor game-to-game. The edge this
  system is actually chasing is structural (salary staleness, ownership leverage, stacking,
  contest selection), not projection quality.
- **Bullpen wiring costs ~135 seconds on a cold cache** (once per ~6-hour window) — free
  (no API credits), but real wall-clock latency the first time a slate is built in a fresh
  window. Verified warm-cache rebuilds are back to ~3 seconds.
- **CASH and GPP builds can legitimately differ between the phone app and desktop CLI on
  the same slate** — not a bug. GPP's mandatory stack is chosen via randomized search among
  several similarly-strong consecutive-order candidates; a single extra/missing player
  anywhere in the pool (confirmed lineups refresh live, timing differs build to build)
  shifts the exact random-number trajectory across all 800 search iterations. CASH has no
  such randomized commitment and reliably converges to the same top-value picks regardless.
- **Ownership calibration currently has less data than points calibration** — ownership is
  only logged when a full lineup successfully builds that day (`project_ownership` doesn't
  run on a partial/early pitcher-only build); of 6 usable historical slates, only 2 have
  ownership data logged. This will fill in naturally as more slates get built to completion
  near lock.
- **Two contest files couldn't be used for calibration at all** (7/4, 7/5) — no
  projections were ever logged those days (pre-existing gaps in when the app/CLI was run
  vs when it logged; both fixed going forward, see the forward-test-logger fix below).

## 8. Why Backtesting Was Abandoned for Hitters (in favor of forward-testing)

A proper leakage-free hitter backtest requires historical batter prop odds, which cost
real credits per event (~30cr/event → an estimated 9,000–18,000 credits for a usable
sample). Rather than spend that, the call was made to **forward-test**: log every real
build's projections, grade them against real outcomes once games finish, and accumulate
slates over time. This is slower to reach statistical confidence but costs nothing, and it
tests the *actual* deployed pipeline rather than a backtest proxy. The signals validated
this session (season-freshness, bullpen) used a different, free technique — leak-free
backtesting on 2025 data using free box-score history rather than paid historical odds,
which sidesteps the original cost problem for structural (non-prop) signals.

## 9. Bugs Found and Fixed This Session (for the record)

- **Doubleheader leak**: a completed game 1's confirmed lineup was leaking into game 2's
  build for the same team/date, because both games' data was written into the same dict
  keyed only by player name. Fixed by picking one "authoritative" game per team per date
  (whichever isn't Final yet).
- **Pitcher-vs-own-hitters constraint didn't actually work.** The first fix compared
  `game` id between pitcher and hitter pool entries — but pitcher entries carry a
  DK/Odds-API competition id and hitter entries carry a statsapi gamePk, two id spaces
  that never coincide even for the same real matchup. It passed its own unit test (which
  used contrived matching ids) but silently never fired in production. Caught by checking
  real field values in an actual entered lineup, not by re-reading the code. Refixed to
  match on team abbreviation instead, and re-verified against live data (30 teams,
  correctly symmetric).
- **Live build crash**: a single failed Odds-API event call (a 404/429/500 on one event)
  crashed the *entire* build in live mode — only the cache-mode-blocked case was caught.
  Now skips the one bad event and continues.
- **Forward-test logging gap**: only the CLI wrote to `data/dfs_proj_log.csv`; builds done
  through the phone app were never logged, silently losing forward-test data (this is
  exactly what happened to the 7/5 slate). Fixed by moving the logger into the shared
  `build_slate` pipeline so both interfaces log identically.

## 10. External Review — Independently Verified, Not Taken on Faith

An outside DFS-literate reviewer read this document and the calibration dashboard and
produced a detailed critique, benchmarking this system against commercial tools
(SaberSim, Stokastic) that run full correlated game simulation and field-simulated
ownership rather than mean-point optimization plus a subtraction term. Rather than accept
or wave off the critique, every checkable empirical claim was recomputed directly against
`data/dfs_calibration.json`. Results:

**Confirmed as stated:**
- **Hitter projections are still severely under-dispersed.** std(predicted)=1.37 vs
  std(actual)=7.56 — actual spreads **5.5x** wider. That's essentially the *same* ratio as
  the original prop-based model this doc declared fixed (5.44x). The skill-model rebuild
  reduced the absolute error, but the compression diagnosis that killed the old model was
  never actually resolved.
- **Pitcher forward MAE (10.31) is consistently worse than the 7.18 backtest across every
  one of 5 dates individually** (9.56–14.24), not one bad slate dragging an average. A real
  gap between backtest and live conditions, not slate variance.
- **Pitcher ownership corr is mostly a leverage-point artifact.** Pearson 0.892 → Spearman
  0.654. Restricted to the sub-15%-owned pitchers (where leverage decisions actually get
  made), Pearson drops to **0.332**, 95% CI **[-0.04, 0.62]** — not distinguishable from
  zero at this sample size.
- **Ownership MAE is close to the size of ownership itself.** Hitter ownership MAE (3.88)
  vs mean hitter ownership (4.03%) — the error is 96% of the thing being predicted.
- **Hitter ownership softmax is too hot.** Every one of the top-8 predicted-ownership
  hitters was over-predicted, several badly (65%→12% actual, 37%→15%, 33%→12%).
- **§3's "0.5–0.6" ownership corr claim doesn't reconcile with the calibration pipeline**
  even on the same date — corrected in §3 above; the pipeline's number is authoritative
  going forward because it's reproducible code, not an ad-hoc calculation.

**Partially correct, precision-corrected:**
- **"No HR term anywhere"** — not quite. `proj` has HR baked into the historical skill
  rate (no forward-looking adjustment); `ceiling` has an explicit separate term
  (`proj + 10 × hr_rate × PA`). The substantive point survives correction though: neither
  term creates *correlation* across teammates, which is the actual mechanism stacking pays
  through in reality (one HR simultaneously boosting several players' R/RBI). That's the
  real reason the measured stacking lift (§3, "+4% ceiling std") is small — the model has
  no mechanism for a stack to pay off together, independent per-player HR rate or not.
- **OLS-slope compression diagnostic** — a slope <1 regressing actual~predicted (measured:
  0.75) is ordinary attenuation from a noisy, weakly-correlated regressor, not evidence
  against compression specifically. The raw std ratio (5.5x, above) is the correct
  diagnostic and it confirms the critique's underlying point regardless.

**The load-bearing test — run for the first time, on this reviewer's suggestion:**
`actual ~ salary + model_proj`, testing whether the model adds anything DK's own salary
doesn't already contain (the entire premise in §1):

| | coef on `proj` | t-stat | incremental R² over salary-only |
|---|---|---|---|
| Hitters (n=816) | +0.296 | 1.16 (not significant) | **+0.0016** |
| Pitchers (n=82) | +0.817 | 1.38 (not significant) | +0.0216 |

For hitters, incremental R² is functionally zero. **On this sample, the model does not
carry information about outcomes beyond what salary already contains** — the direct test
of §1's premise does not currently support it. Six slates is a small sample for this test,
but the *effect size* for hitters, not just its significance, is tiny — this isn't purely
a power problem. This is the most important open question in the whole system and the
next thing that should move, not projection tuning.

**ROI/rank backtest** (built in response to the critique's #1-ranked gap:
`scripts/dfs_roi_backtest.py`) — where would our built lineups have actually finished in
the real contest fields the user played? Both CASH- and GPP-mode builds graded against the
*same* real contest each date (both are our own construction strategies tested against one
real field, not two contest types):

| | avg percentile | range | n |
|---|---|---|---|
| CASH-mode | 62.7% | 40.7%–92.4% | 6 |
| GPP-mode | 58.4% | 7.7%–97.1% | 6 |
| **Overall** | **60.5%** | 7.7%–97.1% | 12 |

Modestly above median, with real slate-to-slate spread. Only 4 of 12 finishes clear a
typical large-field-GPP cash line (~top 15–20%); the other 8 would not have cashed.
**Dollar ROI is not computable from DK's standings export** — it has Rank and Points but no
entry fee or payout curve, so this reports percentile-in-field, the honest ceiling of what
these files support. Six dates is too small to prove the system loses money, but it gives
**no evidence of a beat-the-rake edge** either — consistent with the salary-regression
result immediately above, not a contradiction of it.

**Net read:** the projection and ownership layers have real, if modest, signal (§3), but
two independent tests this section ran for the first time — salary-conditioned
significance and real-field rank — both come back not supporting a demonstrated edge yet.
That doesn't mean the premise in §1 is wrong; it means it hasn't been *shown* right, on the
data collected so far, and the next priority should be resolving that question rather than
refining models whose incremental value over salary is currently unmeasurable from noise.

## 11. Acting On The Review: Validation Methodology + Ownership Fix

Two concrete fixes came out of §10, done in the order the evidence justified — formalize
honest measurement first, then use it to validate any model change, rather than trust a
single pooled number the way the ownership 0.5–0.6 claim did.

**Validation module (`edge/dfs_validate.py`).** Every future calibration number should run
through this rather than get re-derived ad-hoc: `pearson`/`spearman`, Fisher-z confidence
intervals, `cross_slate_summary` (per-slate correlation *and* pooled, plus the cross-slate
standard error — the honest way to say "6 slates isn't 816 independent data points"), and
`incremental_baseline_test` (the salary-conditioned regression from §10, generalized so it
can be re-run for any model/baseline pair). Also wired in DK's own **FPPG** stat
(`draftStatAttributes` id 408 — free, sitting unused in the same draftables call the
pipeline already makes) as a second baseline going forward; it can't be backfilled for the
6 historical slates (DK only serves current/upcoming draftables), so it accumulates from
here on rather than retroactively.

**Dashboard axis-tick bug.** The reviewer said they couldn't read a single value off the
four panels. That wasn't a design nitpick — the code built a string of tick-value `<text>`
elements (`axisLabels`) and then never inserted it into the returned SVG template. Fixed;
verified the template now references it. The dashboard also now reports per-slate Spearman
and cross-slate SE alongside pooled Pearson, not pooled Pearson alone.

**Hitter projection dispersion — investigated, not fixed, and here's why.** Checked
whether std(predicted)=1.37 vs std(actual)=7.56 is an independently fixable bug: for a
well-calibrated `E[Y|X]` estimator, `std(pred)/std(actual)` should approximately equal
`|corr|`. Measured ratio 0.181 vs corr 0.136 — they match. The compression **is** the weak
correlation, restated in a different unit; it is not a separate flaw sitting on top of it.
Artificially widening the predictions (multiplying by a scale factor) would be a linear
rescale, and a linear rescale changes no player's rank relative to any other — it would
have **zero effect** on which players the optimizer selects, only cosmetic effect on how
the scatter plot looks. That would be dishonest theater, not a fix, so it wasn't done. The
real lever is the same one §10 already named: better underlying signal (§10's simulation
gap), not a parameter tweak here.

**Ownership gamma — genuinely fixed, out-of-sample, across every available slate (not
just the 2 checked in §10).** `scripts/dfs_ownership_gamma_sweep.py` reconstructs each
date's pool from `data/dfs_proj_log.csv` and sweeps `project_ownership`'s softmax
temperature against real contest ownership:
- **Hitter `gamma`: 3.5 → 1.5.** MAE improved on **all 6 dates** with real ownership data
  (e.g. 7/7: 4.42→3.69); Spearman rank correlation was flat throughout every date tested —
  softmax temperature only changes *concentration*, not *who's ranked ahead of whom*, so
  this was a pure calibration win with no ranking cost. Confirmed live: max predicted
  hitter ownership on a fresh real build dropped from routinely hitting the 65% chalk cap
  to a top value of 17.0%.
- **Pitcher `pitcher_gamma`: 6.0 → 7.0.** Messier evidence — 3 of 5 available dates
  preferred *higher* gamma (the original 6.0 was still under-concentrated, not over), but
  2 dates preferred lower. The n-weighted pooled MAE across all 5 picks 7.0. Flagged in the
  code comment as resting on thinner, more mixed evidence than the hitter fix, with a
  pointer to re-run the sweep as more slates accumulate.

**A bug caught along the way, not by the review:** wiring in DK's FPPG crashed the entire
draftables pull the first time it ran live — DK returns the literal string `"-"` for
players with no game history yet (rookies/callups), and `float("-")` doesn't fail
gracefully. Fixed and regression-tested before it could reach the phone app.

## 12. Cash-Game Investigation: A Floor Metric, Tested Before Trusted

Following §10's advice (cash games are the more tractable near-term validation target),
two gaps surfaced: no real cash/double-up contest data exists (every export so far is a
large-field GPP tournament, 475–1,189 entries), and CASH mode's construction had no concept
of *consistency* — it maximized mean projection only, treating a steady contact hitter and
an equally-projected boom/bust slugger identically, when a cash game (pay ~50% of the
field, flat payout) rewards the former and a GPP rewards the latter.

**The obvious hypothesis — falsified with real data before writing any code.** The
intuitive idea: HR-heavy hitters are boom/bust, so dock their score for cash-mode
selection. Tested directly against real 2025 game logs (60 qualified hitters, ≥400 PA):
raw std(game points) vs. season HR rate looked strong (**+0.847**) — but that's confounded
by mean (power hitters simply score more points on average, so their absolute swings are
bigger too). Normalized to coefficient-of-variation, the relationship **vanished** (−0.010).
Checked against bust-rate (fraction of games ≤1 DK point): **−0.21**, the *opposite* sign
from the boom/bust hypothesis. This idea does not survive contact with data and was not
implemented — the same discipline that killed platoon and team_total earlier in this doc.

**A more interesting, unplanned finding:** mean skill level *alone* strongly predicts
consistency (corr −0.55 with CV, −0.67 with bust-rate) — better hitters are already more
consistent hitters, largely because more ways to reach base is itself a floor mechanism.
This means CASH mode's existing pure proj-maximization was never starting from zero on
consistency; it was already leaning floor-ward as a side effect of maximizing skill.

**What did add real, incremental signal:** walk rate. `incremental_baseline_test(bust_rate,
mean_skill, bb_rate)` → incremental R² **+0.067**, coefficient t=**−2.80** (n=60,
significant at 5% even controlling for mean skill). A guaranteed way to reach base without
needing a hit (2 DK points, plus downstream run chances) is a genuine floor mechanism a
low-walk hitter lacks, and it isn't just riding on the same "better hitters are more
consistent" effect.

**Shipped:** `edge/dfs.py::BB_FLOOR_WEIGHT = 2.0`. Hitters get a new `floor` field
(`proj + BB_FLOOR_WEIGHT * bb_rate * PA_for_slot`) computed from season walk rate — data
already being pulled for the existing HR-rate ceiling term, so no new API calls. CASH
mode's optimizer objective changed from `proj` to `floor` (`edge/dfs_opt.py`); GPP is
untouched (still `lev` = ceiling faded by ownership). Pitchers get `floor == proj` — no
validated pitcher-specific signal exists yet, so this deliberately does nothing for them.
Displayed/logged `proj` and `ceiling` are unchanged; `floor` only steers which players the
cash optimizer selects, not what gets reported or calibrated against.

**Honest limitation, stated plainly:** this cannot be backtested the way the season-
freshness or bullpen fixes were — those needed only player-level projections, but this
changes *lineup construction*, which needs real historical DK salaries, and DK doesn't
serve those for past slates. This can only be forward-tested, exactly like the rest of the
hitter model. The weight (2.0) is deliberately modest and should not be raised without
re-validating on a bigger game-log sample — n=60 is a real, significant effect, but not a
large one to lean on heavily.

## 13. Team Exclusion — DK Voids a Game, the Generator Didn't Know

Caught live 2026-07-09: DK notified the user that BAL@CHC wouldn't count for a specific
contest (postponement, or a contest-scoring rule — DK doesn't expose which via any free
API), but the lineup generator had no way to know and built lineups using those players
anyway. Two mechanisms now address this, one automatic and one manual, because they catch
different causes:

**Automatic: `edge.dfs.team_game_status(date)`.** Cross-references every team against its
real MLB game status. A genuine gotcha found while building this: a postponed game's
`abstractGameState` is misleadingly **`"Final"`** (matches a normal completed game) — only
`detailedState` actually says `"Postponed"`. Checking `abstractGameState` alone (the way
the doubleheader-authoritative-game logic elsewhere in this codebase does) would silently
miss this entirely. Built as an allowlist of known-normal `detailedState` values (Final,
In Progress, Pre-Game, Completed Early, Scheduled, Warmup, and a couple of in-game states),
not a denylist — an unrecognized state should surface as a warning, not be silently waved
through. This catches real postponements/suspensions; it does **not** know about a
DK-specific "won't count for this contest" designation that isn't a change in the game's
actual status — that's a business rule DK doesn't expose.

**Manual: `exclude_teams` on `build_slate`.** A set of team abbreviations dropped from the
pool entirely, for exactly the DK-contest-rule case automatic detection can't see. Exposed
as `--exclude-teams BAL,CHC` and `--list-teams` (prints every team with its status flag) on
the CLI, and as a sidebar multiselect in the phone app — populated progressively via
`st.session_state` after each successful build (empty on the very first load of a session,
since there's nothing to learn the team list from yet) rather than a separate discovery
build, so this doesn't double the cost of every build just to learn the team list.

**A real, separate bug found while wiring this up, not by looking for it:** statsapi's
`team.abbreviation` for Arizona is `"AZ"`; DK's own draftables field says `"ARI"`. Hitters'
pool `team` field came from `team_abbrev_map()` (statsapi-based); pitchers' came directly
from DK's draftables. The result: an Arizona hitter and Arizona's own pitcher never matched
on team string, which silently broke the pitcher-vs-own-hitters constraint (§9) for this
one team specifically — an Arizona pitcher could in principle have been rostered against
Arizona's own hitters (or vice versa) without the safety check ever firing, because
`opp_team` lookups keyed on one spelling never found the entry filed under the other. Fixed
by normalizing at the source (`team_abbrev_map()` now maps through the same
`_STATSAPI_TO_DK_ABBR` table `team_game_status` uses), verified live (team 109 now resolves
to `"ARI"` everywhere), and regression-tested. This predates this session's other pitcher-
vs-hitter work entirely — it was just never exercised by a build that happened to roster
an Arizona pitcher against Arizona's own hitters until this investigation went looking at
team abbreviations for an unrelated reason.

## 14. A Phone Crash This Doc Can't Fully Explain — Said Plainly

The day after §13 shipped, the user hit a crash on the phone app pointing into
`cached_build` → `build_slate`. The pasted traceback cut off exactly at the call into
`build_slate` — Streamlit Cloud's error view doesn't scroll on mobile, so no further frames
or exception message were recoverable.

**What I could not do: reproduce it.** Ran the exact call path (`exclude_teams=()`) in both
cache mode and a real live props pull (35 credits spent) for the reported date. Both clean.
Then found why: that date fell on the last day before the All-Star break, and by the time
this investigation happened, MLB had moved into the break itself (July 10–16, confirmed
zero regular-season games scheduled) — the specific moment of the crash wasn't
reproducible after the fact, full stop.

**What I found and fixed anyway, because it's real regardless of whether it's *the*
cause:**
- `team_game_status`'s per-game loop used **unguarded direct dict indexing**
  (`g["teams"][side]["team"]`) — inconsistent with the defensive `.get()`-chained style used
  everywhere else in this codebase, and a genuine crash risk: any game entry with an
  unexpected shape raises an uncaught `KeyError`, and this specific loop had no per-game
  try/except (only the initial schedule fetch was guarded). Fixed: defensive `.get()` chains
  throughout, plus a try/except per game so one malformed entry can't take down the whole
  function.
- **Confirmed real, not hypothetical:** an All-Star Game entry (`gameType: "A"`) exists on
  2026-07-14, using `"AL"`/`"NL"` in place of real team abbreviations. Not a crash by itself
  (this specific entry had a complete structure), but it would have polluted
  `team_game_status`'s output with bogus non-DK-team keys, and there's no reason to process
  an exhibition game at all. Filtered to `gameType == "R"` in both `team_game_status` and
  `lineups_for_date` (the latter had the same unfiltered game list, though real team IDs
  don't collide with the All-Star pseudo-team IDs there, so the exposure was lower).

**Net honest read:** I can't tell you with certainty this was the exact bug, because the
traceback needed to confirm that was never recoverable. What I can say: the code now
defends against a real crash class (malformed schedule entries) it didn't defend against
before, and against a real non-hypothetical seasonal event (the All-Star break) that was
about to hit this exact code path within days. If the phone app crashes again after this
ships, that's a genuine "still broken" signal worth a fresh traceback, not a sign this fix
was wrong — they may simply be different bugs.

## 15. A Real Bug the Crash Investigation Found, But Not the Crash Itself

The user asked a good, testable question about §14's unreproduced crash: *"is it because the
slate is over?"* Investigating it directly didn't confirm that hypothesis, but surfaced a
genuinely different bug along the way — one that had been silently broken since §13 shipped.

**What was actually wrong:** the plain (unhydrated) `/schedule` endpoint's embedded team
objects carry only `id`/`name`/`link` — **never `abbreviation`**, confirmed for every team,
every game, on a full real slate day. `team_game_status` (§13) extracted abbreviation
straight from that payload. Even with the defensive `.get()` chains from §14's hardening,
this meant the field was simply never present — so the function silently returned `""` for
every team, every time, regardless of whether a game was actually postponed. The
postponement-detection feature had never worked, not even once, and nothing about how it
failed looked like a bug from the outside — it just never had anything to warn about.

**Fixed** by resolving team ID → abbreviation via the separate, already-reliable `/teams`
endpoint (the exact approach `team_abbrev_map()` already used, for the exact same reason)
instead of trusting the schedule payload to carry it. Verified live: `team_game_status`
now correctly resolves all 30 teams with proper DK-style abbreviations on a real slate day,
where it previously returned nothing at all.

**Said plainly: this does not explain §14's `TypeError`.** Four combinations (cache/live ×
empty/non-empty `exclude_teams`) all ran clean before and after this fix. The crash
investigation is still open — this section fixes a real bug the question led to, not the
bug the question was actually about.

## 16. Injured-List Players Slipping Into the Pool

The user caught a real, concrete bug from actual use: Carter Jensen (KC) was on the injured
list but still showed up in a real cash lineup the app built. Their question was direct —
*"Is there a way to search MLB for the IL players?"*

**Checked and ruled out, in order:**
- **DK's own draftables data.** `status`, `isDisabled`, `newsStatus`, `draftAlerts` on
  Jensen's entry: `"None"`, `false`, `"Recent"`, `[]`. Nothing usable — DK doesn't expose
  IL status through the free draftables feed.
- **MLB's roster `status` text field.** Pulled Jensen's `40Man`/`fullRoster` entries live:
  `{"code": "A", "description": "Active"}`. Also checked recent Royals transactions
  (7/1–7/10 window, 23 transactions) — none involved Jensen. **As of this check, MLB's own
  public data does not corroborate that Jensen is on the IL.** This is a genuine,
  unresolved discrepancy with the user's real-world report, not a case I could quietly wave
  away — either the injury is too recent to be reflected anywhere in MLB's systems yet, or
  the user's source is simply ahead of statsapi's own refresh cycle. Said plainly rather
  than assumed away.

**What did validate, checked against real examples on other teams:** comparing a team's
`rosterType=active` player-ID set against its `rosterType=40Man` set. Confirmed against five
real, currently-injured/optioned players across three teams — Alec Marsh (D60), Carlos
Estévez (D60), Aaron Judge (D10), Carlos Rodón (D15), Austin Warren (D15), Clay Holmes
(D60), plus several `RM` (Reassigned to Minors) — every one of them present on `40Man` but
correctly absent from `active`. The roster's own `status` text field can't be trusted at
face value (it's what said Jensen was "Active"); set membership is the signal that actually
held up.

**Likely mechanism for how this happens at all:** when a team's real lineup for the day
isn't posted yet, `lineups_for_date`'s projected fallback reuses that player's most recent
game's batting slot. If the player has gone on IL or been optioned since that last game, the
projection has no way to know — it just carries the stale slot forward.

**Fix**: `dfs.inactive_players(team_id)` returns `{norm(name)}` for every player on the
40-man roster not present on the active roster, cached per team (6hr `max_age`, same pattern
as `bullpen_k9`'s per-team cache). `build_slate` now filters the full pool (hitters and
pitchers) against this set, keyed by team abbreviation, right after the existing
`exclude_teams` filter.

**Live-verified**: re-ran a real build against today's slate. The general mechanism runs
clean against live data with no regressions (213-player pool, no crash). Jensen specifically
is *still* included — consistent with MLB's own data still showing him active as of this
check. This isn't a failure of the fix; it's the fix correctly reflecting what MLB's public
data currently says, which is the honest limit of what any API-based filter can do.

## 17. Does Strategy Change With Slate Size? Investigated Directly, Not Assumed

The user asked whether a small night slate (2-4 games) should be approached differently than a
large main slate (12-15 games) — a common DFS-community heuristic that had never actually been
tested against anything in this system. Two separate tests were run: a large, well-powered
backtest of the *projection model* across a stratified sample of 2025 slates, and an honest look
at what the 6 real contest slates already collected (§10) show about *ownership/construction*.

**Projection accuracy does not meaningfully change with slate size — tested, not assumed.**
`scripts/dfs_slate_size_backtest.py` reused the exact leak-free "+2025-to-date" model shape from
§3/§9 (the closest free proxy to current production) across 72 stratified 2025 dates chosen to
cover the full range of real slate sizes (3-17 games/day), oversampling the rare small-slate days
(only 17 exist all season with ≤8 games) rather than a plain contiguous window that would be
~90% 15-game main slates by default:

| slate size | games/day | hitter-games | corr | MAE |
|---|---|---|---|---|
| small | 3-8 | 1,872 | **0.210** | 5.613 |
| medium | 9-13 | 5,775 | 0.181 | 5.508 |
| large | 15-17 | 6,838 | 0.181 | 5.547 |

Small slates show a slightly higher correlation (0.210 vs 0.181), but a Fisher z-test on the
small-vs-large difference gives **z=1.16, p=0.246** — not statistically significant on 1,872 vs
6,838 rows. Honest read: **no evidence the projection model needs a slate-size-specific
adjustment.** The std(pred)/std(actual)-vs-|corr| compression check from §11 also holds
identically in every bucket (ratio 0.187–0.199 vs corr 0.18–0.21) — no differential dispersion
problem on small slates either.

**Ownership concentration plausibly changes with slate size — directionally, but too thin to
confirm.** Pitcher pool size scales roughly with game count in our own logged builds (11 SP at 6
games, 23 SP at 12 games — about 2/game, as expected). On the 6 real contest slates with
ownership data (§10):

| date | games | SP pool | max pitcher own% |
|---|---|---|---|
| 7/6 | 5 | 0 (partial build, pitcher props never logged that day) | — |
| 7/2 | 6 | 11 | 67.0% |
| 7/1 | 7 | 12 | 57.3% |
| 7/7 | 9 | 15 | 64.9% |
| 7/3 | 11 | 22 | 39.9% |
| 6/30 | 12 | 23 | 47.8% |

Directionally consistent with the fewer-arms-means-more-chalk intuition (the two smallest pools
show two of the three highest peaks) but not clean — 7/7 (9 games, 15 arms) also spiked to 65%,
and n=6 is nowhere near enough to fit a real trend. Flagged as an open, plausible-but-unconfirmed
hypothesis, not a shipped model change, per the standard set in §4/§12: a plausible intuition
doesn't get to steer construction just because it's plausible.

**Real ROI percentile: directionally favors small slates, at a sample size that can't support the
claim.** Splitting the 6-date ROI backtest (§10) by slate size: the 3 smallest-slate dates (5-7
games) averaged 76.8% cash-mode percentile and 62.4% GPP-mode; the 3 largest (9-12 games)
averaged 48.6% cash and 54.4% GPP. One of the small-slate dates (7/2) is the same build flagged
in §3 as built from a contaminated partial pool — excluding it, small-slate cash percentile rises
further to 72.1% (n=2). Stated for completeness, not as evidence of anything — 3 dates against 3
dates is not a result.

**Net read:** the one claim that could actually be tested at scale (does the *projection* need to
change with slate size) came back clean — no, on 14,485 hitter-games across 72 dates it doesn't.
The plausible construction-side effects (pitcher chalk concentration, whole-slate outcome
correlation when a lineup's fate rides on fewer independent games) are real mechanisms
structurally — a 5-game slate literally has half the arms and half the games of a 10-game slate,
that's not in question — but this system doesn't yet have enough real contest data at each slate
size to *measure* whether that structural fact should change GPP pitcher leverage or cash-mode
game selection. Worth revisiting once more real contest exports accumulate across a wider spread
of slate sizes; not worth shipping a model change on 6 data points.

## 18. Full-System Review: Model + Construction Overhaul (2026-07-11)

An 8-hour unattended review session: audit the whole methodology for holes, research
current MLB DFS strategy, and backtest candidate improvements. Everything below was
measured before it was shipped; two candidate ideas were re-killed by the same
discipline that killed platoon/team_total/umpire earlier.

**A DK rule the optimizer never knew: max 5 hitters per team.** DK's own editorial
site states it verbatim ("a maximum of five hitters from the same team"; pitchers
don't count). Nothing in `_valid()` enforced it — a chalky enough team could produce
a 6+-hitter lineup DK's entry validator would reject at upload. No logged lineup ever
actually violated it (checked all of them), but the new GPP construction below forces
5-stacks, which sits exactly at the cap — shipped as a hard constraint plus
team-cap-aware candidate filtering inside `_fill` (without which a dominant team's
leftovers made most fill iterations die at final validation). Regression test built
to fail pre-fix.

**Hitter model: three upgrades, backtested leak-free on 25,086 2025 hitter-games**
(Apr 15–Jul 31 boxscores; hyperparameters tuned on an April–May train window, all
reported numbers from the 13,801-row June–July test window; harness:
`scripts/dfs_model_lab_collect.py` + `dfs_model_lab_eval.py`):

| change | MAE | corr |
|---|---|---|
| production shape (baseline) | 5.565 | 0.166 |
| + empirical home/away PA tables | 5.467 | 0.168 |
| + opposing starter ERA (w=0.2) | 5.458 | 0.174 |
| + EB-shrunk skill rates (K=60) | **5.456** | **0.177** |

The combined change improves MAE on **56 of 57 test dates** (incremental-over-baseline
t=7.23). Shipped: `SLOT_PA_HOME/AWAY` (measured on 2,782 complete 2025 team-games —
the old flat table ran ~0.2–0.5 PA hot every slot and missed that away lineups get
~0.15–0.2 more PA because home teams skip the bottom 9th when leading),
`pitcher_era()` + a `w_era=0.2` matchup term, and `pooled_skill_rates(shrink_k=60)`
(empirical-Bayes shrinkage toward league average instead of the min-120-PA hard
cutoff — keeps 242 more real players in the skill table instead of flattening them
to league average). Sanity-checked end-to-end on the real 2026-07-09 slate:
MAE 5.631→5.537 vs the logged build.

**Platoon: killed a second time, same failure mode.** Re-attempted per §4's own
suggestion (bigger PA floor, shrunk weight, tuned on train): every configuration
lowered MAE but *also lowered correlation* on test (0.171→0.167 at the best setting) —
still a shrink-toward-the-mean artifact, not ranking signal. Not shipped, again.

**GPP construction: 4-stack chalk → 5-stack + secondary 3, leverage-picked.** Three
independent lines of evidence, weakest to strongest n:
- *Replay backtest* (`scripts/dfs_construction_replay.py`, rebuilds the exact logged
  pools for the 8 replayable slates and scores variants with real DK points +
  percentile in the real contest fields, 5 optimizer seeds each): old shape averaged
  **59.6%** percentile-in-field (range 56.5–64.7 across seeds); 5-stack leverage-picked
  **77.8%** (71.9–85.5); 5-3 double stack **74.3%** (66.1–82.3). No seed overlap with
  the old shape. n=8 slates — directional, not proof.
- *Stack-shape backtest on 2,782 real 2025 team-games* (`scripts/dfs_stack_shape_backtest.py`):
  teammate DK-point correlation is real and monotonic in batting-order distance
  (+0.167 adjacent → +0.107 at distance 4 — the §10 correlation mechanism, now
  measured); a 5-stack beats a 4-stack+one-off at the same five roster slots in the
  tails (P95 79 vs 76, P99 97.2 vs 96.2) for ~1 pt of mean; and a *correlated*
  secondary 3-stack beats three scattered one-offs at identical mean (P99 137.0 vs
  131.2). GPPs pay at the tails.
- *Published consensus* (Stokastic, DK Network, RotoGrinders, SaberSim): primary
  4–5 + secondary 2–3 is the standard winning shape; DK Network's own data note says
  the 5-cap binds ("four and five about the same… a good deal less upside than six").
Shipped in `build_slate`: stack team picked by **leverage** (Σ ceiling − 0.3×own,
was: raw projected chalk), `stack_n=5`, secondary 3-stack from the next team by the
same metric, player-level ownership fade 0.1→0.3. Cash construction unchanged — a
forced cash 3-stack was tested in the replay and was not better (55.3% vs 57.4%).

**Pitcher "live vs backtest MAE gap" (§10) resolved — it's sample hardness, not
model decay.** On the same 82 calibration rows: actual pitcher scores have std 13.36,
so a constant-mean predictor gets MAE **11.0** and salary-only regression gets
**10.34**. The model's 10.31 therefore sits where "field parity" should sit; the 7.18
backtest number came from a lower-variance sample, and chasing it with parameter
tuning would be fitting noise. (Bias is only +1.1 — the projections aren't
systematically high.)

**Grading bug: DK pitcher scoring includes −0.6 per hit batsman** (and +2.5 CG/+2.5
CG-shutout bonuses). `actual_pitcher_points` had none of these — every graded pitcher
actual ran ~0.2 pts/start high on average. Fixed with a regression test. Hitter
scoring was verified correct (no CS penalty on DK).

**Also checked, no action:** the randomized optimizer's optimality gap vs an exact
MILP solve on all 9 logged slates is ≤0.3 proj pts (0.0 on 7 of 9) — the heuristic
is not the bottleneck, no solver dependency needed. Name-collision risk in the
norm-name pool keying (two active players with identical names would cross-wire) was
checked live: none on the current slate; latent, low priority. A stale actuals-cache
bug surfaced during the replay (2026-07-08's cache had been written mid-slate with
25 players and never invalidated — `cached_actuals`/`load_proj_log_actuals` cache
without checking games went final); refreshed manually here, worth a real fix later.

**Not done, deliberately:** ownership-vs-Vegas-totals and totals-driven stack
selection would need historical game odds (credits) to validate against only 6
ownership slates — too thin to justify shipping untested logic; noted as the next
thing worth credits once more contest exports accumulate. The 2025 feature/boxscore
caches (`data/bt_boxscores/`, `data/model_lab_rows.json`, ~16MB) are kept as reusable
backtest infrastructure alongside the new scripts.

## 19. Phone-App "Placeholder" Bug, a Real Small-Slate Ownership Data Point, and Slate-Size Tracking

The user reported the phone app still showing "PLACEHOLDER — sample data" after spending Odds-API
credits on pitcher props for that morning's slate, and asked whether that's expected this early or
a bug. Diagnosed live rather than guessed: pulled real event odds for today's earliest game (Pirates
@ Brewers, first pitch ~3h out) and found DraftKings had posted only `pitcher_strikeouts` for that
game — not `pitcher_outs`, which `project_pitcher()` requires alongside K's before it'll return any
projection at all (§3's "core markets" design). Checking a mid-afternoon and a night game confirmed
both already had the full 6-market board. **Verdict: not a bug.** Sportsbooks stagger which starters
get full prop boards posted through the morning, exactly the way batting orders stagger through the
afternoon (already documented) — pitchers just never had an equivalent message. A live full build
minutes later (10 pitchers, 108 hitters) confirmed the pipeline works correctly once enough props
post; no credit/dry-run logic was at fault.

**Fixed:** the placeholder message named only "batting orders aren't posted yet" unconditionally,
even when pitchers were the actual blocker (as they were this morning) — a genuinely wrong diagnosis
shown to the user every time this specific case happened. Now checks `len(pitchers) < 2` and
`len(hitters) < 8` independently and names whichever is actually short, with a note that re-spending
credits won't help the pitcher case — only time will, since it's DK's own posting cadence.

**Also fixed while in this code:** the GPP construction changed to 5-stack + secondary 3-stack in
§18, but the CLI and app's on-screen labels still hardcoded "4-man stack" — stale display text left
over from the change, not caught by any test since nothing asserts on display strings. Both now show
the real construction, and `build_slate` now returns `stack2_team` so it's actually displayable.

**A real small-slate contest, and a concrete ownership finding.** The user played a 3-game night
slate for cash (`contest-standings-192176856.csv`, 23-entry field, 2026-07-10) — exactly the small-
slate case §17 flagged as "plausible but unconfirmed." Our logged cash build that night scored 85.30,
essentially identical to the user's actual entry (85.35, rank 16/23 — no cash), so the model's cash
pick matched what was actually played. The striking result is ownership: every one of the 11 rostered
players' predicted ownership came in under actual, by up to 3x —

| player | predicted own | actual own |
|---|---|---|
| Robbie Ray (P) | 65.0 (hit the hard cap) | **91.3%** |
| Shane Bieber (P) | 11.7 | 47.8% |
| Andy Pages | 12.3 | 52.2% |
| Ketel Marte | 16.2 | 47.8% |

MAE 22.05 across the 11 players (bias −19), versus ~3.9 MAE on normal-size slates. Robbie Ray's real
91.3% ownership **exceeded `project_ownership`'s hard 65% cap** — on a 3-game slate there simply aren't
enough viable arms, so the field piles on far harder than a softmax tuned on 10-15 game slates expects.
This is now 2 real small slates (this one; 7/2's 6-game slate in §17) pointing the same direction, with
this one far more extreme. **Not shipped as a gamma change** — n=2 small slates is thin evidence for a
shape claim, same discipline as every other rejected-on-thin-evidence idea in this doc. The 65% cap
being empirically breachable, independent of the slate-size question, is flagged as a cleaner
standalone candidate fix once more data confirms it's not a one-off.

**Slate-size + contest-type tracking added to the calibration pipeline (per user request), so this
question can be answered from accumulating data instead of one-off manual digs like the table above:**
- `resolve_slate()` had a real bug: the auto "Main (auto)" path (the app's default) never populated
  `meta["games"]`/`meta["start"]` — only the explicit-named-slate path did — so slate size was silently
  unavailable for the common case. Fixed; `build_slate()` now returns `games` (DK's own declared
  `GameCount`), and `log_forward_test` writes it to a new `games` column in `dfs_proj_log.csv`.
- `data/contest_meta.json`: a small manually-maintained manifest (contest id → `"cash"`/`"gpp"`) — DK's
  standings export carries no contest-type or entry-fee field, so this can't be detected
  programmatically (same limitation `dfs_roi_backtest.py` already documented for dollar ROI). All 8
  prior exports confirmed as GPP fields (475–1,189 entries, matching §12's existing claim); the new
  file tagged `cash`. Untagged future files default to `"unknown"`, not silently assumed `"gpp"`.
- `dfs_calibration.py` now stamps every row with `games` (from the new column, falling back to
  distinct-team-count/2 for rows logged before it existed) and `contest_type`.
- `dfs_ownership_gamma_sweep.py` now excludes `contest_type == "cash"` dates from the gamma fit
  entirely — per the user's own point: cash fields have no incentive to differentiate, so their
  ownership concentration isn't the thing GPP leverage gamma should be fit to, and mixing it in would
  bias the fit rather than just add noise.

**Known gap, stated plainly:** the 7/10 cash slate above still couldn't enter `dfs_calibration.json`
automatically — it was a sub-slate (Night) build, and `log_forward_test` only writes the shared
`dfs_proj_log.csv` for the **main** slate (by design, so a smaller sub-slate pool never clobbers the
main log). `dfs_calibration.py`'s date-matching only considers dates present in that file, so this
slate was invisible to it even after the tracking fixes above — the same class of gap as the 7/4/7/5
dates already noted missing in §7, now hit by a sub-slate instead of a missed run entirely. The table
above was computed by hand against the sub-slate's own lineup file. Not fixed here — would need a
per-draft-group projection log (mirroring the existing per-draft-group lineup log) and a calibration-
pipeline change to scan it, which is more scope than "start tracking slate size" asked for. Worth doing
if small/cash slates are going to be a regular source of data going forward.

## 20. A Local Dev Loop, and a Real Construction Bug It Caught Immediately

The user asked to stop relaying phone screenshots through email for every app error, and to
make the dev loop autonomous. Built `.claude/skills/run-dfs-app/`: a Python-Playwright driver
that runs the real `streamlit run app.py` server + a headless Chromium against it, so app
errors and Streamlit's crashed-app box are caught directly — same fidelity as the phone,
without the phone, and with **unredacted** tracebacks (Streamlit Cloud's error box explicitly
redacts messages; the local server's own stdout does not).

**First use immediately paid for itself.** The reported crash (`log_forward_test() got an
unexpected keyword argument`) turned out to be timing, not a bug — the user tested it before
Streamlit Cloud finished redeploying the just-pushed fix; reproducing locally against the
already-correct code confirmed this cleanly. But driving the GPP tab in the same session
surfaced a real, live construction bug the crash report never would have: the caption read
"5-man LAD stack + 3-man CHC stack" while the actual lineup had only **1** CHC hitter.

**Root cause, and why it's systemic, not a one-off:** `_secondary_stack()`'s original design
picked a *random* n-of-top-5 by leverage with no position awareness, then trimmed from the tail
if the combined group couldn't fill distinct slots. Checked against **every one of the 9 real
logged slates** (not just today's): the secondary stack **never once reached its target n=3**
in production — when the secondary team's best hitters were mostly OF (common) and the primary
5-stack had already claimed all 3 OF slots (also common), the random draw collapsed to 0-1
survivors. This means §18's shipped "5-3 double stack" wasn't actually forming as a real
correlated stack in real builds — closer to the "B" comparison arm that backtest measured as
*worse* (P99 131.2 vs 137.0), not the "A" arm that was shipped.

**First fix attempt (position-aware, still wrong):** rewrote `_secondary_stack` to greedily
build the best FEASIBLE combination in value order instead of a random slice. Re-checked
against all 9 slates: fixed 5 of 9 to hit a clean 3-man stack, but **completely failed** a 6th
(2026-07-08) — returned no lineup at all. Diagnosed directly: that date's true best secondary
group (Soto/Benge/Alvarez from NYM) was position-legal but **salary-infeasible** — $40,500 on 8
forced hitters left only $9,500 for 2 pitchers, and the cheapest real pair cost $12,700.

**Second fix attempt (per-iteration salary degradation, also wrong, caught by re-testing all 9
slates again):** tried the full secondary group first, degrading one player at a time within
the SAME iteration until something fit budget. This fixed 2026-07-08 but **regressed 5 of the 6
previously-fixed dates** — they degraded to scattered 1-2 one-offs instead of the achievable
3-stack. Root cause: raw "lev" doesn't reward stack completeness, so whenever an unconstrained
degraded lineup from one iteration happened to score higher than a full-stack lineup from
another, the plain `if score > best_score` comparison picked the higher-scoring but less-
correlated lineup — exactly the failure mode that's the entire reason primary-stack players are
locked against hill-climb swaps in the first place.

**Correct fix, shipped:** two-phase search. Try the FULL secondary target for the whole
`iters` budget first; only if that size is proven unreachable across every iteration does the
search restart at one size smaller. This never trades an achievable full stack for a
higher-scoring partial one, and still degrades gracefully when the full size is genuinely
infeasible. Re-verified against all 9 real slates a third time: **7 of 9 now hit the clean
5-3, the other 2 degrade to 5-2-1** (both confirmed genuinely salary-constrained, not a search
failure) — and confirmed live in the actual running app via the driver (2026-07-11: "5-man LAD
+ 2-man CHC stack", matching the real built lineup exactly). A synthetic regression test
(`test_gpp_secondary_stack_degrades_on_salary_infeasibility`) locks in the specific
salary-infeasible-but-position-legal scenario that broke the first fix attempt.

**Display fix, needed regardless of construction quality:** both the CLI and app captions now
report the lineup's ACTUAL team composition (counted directly from the built lineup) instead of
the construction *target* — a caption that can overclaim a stack that didn't fully form would
be actively misleading, not just imprecise, independent of how good the construction algorithm
gets.

**Also fixed in the same pass, unrelated:** the placeholder message always blamed "batting
orders aren't posted yet," even when the actual blocker was DraftKings not having posted the
full pitcher prop board yet (confirmed live: the earliest game that morning had only
`pitcher_strikeouts` posted, not `pitcher_outs`, which `project_pitcher()` requires alongside
K's). The message now checks pitcher and hitter counts independently and names whichever is
actually short; and the slate-picker dropdown now shows each slate's start time in ET alongside
the raw UTC value it already had (`Main 23:05Z (7:05 PM ET)`) — the user asked directly whether
those times were ET or UTC, a real point of confusion nothing on screen had answered before.

**Debug-log capture, added the same day a stale-deploy version of the log_forward_test bug
resurfaced (Streamlit Cloud hadn't redeployed yet when the user re-tested — not a new bug, but
a real reminder that its OWN crash box is actively unhelpful for diagnosing anything):**
Streamlit Cloud's crash screen explicitly redacts the exception message ("to prevent data
leaks"), which is exactly the box the user had been screenshotting and emailing. `app.py` now
wraps its whole render path in `render_app()` and catches any exception itself, before it ever
reaches that redacted handler — rendering the full unredacted traceback plus context (slate
date, live/cache mode, key presence, iters, excluded teams, Python/Streamlit versions) inside
an `st.code()` block, which Streamlit gives a one-click copy button for free. Verified end-to-end
by deliberately injecting the exact bug class this session started with (`log_forward_test(...,
totally_bogus_kwarg=True)`) via the local driver (§20) and confirming the rendered box carries
the real message — `TypeError: log_forward_test() got an unexpected keyword argument
'totally_bogus_kwarg'` — not a redacted one. `st.stop()`/`st.rerun()` are Streamlit's own
control-flow signals (subclass `BaseException`, not `Exception`) and pass through this wrapper
untouched, confirmed by checking their actual class hierarchy rather than assuming.

## 21. Two Real Bugs the Debug Log Made Diagnosable, Fixed Like Production Incidents

The debug-log feature (§20) paid for itself again immediately: the user hit the exact same
`log_forward_test` `TypeError` a second time, but with the full traceback in hand this time
instead of a redacted box. Checking GitHub's raw source directly (not trusting the local repo)
confirmed the fix WAS correctly on `main` — so this was Streamlit Cloud serving a stale build,
not a code regression. Separately, the user reported a real, concrete incident from actual play:
an "Early" slate build (2 hours before lock) silently included STL and LAD, teams that weren't
part of that slate, forcing a manual workaround; then later that same session, after the app
"stopped working" and needed a reboot, it crashed outright with `ODDS_API_KEY not set` even in
CACHE mode. Both investigated and fixed as real incidents, not guessed at.

**Bug 1 — wrong-slate resolution (the STL/LAD leak).** Reading the resolution path end to end:
`build_slate(client, date, draft_group, ...)` has the real slate date, but its call to
`resolve_slate(draft_group, groups)` never forwarded it — and `resolve_slate` itself didn't even
accept a `date` parameter. For a NAMED slate (the user had picked "Early" from the dropdown),
this fell through to `dfs.resolve_draft_group(draft_group)` with no date filter, meaning it
considered every same-named group across every date DK's lobby currently lists, not just today's.
Live-checked whether DK actually posts name-duplicate groups: confirmed yes — the same dropdown
session had shown two identical "Main 23:05Z" entries with different game counts (6g vs 14g).
`resolve_draft_group`'s tie-break for same-time candidates had no preference between them at all
(sorted only by start time; a true tie fell to whatever order the API happened to return) — unlike
`main_slate_group`, which already tie-breaks toward more games for the exact same reason. **Fixed
both:** threaded `date` through `resolve_slate` → `resolve_draft_group` (verified the specific
scoped-fetch for a live 3-game sub-slate today returned exactly 6 teams, confirming DK's
draftables endpoint itself is correctly scoped — the resolution layer was the gap, not the data);
and gave `resolve_draft_group` the same games-count tie-break `main_slate_group` already had.
**Added regardless of root cause, as a safety net:** `build_slate` now cross-checks the resolved
slate's own declared `GameCount` against how many teams actually showed up in its fetched
salaries, and surfaces a visible `st.error` if they don't roughly match — so a wrong-slate
resolution (from this cause or any other) shows up as an explicit warning instead of a silently
contaminated pool the user has to notice and hand-exclude, which is exactly what happened here.

**Bug 2 — a missing API key crashed the ENTIRE app, including free data.** `OddsAPIClient.__init__`
unconditionally raised `RuntimeError("ODDS_API_KEY not set")` if no key was present — before ever
checking cache, before any free endpoint ran. The user's key had only ever been pasted into the
sidebar each session (never configured as a persistent Streamlit Cloud secret), so a reboot lost
it, and the very next build crashed outright — in CACHE mode, which the sidebar's own text
explicitly promises works without a key ("Salaries + confirmed lineups (free) still work"). That
promise was never actually kept in code. **Fixed:** the key check moved from construction time to
the point of an actual network call (a new `NoApiKey` exception, parallel to the existing
`DryRunBlocked`), so a cache hit or a free endpoint never needs one at all. `build_slate`'s
`client.get_events(SPORT)` call — the ONE call outside the existing per-event try/except — is now
itself wrapped the same way the per-event loop already was (§9's own precedent, one level up):
a failure degrades to zero pitchers for that build, not a dead page. Verified directly: with the
key genuinely absent from every source (env, `.env`, Streamlit secrets), a full `build_slate` call
completed in 3.3s with 199 real hitters and 0 pitchers, no exception. Also fixed the "Pull fresh
pitcher props" button to check for a key upfront and say so plainly, instead of silently doing
nothing productive, and strengthened the sidebar's own warning to recommend a permanent Streamlit
Cloud secret (`Settings → Secrets → ODDS_API_KEY`) so a reboot can't lose it again.

**A red herring worth recording:** mid-investigation, a `build_slate` call appeared to hang for
75+ seconds inside `team_game_status` (two statsapi calls that normally take under 0.2s each).
Re-timed each call in isolation immediately after — both fast. Re-timed the full build with
nothing else competing for the machine's resources — 3.3s, clean. The likely cause was this
session's own test rig (a live Streamlit server, a headless Chromium, and a diagnostic script all
contending for the same container's CPU/network at once), not a bug in the function — worth
naming explicitly so a future session doesn't chase a phantom performance regression here.

Both fixes verified end-to-end via the local driver (§20): a clean build with a real key shows no
errors and a normal lineup; a build with the key removed from every source shows the strengthened
warning text and still produces real hitters. 86 tests pass (up from 77), including regression
tests for the date-threading bug, the game-count tie-break, the slate/salary mismatch check, and
`OddsAPIClient`'s full construct-without-a-key / cache-needs-no-key / uncached-call-needs-a-key /
dry-run-still-blocks-paid-calls / credit-floor-still-enforced matrix.

## 22. A Real Player-Identity Bug: Two "Max Muncy"s, One Silently Overwriting the Other

The user asked for a fresh architecture read of the whole codebase ("think like a senior engineer
who just joined"), which surfaced a "latent, low priority" name-collision risk in the pool-keying
logic — norm-name is the join key between DK salaries and statsapi lineup data everywhere in this
codebase, with no tie-break if two different real players ever share one. Two fixes into that same
review (the duplicated backtracking functions, the double team-list fetch), the user hit exactly
that risk live: **Max Muncy of the LAD showed up in a Main-slate build, but he wasn't in the Main
slate at all** — he plays the Afternoon slate that day. They correctly guessed the cause before I
even looked: a second, different, real "Max Muncy" (Athletics, $3,200 3B) exists this season.

**Confirmed directly, not assumed:** DK's own draftables for the Main slate that day price a real
"Max Muncy" at $3,200 for ATH (`ATH @ CWS`); the Afternoon slate separately prices a different real
"Max Muncy" at $5,600 for LAD (`ARI @ LAD`) — two distinct, correctly-DK-priced players who happen
to share a name. `lineups_for_date()` pulls every one of that day's games LEAGUE-WIDE (never
slate-scoped) and keyed its output by `norm(name)` alone — so when both real players' games got
processed, whichever ran later in the loop silently overwrote the other's entry. The survivor was
LAD's (a *projected*, unconfirmed fallback entry, at that). `build_slate()` then joined that
single squashed entry against the Main slate's *own* salaries by name only, producing a pool entry
with ATH's correct $3,200 salary but LAD's team, opponent, batting slot, and skill rate stitched
onto it — a real Frankenstein identity, not a display glitch. The `"team"` field came from
`lu["team_id"]` (LAD), never cross-checked against the DK salary entry's own `"team"` field (ATH),
so nothing in the code ever had a chance to notice the two didn't match.

**Fixed at the source, not patched at the display:** `lineups_for_date()` and `season_hitting()`
(which had the identical bare-name-key structure and could exhibit the same failure independently)
now key their output by `(team_id, norm_name)` instead of name alone, so two same-named real
players never collide in the same dict slot. `build_slate()`'s hitter loop now unpacks that pair
and verifies DK's own `salaries` entry actually belongs to the same team the lineup entry claims
(`info["team"] == team_abbr`) before merging anything — a mismatch (the ATH/LAD case exactly) is
skipped rather than stitched together. `season_hitting`'s on-disk cache had to be regenerated (JSON
has no tuple keys, so the composite key is packed as `"team_id|name"` on disk); the stale
old-format file would have raised on first read after this shipped, so it was force-regenerated
and verified before commit, not left to fail in production.

**Verified against the real live data that exposed the bug, not just synthetic tests:** the Main
slate now shows zero "Muncy" entries at all — ATH's Max Muncy has no confirmed or projectable
lineup entry of his own today (a separate, pre-existing, unrelated gap: he simply has no batting-
order data available yet, so he correctly can't be projected), and LAD's is correctly excluded as
belonging to the wrong team for this slate. The Afternoon slate — which the user had confirmed was
already showing LAD's Max Muncy correctly — still does, unchanged (`team=LAD, $5,600, slot 5`),
confirming the fix didn't regress the working case while fixing the broken one. A new regression
test locks in the exact scenario (two same-team-id-collision players, one priced in the slate, one
not, the wrong one positioned to "win" a bare-name dict) and checks the resulting pool entry uses
the priced player's own team, slot, and skill rate throughout, not the other's.

**Pitcher side checked, not changed:** the pitcher pool's `"team"` field is sourced directly from
DK's own salary entry (`info["team"]`), never merged in from a separate statsapi lookup the way the
hitter side was — so the exact "wrong team displayed" failure mode isn't structurally possible
there today. A theoretical adjacent risk (two same-named pitchers, one's DK entry matching under a
different real player's props) hasn't been observed and wasn't chased further without evidence,
consistent with this project's own standard of fixing what's demonstrated, not what's merely
conceivable.

87 tests pass (up from 86).

## 23. First Real Forward-Test Since the §18/§21/§22 Fixes — Projection Holds, Placement Doesn't

2026-07-12 is the first fully graded slate since the model/construction overhaul (§18), the
app/data-integrity fixes (§21), and the name-collision fix (§22) — the first real read on whether
any of it changed real-world outcomes, not just backtests.

**Projection: matches or beats the backtest, encouragingly.** Forward grade (`scripts/dfs_grade.py`,
n=213 matched players): hitters corr **+0.185** MAE **4.82** (n=196) vs the §18 backtest's test-window
target of corr 0.177 / MAE 5.456 — this slate came in *better* than the backtest on both axes, a good
sign the shipped model changes are real and not just backtest artifacts. Pitchers corr **+0.514** MAE
**7.37** (n=17) — MAE is now close to the 7.18 backtest anchor (§10 had flagged forward pitcher MAE
running consistently near 10.31 across 5 earlier dates; this one slate lands right back near the
anchor, though n=17 is too small on its own to say the earlier gap is resolved). Bias −0.94 (actual
ran under projection) — worth watching, not alarming at n=1.

**Ownership gamma re-swept with this as the 7th real GPP-ownership date** (`dfs_ownership_gamma_sweep.py`,
correctly auto-excluding 7/12's *cash* file per §19's fix, including only its *gpp* file): pooled,
n-weighted MAE confirms `pitcher_gamma=7.0` is still the minimum (4.728, vs 4.792 at 6.0 and 4.802 at
8.0) — the shipped default holds. Hitter `gamma` shows a small, consistent-direction signal toward
lower than 1.5 (pooled MAE 3.662 at gamma=1.0 vs 3.691 at 1.5, and 1.0 beats 1.5 on every individual
date) — but 1.0 is the *edge* of what's been swept, so this isn't shipped: the responsible next step
is sweeping below 1.0 before committing to a change, not extrapolating past the tested range.

**Real-field placement: the first true cash-field data point, and it's below the historical proxy.**
2026-07-12 is the first date with BOTH a real cash contest and a real GPP contest for the same slate
— previously, "cash-mode" placement could only ever be checked against a GPP-sized field (no real
cash field existed yet), an imperfect proxy §10 used for lack of anything better. This slate's actual
cash-mode-vs-cash-field result: **30.4th percentile** (23-entry field) — did not cash, and materially
below the 62.7% average the GPP-field proxy had shown across the prior 6 dates. The properly-matched
GPP-mode-vs-GPP-field running average also moved from 58.4% (6 dates) to **54.3%** (7 dates) with this
slate's 31.0% pulling it down. Both builds underperformed the field this slate.

**Net read, stated plainly:** better projection accuracy this slate did not translate into better
field placement — the same tension §10 already named (ranking quality and construction/leverage are
different problems). One slate is not enough to say whether the identity/construction fixes helped,
hurt, or are neutral for real placement; it IS enough to say the projection layer itself is behaving
as backtested, which was the open question after so much model surgery in one sitting. Keep
accumulating dates before drawing a construction-quality conclusion from percentile alone.

## 24. "I Had My Key Entered" — A Silent Failure With No Way to Tell Which of Four Causes It Was

The user reported trying to build lineups from the phone with their API key entered, and getting no
prop data and no lineups. Tested the reported key directly against the live Odds API at the same
time: it worked fine (28 real events came back), ruling out an expired key or an Odds-API outage.

**Root cause 1 — a real UI ordering bug.** The sidebar rendered the "Pull fresh pitcher props" button
*before* the `ODDS_API_KEY` text input that actually commits a typed key into `os.environ`. Across two
separate Streamlit reruns this doesn't matter (`os.environ` persists between them), but it's exactly
the kind of interaction-ordering hazard a mobile browser can hit if a key-entry and a button-tap don't
resolve into two cleanly separate reruns. Fixed by moving the key input above both action buttons —
correct regardless of whether this was the exact mechanism this time.

**Root cause 2, the more important one — every possible failure degraded identically.** `build_slate`
caught `get_events()` failures and per-event `get_event_odds()` failures with a bare `except Exception`
and silently produced an empty pitcher pool either way. That means "no key," "a key that's present but
rejected by the API (401)," "credit floor hit," and "a real network outage" were **all indistinguishable
from each other and from the completely normal 'DK hasn't posted props yet' case** — exactly the
scenario the user hit: a key that looked entered, with zero way for the app (or the user, or this
investigation) to tell what actually went wrong.

**Fixed:** `build_slate` now captures the real exception from `get_events()` and — separately — tracks
whether *every single* per-event odds call failed (a systematic signal: a normal slate always has SOME
events without markets posted yet, so 100% failure is qualitatively different from "some events are
early"). Either case populates a new `pitcher_fetch_error` string in the result, and both the app and
CLI now show it as a distinct, specific error — "pitcher props pull failed: `<real reason>`" — instead
of folding it into the generic timing-based placeholder message.

**Verified live, not just unit-tested:** relaunched the app via the local driver (§20) with the
reordered sidebar, confirmed CACHE mode still shows the correct benign placeholder (no false
`pitcher_fetch_error`), then clicked "Pull fresh pitcher props" for real — 8 pitchers priced, 82 real
credits spent, a complete lineup built, no errors. Three new tests lock in the distinction: a
get_events-level failure surfaces its real message; a systematic (100%) per-event failure surfaces
too; a normal slate with just some early/marketless events does NOT falsely trigger the new error path.

89 tests pass (up from 87).

## 25. Two Recurring Gaps, Both Closed: Sub-Slate Forward-Testing and the Slate Picker's Real Bug

The user shared real Night-slate contest results (cash + GPP, 2026-07-17) and separately reported a
live `slate_mismatch` error trying to build a Turbo slate ("resolved slate claims 7 game(s) but
salaries cover 16 teams") plus a phone build that didn't pull pitcher props. Both threads led
somewhere real.

**Sub-slate builds were never logged anywhere — the second time this has cost real data.** A named
sub-slate (Turbo/Night/Afternoon, `is_main=False`) only ever wrote its lineup file, never its
projections — §19 already lost a cash slate's data to this exact gap on 2026-07-10, flagged as
"worth doing if small/cash slates are going to be a regular source of data." They are: the 2026-07-17
Night slate (cash 8.1st percentile, GPP 84.0th — a real, useful split result) hit the identical wall.
Fixed: `log_forward_test` now writes sub-slate projections to `data/dfs_proj_log_<date>_g<gid>.csv`;
`dfs_calibration.py::load_proj_log()` merges these in alongside the main log. This doesn't recover
7/10 or 7/17 (the fix is forward-only), but no future played sub-slate should lose its data again.

**Found and fixed while wiring that in: a stray verification build could contaminate real ROI
numbers.** A Main-slate build logged from local testing on 2026-07-17 sat under the same date the
user's real Night slate was later played and contested. `dfs_roi_backtest.py`'s ground-truth date
matching correctly found *a* date match — but silently scored the wrong slate's players against the
Night contest's real leaderboard, since nothing checked that the "matching date" lineup was actually
for the *same slate* as the contest. Checked empirically against every date already on record: a
genuinely correct match always shows 9/10 or 10/10 player overlap with the contest's own ownership
board; the wrong-slate case measured 5/10 and 0/10. Added `lineup_matches_contest()` — anything below
"at most one player missing" is now skipped with a clear message instead of silently printing a
meaningless score.

**The `slate_mismatch` warning did its job — investigating it surfaced the real, deeper bug.** Couldn't
reproduce the user's exact historical numbers (DK's lobby had already moved on by the time this was
investigated), but building today's actual current Turbo slate directly (by numeric id) came back
completely clean — correct team count, a real live pull, no mismatch. That pointed at the picker
itself, not the resolution logic: `app.py`'s slate dropdown built its options from `(name, id, time,
games)` tuples but passed **the bare name** to `build_slate`, not the id. DK can post two same-named
slates (confirmed same-day duplicates exist right now: two real "Turbo" slates today). The dropdown
*looked* like it was letting you pick a specific slate — each row showed a distinct time — but every
row resolved through `build_slate`'s own independent "soonest future, most games" tie-break,
completely ignoring which one was actually clicked. Fixed: the dropdown now passes the numeric id.

**A second, compounding bug in the same picker, found while fixing the first:** the dropdown's own
slate list was never filtered by the selected date at all — it mixed every upcoming slate across
every date together, distinguishable only by a bare `HH:MM` with no date shown. `list_slate_names()`
now filters by date, but naively (`StartDate[:10] == date`) would have silently hidden a real slate
from "today"'s list: anything starting after ~8pm ET is already tomorrow in UTC (confirmed live: a
"Late Night" slate at 02:05 UTC is unambiguously tonight's slate in ET, but a UTC-string-prefix
filter would exclude it from today entirely). Fixed with a proper ET (`America/New_York`) calendar-
date comparison instead of a raw string match.

**Verified live, not just unit-tested:** relaunched the app after all four fixes. The dropdown for
today now shows exactly the real, distinct, currently-open slates (Night, Late Night, Turbo, Main —
no cross-date mixing, no duplicate ambiguity); selecting the Turbo option built cleanly with the
correct 6 teams / 3 games, no `slate_mismatch`, no errors. Six new tests cover the ET-aware date
filter, same-day duplicate slates staying distinct and selectable, the sub-slate proj-log write/merge,
and the ROI backtest's wrong-slate detection at both a real match (10/10, 9/10) and the actual
measured mismatch (5/10, 0/10).

94 tests pass (up from 89).

## 26. The Slate Mismatch Warning Caught a Real DK Data Bug, Live — Not a Resolution Bug

Minutes after the §25 fixes shipped, the user pushed, rebooted the app, pulled fresh pitcher props,
and hit the `slate_mismatch` warning again on the AUTO-resolved Main slate ("claims 7 game(s) but
salaries cover 16 teams"), plus a new, clear error from the §24 fix: "all 15 event odds calls failed,
e.g. HTTPError: HTTP Error 404: Not Found."

**Reproduced live, immediately, on the real current slate.** `main_slate_group()`'s auto-resolution
(the `draft_group=None` default — a completely different code path from §25's dropdown-id fix, which
only applies to named sub-slates) resolved to a real draft group declaring 7 games, whose own
draftables response showed 16 teams. This was NOT the dropdown/resolution bug from §25 — it reproduced
identically via the plain auto-default path.

**Root-caused precisely, not assumed.** The group's declared `GameCount: 7` and its actual matchups
(`matchup` field on each draftable) agreed perfectly: exactly 7 real games, 14 teams, fully internally
consistent. The extra 2 teams (NYM, PHI) were a **DK-side data bleed** — checked against the real MLB
schedule: NYM @ PHI was a genuine game that day, just in a completely different time window (3:05 PM ET)
than this slate's actual games (4:10 PM ET + a doubleheader nightcap). DK's draftables response for
this specific draft group had somehow included the two unrelated teams' full active rosters — every
single one of ~90 NYM/PHI entries (from Bryce Harper and Juan Soto down to bench players) carried
`matchup: None`, while every single one of the 14 real-slate-team entries had a populated matchup.
A perfect, exact discriminator — not a heuristic guess.

**Fixed at the source.** `fetch_draftables()` now skips any entry with no `matchup` (`competition.name`)
at all. Verified against the exact same live, contaminated draft group: teams dropped from 16 to the
correct 14, entry count from 641+ to 641 clean ones, and a full `build_slate()` re-run came back with
`slate_mismatch: None`.

**The pitcher-props 404s were not reproducible and are believed transient.** Directly tested all 13
real events for this exact slate live, immediately after the report: every single one succeeded (some
simply had no DK book posted yet, the normal case already handled gracefully). Whatever caused a
uniform 404 across all 15 calls at that exact moment did not persist minutes later — most likely a
brief Odds-API-side hiccup, not something in this codebase to fix. Worth stating plainly: the §24
error-surfacing fix did exactly its job here — instead of a silent empty pitcher pool, the user got a
specific, actionable message, which is what made this transient issue distinguishable from a real,
persistent problem in the first place.

**Verified live, end to end:** relaunched the app after the fix — "Main (auto)" now builds cleanly,
641 correctly-filtered salaries, no mismatch warning, real players from only the 14 legitimate teams.
One new test locks in the exact contaminated shape found live (a real entry with a matchup alongside
a bled-in entry with none), and the existing `dk_fppg`-dash regression test was updated to include a
real matchup (it had been using an empty `competition: {}` for legitimate players, which this fix
would now have — correctly — excluded).

95 tests pass (up from 94).

## 27. The Exclude-Teams Widget Had No Options On The First Build Of A Session

The user reported two things after a slate with a PIT@CLE doubleheader and a live NYM@PHI game: they
couldn't pick any team to exclude in the app, and thought PIT/CLE and NYM/PHI might be rained out.

**The rainout read was checked directly against the real schedule and wasn't right.** PIT@CLE that day
was a plain doubleheader (game 1 already `Final`, game 2 `Scheduled` later — both normal states, so
correctly un-flagged). NYM@PHI was `In Progress` at the time, not postponed. The only two teams the
app's own game-status check actually had reason to flag that day were BOS and TB (`Delayed Start`,
weather) — exactly what the sidebar's warning banner showed.

**The exclude-teams bug was real, though, and reproduced immediately** via the local Playwright driver.
The sidebar's "Exclude teams" multiselect read its `options` from `st.session_state["all_teams"]`,
written only at the END of `render_app()` — i.e. from the PREVIOUS build. On the very first build of a
fresh session (the exact case the user hit: open the app, look at tonight's slate for the first time),
that session-state key doesn't exist yet, so the widget rendered with zero options and the literal
placeholder text "No options to select" — not a subtle bug, just genuinely un-clickable on first load.

**Root cause: the multiselect's options depended on a full `build_slate()` run that hadn't happened yet
in the CURRENT script execution**, and Streamlit session state only carries data forward from a run that
already finished. The fix factors the cheap part of that dependency — which teams are in the slate, and
their game-status flags — out of `build_slate()` into a new `dfs_run.team_list_for_slate()`: same free
statsapi + DK-draftables calls, no Odds-API cost, no optimizer. The app calls this (wrapped in its own
5-minute `st.cache_data`, same pattern as `cached_slate_names`) BEFORE rendering the multiselect, so the
widget's options come from THIS run, not the last one. `st.session_state["all_teams"]`/`["team_status"]`
were dead code after the change (nothing else read them) and were removed rather than left stale.

**Verified live, end to end**, via the local driver on a completely fresh app process (no prior build in
that session): the "Exclude teams" dropdown listed real teams (ARI, ATL, BAL, BOS ⚠ Delayed Start, CLE,
HOU, ...) with the game-status warning icon already attached, on the very first build — before this fix,
that same first-load state showed "No options to select."

Three new regression tests cover the happy path, the unpriced-slate case (empty options, no crash), and
a bad slate name (returns an error string, not an exception) for `team_list_for_slate` directly.

98 tests pass (up from 95).

## 28. Verifiable, Not Just Asserted

- **Test suite**: 98 tests, all passing (`pytest -q`), including regression tests for
  every bug in §9, §11, §12, §13, §14, §16, §18, §19, §20, §21, §22, §24, §25, §26, and §27 — each
  constructed to fail against the pre-fix code and pass against the fix, not just exercise the happy path.
- **Calibration dashboard** (live, updates as new contest data comes in):
  actual-vs-predicted scatter plots for points and ownership, pitchers and hitters
  separately, built directly from DK contest exports joined against logged predictions —
  https://claude.ai/code/artifact/7ef69d33-1ccd-49a6-bd2c-48337f6c3de7
- Every backtest number in §3/§4 came from either cached free box-score data (statsapi,
  no cost) or real DK contest exports the user downloaded — nothing here is simulated or
  assumed.

---

*Everything above is measured against real data — backtests on cached box scores, or
forward tests against actual DK contest results — not modeled expectations. Where a number
is small-sample and noisy, that's said explicitly rather than rounded up.*
