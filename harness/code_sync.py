"""Fast-forward the desktop checkout to origin/main -- code only, never forced.

Until 2026-10-16 the desktop picked up code pushed from elsewhere (a web
session, the phone) as a side effect: every timer ran `git pull --rebase
--autostash` on main before pushing its snapshot. Timers now publish to the
data branch (edge/data_branch.py) and never touch main, so that side effect is
gone and this is its explicit, safe replacement:

  * fetch origin/main;
  * if the checkout has LOCAL commits main does not (someone is mid-change),
    do nothing and say so -- `harness status` shows it;
  * otherwise `git merge --ff-only`, which git itself refuses when an
    incoming file is edited locally. No rebase, no autostash, no reset.

The arb agent re-execs itself when HEAD moves (scripts/arb_agent.py), so a
fast-forward here reaches the long-running process too.

    python3 -m harness run code-sync          # what the timer runs
"""
from __future__ import annotations

import subprocess
import sys

from harness.registry import ROOT


def git(*args, check=False):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          check=check)


def state() -> dict:
    """{behind, ahead, branch} against origin/main, from the last fetch."""
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    counts = git("rev-list", "--left-right", "--count", "HEAD...origin/main").stdout.split()
    ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
    return {"branch": branch, "ahead": ahead, "behind": behind}


def sync() -> int:
    from edge.git_lock import git_lock
    with git_lock():
        if git("fetch", "--quiet", "origin", "main").returncode:
            print("code-sync: fetch failed (offline?)")
            return 1
        s = state()
        if s["branch"] != "main":
            print(f"code-sync: checkout is on {s['branch']}, not main -- left alone")
            return 0
        if not s["behind"]:
            print("code-sync: up to date")
            return 0
        if s["ahead"]:
            print(f"code-sync: {s['ahead']} local commit(s) not on origin/main -- "
                  f"left alone ({s['behind']} behind). Push or rebase them by hand.")
            return 0
        r = git("merge", "--ff-only", "--quiet", "origin/main")
        if r.returncode:
            print(f"code-sync: fast-forward refused ({r.stderr.strip()[:200]}) -- "
                  "a local edit touches an incoming file")
            return 1
        print(f"code-sync: fast-forwarded {s['behind']} commit(s)")
        return 0


if __name__ == "__main__":
    sys.exit(sync())
