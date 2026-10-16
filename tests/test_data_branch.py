"""edge/data_branch.py against a throwaway repo + bare remote: a publish lands
on the data branch, removes what a directory no longer holds, is a no-op when
nothing changed, never moves HEAD / main's index / the working tree, and
survives losing the push race to another writer (the phone)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from edge import data_branch as D


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    root = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        git(root, "config", k, v)
    (root / "code.py").write_text("x = 1\n")
    git(root, "add", "code.py")
    git(root, "commit", "-q", "-m", "code")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "origin", "main")
    (root / "data").mkdir()
    return root, remote


def files_on(root, ref="origin/data"):
    git(root, "fetch", "-q", "origin", "+refs/heads/data:refs/remotes/origin/data")
    return set(git(root, "ls-tree", "-r", "--name-only", ref).splitlines())


def test_publish_lands_on_data_and_leaves_main_alone(repo):
    root, _ = repo
    head = git(root, "rev-parse", "HEAD")
    (root / "code.py").write_text("x = 2  # an edit in progress\n")
    (root / "data" / "odds_snapshot_dfs_nba.json").write_text('{"a": 1}')
    assert D.publish(["data/odds_snapshot_dfs_nba.json"], "odds: dfs_nba snapshot", root=root)
    assert files_on(root) == {"data/odds_snapshot_dfs_nba.json"}
    assert git(root, "rev-parse", "HEAD") == head                 # main did not move
    assert git(root, "status", "--porcelain", "code.py") == "M code.py"   # edit untouched
    assert git(root, "diff", "--cached", "--name-only") == ""      # main's index untouched
    assert git(root, "log", "-1", "--format=%s", "origin/data") == "odds: dfs_nba snapshot"


def test_unchanged_publish_is_a_no_op(repo):
    root, _ = repo
    (root / "data" / "f.json").write_text("1")
    assert D.publish(["data/f.json"], "one", root=root)
    tip = git(root, "rev-parse", "origin/data")
    assert D.publish(["data/f.json"], "two", root=root)
    assert git(root, "rev-parse", "origin/data") == tip


def test_a_directory_publish_removes_pruned_files(repo):
    root, _ = repo
    d = root / "data" / "draftables_snapshot"
    d.mkdir()
    for gid in (1, 2, 3):
        (d / f"{gid}.json").write_text(str(gid))
    (root / "data" / "keep.json").write_text("k")
    assert D.publish(["data/draftables_snapshot", "data/keep.json"], "draftables", root=root)
    (d / "2.json").unlink()                                          # pruned at lock
    assert D.publish(["data/draftables_snapshot"], "draftables", root=root)
    assert files_on(root) == {"data/draftables_snapshot/1.json", "data/draftables_snapshot/3.json",
                              "data/keep.json"}


def test_losing_the_push_race_rebuilds_on_the_new_tip(repo, tmp_path):
    root, remote = repo
    (root / "data" / "a.json").write_text("a")
    assert D.publish(["data/a.json"], "a", root=root)
    # another writer (the phone's scan request) commits to data on the remote
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", "data", str(remote), str(other)], check=True)
    for k, v in (("user.email", "p@p"), ("user.name", "phone")):
        git(other, "config", k, v)
    (other / "data" / "scan_request.json").write_text('{"id": "x"}')
    git(other, "add", "data/scan_request.json")
    git(other, "commit", "-q", "-m", "scan request")
    git(other, "push", "-q", "origin", "data")
    (root / "data" / "b.json").write_text("b")
    assert D.publish(["data/b.json"], "b", root=root)
    assert files_on(root) == {"data/a.json", "data/b.json", "data/scan_request.json"}
    assert D.show("data/scan_request.json", root=root) == '{"id": "x"}'


def test_checkout_files_fills_a_fresh_clone_without_clobbering(repo, tmp_path):
    root, remote = repo
    (root / "data" / "a.json").write_text("new")
    (root / "data" / "b.json").write_text("b")
    assert D.publish(["data/a.json", "data/b.json"], "ab", root=root)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    (clone / "data").mkdir(exist_ok=True)
    (clone / "data" / "a.json").write_text("local edit")
    wrote = D.checkout_files(root=clone)
    assert wrote == ["data/b.json"]
    assert (clone / "data" / "a.json").read_text() == "local edit"
    assert git(clone, "status", "--porcelain") == "?? data/"


def test_main_ignores_exactly_the_published_feeds():
    """.gitignore's last block and data_branch.FEEDS must name the same paths:
    a feed main still tracks would be committed by both, one main ignores but
    no timer publishes would never reach the phone."""
    block = (Path(D.ROOT) / ".gitignore").read_text().split("re-includes one.\n")[1].split()
    assert sorted(p.rstrip("/") for p in block) == sorted(D.FEEDS)
