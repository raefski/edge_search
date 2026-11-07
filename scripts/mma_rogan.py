#!/usr/bin/env python3
"""Joe Rogan as an MMA source: collect what he said BEFORE a fight, and test
whether any of it beats the de-biased market (MMA_STATUS.md §4, §13).

    python3 scripts/mma_rogan.py episodes            # RSS + YouTube -> data/mma_rogan/episodes.json
    python3 scripts/mma_rogan.py fetch [--limit N]   # YouTube captions (cached, ~3 s apart)
    python3 scripts/mma_rogan.py extract             # -> data/mma_rogan/claims.csv
    python3 scripts/mma_rogan.py sample -n 120       # random claims to label by eye
    python3 scripts/mma_rogan.py grade               # held-out report

WHY ROGAN, AND WHY THE PRIOR IS "NO EDGE". He calls the fights, so he has seen
almost everyone on a card, and fighters and coaches sit in his studio. The
hypothesis is that he sometimes says what the line has not priced (an injury,
a bad cut, a camp change, a chin that is gone). This repo's record says touts
are coin flips against the close (GPP_META.md §C2) and fading the crowd is
null (§G), so nothing here ships unless it beats the market on fights after
the cutoff.

THE TRAP IS TIMING. Fight Companion is recorded live while the card airs and
the commentary is live, so most of what he says about a fight is said after
it started. Every claim carries `public_at`, the moment the episode became
public, and is used only for fights on a LATER UTC calendar day:
  * public_at = the LATER of the RSS pubDate and YouTube's publish time (a
    live stream: when it started). Old RSS items carry a date only (00:00
    UTC); taking the later of the two never lets a claim count early.
  * a fight's time is only known to the day, so "later UTC day" is the
    conservative cut: a card in Auckland or Abu Dhabi starts on its own date
    in UTC, after anything published the day before.
  * claims about a fighter's PAST fight (Fight Companion, in-fight remarks)
    are kept as evidence about his NEXT fight only (`kind` = next).

SOURCES (measured 2026-11-07): the JRE RSS feed (megaphone GLT1412515089) has
the whole back catalogue, 2,778 episodes from 2009 with pubDates, audio only.
The PowerfulJRE YouTube channel has 185 of the 187 JRE MMA Shows, 62 Fight
Companions and most numbered episodes, with captions through get_panel (the
transcripts package). Captions are auto-generated: names come out mangled
("Lookboonmee" -> "look boo me"), which costs recall, never timing.

EXTRACTION IS A LEXICON, NOT A MODEL: no API key on this machine, and a rule
whose misses can be counted is worth more than one that cannot. `sample`
prints claims with their quote for labelling; the error rate is reported
with every grade.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import math
import random
import re
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "transcripts"))
from edge import mma  # noqa: E402
from edge import mma_market as mk  # noqa: E402
from edge.names import norm  # noqa: E402
from transcripts import Cache, YouTube  # noqa: E402
from transcripts._http import Http  # noqa: E402

OUT = ROOT / "data" / "mma_rogan"
EPISODES = OUT / "episodes.json"
CLAIMS = OUT / "claims.csv"
CACHE = ROOT / "data" / "cache" / "buzz"
RSS = "https://feeds.megaphone.fm/GLT1412515089"
POWERFULJRE = "UCzQUP1qoWDoEbmsQxvdjxgQ"

#: Train/test split, chosen 2026-11-07 BEFORE any claim was graded: fit on
#: fights before it, report on fights from it on (the date the brief named).
CUTOFF = dt.date(2023, 1, 1)
#: A pre-fight claim counts for fights up to this many days after it aired.
#: Bookings change; past a month a "preview" is mostly about a fight that will
#: be rebooked. 0..30 is what is graded; the table keeps the gap for any cut.
HORIZON_DAYS = 30
#: Next-fight evidence (said about a fight that already happened) is graded
#: against the fighter's next fight, which is usually months away.
NEXT_HORIZON_DAYS = 400
#: YouTube more than this many days after the RSS date is a re-upload (public_at).
REUPLOAD_DAYS = 2

#: Guests who are not in UFCStats under the title's spelling but talk fights:
#: coaches, referees, analysts. A regular JRE episode is collected when its
#: guest is a UFC fighter OR one of these.
MMA_PEOPLE = (
    "eddie bravo", "firas zahabi", "john danaher", "joe schilling", "greg jackson",
    "mike winkeljohn", "trevor wittman", "duane ludwig", "john kavanagh",
    "javier mendez", "big john mccarthy", "john mccarthy", "herb dean",
    "marc goddard", "ariel helwani", "luke thomas", "dana white", "jon anik",
    "joe silva", "teddy atlas", "freddie roach", "kru", "craig jones",
    "gordon ryan", "jeff novitzky", "josh thompson", "radio rahim", "brian kelleher",
)


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------
def _utc(d: dt.datetime) -> dt.datetime:
    return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d.astimezone(dt.timezone.utc)


def rss_items(http: Http) -> list[dict]:
    root = ET.fromstring(http.get(RSS))
    out = []
    for it in root.findall("channel/item"):
        t = (it.findtext("title") or "").strip()
        pub = it.findtext("pubDate")
        if not pub:
            continue
        out.append({"title": t, "rss_pub": _utc(parsedate_to_datetime(pub)).isoformat()})
    return out


def episode_key(title: str) -> str | None:
    """A key RSS and YouTube titles share: mma:92, jre:1450, fc:2024-03-09."""
    m = re.search(r"MMA Show\s*#\s*(\d+)", title, re.I)
    if m:
        return f"mma:{int(m.group(1))}"
    m = re.search(r"Fight Companion\s*[-–—:]?\s*([A-Z][a-z]+\.? \d{1,2},? \d{4})", title)
    if m:
        s = m.group(1).replace(".", "").replace(",", "")
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return "fc:" + dt.datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass
    m = re.search(r"(?:^|Experience\s*)#\s*(\d{2,4})\b", title)
    if m:
        return f"jre:{int(m.group(1))}"
    return None


def classify(title: str, fighter_names: set) -> tuple[str | None, list[str]]:
    """(kind, guests matched) or (None, []) for an episode not about fights."""
    if re.search(r"Fight Companion", title, re.I):
        return "fight_companion", []
    guest = norm(re.sub(r"^.*?#\s*\d+\s*[-–—:]?", "", title))
    hits = sorted({n for n in fighter_names if n in guest}
                  | {p for p in MMA_PEOPLE if norm(p) in guest and len(norm(p)) >= 6})
    if re.search(r"MMA Show", title, re.I):
        return "mma_show", hits
    return ("jre_mma_guest", hits) if hits else (None, [])


def cmd_episodes(args) -> int:
    http = Http(1.0)
    fighter_names = {norm(p["name"]) for f in mma.fights() for p in f["f"]}
    fighter_names = {n for n in fighter_names if len(n) >= 9}
    yt_list = []
    for tab in ("videos", "streams"):
        cached = OUT / f"yt_{tab}.json"
        if cached.exists() and not args.refresh:
            yt_list += json.loads(cached.read_text())
            continue
        vids = YouTube(Cache(CACHE)).channel_videos(POWERFULJRE, tab, limit=6000)
        OUT.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(vids))
        yt_list += vids
    yt = {}
    for v in yt_list:
        k = episode_key(v["title"])
        if k and k not in yt:
            yt[k] = v["id"]
    eps = {}
    for it in rss_items(http):
        kind, guests = classify(it["title"], fighter_names)
        if kind is None:
            continue
        k = episode_key(it["title"]) or "rss:" + norm(it["title"])
        if k in eps:            # parts 1/2 of one Fight Companion share a key
            k += ":" + str(sum(1 for x in eps if x.startswith(k)))
        eps[k] = {"key": k, "kind": kind, "title": it["title"], "guests": guests,
                  "rss_pub": it["rss_pub"], "yt_id": yt.get(k)}
    OUT.mkdir(parents=True, exist_ok=True)
    EPISODES.write_text(json.dumps(sorted(eps.values(), key=lambda e: e["rss_pub"]), indent=0))
    c = collections.Counter((e["kind"], bool(e["yt_id"])) for e in eps.values())
    for (kind, has), n in sorted(c.items()):
        print(f"  {kind:16s} {'youtube' if has else 'audio only':10s} {n}")
    return 0


def load_episodes() -> list[dict]:
    return json.loads(EPISODES.read_text())


def public_at(ep: dict, meta: dict | None) -> dt.datetime:
    """When the episode became public: the RSS pubDate, never earlier.

    YouTube's time is used only when it lands within REUPLOAD_DAYS after the
    RSS date (the same release, timed to the minute). Later than that it is a
    RE-UPLOAD: PowerfulJRE put 61 old episodes back up in 2024 after the
    Spotify exclusive ended, and 15 more in 2013-14. Taking "the later of
    the two" (the first version, 2026-11-07) dated 2021 talk as 2024 and
    matched it to 2024 fights -- leak-free but meaningless, and all of it in
    the test period. A date-only RSS stamp (00:00:00) counts as the END of
    that day."""
    t = dt.datetime.fromisoformat(ep["rss_pub"])
    if (t.hour, t.minute, t.second) == (0, 0, 0):
        t = t.replace(hour=23, minute=59, second=59)
    if meta:
        a = YouTube.aired(meta)
        if a and t < a <= dt.datetime.fromisoformat(ep["rss_pub"]) + dt.timedelta(days=REUPLOAD_DAYS):
            t = a
    return t


def cmd_fetch(args) -> int:
    yt = YouTube(Cache(CACHE))
    eps = [e for e in load_episodes() if e["yt_id"]]
    if args.kind:
        eps = [e for e in eps if e["kind"] in args.kind]
    eps.sort(key=lambda e: (e["kind"] != "mma_show", e["kind"] == "fight_companion", e["rss_pub"]))
    done = 0
    for e in eps[: args.limit or None]:
        cache = Cache(CACHE)
        if cache.get("transcripts", e["yt_id"]) is not None and cache.get("meta", e["yt_id"]):
            continue
        try:
            meta = yt.video(e["yt_id"], e["title"])
            yt.transcript(e["yt_id"], YouTube.aired(meta))
            done += 1
        except Exception as exc:                         # noqa: BLE001 -- keep going, report
            print(f"  {e['key']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            if "Blocked" in type(exc).__name__:
                break
    print(f"fetched {done}")
    return 0


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------
#: Each claim type: (regex on the window around a fighter's name, sign).
#: Sign +1 = good for that fighter, -1 = bad. A window can fire several types.
#: Written 2026-11-07 from the words in the Angela Hill episode (#92) and the
#: brief's list, BEFORE grading; changing them after seeing a grade would be
#: fitting the lexicon to the result.
_NEG = r"(?:gone|bad|terrible|questionable|issue|problem|worr\w*|concern\w*|cracked|shot|fad\w+|declin\w*|slow\w*|not the same|diminish\w*|suspect)"
_POS = r"(?:great|amazing|insane|crazy|incredible|unreal|iron|granite|never (?:gets|got) tired|endless|elite|phenomenal)"
TYPES = {
    "injury": (re.compile(r"\b(injur\w*|torn|tore his|tore her|broke his|broke her|broken (?:hand|foot|rib|leg|arm)|surgery|staph|hurt (?:his|her) (?:knee|back|hand|foot|shoulder|neck)|fighting hurt|not a hundred percent|not 100 percent|sick)\b"), -1),
    "weight_cut": (re.compile(r"\b(weight cut|cutting weight|cut weight|missed weight|miss weight|bad cut|rough cut|drained|dehydrat\w*|making weight)\b"), -1),
    "short_notice": (re.compile(r"\b(short notice|late notice|late replacement|replacement|took (?:the|this) fight on|(?:two|three|one|four|\d+) weeks? notice|days notice)\b"), -1),
    "camp_change": (re.compile(r"\b(new (?:camp|gym|coach|team)|switched (?:camps|gyms|coaches)|left (?:his|her) (?:gym|camp|coach|team)|moved (?:camps|gyms)|training (?:now )?(?:at|with) (?:a )?new)\b"), 0),
    "cardio_neg": (re.compile(r"\b(gas(?:sed|ses|s out| out)|gas tank (?:is )?" + _NEG + r"|cardio (?:is |was )?" + _NEG + r"|" + _NEG + r" cardio|gets tired|got tired|conditioning (?:is |was )?" + _NEG + r")\b"), -1),
    "cardio_pos": (re.compile(r"\b(" + _POS + r" cardio|cardio (?:is |was )?" + _POS + r"|never (?:gets|got) tired|gas tank (?:is )?" + _POS + r"|" + _POS + r" gas tank)\b"), +1),
    "chin_neg": (re.compile(r"\b(chin (?:is |was )?" + _NEG + r"|" + _NEG + r" chin|glass (?:jaw|chin)|been (?:knocked out|ko'?d|stopped|finished) (?:a lot|several|multiple|many|so many)|durability (?:is |was )?" + _NEG + r"|can'?t take (?:a|the) (?:shot|punch))\b"), -1),
    "chin_pos": (re.compile(r"\b(" + _POS + r" chin|chin (?:is |was )?" + _POS + r"|so durable|very durable|incredibly durable|can take a (?:shot|punch)|never been (?:knocked out|stopped|finished|hurt))\b"), +1),
}
#: Picks: who Rogan (or the guest) says wins. Attributed to the name the
#: phrase governs: "<NAME> (is gonna|will|wins|beats|by ko|...)" and
#: "(i got|i like|i'm going with|i pick|money on|bet on) <NAME>".
_PICK_AFTER = re.compile(r"^\W*(?:is (?:gonna|going to) (?:win|beat|smash|finish|knock|stop|submit|destroy|starch|maul|run through)|"
                         r"(?:will|would|should) (?:win|beat|finish|knock|stop|submit|destroy)|wins\b|beats\b|"
                         r"by (?:ko|knockout|tko|submission|decision|stoppage)|takes (?:it|this)|in a (?:landslide|walk)|"
                         r"(?:knocks|knock) (?:him|her) out|(?:stops|finishes|submits|chokes|smashes|outworks|outpoints|outstrikes|beats|wrecks|mauls) (?:him|her)\b|"
                         r"gets the (?:win|finish|stoppage|knockout|w)\b|(?:is|'s) gonna (?:get|have) (?:his|her) hand raised)")
_PICK_BEFORE = re.compile(r"(?:i (?:got|have|like|pick|'m picking|am picking|'m going with|am going with|'d go with|would go with|favou?r|'m taking|'m leaning)|"
                          r"i'?ve got|money(?:'s| is)? on|bet on)\W*$")
#: "if max holloway wins this fight..." is a hypothetical, not a pick.
_HYPOTHETICAL = re.compile(r"\b(?:if|say|whether|unless|when|whoever|who)\W+(?:\w+\W+){0,1}$")
_AGAINST_AFTER = re.compile(r"^\W*(?:is in (?:big )?trouble|is done|is washed|loses|is gonna lose|is going to lose|gets (?:knocked|finished|stopped|submitted|smashed|starched|destroyed)|"
                            r"has no (?:chance|shot)|doesn'?t have a chance|is (?:way )?past (?:his|her) prime)")
_AGAINST_BEFORE = re.compile(r"(?:(?:gonna|going to|will|would|could|can) (?:beat|knock out|ko|stop|finish|submit|smash|destroy|starch|maul|run through)|"
                             r"knocks out|stops|finishes|submits|i (?:don'?t|do not) (?:like|see|think)|bet against|fade)\W*$")

WINDOW = 160        # characters each side of a mention


def _words() -> re.Pattern:
    return re.compile(r"[a-z']+")


def fight_index() -> dict:
    """{date: [fight]} for every UFC fight with a date."""
    by = collections.defaultdict(list)
    for f in mma.fights():
        if f["date"]:
            by[f["date"]].append(f)
    return by


def name_patterns(fighters: list[dict], common: set) -> dict:
    """{regex: [(fighter key, fight id)]}. Full name always; the surname alone
    when it is 5+ letters, not a common caption word (document frequency
    across the corpus, `common`) and unique among the fighters in scope --
    two Silvas on one horizon make "silva" ambiguous, so neither gets it."""
    pats: dict = collections.defaultdict(list)
    last = collections.Counter()
    for fx in fighters:
        toks = mma.name_tokens(fx["name"])
        if toks:
            last[toks[-1]] += 1
    for fx in fighters:
        toks = mma.name_tokens(fx["name"])
        if not toks:
            continue
        pats[r"\b" + r"\s+".join(map(re.escape, toks)) + r"\b"].append(fx)
        ln = toks[-1]
        if len(ln) >= 5 and ln not in common and last[ln] == 1 and len(toks) > 1:
            pats[r"\b" + re.escape(ln) + r"\b"].append(fx)
    return pats


def transcript_text(yt_id: str) -> str | None:
    d = Cache(CACHE).get("transcripts", yt_id)
    if not d or not d.get("segments"):
        return None
    t = " ".join(s[1] for s in d["segments"]).lower()
    t = re.sub(r"\[[^\]]*\]", " ", t)
    return re.sub(r"\s+", " ", t)


def common_words(texts: list[str], share: float = 0.2) -> set:
    """Tokens in more than `share` of episodes: English, not surnames."""
    df = collections.Counter()
    for t in texts:
        df.update(set(re.findall(r"[a-z]+", t)))
    return {w for w, n in df.items() if n > share * len(texts)}


def claims_in(text: str, pats: dict, blockers: re.Pattern | None = None) -> list[dict]:
    """Every (fighter, claim type, sign, quote) in one transcript.

    A name governs the words AFTER it (captions have no punctuation, and
    "<name> got injured / missed weight / gasses" is how it is said) up to
    WINDOW characters, and a shorter stretch before it ("i got <name>").
    Both stop at the nearest OTHER fighter's name -- any UFC fighter, not
    only the ones on upcoming cards: the first sample (2026-11-07) gave
    Frankie Edgar's injury to Max Holloway because Edgar was not booked."""
    out = []
    spans = []
    for pat, fxs in pats.items():
        for m in re.finditer(pat, text):
            spans.append((m.start(), m.end(), fxs))
    spans.sort()
    walls = sorted({(m.start(), m.end()) for m in blockers.finditer(text)}) if blockers else []
    walls = sorted(set(walls) | {(s, e) for s, e, _ in spans})
    import bisect
    starts = [w[0] for w in walls]
    bspans = [(m.start(), m.end()) for m in blockers.finditer(text)] if blockers else []
    for s, e, fxs in spans:
        # "oliveira" inside "charles oliveira" when Vinicius Oliveira is the
        # one booked: a longer, different fighter's name owns those letters
        if any(bs <= s and be >= e and be - bs > e - s for bs, be in bspans) and \
                not any(re.fullmatch(r"\s*".join(map(re.escape, mma.name_tokens(fx["name"]))),
                                     re.sub(r"\s+", "", text[bs:be]).replace(" ", ""))
                        for fx in fxs for bs, be in bspans if bs <= s and be >= e):
            continue
        lo, hi = max(0, s - WINDOW // 2), min(len(text), e + WINDOW)
        i = bisect.bisect_right(starts, e)
        if i < len(walls) and walls[i][0] < hi:
            hi = walls[i][0]
        j = bisect.bisect_left(starts, s) - 1
        while j >= 0 and walls[j][1] > s:          # overlapping spans of this same name
            j -= 1
        if j >= 0 and walls[j][1] > lo:
            lo = walls[j][1]
        before, after = text[lo:s], text[e:hi]
        found = []
        for typ, (rx, sign) in TYPES.items():
            m = rx.search(after) or rx.search(before[-60:])
            if m:
                found.append((typ, sign, m.group(0)))
        hypo = _HYPOTHETICAL.search(before[-25:])
        if not hypo and (_PICK_AFTER.search(after[:60]) or _PICK_BEFORE.search(before[-40:])):
            found.append(("pick_for", +1, ""))
        if not hypo and (_AGAINST_AFTER.search(after[:60]) or _AGAINST_BEFORE.search(before[-40:])):
            found.append(("pick_against", -1, ""))
        found.append(("mention", 0, ""))
        for fx in fxs:
            for typ, sign, hit in found:
                out.append({"fighter": fx["name"], "fkey": fx["key"], "fight": fx["fight"],
                            "type": typ, "sign": sign, "pos": s, "hit": hit,
                            "quote": text[max(0, s - WINDOW):min(len(text), e + WINDOW)]})
    return out


def blocker_regex(common: set) -> re.Pattern:
    """Any UFC fighter's surname (5+ letters, not a common word) or full name."""
    names = set()
    for f in mma.fights():
        for p in f["f"]:
            toks = mma.name_tokens(p["name"])
            if len(toks) > 1:
                names.add(" ".join(toks))
                if len(toks[-1]) >= 5 and toks[-1] not in common:
                    names.add(toks[-1])
    alt = "|".join(sorted((re.escape(n).replace(r"\ ", r"\s+") for n in names), key=len, reverse=True))
    return re.compile(r"\b(?:" + alt + r")\b")


def cmd_extract(args) -> int:
    eps = load_episodes()
    cache = Cache(CACHE)
    texts = {}
    for e in eps:
        if e["yt_id"]:
            t = transcript_text(e["yt_id"])
            if t:
                texts[e["key"]] = t
    common = common_words(list(texts.values())) | {"jones", "rogan", "white", "young"}
    blockers = blocker_regex(common)
    by_date = fight_index()
    all_fights = sorted((f for fs in by_date.values() for f in fs), key=lambda f: f["date"])
    by_fighter = collections.defaultdict(list)
    for f in all_fights:
        for i, p in enumerate(f["f"]):
            by_fighter[p["key"]].append((f["date"], f, i))
    rows = []
    for e in eps:
        text = texts.get(e["key"])
        if text is None:
            continue
        meta = cache.get("meta", e["yt_id"])
        pub = public_at(e, meta)
        day = pub.date()
        # PRE: fighters on cards in (day, day + HORIZON]
        scope = []
        for k in range(1, HORIZON_DAYS + 1):
            for f in by_date.get(day + dt.timedelta(days=k), ()):
                fid = f"{f['date']}|{f['bout']}"
                for i, p in enumerate(f["f"]):
                    scope.append({"name": p["name"], "key": p["key"],
                                  "fight": (fid, f["date"].isoformat(), i, "pre")})
        # NEXT: fighters who fought in the 3 days before (Fight Companion and
        # the week's recap), graded against their next fight only
        for k in range(0, 4):
            for f in by_date.get(day - dt.timedelta(days=k), ()):
                for i, p in enumerate(f["f"]):
                    nxt = [x for x in by_fighter[p["key"]] if x[0] > day]
                    if not nxt or any(x["key"] == p["key"] for x in scope):
                        continue
                    d2, f2, i2 = nxt[0]
                    scope.append({"name": p["name"], "key": p["key"],
                                  "fight": (f"{f2['date']}|{f2['bout']}", d2.isoformat(), i2, "next")})
        if not scope:
            continue
        for c in claims_in(text, name_patterns(scope, common), blockers):
            fid, fdate, side, kind = c["fight"]
            rows.append({"episode": e["key"], "ep_kind": e["kind"], "title": e["title"],
                         "public_at": pub.isoformat(timespec="minutes"), "fight_id": fid,
                         "fight_date": fdate, "side": side, "kind": kind,
                         "gap_days": (dt.date.fromisoformat(fdate) - day).days,
                         "fighter": c["fighter"], "fkey": c["fkey"], "type": c["type"],
                         "sign": c["sign"], "pos": c["pos"], "hit": c["hit"],
                         "quote": c["quote"]})
    with open(CLAIMS, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["episode"])
        w.writeheader()
        w.writerows(rows)
    c = collections.Counter((r["kind"], r["type"]) for r in rows)
    print(f"{len(texts)} transcripts, {len(rows)} rows -> {CLAIMS.relative_to(ROOT)}")
    for (k, t), n in sorted(c.items()):
        print(f"  {k:5s} {t:13s} {n}")
    return 0


def read_claims() -> list[dict]:
    rows = list(csv.DictReader(open(CLAIMS)))
    for r in rows:
        r["sign"] = int(r["sign"])
        r["side"] = int(r["side"])
        r["gap_days"] = int(r["gap_days"])
        r["pos"] = int(r["pos"])
    return rows


def cmd_sample(args) -> int:
    rows = [r for r in read_claims() if r["type"] != "mention"]
    random.Random(args.seed).shuffle(rows)
    for i, r in enumerate(rows[: args.n]):
        print(f"[{i}] {r['type']} {r['sign']:+d} {r['fighter']} ({r['kind']}, {r['episode']}, fight {r['fight_date']})")
        print("    " + r["quote"].replace("\n", " "))
    return 0


# ---------------------------------------------------------------------------
# Grading: against the de-biased market, the fight as the unit
# ---------------------------------------------------------------------------
def _lg(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def fit_beta(rows: list[tuple[float, float, int]], l2: float = 1.0) -> float:
    """One slope on top of the market: y ~ sigmoid(offset + b x). Newton steps,
    a light ridge so a type with a handful of train fights cannot run off."""
    b = 0.0
    for _ in range(50):
        g = -l2 * b
        h = l2
        for off, x, y in rows:
            p = _sig(off + b * x)
            g += (y - p) * x
            h += p * (1 - p) * x * x
        step = g / h
        b += step
        if abs(step) < 1e-8:
            break
    return b


def _ll(p: float, y: int) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def market_rows() -> dict:
    """{fight id: (fight, outcome table cells, p_A)} for every priced fight."""
    out = {}
    for f, odds in mma.priced_fights():
        t = mk.outcome_table(odds["ml"], odds["method"], f["lbs"], f["women"], f["sched_rounds"])
        c = t["cells"]
        pa = c["A_KO"] + c["A_SUB"] + c["A_DEC"]
        pb = c["B_KO"] + c["B_SUB"] + c["B_DEC"]
        out[f"{f['date']}|{f['bout']}"] = (f, c, pa / (pa + pb))
    return out


def _boot(vals: list[tuple[str, str, float]], stat, n: int = 2000, seed: int = 11) -> dict:
    """90% intervals resampling whole EVENTS, then whole EPISODES (claims
    from one card share one realisation; claims from one episode share one
    mood). Both are reported; read the wider."""
    rng = random.Random(seed)
    out = {}
    for name, idx in (("event", 0), ("episode", 1)):
        groups = collections.defaultdict(list)
        for v in vals:
            for g in (v[idx] if isinstance(v[idx], (list, tuple, set)) else [v[idx]]):
                groups[g].append(v)
        keys = list(groups)
        if len(keys) < 2:
            out[name] = (float("nan"), float("nan"))
            continue
        draws = []
        for _ in range(n):
            pick = [v for _k in keys for v in groups[rng.choice(keys)]]
            draws.append(stat(pick))
        draws.sort()
        out[name] = (draws[int(0.05 * n)], draws[int(0.95 * n)])
    return out


WIN_TYPES = ("mention", "pick_for", "pick_against", "pick", "injury", "weight_cut",
             "short_notice", "health", "cardio_neg", "cardio_pos", "chin_neg", "chin_pos", "guest")


def fight_signals(claims: list[dict], kind: str, max_gap: int | None = None) -> dict:
    """{fight id: {type: [x_A, episodes]}} -- x_A is side 0's signal minus
    side 1's, each side's signal = sum of signs over EPISODES (an episode that
    says it five times says it once)."""
    if max_gap is None:
        max_gap = HORIZON_DAYS if kind == "pre" else NEXT_HORIZON_DAYS
    per = collections.defaultdict(lambda: collections.defaultdict(set))
    for r in claims:
        if r["kind"] != kind or r["gap_days"] > max_gap:
            continue
        t = r["type"]
        sign = r["sign"] if t != "mention" else 1
        keys = [t]
        if t in ("pick_for", "pick_against"):
            keys.append("pick")
        if t in ("injury", "weight_cut", "short_notice"):
            keys.append("health")
        for k in keys:
            per[(r["fight_id"], r["side"], k)][r["episode"]].add(sign)
    out = collections.defaultdict(dict)
    for (fid, side, t), eps in per.items():
        if t == "mention":
            v = math.log1p(len(eps))
        else:
            v = sum(1 if sum(s) > 0 else -1 if sum(s) < 0 else 0 for s in eps.values())
            v = max(-1, min(1, v))
        x, e0 = out[fid].get(t, (0.0, set()))
        out[fid][t] = (x + (v if side == 0 else -v), e0 | set(eps))
    return out


def guest_signals(eps: list[dict], days: int = 60) -> dict:
    """A fighter who was the GUEST within `days` before a fight -- no
    extraction at all, so no extraction error."""
    by_fighter = collections.defaultdict(list)
    for f in mma.fights():
        if f["date"]:
            for i, p in enumerate(f["f"]):
                by_fighter[norm(p["name"])].append((f["date"], f, i))
    for v in by_fighter.values():
        v.sort(key=lambda x: x[0])
    out = collections.defaultdict(dict)
    for e in eps:
        day = dt.datetime.fromisoformat(e["rss_pub"]).date()
        for g in e.get("guests", []):
            nxt = [x for x in by_fighter.get(g, []) if x[0] > day]
            if nxt and (nxt[0][0] - day).days <= days:
                d, f, i = nxt[0]
                fid = f"{f['date']}|{f['bout']}"
                x, e0 = out[fid].get("guest", (0.0, set()))
                out[fid]["guest"] = (x + (1 if i == 0 else -1), e0 | {e["key"]})
    return out


def grade_win(sig: dict, mkt: dict, typ: str) -> dict:
    """Win test for one claim type: A won ~ market logit + b x_A."""
    tr, te = [], []
    for fid, d in sig.items():
        if typ not in d or fid not in mkt:
            continue
        x, eps = d[typ]
        if x == 0:
            continue
        f, _c, pa = mkt[fid]
        if f["draw"] or f["nc"]:
            continue
        y = int(f["f"][0]["won"])
        row = (_lg(pa), x, y, f["event"], tuple(sorted(eps)), pa, f["date"])
        (tr if f["date"] < CUTOFF else te).append(row)
    return _summarise(tr, te)


def grade_ko(claims: list[dict], mkt: dict, typ: str, kind: str) -> dict:
    """Method test: a fighter called chinny (chin_neg) or durable (chin_pos)
    ~ P(he is KO'd), against the market's own P(opponent wins by KO)."""
    per = collections.defaultdict(set)
    for r in claims:
        if r["type"] == typ and r["kind"] == kind and \
                r["gap_days"] <= (HORIZON_DAYS if kind == "pre" else NEXT_HORIZON_DAYS):
            per[(r["fight_id"], r["side"])].add(r["episode"])
    tr, te = [], []
    for (fid, side), eps in per.items():
        if fid not in mkt:
            continue
        f, c, _pa = mkt[fid]
        if f["nc"]:
            continue
        opp = "B" if side == 0 else "A"
        p = c[f"{opp}_KO"] / (1 - mk.P_DRAW)
        y = int(f["f"][1 - side]["won"] and f["method"] == "KO")
        x = 1.0 if typ == "chin_neg" else -1.0      # +x = expect MORE KO losses
        row = (_lg(p), x, y, f["event"], tuple(sorted(eps)), p, f["date"])
        (tr if f["date"] < CUTOFF else te).append(row)
    return _summarise(tr, te)


def _summarise(tr: list, te: list) -> dict:
    b = fit_beta([(o, x, y) for o, x, y, *_ in tr]) if tr else 0.0

    def resid(rows):        # signed: >0 = the claim's direction beat the market
        return sum((y - p) * (1 if x > 0 else -1) for _o, x, y, _e, _ep, p, _d in rows) / max(1, len(rows))

    def dll(rows):          # log loss saved per fight by the train-fitted b
        return sum(_ll(_sig(o), y) - _ll(_sig(o + b * x), y) for o, x, y, *_ in rows) / max(1, len(rows))

    out = {"b_train": round(b, 3), "n_train": len(tr), "n_test": len(te),
           "events_test": len({r[3] for r in te}),
           "episodes_test": len({e for r in te for e in r[4]}),
           "events_train": len({r[3] for r in tr})}
    for name, rows in (("train", tr), ("test", te)):
        if not rows:
            continue
        vals = [(r[3], r[4], r) for r in rows]
        ci = _boot(vals, lambda pick: resid([v[2] for v in pick]))
        out[f"resid_{name}"] = round(resid(rows), 4)
        out[f"resid_{name}_ci_event"] = [round(v, 4) for v in ci["event"]]
        out[f"resid_{name}_ci_episode"] = [round(v, 4) for v in ci["episode"]]
        out[f"mkt_{name}"] = round(sum(r[5] if r[1] > 0 else 1 - r[5] for r in rows) / len(rows), 4)
        out[f"hit_{name}"] = round(sum(r[2] if r[1] > 0 else 1 - r[2] for r in rows) / len(rows), 4)
    if te:
        vals = [(r[3], r[4], r) for r in te]
        out["dll_test"] = round(dll(te), 5)
        ci = _boot(vals, lambda pick: dll([v[2] for v in pick]))
        out["dll_test_ci_event"] = [round(v, 5) for v in ci["event"]]
        out["dll_test_ci_episode"] = [round(v, 5) for v in ci["episode"]]
    out["enough_data"] = len(te) >= 100 and out["events_test"] >= 5
    return out


def cmd_grade(args) -> int:
    claims = read_claims()
    mkt = market_rows()
    eps = load_episodes()
    report = {"cutoff": CUTOFF.isoformat(), "horizon_days": HORIZON_DAYS,
              "built": dt.date.today().isoformat(), "win": {}, "ko": {}}
    for kind in ("pre", "next"):
        sig = fight_signals(claims, kind)
        if kind == "pre":
            for fid, d in guest_signals(eps).items():
                sig[fid].update(d) if fid in sig else sig.__setitem__(fid, d)
        for t in WIN_TYPES:
            if t == "guest" and kind != "pre":
                continue
            report["win"][f"{kind}:{t}"] = grade_win(sig, mkt, t)
        for t in ("chin_neg", "chin_pos"):
            report["ko"][f"{kind}:{t}"] = grade_ko(claims, mkt, t, kind)
    (OUT / "grade.json").write_text(json.dumps(report, indent=1))
    print(f"held out: fights from {CUTOFF}; claims up to {HORIZON_DAYS} days before the fight")
    print(f"{'test':22s} {'train n/ev':>10s} {'b':>6s} | {'test n':>6s} {'ev':>4s} {'eps':>4s} "
          f"{'mkt':>6s} {'hit':>6s} {'resid':>7s} {'90% (event)':>17s} {'90% (episode)':>17s} {'dLL/fight':>9s}")
    for grp in ("win", "ko"):
        for k, r in report[grp].items():
            if not r.get("n_test") and not r.get("n_train"):
                continue
            ce = r.get("resid_test_ci_event", [float("nan")] * 2)
            cp = r.get("resid_test_ci_episode", [float("nan")] * 2)
            print(f"{grp + ' ' + k:22s} {r['n_train']:>4d}/{r['events_train']:<5d} {r['b_train']:>6.2f} | "
                  f"{r['n_test']:>6d} {r['events_test']:>4d} {r['episodes_test']:>4d} "
                  f"{r.get('mkt_test', float('nan')):>6.3f} {r.get('hit_test', float('nan')):>6.3f} "
                  f"{r.get('resid_test', float('nan')):>+7.3f} [{ce[0]:+.3f},{ce[1]:+.3f}] "
                  f"[{cp[0]:+.3f},{cp[1]:+.3f}] {r.get('dll_test', float('nan')):>+9.4f}"
                  + ("" if r["enough_data"] else "  not enough data"))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("episodes")
    p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("fetch")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--kind", nargs="*")
    sub.add_parser("extract")
    sub.add_parser("grade")
    p = sub.add_parser("sample")
    p.add_argument("-n", type=int, default=120)
    p.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    sys.exit({"episodes": cmd_episodes, "fetch": cmd_fetch, "extract": cmd_extract,
              "sample": cmd_sample, "grade": cmd_grade}[a.cmd](a))
