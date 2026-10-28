# NBA GPP ownership — critique log

Target: GPP %Drafted per player, MAE (pts of %), held out by DATE. Cash never pooled.
Harness: `scripts/nba_ownership_lab.py report` — replays the LAST logged pre-lock board
of each NBA GPP draft group (contests.db `projection`) + that day's buzz through
`edge/dfs_nba_theory.add_ownership`, scored against the exports' %Drafted (GPPs on one
draft group averaged). Parity with the logged own: exact on 8/9 slates; 10/21 differs
(mean 0.54) because buzz_nba.csv gained rows for that date after lock.
Train = slates before 2026-10-25 (4 slates / 7 GPPs), test = 10/25-10/27 (5 slates / 10 GPPs).
Few slates: every number here is thin (CLAUDE.md "~5 slates").

## Cycle 0 — baseline, target "cheap starters who are heavy chalk" (2026-10-28)

Shipped: softmax per primary position of value 1.0 · z(proj/salary) + salary 0.4 · z(salary)
+ promoted 0.6 + buzz 0.2, normalised to the position's slots, cap 75%.

| slice | train n | train MAE | bias | test n | test MAE | bias |
|---|---|---|---|---|---|---|
| all | 676 | 3.671 | +0.13 | 549 | 4.950 | +0.47 |
| cheap starter (≤$5,500, NBA.com starter) | 79 | 9.624 | −2.73 | 69 | 10.274 | −3.19 |
| field chalk (≥20%) | 50 | 20.139 | −18.02 | 63 | 17.894 | −13.71 |
| cheap + chalk | 20 | 28.363 | −28.36 | 20 | 24.566 | −22.26 |

Per-slate test MAE 5.64 / 3.09 / 6.81 / 5.56 / 6.32; mean rank corr +0.893.

## Cycle 1 — target "cheap starters who are heavy chalk" (2026-10-28): SHIPPED

**Hole (critic):** ownership ranks players only by OUR projection's value, on a too-flat
per-position z scale. The field also prices what a player has actually scored lately per
$1K; and per-position buckets trap multi-eligible cheap players.

**Fix:** one whole-slate power softmax, `SLATE_OWN = (3.77, 1.42, 0.58, 0.19, 1.65)`:
`w = exp(A ln(v/vmax) + B ln(sal/5000) + C promoted + D z(log1p buzz) + E ln(rv/vmax))`,
rv = recent DK per $1K (last 3 played games this season, else last 10 of last season;
proj if none), normalised to 800%, cap 75. Fitted on the 4 train slates only (Powell, player
MAE). Production computes `recent` from the board's history (DNPs and a rookie's trailing
preseason rows skipped); parity vs the lab's box-score rule on the 10/28 board: 253/255
equal, 2 fringe players differ at the season boundary.

| slice | train shipped → fix | test shipped → fix | test bias → |
|---|---|---|---|
| all | 3.671 → 3.182 | **4.950 → 3.962** | +0.47 → +0.47 |
| cheap starter | 9.624 → 8.187 | 10.274 → 8.303 | −3.19 → −4.08 |
| field chalk ≥20% | 20.139 → 17.955 | 17.894 → 14.661 | −13.71 → −8.13 |
| cheap + chalk (n=20) | 28.363 → 26.448 | 24.566 → 20.666 | −22.26 → −19.30 |

Per-slate test MAE 5.64→4.47, 3.09→2.54, 6.81→6.18, 5.56→4.20, 6.32→4.77 (5/5 better);
rank corr +0.893 → +0.925. Thin: 9 slates; refit weekly as slates arrive.

**Runner-ups:** (1) news-driven chalk — no "teammate Out → his role grows" term beyond
`promoted`; most of the remaining −19 on cheap chalk (Naji Marshall, Paul Reed, Ajay Mitchell,
Schroder). Next cycle. (2) per-position normalisation alone costs 0.15–0.4 MAE. (3) the
promoted bonus fits ~0 without the recent term; its value is unverified.

## Cycle 2 — target "news-driven cheap chalk" (2026-10-28): SHIPPED

**Hole (critic):** the model never uses NBA.com's pre-lock starter flag. The field drafts
the "starting" label on cheap players far past what our minutes and value imply. The
teammate-Out terms this cycle was aimed at did NOT help when tested: vacated minutes ×
cheapness, log vacated minutes, newly vacated only, and board minutes gain each fitted on
train scored 3.95–4.00 test (vs 3.96), and minutes gain fits NEGATIVE — the field fades
our minutes-boosted bench players (Jaylin Williams 50% ours vs 1%, Julian Reese 28 vs 0.2).

**Fix:** `CHEAP_STARTER = (0.48, 6000)`: + 0.48 in the softmax when NBA.com lists him
starting before lock and salary ≤ $6,000. G fitted on train with SLATE_OWN fixed (grid
0.3: 3.135, 0.5: 3.129, 0.7: 3.158). Lab replays the pre-lock starters from the newest
`data/nba_lineups_snapshot.json` commit on the data branch at or before each board's
logged_at (agrees with the final file on 1,203/1,225). Every board was logged 10–14 min
before its lock (autolog lead), so no post-lock news. A full 6-parameter refit (train 3.095,
test 3.712) made 10/26 worse and was not taken. Production now logs `starter` and `recent`.

| slice | train cycle 1 → 2 | test cycle 1 → 2 | test bias → |
|---|---|---|---|
| all | 3.182 → 3.129 | **3.962 → 3.645** | +0.47 → +0.47 |
| cheap starter | 8.187 → 8.570 | 8.303 → 7.655 | −4.08 → −0.47 |
| field chalk ≥20% | 17.955 → 16.648 | 14.661 → 12.915 | −8.13 → −7.53 |
| cheap + chalk (n=20) | 26.448 → 22.913 | 20.666 → 15.925 | −19.30 → −13.81 |

Per-slate test MAE 4.47→4.38, 2.54→2.42, 6.18→6.02, 4.20→3.71, 4.77→3.78 (5/5 better);
rank +0.925 → +0.929. Since cycle 0: test MAE 4.950 → 3.645 (−26%), cheap+chalk 24.6 → 15.9.

**Runner-ups:** (1) our minutes bumps on NON-starters are over-owned — cap promoted/gain
ownership for non-starters. (2) buzz agrees with residuals on train (+0.35 among cheap) but
not test (−0.01): BUZZ weight unstable. Still −14 on cheap chalk (Reed 66% vs 12%, Ajay 63 vs
8, Schroder): what the field reads that we don't — likely projected-lineup sites before
NBA.com posts, the same gap as the minutes "backup starts" fix queued next.
