"""edge/git_lock.py: two processes never run git in this checkout at once
(2026-10-13: concurrent timer pulls stranded an autostash of Adam's work)."""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from edge.git_lock import git_lock

HOLD = """
import sys, time
sys.path.insert(0, {root!r})
from pathlib import Path
from edge.git_lock import git_lock
with git_lock(path=Path({lock!r})):
    print("held", flush=True)
    time.sleep(1.5)
"""


def test_a_second_holder_waits_and_a_timeout_raises(tmp_path):
    from edge.git_lock import ROOT
    lock = tmp_path / "edge_git.lock"
    p = subprocess.Popen([sys.executable, "-c", HOLD.format(root=str(ROOT), lock=str(lock))],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    with pytest.raises(TimeoutError):
        with git_lock(timeout=0.3, path=lock):
            pass
    t = time.time()
    with git_lock(timeout=10, path=lock):
        waited = time.time() - t
    p.wait(timeout=10)
    assert waited > 0.5
