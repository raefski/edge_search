#!/usr/bin/env python3
"""Cross-sport transfers: what one sport PROVED, checked in the others -- and what every sport RULED OUT.

    python3 scripts/dfs_transfer.py                  # table of every transfer -> data/harness/dfs_transfers.json
    python3 scripts/dfs_transfer.py nhl              # one sport: open transfers, new lessons to map, the graveyard
    python3 scripts/dfs_transfer.py probe goalies    # re-measure one probe (money|pot|tiers|goalies|nfl-questionable|all)
    python3 scripts/dfs_transfer.py pareto           # ideas ranked by $/week + the selection gap + the goalie table
                                                     # -> data/harness/dfs_transfer_pareto.json (the dashboard's file)

Why it exists (Adam, 2026-11-11, after looking at an LLM "idea generator" that breeds ideas
and lets another LLM score them): "using methods that worked in one sport that can apply in
others". Our critique loop finds holes one sport at a time; a hole fixed in NBA was never
looked for in NHL. The first transfer tried here took 30 minutes and found one: NBA cycle 25
(11/7) shipped "an Expected starter was treated as certain", and NHL's board gives a DailyFaceoff
goalie with no news ("Projected") full starter credit -- 20% of those did not start (12/59 at
T-90, 10/24-11/10), 0 of 285 Likely / Confirmed did.

What an idea generator could not do and this does: every row is CHECKED against our code
(`where`), our data (`data`) and, where possible, MEASURED on it (`measured`, with n and the
date) -- an LLM's opinion of an idea is not evidence; the held-out test is. And the GRAVEYARD
lists what we tested and found null, so no critic or scout re-proposes it without saying what
is different now (the critique log's "not shipped" entries do this per sport; this is the
cross-sport version).

How the loop uses it: scripts/dfs_target_scout.py <sport> includes this sport's open transfers,
the lessons shipped elsewhere since (`unmapped`: a fix the loop shipped that no LESSONS entry
covers yet -- every other sport's scout sees it until someone maps it) and the graveyard; the
dfs-target-scout agent weighs them with the money. After a cycle SHIPS, add its lesson to
LESSONS (general mechanism, not the sport's symptom) and a TRANSFERS row per sport to check.

Pricing (pareto): a measured hole is in DK points per entered lineup; $/week = points x the
sport's dollars per point per week = sum over cash / GPP of (our weekly entry fees, last 28
days) x (ROI per +1 DK point: the field-average slope of payout vs score in our own contests,
contests.db). A single linear rescale of every projection changes no lineup (the optimizer's
argmax is the same), so calibration holes are priced by their RELATIVE part only.
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import sqlite3
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

OUT = ROOT / "data" / "harness" / "dfs_transfers.json"
PARETO = ROOT / "data" / "harness" / "dfs_transfer_pareto.json"
LEDGER = ROOT / "data" / "harness" / "critique_ledger.jsonl"
SCORECARD = ROOT / "data" / "dfs_scorecard.json"
CONTESTS = ROOT / "data" / "contests.db"
SPORTS = ("nba", "ncaab", "nhl", "ncaaf", "nascar", "nfl", "mma", "mlb")

# ── what a sport proved ─────────────────────────────────────────────────────
#: `covers` = (sport, date) of the shipped cycles this lesson generalises; a shipped cycle in
#: the critique ledger / scorecard that no lesson covers is listed to every other sport as
#: unmapped until it is added here.
LESSONS = [
    {"id": "L01", "from": "nba", "mechanism": "an unconfirmed starter treated as certain",
     "ref": "NBA minutes cycles 4 (10/22) + 25 (11/7)",
     "evidence": "Expected starters also listed Questionable dressed 45% (n 31) but got full starter "
                 "minutes; hedged: minutes RMSE 5.843 -> 5.710 held out (3,521 rows, 14 nights)",
     "covers": [("nba", "2026-11-07")]},
    {"id": "L02", "from": "nba", "mechanism": "a Questionable / Doubtful player who plays is limited",
     "ref": "NBA minutes cycle 16 (10/28)",
     "evidence": "Doubtful / Questionable players who dress play a minutes limit; shipped held out",
     "covers": []},
    {"id": "L03", "from": "nba", "mechanism": "a market the books price on our quantity, collected and never read",
     "ref": "NBA cycle 27 (11/8)",
     "evidence": "the DK player props blended in: CASH lineups cashed 24% -> 65% held out "
                 "(17 contests / 12 dates); NCAAF 10/17 ladders beat usage/pace models the same way",
     "covers": [("nba", "2026-11-08")]},
    {"id": "L04", "from": "nfl", "mechanism": "a median read as a mean (prop lines are medians; a skewed stat's mean sits above)",
     "ref": "NFL cycle 2 (11/10) + NASCAR cycle 3 (10/25)",
     "evidence": "RB yardage line read as the mean: backs ran +1.22 pts low, cheap backs most; NASCAR's noise "
                 "scale was chosen for the median while lineups use the mean (k 0.7 -> 0.85)",
     "covers": [("nfl", "2026-11-10"), ("nascar", "2026-10-25")]},
    {"id": "L05", "from": "nhl", "mechanism": "projection error that depends on tier (a knot, not a scale)",
     "ref": "NHL cycles 8 + 10 (10/29-10/31)",
     "evidence": "the sim over-projected every skater (scale 0.976) and the $8.5k+ tier differently "
                 "(points knot 1.1, top stretch 1.7), both shipped held out",
     "covers": [("nhl", "2026-10-29"), ("nhl", "2026-10-31")]},
    {"id": "L06", "from": "ncaab", "mechanism": "an adjustment applied twice",
     "ref": "NCAAB cycle 3 (11/8) + NASCAR cycle 2 (10/25)",
     "evidence": "the forward log already held LAST_GAME and the lab applied it again (11/7 graded on a "
                 "board nobody built); NASCAR's simulator counted wrecks twice",
     "covers": [("ncaab", "2026-11-08")]},
    {"id": "L07", "from": "nba", "mechanism": "phantom games in a player's history (ruled Out counted as 0-minute games)",
     "ref": "NBA minutes cycles 8 + 15 (10/24, 10/28)",
     "evidence": "~1,000 injured 0-minute 'actives' a season in the lab truth; rank-gap refit after removal",
     "covers": []},
    {"id": "L08", "from": "nhl", "mechanism": "graders score the lineups we LOGGED, not the ones we ENTERED",
     "ref": "NHL cycle 11 runner-up (10/31)",
     "evidence": "10/24: the logged lineups held Kuemper (did not start); the entered ones did not",
     "covers": []},
    {"id": "L09", "from": "nba", "mechanism": "news-driven cheap chalk (a starter out makes a cheap replacement heavy chalk)",
     "ref": "NBA ownership cycles 1-2 (10/28) + Adam's promoted-bench note",
     "evidence": "cheap starters / news chalk under-owned by the model; both cycles shipped (GPP only)",
     "covers": []},
    {"id": "L10", "from": "ncaaf", "mechanism": "DK's own FPPG (on the player card) drives field ownership",
     "ref": "NCAAF ownership 10/17", "evidence": "DK-FPPG ownership term shipped held out", "covers": []},
    {"id": "L11", "from": "all", "mechanism": "a name join silently drops players",
     "ref": "names audit 10/17 + NHL cycles 5, 11b",
     "evidence": "book spellings and nicknames (DK Freddy / DFO Frederick) left stars without props or lines; "
                 "fixed in every sport; the scout audits name suspects each cycle",
     "covers": []},
    {"id": "L12", "from": "overlay", "mechanism": "late-listed re-posts priced on a pooled curve (overlay only)",
     "ref": "overlay cycle (11/9)", "evidence": "re-posts fill 0.88 of room; no DFS-model analogue",
     "covers": [("overlay", "2026-11-09")]},
]

# ── each lesson, checked in the other sports ────────────────────────────────
#: status: open (the hole is in our code, data on hand) | measure (open, not yet measured) |
#: waiting (no data yet: what would make it testable) | handled (the sport already does it) |
#: null (measured here: no effect) | audit (mechanical check, eval integrity, not priced)
#: kind: projection | availability | ownership | eval | market
#: measured: the probe's numbers (date, n). pts_lineup = DK points per entered lineup the hole
#: costs (held out where stated; "upper bound" when re-optimising would recover less).
TRANSFERS = [
    {"id": "T01", "lesson": "L01", "sport": "nhl", "kind": "availability", "status": "open",
     "idea": "a DailyFaceoff goalie with no news ('Projected') gets full starter credit: give him P(start)",
     "where": "edge/dfs_run_nhl.py:579 stores starter_status; nothing reads it",
     "data": "690 DailyFaceoff snapshots on the data branch (10/16-), box-score starters (NHL API)",
     "probe": "goalies", "target": "availability goalies (queued for the 11/12 night)",
     "measured": {"at": "2026-11-11", "pts_lineup": 0.75,
                  "note": "status 90 min before puck drop vs who started, 10/17-11/10: Projected wrong 12/59 "
                          "(20%), Likely 0/56, Confirmed 0/229. Our logged lineups: 25 of 132 goalie slots "
                          "Projected at T-60, 8 did not start (Kuemper 10/24, Saros 11/5) -> 8 x ~12.3 pts / "
                          "132 lineups = 0.75 pts/lineup if not swapped. Adam swapped both by hand (0 entered), "
                          "so the realised cost was his time before lock, not points."}},
    {"id": "T02", "lesson": "L05", "sport": "nba", "kind": "projection", "status": "open",
     "idea": "the top projection tier is overrated: a knot on projection level (NHL cycle 10's shape), "
             "plausibly from turnovers + double-doubles left in the blend's constant (T03) and count "
             "props read through a normal (T04)",
     "where": "edge/dfs_run_nba.py PROPS_BLEND / market_dk", "data": "contests.db: 18 graded slates with our entries",
     "probe": "tiers", "target": "projection tier / stars overrated (lineup lab)",
     "measured": {"at": "2026-11-11", "pts_lineup": 5.0, "upper_bound": True,
                  "note": "train 10 slates -> test 8 slates (20 entered lineups, mostly before the 11/8 props "
                          "blend): our rostered players ran -10.2 pts/lineup below the slate's own miss; "
                          "projection-quintile correction fitted on train explains -5.0 of it (top quintile "
                          "-1.74/player relative); salary quintiles do not (+2.4, wrong sign). NBA cycle 20 "
                          "found no SALARY-tier bias: this is projection level. Re-measure on post-blend slates."}},
    {"id": "T03", "lesson": "L03", "sport": "nba", "kind": "market", "status": "measure",
     "idea": "turnovers (-0.5 DK each; stars 3-4 a game, bench ~0.7) and double-double odds are left in the "
             "blend's constant; player_turnovers and the combo markets are collected, never read",
     "where": "edge/dfs_run_nba.py:204 ('turnovers and double-double bonuses are absorbed by the intercept')",
     "data": "odds.db dfs_nba scans since 10/15 (scout: 6 unused markets)",
     "probe": None, "target": "unused props: turnovers (lineup lab props-fit)"},
    {"id": "T04", "lesson": "L04", "sport": "nba", "kind": "market", "status": "measure",
     "idea": "count props (steals, blocks, threes, lines 0.5-2.5) read through a normal with sd 1.1 sqrt(line): "
             "NHL reads count props as a Poisson mean (nhl_sim.market_mean); NFL's median-vs-mean lesson",
     "where": "edge/dfs_run_nba.py market_dk / PROP_SD_K (not fitted)", "data": "odds.db + DK points by draft group",
     "probe": None, "target": "market mapping: Poisson count props"},
    {"id": "T05", "lesson": "L02", "sport": "nfl", "kind": "projection", "status": "open",
     "idea": "a Questionable RB / WR who plays scores ~1 pt below his projection; the tag only feeds ownership",
     "where": "edge/dfs_nfl_theory.py INTEL_* (ownership only); edge/dfs_run_nfl.py:418 keeps Q at full projection",
     "data": "nflverse injuries 2023-26 (fetched 11/11) + our 9 graded 2026 slates",
     "probe": "nfl-questionable", "target": "rb / wr questionable",
     "measured": {"at": "2026-11-11", "pts_lineup": 0.4,
                  "note": "2023-25 (players who played, PPR vs own trailing-6 mean, vs untagged): WR -1.20 +-0.91 "
                          "(n 195), RB -1.05 +-1.11 (n 106), TE -0.04. 2026 vs OUR projection: Questionable "
                          "-0.32 vs untagged +0.61 (n 66 / 1,360, 9 slates): -0.93 relative, not significant "
                          "alone. ~0.4 Q players per lineup x ~1 pt."}},
    {"id": "T06", "lesson": "L03", "sport": "nhl", "kind": "market", "status": "measure",
     "idea": "anytime-goal props (player_goals) are collected and unread; goals come from points minus assists",
     "where": "edge/dfs_run_nhl.py skater_means (points, SOG, assists props)", "data": "odds.db dfs_nhl scans",
     "probe": None, "target": "unused props: player_goals (skater lab)"},
    {"id": "T07", "lesson": "L01", "sport": "ncaab", "kind": "availability", "status": "measure",
     "idea": "RotoWire's projected college starters used as certain (NBA's Expected-five hole)",
     "where": "edge/dfs_run_ncaab.py rotowire starters", "data": "graded slates 5 (box scores via ncaab-box-grade)",
     "probe": None, "target": "availability starters"},
    {"id": "T08", "lesson": "L05", "sport": "ncaab", "kind": "projection", "status": "waiting",
     "idea": "tier / skew knot; our rostered players ran -37 pts/lineup vs the field's, scored on the same "
             "projection (the largest gap of any sport)",
     "data": "5 graded slates: needs ~10 for a 60/40 held-out split (about 11/20 at the current pace)",
     "probe": "tiers", "target": None},
    {"id": "T09", "lesson": "L03", "sport": "nascar", "kind": "market", "status": "waiting",
     "idea": "no odds are collected at all (win / top-5 / top-10 / H2H); markets beat our model in NBA and NCAAF, "
             "and NASCAR's selection gap is -55 pts/lineup (4 slates)",
     "data": "season over (last race 11/8): add an edge/odds profile before Daytona (Feb 2027) so the first "
             "races are logged", "probe": None, "target": None},
    {"id": "T10", "lesson": "L05", "sport": "ncaaf", "kind": "projection", "status": "null",
     "idea": "tier / projection-level knot", "data": "11 slates", "probe": "tiers", "target": None,
     "measured": {"at": "2026-11-11", "pts_lineup": 0.0,
                  "note": "train 6 / test 5 slates: the train-fitted correction points the wrong way on test "
                          "(+3.7 predicted vs -6.5 realised per lineup) -- no stable tier shape yet"}},
    {"id": "T11", "lesson": "L05", "sport": "nfl", "kind": "projection", "status": "null",
     "idea": "tier / projection-level knot", "data": "9 slates", "probe": "tiers", "target": None,
     "measured": {"at": "2026-11-11", "pts_lineup": 0.0,
                  "note": "our NFL lineups beat their projection by +12.5/lineup vs the field (9 slates): nothing "
                          "to correct; the projection IS the props"}},
    {"id": "T12", "lesson": "L04", "sport": "nhl", "kind": "market", "status": "handled",
     "idea": "median vs mean on props", "where": "nhl_sim.market_mean: a Poisson mean from the devigged line",
     "data": "-", "probe": None, "target": None},
    {"id": "T13", "lesson": "L04", "sport": "ncaaf", "kind": "market", "status": "handled",
     "idea": "median vs mean on yardage props", "where": "edge/dfs_ladder.py: per-market fitted c (pass .53, rec .38)",
     "data": "-", "probe": None, "target": None},
    {"id": "T14", "lesson": "L06", "sport": "all", "kind": "eval", "status": "audit",
     "idea": "every lab: rebuild a logged slate and compare its projection with the forward log's, player by "
             "player -- a double-applied (or missing) adjustment shows as a constant ratio on a subset",
     "data": "forward logs + each lab's cache", "probe": None, "target": None},
    {"id": "T15", "lesson": "L07", "sport": "ncaab", "kind": "eval", "status": "audit",
     "idea": "per-game history: are DNP / ruled-out games counted as 0-minute (0-point) games?",
     "where": "edge/dfs_run_ncaab.py last-season / LAST_GAME history", "data": "box scores", "probe": None, "target": None},
    {"id": "T16", "lesson": "L07", "sport": "nhl", "kind": "eval", "status": "audit",
     "idea": "per-game rates: scratched games counted as 0-TOI games?", "where": "edge/nhl_sim.py / nhl_cache rates",
     "data": "box scores", "probe": None, "target": None},
    {"id": "T17", "lesson": "L08", "sport": "all", "kind": "eval", "status": "audit",
     "idea": "grade what was ENTERED (contests.db is_adam lineups), not what the app logged; report the share "
             "of entered lineups that match a logged one, per sport",
     "data": "contests.db entry_player (is_adam) + data/dfs_lineups_<sport>_*.csv", "probe": None, "target": None},
    {"id": "T18", "lesson": "L09", "sport": "nfl", "kind": "ownership", "status": "measure",
     "idea": "a backup RB / WR promoted by an Out tag becomes heavy GPP chalk; vacated() already measures the volume",
     "where": "edge/nfl_intel.py vacated() -> ownership?", "data": "15 GPP slates with field ownership",
     "probe": None, "target": "ownership: news chalk (GPP only)"},
    {"id": "T19", "lesson": "L09", "sport": "nhl", "kind": "ownership", "status": "measure",
     "idea": "a skater promoted to PP1 / the top line by news becomes chalk", "where": "edge/dfs_nhl_theory.py",
     "data": "28 GPP slates; DFO line snapshots", "probe": None, "target": "ownership: promotions (GPP only)"},
    {"id": "T20", "lesson": "L10", "sport": "nba", "kind": "ownership", "status": "measure",
     "idea": "DK FPPG as an ownership input (NHL, NFL, NCAAF, NCAAB, NASCAR boards carry it; NBA's theory does not)",
     "where": "edge/dfs_nba_theory.py", "data": "26 GPP slates", "probe": None, "target": "ownership: dk fppg (GPP only)"},
]

# ── what we tested and found null (do not re-propose without saying what changed) ──
GRAVEYARD = [
    {"idea": "ownership as an ROI signal at a fixed projection (chalk vs contrarian)", "sports": "mlb, nhl (all but mma)",
     "evidence": "108 exports / 201,928 entries: no detectable effect on cash rate or ROI (GPP_META.md)",
     "when": "2026-10", "retest": "a sport where ownership is fitted per contest type and n > 10 slates"},
    {"idea": "fade the public / inverse consensus", "sports": "all",
     "evidence": "the crowd does not overreact; no fade rule beats noise (10/19)", "when": "2026-10-19", "retest": "-"},
    {"idea": "tout picks / consensus as a signal", "sports": "all",
     "evidence": "graded touts are coin flips; X touts lose (public_bettors 10/27)", "when": "2026-10-27",
     "retest": "Juice Reel leaderboard after its forward test"},
    {"idea": "next-game minutes bounce after foul trouble or a blowout", "sports": "nba",
     "evidence": "no bounce either way (11/10)", "when": "2026-11-10", "retest": "-"},
    {"idea": "stars are under-projected", "sports": "nba (nhl untested)",
     "evidence": "selection on winners: winners' players are on our board 99%; cycle 20 no salary-tier bias "
                 "(T02 is a different, projection-level shape)", "when": "2026-10-29", "retest": "-"},
    {"idea": "referees / travel / time zones", "sports": "nhl",
     "evidence": "null on NHL skater points (10/19)", "when": "2026-10-19", "retest": "-"},
    {"idea": "NHL ownership as a signal; NHL YouTube buzz for ownership", "sports": "nhl",
     "evidence": "null (10/19); buzz showed no relationship with cash ownership", "when": "2026-10-19", "retest": "-"},
    {"idea": "CourtIQ as a projection input", "sports": "nba", "evidence": "cycles 22-23 not shipped",
     "when": "2026-10-29", "retest": "-"},
    {"idea": "blowout-potential minutes correction; P(start) inside the 240 step", "sports": "nba",
     "evidence": "cycles 10-11 not shipped", "when": "2026-10-27", "retest": "-"},
    {"idea": "goalie DK points from the saves prop / goal edge", "sports": "nhl",
     "evidence": "cycle 7: no fixable hole (projection sd 1.1 vs actual 9.8 caps rho ~0.11)", "when": "2026-10-28",
     "retest": "~40 slates (T01 is availability, not points)"},
    {"idea": "salary saver (Mans rule)", "sports": "nfl",
     "evidence": "loses in the 10/31-11/1 replay; dropping top chalk is the live lead (decide ~12/6)",
     "when": "2026-11-01", "retest": "~12/6 with the GPP dollar replay"},
]


# ── lessons shipped since that no LESSONS entry covers ──────────────────────
def shipped_cycles() -> list[dict]:
    """Every shipped / merged cycle the ledger and the scorecard know of: {sport, date, hole}."""
    out = []
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            shipped = r.get("event") == "merged" or (r.get("event") == "manual" and str(r.get("verdict", "")).lower().startswith("ship"))
            if shipped and r.get("sport"):
                out.append({"sport": r["sport"].lower(), "date": str(r.get("at", ""))[:10], "hole": r.get("hole") or ""})
    try:
        for c in json.loads(SCORECARD.read_text()).get("critique_cycles", []):
            if c.get("shipped"):
                out.append({"sport": c["sport"].lower(), "date": c.get("date", ""), "hole": c.get("hole") or ""})
    except (OSError, ValueError):
        pass
    return out


def unmapped(cycles: list[dict] | None = None) -> list[dict]:
    """Shipped cycles no lesson covers (same sport, within a day of a covered date)."""
    covered = [(s, datetime.strptime(d, "%Y-%m-%d").date()) for L in LESSONS for s, d in L["covers"]]
    seen, out = set(), []
    for c in cycles if cycles is not None else shipped_cycles():
        try:
            d = datetime.strptime(c["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if any(s == c["sport"] and abs((d - cd).days) <= 1 for s, cd in covered):
            continue
        key = (c["sport"], c["hole"][:80])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def for_sport(sport: str) -> dict:
    """What the scout reads for one sport."""
    lessons = {L["id"]: L for L in LESSONS}
    rows = [t for t in TRANSFERS if t["sport"] in (sport, "all")]
    return {"open": [{"id": t["id"], "status": t["status"], "kind": t["kind"], "idea": t["idea"],
                      "from": f"{lessons[t['lesson']]['from']}: {lessons[t['lesson']]['ref']}",
                      "data": t["data"], "target": t.get("target"),
                      "measured": (t.get("measured") or {}).get("note")}
                     for t in rows if t["status"] in ("open", "measure", "audit")],
            "settled": [f"{t['id']} {t['status']}: {t['idea']}" for t in rows if t["status"] in ("handled", "null", "waiting")],
            "unmapped_lessons": [c for c in unmapped() if c["sport"] != sport],
            "graveyard": [g for g in GRAVEYARD if sport in g["sports"] or "all" in g["sports"]]}


# ── probes: every number above, rebuilt from the data ───────────────────────
def _db():
    return sqlite3.connect(f"file:{CONTESTS}?mode=ro", uri=True)


def probe_money(days: int = 28) -> dict:
    """{SPORT: {"dppw": $ per +1 DK point per lineup per week, cash / gpp parts}}."""
    c = _db()
    roi_pt = defaultdict(list)
    for cid, sport, typ, fee in c.execute(
            "select contest_id, sport, type, entry_fee from contest where slate_date >= '2026-06-28' "
            "and entry_fee > 0 and type in ('cash', 'gpp')"):
        rows = c.execute("select points, coalesce(payout, 0) from entry_feature where contest_id = ? and live = 1 "
                         "and points is not null order by points", (cid,)).fetchall()
        if len(rows) < 20:
            continue
        xs, best, m = [r[0] for r in rows], [], 0.0
        for _, y in rows:
            m = max(m, y)
            best.append(m)          # payout an entry scoring x would have earned (monotone envelope)

        def pay(x):
            i = bisect.bisect_right(xs, x) - 1
            return best[i] if i >= 0 else 0.0
        roi_pt[(sport, typ)].append(st.mean((pay(s + 2) - pay(s)) / 2 for s in xs) / fee)
    card = json.loads(SCORECARD.read_text())
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    fees = defaultdict(float)
    for r in card.get("slates", []):
        if r["period"] == "all" and r["type"] in ("cash", "gpp") and r["date"] >= since and r["sport"] != "all":
            fees[(r["sport"], r["type"])] += r["fee"] * 7 / days
    out = {}
    for sport in {s for s, _ in list(roi_pt) + list(fees)}:
        parts = {t: {"fee_week": round(fees[(sport, t)], 2),
                     "roi_per_pt": round(st.median(roi_pt[(sport, t)]), 4) if roi_pt[(sport, t)] else 0.0,
                     "contests": len(roi_pt[(sport, t)])} for t in ("cash", "gpp")}
        out[sport] = {"dppw": round(sum(p["fee_week"] * p["roi_per_pt"] for p in parts.values()), 3), **parts}
    return out


def probe_pot() -> dict:
    """Per sport: our entered lineups' (points - our projection) minus the field's, by slate,
    with a slate bootstrap. Optimising on a noisy projection picks its errors (a negative
    number is partly inherent); NFL's positive one shows it need not be."""
    c = _db()
    by = defaultdict(lambda: defaultdict(lambda: [[], []]))
    for sport, d, adam, gap in c.execute(
            "select k.sport, k.slate_date, f.is_adam, f.points - f.proj_sum from entry_feature f "
            "join contest k using(contest_id) where f.live = 1 and f.proj_cov >= 0.99 and f.points is not null "
            "and k.slate_date >= '2026-06-28'"):
        by[sport][d][1 if adam else 0].append(gap)
    out = {}
    for sport, days in by.items():
        diff = {d: st.mean(a) - st.mean(f) for d, (f, a) in days.items() if a and len(f) > 20}
        if not diff:
            continue
        rnd, ks, boots = random.Random(1), list(diff), []
        for _ in range(2000):
            boots.append(st.mean(diff[rnd.choice(ks)] for _ in ks))
        boots.sort()
        out[sport] = {"slates": len(diff), "gap": round(st.mean(diff.values()), 2),
                      "lo": round(boots[100], 2), "hi": round(boots[1900], 2)}
    return out


def probe_tiers(train_share: float = 0.6) -> dict:
    """Per sport: pool residual (actual - proj) by salary and by projection quintile, RELATIVE
    to the slate's mean miss (a constant moves no lineup), fitted on the first 60% of slates,
    applied to the players we ENTERED on the rest: points per lineup it says we overrated vs
    the realised relative miss of those players."""
    c = _db()
    pool = {}
    for s, d, pn, sal, pr, f in c.execute("""
        select m.sport, m.slate_date, p.player_norm, p.salary, p.proj, r.fpts
        from contest_match m
        join projection p on p.draft_group = m.proj_draft_group and p.date = m.proj_date and p.sport = m.sport
             and (m.proj_variant is null or p.variant = m.proj_variant)
        join player_result r on r.contest_id = m.contest_id and r.player_norm = p.player_norm
        where m.ok_points = 1 and m.slate_date >= '2026-06-28' and p.proj > 0 and r.fpts is not null"""):
        pool[(s, d, pn)] = (sal, pr, f)
    lineups = defaultdict(list)
    for s, d, cid, eid, pn in c.execute(
            "select k.sport, k.slate_date, e.contest_id, e.entry_id, e.player_norm from entry_player e "
            "join entry_feature f using(contest_id, entry_id) join contest k using(contest_id) "
            "where f.is_adam = 1 and f.live = 1 and k.slate_date >= '2026-06-28'"):
        lineups[(s, d, cid, eid)].append(pn)

    def q(edges, x):
        return next((i for i, v in enumerate(edges) if x <= v), len(edges))
    out = {}
    for sport in sorted({k[0] for k in pool}):
        rows = [(d, pn, *v) for (s, d, pn), v in pool.items() if s == sport]
        dates = sorted({r[0] for r in rows})
        if len(dates) < 6:
            out[sport] = {"slates": len(dates), "note": "not enough data"}
            continue
        cut = dates[int(len(dates) * train_share)]
        tr = [r for r in rows if r[0] < cut]
        te = {(r[0], r[1]): r for r in rows if r[0] >= cut}
        te_bias = {d: st.mean(r[4] - r[3] for r in te.values() if r[0] == d) for d in {r[0] for r in te.values()}}
        res = {"slates_train": sum(d < cut for d in dates), "slates_test": sum(d >= cut for d in dates)}
        for name, key in (("salary", 2), ("proj", 3)):
            edges = [sorted(r[key] for r in tr)[int(len(tr) * k / 5)] for k in (1, 2, 3, 4)]
            bins = defaultdict(list)
            for r in tr:
                bins[q(edges, r[key])].append(r[4] - r[3])
            base = st.mean(r[4] - r[3] for r in tr)
            corr = {b: st.mean(v) - base for b, v in bins.items()}
            pred, real = [], []
            for (s, d, _, _), pns in lineups.items():
                got = [te.get((d, pn)) for pn in pns] if s == sport and d >= cut else []
                if got and all(got):
                    pred.append(sum(corr[q(edges, g[key])] for g in got))
                    real.append(sum((g[4] - g[3]) - te_bias[d] for g in got))
            res[name] = {"quintiles": [round(corr.get(b, 0), 2) for b in range(5)], "lineups": len(pred),
                         "predicted": round(st.mean(pred), 2) if pred else None,
                         "realised": round(st.mean(real), 2) if real else None}
        out[sport] = res
    return out


def probe_goalies(start: str = "2026-10-17", end: str | None = None) -> dict:
    """NHL: DailyFaceoff's goalie + status 90 / 60 / 30 min before puck drop (rebuilt from the
    data branch's nhl_lines_snapshot.json history) vs the box score's starter; and our logged
    lineups' goalies by status. NHL API calls only (schedule + box scores, cached)."""
    import csv
    import subprocess
    from edge import nhl
    from edge.names import norm
    path = "data/nhl_lines_snapshot.json"
    log = subprocess.run(["git", "log", "--format=%H %cI", "origin/data", "--", path], cwd=ROOT,
                         capture_output=True, text=True).stdout.split("\n")
    snaps = []
    for ln in filter(str.strip, log):
        h, t = ln.split()
        try:
            g = json.loads(subprocess.run(["git", "show", f"{h}:{path}"], cwd=ROOT, capture_output=True,
                                          text=True).stdout).get("goalies") or {}
        except ValueError:
            continue
        snaps.append((datetime.fromisoformat(t).astimezone(timezone.utc), g))
    snaps.sort(key=lambda x: x[0])
    times = [t for t, _ in snaps]

    def state(date, team, at):
        for i in range(bisect.bisect_right(times, at) - 1, -1, -1):
            e = (snaps[i][1].get(date) or {}).get(team)
            if e:
                return e
        return None

    def sig(n):
        p = (n or "").replace(".", " ").split()
        return f"{norm(p[0])[:1]} {norm(p[-1])}" if p else ""
    rows, day = [], datetime.strptime(start, "%Y-%m-%d")
    stop = datetime.strptime(end, "%Y-%m-%d") if end else datetime.now() - timedelta(days=1)
    while day <= stop:
        date = day.strftime("%Y-%m-%d")
        day += timedelta(days=1)
        for g in (x for x in nhl.schedule(date) if x["type"] == 2):
            box = nhl.boxscore(g["id"])
            if not box.get("goalies"):
                continue
            t0 = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
            for team in (g["away"], g["home"]):
                started = [x for x in box["goalies"] if x["team"] == team and x.get("starter")]
                if not started:
                    continue
                rec = {"date": date, "team": team, "actual": started[0]["name"]}
                for lead in (90, 60, 30):
                    e = state(date, team, t0 - timedelta(minutes=lead))
                    rec[f"s{lead}"] = ((e or {}).get("status") or "Projected") if e else "none"
                    rec[f"ok{lead}"] = None if not e else sig(e.get("goalie")) == sig(started[0]["name"])
                rows.append(rec)
    out = {"team_games": len(rows), "by_status": {}}
    for lead in (90, 60, 30):
        k = defaultdict(Counter)
        for r in rows:
            k[r[f"s{lead}"]][r[f"ok{lead}"]] += 1
        out["by_status"][f"T-{lead}"] = {s: {"n": sum(v.values()), "wrong": v[False]} for s, v in k.items()}
    idx = {(r["date"], r["team"]): r for r in rows}
    slots, missed, pts = Counter(), [], 0.0
    for f in sorted((ROOT / "data").glob("dfs_lineups_nhl_20??-??-??.csv")):
        d = f.name[-14:-4]
        for r in csv.DictReader(f.open()):
            g = idx.get((d, r["team"])) if r["slot"] == "G" else None
            if not g:
                continue
            slots[g["s60"]] += 1
            if g["s60"] == "Projected" and sig(r["player"]) != sig(g["actual"]):
                missed.append(f"{d} {r['mode']} {r['player']}")
                pts += float(r["proj"] or 0)
    n = sum(slots.values())
    out["our_lineups"] = {"goalie_slots_by_status_T-60": dict(slots), "projected_not_started": missed,
                          "pts_lineup_if_unswapped": round(pts / n, 2) if n else None}
    return out


def probe_nfl_questionable() -> dict:
    """NBA cycle 16 in NFL: (a) 2023-25 nflverse, players who played: PPR minus own trailing-6
    mean, Questionable vs untagged; (b) 2026 graded slates: actual - our projection by Friday status."""
    import csv
    from edge import nfl_intel as I
    from edge.names import norm
    hist = defaultdict(list)
    for season in (2023, 2024, 2025):
        inj = {(norm(r["full_name"]), int(r["week"])): (r.get("report_status") or "").strip()
               for r in I._csv(I._fetch("injuries", f"injuries_{season}.csv", season))
               if r.get("game_type", "REG") == "REG"}
        games = defaultdict(list)
        for r in I._csv(I._fetch("stats_player", f"stats_player_week_{season}.csv", season)):
            if r.get("season_type", "REG") == "REG" and r.get("position") in ("QB", "RB", "WR", "TE"):
                try:
                    games[norm(r.get("player_display_name") or r["player_name"])].append(
                        (int(r["week"]), float(r["fantasy_points_ppr"] or 0), r["position"]))
                except (KeyError, ValueError):
                    continue
        for name, g in games.items():
            g.sort()
            for i, (wk, pts, pos) in enumerate(g):
                prev = [p for _, p, _ in g[max(0, i - 6):i]]
                if len(prev) >= 3 and st.mean(prev) >= 5:
                    hist[(pos, inj.get((name, wk)) or "none")].append(pts - st.mean(prev))
    out = {"history": {}}
    for pos in ("QB", "RB", "WR", "TE"):
        base = st.mean(hist[(pos, "none")]) if hist[(pos, "none")] else 0.0
        v = hist.get((pos, "Questionable"), [])
        if len(v) >= 30:
            out["history"][pos] = {"n": len(v), "vs_untagged": round(st.mean(v) - base, 2),
                                   "ci90": round(1.64 * st.pstdev(v) / len(v) ** .5, 2)}
    wk = {r["gameday"]: int(r["week"]) for r in csv.DictReader((ROOT / "data" / "nflverse_games.csv").open())
          if r.get("season") == "2026" and r.get("game_type", "REG") == "REG"}
    inj = {(norm(r["full_name"]), int(r["week"])): r["report_status"].strip()
           for r in I._csv(I._fetch("injuries", "injuries_2026.csv", 2026))
           if r.get("game_type", "REG") == "REG" and (r.get("report_status") or "").strip()}
    by, slates = defaultdict(list), defaultdict(set)
    for r in json.loads(SCORECARD.read_text())["player_games"]:
        w = wk.get(r["date"])
        if r["sport"] == "NFL" and w is not None and r["pos"] in ("QB", "RB", "WR", "TE") and r["proj"] >= 5:
            tag = inj.get((norm(r["name"]), w), "none")
            by[tag].append(r["actual"] - r["proj"])
            slates[tag].add(r["date"])
    out["ours_2026"] = {t: {"n": len(v), "slates": len(slates[t]), "resid": round(st.mean(v), 2)} for t, v in by.items()}
    return out


PROBES = {"money": probe_money, "pot": probe_pot, "tiers": probe_tiers, "goalies": probe_goalies,
          "nfl-questionable": probe_nfl_questionable}

#: dollars per +1 DK point per entered lineup per week, by sport -- probe_money() on 2026-11-11
#: (ROI per point = median over our 2026 contests of the field-average payout slope; fees = last
#: 28 days of our entries). Re-run `probe money` before quoting; pareto() uses the live value.
DPPW_2026_11_11 = {"NBA": 2.17, "NHL": 1.44, "NFL": 1.23, "NCAAF": 0.70, "NCAAB": 0.24,
                   "NASCAR": 0.03, "MMA": 0.01, "MLB": 0.0}


def pareto(live_money: bool = True, probes: bool = True) -> dict:
    """Every transfer ranked: priced ones by $/week (measured points x the sport's $/point-week),
    then the unpriced open ones by how much a point is worth in their sport. With `probes`, also
    the selection gap by sport and the NHL goalie table, so this one file is exactly what the
    "DFS Idea Pareto" dashboard shows (its machine-readable twin; Adam 11/11)."""
    dppw = DPPW_2026_11_11
    source = "constant 2026-11-11"
    if live_money:
        try:
            dppw = {k: v["dppw"] for k, v in probe_money().items()}
            source = "probe_money (live)"
        except Exception as exc:                              # noqa: BLE001 -- the constant is the fallback
            source = f"constant 2026-11-11 (probe failed: {exc})"
    lessons = {L["id"]: L for L in LESSONS}
    rows = []
    for t in TRANSFERS:
        m = t.get("measured") or {}
        sports = [t["sport"].upper()] if t["sport"] != "all" else []
        k = dppw.get(sports[0], 0.0) if sports else 0.0
        priced = bool(m) and t["status"] == "open"
        rows.append({"id": t["id"], "sport": t["sport"], "kind": t["kind"], "status": t["status"],
                     "idea": t["idea"], "from": f"{lessons[t['lesson']]['from'].upper()}: {lessons[t['lesson']]['ref']}",
                     "mechanism": lessons[t["lesson"]]["mechanism"], "data": t["data"], "target": t.get("target"),
                     "pts_lineup": m.get("pts_lineup"), "upper_bound": bool(m.get("upper_bound")),
                     "usd_week": round(m["pts_lineup"] * k, 2) if priced else None,
                     "usd_per_point_week": k, "evidence": m.get("note") or ""})
    order = {"open": 0, "measure": 1, "audit": 2, "waiting": 3, "null": 4, "handled": 5}
    rows.sort(key=lambda r: (r["usd_week"] is None, -(r["usd_week"] or 0), order[r["status"]], -r["usd_per_point_week"]))
    total = sum(r["usd_week"] or 0 for r in rows)
    cum = 0.0
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["cum_share"] = None
        if r["usd_week"] is not None:
            cum += r["usd_week"]
            r["cum_share"] = round(cum / total, 3) if total else None
    doc = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "dppw": dppw,
           "dppw_source": source, "ideas": rows, "graveyard": GRAVEYARD, "unmapped_lessons": unmapped()}
    if probes:
        pot = probe_pot()
        doc["selection"] = [{"sport": s, **v, "usd_per_point_week": round(dppw.get(s, 0), 2),
                             "enough_data": v["slates"] >= 5} for s, v in sorted(pot.items(), key=lambda kv: kv[1]["gap"])]
        try:
            g = probe_goalies()
            doc["goalies"] = [{"key": f"{lead} {st_}", "lead": lead, "status": st_, "n": x["n"], "wrong": x["wrong"],
                               "wrong_share": round(x["wrong"] / x["n"], 3) if x["n"] else None}
                              for lead in ("T-90", "T-60")
                              for st_ in ("Confirmed", "Likely", "Projected")
                              for x in [g["by_status"][lead].get(st_, {"n": 0, "wrong": 0})]]
        except Exception as exc:                              # noqa: BLE001 -- NHL API down: say so, keep the rest
            doc["goalies"], doc["goalies_error"] = [], str(exc)
    return doc


def table() -> str:
    lines = ["| id | sport | status | kind | from | idea | measured |", "|---|---|---|---|---|---|---|"]
    lessons = {L["id"]: L for L in LESSONS}
    for t in TRANSFERS:
        m = t.get("measured") or {}
        lines.append(f"| {t['id']} | {t['sport']} | {t['status']} | {t['kind']} | {lessons[t['lesson']]['from']} | "
                     f"{t['idea'][:90]} | {m.get('pts_lineup', '-')} pts/lineup |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", nargs="?", default="table", help="table | pareto | probe | a sport")
    ap.add_argument("probe", nargs="?", choices=[*PROBES, "all"])
    a = ap.parse_args(argv)
    if a.what in SPORTS:
        print(json.dumps(for_sport(a.what), indent=1))
    elif a.what == "probe":
        for name in (PROBES if a.probe in (None, "all") else [a.probe]):
            print(f"== {name}\n{json.dumps(PROBES[name](), indent=1)}", flush=True)
    elif a.what == "pareto":
        doc = pareto()
        PARETO.parent.mkdir(parents=True, exist_ok=True)
        PARETO.write_text(json.dumps(doc, indent=1))
        for r in doc["ideas"]:
            usd = f"${r['usd_week']:.2f}/wk" if r["usd_week"] is not None else "-"
            print(f"{r['id']} {r['sport']:6s} {r['status']:8s} {usd:>11s}  {r['idea'][:100]}")
        print(f"-> {PARETO.relative_to(ROOT)}")
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                   "lessons": LESSONS, "transfers": TRANSFERS, "graveyard": GRAVEYARD,
                                   "unmapped_lessons": unmapped(),
                                   "by_sport": {s: for_sport(s) for s in SPORTS}}, indent=1, default=list))
        print(table())
        print(f"\n-> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
