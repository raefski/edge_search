"""DraftKings Sportsbook UFC markets -> a calibrated outcome table per fight.

THE QUANTITY DK'S WIN BONUS PAYS ON IS PRICED DIRECTLY
A DK MMA score is mostly a fight-conclusion bonus -- 90 for a first-round win,
70 second, 45 third, 30 for a decision -- and DraftKings Sportsbook prices the
exact joint distribution that bonus is a function of. Per fight, league 9034:

    Fight Lines              moneyline (2-way, ~3.5% hold), total rounds
    Method of Victory        KO/TKO/DQ, Submission, Decision -- one 2-way
                             market each, so six "fighter by method" prices
    Round and Method         fighter x method x round, plus "To Go the Distance"
    Fight to go the Distance Yes / No
    Significant Strikes O/U  every fighter on a full card
    Takedowns Landed O/U     every fighter on a full card

So unlike college (one-sided ladders) or NASCAR (no market at all), nothing
about the win bonus has to be inferred. It has to be DE-BIASED, and that part
was measured.

TWO BIASES, MEASURED ON 6,821 PRICED FIGHTS, 2010-2026
(ufc-master.csv, joined to UFCStats results; scripts/mma_fit.py --market)

1. THE METHOD MARKET UNDERPRICES DECISIONS, IN EVERY ERA.
   Power-devigged, the six method prices imply 45.7% of fights go the
   distance; 49.5% do. The gap is 3-4 points in 2013-16, 2017-19, 2020-22 and
   2023-26 alike, in three-round and five-round fights alike, and 6-8 points
   in the 10-40% buckets where most fights sit. The public bets knockouts.
   For DFS this is the expensive direction: a finish pays 45-90 and a decision
   30, so taking the market at face value inflates every fighter's win bonus.
   Fitted on fights before 2021 and tested on 2021-26, the correction
   `logit(p) = a + b*logit(p_mkt)` moved the mean from 0.469 to 0.508 against
   an actual 0.501 and cut the log loss by 0.0044.

2. FAVOURITES WIN MORE THAN THE MONEYLINE SAYS.
   The 80-90% bucket wins 88.2% against 83.7% implied; the 10-20% bucket wins
   12.2% against 16.3%. Power devig removes part of it (log loss 0.6055 vs
   0.6066 multiplicative); a logit slope removes a little more, stably
   (1.024 fitted on either pre-2019 or pre-2021 data).

HOW THE TABLE IS BUILT
Six method prices, power-devigged -> the joint (winner x method) table. Its
DECISION column is re-targeted to the calibrated distance probability and its
ROWS to the calibrated moneyline, by iterative proportional fitting. KO:SUB
within a fighter keeps the market's ratio. What comes out sums to one and
agrees with the two sharpest, most-measured margins available.
"""
from __future__ import annotations

import math
import re

from edge.names import norm
from edge.oddsmath import _power

#: DraftKings Sportsbook league id for UFC (catalog page, sport 43 -> 9034).
DK_BOOK_LEAGUE = 9034

#: logit(p_true) = ML_SLOPE * logit(p_power_devigged). Fitted on all 6,551
#: two-way-priced fights 2010-2026 (scripts/mma_fit.py --market). Out of
#: sample the slope is 1.024 from either a pre-2019 or a pre-2021 fit; the
#: full-sample value is higher because recent favourites are the more
#: underpriced.
ML_SLOPE = 1.058

#: logit(P(decision)) = DEC_A + DEC_B * logit(P_mkt(decision)), P_mkt from the
#: power-devigged six-way method market. Fitted on all 5,362 fights with the
#: full six prices; see the module docstring for the out-of-sample check.
DEC_A = 0.128
DEC_B = 0.780

#: UFCStats 2013-2026: 66 draws in 8,924 fights is ~0.7%, and DraftKings pays
#: nobody a bonus for one. The market prices a draw at +5000 (2% with hold);
#: the base rate is used instead of that because it is measured.
P_DRAW = 0.007

CELLS = ("A_KO", "A_SUB", "A_DEC", "B_KO", "B_SUB", "B_DEC")

#: When a bout has NO usable market, its win probability comes from DraftKings'
#: own salaries: logit(p_A) = SALARY_LOGIT_PER_K * (salary_A - salary_B)/1000.
#: DK prices MMA fighters off the betting line, and on the 2026-09-26 card the
#: eleven priced bouts put this at 0.576 with a mean absolute error of 0.023 in
#: probability -- so the salary gap is nearly the line itself. ONE CARD; the
#: forward log (data/dfs_proj_log_mma.csv) accumulates the pairs to refit it.
#: Far better than the 50/50 it replaced, which made a $7,300 debut against a
#: replacement opponent the chalkiest fighter on the board.
SALARY_LOGIT_PER_K = 0.576


def _lg(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _ilg(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def devig_power(decimals) -> list[float]:
    q = [1.0 / d for d in decimals]
    s = sum(q)
    return _power(q) if s > 1.0 else [x / s for x in q]


def win_prob(ml_a: float, ml_b: float, slope: float = ML_SLOPE) -> float:
    """P(A wins | not a draw) from the two moneyline decimals."""
    pa, _pb = devig_power([ml_a, ml_b])
    return _ilg(slope * _lg(pa))


def calibrate_decision(p_mkt: float, a: float = DEC_A, b: float = DEC_B) -> float:
    return _ilg(a + b * _lg(p_mkt))


def rake(table: list[float], p_a: float | None, p_dec: float | None,
         iters: int = 40) -> list[float]:
    """IPF a six-cell (A_KO, A_SUB, A_DEC, B_KO, B_SUB, B_DEC) table so that
    A's row sums to `p_a` and the DEC column to `p_dec`, keeping every other
    ratio the table already has. Either target may be None (left free)."""
    t = [max(1e-9, x) for x in table]
    s = sum(t)
    t = [x / s for x in t]
    for _ in range(iters):
        if p_a is not None:
            ra, rb = sum(t[:3]), sum(t[3:])
            t = [x * p_a / ra for x in t[:3]] + [x * (1 - p_a) / rb for x in t[3:]]
        if p_dec is not None:
            d = t[2] + t[5]
            f = 1 - d
            t = [t[0] * (1 - p_dec) / f, t[1] * (1 - p_dec) / f, t[2] * p_dec / d,
                 t[3] * (1 - p_dec) / f, t[4] * (1 - p_dec) / f, t[5] * p_dec / d]
    return t


#: Fallback method split when a fight has no method market: a winner's share
#: by KO / SUB / DEC, UFCStats 2013-2026 by (division, women), from
#: scripts/mma_fit.py --what market. Strawweight women go the distance 65.6%
#: of the time, heavyweights 37.2% -- a fallback that ignored division would
#: be wrong by more than the market's own decision bias.
FALLBACK_SPLIT = {
    (115, True): (0.144, 0.200, 0.656), (125, False): (0.241, 0.215, 0.544),
    (125, True): (0.166, 0.202, 0.632), (135, False): (0.273, 0.198, 0.530),
    (135, True): (0.222, 0.173, 0.605), (145, False): (0.311, 0.166, 0.523),
    (155, False): (0.332, 0.194, 0.474), (170, False): (0.344, 0.161, 0.495),
    (185, False): (0.391, 0.184, 0.426), (205, False): (0.475, 0.166, 0.359),
    (265, False): (0.504, 0.124, 0.372),
    0: (0.327, 0.179, 0.494),
}


def outcome_table(ml=None, method=None, lbs: int = 0, women: bool = False,
                  sched_rounds: int = 3, salary_diff: float | None = None) -> dict:
    """{cells: {A_KO: p, ...}, p_draw, source} for one fight.

    ml     = (dec_a, dec_b) moneyline decimals, or None
    method = (A_KO, A_SUB, A_DEC, B_KO, B_SUB, B_DEC) decimals, or None

    `source` says which prices it rests on, so the app can show a fighter with
    no market as exactly that rather than as a confident number.
    """
    p_a = win_prob(*ml) if ml and all(ml) else None
    if method and all(method):
        q = devig_power(method)
        p_dec = calibrate_decision(q[2] + q[5])
        t = rake(q, p_a, p_dec)
        source = "moneyline+method" if p_a is not None else "method"
    else:
        ko, sub, dec = split_for(lbs, women, sched_rounds)
        if p_a is not None:
            pa, source = p_a, "moneyline"
        elif salary_diff is not None:
            pa, source = _ilg(SALARY_LOGIT_PER_K * salary_diff / 1000.0), "salary"
        else:
            pa, source = 0.5, "none"
        t = [pa * ko, pa * sub, pa * dec, (1 - pa) * ko, (1 - pa) * sub,
             (1 - pa) * dec]
    t = [x * (1 - P_DRAW) for x in t]
    return {"cells": dict(zip(CELLS, t)), "p_draw": P_DRAW, "source": source}


def split_for(lbs: int, women: bool, sched_rounds: int) -> tuple:
    key = (lbs, women, sched_rounds)
    for k in (key, (lbs, women), 0):
        if k in FALLBACK_SPLIT:
            return FALLBACK_SPLIT[k]
    return FALLBACK_SPLIT[0]


# ---------------------------------------------------------------------------
# Parsing DraftKings Sportsbook
# ---------------------------------------------------------------------------
def _dec(sel: dict) -> float | None:
    try:
        d = float(sel.get("trueOdds"))
        return d if d > 1.0 else None
    except (TypeError, ValueError):
        return None


def _ou(line: float | None, over: float | None, under: float | None) -> dict | None:
    if line is None or not over or not under:
        return None
    p_over, _ = devig_power([over, under])
    return {"line": line, "p_over": p_over, "over": over, "under": under}


def parse_book(bundle: dict) -> dict:
    """{event_id: fight markets} from a saved DK league bundle.

    `bundle` is {"league": <league payload>, "subcategories": {"cid/sid":
    {"name", "payload"}}} -- what scripts/mma_odds_capture.py writes. Every
    market is matched to its fighter by the selection's participant name or
    the "Home"/"Away" outcome type, never by position in a list.
    """
    events = {}
    for e in (bundle.get("league") or {}).get("events") or []:
        names = [n.strip() for n in re.split(r"\s+vs\.?\s+", e.get("name") or "")]
        if len(names) != 2:
            continue
        events[str(e["id"])] = {
            "event_id": str(e["id"]), "name": e.get("name"), "a": names[0],
            "b": names[1], "start": e.get("startEventDate"),
            "ml": None, "method": [None] * 6, "rounds": {}, "distance": None,
            "sched_rounds": 3, "total_rounds": None, "sig": {}, "td": {},
        }
    for sub in (bundle.get("subcategories") or {}).values():
        p = sub.get("payload") or {}
        sels: dict = {}
        for s in p.get("selections") or []:
            sels.setdefault(s.get("marketId"), []).append(s)
        for m in p.get("markets") or []:
            ev = events.get(str(m.get("eventId")))
            if ev is None:
                continue
            _ingest_market(ev, m, sels.get(m.get("id"), []))
    for ev in events.values():
        if any(k[2] >= 4 for k in ev["rounds"]):
            ev["sched_rounds"] = 5
        if ev["total_rounds"] and ev["total_rounds"]["line"] >= 3.5:
            ev["sched_rounds"] = 5
    return events


def _side(ev: dict, sel: dict) -> int | None:
    """0 for the event's first-named fighter, 1 for the second."""
    ot = (sel.get("outcomeType") or "").lower()
    if ot == "home":
        return 0
    if ot == "away":
        return 1
    parts = [norm(x.get("name", "")) for x in sel.get("participants") or []]
    for i, who in enumerate((ev["a"], ev["b"])):
        if norm(who) in parts:
            return i
    return None


def _ingest_market(ev: dict, m: dict, sels: list) -> None:
    mtype = ((m.get("marketType") or {}).get("name") or m.get("name") or "").strip()
    name = (m.get("name") or "").strip()
    low = mtype.lower()
    if low == "moneyline":
        pr = [None, None]
        for s in sels:
            i = _side(ev, s)
            if i is not None:
                pr[i] = _dec(s)
        if all(pr):
            ev["ml"] = tuple(pr)
    elif low in ("ko/tko/dq", "submission", "decision") and len(sels) == 2:
        col = {"ko/tko/dq": 0, "submission": 1, "decision": 2}[low]
        for s in sels:
            i = _side(ev, s)
            if i is not None:
                ev["method"][3 * i + col] = _dec(s)
    elif low == "round and method betting":
        for s in sels:
            label = s.get("label") or ""
            if label.lower().startswith("to go the distance"):
                ev["rounds"][("DIST", None, 0)] = _dec(s)
                continue
            mm = re.search(r"by (KO/TKO/DQ|Submission) in Round (\d)", label)
            i = _side(ev, s)
            if mm and i is not None:
                meth = "KO" if mm.group(1).startswith("KO") else "SUB"
                ev["rounds"][(i, meth, int(mm.group(2)))] = _dec(s)
    elif low == "fight to go the distance":
        yes = next((s for s in sels if (s.get("label") or "").lower() == "yes"), None)
        no = next((s for s in sels if (s.get("label") or "").lower() == "no"), None)
        if yes and no and _dec(yes) and _dec(no):
            ev["distance"] = devig_power([_dec(yes), _dec(no)])[0]
    elif low == "total rounds":
        over = next((s for s in sels if s.get("outcomeType") == "Over"), None)
        under = next((s for s in sels if s.get("outcomeType") == "Under"), None)
        if over and under:
            ev["total_rounds"] = _ou(over.get("points"), _dec(over), _dec(under))
    elif "total significant strikes o/u" in low or "total takedowns landed o/u" in low:
        key = "sig" if "significant" in low else "td"
        who = name.split(" Total ")[0].strip()
        i = 0 if norm(who) == norm(ev["a"]) else 1 if norm(who) == norm(ev["b"]) else None
        if i is None:
            i = 0 if "[team1]" in low else 1 if "[team2]" in low else None
        over = next((s for s in sels if s.get("outcomeType") == "Over"), None)
        under = next((s for s in sels if s.get("outcomeType") == "Under"), None)
        line = None
        if over is not None:
            mm = re.search(r"([\d.]+)\s*$", over.get("label") or "")
            line = float(over.get("points") or (mm.group(1) if mm else 0) or 0) or None
        if i is not None and over and under:
            ev[key][i] = _ou(line, _dec(over), _dec(under))


def fight_markets_table(ev: dict, lbs: int = 0, women: bool = False) -> dict:
    """outcome_table() for one parsed DK event."""
    method = tuple(ev["method"]) if all(ev["method"]) else None
    return outcome_table(ev.get("ml"), method, lbs, women, ev.get("sched_rounds", 3))


def round_shape(ev: dict, side: int, meth: str, sched: int) -> list[float] | None:
    """The market's own P(round r | this fighter finishes by this method),
    from the Round-and-Method prices, power-devigged within the cell. Used as
    a DISPLAY cross-check against the fitted hazard model -- not as an input,
    because nothing here has measured whether those prices are calibrated."""
    prices = [ev["rounds"].get((side, meth, r)) for r in range(1, sched + 1)]
    if not all(prices):
        return None
    q = [1.0 / p for p in prices]
    s = sum(q)
    return [x / s for x in q]
