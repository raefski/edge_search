"""The Streamlit Cloud module-reload guard, edge/dfs_pagereload.py.

WHAT THESE EXIST TO CATCH
A deployed page that serves the commit which added a thing, while running the
stale module that does not have it:

    2026-09-24, deployed edge_search, NCAAF page
      KeyError: unknown profile 'dfs_ncaaf';
                have ['arb','dfs_mlb','dfs_nba','dfs_nfl','pickem_nfl']

The guard existed and ran. It reloaded `edge.dfs*` and the sport module and
nothing else, so `edge.odds.profiles` -- where the new profile lives -- stayed
stale. These tests pin the two invariants that failure violated.

NOTHING HERE CALLS reload_packages() ON LIVE MODULES, deliberately. Reloading
rebinds classes, so edge.odds.source.StaleOdds becomes a different object and
every other test's `pytest.raises(StaleOdds)` silently stops matching -- which
cost five unrelated failures in test_odds_store.py the first time these were
written. The matcher is a pure function precisely so it can be tested directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

from edge import dfs_pagereload as pr


def test_the_odds_package_is_covered():
    """The package the NCAAF outage was actually in.

    A new sport adds a collection profile to edge/odds/profiles.py and may
    touch no edge/dfs*.py at all, so this is the prefix most likely to matter
    and was the one missing.
    """
    assert "edge.odds" in pr.DFS_PACKAGES
    assert pr.matches("edge.odds.profiles")


def test_sibling_modules_match_as_well_as_subpackages():
    """Two different separators, and neither is redundant."""
    assert pr.matches("edge.odds.profiles")     # "." — a subpackage
    assert pr.matches("edge.dfs_ladder")        # "_" — a sibling module
    assert pr.matches("edge.nascar_sim")
    assert pr.matches("edge.dfs")               # the prefix itself


def test_the_matcher_does_not_swallow_unrelated_names():
    """A plain startswith would be wrong in the other direction."""
    assert not pr.matches("edge.arb.engine")
    assert not pr.matches("edge.pickem")
    assert not pr.matches("edgecase.dfs")
    assert not pr.matches("edge.oddsmath")      # NOT edge.odds


def test_the_fingerprint_watches_everything_the_reload_touches():
    """The same bug one level up, and the subtler half of it.

    The reload is gated on the fingerprint via st.cache_resource. If the
    fingerprint watches a narrower set of files than the reload covers, a
    change outside it never re-fires the reload at all -- which is exactly
    what would have happened here even with the package list widened:
    edge/odds/profiles.py changed and no edge/dfs*.py did.
    """
    root = Path(pr.ROOT)
    watched = {p.resolve() for g in pr.DFS_GLOBS for p in root.glob(g)}
    assert watched, "the fingerprint globs match nothing at all"

    for name in sorted(k for k in sys.modules if k.startswith("edge.")):
        if not pr.matches(name):
            continue
        src = getattr(sys.modules.get(name), "__file__", None)
        if not src:
            continue
        assert Path(src).resolve() in watched, f"{name} reloaded but unwatched"


def test_every_dfs_page_module_is_covered():
    """Import what the pages import, then require the guard to cover it.

    This is the test that would have caught the outage: edge.odds.cli is what
    every DFS page calls scraped_client from, and it reaches
    edge.odds.profiles.
    """
    import edge.dfs_ladder          # noqa: F401
    import edge.dfs_run_ncaaf       # noqa: F401
    import edge.dfs_run_nascar      # noqa: F401
    import edge.odds.cli            # noqa: F401
    import edge.odds.profiles       # noqa: F401

    for name in ("edge.dfs_ladder", "edge.dfs_run_ncaaf", "edge.dfs_run_nascar",
                 "edge.odds.cli", "edge.odds.profiles", "edge.ncaaf",
                 "edge.nascar", "edge.nascar_sim"):
        assert pr.matches(name), name


def test_fingerprint_is_a_real_number_and_ignores_a_dead_glob():
    assert pr.source_fingerprint() > 0
    assert pr.source_fingerprint(("no/such/glob/*.py",)) == 0.0
