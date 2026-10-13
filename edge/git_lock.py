"""One git operation at a time in this checkout.

Several timers commit and push here on the same minute (draftables, NHL
lines, odds snapshots, buzz, the autolog, the arb agent). Unserialised, they
collide: at 12:59:45 on 2026-10-13 draftables-publish was inside
`pull --rebase --autostash` when nhl-lines-publish committed 3 s later; the
first never re-applied its autostash, the rebase was left "in progress", and
every uncommitted change in the tree (Adam's own PICKEM_MODEL.md edits, the
day's projection logs) sat in a stash commit instead of on disk until it was
noticed. At 12:00 the same race had already failed a push ("cannot lock ref").

    from edge.git_lock import git_lock
    with git_lock():
        ...add / commit / pull --rebase / push...

From a shell: `flock .git/edge_git.lock -c 'git pull --rebase --autostash && git push'`.
"""
from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / ".git" / "edge_git.lock"


@contextmanager
def git_lock(timeout: float = 300.0, path: Path = LOCK):
    """Hold the repo's git lock; waits up to `timeout` s, then raises TimeoutError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise TimeoutError(f"{path} held for more than {timeout:.0f}s")
                time.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
