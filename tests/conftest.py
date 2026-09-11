"""Test-wide guards.

WHY THE FAILURE LOG NEEDS ONE
data/pickem_capture_failures.log is production data, not a scratch file:
pages/4_*_Pickem.py renders every line in it as "a market reading that no
longer exists", which is the strongest alarm the pick'em page has. Anything
that appends to it is claiming a deadline was permanently missed.

scripts/pickem_pool_fetch.py writes there on its failure path -- and its
tests exercise exactly that path. Without this fixture, running the suite
appends two entirely fictional missed-capture warnings to the real file and
they show up on the phone. That happened once, on 2026-09-10, within
minutes of the failure recording being added.

Redirecting the module attribute (rather than trusting each test to
remember) is what makes it structural: a test added later cannot reintroduce
the problem by forgetting.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _never_touch_the_real_failure_log(tmp_path, monkeypatch):
    """Redirect EVERY writer of the failures log, not just one.

    There are three module attributes naming that file -- the shared
    implementation in edge.pickem_log and the two scripts that wrap it -- and
    a guard that covers only some of them is a guard that stops working the
    next time a writer is added. That is not hypothetical: this fixture was
    written for scripts.pickem_pool_fetch alone, and scripts.pickem_capture
    became a writer the same week.
    """
    redirected = tmp_path / "pickem_capture_failures.log"
    for mod, attr in (("edge.pickem_log", "FAILURES_LOG"),
                      ("scripts.pickem_pool_fetch", "FAILURES_LOG"),
                      ("scripts.pickem_capture", "FAILURES_LOG")):
        try:
            import importlib
            monkeypatch.setattr(importlib.import_module(mod), attr,
                                redirected, raising=False)
        except Exception:                               # noqa: BLE001
            continue
