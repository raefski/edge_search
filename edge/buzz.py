"""Player buzz: how often public DFS content names each player before lock.

GPP ownership is set by what the field was told to play, and the field is told
by the same few dozen YouTube shows every week. Counting who those shows talk
about is a direct read on that, where edge/dfs_nfl_theory.py's field model
only sees points per dollar. This module is the sport-free matching half;
scripts/buzz_nfl.py collects transcripts and scripts/buzz_fit_nfl.py tests
whether the counts improve the ownership fit.

A mention is counted, not a recommendation. "Everyone's on Gibbs, I'm fading
him" still says Gibbs will be owned, which is the only thing ownership needs.

Matching is tuned to YouTube's automatic captions, which capitalise proper
nouns reliably but garble first names ("Jir Gibbs", "Bjon" for Bijan). So
surnames carry most of the weight, and a bare surname or first name only
counts when it is capitalised and not the first word of a sentence -- "Love
the matchup" must not credit Jordan Love.
"""
from __future__ import annotations

import re
from collections import Counter

from edge.names import norm as bare_name

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)*")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

#: DFS shorthand that no rule derives from a name. Keyed by edge.names.norm of
#: the DK display name; only used when that player is on the board.
NICKNAMES = {
    "christianmccaffrey": ["CMC"],
    "jaxonsmithnjigba": ["JSN"],
    "amonrastbrown": ["ARSB"],
    "ajbrown": ["AJB"],
    "brianthomas": ["BTJ"],
    "marvinharrison": ["MHJ"],
    "travisetienne": ["ETN"],
    "deandrehopkins": ["Nuk"],
    "marquisebrown": ["Hollywood Brown"],
}

DST_WORDS = [("defense",), ("d",), ("dst",), ("def",), ("d", "st")]

#: Never a bare surname or first name: football talk says these as places and
#: teams. On the 2026-09-20 slate "Dallas" was the Cowboys 125 times out of
#: 125, all of which had been credited to DeeJay Dallas.
PLACES = set("""
arizona atlanta baltimore buffalo carolina chicago cincinnati cleveland dallas
denver detroit green bay houston indianapolis indy jacksonville kansas city las
vegas los angeles miami minnesota england orleans york philadelphia philly
pittsburgh francisco seattle tampa tennessee washington london ford mexico
dublin berlin munich madrid paris brazil germany lambeau arrowhead
cardinals falcons ravens bills panthers bears bengals browns cowboys broncos
lions packers texans colts jaguars chiefs raiders chargers rams dolphins vikings
patriots saints giants jets eagles steelers niners seahawks buccaneers bucs
titans commanders
alabama alaska arkansas california colorado connecticut delaware florida georgia
hawaii idaho illinois indiana iowa kentucky louisiana maine maryland
massachusetts michigan mississippi missouri montana nebraska nevada ohio oklahoma
oregon pennsylvania texas utah vermont virginia wisconsin wyoming dakota
""".split())

FULL, NICK, LAST, FIRST, DST = "full", "nick", "last", "first", "dst"
_NEEDS_PROPER_NOUN = {LAST, FIRST}


def _norm_token(t: str) -> str:
    return "".join(c for c in t.lower() if c.isalnum())


def tokenize(text: str) -> list[tuple[str, bool, bool]]:
    """-> [(token, capitalised, starts_sentence)].

    Runs of single capital letters merge ("A. J." -> "aj") so an initialled
    name reads the same whether the captioner dotted it or not. A caption's
    ">>" speaker change counts as a sentence break.
    """
    out: list[list] = []
    prev_end = 0
    for m in _TOKEN_RE.finditer(text):
        raw = m.group(0)
        gap = text[prev_end:m.start()]
        prev_end = m.end()
        tok = _norm_token(raw)
        single_cap = len(raw) == 1 and raw.isupper()
        if single_cap and out and out[-1][3] and re.fullmatch(r"[.\s]*", gap):
            out[-1][0] += tok
            continue
        sentence = not out or bool(re.search(r"[.!?>]", gap))
        out.append([tok, raw[0].isupper(), sentence, single_cap])
    return [(t, cap, sent) for t, cap, sent, _ in out]


#: Rare among NFL players but common among the people doing the talking --
#: hosts, analysts ("Evan" is usually Evan Silva, not Evan Engram), coaches,
#: owners. Never a bare first name. Distinctive ones (Bijan, Baker, Chuba)
#: still count.
COMMON_FIRST_NAMES = set("""
aaron adam alan albert alex alexander andre andrew andy anthony austin ben benjamin
bill billy blake bob bobby brad brandon brett brian bruce bryan carl carson chad
charles charlie chris christopher cole colin connor cooper craig dan daniel
darnell dave david dawson dennis derek don donald doug douglas drew dylan ed
edward eric ethan evan frank gabe gary george grant greg gregory harold henry
hunter ian jack jacob jake james jason jeff jeffrey jeremy jerry jesse jim jimmy
joe john johnny jon jonah jonathan jonathon jordan jose joseph josh joshua juan
justin keith kevin kurt kyle larry logan luke mack marcus mark mason matt
matthew max michael mike nate nathan nick nicholas noah omar owen parker pat
patrick paul pete peter phil philip ralph randy ray raymond rich richard rick
rob robert roger ron ronald roy russell ryan sam samuel scott sean seth shane
stephen steve steven ted terry tim timothy todd tom tommy tony travis troy
tyler vincent walter wayne will william zach zachary
""".split())


CALENDAR = set("""
monday tuesday wednesday thursday friday saturday sunday january february march
april may june july august september october november december easter christmas
thanksgiving halloween
""".split())


def _is_place(tokens: tuple[str, ...]) -> bool:
    return len(tokens) == 1 and tokens[0] in PLACES


def _name_parts(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """("Amon-Ra St. Brown") -> (("amon", "ra"), ("st", "brown"))."""
    words = name.split()
    while len(words) > 1 and _norm_token(words[-1]) in _SUFFIXES:
        words.pop()
    first = tuple(t for t, _, _ in tokenize(words[0])) if words else ()
    last = tuple(t for w in words[1:] for t, _, _ in tokenize(w))
    return first, last


def build_aliases(board: list[dict], first_name_counts: Counter | None = None) -> dict:
    """{token tuple: (board name, kind)} for one slate.

    `board` rows need "name" and "pos" ("DST" marks a defence, whose name is
    the team nickname). Every name on the slate belongs here, not just the
    players the model priced: an off-pool "Mason Taylor" has to exist as a full
    name, or its tokens get read as Jordan Mason plus Jonathan Taylor.

    A bare surname is kept only when no other board player shares it. A bare
    first name additionally has to be rare across past NFL seasons
    (`first_name_counts`, at most one prior player), so "Bijan" and "Saquon"
    count but "Kyle" (Kyle Shanahan is not Kyle Pitts) does not.
    """
    first_name_counts = first_name_counts or Counter()
    aliases: dict = {}
    surnames: Counter = Counter()
    firsts: Counter = Counter()
    parts = {}
    for row in board:
        name = row["name"].strip()
        if row.get("pos") == "DST":
            nick = tuple(t for t, _, _ in tokenize(name))
            for words in DST_WORDS:
                aliases[nick + words] = (name, DST)
            continue
        first, last = _name_parts(name)
        if not last:
            continue
        parts[name] = (first, last)
        surnames[last] += 1
        if len(first) == 1:
            firsts[first[0]] += 1

    surname_set = set(surnames)
    for name, (first, last) in parts.items():
        aliases[first + last] = (name, FULL)
        for nick in NICKNAMES.get(bare_name(name), []):
            aliases[tuple(t for t, _, _ in tokenize(nick))] = (name, NICK)
    for name, (first, last) in parts.items():
        if surnames[last] == 1 and last not in aliases and not _is_place(last):
            aliases[last] = (name, LAST)
    for name, (first, last) in parts.items():
        if (len(first) == 1 and len(first[0]) >= 4 and firsts[first[0]] == 1
                and (first[0],) not in surname_set and first not in aliases
                and first_name_counts[first[0]] <= 1 and not _is_place(first)
                and first[0] not in COMMON_FIRST_NAMES):
            aliases[first] = (name, FIRST)
    return aliases


#: A learned spelling has to resemble the name it stands for, be seen beside
#: the other half of that name this often, and point at one player this
#: consistently.
VARIANT_MIN_SIMILARITY = 0.6
VARIANT_MIN_SEEN = 3
VARIANT_MIN_SHARE = 0.8
#: Heavy evidence can carry a spelling that only sounds right: "Tyler Shuck"
#: 111 times on 2026-09-27 is Tyler Shough, though "shuck" scores 0.52 against
#: "shough" letter for letter.
VARIANT_STRONG_SEEN = 10
VARIANT_STRONG_SIMILARITY = 0.45
VARIANT_STRONG_MARGIN = 0.10
#: A surname can also be learned with no context at all when it is common and
#: very close: "Baitman" 348 times is Rashod Bateman, whose first name the
#: captions also mangle ("Rashad"), so no exact half-name ever sits beside it.
SOLO_MIN_SEEN = 10
SOLO_MIN_SIMILARITY = 0.75


def _consonants(s: str) -> str:
    return "".join(c for c in s if c not in "aeiouy")


def _similarity(a: str, b: str) -> float:
    """Captions mangle vowels far more than consonants: "Bejian" is b-j-n like
    Bijan, not b-r-n like Brian, though letter for letter it is equally close
    to both. So consonant skeletons count for half."""
    from difflib import SequenceMatcher
    return (SequenceMatcher(None, a, b).ratio()
            + SequenceMatcher(None, _consonants(a), _consonants(b)).ratio()) / 2


def _ranked(tok: str, candidates: list) -> tuple:
    """(best name, its similarity, margin over the runner-up)."""
    scored = sorted(((_similarity(tok, real), name) for real, name in candidates),
                    reverse=True)
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    return scored[0][1], scored[0][0], scored[0][0] - runner_up


def learn_variants(texts: list[str], board: list[dict], aliases: dict,
                   known: set | None = None) -> dict:
    """Caption misspellings of board names, learned from the slate's own
    transcripts, as extra aliases.

    Automatic captions spell a name the way it sounds. On the 2026-09-20 slate
    Bijan Robinson was said ~630 times and spelled "Bijan" 17 of them --
    "Bejon" 331, "Bejian" 148, "Bejan", "Bjon", "Bjan" -- and Ashton Jeanty was
    "Genty" 182 times and never "Jeanty". Bijan was 46.6% owned; counted by
    exact spelling he looked like a fringe play. No list written in advance
    would keep up with this, but the transcripts teach it: "Bejian" keeps
    turning up right before "Robinson" and looks far more like "Bijan" than
    like Wan'Dale or Demarcus, so it is Bijan, alone as well as in front of
    the surname. The same works the other way round ("Ashton Genty"), and a
    frequent near-spelling of a surname is learned on its own ("Baitman").

    A learned FIRST name is not used on its own when it is mostly followed by
    somebody else's surname: "Rashad White" (Rachaad) was 20 of the 109
    "Rashad"s on 2026-09-27 and "Rashad Baitman" (Rashod Bateman) 84.

    Never learned, because they are words or other people: `known` (name
    tokens of every NFL player on file -- Nico Collins off the slate is not
    Chig Hollins on it), anything the captions also write in lowercase
    ("Fantasy" is not Noah Fant), and calendar words ("Saturday" is not Stroud).
    """
    by_surname: dict = {}
    by_first: dict = {}
    real_tokens: set = set()
    solo_surnames: list = []
    for row in board:
        if row.get("pos") == "DST":
            continue
        name = row["name"].strip()
        first, last = _name_parts(name)
        real_tokens.update(first + last)
        if len(first) == 1 and len(last) == 1:
            by_surname.setdefault(last[0], []).append((first[0], name))
            by_first.setdefault(first[0], []).append((last[0], name))
    for surname, players in by_surname.items():
        if len(players) == 1:
            solo_surnames.append((surname, players[0][1]))

    known = known or set()

    def usable(tok):
        return (len(tok) >= 3 and tok not in real_tokens and tok not in known
                and (tok,) not in aliases and tok not in PLACES
                and tok not in COMMON_FIRST_NAMES and tok not in CALENDAR)

    def accept(sim, margin, n):
        if margin < 0.05:
            return False                    # too close to call between two players
        return sim >= VARIANT_MIN_SIMILARITY or (
            n >= VARIANT_STRONG_SEEN and sim >= VARIANT_STRONG_SIMILARITY
            and margin >= VARIANT_STRONG_MARGIN)

    def is_word(tok):
        return lower[tok] >= 0.2 * max(1, proper[tok])

    votes: dict = {}
    proper: Counter = Counter()             # capitalised, mid-sentence uses
    lower: Counter = Counter()              # lowercase uses: a word, not a name
    followers: dict = {}                    # token -> capitalised names after it
    for text in texts:
        toks = tokenize(text)
        for i, (a, a_cap, a_sent) in enumerate(toks):
            if a_cap and not a_sent:
                proper[a] += 1
            elif not a_cap:
                lower[a] += 1
            if i + 1 == len(toks):
                break
            b, b_cap, b_sent = toks[i + 1]
            if b_cap and not b_sent and len(b) > 1:
                followers.setdefault(a, Counter())[b] += 1
            if (a, b) in aliases:
                continue
            if a_cap and b in by_surname and usable(a):       # "Bejian Robinson"
                name, sim, margin = _ranked(a, by_surname[b])
                votes.setdefault((a, FIRST), {}).setdefault(name, []).append((sim, margin))
            if b_cap and a in by_first and usable(b):         # "Ashton Genty"
                name, sim, margin = _ranked(b, by_first[a])
                votes.setdefault((b, LAST), {}).setdefault(name, []).append((sim, margin))

    learned: dict = {}
    for (tok, kind), by_name in votes.items():
        name, seen = max(by_name.items(), key=lambda kv: len(kv[1]))
        n, total = len(seen), sum(len(v) for v in by_name.values())
        sim, margin = seen[0]
        if n < VARIANT_MIN_SEEN or n / total < VARIANT_MIN_SHARE or not accept(sim, margin, n):
            continue
        first, last = _name_parts(name)
        pair = (tok,) + last if kind == FIRST else first + (tok,)
        learned.setdefault(pair, (name, FULL))
        if len(tok) < 4 or is_word(tok):    # alone, a fragment ("Ave") or a word is too easy to hit
            continue
        if kind == FIRST:
            after = followers.get(tok, Counter())
            others = sum(c for t, c in after.items() if t != last[0])
            if proper[tok] and others / proper[tok] > 0.3:
                continue                    # "Rashad" is mostly somebody else
        learned[(tok,)] = (name, kind)

    for tok, n in proper.items():
        if (n < SOLO_MIN_SEEN or len(tok) < 5 or not usable(tok) or is_word(tok)
                or (tok,) in learned):
            continue
        name, sim, margin = _ranked(tok, solo_surnames) if solo_surnames else (None, 0, 0)
        if name and sim >= SOLO_MIN_SIMILARITY and margin >= 0.05 and not any(
                abs(len(k) - len(tok)) <= 2 and _similarity(tok, k) >= sim
                for k in known - real_tokens):
            # ...and no player OFF the slate is as close: "Craft" is Tucker
            # Kraft, not River Cracraft, when only Cracraft is playing.
            learned[(tok,)] = (name, LAST)
            learned.setdefault(_name_parts(name)[0] + (tok,), (name, FULL))
    return learned


def is_cased(tokens: list[tuple[str, bool, bool]]) -> bool:
    """False for an all-lowercase caption track, where proper-noun rules can't
    work and only full names, nicknames and defences are matched."""
    mid = [cap for _, cap, sent in tokens if not sent]
    return bool(mid) and sum(mid) / len(mid) > 0.02


def count_mentions(text: str, aliases: dict) -> Counter:
    """{board name: mentions} in one transcript. Longest alias wins at each
    position, so "Chase Brown" is one Chase Brown mention and not also a
    Ja'Marr Chase one."""
    tokens = tokenize(text)
    cased = is_cased(tokens)
    longest = max((len(k) for k in aliases), default=0)
    words = [t for t, _, _ in tokens]
    counts: Counter = Counter()
    i = 0
    while i < len(words):
        for n in range(min(longest, len(words) - i), 0, -1):
            hit = aliases.get(tuple(words[i:i + n]))
            if not hit:
                continue
            name, kind = hit
            if kind in _NEEDS_PROPER_NOUN:
                _, cap, sent = tokens[i]
                if not cased or not cap or sent:
                    continue
            counts[name] += 1
            i += n
            break
        else:
            i += 1
    return counts


def aggregate(videos: list[dict]) -> dict:
    """Per-player slate features from per-video counts.

    Each video is {"channel", "views", "counts": Counter}. `reach` weights a
    video's views by the player's share of that video's mentions -- roughly how
    much of its audience's attention went to him.
    """
    out: dict = {}
    for v in videos:
        total = sum(v["counts"].values())
        for name, c in v["counts"].items():
            row = out.setdefault(name, {"mentions": 0, "videos": 0,
                                        "channels": set(), "reach": 0.0})
            row["mentions"] += c
            row["videos"] += 1
            row["channels"].add(v["channel"])
            row["reach"] += (v.get("views") or 0) * c / total
    for row in out.values():
        row["channels"] = len(row["channels"])
        row["reach"] = round(row["reach"], 1)
    return out
