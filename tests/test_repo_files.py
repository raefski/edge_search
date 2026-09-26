"""edge/repo_files.py: main's copy of a data file vs the deployed disk copy.

Added 2026-09-26, after Streamlit Cloud sat on a 39-hour-old deploy while the
desktop's pushes piled up on main. The rule under test is the one in the
module docstring: GitHub's copy wins when main's newest commit touching the
file is newer than the disk copy's mtime (less a margin), and every GitHub
failure falls back to the disk copy with a reason rather than raising.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from edge import repo_files


class _Resp:
    def __init__(self, status: int, payload=None, text: str | None = None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            from edge.arb.http import HTTPError
            raise HTTPError(f"{self.status_code}", self)


class FakeGitHub:
    """main has `body` at commit `sha`, committed `committed_at`."""

    def __init__(self, body="from github", sha="c0ffee", committed_at=None,
                 commits_fail=False, raw_fail=False):
        self.body, self.sha = body, sha
        self.committed_at = committed_at or datetime.now(timezone.utc)
        self.commits_fail, self.raw_fail = commits_fail, raw_fail
        self.asked: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.asked.append(url)
        if "/commits" in url:
            if self.commits_fail:
                return _Resp(502)
            when = self.committed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            return _Resp(200, [{"sha": self.sha,
                                "commit": {"committer": {"date": when}}}])
        if self.raw_fail:
            return _Resp(500, text="")
        return _Resp(200, text=self.body)

    def commits_asked(self) -> int:
        return sum("/commits" in u for u in self.asked)

    def raws_asked(self) -> int:
        return sum("raw.githubusercontent.com" in u for u in self.asked)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """tmp_path standing in for the repo root, with credentials set."""
    monkeypatch.setattr(repo_files, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_REPO", "a/b")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_x")
    (tmp_path / "data").mkdir()
    return tmp_path


def _deployed(path, text="from disk", hours_ago=39.0):
    """A disk copy as a deploy that many hours ago would have left it."""
    path.write_text(text)
    t = time.time() - hours_ago * 3600
    os.utime(path, (t, t))
    return path


def test_a_push_since_the_deploy_is_read_off_github(repo):
    f = _deployed(repo / "data" / "odds.json")
    gh = FakeGitHub()
    found = repo_files.read(f, session=gh)
    assert (found.data, found.source, found.problem) == ("from github", "github", "")
    assert gh.asked[-1] == "https://raw.githubusercontent.com/a/b/c0ffee/data/odds.json"


def test_a_disk_copy_newer_than_main_is_not_shadowed(repo):
    """The desktop writes a file before committing it: an unpushed change is
    newer than main and must win, without downloading main's copy at all."""
    f = _deployed(repo / "data" / "odds.json", "unpushed", hours_ago=0)
    gh = FakeGitHub(committed_at=datetime.now(timezone.utc) - timedelta(hours=2))
    found = repo_files.read(f, session=gh)
    assert (found.data, found.source) == ("unpushed", "disk")
    assert gh.raws_asked() == 0


def test_a_commit_just_before_the_deploy_still_reads_github(repo):
    """The margin: a deploy that cloned between a commit and its push has an
    mtime AFTER a commit it does not contain."""
    f = _deployed(repo / "data" / "odds.json", hours_ago=0)
    gh = FakeGitHub(committed_at=datetime.now(timezone.utc) - timedelta(minutes=2))
    assert repo_files.read(f, session=gh).source == "github"


def test_a_file_only_on_github_is_found(repo):
    """A DraftKings slate priced after the last deploy has no disk copy."""
    found = repo_files.read(repo / "data" / "draftables_snapshot" / "999.json",
                            session=FakeGitHub(body="[]"))
    assert found.json() == [] and found.source == "github"


def test_github_is_asked_once_per_check_window_and_downloads_once_per_commit(repo):
    f = _deployed(repo / "data" / "odds.json")
    gh = FakeGitHub()
    for _ in range(3):
        assert repo_files.read(f, session=gh).data == "from github"
    assert (gh.commits_asked(), gh.raws_asked()) == (1, 1)

    repo_files._checks.clear()              # the window passes...
    repo_files.read(f, session=gh)
    assert (gh.commits_asked(), gh.raws_asked()) == (2, 1), \
        "the same commit must not be downloaded twice"

    repo_files._checks.clear()              # ...and main moves on
    gh.sha, gh.body = "d00d", "newer"
    assert repo_files.read(f, session=gh).data == "newer"
    assert gh.raws_asked() == 2


def test_github_down_falls_back_to_disk_and_says_why(repo):
    f = _deployed(repo / "data" / "odds.json")
    found = repo_files.read(f, session=FakeGitHub(commits_fail=True))
    assert (found.data, found.source) == ("from disk", "disk")
    assert "502" in found.problem


def test_a_failed_download_falls_back_and_is_not_retried_every_rerun(repo):
    f = _deployed(repo / "data" / "odds.json")
    gh = FakeGitHub(raw_fail=True)
    first = repo_files.read(f, session=gh)
    assert first.source == "disk" and "500" in first.problem
    raws = gh.raws_asked()
    second = repo_files.read(f, session=gh)
    assert second.source == "disk" and second.problem
    assert gh.raws_asked() == raws, "a failure must back off, not hammer GitHub"


def test_without_credentials_github_is_never_asked(repo, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    f = _deployed(repo / "data" / "odds.json")
    gh = FakeGitHub()
    assert repo_files.read(f, session=gh).source == "disk"
    assert gh.asked == []


def test_a_path_outside_the_repo_is_disk_only(repo, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "x.json"
    elsewhere.write_text("local")
    gh = FakeGitHub()
    assert repo_files.read(elsewhere, session=gh).data == "local"
    assert gh.asked == []


def test_missing_everywhere_is_none(repo):
    assert repo_files.read(repo / "data" / "nope.json",
                           session=FakeGitHub(commits_fail=True)) is None


def test_credentials_come_from_streamlit_secrets_when_the_app_loaded_it(monkeypatch):
    """Cloud keeps the pair in Streamlit secrets, not the environment."""
    import sys
    import types
    fake = types.ModuleType("streamlit")
    fake.secrets = {"GITHUB_REPO": "a/b", "GITHUB_TOKEN": "github_pat_x"}
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    assert repo_files.credentials() == ("a/b", "github_pat_x")


def test_a_refused_token_is_retried_anonymously():
    calls = []

    class Session:
        def get(self, url, headers=None, **k):
            calls.append(headers)
            return _Resp(404, text="") if headers else _Resp(200, text="ok")

    assert repo_files.fetch("a/b", "github_pat_x", "c0ffee", "data/x", session=Session()) == "ok"
    assert calls[0] and calls[1] is None


def test_cloud_prices_come_from_mains_odds_snapshot(repo, monkeypatch):
    """scraped_client on a host with no odds store -- Streamlit Cloud -- serves
    main's snapshot. The deployed one here is 39h old, past the 6h bound, so
    reading it would raise StaleOdds instead of pricing anything."""
    import edge.odds.cli as cli
    import edge.odds.publish as publish
    monkeypatch.setattr(cli, "ROOT", repo)          # no data/odds.db
    monkeypatch.setattr(publish, "ROOT", repo)
    now = datetime.now(timezone.utc)
    old = {"profile": "dfs_nfl", "events": [],
           "generated_at": (now - timedelta(hours=39)).isoformat()}
    new = {"profile": "dfs_nfl", "generated_at": now.isoformat(),
           "events": [{"id": "e1", "sport_key": "americanfootball_nfl",
                       "commence_time": now.isoformat(), "home_team": "H",
                       "away_team": "A", "bookmakers": []}]}
    _deployed(repo / "data" / "odds_snapshot_dfs_nfl.json", json.dumps(old))
    monkeypatch.setattr(repo_files, "http", FakeGitHub(body=json.dumps(new)))

    client = cli.scraped_client("americanfootball_nfl", "dfs")
    assert client.generated_at == new["generated_at"]
    assert [e["id"] for e in client.get_events("americanfootball_nfl")] == ["e1"]
    assert client.problem == ""


def test_a_slate_priced_after_the_deploy_still_gets_its_player_pool(repo, monkeypatch):
    """Cloud is 403'd by DraftKings' draftables endpoint, so it reads the
    desktop's pushed pool -- which, for a slate DK priced after the last
    deploy, exists only on main."""
    from edge import dfs

    def blocked(*a, **k):
        raise RuntimeError("403")

    monkeypatch.setattr(dfs, "_get", blocked)
    monkeypatch.setattr(dfs, "_SNAP_DIR", repo / "data" / "draftables_snapshot")
    pool = [{"displayName": "Driver", "salary": 9000}]
    monkeypatch.setattr(repo_files, "http", FakeGitHub(body=json.dumps(pool)))

    assert dfs._draftables_raw(424242) == pool
    assert dfs.LAST_DRAFTABLES_SOURCE.startswith("snapshot ")
