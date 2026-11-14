# edge_search: skeptical system review (2026-11-14)

Reviewer brief: argue against the current design. Sources: SYSTEM_OVERVIEW.md,
`data/dfs_scorecard.json` (built 2026-11-14, DK history through 11/12),
`harness/critique_gate.py`, `harness critique-loop --status`, the `*_CRITIQUE_LOG.md` files,
`deploy/`, git history. Ratings: value (H/M/L) vs effort (H/M/L).

## 0. The one number that frames everything

| sport | entries | slates | fees | net |
|---|---|---|---|---|
| **all** | 346 | 82 | **$505** | **+$116** (ROI +23%, 90% [-15%, +61%]) |
| NFL | 47 | 11 | $95 | **+$179.50** |
| NCAAF | 25 | 11 | $45 | +$32 |
| NHL | 102 | 30 | $135 | +$13 |
| MMA | 9 | 4 | $11 | +$5 |
| NASCAR | 10 | 5 | $9 | -$6 |
| NCAAB | 18 | 7 | $21 | -$13 |
| MLB | 67 | 38 | $57 | -$18 |
| NBA | 68 | 21 | $132 | **-$76** |

- Total handle in 4.5 months is **$505**. The whole profit is NFL; **ex-NFL the system is -$63**
  on $410 of fees. NFL itself is 11 slates with a GPP ROI of +251% that is almost surely
  driven by a few small-field / overlay hits (see section 2), not by a projection edge
  (NFL R² 0.32, DST R² 0.05).
- Against that: ~4,650 commits since 6/28, 34 top-level .md files (~16,300 lines),
  33 timer units in `deploy/`, 8 DFS sport models, ~100 critique cycles across 10 logs.
  The engineering-to-money ratio is the central finding of this review. At $2-3 average
  entry fees, even a real +10% edge is worth ~$10/week. The system is a research hobby
  priced as a business; decide which it is.

## 1. Is build -> test -> ship effective?

### 1a. What the gate measures: proxies, not money
`harness/critique_gate.py` compares candidate vs main on per-row squared/absolute error
(minutes RMSE, DK points MAE) or, for a few labs, lineup field percentile. **No gate
anywhere scores dollars.** The repo's own evidence says the proxy and the money are weakly
connected:
- `GPP_META.md` / memory: "ownership no ROI effect at fixed projection", "props-vs-ROI null
  across sports".
- NBA has the best projection by far (R² 0.55, the most critique effort: 41 minutes cycles +
  4 ownership) and the **worst money** (-58%, 21 slates, interval [-82%, -35%] on GPP — the
  only sport whose loss is clearly beyond noise besides MLB GPP).
- NFL has middling projection and all the profit.
Lowering minutes RMSE 5.84 -> 5.71 (cycle 25, the flagship ship) is a 2% proxy improvement
that cannot be seen in ROI at any sample size Adam will ever generate. Value H / effort M to
fix: add a dollar-denominated replay (lineup vs the actual contest payout curve, which
`contests.db` already holds) as the gate's guard metric, or accept the gate is a model-
hygiene check, not a profit engine.

### 1b. Statistics: mostly sound mechanics, unsound program
Good: cluster (night) bootstrap, coverage >= 99% and identical-truth checks (prevents
moving the goalposts), judge files protected, 7-day forward re-gate. This is better than
most hobby setups.

Problems:
- **Multiple comparisons.** Pass = *any* primary state's 90% interval below 0. Each state
  alone has a ~5% one-sided false-pass rate; with several states, a guard tolerance of 1%,
  and ~100 cycles, a handful of noise passes is expected. The "every other point estimate
  <= 0" rule helps but is not a correction. Use one pre-registered primary metric per cycle,
  or Holm/Bonferroni across states. (H/L)
- **Garden of forking paths upstream of the gate.** The scout + critic search many holes on
  the same historical nights, then the gate tests on those nights' held-out split. The
  hold-out is reused every cycle (NBA: 41 cycles against overlapping 2025-26/2026-27 data).
  The forward re-gate is the only truly fresh test, and 7 days is ~3-7 slates: below the
  repo's own "~5 slates" floor for some sports. (H/M)
- **min_clusters = 5** is the bar for "better". Five nights is the same bar the repo uses to
  say "not enough data". (M/L)
- **Seed 0, 2,000 draws** fixed: fine for reproducibility, but the interval is not re-drawn
  so borderline cases are frozen. Minor.

### 1c. How much did it ship, and did it move results?
Scorecard `critique_cycles`: **40 logged cycles, 7 shipped**, and of those one (NCAAB 11/8)
is a lab fix with change 0.000. Status today: NHL and NCAAF paused (3-streak of nothing,
until 12/05 and 12/01), NASCAR and MMA on 2-streaks, NBA "no commits" last run. The
unattended loop's realised output is close to zero, while its cost is a nightly
multi-agent run plus review overhead (NHL cycle 15 spent a night discovering an input
was never collected; NCAAF cycle 9 "wins but thin").
Did shipped changes move ROI? **Unmeasurable**: every shipped change is <= 7 days old, the
affected sports have 7-21 slates total, and nothing links a ship to a before/after ROI
window except `code_updates` timestamps. Verdict: the loop is effective at *refusing*
noise (good) and ineffective at producing money. (stop/shrink: H value, negative effort)

## 2. Where the money is (by the repo's own data)

Contest-group rows from `dfs_scorecard.py show` (model era, >= 5 slates):
- GPP field 26-100 entries: **+327%** [+95, +559] 16e/12s; GPP cap 26-100: +319% [+164, +473].
- GPP ended in overlay: **+168%** [+19, +318] 69e/23s. Cash overlay +39%.
- GPP 501-5,000 entries: **-33%** [-64, -1] 80e/59s — the largest bucket by volume and a
  loser beyond noise.
- Cash 1-2 game slates +85% [+60, +109] 9e/8s; cash 5-7 games -47%.
- Overlay alert sent: GPP +93% vs no alert -5%.

These are the only effects in the whole system with intervals clear of zero and decent n,
and they are **contest-selection** effects, not projection effects. They are the same
lever across sports — which also explains NFL: small, overlaid NFL GPPs. Meanwhile 80
entries (38% of GPP volume) went into 501-5,000 fields that lose.

Effort allocation is inverted: ~95% of critique cycles target projections/ownership
(the proxies with null ROI links); the overlay model has had 5 cycles and pick'em 2.
Recommendation: treat contest selection as the product. A rule as simple as "GPP only in
<= 100-entry or overlay-alerted contests; skip 501+ fields" would have removed the -33%
bucket. Caveat: the selection rows are also post-hoc slices of 82 slates (forking paths
again) — so pre-register the rule now and forward-test it, don't back-fit more slices.
(Value H / effort L)

Projections: still matter as table stakes (bad availability = zeroed lineup), so keep
availability/late-swap work (NBA late-swap miss 11/1 is a real money event). Ownership and
construction: the repo's own GPP_META says null at fixed projection; deprioritise.

## 3. What is missing

| gap | evidence | value / effort |
|---|---|---|
| **Kill criteria per sport** | NBA -58% over 21 slates with an interval excluding 0 and the most model effort; MLB GPP -37% [-72, -1]; NCAAB -60%, NASCAR -62%. No rule says "stop entering". | H / L |
| **Bankroll + stake sizing** | no bankroll/Kelly logic in the DFS path (grep hits are betting/arb only). Fees are flat and tiny; variance per slate (median slate ROI -46%) argues for sizing by contest type, not sport enthusiasm. | M / L |
| **ROI feedback loop** | scorecard is descriptive; nothing feeds ROI back into what gets entered or which sport gets critique time. The scout reads "where money is going" but optimises proxies. | H / M |
| **Sample-size planning** | at ~20 entries/week, detecting a 10% ROI edge in GPPs (slate SD ~1.5) needs on the order of 1,000+ entries — years. State this; pick questions answerable at this volume (selection rules with big effects) and stop asking ones that aren't (2% RMSE). | H / L |
| **Pre-registration log** | contest-group "best/worst" slices are chosen after the fact. Write the rule + metric before the next 30 slates. | M / L |
| **Ship -> ROI attribution** | `code_updates` exists; add a before/after ROI per shipped change with n, flagged "not enough data" (which it will be — that is the point). | L / L |
| **Entry tracking at decision time** | DK exports are after the fact; record intended contests/lineups at entry so selection rules can be audited (partially exists via forward log). | M / M |

## 4. What is overbuilt

- **Eight sports at ~$500 total handle.** NASCAR (10 entries), MMA (9), NCAAB (18) have
  models, apps, mirrors, critique logs and timers for <$25 of fees each. Each sport is a
  permanent maintenance tax (name matching "fix in every sport", mirrors, page cache
  isolation tests). Freeze to 2-3 sports. (H value / negative effort)
- **Nightly unattended critique loop.** 7/40 shipped, mostly not money-linked, two sports
  already auto-paused, and it competes for the same desktop that runs time-critical
  timers. Run it weekly and only for a sport that passes a "money gate" (enough slates,
  non-negative selection-adjusted ROI). (H / L)
- **Docs:** 34 top-level .md files, ~16k lines, plus memory, plus per-sport critique logs
  (NBA minutes log alone is long). Docs-of-docs (SYSTEM_OVERVIEW, CLAUDE.md, STATUS, META,
  METHODOLOGY) repeat the same numbers that `harness status`/scorecard already compute and
  therefore drift. Keep scorecard + one STATUS per active sport. (M / L)
- **33 timer units** for one user: buzz/podcast/transcript collectors whose own findings
  are "touts = coin flips" and "crowd doesn't overreact" (memory: gpp_meta_findings,
  inverse_consensus) keep running. Retire collectors whose research verdict was null.
  (M / L)
- **Betting side** (arb, parlays, pick'em) is out of scope of the scorecard entirely — if
  it has P&L it isn't tracked alongside DFS, so it can't be compared for effort.

## 5. Top-5 recommendations (ranked)

1. **Make contest selection the product (H/L).** Pre-register: GPPs only in <= 100-entry
   fields or overlay-alerted contests; no 501+ GPPs; cash on short slates. Forward-test 30
   slates with the scorecard. This is the only lever with an effect beyond noise.
2. **Stop entering losing sports until they earn it back on paper (H/L).** Kill rule: a
   sport whose model-era ROI upper 90% bound is < 0 over >= 15 slates goes paper-only
   (forward log keeps grading). Today that is NBA GPP and MLB GPP; NCAAB/NASCAR are close.
3. **Stop the nightly critique loop; run it weekly with a money gate (H/L).** Add a dollar
   replay (lineup vs the contest's real payout table in contests.db) as a guard metric, a
   multiple-comparison correction, and a fresh-forward-only final test of >= 10 slates.
4. **Cut to 2-3 sports and retire null-result collectors (M-H/L).** Keep NFL, NHL, maybe
   NCAAF; freeze NASCAR, MMA, NCAAB, MLB (off-season anyway) code and timers; stop
   buzz/tout collection that tested null.
5. **Add bankroll + sample-size discipline (M/L).** A bankroll number, a per-slate cap
   (~2-5%), and a written "what can 20 entries/week ever prove" note that decides which
   questions are worth a cycle. Report every ROI with the slate count and, ex-NFL, the
   honest headline: the system is not yet profitable outside one sport over 11 slates.

Stop doing: proxy-only critique cycles on thin data; adding sports; writing new status docs
that restate the scorecard.
