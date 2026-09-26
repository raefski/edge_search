"""DK MMA Classic scoring + free ground truth (UFCStats, via a public mirror).

WHAT THE CONTEST IS, READ OFF DRAFTKINGS RATHER THAN AN ARTICLE
api.draftkings.com/lineups/v1/gametypes/168/rules, 2026-09-26:

    roster       F F F F F F  -- six fighters, no positions
    cap          $50,000
    late swap    NOT ALLOWED (allowLateSwap=false). Lock is the first bell of
                 the first fight on the slate, and the main event -- which is
                 where most of a lineup's points are -- runs three to four
                 hours after it.
    lobby code   "MMA"
    game types   168 Classic, 169 Captain Mode (1.5x CPT slot, a SEPARATE
                 late-card slate), 373 Snake (no cap, not built here)

WHY THIS SPORT GETS A SIMULATOR, LIKE NASCAR, AND NOT A CORRELATION MATRIX
A fight is a zero-sum event with exactly one winner, and the win bonus is most
of a DK MMA score: 90 for a first-round win against ~0 for the loser. The two
fighters in a bout are not "negatively correlated" in the sense a matrix can
express -- their outcomes are one draw from one distribution over
(winner, method, round). And the stats that make up the rest of the score are
accumulated over the fight's DURATION, which that same draw decides. A
first-round knockout ends the strike count for both men at once. Sampling
whole fights enforces all of that exactly; see edge/mma_sim.py.

WHERE THE PRICES COME FROM
DraftKings Sportsbook prices the whole outcome distribution directly --
"Round and Method of Victory" is winner x {KO/TKO, Submission, Decision} x
round, one market per fight -- plus significant-strike and takedown O/U lines
for every fighter on a full card. Unlike college (one-sided ladders) or NASCAR
(no market at all), the quantity DK's win bonus pays on is priced exactly.
See edge/mma_market.py.

GROUND TRUTH
UFCStats is behind a JavaScript proof-of-work gate. Greco1899/scrape_ufc_stats
on GitHub republishes it as six CSVs, refreshed daily (last push 2026-09-25 at
build time), with per-ROUND stats for every UFC fight since 1994 -- 8,924
fights, 41,958 fighter-rounds. That is what this module reads, and it is what
every constant in this build was fitted on.
"""
from __future__ import annotations

import csv
import collections
import datetime as _dt
import logging
import re
import time
import urllib.request
from pathlib import Path

from edge.names import norm

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("edge.mma")

# ---------------------------------------------------------------------------
# The contest
# ---------------------------------------------------------------------------
DK_SPORT = "MMA"
CLASSIC_GAME_TYPE = 168
CAPTAIN_GAME_TYPE = 169
SALARY_CAP = 50000
ROSTER_SIZE = 6
CAPTAIN_MULT = 1.5
ALLOW_LATE_SWAP = False
#: DK's "Fantasy Points per Fight" (glossary term FPPF) in draftStatAttributes.
#: A different id from every other sport here -- 408 MLB/NFL, 174 CFB, 653
#: NASCAR -- and asking for the wrong one returns None, not an error.
FPPF_STAT_ID = 635

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
#: DK MMA Classic, current since January 2021 (RotoWire and FantasyLabs both
#: dated the change 2021-01-13/15; Establish The Run prints the same table).
#:
#: "Strikes" is EVERY landed strike and "Significant Strikes" is a second award
#: on the significant ones, so a significant strike is worth 0.4 and any other
#: landed strike 0.2. That reading is not assumed: scripts/mma_fit.py
#: --scoring recomputes each fighter's FPPF from UFCStats and compares it with
#: DraftKings' own published number, and the alternatives lose.
PTS_STRIKE = 0.2          # per landed strike, significant or not
PTS_SIG_STRIKE = 0.2      # additional, per landed significant strike
PTS_CTRL_SEC = 0.03       # per second of control time (1.8 per minute)
PTS_TAKEDOWN = 5.0
PTS_REVERSAL = 5.0
PTS_KNOCKDOWN = 10.0

#: Win bonus by the round a finish came in. A decision pays 30 whether the fight
#: was scheduled for three rounds or five, so a five-round decision is worth
#: LESS than a third-round finish and a first-round finish is worth three
#: decisions.
WIN_BONUS = {1: 90.0, 2: 70.0, 3: 45.0, 4: 40.0, 5: 40.0}
DECISION_BONUS = 30.0
#: Awarded on top of the round-1 bonus for a win inside the first 60 seconds.
QUICK_WIN_BONUS = 25.0
QUICK_WIN_SECONDS = 60

ROUND_SECONDS = 300


def stat_points(strikes: float, sig: float, ctrl_sec: float, td: float,
                rev: float, kd: float) -> float:
    """DK points from the in-fight stats alone, before any win bonus."""
    return (PTS_STRIKE * strikes + PTS_SIG_STRIKE * sig
            + PTS_CTRL_SEC * ctrl_sec + PTS_TAKEDOWN * td
            + PTS_REVERSAL * rev + PTS_KNOCKDOWN * kd)


def win_bonus(won: bool, method: str, rnd: int, sec_in_round: float) -> float:
    """DK's fight-conclusion bonus for one fighter.

    `method` is the normalised one from classify_method: a decision pays the
    flat decision bonus, any other WIN pays by round (DK pays a DQ or a
    doctor's stoppage the same as a knockout in that round -- it is a win in
    that round). A draw or no-contest pays nobody.
    """
    if not won:
        return 0.0
    if method == "DEC":
        return DECISION_BONUS
    bonus = WIN_BONUS.get(int(rnd), WIN_BONUS[5])
    if int(rnd) == 1 and sec_in_round <= QUICK_WIN_SECONDS:
        bonus += QUICK_WIN_BONUS
    return bonus


def dk_points(s: dict, won: bool, method: str, rnd: int,
              sec_in_round: float) -> float:
    return round(stat_points(s["strikes"], s["sig"], s["ctrl"], s["td"],
                             s["rev"], s["kd"])
                 + win_bonus(won, method, rnd, sec_in_round), 2)


def classify_method(raw: str) -> str:
    """UFCStats' METHOD string -> KO | SUB | DEC | NC.

    KO carries every non-decision stoppage that is not a submission -- a
    doctor's stoppage is scored KO/TKO by the commission, and a DQ or "could not
    continue" is paid by DK as a win in that round, which is all this label is
    used for. "Overturned" is a no-contest after the fact.
    """
    m = (raw or "").strip().lower()
    if m.startswith("decision"):
        return "DEC"
    if m.startswith("submission"):
        return "SUB"
    if m.startswith("overturned") or m == "":
        return "NC"
    return "KO"


# ---------------------------------------------------------------------------
# Ground truth: the UFCStats mirror
# ---------------------------------------------------------------------------
MIRROR = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/{name}.csv"
CACHE_DIR = ROOT / "data" / "mma_cache"
MIRROR_FILES = ("ufc_event_details", "ufc_fight_results", "ufc_fight_stats",
                "ufc_fighter_tott")
#: The mirror refreshes once a day, around 18:00 UTC.
MAX_AGE_SECONDS = 20 * 3600

_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/140.0.0.0 Safari/537.36")}


def refresh_mirror(force: bool = False, max_age: float = MAX_AGE_SECONDS) -> dict:
    """Download any mirror CSV older than `max_age`. {name: "fresh"|"cached"|error}.

    A failed download keeps the file already on disk -- a day-old fight table
    is missing at most one card, and every fighter on an upcoming card fought
    before that.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in MIRROR_FILES:
        path = CACHE_DIR / f"{name}.csv"
        if (not force and path.exists()
                and time.time() - path.stat().st_mtime < max_age):
            out[name] = "cached"
            continue
        try:
            req = urllib.request.Request(MIRROR.format(name=name), headers=_UA)
            body = urllib.request.urlopen(req, timeout=60).read()
            if len(body) < 1000:
                raise ValueError(f"only {len(body)} bytes")
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
            out[name] = "fresh"
        except Exception as exc:                            # noqa: BLE001
            out[name] = f"failed: {exc}"
            log.warning("mma: mirror %s refresh failed (%s)", name, exc)
    return out


def ensure_fresh() -> dict:
    """Refresh any mirror file older than MAX_AGE_SECONDS and, if anything new
    arrived, drop the parsed fight table so the next fights() re-reads it.

    fights() alone never refreshes a cache that already exists -- which would
    have built next Saturday's card on style indices that do not know about
    this Saturday's. Every slate build calls this first.
    """
    status = refresh_mirror()
    global _FIGHTS
    if any(v == "fresh" for v in status.values()):
        _FIGHTS = None
    return status


def _of(v: str) -> tuple[int, int]:
    """'13 of 27' -> (13, 27)."""
    m = re.match(r"\s*(\d+)\s+of\s+(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _mmss(v: str) -> int:
    m = re.match(r"\s*(\d+):(\d+)", v or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def _rounds_in_format(fmt: str) -> int:
    """'3 Rnd (5-5-5)' -> 3; '5 Rnd (5-5-5-5-5)' -> 5. Anything else -> 0
    (the early tournament formats, which this build never uses)."""
    m = re.match(r"\s*(\d)\s+Rnd\s+\((5-)*5\)\s*$", fmt or "")
    return int(m.group(1)) if m else 0


def _date(s: str) -> _dt.date | None:
    try:
        return _dt.datetime.strptime(s.strip(), "%B %d, %Y").date()
    except ValueError:
        return None


#: Weight class -> the division limit in pounds, for the name collision below
#: and as a model input (heavier divisions finish more).
WEIGHT_LBS = {
    "strawweight": 115, "flyweight": 125, "bantamweight": 135,
    "featherweight": 145, "lightweight": 155, "welterweight": 170,
    "middleweight": 185, "light heavyweight": 205, "heavyweight": 265,
    "catch weight": 0, "open weight": 0,
}


def weight_of(weightclass: str) -> tuple[int, bool]:
    """('UFC Women's Flyweight Title Bout') -> (125, True). lbs 0 = unknown."""
    wc = (weightclass or "").lower()
    women = "women" in wc
    for name in sorted(WEIGHT_LBS, key=len, reverse=True):
        if name in wc:
            return WEIGHT_LBS[name], women
    return 0, women


_FIGHTS: list | None = None


def fights(refresh: bool = False) -> list[dict]:
    """Every UFC fight with a usable format, oldest first, both corners scored.

    One dict per fight:
        date, event, bout, lbs, women, title, sched_rounds,
        method (KO|SUB|DEC|NC), rnd, sec_in_round, elapsed (seconds),
        draw (bool), nc (bool),
        f: [corner A, corner B], each
            name, key, won, strikes, sig, sig_att, ctrl, td, td_att, rev, kd,
            sub_att, dk, dk_stats, dk_bonus, rounds: [per-round stat dicts]

    Fights whose round-by-round stats are missing, or whose format is not
    3x5 or 5x5, are dropped: DK's bonus table is written for those two formats
    and nothing on a modern card uses another.

    DEDUPLICATED ON THE FIGHT URL. UFCStats renamed two events after the fact
    ("UFC Fight Night: Grasso vs. Shevchenko 2" -> "Noche UFC: ...", and
    "Lopes vs. Silva" likewise) and the mirror carries BOTH names, the stale
    one with no row in the events file and so no date. Counted twice, Raul
    Rosas Jr.'s 132.9-point knockout of Terrence Mitchell moved his career
    average 4.4 points off the FPPF DraftKings publishes. The dated copy wins.
    """
    global _FIGHTS
    if _FIGHTS is not None and not refresh:
        return _FIGHTS
    if refresh or not (CACHE_DIR / "ufc_fight_results.csv").exists():
        refresh_mirror(force=refresh)

    events = {}
    for r in csv.DictReader(open(CACHE_DIR / "ufc_event_details.csv",
                                 encoding="utf-8")):
        events[r["EVENT"].strip()] = _date(r["DATE"])

    per_round: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in csv.DictReader(open(CACHE_DIR / "ufc_fight_stats.csv",
                                 encoding="utf-8")):
        if not r["ROUND"].startswith("Round"):
            continue
        sig, sig_att = _of(r["SIG.STR."])
        tot, _tot_att = _of(r["TOTAL STR."])
        td, td_att = _of(r["TD"])
        per_round[(r["EVENT"].strip(), r["BOUT"].strip())][r["FIGHTER"].strip()].append({
            "rnd": int(r["ROUND"].split()[-1]),
            "kd": _num(r["KD"]), "sig": sig, "sig_att": sig_att,
            "strikes": tot, "td": td, "td_att": td_att,
            "sub_att": _num(r["SUB.ATT"]), "rev": _num(r["REV."]),
            "ctrl": _mmss(r["CTRL"]),
            "head": _of(r["HEAD"])[0], "body": _of(r["BODY"])[0],
            "leg": _of(r["LEG"])[0], "distance": _of(r["DISTANCE"])[0],
            "clinch": _of(r["CLINCH"])[0], "ground": _of(r["GROUND"])[0],
        })

    out = []
    for r in csv.DictReader(open(CACHE_DIR / "ufc_fight_results.csv",
                                 encoding="utf-8")):
        event, bout = r["EVENT"].strip(), r["BOUT"].strip()
        sched = _rounds_in_format(r["TIME FORMAT"])
        if sched not in (3, 5):
            continue
        names = [n.strip() for n in bout.split(" vs. ")]
        if len(names) != 2:
            continue
        stats = per_round.get((event, bout))
        if not stats or any(n not in stats for n in names):
            continue
        outcome = (r["OUTCOME"] or "").strip()
        method = classify_method(r["METHOD"])
        nc = outcome.startswith("NC") or method == "NC"
        draw = outcome.startswith("D")
        rnd = int(_num(r["ROUND"])) or 1
        sec = _mmss(r["TIME"])
        lbs, women = weight_of(r["WEIGHTCLASS"])
        f = {"date": events.get(event), "event": event, "bout": bout,
             "url": r.get("URL", ""), "lbs": lbs, "women": women,
             "weightclass": r["WEIGHTCLASS"].strip(),
             "title": "title" in r["WEIGHTCLASS"].lower(),
             "sched_rounds": sched, "method": method, "rnd": rnd,
             "sec_in_round": sec, "elapsed": (rnd - 1) * ROUND_SECONDS + sec,
             "draw": draw, "nc": nc, "f": []}
        for i, name in enumerate(names):
            rounds = sorted(stats[name], key=lambda x: x["rnd"])
            tot = {k: sum(x[k] for x in rounds)
                   for k in ("kd", "sig", "sig_att", "strikes", "td", "td_att",
                             "sub_att", "rev", "ctrl", "head", "body", "leg",
                             "distance", "clinch", "ground")}
            won = (not nc and not draw and len(outcome) >= 3
                   and outcome.split("/")[i] == "W")
            bonus = win_bonus(won, method, rnd, sec)
            pts = stat_points(tot["strikes"], tot["sig"], tot["ctrl"],
                              tot["td"], tot["rev"], tot["kd"])
            f["f"].append({"name": name, "key": fighter_key(name, lbs),
                           "won": won, **tot, "rounds": rounds,
                           "dk_stats": round(pts, 2), "dk_bonus": bonus,
                           "dk": round(pts + bonus, 2)})
        out.append(f)
    by_url: dict = {}
    for f in out:
        key = f["url"] or (f["event"], f["bout"])
        if key not in by_url or (by_url[key]["date"] is None and f["date"] is not None):
            by_url[key] = f
    # A fight with no event date at all is not a UFC-card fight DraftKings
    # would have scored (the remainder are Road to UFC bouts).
    out = [f for f in by_url.values() if f["date"] is not None]
    out.sort(key=lambda x: (x["date"], x["event"]))
    _FIGHTS = out
    return out


#: Names shared by two different UFC fighters (ufc_fighter_details, 2026-09-26:
#: nine of them, two pairs active -- Bruno Silva at 125 and 185, Jean Silva).
#: Keyed with the weight class so their careers do not merge. Anything else is
#: keyed by the normalised name alone, which is what a DK name joins against.
COLLIDING = {"mikedavis", "anthonyfigueroa", "lancegibson", "joeygomez",
             "tonyjohnson", "michaelmcdonald", "jeansilva", "brunosilva",
             "victorvalenzuela"}


def fighter_key(name: str, lbs: int = 0) -> str:
    k = norm(name)
    if k in COLLIDING and lbs:
        # Adjacent divisions are the same fighter moving; the two Bruno Silvas
        # are 60 lbs apart. Bucket to a light/heavy half.
        return f"{k}@{'L' if lbs <= 155 else 'H'}"
    return k


def fighter_history(before: _dt.date | None = None) -> dict:
    """{fighter key: [appearance dicts, oldest first]} from fights before `before`.

    An appearance is the fighter's own corner plus the fight's context and the
    opponent's corner -- everything the rate model needs, with nothing from the
    fight being predicted.
    """
    hist: dict = collections.defaultdict(list)
    for f in fights():
        if before is not None and (f["date"] is None or f["date"] >= before):
            continue
        for i, me in enumerate(f["f"]):
            opp = f["f"][1 - i]
            hist[me["key"]].append({
                "date": f["date"], "event": f["event"], "lbs": f["lbs"],
                "women": f["women"], "sched_rounds": f["sched_rounds"],
                "method": f["method"], "rnd": f["rnd"],
                "sec_in_round": f["sec_in_round"], "elapsed": f["elapsed"],
                "nc": f["nc"], "draw": f["draw"], "me": me, "opp": opp,
            })
    return hist


# ---------------------------------------------------------------------------
# Historical prices: the backtest set
# ---------------------------------------------------------------------------
#: shortlikeafox/ultimate_ufc_dataset (the Kaggle "Ultimate UFC Dataset"):
#: moneylines for every UFC fight 2010-03 -> 2026-03, and per-fighter KO / SUB /
#: DEC prices for ~5,400 of them from 2012 on. The only public source found of
#: METHOD odds at scale, and the reason this build could be backtested end to
#: end where NCAAF and NASCAR could not.
HIST_ODDS_URL = ("https://raw.githubusercontent.com/shortlikeafox/"
                 "ultimate_ufc_dataset/main/ufc-master.csv")


def _american(v) -> float | None:
    try:
        a = float(str(v).strip())
    except ValueError:
        return None
    if a == 0:
        return None
    return 1 + (a / 100.0 if a > 0 else 100.0 / -a)


def priced_fights(refresh: bool = False) -> list[tuple[dict, dict]]:
    """[(fight, odds)] for every historical fight with prices that joins to
    UFCStats, odds oriented to the fight's own corner order:

        odds = {"ml": (a, b), "method": (A_KO, A_SUB, A_DEC, B_KO, B_SUB, B_DEC)}

    decimals; a missing price is None. Joined on the date (+/- a day, for
    cards that cross midnight UTC) and the unordered pair of normalised names:
    6,821 of 7,177 rows join on the 2026-03-28 file.
    """
    path = CACHE_DIR / "ufc-master.csv"
    if refresh or not path.exists():
        req = urllib.request.Request(HIST_ODDS_URL, headers=_UA)
        path.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    idx = {}
    for f in fights():
        if f["date"] is None:
            continue
        pair = frozenset(norm(p["name"]) for p in f["f"])
        for d in (-1, 0, 1):
            idx[(f["date"] + _dt.timedelta(days=d), pair)] = f
    out = []
    for x in csv.DictReader(open(path, encoding="utf-8")):
        try:
            day = _dt.date.fromisoformat(x["date"])
        except ValueError:
            continue
        r, b = norm(x["R_fighter"]), norm(x["B_fighter"])
        f = idx.get((day, frozenset((r, b))))
        if f is None:
            continue
        flip = norm(f["f"][0]["name"]) != r

        def two(ca, cb):
            t = (_american(x.get(ca)), _american(x.get(cb)))
            return (t[1], t[0]) if flip else t

        ml = two("R_odds", "B_odds")
        ko, sub, dec = (two("r_ko_odds", "b_ko_odds"), two("r_sub_odds", "b_sub_odds"),
                        two("r_dec_odds", "b_dec_odds"))
        out.append((f, {"ml": ml, "method": (ko[0], sub[0], dec[0],
                                             ko[1], sub[1], dec[1])}))
    return out


# ---------------------------------------------------------------------------
# Names: three sources, three spellings
# ---------------------------------------------------------------------------
#: Measured on the 2026-09-26 card, where DraftKings' OWN two products disagree:
#:     DK DFS                 DK Sportsbook              UFCStats
#:     Heili Alatengheili     Alateng Heili              Alateng Heili
#:     Mahammadali Osmanli    Mehemmedeli Osmanli        (debut)
#:     Ilimbek Akylbek        Ilimbek Akylbek Uulu       (debut)
#: so a join on the normalised name alone silently drops fighters, and the
#: one that drops is exactly the one nobody notices is missing.
import difflib as _difflib
import unicodedata as _ud

_JUNK = {"jr", "sr", "ii", "iii", "iv", "uulu", "de", "da", "dos", "do"}


def name_tokens(name: str) -> list[str]:
    s = _ud.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    toks = re.findall(r"[a-z]+", s)
    return [t for t in toks if t not in _JUNK] or toks


def name_similarity(a: str, b: str) -> float:
    """0..1. The max of three views, because each spelling failure above
    defeats a different one:
      * whole-string ratio              (Mahammadali / Mehemmedeli)
      * token containment, both ways    (Alateng Heili inside Heili Alatengheili)
      * sorted-token ratio              (given/family name order swapped)
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    ja, jb = "".join(ta), "".join(tb)
    r1 = _difflib.SequenceMatcher(None, ja, jb).ratio()
    r2 = _difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()

    def contain(xs, hay):
        return sum(len(x) for x in xs if x in hay) / max(1, sum(len(x) for x in xs))

    r3 = min(contain(ta, jb), contain(tb, ja)) if len(ta) > 1 and len(tb) > 1 else 0.0
    r3 = max(r3, 0.97 * max(contain(ta, jb), contain(tb, ja))
             if (ta[-1] == tb[-1] or ta[-1] in jb or tb[-1] in ja) else 0.0)
    return max(r1, r2, r3)


def match_fighter(names: list[str], hist: dict, lbs: int = 0,
                  threshold: float = 0.86) -> tuple[str | None, float]:
    """(history key, score) for a fighter known by any of `names`.

    Exact normalised-name hits first (with the weight-class split for the nine
    colliding names), then the best fuzzy hit above `threshold`. None means a
    UFC debut -- or a spelling nobody has seen, which is why the app lists
    every unmatched fighter rather than quietly treating him as a debutant.
    """
    for nm in names:
        k = fighter_key(nm, lbs)
        if k in hist:
            return k, 1.0
        k0 = norm(nm)
        for suffix in ("@L", "@H"):
            if k0 + suffix in hist:
                return k0 + suffix, 1.0
    best, best_s = None, 0.0
    for k, apps in hist.items():
        cand = apps[-1]["me"]["name"]
        s = max(name_similarity(nm, cand) for nm in names)
        if s > best_s:
            best, best_s = k, s
    return (best, best_s) if best_s >= threshold else (None, best_s)
