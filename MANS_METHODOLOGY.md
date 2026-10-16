# Jeff Mans's methodology: what to take from One MANS Opinion

Adam, 2026-10-14: "utilize Jeff's methodology for how he goes about making lineups ...
his philosophy to put together a DFS lineup for football, both GPP and cash."

Sources: the six DFS episodes of his 300-episode back catalog (ep 31 "Making Millions"
2020, ep 39 "A Million Reasons" 2020, ep 86 "DFS Lineup Review" 2021, ep 89 "Meltdown"
2021, ep 181 "Fantasy Sports Daily" 2023, ep 227 "Late Swapping" 2024) plus 2026's eps
319-323. Transcribed locally with faster-whisper (cache `~/.cache/transcripts/`); the rest
of the catalog is backfilling. His actual lineups sit behind fantasyguru.com, so this is
his stated method, not his plays.

**How the rules were tested** (`scripts/mans_dfs_rules.py`). Each rule became a lineup
feature, scored on the real field of Adam's five 2026 NFL main-slate single-entry GPPs
(9/13-10/11, 61,658 lineups with a pre-lock projection), **holding our projection fixed**
so "lineups with X won" can't just mean "lineups with better players won". Outcomes: top 1%
(GPP) and finishing in the top 45% of the GPP field, a double-up's cash line, standing in
for cash because Adam's real Double Ups (23-124 entries) are too small to read. The slate
is the unit: a rule counts as supported at 5/5 or 4/5 slates the same sign. ~46 checks were
run, so ~3 would hit 5/5 by chance; the stacking features are one finding, not three.
Week 4 (10/4) was built on the phone, so its projection is rebuilt from the pre-lock odds
scan (`scripts/mans_claims.py`).

## His cash-game method (he has mostly played cash since ~2016)

| # | rule (episode) | verdict on our data |
|---|---|---|
| C1 | **Core four.** Four pillars (elite QB/TE, the best-value WR, a volume RB) take ~50% of salary; build around them (86) | **Partly supported.** More salary in the top 4 players: top-45% finish 4/5 slates; an $8k+ RB 4/5 (+7 to +30pp). Pay up, but at RB, not TE |
| C2 | **Pay up for the elite QB and TE** ("Kelce is worth $11k", 86) | **TE: refuted, 5/5.** Every extra $1k on the TE *lowered* both top-45% and top-1% rates on all five slates. QB salary: no effect (2/3). His rule was about 2021 Kelce |
| C3 | **Diversify across games in cash; stack only in GPPs** (86: split McLaurin and Ridley) | **Refuted, 5/5.** QB stacks, bring-backs and game stacks all *raised* top-45% finishes on every slate. Splitting games: nothing (3/2). This agrees with the 2020-21 backtest, where our no-stack cash mode never beat plain max-projection (DFS_STATUS) |
| C4 | **80%-owned = free square**; drift with the chalk (86) | **Can't test cleanly.** Adam's real Double Ups do have it (top player 41-87%); GPPs top out at 30-52%. There, the slate's most-owned player and top-5 chalk *lowered* top-45% finishes (4/5). The arithmetic of a free square is sound; "drift with chalk" below ~50% is not supported |
| C5 | **Zero fat:** no boom-or-bust punts; stay above "the Sterling Shepherd line" (39, 86) | **No effect** (3/2). Neither helps nor hurts |
| C6 | **Volume over talent at RB:** a three-down back goes in the core four "even though I hate him" (89) | **Supported** for RB and TE: early workload predicts rest-of-season beyond points (below) |
| C7 | **Late-swap capability:** a latest-game player in FLEX; at halftime find the "lineup train", swap to gain ground or swap INTO its players to lock a lead (227) | **Not testable from results.** It is an option, worth most on FanDuel (Sunday night is on its main slate) and against trains. Adam's DK Double Ups show no trains (duplicate rate ~1.0-1.2) |
| C8 | **Target a score, not the max:** FanDuel 128 cashes ~70% (86) | FanDuel-specific; not ported |
| C9 | **Bankroll:** grind cash, aim for ~12 of 17 winning weeks, then take profits into small-field high-stakes GPPs; skip milly makers (86, 39) | Process, not testable. Adam already plays single-entry |

## His GPP method

| # | rule (episode) | verdict on our data |
|---|---|---|
| G1 | **Fade the 80%-owned player**; play the one who benefits when he fails (86) | **Supported, 4/5.** Lineups with the slate's most-owned player, more top-5 chalk, more 20%+ players, or higher total ownership all did worse at top 1% on 4 of 5 slates |
| G2 | **Stack the shootout like a pinball machine,** with a bring-back (86) | **Supported.** Pass-catchers with the QB: top 1% 4/5; QB+2+bring-back: top 1% 3/5 (+2 to +3pp when it hits, ~3x the base rate), top 45% 5/5 |
| G3 | **A low-owned QB in a shootout wins GPPs** (39: Brees) | **Mixed** (3/2 top 1%); the last 3 slates positive |
| G4 | Leverage means fading chalk, **not** stuffing the lineup with sub-5% dart throws (implied by "zero fat") | **Supported, 4/5.** More sub-5%-owned players lowered top-1% odds |
| G5 | Small fields, high stakes; a good lineup "never wins a milly maker" (86) | Adam's single-entry Daily Dollar fits |

**What this means for the lineup engine.** GPP already does G1/G2 (it stacks 100% of the
time and has an in-climb ownership penalty). Two things are new:
- **Cash should be allowed to stack.** Jeff and our cash mode both say no, and the field
  says yes on 5 of 5 slates. Next step: re-run `scripts/nfl_lineup_backtest.py` with cash
  allowed a QB + 1 stack and compare it to the shipped cash mode before changing anything.
- **Don't pay up at TE** unless the projection insists. Our optimizer already follows the
  projection, so this is a check on the projection's TE pricing, not a new rule.

## The broader philosophy (eps 319-323)

1. **Opportunity before production.** "Being on the field is the only way to score."
   He holds Marvin Harrison Jr. through WR107 because his snaps are high. He worries about
   Chris Rodriguez at a 31% snap share, not about a bad box score. His "worry-o-meter" is
   mostly snap share against what he expected, plus who gets the goal-line work.
2. **The situation beats the player.** "I would rather have a terrible player with a great
   coaching staff than a great player with a shitty coaching staff, 100% of the time."
   He grades the play-caller first (he admits being wrong about McDaniel and Daboll), then
   the offensive line (Seattle's line, not Jadarion Price, is the problem). Then he asks
   what the QB can physically do: Rodgers can't throw deep, so the targets go to the TE,
   RB and underneath receivers, and the outside WRs are dead.
3. **Know each position's noise.** RBs are stable. QBs are erratic early, then level out.
   WRs and TEs are "a greyhound race". So don't panic over a WR after three weeks.
   (Half right; see the evidence below.)
4. **Fade the hype, buy the panic.** "Everybody they're talking up, take a second, third
   look. Everybody they're talking down on, I would buy them now." He wanted Bryce Young
   on the main slate *because* the field would roster him in a soft matchup and he would
   "put up 11 and a half".
5. **The value is behind the headline name.** In waivers, the obvious injury replacement
   draws 11 bids (he calls these players "lead blockers"), and the next guy gets 2. The DFS
   version: when a role opens, the crowd piles onto one name.
6. **Matchups are specific, not "bad defence".** He matches defence against position
   (ATL allows TE points, so Tucker Kraft is a TD prop at +175), slot WR against a
   shutdown outside corner, and single-high coverage ("feast city"). He checks how a
   run defence has actually played, not its reputation.
7. **Game script follows defence.** A bad defence makes its own offence throw (Bears,
   Texans at 0-4). "Dead" teams still produce garbage-time fantasy points.
8. **Process over outcomes, a week ahead.** "The real season starts now" (week 4): blend
   the priors out by week 4 and go on this year's data. Plan bye weeks one week ahead.
   Judge the whole year, not one slate. He is transparent about his record: 33-33 betting
   (+4.89u, and he says he takes too many -110s), DFS cash 1-3, GPP +$15.7k with almost
   all of it from two weeks. That is variance, not proof of skill, and he would say the
   same.

## What the data says about the testable rules

`python3 scripts/mans_claims.py` (rules written before running; nflverse 2020, 2021,
2023, 2024; DK scoring via `edge.nfl`).

**Rule 3, position noise. Half right.** Weeks 1-3 points per game predicting weeks 4-18
(rank correlation), and single week to next week:

| | 2020 | 2021 | 2023 | 2024 | week→week |
|---|---|---|---|---|---|
| QB | .56 | .40 | .35 | .54 | ~.19 |
| RB | .79 | .68 | .75 | .78 | .43-.55 |
| WR | .80 | .72 | .83 | .80 | .38-.46 |
| TE | .65 | .75 | .63 | .72 | .36-.42 |

RBs are stable, and QBs are by far the noisiest. **WRs are not a greyhound race.** Their
early rank holds as well as RBs' in every season. The TEs who start in the top 12 churn
(only 33-67% stay there). The repo's projections already handle this the right way: the
skill model's 10%/week decay with no shrinkage (DFS_STATUS) beat flat averages at every
position.

**Rule 1, opportunity. Right for RB and TE.** Fit on 2020-21, scored on 2023-24: adding
early workload to early points cuts weeks 4-18 MAE by **+0.28** pts/g for RB
[+0.10, +0.45] (n=154) and **+0.27** for TE [+0.13, +0.40] (n=125). WR +0.07 (CI
crosses 0). QB nothing. When an RB's or TE's points lag his touches or targets, trust
the touches.

**Rule 4, the crowd chases. Confirmed as field behaviour, not as profit.** In 2026
main-slate single-entry GPPs, players who beat this week's projection *last week* were
owned more than our model predicted. Rank correlation of the ownership miss with
last-week surprise: **+0.35, +0.40, +0.34, +0.26** (weeks 2-5; n = 112-142 players;
week 4 rebuilt). A leave-one-slate-out recency term improved ownership MAE in 3 of 4
(2.72→2.60, 2.68→2.45, 2.43→2.29) and hurt week 5 (2.59→2.65; buzz may already carry it).
**4 slates: one short of the bar.** Week 1 can never count (no prior week), so 10/18 is
the 5th. GPP rule G1 above says fading chalk did help top-1% odds 4/5 at fixed projection
in NFL, which is the first sport where GPP_META's "ownership has no ROI effect" fails.

**Revenge games (ep 321, ep 39).** A player facing a team he played 4+ games for last
season, against his own average in the season's other games, 2021-2025: +1.24, +0.43,
+0.40, +0.68, +1.15 DK points, positive all 5 seasons, pooled **+0.77 [-0.04, +1.66]**
(n=177). A lead, not an edge, and props may already price it.

Rule 4b, the field under-owning RB/TEs whose points lagged their workload: -0.50 and
-0.32 in weeks 2-3, +0.04 in week 5. Mixed; watch it.

**Rules 2, 6 and 7 are already in the price.** The live NFL projection is built from
player props, and the books set those knowing snap share, coaching, OL and coverage. A
weekly projection gains little from re-deriving them. Where they still pay is ownership:
the field reads box scores and narratives, not snap counts. Game script is already in
the GPP objective through the correlation matrix (stacks and bring-backs).

## Pick'em: what transfers

Mostly nothing, and the reason is structural. The pool scores **team against a frozen
spread**, and our edge is the line going stale after CBS posts it (PICKEM_MODEL.md).
Each of Jeff's team-level views has already been tested and is dead there:
- coaching quality (5b): every rule flipped sign between eras;
- team strength and "dead or alive" type ratings (5a): corr beyond the line +0.002;
- fading the crowd (5k): +4-7pp, but it failed the pre-set bar;
- coordinator buzz saws (5n): none.

The part that does transfer is his *process*: don't overreact to weeks 1-3, and follow
the information that arrives after the line is set (injuries, QB changes). That is
already the model, because it follows the market's move. His "dead or alive" team
calls could go into the tout ledger (`scripts/tout_picks.py`) as a free forward test,
but five teams a month will not reach a verdict this season.

## What to do with it

1. **NFL ownership:** add a last-week-surprise term once ≥5 GPP slates support it
   (re-run `scripts/mans_claims.py` C3 after each import).
2. **Skill projection / leverage:** add RB/TE workload (carries + 2×targets; target
   share) to the skill model, then check it at the *weekly* level in
   `scripts/nfl_skill_backtest.py` before it touches anything live. Its first use is
   flagging "touches up, points down" RB/TEs as likely under-owned GPP plays.
3. Don't build anything off rules 2, 6 or 7 for weekly projections; the props carry them.
