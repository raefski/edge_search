"""edge/pickem_cbs.ensure_chromium_libs -- the loader path a systemd unit lacks.

WHY THIS IS TESTED AT ALL
Chromium's shared libraries are not installed as system packages on the
desktop that runs the capture timers; they were unpacked into
~/.local/chromium-deps and reached through an LD_LIBRARY_PATH exported from
~/.bashrc. ~/.bashrc runs for interactive shells only, so every Playwright
call in this repo worked by hand and failed 100% of the time under
deploy/pickem-capture@.service -- silently, because the CBS fetch is a
soft-failing `ExecStartPre=-`.

The bug was invisible from inside the app: the unit reported success, and
the only symptom was pickem_current_week.csv never losing its `provisional`
notes. These tests pin the environment contract so it cannot regress into
that shape again.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import pickem_cbs


def _fake_deps(tmp_path: Path) -> Path:
    root = tmp_path / "chromium-deps"
    (root / "usr" / "lib" / "x86_64-linux-gnu").mkdir(parents=True)
    (root / "lib" / "x86_64-linux-gnu").mkdir(parents=True)
    return root


def test_it_adds_the_unpacked_libs_when_the_env_has_none(monkeypatch, tmp_path):
    """The systemd case exactly: a process that starts with no
    LD_LIBRARY_PATH at all must end up with one."""
    deps = _fake_deps(tmp_path)
    monkeypatch.setattr(pickem_cbs, "CHROMIUM_DEPS_DIR", deps)
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    pickem_cbs.ensure_chromium_libs()

    got = os.environ["LD_LIBRARY_PATH"].split(":")
    assert str(deps / "usr" / "lib" / "x86_64-linux-gnu") in got
    assert str(deps / "lib" / "x86_64-linux-gnu") in got


def test_it_preserves_whatever_was_already_there(monkeypatch, tmp_path):
    """An interactive shell already sets this, and may set other things in
    it too. Prepending rather than replacing is what keeps the fix from
    breaking the case that already worked."""
    deps = _fake_deps(tmp_path)
    monkeypatch.setattr(pickem_cbs, "CHROMIUM_DEPS_DIR", deps)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/something/lib")

    pickem_cbs.ensure_chromium_libs()

    assert os.environ["LD_LIBRARY_PATH"].endswith("/opt/something/lib")
    assert str(deps / "usr" / "lib" / "x86_64-linux-gnu") in os.environ["LD_LIBRARY_PATH"]


def test_it_is_idempotent(monkeypatch, tmp_path):
    """Called before every launch, not once at import -- so calling it twice
    must not grow the variable without bound."""
    deps = _fake_deps(tmp_path)
    monkeypatch.setattr(pickem_cbs, "CHROMIUM_DEPS_DIR", deps)
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    pickem_cbs.ensure_chromium_libs()
    once = os.environ["LD_LIBRARY_PATH"]
    pickem_cbs.ensure_chromium_libs()

    assert os.environ["LD_LIBRARY_PATH"] == once


def test_it_is_a_noop_where_chromium_is_installed_normally(monkeypatch, tmp_path):
    """On a machine with libnss3 from apt there is no such directory, and
    this must not invent an LD_LIBRARY_PATH that shadows the system one."""
    monkeypatch.setattr(pickem_cbs, "CHROMIUM_DEPS_DIR", tmp_path / "absent")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    pickem_cbs.ensure_chromium_libs()

    assert "LD_LIBRARY_PATH" not in os.environ


def test_both_playwright_launch_paths_call_it():
    """fetch_pool_text is the automated one and fetch_public_odds the manual
    one. A fix applied to only one of two launch sites is how this class of
    bug survives its own fix."""
    src = (ROOT / "edge" / "pickem_cbs.py").read_text()
    for fn in ("def fetch_public_odds", "def fetch_pool_text"):
        body = src.split(fn, 1)[1].split("\nwith sync_playwright")[0]
        body = body[:body.find("\ndef ")] if "\ndef " in body else body
        assert "ensure_chromium_libs()" in body, f"{fn} launches without the loader fix"
