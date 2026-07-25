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


def norm(name: str) -> str:
    s = _SUFFIX_RE.sub("", (name or "").strip())
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())
