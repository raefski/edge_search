"""scripts/pickem_pool_fetch.py -- the automated replacement for the
copy-paste half of the Tuesday step (and, via deploy/pickem-capture@.service,
every other deadline's CBS reading too).

Deliberately does not touch a real browser or a real session: fetch_pool_text
itself is covered by tests/test_pickem_cbs.py against a fake playwright. What
this file pins is the CONTRACT between the fetch and the write -- most of all
that a SessionExpired must reach the CSV as "nothing written", not as a
retry or a guess.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from edge.pickem_cbs import SessionExpired
from scripts import pickem_pool_fetch as fetch_cli
from scripts import pickem_pool_import as pool_import


def test_a_session_expired_error_writes_nothing_and_exits_nonzero(monkeypatch):
    """The load-bearing property: an expired session must not touch the CSV
    at all -- run() is never even called -- so a capture that follows can
    tell "the fetch didn't happen" from "the fetch said zero games"."""
    def _raise(*_a, **_k):
        raise SessionExpired("landed on /join")
    monkeypatch.setattr(fetch_cli, "fetch_pool_text", _raise)

    called = []
    monkeypatch.setattr(pool_import, "run", lambda *a, **k: called.append((a, k)))
    monkeypatch.setattr(sys, "argv", ["pickem_pool_fetch.py", "--week", "3", "--write"])

    with pytest.raises(SystemExit) as exc:
        fetch_cli.main()
    assert exc.value.code == 1
    assert not called, "run() must not be called when the session is dead"


def test_a_live_fetch_is_handed_to_the_shared_import_logic(monkeypatch):
    """No second parser: whatever fetch_pool_text returns goes straight into
    pickem_pool_import.run, the same function a manual paste calls."""
    monkeypatch.setattr(fetch_cli, "fetch_pool_text", lambda *_a, **_k: "PICKS TEXT")

    called = []
    monkeypatch.setattr(pool_import, "run",
                        lambda text, week, no_enrich=False, write=False:
                        called.append((text, week, no_enrich, write)))
    monkeypatch.setattr(sys, "argv",
                        ["pickem_pool_fetch.py", "--week", "3", "--write"])

    fetch_cli.main()
    assert called == [("PICKS TEXT", 3, False, True)]


def test_dry_run_is_the_default_same_as_every_other_script_here(monkeypatch):
    monkeypatch.setattr(fetch_cli, "fetch_pool_text", lambda *_a, **_k: "PICKS TEXT")
    called = []
    monkeypatch.setattr(pool_import, "run",
                        lambda text, week, no_enrich=False, write=False:
                        called.append(write))
    monkeypatch.setattr(sys, "argv", ["pickem_pool_fetch.py", "--week", "3"])

    fetch_cli.main()
    assert called == [False]


def test_week_auto_resolves_through_current_week(monkeypatch):
    monkeypatch.setattr(fetch_cli, "current_week", lambda: 7)
    monkeypatch.setattr(fetch_cli, "fetch_pool_text", lambda *_a, **_k: "PICKS TEXT")
    called = []
    monkeypatch.setattr(pool_import, "run",
                        lambda text, week, no_enrich=False, write=False:
                        called.append(week))
    monkeypatch.setattr(sys, "argv", ["pickem_pool_fetch.py", "--week", "auto"])

    fetch_cli.main()
    assert called == [7]


def test_week_auto_outside_season_refuses_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(fetch_cli, "current_week", lambda: None)
    called = []
    monkeypatch.setattr(pool_import, "run", lambda *a, **k: called.append((a, k)))
    monkeypatch.setattr(sys, "argv", ["pickem_pool_fetch.py", "--week", "auto"])

    with pytest.raises(SystemExit) as exc:
        fetch_cli.main()
    assert exc.value.code == 1
    assert not called
