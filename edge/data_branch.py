"""Publish machine-written data to the `data` branch -- never to main.

WHY A SEPARATE BRANCH
Until 2026-10-16 every timer committed its snapshot to main: ~180 commits a
day, 98% of main's history (1,363 "odds: dfs_nfl snapshot" alone). Each push
first ran `git pull --rebase --autostash` IN THE CHECKOUT WHERE CODE IS
EDITED, so a timer could strand an autostash mid-rebase (it did, 2026-10-13),
push whatever local commit happened to sit on main, and a web session's branch
fell ~140 commits behind main per day -- all of it data. Code and data now
live apart:

    main   code, docs, and the few files a human-run job writes (contest
           exports, contest_meta.json, model caches)
    data   everything a timer publishes for the phone apps (odds / draftables /
           NHL lines / NBA lineups snapshots, buzz, pick'em lines, arb snapshot,
           scan requests) and the autolog's projection logs -- FEEDS below

HOW A TIMER PUBLISHES WITHOUT TOUCHING THE CHECKOUT
publish() builds the commit with git plumbing: a private index file seeded
from the data branch's tip, the named files hashed in from disk, write-tree,
commit-tree, push. HEAD, the working tree and main's index are never read or
written -- there is no checkout, no rebase and no autostash, so a timer and
someone editing code on main cannot interfere with each other at all. The
repo's git lock (edge/git_lock.py) still serialises publishers.

The only other writer is the phone's scan-request button (GitHub's contents
API, edge/arb/scan_request.py), so a push that loses that race re-fetches and
rebuilds on the new tip, up to PUSH_TRIES times.

READING IT BACK
The desktop reads its own disk, as always. Streamlit Cloud reads through
edge/repo_files.py, which asks for the newest commit on DATA_BRANCH first.
A fresh clone (a web session) gets the files with
`python3 -m harness data-pull` (checkout_files below).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_BRANCH = "data"
REMOTE = "origin"
PUSH_TRIES = 3

#: Every path a timer publishes (a directory means every file under it), as
#: committed on the data branch. Kept here so main's .gitignore, the migration
#: and `harness data-pull` agree on one list.
FEEDS: tuple[str, ...] = (
    "data/odds_snapshot_*.json",          # scripts/odds_collect.py --push, mma_odds_capture.py
    "data/draftables_snapshot",           # scripts/draftables_publish.py (pruned as slates lock)
    "data/arb_snapshot.json",             # scripts/arb_agent.py
    "data/scan_request.json",             # the phone (edge/arb/scan_request.put_request)
    "data/nhl_lines_snapshot.json",       # scripts/nhl_lines_publish.py
    "data/nba_lineups_snapshot.json",     # harness/nba_scratch.py
    "data/nba_player_history.json",       # scripts/nba_history_publish.py
    "data/buzz_*.csv",                    # scripts/buzz_{nfl,nhl,ncaaf}.py
    "data/pickem_current_week.csv",       # scripts/pickem_pool_fetch.py
    "data/pickem_line_log.csv",           # scripts/pickem_capture.py
    "data/dfs_proj_log*.csv",             # scripts/dfs_autolog.py (+ CLI builds)
    "data/dfs_lineups_*.csv",             # scripts/dfs_autolog.py
)


class PublishError(RuntimeError):
    pass


def _git(*args, env=None, check=True, root: Path = ROOT, input_=None) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                       env=env, input=input_)
    if check and r.returncode != 0:
        raise PublishError(f"git {' '.join(args[:2])}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def _sha(ref: str, root: Path) -> str | None:
    return _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False, root=root) or None


def _is_ancestor(a: str, b: str, root: Path) -> bool:
    return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", a, b],
                          capture_output=True).returncode == 0


def _tip(root: Path, fetch: bool) -> str | None:
    """The commit to build on: the remote data branch as just fetched, unless
    the local ref is ahead of it (a publish made offline, not pushed yet)."""
    if fetch:
        subprocess.run(["git", "-C", str(root), "fetch", "--quiet", REMOTE,
                        f"+refs/heads/{DATA_BRANCH}:refs/remotes/{REMOTE}/{DATA_BRANCH}"],
                       capture_output=True, text=True)
    remote = _sha(f"refs/remotes/{REMOTE}/{DATA_BRANCH}", root)
    local = _sha(f"refs/heads/{DATA_BRANCH}", root)
    if remote and local and remote != local and _is_ancestor(remote, local, root):
        return local
    return remote or local


def _files(rel: str, root: Path) -> list[str]:
    """Repo-relative files for one path: itself, or everything under a directory."""
    p = root / rel
    if p.is_dir():
        return sorted(str(f.relative_to(root)) for f in p.rglob("*") if f.is_file())
    return [rel] if p.is_file() else []


def build_commit(paths: list, message: str, root: Path = ROOT, fetch: bool = True) -> str | None:
    """The data-branch commit that makes `paths` match the disk, or None if it
    already does. Writes objects and nothing else -- no ref is moved."""
    rels = [str(Path(p).resolve().relative_to(root.resolve())) if Path(p).is_absolute()
            else str(p) for p in paths]
    parent = _tip(root, fetch)
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if parent:
            _git("read-tree", parent, env=env, root=root)
        for rel in rels:
            on_disk = set(_files(rel, root))
            # whatever the branch holds under this path that is gone from disk
            # (a pruned draftables snapshot, a deleted log) is removed with it
            held = set(_git("ls-files", "--", rel, env=env, root=root).splitlines())
            for gone in sorted(held - on_disk):
                _git("update-index", "--force-remove", "--", gone, env=env, root=root)
            for f in sorted(on_disk):
                blob = _git("hash-object", "-w", "--", f, root=root)
                _git("update-index", "--add", "--cacheinfo", f"100644,{blob},{f}", env=env, root=root)
        tree = _git("write-tree", env=env, root=root)
    if parent and tree == _git("rev-parse", f"{parent}^{{tree}}", root=root):
        return None
    args = ["commit-tree", tree, "-m", message]
    if parent:
        args[2:2] = ["-p", parent]
    return _git(*args, root=root)


def publish(paths: list, message: str, push: bool = True, root: Path = ROOT) -> bool:
    """Commit `paths` (files or directories, as they are on disk) to the data
    branch and push it. True on success or when nothing changed."""
    from edge.git_lock import git_lock
    with git_lock(path=root / ".git" / "edge_git.lock"):
        for attempt in range(PUSH_TRIES):
            try:
                commit = build_commit(paths, message, root=root, fetch=push)
            except PublishError as exc:
                print(f"  ! data branch: {exc}")
                return False
            if commit is None:
                print("  unchanged -- nothing to publish")
                return True
            _git("update-ref", f"refs/heads/{DATA_BRANCH}", commit, root=root)
            if not push:
                return True
            r = subprocess.run(["git", "-C", str(root), "push", "--quiet", REMOTE,
                                f"{commit}:refs/heads/{DATA_BRANCH}"], capture_output=True, text=True)
            if r.returncode == 0:
                _git("update-ref", f"refs/remotes/{REMOTE}/{DATA_BRANCH}", commit, root=root)
                return True
            # lost a race (the phone's scan request, usually): rebuild on the new tip
            print(f"  ! push failed (attempt {attempt + 1}): {r.stderr.strip()[:200]}")
            time.sleep(3 * (attempt + 1))
    return False


def publish_one(path, label: str) -> bool:
    """One snapshot (a file, or a directory as it is on disk), committed as
    "data: <label>". Every timer's publish goes through here."""
    return publish([path], f"data: {label}")


def show(rel: str, root: Path = ROOT, fetch: bool = True) -> str | None:
    """A file as it is on the REMOTE data branch (e.g. the phone's scan request)."""
    tip = _tip(root, fetch)
    if not tip:
        return None
    r = subprocess.run(["git", "-C", str(root), "show", f"{tip}:{rel}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def checkout_files(root: Path = ROOT, fetch: bool = True, overwrite: bool = False) -> list[str]:
    """Write the data branch's files into the working tree (a fresh clone, a
    web session). Never touches the index or HEAD. Leaves a file that already
    exists alone unless `overwrite` -- on the desktop, disk is the newer copy."""
    tip = _tip(root, fetch)
    if not tip:
        return []
    out = []
    for rel in _git("ls-tree", "-r", "--name-only", tip, root=root).splitlines():
        dest = root / rel
        if dest.exists() and not overwrite:
            continue
        blob = subprocess.run(["git", "-C", str(root), "show", f"{tip}:{rel}"], capture_output=True)
        if blob.returncode == 0:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob.stdout)
            out.append(rel)
    return out
