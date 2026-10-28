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
