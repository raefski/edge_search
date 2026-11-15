# Pick'em model critique log

One cycle per `/dfs-critique pickem`. Metric (Adam, 2026-10-22): how often the
pick beats the pool line, and points per week vs. a random picker, held out by
week. Harness: `python3 scripts/pickem_lab.py report [--variant NAME]`.
Train 2014-19 (fit here only), test 2020-22 (seen during the Aug feature
rounds -- held out from this loop's fits, not virgin), live 2026 (real CBS
lines, tiny). 2023-24 is the spent holdout and is never read. Ship only if
test AND live do not get worse and test improves.

## Cycle 0 -- baseline (shipped make_pick, 2026-10-22)

| split | weeks | games | W-L | win% (95% CI) | pts/wk | vs random/wk (se) | weeks +/- | signal win% |
|---|---|---|---|---|---|---|---|---|
| train 2014-19 | 102 | 1536 | 825-677 | 54.9% (52.4%-57.4%) | 8.09 | +0.73 (0.19) | 59/29 | 54.4% |
| test 2020-22 | 53 | 799 | 436-343 | 56.0% (52.5%-59.4%) | 8.23 | +0.88 (0.27) | 32/18 | 57.7% |
| live 2026 (CBS) | 6 | 93 | 53-40 | 57.0% (46.8%-66.6%) | 8.83 | +1.08 (0.80) | 4/2 | 63.0% |

## Cycle 1 -- sub-0.5 live moves sent to the coin flip (2026-10-22) -- NOT SHIPPED

**Hole (critic):** live edges are a 3-book weighted mean, so the 0.5-pt move
floor -- validated on single-book half-point history, where >=0.5 means "any
move" -- turns one book's half-point move (reads 0.125) into a coin flip.
27 of 93 live games (29%) sat at 0 < |edge| < 0.5; shipped went 13-14 on them.
On history, following an exact 0.5 move beat the tiebreak in both eras
(train 230-219, test 107-80).

**Fix tested:** `pickem_lab.py --variant any_move` -- follow any nonzero
move, tier LEAN; else make_pick. No new constant.

| split | shipped W-L | any_move W-L | pts/wk vs random |
|---|---|---|---|
| train 2014-19 | 825-677 | identical | +0.73 -> +0.73 |
| test 2020-22 | 436-343 | identical | +0.88 -> +0.88 |
| live 2026 (6 wk, 93 g) | 53-40 | 57-36 | +1.08 -> +1.75 (se 0.85) |

**Decision: not shipped.** History cannot measure it (it contains no
sub-0.5 moves), and the only evidence is the 6 live weeks the critic used to
find the hole -- not held out. The 12 changed picks went 8-4 (+0.67/wk, se
0.33). Warning: the gain sits on 0.125-0.19 readings (one book moving); at a
0.2 floor the changed picks are 3-2. **Forward test:** from week 7 on, grade
`any_move` vs shipped each week (`pickem_lab.py report --variant any_move`,
live row, changed weeks only); ship if the paired gain on weeks 7+ stays
positive over 10+ weeks. Median-book line instead of the weighted mean is the
alternative to test alongside it.

**Runner-ups:** (1) the backtest overstates staleness (opener as pool line:
mean gap 1.23 vs 0.65 live, coin-flip share 17% vs 43%), so test numbers are
not comparable to live -- a partial-drift pool proxy
pool = open + 0.76*(close-open) would fix that; (2) the totals tiebreak on
zero-move games is 57.3% train but 49.0% test -- possibly noise, but chalk is
worse on train; (3) only 17-43% of games are open to change at all.

## Cycle 2 (auto -> Cycle 1 forward check, 2026-11-14) -- NOT SHIPPED (yet)

**Target:** `dfs_target_scout.py` has no pick'em mode, so the target was chosen
by hand: the forward test Cycle 1 pre-registered (any_move vs shipped, live
weeks 7+, paired, changed picks only). No critic was spawned -- the live
weeks are the only data that can measure this hole, and a critic hunting a new
hole on 10 live weeks would be fitting the same rows.

| live 2026 (CBS) | weeks | games | shipped W-L | any_move W-L | vs random/wk |
|---|---|---|---|---|---|
| weeks 1-6 (found the hole) | 6 | 93 | 53-40 | 57-36 | +1.08 -> +1.75 |
| weeks 7-10 (forward) | 4 | 44 | 17-27 | 22-22 | -- |
| all 10 | 10 | 137 | 70-67 (51.1%) | 79-58 (57.7%) | +0.15 -> +1.05 (se 0.60) |

Changed picks forward (weeks 7-10): **7 games, any_move 6-1** (shipped 1-6);
ahead in all 3 forward weeks that had a changed pick (week 10 had none). At
|edge| >= 0.2 forward: 3-0. All 10 weeks: changed picks 14-5.
History unchanged (no sub-0.5 moves there).

**Decision: not shipped** -- the pre-registered rule needs 10+ forward weeks;
we have 4 (3 with changes). Direction holds out of sample (6-1, one-sided
binomial p ~0.06). Shipped model is 51.1% live, barely above a coin flip; the
gap is the 0.5 floor. Re-check weeks 7+ again; ship if still positive after
the regular season (week 18) or ask Adam to waive the 10-week rule now.

**Update 2026-11-15 -- SHIPPED on Adam's call.** Adam waived the 10-forward-week
rule ("NFL has a small sample size"): `edge.pickem.FOLLOW_ANY_MOVE = True` --
0 < |edge| < 0.5 now follows the move, tier LEAN, prob Phi(|edge|/13.45).
Parity: `pickem_lab.py report` (shipped) == the any_move variant (live 79-58,
history unchanged); the old rule stays as `--variant floor_only`.
**Week-18 re-evaluation** (due 2027-01-05, shown by `harness status` from that
date, `harness/status.py` REVIEWS): weeks 7-18 paired, shipped vs floor_only,
plus median-book line, the partial-drift pool proxy (Cycle 1 runner-up 1) and
the totals tiebreak (runner-up 2) -- choose the 2027 rule there.
