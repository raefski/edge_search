"""Make a Streamlit Cloud deploy actually take effect. One implementation.

THE PROBLEM, WHICH IS NOT THEORETICAL
Streamlit Community Cloud pulls a new commit and RERUNS the page script without
restarting the Python process. `sys.modules` therefore keeps whatever module
objects an earlier run imported, and a deploy that changes an existing
function's BODY -- the ordinary bugfix -- goes on running the pre-fix code with
no error at all. `import` only re-executes a file for a name that is MISSING,
which never catches this.

WHY THIS IS A SHARED MODULE NOW
It is the fourth copy. pages/5_⚖️_Arbitrage.py wrote the first one, and the
three DFS pages each carried their own. They drifted, and the drift was a live
outage rather than untidiness:

    2026-09-24, deployed edge_search, NCAAF page
      KeyError: unknown profile 'dfs_ncaaf';
                have ['arb','dfs_mlb','dfs_nba','dfs_nfl','pickem_nfl']

The commit that added the `dfs_ncaaf` profile was on main and the page file it
served was the new one. What was stale was `edge.odds.profiles`, imported into
the process by app.py or the NFL page before the deploy -- and every DFS page's
reload guard covered `edge.dfs*` and the sport module and NOTHING ELSE. The
guard existed, ran, and reloaded the wrong set.

TWO THINGS HAVE TO COVER THE SAME SET, AND THAT IS THE TRAP
A reload is gated on a FINGERPRINT via st.cache_resource, so that it costs
nothing on an ordinary rerun. If the fingerprint watches a narrower set of
files than the reload touches, a change to a file outside the fingerprint never
re-fires the reload at all -- which is the same bug one level up, and is
exactly what would have happened here even with the module list widened:
edge/odds/profiles.py changed and no edge/dfs*.py did, so the fingerprint was
unchanged. Both now come from the same PACKAGES list.

THE PASS COUNT IS NOT ARBITRARY
Reload order is not dependency-sorted. A module reloaded before something it
does `from .x import y` on picks up that something's PRE-reload value on the
first pass and only catches up once that something has itself been reloaded.
Three passes, the number pages/5_⚖️_Arbitrage.py already settled on.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Every package a DFS page's behaviour can come from.
#:
#: `edge.odds` is the one that was missing and it is the one most likely to
#: change without any edge/dfs*.py changing: it holds the collection profiles,
#: the store, the snapshot reader and the price-source resolution. A new sport
#: adds a profile there and touches nothing else in it.
DFS_PACKAGES: tuple[str, ...] = ("edge.dfs", "edge.odds", "edge.ncaaf",
                                 "edge.nascar", "edge.nfl", "edge.nba",
                                 "edge.mma", "edge.names")

#: Source globs the fingerprint watches. Must cover the same ground as
#: DFS_PACKAGES -- see the module docstring on why a narrower fingerprint
#: silently disables the reload it gates.
DFS_GLOBS: tuple[str, ...] = ("edge/dfs*.py", "edge/odds/*.py", "edge/ncaaf.py",
                              "edge/nascar*.py", "edge/nfl.py", "edge/nba.py",
                              "edge/mma*.py", "edge/names.py")


def matches(name: str, prefixes: tuple[str, ...] = DFS_PACKAGES) -> bool:
    """Does this module name fall under one of the watched packages?

    A module matches a prefix when its name is that prefix exactly, or
    continues it with a "." or a "_". BOTH separators are needed and neither
    is redundant:

        edge.odds.profiles   matches "edge.odds"    via "."   (a subpackage)
        edge.dfs_ladder      matches "edge.dfs"     via "_"   (a sibling module)

    A plain `startswith` would be wrong the other way -- "edge.nascar" would
    swallow anything beginning with those letters -- and requiring "." alone
    silently misses every edge/dfs_*.py file, which is most of this repo's DFS
    code and was the whole point.

    Split out as its own function so it can be tested WITHOUT calling
    reload_packages(). Reloading live modules inside a test is not a harmless
    way to exercise this: it rebinds classes, so edge.odds.source.StaleOdds
    becomes a different object and every other test's `pytest.raises(StaleOdds)`
    silently stops matching. That cost five unrelated failures once.
    """
    return any(name == p or name.startswith(p + ".") or name.startswith(p + "_")
               for p in prefixes)


def source_fingerprint(globs: tuple[str, ...] = DFS_GLOBS,
                       root: Path | None = None) -> float:
    """Newest mtime across the watched sources. The cache key for the reload.

    Returns 0.0 rather than raising when nothing matches, so a page still
    renders in a checkout where a glob has gone stale.
    """
    root = root or ROOT
    newest = 0.0
    for pattern in globs:
        for path in root.glob(pattern):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest


def reload_packages(prefixes: tuple[str, ...] = DFS_PACKAGES,
                    passes: int = 3) -> int:
    """Re-execute every already-imported module under `prefixes`. Returns count.

    Membership is `matches()` -- see there for why both separators are needed.

    Every failure is swallowed deliberately. A module that cannot be reloaded
    is no worse off than it was, and raising here would take down a page that
    was about to work.
    """
    wanted = sorted(name for name in sys.modules if matches(name, prefixes))
    for _pass in range(passes):
        for name in wanted:
            module = sys.modules.get(name)
            if module is None:
                continue
            try:
                importlib.reload(module)
            except Exception:                               # noqa: BLE001
                pass
    return len(wanted)
