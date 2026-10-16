"""Make a Streamlit Cloud deploy actually take effect. Every page calls fresh().

    from edge.pagereload import fresh   # first thing after set_page_config
    fresh()

THE PROBLEM, WHICH IS NOT THEORETICAL
Streamlit Community Cloud pulls a new commit and RERUNS the page script without
restarting the Python process. `sys.modules` therefore keeps whatever module
objects an earlier run imported, and a deploy that changes an existing
function's BODY -- the ordinary bugfix -- goes on running the pre-fix code with
no error at all. `import` only re-executes a file for a name that is MISSING,
which never catches this.

WHY IT RELOADS EVERYTHING, AND WHY ONE GATE
This was the fifth implementation. Arbitrage wrote the first, the DFS pages
carried their own, and then a shared module took a PACKAGE LIST per page --
and every list was a chance to be too narrow, which was a live outage, twice:

    2026-09-24, NCAAF page: KeyError: unknown profile 'dfs_ncaaf'. The stale
      module was edge.odds.profiles; every DFS page's guard covered edge.dfs*
      and its sport module and NOTHING ELSE.
    The parlay pages reloaded only edge.arb + edge.repo_files, app.py (MLB) and
      Pick'em reloaded nothing, Arbitrage ran its own copy -- and all the pages
      shared ONE cached gate by name (`_reload_edge`), so whichever page ran
      first after a deploy decided which packages got reloaded for all of them.

There is no list to get wrong now: every resident module under edge/, scripts/
and harness/ is reloaded when any .py file under them changed since this
process loaded them -- one process-wide check that every page shares, held in
this module (never reloaded), not in a Streamlit cache. A deploy is rare and a
reload is ~0.5 s, so reloading what a page did not need costs nothing.

TWO THINGS HAVE TO COVER THE SAME SET, AND THAT IS THE TRAP
The check compares a FINGERPRINT (newest mtime) so an ordinary rerun costs one
stat per source file and nothing else. If the fingerprint watched a narrower set of files than the reload
touches, a change outside it would never re-fire the reload -- the same bug one
level up. PACKAGES and GLOBS below are the same three trees.

THE PASS COUNT IS NOT ARBITRARY
Reload order is not dependency-sorted. A module reloaded before something it
does `from .x import y` on picks up that something's PRE-reload value on the
first pass and only catches up once that something has itself been reloaded.
Three passes, the number pages/5_⚖️_Arbitrage.py settled on.

RELOADING REBINDS CLASSES. edge.odds.source.StaleOdds becomes a new object, so
an `except StaleOdds` holding the old one stops matching. That is why the
reload only runs when the source changed (a deploy), never on a rerun -- and
why tests exercise matches() rather than reloading live modules.
"""
from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Every package a page's behaviour can come from: the library, the scripts a
#: page imports (pages/11 reads scripts.dfs_overlay), and the harness.
PACKAGES: tuple[str, ...] = ("edge", "scripts", "harness")
#: The same three trees, as files -- see "TWO THINGS" above.
GLOBS: tuple[str, ...] = ("edge/**/*.py", "scripts/*.py", "harness/*.py")


def matches(name: str, prefixes: tuple[str, ...] = PACKAGES) -> bool:
    """Is this module under one of the packages? A name matches a prefix when
    it IS the prefix or continues it with "." -- never a bare `startswith`,
    which would let "edge" swallow an unrelated top-level "edgecase". This
    module itself is excluded: it is the one doing the reloading."""
    if name == __name__:
        return False
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def source_fingerprint(globs: tuple[str, ...] = GLOBS, root: Path | None = None) -> float:
    """Newest mtime across the watched sources: the gate's cache key. 0.0
    rather than raising when nothing matches, so a page still renders."""
    root = root or ROOT
    newest = 0.0
    for pattern in globs:
        for path in root.glob(pattern):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest


def reload_packages(prefixes: tuple[str, ...] = PACKAGES, passes: int = 3) -> int:
    """Re-execute every already-imported module under `prefixes`. Returns count.

    Every failure is swallowed deliberately: a module that cannot be reloaded
    is no worse off than it was, and raising here would take down a page that
    was about to work."""
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


#: The fingerprint of the sources this process is running, and the lock that
#: serialises Streamlit's session threads around a reload. Module state, so
#: process-wide: this module is the one never reloaded.
_loaded = source_fingerprint()
_lock = threading.Lock()


def fresh() -> bool:
    """Reload every resident edge / scripts / harness module if any of their
    sources changed since this process loaded them. Call it at the top of every
    page (app.py included), before importing anything else from the repo.
    True when it reloaded. Changing THIS file needs a Cloud reboot: it is the
    one module not reloaded."""
    global _loaded
    now = source_fingerprint()
    if now <= _loaded:
        return False
    with _lock:
        if now <= _loaded:                 # another session got here first
            return False
        reload_packages()
        _loaded = now
    return True
