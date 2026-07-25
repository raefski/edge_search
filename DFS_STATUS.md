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
3. **Does the model add real value beyond DK's own salary?** Re-run 2026-07-23 at n=11
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

**Status: data pipeline built and validated. No live app, no DK draftables integration,
no optimizer, no backtest results yet.** Do not imply either sport is usable for a real
slate — it isn't.

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

**Immediate next step, not yet done:** run the actual props-vs-skill-model backtest these
datasets exist for — the same head-to-head test that validated MLB's pitcher-props model
and killed its original batter-props model. The data and scoring formulas are ready; the
test itself hasn't been run.

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
