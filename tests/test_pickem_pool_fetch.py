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


def test_a_browser_that_cannot_start_is_recorded_not_swallowed(monkeypatch, tmp_path):
    """The regression this file exists for after 2026-09-10.

    The unit runs this on `ExecStartPre=-`, which throws the exit status
    away -- so a failure that only shows up as a non-zero exit is invisible.
    For a year of Sundays that is indistinguishable from success. Anything
    that stops the fetch has to leave a line in the file the page reads.
    """
    log = tmp_path / "pickem_capture_failures.log"
    monkeypatch.setattr(fetch_cli, "FAILURES_LOG", log)

    def _boom(*_a, **_k):
        raise RuntimeError("chrome-headless-shell: libnspr4.so: cannot open "
                           "shared object file")
    monkeypatch.setattr(fetch_cli, "fetch_pool_text", _boom)

    called = []
    monkeypatch.setattr(pool_import, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(sys, "argv", ["pickem_pool_fetch.py", "--week", "3", "--write"])

    with pytest.raises(SystemExit) as exc:
        fetch_cli.main()

    assert exc.value.code == 1
    assert called == [], "a failed fetch must never reach the CSV writer"
    assert log.exists(), "a failed fetch left no trace where the page looks"
    line = log.read_text().strip()
    assert "cbs-pool-fetch" in line
    assert "libnspr4" in line, "the line has to say what actually broke"


def test_an_expired_session_is_recorded_too(monkeypatch, tmp_path):
    """Same channel for the expected failure as for the unexpected one --
    a session silently expiring mid-season is the likeliest way this stops
    working, and it must not be the quietest."""
    log = tmp_path / "pickem_capture_failures.log"
    monkeypatch.setattr(fetch_cli, "FAILURES_LOG", log)
    monkeypatch.setattr(fetch_cli, "fetch_pool_text",
                        lambda *a, **k: (_ for _ in ()).throw(SessionExpired("landed on /join")))
    monkeypatch.setattr(sys, "argv", ["pickem_pool_fetch.py", "--week", "3", "--write"])

    with pytest.raises(SystemExit):
        fetch_cli.main()

    assert "session expired" in log.read_text()


def test_recording_a_failure_never_raises(monkeypatch, tmp_path):
    """It runs on the failure path. A log that cannot be written must not
    replace the real error with a confusing second one."""
    monkeypatch.setattr(fetch_cli, "FAILURES_LOG", tmp_path / "nope" / "x.log")
    (tmp_path / "nope").write_text("not a directory")
    fetch_cli.record_failure("anything")      # must not raise
