# DFS Project Status — Orientation Doc

**Purpose of this file**: a 60-second re-orientation, not a full record. Point a new
conversation at this file first. For the full story behind any line here, the detailed
docs are: `DFS_METHODOLOGY.md` (every session's work, numbered §1-33+, the authoritative
history), `DFS_IMPROVEMENT_PLAN.md` (the standing roadmap), `DFS_MULTISPORT_PLAN.md`
(NFL/NBA build plan + progress), `DFS_COMMANDS.md` (the actual CLI commands to run),
`data/HISTORICAL_COLLECTION_README.md` (what NFL/NBA data exists on disk and why).

**Keep this file current.** Update it at the end of any session that ships a model change,
kills a signal, or resolves/updates an open question — that's the whole point of it.

**Workflow note**: MLB's nightly contest-processing routine (tag → grade → calibrate →
update the cash table) is a self-contained loop that doesn't need long conversation
history — start a fresh chat for it, pointed at this file. Reserve long single-thread
sessions for active build work (model changes, the NFL/NBA build-out).

---

## MLB — where the model actually stands

**Shipped, in production** (`edge/dfs.py`, `edge/dfs_run.py`):
- Pitchers: projected from live sportsbook props (K's/outs core, other stats imputed).
- Hitters: skill (EB-shrunk, Marcel-weighted pooled rate) × opportunity (home/away PA
  tables) × park × matchup (SP+bullpen K9 blend, opp ERA) × home-quality × platoon-cell.
  Backtest corr ~0.18 — this is at or near the ceiling for free MLB hitter data (see
  "killed" list below; 8 more candidates tested 2026-07-18, none moved the needle).
- Ownership model (power-softmax, gammas tuned against real contest exports).
- Optimizer: CASH maximizes a walk-rate-adjusted floor; GPP forces a leverage-picked 5-3
  stack, DK-legal (max 5/team, no pitcher-vs-own-stack).
- **Field simulator** (`edge/dfs_sim.py`): correlated-world Monte Carlo, calibrated to
  measured teammate corr-by-batting-distance and real SP-vs-opposing-lineup anti-corr
  (−0.672, matched to −0.698 simulated). Field model uses stack-size distribution measured
  from 7,215 real DK entries. Wired into the CLI (`--sim`) and app (🎲 expander), free.
- **Sim-EV GPP construction**: opt-in only (`--gpp-sim-ev` / experimental app button).
  Replay-validated +11 mean percentile over the default builder on 8 slates, but NOT
  statistically significant (t=0.80) — do not make this the default without more real data.

**Tested and killed — do not re-propose without genuinely new evidence:**
batter-props hitter model (corr 0.02, original approach); per-player platoon splits
(killed twice — shrinks toward mean, doesn't add rank signal); umpire zone tendency;
`team_total` as a mean-model multiplier; HR-rate as a cash "boom/bust" floor signal;
full per-event component decomposition of the mean model; odds-ratio K-matchup
interaction; PA-vs-opponent-quality adjustment; recency/"hot hand" blending (corr got
*worse* on train, 3rd time this exact idea has failed); weather as a mean-model
multiplier (real, t=3.24, but sub-threshold gain — belongs in the simulator's team
environments if anywhere, not the projection).

**Open questions actively being tracked (update these as new data comes in, don't let
them go stale):**
1. **Is cash-mode's simulated win-probability well-calibrated?** 4 real cash contests
   checked: 3 badly overpredicted (sim ~61%, actual 6-26%), 1 matched (predicted 61%,
   actual 72%, a real win). Net: probably still optimistic, not proven, not fixed — see
   `DFS_METHODOLOGY.md` §32 for the full running table. **Update this table every time a
   new cash result comes in.**
2. **Does the sim-EV GPP selector actually beat the default?** n=8 backtested slates,
   directionally positive, not significant. Needs either more replay data or live opt-in
   usage tracked via `data/dfs_sim_log.csv`.
3. **NFL only — does cash mode's floor term (Z_CASH) earn its place?** Swept
   2026-09-12 over 31 slates: train (2020) picks z=0, test (2021) picks z=0.75, on the
   same metric. Unresolved, and n is the reason. Re-run
   `scripts/nfl_lineup_backtest.py --z-grid ...` as real 2026 slates accumulate, or
   settle it faster with a real DK contest-standings export (which would also fit the
   ownership model, the other unvalidated NFL piece).
4. **Does the model add real value beyond DK's own salary?** Re-run 2026-07-23 at n=11
   slates (up from 6): now significant for both hitters (t=2.98) and pitchers (t=2.79),
   but a split-sample check shows the effect is concentrated in the more recent slates,
   not uniform — progress, not a settled fact. **Re-run this test every 5-10 new slates**
   (the exact regression is in `DFS_METHODOLOGY.md` §10's update, reusable code in
   `edge/dfs_validate.py::incremental_baseline_test`).

**Real-money track record** (via `scripts/dfs_entry_history.py`, reads
`data/draftkings-contest-entry-history*.csv` — never committed, gitignored, repo is
public): net negative and small-n as of 2026-07-22, not statistically distinguishable
from "just paying the rake" yet. Sub-$1 contests run notably worse than $2-5 ones in the
user's full lifetime history — a real contest-selection signal worth acting on
independently of model quality.

**A real grading bug found and fixed** (2026-07-20): `actuals_for_date()` was
double-counting players who both batted AND had an incidental pitching appearance the
same game (position-player mop-up pitching, or true two-way players) — fixed to score
under whichever role the player actually had plate appearances in. Affected historical
calibration numbers were regenerated. See `DFS_METHODOLOGY.md` §31.

---

## NFL / NBA — multi-sport build

**NFL status (2026-09-12): SHIPPED and usable for a real slate.** DK draftables,
optimizer, two game theories, a Streamlit page and a lineup-level backtest all exist.
**NBA remains pipeline-only** — no app, no optimizer, nothing checked against a live
payload. Do not imply NBA is usable; it isn't.

### NFL shipped 2026-09-12 — what to know in 60 seconds

- `pages/2_🏈_NFL_DFS.py` (cash + GPP, phone-first) and `scripts/dfs_lineups_nfl.py`
  both call `edge/dfs_run_nfl.py::build_slate`, so the phone and the desktop cannot
  produce different lineups.
- Defaults to DK's **Main** Classic slate, resolved as a property (the only Classic
  group with no ContestStartTimeSuffix) rather than by `pick_priced_group`'s
  "biggest", which on an NFL lobby is a season-long tournament.
- `deploy/odds-publish-dfs-nfl.timer` pushes a fresh props snapshot every 30 min on
  Sunday 08:00–13:00 ET. Without it the cloud app silently falls back to the paid
  Odds API, because `scraped_client` refuses a snapshot over 6 hours old.

### A LIVE BUG THAT WAS SILENTLY WRONG, found and fixed 2026-09-12

A scraped `dfs_nfl` scan holds every event the books have posted — on 2026-09-12 that
was 27 events, week 2 **and** week 3. The old `game_lines()` walked all of them into a
`{team: line}` dict, so for any team playing both weeks the later event overwrote the
earlier one. Measured on that scan: **21 of 28 slate teams carried the wrong opponent
and the wrong game total.** Every DST was projected off the wrong game, and the hard
"a DST never faces your own lineup" rule *silently stopped working* — a wrong opponent
makes that comparison pass rather than fail.

Fixed by taking opponent and game id from **DraftKings' own draftables** (`matchup`,
`game`, `start`), and admitting a book's total/spread only when the team pair matches a
slate game *and* kickoff is within 6 hours of DK's start time. Regression tests:
`tests/test_dfs_run_nfl.py`. A side effect worth knowing: a missing game total no
longer drops offensive players, only that game's defences.

### The two game theories — and the backtest is split on them

`edge/dfs_nfl_theory.py`. A lineup is a sum of nine correlated random variables, so
both modes come out of the same mean/sd through a measured correlation matrix:

    cash = mean − Z_CASH·sd        gpp = mean + Z_GPP·sd

Correlation raises a lineup's spread, so cash walks away from a stack and GPP walks
into one — neither is told to. Per-player spreads are **measured**
(`scripts/nfl_variance_fit.py`, 9,979 leak-free player-weeks, 2020+2021): a QB's sd is
nearly flat in his projection (7.98 + 0.038·proj) while a TE's nearly doubles it
(2.21 + 0.480·proj), which is where "pay up at QB" comes from without anyone asserting it.

**Backtest** (`scripts/nfl_lineup_backtest.py`): 31 slates, 2020+2021, real DK salaries
(RotoGuru) + nflverse actuals, leak-free skill projections, ownership-weighted simulated
field. Join check: our DK scoring vs RotoGuru's own, **corr 0.9982** over 10,340
player-weeks.

| | mean | realised sd | ≥50th pct | ≥90th | ≥99th | stacked |
|---|---|---|---|---|---|---|
| cash | 125.0 | 22.9 | 94% | 35% | 6% | **0%** |
| gpp | 127.1 | 28.9 | 87% | 45% | **16%** | **100%** |
| maxproj (pre-rewrite) | 126.9 | 24.1 | 94% | 42% | 10% | 0% |

**GPP's claim is confirmed.** It reaches the 99th percentile 16.1% of slates against
cash's 6.5% — 2.5× — and carries visibly more realised variance. The 0%/100% stacking
split is the objective doing it, not a rule.

**CASH's claim is NOT confirmed, and this is the honest finding.** The floor term did
not beat simply maximising projection: cash − maxproj = **−1.91 DK pts, t=−0.67**, and
maxproj's own bad-slate floor (p10 field percentile 63.0) was *better* than cash's
(55.2). Cash did lower realised variance (22.9 vs 24.1) and ties maxproj on the
cash-relevant ≥50th-percentile rate (94%), so the term is not harmful — it is just not
yet earning its place. Do not describe cash mode as backtest-validated.

**Z_CASH was then swept, train/test, and the data CANNOT settle it.**
`--z-grid 0.0,0.25,0.5,0.75,1.0`, chosen on 2020 (14 slates) and read on 2021 (17):

| | train 2020 mean pct | train ≥50th | test 2021 mean pct | test ≥50th |
|---|---|---|---|---|
| z=0.00 (no floor term) | **81.9** | **100%** | 79.5 | 88% |
| z=0.75 (shipped) | 76.0 | 93% | **82.2** | **94%** |

Train says z=0, test says z=0.75, on the same metric. That is a straight
contradiction across the split, which is the signature of noise rather than signal —
15-slate halves cannot separate effects this size. **Z_CASH stays 0.75 because it is
the theoretically motivated value and it survived the held-out half, NOT because the
backtest chose it.** Do not quote 0.75 as tuned. What IS robust across every cut: cash
never stacks (0% of 31 slates) and carries lower realised variance than maxproj
(22.9 vs 24.1), which is the behaviour a double-up wants.

**Caveats that are not decoration:** no rake, no real field, no payout curve; the
percentile field is built from this repo's own *unvalidated* ownership prior; and the
projection is the SKILL model, not the props model the live app runs on. These numbers
compare CONSTRUCTIONS. They are not an ROI and cannot be quoted as one.

### Still missing for NFL

- **Ownership is a prior, not a fit.** No NFL contest exports exist on this machine, so
  unlike MLB's gammas nothing here is tuned. It is capped and normalised so the output
  is at least possible (an unclipped version handed a $2,900 TE 99.1% ownership), and it
  only tilts GPP. Get a real DK contest-standings export and fit it.
- **No inactive feed.** NFL inactives land 90 min before kickoff; the only thing
  tracking them is the books pulling a ruled-out player's props, so snapshot freshness
  IS the inactive check. The page warns when props are over 90 minutes old.
- **No late-swap** (MLB has one), no field simulator, no real-money log for NFL.

**What exists:**
- Historical player props (paid, one-shot — the key that fetched this expired
  2026-07-24, cannot be topped up): NFL 2025 season (261/262 games), NFL 2024 season
  (262/262 games), NBA 2024-25 sample (572 games, sampled — full season wasn't
  affordable). Local disk only, gitignored (~69MB), **not backed up anywhere else — back
  it up if it matters.** See `data/HISTORICAL_COLLECTION_README.md`.
- Free ground truth (nflverse for NFL, stats.nba.com backend for NBA — `balldontlie` now
  requires a paid key, don't use it): NFL 2024 season complete; NFL 2025's detailed
  player-level stats aren't published by nflverse yet (only game scores are — free
  re-check later, no urgency). NBA 2024-25 complete.
- DK scoring formulas (`edge/nfl.py`, `edge/nba.py`), validated against independent
  sources (corr >0.99 against each site's own fantasy-point calculation, hand-verified
  on real games) before being trusted. Known gap: NFL DST scoring has no source for
  "blocked kick caused" — small, permanent, documented undercount.
- Joined, leak-free model-ready datasets: `data/nfl_model_rows_2024.json` (3,572 rows,
  96.5% match rate), `data/nba_model_rows_2024-25.json` (7,329 rows, 98.2% match rate).
  Gitignored (regenerable for free from committed scripts + the ground truth above).
- `edge/names.py`: shared player-name normalization (diacritic folding + generational
  suffix stripping) — the first real cross-sport code extraction, earned by hitting the
  identical join bug independently in both NFL and NBA, not designed in advance.

### ⚠️ THE PAID HISTORICAL PROPS ARE GONE (found 2026-09-12)

`data/nfl_historical_props_2024/`, `_2025/`, the NBA sample, `data/nba_ground_truth/`,
and both derived `*_model_rows_*.json` files are **not on this machine**. Searched the
repo, `$HOME`, `~/arbitrage`, and the Windows side under `/mnt/c/Users/ASR` — nothing.

That is the 2026-07-24 pull: ~79,829 credits on a key that expired the same day.
`data/HISTORICAL_COLLECTION_README.md` said in bold to back it up somewhere durable
outside this machine/repo; that did not happen. **It is not re-fetchable at any price
without buying a new key and re-spending the credits.**

What survived is `data/nfl_ground_truth/` (109MB — `player_week_2023.json`,
`player_week_2024.json`, `games.json`). That half is **free and regenerable**
(`scripts/nfl_ground_truth_collect.py`), so it is not a loss, but it is also the only
copy on disk and equally unbacked.

**Consequence for the head-to-head:** the props arm cannot be backtested *backwards* at
all. It has to be rebuilt *forwards* from `odds-collect-dfs-nfl.timer`, which has been
banking real NFL props into `data/odds.db` hourly since 2026-09-06.

### The skill model — BUILT AND MEASURED 2026-09-12

`scripts/nfl_skill_backtest.py`. The shipped NFL projection
(`edge/dfs_project.project`) is purely props-driven and had nothing to be compared
against; this is that missing arm. Train 2024 wk 1–9, **report 2024 wk 10–18, held out**.
Per-category means → `edge.dfs_project.points_from_means`, i.e. the *same assembler* the
props path uses (extracted for exactly this reason), so the only variable is the means.

| model | corr | MAE | RMSE |
|---|---|---|---|
| position mean | 0.3057 | 6.392 | 8.377 |
| player mean (flat) | 0.6319 | 4.816 | 6.825 |
| **skill, K=0 decay=0.90** | **0.6602** | **4.626** | **6.611** |

n = 2,886 player-weeks, QB/RB/WR/TE, regular season. It improves on the flat average at
**every position** (QB corr .452→.515, RB .621→.664, WR .608→.627, TE .586→.610) and
clears MLB's own ship rule from `dfs_component_eval.py` — corr up, MAE not worse.

**Two findings worth keeping:**
- **Recency is the whole gain.** A 10%/week decay does all the work; `d=1.0` is in the
  grid and loses. 2023 gets downweighted for free by being ~18 more weeks back, which is
  the MARCEL-style cross-season decay MLB tested, with no extra parameter.
- **Shrinkage adds nothing. K=0 won.** Once recency is in, pulling toward the position
  mean is pure loss. That is the opposite of MLB's hitters (EB K=60) and worth
  remembering before anyone ports the MLB recipe across.

**What this is NOT:** it measures projection accuracy *given the player played* (the same
look-ahead the live path makes from a posted prop), it is not a lineup ROI, and it is
**not** a props comparison. Do not quote it as one.

### The props arm, rebuilt forwards — HARNESS BUILT 2026-09-12

`scripts/nfl_props_grade.py`. Reads the props out of `data/odds.db` for a chosen scan,
projects them through the shipped `edge.dfs_project.project`, joins nflverse actuals, and
prints the **same metrics as the skill backtest** (it imports that module's `metrics`, so
the two are comparable by construction rather than by agreement).

**It is a pure read-side tool, and that is the point.** `odds-collect-dfs-nfl.timer` has
banked a `dfs_nfl` scan every hour since 2026-09-06 — 158 finished scans covering Sep 6–12
continuously, 192 DraftKings-priced players in the latest — and the store is append-only
with 400-day retention. **Nothing is lost by grading late.**

**Blocked on nflverse, not on us.** `player_stats_2026.csv` was still a 404 on 2026-09-12;
nflverse publishes player-level stats for an in-progress season on a lag (the same lag the
2024 collector's docstring recorded for 2025). The script says so and exits 0 — waiting is
a normal state here, not a failure. The day it appears:

```bash
python3 scripts/nfl_ground_truth_collect.py --season 2026
python3 scripts/nfl_props_grade.py --week 1
```

**The line props have to beat: corr 0.6602 / MAE 4.626** (skill, held out). One week will
not settle it — a few hundred player-weeks against that model's 2,886, and single-week
variance swamps the gap. Accumulate weeks.

**Immediate next step:** nothing to do until nflverse publishes. Then run the two commands
above for each completed week and start accumulating the comparison.

**After that** (see `DFS_MULTISPORT_PLAN.md` for full detail): DK draftables integration
for both sports, an optimizer, ownership modeling, and eventually a unified app with a
sport picker. NFL's real deadline is sooner (preseason ~Aug 7); NBA's season is further
out (~3 months) — prioritize accordingly if time-constrained.

---

## Quick facts easy to forget

- Odds API cost = markets × regions per live pull; historical endpoints are 10× that.
- The current Odds API key (500 credits/month) lives in this repo's own `.env`
  (gitignored). `credits_floor` default is 50 — do not raise this near the monthly
  budget or live pulls will silently stop working entirely (this exact bug shipped and
  got caught once already, 2026-07-24).
- `data/draftkings-contest-entry-history*.csv` and `.env` are gitignored and must never
  be committed — the repo is public.
- The local Playwright driver (`.claude/skills/run-dfs-app/`) is how app.py bugs get
  reproduced — don't guess at a live-app bug without driving it first.
