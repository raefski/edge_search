# DK College Football DFS — status, methodology, and what is not validated

Start here for anything NCAAF. Built 2026-09-24. Everything below was measured
on this machine on that date unless it says otherwise, and the things that were
*not* measured are called out rather than left to be discovered.

---

## 1. What the contest actually is

Read off `api.draftkings.com/lineups/v1/gametypes/94/rules` — DraftKings' own
published roster rules — not transcribed from an article.

| | |
|---|---|
| Roster | **QB, RB, RB, WR, WR, WR, FLEX (RB/WR), S-FLEX (QB/RB/WR)** — 8 players |
| Cap | $50,000 |
| Minimum games | 2 |
| Late swap | allowed |
| DK lobby sport code | **`CFB`** — *not* `NCAAF`, which returns every sport in the lobby and looks like it worked |
| Classic game type id | **94** (95 is Showdown, 377 is a Snake draft with no cap) |
| Main slate | the Classic group whose `ContestStartTimeSuffix` is **empty** |

**No defence slot and no tight-end slot.** DraftKings lists college tight ends
as WR. So the entire DST half of the NFL build — points-allowed tiers, the
opponent's implied team total, the "never roster a defence against your own
stack" rule — has no counterpart here, and a missing game line costs nothing.

## 2. Scoring — validated against DraftKings, not cross-referenced

`edge/nfl.py` is candid that its scoring constants were "cross-referenced across
multiple independent sources" but never confirmed against DraftKings directly,
because DK's rules pages blocked every automated fetch. They still do: the rules
page is a single-page app that serves 576KB of shell and no scoring text, and no
`api.draftkings.com` scoring endpoint answers.

So this was confirmed a different way. DK's draftables payload publishes each
player's season **fantasy points per game** (`draftStatAttributes` id **174**
for CFB). Recomputing that quantity from cfbfastR play data under competing
scoring tables and keeping the one that reproduces DK's own number is a direct
test against DraftKings — just not against its prose. Over the **393 players on
the 2026-09-26 main slate** who matched:

| hypothesis | MAE | bias | verdict |
|---|---|---|---|
| passing TD = 4 | 0.900 | +0.42 | **kept** (QB only) |
| passing TD = 6 | 2.356 | +2.18 | rejected |
| 0.04 per passing yard | 1.144 | +0.90 | **kept** |
| 0.05 per passing yard | 1.270 | +1.07 | rejected |
| 0.1 per rushing yard | 1.144 | +0.90 | **kept** |
| 0.05 per rushing yard | 1.500 | +0.20 | rejected |
| threshold bonuses ON | 1.144 | +0.90 | **kept** |
| threshold bonuses OFF | 1.231 | +0.70 | rejected |
| full PPR | 0.678 | −0.34 | **kept** (WR only) |
| half PPR | 1.425 | −1.20 | rejected |
| no PPR | 2.255 | −2.07 | rejected |

**The PPR row came out backwards the first time, and the reason is a trap.**
Averaged over the games a player *recorded a stat in*, full PPR looks biased
high for receivers (+1.24) and half PPR looks unbiased. That is an artifact of
the denominator: a receiver who played and caught nothing has no row in a
play-derived box score, so he is dropped from the average instead of counted as
the zero DK counts him as — and that hits receivers hardest, backs less,
quarterbacks least, which is exactly the shape of the spurious bias. Dividing by
the player's **team's** games reverses the conclusion at every position. Anyone
re-running this must use team games, not observed rows.

**Conclusion: DK College Football is DK NFL scoring minus everything defensive.**

## 3. The price problem, and why it turned out to be an advantage

**DraftKings posts NCAAF player props ONLY as one-sided milestone ladders.**

```
marketType "Receiving Yards Milestones"
selections  "15+" 1.03   "25+" 1.30   "40+" 1.95   "50+" 2.55 ...
```

There is no Under on any of them. `edge/dfs_project.project` needs an
Over/Under pair to devig, finds none, and returns `proj=None` for every player
on the board. That is not a degraded projection — it is no projection, and it is
why `edge/dfs_ladder.py` exists.

A two-sided line is **one point** on a distribution; turning it into a mean
requires assuming the whole shape around it (that is what the sigma is, and
`edge/dfs_sport.py` is candid that the NFL's fitted sigmas turned out nearly
inert). **A ladder is the distribution.** So the quantity the NFL path has to
assume, this path largely measures:

```
E[X] = ∫ P(X ≥ t) dt          (yardage)
E[N] = Σ_{k≥1} P(N ≥ k)       (counts)
```

Both identities are exact, and the rungs supply most of the terms directly.
College football's uglier market data yields a **better-founded** projection.

### Coverage, measured on a live board

| | events | ladders | per covered game |
|---|---|---|---|
| receiving yards | 32 | 189 | ~6 WR |
| rushing yards | 31 | 107 | ~3.5 RB |
| passing yards | 33 | 62 | ~2 QB |
| receptions | 18 | 102 | thinner, and 1 pt each in PPR |
| passing TDs | 33 | 64 | |

On the 12-game main slate, **all 12 games** were covered and **106 of 857**
slate players carried a ladder. That is enough to fill QB/RB/RB/WR/WR/WR/FLEX/
S-FLEX comfortably — **and it is also the build's biggest structural
limitation**: DraftKings prices the players books take action on, so a cheap
backup getting a full workload is a real play this model cannot see at all. The
app says so on the page rather than in this file.

Collection is cheap: DraftKings serves these at **league level**, so five
requests cover every college game on the board rather than five per game.

### What the estimator had to get right

Graded end-to-end against real cfbfastR distributions with the market removed
entirely (`scripts/ncaaf_ladder_fit.py`):

| | lognormal | Weibull | shipped |
|---|---|---|---|
| passing yards | +5.5% | +0.8% | **+3.2%** |
| rushing yards | +15.8% | +8.9% | **+1.5%** |
| receiving yards | +20.0% | +12.4% | **+3.0%** |
| receptions | — | — | **−4.1%** |
| passing TDs | — | — | **0.0%** |

Fitting a parametric family to the rungs and integrating it over the unpriced
head **does not work** for rushing and receiving: those distributions have a
mass at and near zero (a receiver targeted once for four yards, or not at all)
that no smooth right-skewed density reproduces. Swapping families moved the bias
without removing it.

So the shipped version drops the family and **measures the two unpriced pieces
directly**, each as one bounded number per market:

* `HEAD_FILL` — where the head sits inside the bracket monotonicity already
  guarantees. `c=0` is the rectangle `t_min·S(t_min)` (strict lower bound),
  `c=1` is the full box (strict upper bound). Fitted 0.53 / 0.40 / 0.38 for
  passing / rushing / receiving.
* `TAIL_EXCESS` — mean excess above the top rung, **in units of the rung gap**.
  Against `t_max` the ratio is 0.017–0.067 with a wide spread; against the gap
  it is 0.40–0.50 with a tight one.

For one player's own sample both quantities have exact identities
(`mean(min(x, t_min))` and `mean(max(0, x − t_max))`), so every player-season
with 8+ games gives one exact observation and the constant is the median over
thousands.

### Two bugs worth remembering, both of which produced plausible numbers

1. **The count tail ran away.** The tail decayed geometrically at the rate the
   last two rungs implied, clamped at 0.9. A book posts two rungs at the same
   price (and the isotonic step produces one whenever it pools an inversion),
   so the ratio came out 1.0, clamped to 0.9, and compounded:
   `0.083 × 0.9 / (1−0.9) = 0.75` of a touchdown out of a flat pair. Graded
   +33.7% on passing TDs. The measured mean excess is **0.000**.
2. **A count market is not interpolable.** FanDuel's "Over 1.5" passing TDs IS
   DraftKings' "2+" rung — the same event for an integer stat. Interpolating
   linearly between "1+" and "2+" instead reported a **50%** overround on
   passing TDs and 23% on receptions, against 7–10% on the yardage markets.
   Numbers that are not a plausible hold were the tell.

Both are locked in by regression tests in `tests/test_dfs_ladder.py`.

## 4. The two game theories, and four places college contradicts the NFL

Same shape as the NFL: a lineup is a sum of eight correlated random variables,
`cash = mean − 0.75·sd`, `gpp = mean + 1.25·sd`. Every constant is measured on
cfbfastR 2024+2025 (66,499 player-games → 4,380 player-seasons).

**1. The S-FLEX is a second quarterback, and it is not close.**

| position | mean DK | median | p90 | p99 |
|---|---|---|---|---|
| QB | **17.22** | 15.6 | 33.4 | 51.9 |
| RB | 10.45 | 7.8 | 23.5 | 39.9 |
| WR | 8.89 | 6.8 | 18.9 | 35.1 |

31 of the 40 highest-scoring player-seasons in the sample are quarterbacks.

**2. But never two from the same team.** `R_QB_OWN_QB = −0.098` — they compete
for snaps. Enforced as a hard refusal in the optimiser, the structural analogue
of the NFL's "a DST must not face your own stack".

**3. Team-mate receivers help each other in college and hurt each other in the
NFL.** `WR↔own WR` is **+0.048** here and **−0.029** in the NFL. `dfs_opt_nfl`
reasons correctly from its negative number that a second pass-catcher is worth
less than the first, and stacks 2. The sign flips here, so **`STACK_N = 3`**.
This is the single most consequential difference for roster construction and the
first thing to re-check if GPP lineups ever look wrong.

**4. Do not pay up at quarterback for the floor.** The NFL logic inverts. At a
20-point projection:

| | sd | CV |
|---|---|---|
| QB | 11.60 | **0.580** ← most volatile |
| RB | 10.60 | 0.530 |
| WR | 10.14 | **0.507** ← least |

A college QB's intercept is 8.48 points — he runs, throws picks, and gets pulled
in blowouts. Cash still wants quarterbacks for the **mean**; it should not pay up
for the most expensive one expecting safety.

Full correlation matrix with sample sizes: `edge/dfs_ncaaf_theory.py`.

## 5. What is NOT validated — read before trusting a number

| thing | status |
|---|---|
| **Ownership** | value softmax **plus DK's own FPPG** (§10), fitted on **5** GPP slates held out one at a time; pool MAE 3.36→2.69 and 3.39→2.74 on the two big October slates, 9/26 flat. Still misplaces ~half of the field's ownership. S-FLEX split measured (QB 1.9 / RB 2.3 / WR 3.8); 4–12% of GPP ownership goes to players with no DK ladder (`MODELED_SHARE` 0.88) and nothing tried predicts *which* (§10). GPP only: cash lineups do not read ownership. |
| **Ladder overrounds** | fitted against **FanDuel's** two-sided lines — a cross-book anchor, an assumption about a second book, not ground truth. n is 22–81 per market. The first graded slate (§8) had projections **3.9 points LOW** on 63 players (RB −5.5, QB −3.4, WR −3.3) — the direction too-high overrounds would produce, and the check this row was waiting for. Seven games that move together is not enough to refit on. |
| **`SD_FIT` spreads** | regressed against a leave-one-out **season mean**, not a real projection, because no projection log existed. A season mean knows nothing about the opponent, so these are if anything biased **high**. |
| **Lineup head-to-head** | the NFL build has `scripts/nfl_lineup_backtest.py` testing cash-vs-GPP objectives against real outcomes. **College has no equivalent yet** — the objectives are argued from measured correlations and spreads, not from a backtested edge. |
| **Fumbles / 2-pt conversions** | not in the cfbfastR feed; left at zero. Worth about −0.1 and +0.05 points per player-game. |

All five are checkable from contest exports. `data/dfs_proj_log_ncaaf.csv` is
written on **every** build and starts that clock — it does nothing for a week
already gone, which is why it shipped with the first build.

## 6. Weekly checklist

```bash
# Friday night / Saturday morning — DK keeps ADDING ladders through Friday,
# so an old scan is a THIN pool, not a wrong one.
python3 scripts/odds_collect.py --profile dfs_ncaaf --push

# lineups
python3 scripts/dfs_lineups_ncaaf.py                  # cash + gpp, main slate
python3 scripts/dfs_lineups_ncaaf.py --board --top 40 # the projected pool
python3 scripts/dfs_lineups_ncaaf.py --mode gpp -n 3  # a diversified portfolio

# after the slate: export the contest standings from DK, drop in data/, then
python3 scripts/ncaaf_calibration.py --fit-ownership
```

Re-fitting, when there is more data:

```bash
python3 scripts/ncaaf_overround_fit.py            # accumulates pairs across runs
python3 scripts/ncaaf_ladder_fit.py               # head/tail constants + grade
python3 scripts/ncaaf_fit.py                      # TD rates, spreads, correlations
```

## 7. Layout

| path | role |
|---|---|
| `edge/ncaaf.py` | DK CFB scoring (validated) + cfbfastR ground truth |
| `edge/dfs_ladder.py` | **the milestone-ladder engine** — the core of this build |
| `edge/dfs_ncaaf_theory.py` | spreads, correlations, the two objectives, ownership |
| `edge/dfs_opt_ncaaf.py` | the optimiser (S-FLEX, the two-QB rule) |
| `edge/dfs_run_ncaaf.py` | slate → pool → lineups, one entry point |
| `pages/3_🏈_NCAAF_DFS.py` | the phone app |
| `scripts/dfs_lineups_ncaaf.py` | the CLI |
| `scripts/ncaaf_fit.py` | TD rates, spreads, correlations |
| `scripts/ncaaf_ladder_fit.py` | head/tail constants, and grades the estimator |
| `scripts/ncaaf_overround_fit.py` | ladder overround vs FanDuel |
| `scripts/ncaaf_calibration.py` | predicted vs actual, from a contest export |
| `tests/test_dfs_ladder.py` | the ladder engine, incl. both regressions above |
| `tests/test_dfs_ncaaf.py` | scoring, roster, theory, optimiser rules |

## 8. First graded slate — 2026-09-26 Night (7 games, draft group 153951)

Both model lineups were entered. **Cash 171.9, 21st of 23** (the line was 185).
**GPP 119.0, 548th of 594.**

**The cause was a bug, not the model.** DK's salary list keeps name suffixes
("Michael Hawkins **Jr.**") and a college prop subject drops them
("Michael Hawkins (WVU)"), so every suffixed player fell out of the pool:
**10 of the 75 players with ladders**, including the two players the cash
field built around — Michael Hawkins Jr. (QB, **91%** in cash, scored 35.6) and
Anthony Evans III (74%) — and JC French IV (74%). DK had posted their ladders
all day. Fixed in `edge/dfs_run_ncaaf.py::salary_index`. NFL is unaffected:
its prop subjects keep the suffix.

Replayed from the 6:31 PM odds snapshot, the fixed build's cash lineup puts
Hawkins Jr. at S-FLEX and scores **186.0 against the 185 line** (the buggy
build's replay: 165.0). One replay, cleared by a point: direction, not proof.

| check | result |
|---|---|
| projection (63 players) | MAE 7.16, r 0.51, **bias −3.88** (see §5, overrounds) |
| ladder `band` as a risk measure | high-band half MAE 7.38 vs low-band 6.94 — worse, as it should be |
| ownership, shipped prior on the fixed pool | 5.00 pts/player; with the measured slots + modelled share **4.58** |
| field ownership on players with no DK ladder | 12% GPP, 8% cash (after the fix; 25% / 36% before it) |
| cash field sharpness | gamma 3+ with a 90% cap — recorded, not shipped (cash does not read ownership) |

## 9. Second graded slate — 2026-10-03 Main (4 games, draft group 154161)

Both model lineups were entered. **GPP 183.5, 304th of ~1,900.** **Cash 147.5, 103rd of ~200** (cashed ~50%).

`scripts/ncaaf_calibration.py --fit-ownership`:

| | n | bias | MAE | rank corr | below floor (25) | above ceiling (5) |
|---|---|---|---|---|---|---|
| skaters | 130 | −1.55 | 8.19 | +0.546 | 26% | 11% |
| ownership | — | −0.54 | 3.52 | +0.749 | — | — |

Shipped: OWNERSHIP_GAMMA 1.1 → MAE 3.47. **Best fit: gamma 1.50 → MAE 3.29** (−0.18 improvement).

**Buzz test:** Tested on 3-slate aggregate (γ=0.6, β=1.0). Titans DST example: predicted 22% without buzz → 45% with buzz (YouTube mentions). Pattern is real but single outlier—hold collection running, defer integration to 4–6 slates.

## 10. Five-slate replay: what helps MAE and what does not (2026-10-17)

Every graded slate was **replayed through the current pipeline** from the last
odds scan before lock (`ScrapedOddsClient(scan_id=...)`), which also recovered
10/10 Main (draft group 154582, never logged). The replay reproduces the logged
pools (75 on 9/26, 135 vs 136 logged on 10/3). Truth is the DK export for
drafted players and cfbfastR for everyone else. Everything fitted was fitted
**leave-one-slate-out**. Slates: 9/26 Night, 10/3 Main, 10/10 Main (fields
594 / 1,189 / 356), 10/14 and 10/15 (52 and 71: reported, not trusted).

**Points: the ladder already prices volume, and nothing we added beats it.**

| laddered players (n=377, 5 slates) | MAE | |
|---|---|---|
| ladder (shipped) | **7.38** | |
| cfbfastR usage, season per team game | 8.16 | market beats box scores |
| usage, last 3 games | 8.19 | |
| ladder + usage, LOSO linear | 7.31 | −0.07: noise, not shipped |

Pace, neutral-script pass rate, target share, red-zone share, spread-driven
game script and weather are all inputs a book already prices into a player's
ladder, so a box-score model of them can only re-estimate the ladder, worse.
They would matter for players with **no** ladder, but there they are noise:
on non-laddered players the field drafted (n=217), predicting zero scores MAE
4.21, raw usage 4.84, and usage + salary fitted LOSO 4.07. Ladder bias
(actual − proj) is +1.3 QB / +0.7 RB / +1.2 WR and flips sign slate to slate:
not enough to refit overrounds.

**Ownership: the field reads DraftKings' FPPG column.** Shipped
`OWNERSHIP_FPPG_WEIGHT = 0.4`: DK's displayed season FPPG per $1,000,
z-scored within position, beside the market value term (gamma stays 1.1).
"Misplaced" is Σ|pred − actual| over the whole board / Σ actual, i.e. the
share of the field's ownership put on the wrong players:

| held out | pool MAE before → after | misplaced before → after |
|---|---|---|
| 9/26 Night | 3.81 → 3.85 | 0.479 → 0.483 |
| 10/3 Main | 3.36 → **2.67** | 0.615 → **0.497** |
| 10/10 Main | 3.39 → **2.76** | 0.611 → **0.512** |
| 10/14 (52) | 19.59 → 19.41 | 0.503 → 0.499 |
| 10/15 (71) | 14.04 → 13.76 | 0.658 → 0.647 |

Fold weights 0.42–0.45 / gamma 1.07–1.17. FPPG was **reconstructed** from
cfbfastR (DK serves no past draftables) as DK points per game played; on the
10/17 board that reconstruction tracks DK's real column at r 0.972, MAE 0.85
(n=252, $4k+). `dk_fppg` is now in `dfs_proj_log_ncaaf.csv`, so the next
refit uses the number the field actually saw.

Tried and **rejected** on held-out slates:

* refitting gamma alone (share fixed): better on 2 slates, worse on 3;
* gamma 1.4 + `MODELED_SHARE` 0.70: lower MAE, but only because shrinking every
  prediction lowers absolute error. The field put 88 / 96 / 92% on laddered
  players, and the leverage/contest-EV tools need ownership that sums to a roster;
* salary, implied team total, projection level as extra terms: no consistent gain;
* projecting ownership for the **whole board** (non-laddered players from
  usage): misplaced 0.57 → 0.67–0.77, worse on every big slate. The field's
  non-laddered ownership sits on a few names usage cannot find. Caleb Smith,
  a $3,100 WR5, was 21% on 10/10 and scored 0: that is news, not box scores.

**Two traps, both of which produced believable numbers:**

1. **cfbfastR's `week` is not the calendar week you think.** 9/26 is week 4,
   10/3 week 5, 10/10 week 6, verified against DK export fpts (MAE ~0.5 on the
   right week, 7–9 on any other). Off by one, "season to date" included the
   slate itself, and box-score usage "beat" the market 6.55 to 7.41.
2. **Replaying an archived scan over `/mnt/f` is ~100× slower** (one SQLite
   open per event across the WSL mount). Copy `odds_2026-MM.db` to local disk
   and point `OddsStore.archived_file` at the copy for a replay.

**A third join bug, found grading 10/16.** A ladder joins its draftable by
NAME, and every scan carries off-slate games. "Austin Simmons (MIZZ)",
Missouri's QB, whose game was not on the 10/16 slate, became a $3,000
Washington WR projected 16.3. **Both model lineups played him; he scored 0.**
The cash replay reproduces the 125.5 Adam entered; with the fix it scores
139.2 against a 137.76 line (GPP 119.6 → 143.6). Same bug on 10/3 and 10/17
("Fredrick Moore (MSU)" → Alabama's $4,000 RB). `on_slate_keys` now keeps a
ladder only when its event belongs to the player's own game, i.e. at least half
of the event's subjects resolve to that game. Across the six replayed slates it
removes exactly those three joins and nothing else (`stats["wrong_game"]`).

**Where the remaining error is:** ladder coverage (DK priced no ladder for
Kamario Taylor, a $9,100 QB at 35 FPPG, 22% owned on 9/26), and late news on
cheap receivers. FanDuel posts two-sided college props DK sometimes lacks, but
only 24–48 h out and it is never stored (`scripts/ncaaf_overround_fit.py`
fetches it ad hoc). Adding it as a second projection source for unladdered
players is the next coverage lever and is not measured yet.

## 11. The name join: DK's salary list vs DK's sportsbook (2026-10-31)

Both sides are DraftKings, but they do not spell players alike. The exact join
(`subject_key` vs `salary_index`) missed **2 of 489** ladder subjects on the
6 train slates and **7 of 931** on the 19 test slates
(`scripts/ncaaf_names_lab.py report`, replayed through production at lock).
Every miss was one of two kinds:

* **a nickname or full surname on the salary list**: Bam / Braylon
  McReynolds, Rod Robinson II / Roderick, Ty Mims / Tiaquelin (UAB's whole
  backfield on 10/15), Makhilyn / MK Young, and Dominic Lee-Knicely /
  "Dominic Knicely (CCAR)". Lee-Knicely was a $6.8k RB, 38% owned in the
  10/16 GPP, and invisible to our board.
* **a namesake anywhere on the slate**: Fredrick Moore of MSU and of
  Alabama made `salary_index` drop both, three times.

`alias_joins` (critique cycle 2) now matches what the exact join leaves out,
inside the event's own game only. The book's team suffix is mapped to a DK
team by the event's exact joins. Last names must agree (a hyphen part
counts). The first names must then be prefixes of each other, or share an
initial when one of them is 3 letters or fewer, with exactly one such player
on that team. A DK player already joined is never taken, and a passing-yards
ladder must belong to a QB. Every alias is listed on the page and in the CLI
(`stats["name_alias"]`).

Result: recall is 1.000 on train and test, with 0 wrong joins. Brothers on
one roster (Dylan / Dayton Raiola, Quinten / Quintaelyn Joyner) are what the
short-name rule exists for. Unpriced games (FSU @ MIA, AF @ WYO, ...) are
coverage, not names: DK posted no ladders for them.

## 12. A quarterback's rushing touchdowns come from his season (2026-11-07)

Critique cycle 3 (NCAAF_CRITIQUE_LOG.md). DK posts no college TD market we
collect, so rushing TDs were imputed from rush yards (`NCAAF_RUSH_TD_PER_YARD_QB`
= 1/110). For a QB that misses the goal-line role: designed runs and sneaks
score, scrambles mostly do not. Trinidad Chambliss had 7 rush TDs in 8 team
games and was credited 1.3 points; pocket QBs with 0 were credited 0.6–1.2.

Now a QB with ≥ 2 team games gets `6 × (season rush TDs ÷ team games)` from
cfbfastR in place of that term (`dfs_run_ncaaf.QB_RUSH_TD_*`,
`ncaaf.qb_rush_td_rates`). There is no shrinkage: k = 0 was chosen on train.
Everyone else, and any name two cfbfastR players share, keeps yards/110.
Held out (6 slates from 10/17, 351 players): MAE 6.03 → 5.93, QB 6.78 → 6.19
(n=63), better on 5 of 6 slates. The table is `data/ncaaf_qb_rush_td.json`,
refreshed daily by `ncaaf-qb-rates.timer` (`scripts/ncaaf_qb_rates_publish.py`),
on the data branch for the phone. The page and CLI say how many QBs used it
and where the table came from. If the table is missing, every QB falls back,
visibly.

Known limits: 63 test QBs is thin; held-out QB bias went +0.54 → −0.34; and
early-season rates rest on 2–4 games. QB projections are still compressed
(slope ~1.45 after the fix). A college anytime-TD market would price this
directly: none is collected.

## 13. The cash lineup, replayed against the field (2026-11-09)

Critique cycle 7 (NCAAF_CRITIQUE_LOG.md). Every earlier cycle scored player DK
points. `scripts/ncaaf_lineup_lab.py` scores what we ENTER. It replays the
production optimizer on each logged pre-lock board, and the replay is exact:
29 of 29 boards match the logged cash and GPP lineups (`parity`). It scores the
lineup with DK's FPTS for that draft group against every exported contest on
it: field percentile, cash line, and $ at the rank taken.

Shipped cash lineup: 11 cash contests on 8 slates.

- Cashed 18%, at the field's 25th percentile, ROI −69%.
- Adam's real entries are near-identical (pctile +0.004).
- GPP: 13 contests, 49th percentile, +46%. Adam's own edits do better there
  (+172%, pctile +0.07, inside noise).

What is NOT the lever:

- **Z_CASH.** Chosen leave-one-slate-out it scores −0.012 in field percentile,
  and in-sample the z grid is non-monotonic noise.
- **A salary-aware projection calibration**, `max(0, a + b·proj + c·salary)`
  per position. The critic's lead: QBs over $8k run +4.2 actual − proj.
  - Fitted on every other date: pctile +0.091 [−0.011, +0.187], cash rate
    unchanged.
  - Fitted on earlier dates only (live-faithful): +0.033 [−0.118, +0.192], and
    cashes 0 of 11.
  - Not shipped.

Rerun `report` / `calibrate --forward` as cash exports arrive. 8 slates is
not enough data.
