# Overlay critique log

The dfs-critique loop applied to the overlay app's at-lock field forecast
(`edge/dfs_entry_edge.py` likely_at_lock + the straight-line floor, combined as in
`harness/overlay.py` assess). **Classic contests only** (Showdown/Captain alerts are
off). Target: the field at lock, forecast at the final call (latest snapshot
4.5-9.5 min before lock), versus DK's reported final (`data/contest_finals.csv`).
Harness: `python3 scripts/overlay_lab.py report --cutoff <date>`; the late-fill model
is refitted on train locks only, and the split is by lock date in ET.

Metrics: err/cap = (forecast - final) / cap (bias + = forecast too full), and the
overlay call "prize pool > fee x entries at lock": *missed* = said no, there was
one (lost EV); *false* = said yes, there wasn't (entries into a raked contest).

## Cycle 0 -- baseline (2026-10-22)

Model as shipped 10/15 (pooled curves, STRATIFY=False, tail from finals).

Split A: fit locks < 10/18, test 10/18-10/22 (includes NFL Sunday 10/18).

| slice | n | MAE | RMSE | bias | overlays | missed | false |
|---|---|---|---|---|---|---|---|
| ALL | 772 | 0.081 | 0.108 | +0.017 | 355 | 198 | 77 |
| NFL | 344 | 0.100 | 0.125 | -0.001 | 185 | 88 | 59 |
| NBA | 297 | 0.065 | 0.094 | +0.026 | 116 | 75 | 10 |
| NHL | 77 | 0.064 | 0.092 | +0.048 | 21 | 10 | 4 |
| NASCAR | 43 | 0.076 | 0.088 | +0.028 | 28 | 21 | 4 |
| CFB | 11 | -- not enough data -- | | | | | |
| fill <0.25 | 21 | 0.258 | 0.288 | -0.112 | 12 | 0 | 9 |
| fill 0.25-0.5 | 90 | 0.140 | 0.171 | +0.002 | 55 | 0 | 34 |
| fill >0.5 | 661 | 0.067 | 0.085 | +0.023 | 288 | 198 | 34 |

Split B: fit < 10/20, test 10/20-10/22 (NBA week): n=352, MAE 0.058, RMSE 0.086,
bias +0.007, overlays 134, missed 41, false 26.

Read: test-day count is small (5 lock days on A, 3 on B), so slice numbers are
weak; the tail share moved 0.45 -> 0.33 between the two fits (it is drifting). All
198 misses on A are contests already over half full at the call, so the forecast
over-fills nearly-full contests. The false calls sit in the emptier bands.
Caveats for the critic: finals exist only for contests collect_finals looked up
(cap >= 10, not full at the last snapshot), and the call snapshot depends on the
timer's cadence, which is every minute since 10/22 and was coarser before.

Baseline re-run 10/22 later (more finals collected): A n=784, MAE 0.082, bias +0.020,
missed 199, false 77; B n=364, MAE 0.061, missed 42, false 26. The lab now also
reports "$/1-entry-each": the sum, over every test contest called an overlay, of one
extra entry's value pool/(final+1) - fee (flat-payout approximation, tiers ignored).

## Cycle 1 -- pace at the call (2026-10-22): NOT SHIPPED

**Hole (critic):** the forecast at the final call adds a fixed median share of the
empty room by fill band x minutes and ignores the contest's own current pace. The
straight-line floor that carries pace almost never binds (removing it moves MAE
0.001). On train, the realised call->final share runs 0.33 / 0.35 / 0.56 / 0.64 / 1.00
by quintile of pace v (n=444, corr 0.34). Here v = straight-line share of the room
from the snapshot 12-25 min before the call.

**Fix tried:** `overlay_lab.py report --pace`: forecast = e + median share of v's
quintile x room, with quintiles and medians fitted on train only. Contests without an
earlier snapshot keep the shipped forecast.

| split | model | n | MAE | RMSE | bias | missed | false | $ (1 entry each call) |
|---|---|---|---|---|---|---|---|---|
| A (<10/18 / 10/18-22) | shipped | 784 | 0.082 | 0.111 | +0.020 | 199 | 77 | +280 |
| A | pace | 784 | **0.072** | **0.103** | -0.031 | **60** | 132 | +277 |
| B (<10/20 / 10/20-22) | shipped | 364 | 0.061 | 0.091 | +0.012 | 42 | 26 | +321 |
| B | pace | 364 | **0.052** | **0.082** | -0.002 | **25** | 32 | +316 |

**Decision: not shipped.** Field error drops 12-15% on both splits. But the money the
calls make does not move (-$3 / -$5, inside noise on 5 and 3 test days). The
recovered misses are small overlays, and the new false calls give it back. A better
forecast fed into a hard threshold on a point estimate doesn't pay. The pace option
stays in the lab as a candidate input for the next cycle.

**Runner-ups:**
- (Next cycle) Make the call on the probability that the final stays under
  pool/fee, with the spread taken from the pace bins, and enter only on positive
  expected value. That is the decision the pace forecast would feed.
- Finals are only looked up for contests not full at their last snapshot, so
  fast fillers are under-represented in both the tail fit and the test.
- The tail share drifts (0.45 -> 0.33 between fits) and is pooled across sports.
  Weight recent days or fold it into pace.

Critic retrospective note: the critic optimised the error metric it was handed;
the money metric only came in at fix time. The next critic prompt includes $ up front.

## Cycle 2 -- call on expected entry value, pace spread (2026-10-22): NOT SHIPPED

Adam's spec: keep the pace forecast as an input. Instead of a yes/no on one forecast
number, use the train spread of call->final shares in the contest's pace bin, and
call only when one entry's expected value is positive:
mean over the spread of pool/(field+1) - fee. Contests with no pace snapshot use
every train share. `overlay_lab.py report --pace --ev`; everything fitted on train.

Re-run with more finals (n grew 784 -> 796 on A):

| split | model | n | missed | false | calls | $ (1 entry each call) |
|---|---|---|---|---|---|---|
| A <10/18 | shipped | 796 | 203 | 77 | 252 | +390 |
| A | pace point | 796 | 61 | 132 | 449 | +405 |
| A | pace + EV | 796 | 107 | 110 | 381 | **+412** |
| B <10/20 | shipped | 376 | 44 | 26 | 139 | +434 |
| B | pace point | 376 | 26 | 32 | 163 | **+444** |
| B | pace + EV | 376 | 41 | 25 | 141 | +434 |

By lock day (A), $ for shipped / pace / EV: 10/18 -33 / -39 / -23; 10/19 +2 / +1 / +2;
10/20 +36 / +52 / +52; 10/21 +1 / -10 / -4; 10/22 +384 / +401 / +385.

**Decision: not shipped.** The same 5 contests make $232 of every model's total, and
all three models call them. 10/22, which is still locking, is ~90% of the money. The
differences between models (+-$10-20) are a handful of $1-5 entries, which is noise
on 5 test days. EV is best on A and ties shipped on B. Pace point is best on B. No
model wins both.

**What would decide it:**
- The flat-payout EV is crude for an EV rule. Price each call with the contest's
  real payout tiers (`entry_edge`, as live does), so the lab's $ matches the alert.
- 2+ more weeks of test days, so no single day carries the result.
- Re-run cycle 2 with both.
