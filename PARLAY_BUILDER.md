# Parlay Builder

Spend DraftKings / FanDuel **parlay-only profit boosts** on the legs they are
worth most on. Built 2026-10-03.

| piece | where |
|---|---|
| model (pure, no I/O) | `edge/arb/parlay.py` |
| phone page | `pages/9_🎰_Parlay_Builder.py` |
| CLI | `scripts/parlay_build.py --preset cfb` (`mlb`, `tennis`, `fanduel`, `no boost`, or `--promo-file`) |
| tests | `tests/test_parlay.py` (the three pasted DK offers are fixtures) |
| data | the arb snapshot's `candidates` (every book's price per side) + new `reference` field (Fanatics Markets anchor) |

Rescans go through the same phone → desktop scan-request bus as the Arbitrage
page. Traditional parlays only (one leg per game, pregame).

---

## 1. The math everything follows from

Leg *i*, one per game: book decimal *dᵢ*, fair win chance *pᵢ*.

```
rᵢ = pᵢ·dᵢ          the leg's return per $1 (1.00 = fair, a -110 leg ≈ 0.955)
R  = Π rᵢ           parlay return per $1, unboosted
P  = Π pᵢ           chance the ticket wins
EV = (1+b)·R − b·P − 1        profit boost b (boost multiplies PROFIT)
```

Consequences:

1. **Vig compounds.** 4 legs at −110 return 0.83, 11 return 0.60.
2. **The boost multiplies almost all of R.** A ticket is +EV once R > 1/(1+b).
   For the 105% step that is R > 0.49, and eleven ordinary legs (0.60) clear it.
3. **Stepped boosts:** adding leg n is worth it when r > (1+bₙ₋₁)/(1+bₙ).
   On DK's CFB ladder every step from 6→7 on breaks even at r ≈ 0.90–0.93, so
   even a 7%-hold alternate line pays for itself. **Go to the top step.**
4. **Flat boosts:** no step pays for extra legs. **Use the fewest legs allowed.**
5. **−b·P** is the boost wasted on a likely winner (a boost only pays profit),
   which is why longer odds use a boost better. It is small next to (2).
6. **Variance is the real decision.** Max EV is usually a 1-in-thousands ticket.
   The builder caps the pick at a hit rate you choose (default 1 in 100), shows
   the best EV at every hit rate, and marks the Kelly (bankroll-growth) choice.

**What 2026-10-03's live CFB board gave** (DK stepped 20%→105%, −250 per-leg
floor, $10): every DK leg was −1% to −3% vs fair, yet

| ticket | EV | hits | wins |
|---|---|---|---|
| 4 legs (20%) | +7% | 1 in 10 | $98 |
| 8 legs (55%) | +29% | 1 in 24 | $295 |
| 11 legs (105%), favourites | +59% | 1 in 89 | $1,402 |
| 11 legs, max EV | +55% | 1 in 170+ | $2,600+ |

MLB 50% (3+ legs, +300 total): +36%, 3 legs, 1 in 7. Tennis 30% (2+ legs,
−200 total): +15%, 2 legs, 1 in 3.

## 2. Fair price

Weighted average of de-vigged (power method) probabilities: Fanatics Markets
anchor ×2 when present, every other bettable book quoting both sides ×1, **and
the betting book itself ×1**. That last term is deliberate shrinkage: one soft
book disagreeing is as often a stale line as an edge (seen today: DK's FSU
alternate ladder 7–12% over FanDuel's for hours). Leg r is capped at 1.06 and
flagged above that. A market only the betting book prices both sides of is
priced off its own no-vig line ("DK only"): never +EV, but a low-hold one still
fills a ticket.

Parsimony: an extra leg must add ≥1.5 points of EV over the shorter ticket.
Otherwise a flat boost pads the slip with −1000 alternate-line favourites
"worth" +0.5% each, which is noise.

Search: DP over games, state = (legs, bucket of log P), keeping the best log R
per cell, which yields the whole EV-vs-hit-rate frontier and checks the
total-odds floor exactly. Matches brute force in tests; ~0.5 s on a 40-game
CFB board.

## 3. Research: does the advice hold up?

**Unabated (Captain Jack Andrews).** Holds up. Their content is general rather
than about boosted parlays.
- *Parlays compound the house edge* (3 × −110 pays +600 vs fair +700, ~12.5%):
  the arithmetic is right. ✅
- *SGPs pad an extra 10–20% for correlation uncertainty; fair 50:1 offered 30:1*:
  the exact padding can't be verified from outside. Directionally backed by
  industry data: SGP hold ≈ 2× straight bets (Deutsche Bank via Covers), FanDuel
  Illinois parlay hold ~21%. ✅ directionally
- *Parlays = 31% of handle, 59% of profit (2024)*: consistent with state
  reports (Indiana Apr 2026: parlays 31% of handle; Louisiana: parlays 62.6% of
  GGR). I did not find the national source. ✅ plausible
- *To beat SGPs: line up the same SGP at two books, add/remove legs one at a
  time to see how each prices correlation; play news-driven role changes*: a
  sound method, but manual and needs each book's SGP pricing. Not built. ✅
- *Taking every promo is a fast way to get flagged*: real. Low risk here,
  since $10 boosted parlays are exactly what books want you betting.

**"Every leg must be +EV" (Sports Book Reviews / Cipher Odds case study of this
same DK CFB ladder).** Their 4-leg number checks (4 legs × 6.2% edge at a 20%
boost ≈ +51%). But the rule is **sufficient, not necessary**: on a stepped
boost, ordinary legs are +EV to add (point 3). "3–4 legs is the sweet spot" is
a variance preference, not an EV fact. ⚠️

**SharpMoney: "take longer odds with a profit boost".** Holds (the −b·P term).
Longshots carry more vig (favourite-longshot bias), which can offset it, and the
builder weighs both. ✅

**OddsJam: "pair +EV legs and the parlay is +EV".** True, and the cleanest case.
Same caveat as above: sufficient, not necessary. ✅

**The Risk Takers Podcast: correlation is two effects (causation, and shared
uncertainty).** Legs on different games share almost no hidden factor on one
day, so multiplying is fine for traditional parlays. Same-team-across-weeks
(the McPeak futures parlays) and same-course golf are where it breaks. ✅

**ETR "How to approach odds boosts".** Same method as here: devig a sharp book,
parlay the fair odds, compare. ✅

**SGPx** (DraftKings) = several SGPs and/or single legs combined in one slip;
FanDuel's equivalent is SGP+. Every boost Adam pasted allows it, but the price
is the book's own correlation model and cannot be rebuilt from single legs.

Sources:
[Unabated: SGPs](https://youtu.be/z_Lt059zRno) ·
[Unabated: 5 worst bets](https://youtu.be/S8ouLblnUCw) ·
[Unabated: how books catch sharps](https://youtu.be/HoYE86quhI8) ·
[SBR / Cipher Odds: parlay boosts](https://youtu.be/hiOIshVRcYw) ·
[SharpMoney: bonus bets & boosts](https://youtu.be/e8u3ZQbziAg) ·
[OddsJam: sharps & parlays](https://youtu.be/KVB_pA6FEUM) ·
[Risk Takers: correlated parlays](https://youtu.be/yN1Z0uSUhkc) ·
[ETR: odds boosts](https://establishtherun.com/how-to-approach-odds-boosts/) ·
[Action Network: SGPx](https://www.actionnetwork.com/education/same-game-parlay-plus-sgpx) ·
[Covers: SGP hold](https://www.covers.com/industry/sportsbooks-increased-figures-same-game-parlays-october-26-2022) ·
[LSR: the parlay trend](https://www.legalsportsreport.com/155142/how-the-parlay-became-top-sports-betting-business-trend-2023/) ·
[Indiana Apr 2026 report](https://rg.org/news/gambling-industry/indiana-april-2026-sports-betting-revenue-report) ·
[devig methods](https://oddsguy.ai/learn/devig)

## 4. Known gaps

- **No sharp book.** Fair is soft-book consensus plus the Fanatics Markets
  anchor (main lines only). Most legs therefore read −1% to −3%, and the
  ticket's EV is the boost's. A Pinnacle-style anchor would find real +EV legs.
- **DK promotions API changed** (v3 → 500, v2 → 400 "productName") around
  2026-10-03, so promo terms are pasted or picked from presets for now.
- **DK tennis matching**: after the league-discovery fix, 582 DK tennis quotes
  but only 44 markets pair with FanDuel; the rest price off DK's own line.
- SGP / SGPx, live, and one-sided markets (milestones) are out of scope.

## 5. Fixed alongside

`draftkings_league.discover_leagues`: DK's `/leagues/{sport}` now 301s to the
homepage, which still carries the catalog. The status check threw the page
away, so DK tennis and golf discovery had been finding **0 leagues** (10/2
snapshot: `tennis_leagues_discovered: 0`). Now 46 tennis / 9 golf.
