"""Player-name normalization shared across sports.

The first genuine cross-sport shared seam in the multi-sport build (see
DFS_MULTISPORT_PLAN.md §2's "duplicate first, abstract later" -- this earns
its extraction because the SAME bug independently hit NFL and NBA joins,
not because it was guessed in advance): a book's player description and a
free stats source's display name can disagree on both diacritics ("Nikola
Jokić" vs "Nikola Jokic" -- confirmed live, both spellings real, same
person) and generational suffixes ("Isaiah Stewart" vs "Isaiah Stewart II"
-- confirmed live across both NFL and NBA). Fold both away so a join isn't
silently sensitive to which convention either side's data happened to use.

edge/dfs.py's own norm() already does the diacritic fold correctly
(unicodedata NFKD -> ascii) and works fine for MLB as-is; left untouched
here rather than migrated, since it's proven/tested in production and MLB
hasn't hit a suffix-collision bug in practice."""
import re
import unicodedata

_SUFFIX_RE = re.compile(r"\s+(jr\.?|sr\.?|i{2,3}|iv|v)$", re.IGNORECASE)


_NICK_PAREN = re.compile(r"^\S[^()]*\(([^()]+)\)\s+(\S.*)$")


def norm(name: str) -> str:
    # "Anthony-John (AJ) Greer" (NHL.com) is DK's "A.J. Greer": a nickname in parentheses
    # BEFORE the surname is the name he plays under (2026-11-08: Greer ran on default rates,
    # 18 NHL board rows). A trailing tag ("Sebastian Aho (CAR)") is left for SalaryLookup.
    m = _NICK_PAREN.match((name or "").strip())
    if m:
        name = f"{m.group(1)} {m.group(2)}"
    s = _SUFFIX_RE.sub("", (name or "").strip())
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


#: First names that are the same person and share no usable prefix. Prefixes
#: (Rob/Robert, Cam/Cameron, Chris/Christopher) need no entry: first_names_agree
#: handles those. Each row is one person's names; DK's salary list, its own
#: sportsbook and the stats sites each pick a different one.
NICKNAMES = (
    ("robert", "rob", "robbie", "bob", "bobby", "bobbie"),
    ("william", "will", "willie", "bill", "billy", "liam"),
    ("michael", "mike", "mikey"),
    ("james", "jim", "jimmy", "jimmie", "jamie"),
    ("john", "johnny", "jon", "jonny"),
    ("jonathan", "jon", "johnathan", "jonathon", "johnny"),
    ("joseph", "joe", "joey"),
    ("anthony", "tony", "ant"),
    ("thomas", "tom", "tommy"),
    ("richard", "rick", "ricky", "rich", "richie", "dick"),
    ("charles", "charlie", "chuck", "chaz"),
    ("edward", "ed", "eddie", "ned", "teddy"),
    ("theodore", "ted", "teddy", "theo"),
    ("andrew", "andy", "drew"),
    ("nathan", "nate", "nathaniel", "nat"),
    ("zachary", "zach", "zack", "zak", "zachariah", "zacharia"),
    ("jacob", "jake", "jakob"),
    ("joshua", "josh"),
    ("daniel", "dan", "danny"),
    ("matthew", "matt", "matty"),
    ("samuel", "sam", "sammy"),
    ("benjamin", "ben", "benny", "benji"),
    ("alexander", "alex", "xander", "alec", "sasha"),
    ("christopher", "chris", "topher"),
    ("nicholas", "nick", "nico", "nicolas", "nikolas", "nicky"),
    ("stephen", "steve", "steven", "stevie", "stefan"),
    ("kenneth", "ken", "kenny"),
    ("ronald", "ron", "ronnie"),
    ("donald", "don", "donnie"),
    ("lawrence", "larry", "laurence"),
    ("timothy", "tim", "timmy"),
    ("patrick", "pat", "paddy"),
    ("gabriel", "gabe"),
    ("emmanuel", "manny", "immanuel", "emanuel"),
    ("isaiah", "ike", "izzy", "isiah", "izaiah"),
    ("elijah", "eli"),
    ("maxwell", "max", "maximilian", "maxim", "maxime"),
    ("frederick", "fred", "freddie", "freddy", "fredrick"),
    ("demetrius", "meech"),
    ("dominic", "dom", "dominick", "dominik"),
    ("tre", "trey", "tray"),
    # 2026-11-08 audit (book prop subject vs DK name, every sport, last 10 slates): DK's
    # sportsbook lists "Yegor Chinakhov" (DK DFS: Egor, props lost 5 NHL nights), "Janis
    # Moser" (DFS: J.J. -- his given name is Janis), "GG Jackson" (DFS: Gregory). Russian
    # transliterations fail the same way, so their usual pairs are listed with them.
    ("gregory", "greg", "gg"),
    ("janis", "jj"),
    ("yegor", "egor"),
    ("evgeny", "evgeni", "evgenii", "yevgeny", "yevgeni", "yevgenii"),
    ("dmitri", "dmitry", "dmitrii", "dmitrij"),
    ("alexei", "aleksei", "alexey", "aleksey", "alexej"),
    ("sergei", "sergey"),
    ("andrei", "andrey"),
    ("nikolai", "nikolay"),
    ("artemi", "artemy", "artem"),
    ("ilya", "ilia"),
    ("yaroslav", "jaroslav"),
    ("mikhail", "michail"),
    ("vasili", "vasily", "vasiliy", "vasilii"),
)
_NICK = {}
for _row in NICKNAMES:
    for _name in _row:
        _NICK.setdefault(_name, set()).update(_row)


def _damerau_one(a: str, b: str) -> bool:
    """True when a and b differ by at most one edit or one adjacent swap."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def first_names_agree(a: str, b: str, typo: bool = False) -> bool:
    """Could these two first names be the same person? Adam, 2026-11-02: nicknames
    are fine, a first INITIAL as a first name is not. Mike / Malik Williams share an
    initial and nothing else, so one letter never agrees with anything; "CJ" and
    "C.J." agree because they fold to the same text, not because of their initials.
    Agreement: the same after folding, a listed nickname, or one a 3+ letter prefix of
    the other (Rob/Robert). Only ever a test on top of a matching surname.

    `typo` also accepts one slip on 5+ letters with the same first letter (DK's Antione /
    Antoine, Mouhamed / Mohamed). Only for a join already inside one TEAM: across a slate
    Kevin / Kelvin Smith are two men."""
    x, y = norm(a), norm(b)
    if len(x) < 2 or len(y) < 2:
        return False
    if x == y or y in _NICK.get(x, ()):
        return True
    short, long_ = sorted((x, y), key=len)
    if len(short) >= 3 and long_.startswith(short):
        return True
    return typo and len(short) >= 5 and x[0] == y[0] and _damerau_one(x, y)


def rekey_bare(d: dict) -> dict:
    """Re-key a {edge.dfs.norm(name): value} dict onto suffix-free keys.

    edge.dfs.norm KEEPS "Jr."/"II" (MLB has "Luis Garcia" and "Luis Garcia
    Jr." as different players), while a DK contest export is keyed with
    `norm` above. Joining the two directly dropped every suffixed MLB hitter
    from calibration -- Tatis Jr. at 70% owned, Chisholm Jr. at 65% -- in 33
    of the contests on file. Two source keys that fold to ONE bare key are
    left out rather than guessed.
    """
    out: dict = {}
    clash: set = set()
    for k, v in d.items():
        b = "".join(c for c in k if c.isalnum())
        for suf in ("jr", "sr", "iii", "ii", "iv"):
            if b.endswith(suf) and len(b) > len(suf) + 3:
                b = b[: -len(suf)]
                break
        if b in out:
            clash.add(b)
        out[b] = v
    for b in clash:
        out.pop(b, None)
    return out
