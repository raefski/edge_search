"""Read a committed data file as it is on GitHub's main, not as it was deployed.

Streamlit Cloud serves every page off the commit it last deployed, and its
redeploy-on-push has twice stranded the app on days-old data while the
desktop's pushes sat on main: 9/14 during a push storm, then 39 hours from
9/24 to 9/26 with no storm at all. The arbitrage page started reading its
snapshot straight off GitHub on 9/26; this is the same fix for everything else
the desktop pushes -- the odds snapshots every DFS and pick'em page prices
from, the DraftKings player pools, and pick'em's CBS lines and line log.

WHICH COPY WINS
GitHub's, when main's newest commit touching the file is newer than the disk
copy's mtime less MTIME_MARGIN_SECONDS. On Cloud the disk copy is a checkout,
so its mtime is when it was deployed and any later push wins. On a machine
that WRITES these files (the desktop), a file is written before it is
committed, so an unpushed change stays newer than main and is never shadowed.
The margin covers a deploy that cloned between a commit and its push; what it
costs is at most one redundant download of an identical file.

NO CREDENTIALS, NO GITHUB
Only used with GITHUB_REPO and GITHUB_TOKEN set (environment, or Streamlit
secrets -- the pair the arbitrage page's scan-request button already uses).
The desktop has neither, so every desktop script reads its own disk exactly
as before. A path outside the repo (a test's tmp_path) is always disk-only.

THE TWO REQUESTS
Which commit is newest comes from the commits API: ~4KB, ~0.6s, asked at most
once per CHECK_SECONDS per file per process. The file itself comes from
raw.githubusercontent.com pinned to that commit's sha -- the contents API's
raw media type is served uncompressed (a 15.7MB snapshot was still downloading
after 100s, measured 2026-09-26) where this host gzips it, and a sha-pinned
URL cannot be served stale by the CDN in front of it.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from edge.arb import http

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
CHECK_SECONDS = 60
MTIME_MARGIN_SECONDS = 600


# --------------------------------------------------------------------------
# GitHub -- the only I/O besides the disk read
# --------------------------------------------------------------------------
def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _parse_iso(value) -> datetime | None:
    # GitHub writes "2026-09-26T14:09:42Z"; fromisoformat only takes the "Z"
    # from Python 3.11, and Cloud's Python is not pinned here.
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def latest_commit(repo: str, token: str, path: str, branch: str = "main",
                  session=None) -> tuple[str, datetime | None] | None:
    """(sha, committed at) of the newest commit on `branch` touching `path`,
    or None if it has never been committed -- GitHub."""
    s = session or http
    r = s.get(f"{API}/repos/{repo}/commits",
              params={"path": path, "sha": branch, "per_page": 1},
              headers=_headers(token), timeout=10)
    r.raise_for_status()
    rows = r.json() or []
    if not rows:
        return None
    committer = (rows[0].get("commit") or {}).get("committer") or {}
    return rows[0].get("sha"), _parse_iso(committer.get("date"))


def fetch(repo: str, token: str, ref: str, path: str, session=None) -> str:
    """`path`'s text as of commit `ref` -- GitHub.

    A token raw.githubusercontent.com will not honour gets a 404, even on a
    public repo that needs no token at all (seen live 2026-09-26), so a 4xx is
    retried once anonymously: a public repo still answers that, and a private
    one fails either way.
    """
    s = session or http
    url = f"{RAW}/{repo}/{ref}/{path}"
    r = s.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code in (401, 403, 404):
        r = s.get(url, timeout=60)
    r.raise_for_status()
    return r.text


# --------------------------------------------------------------------------
# the reader
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Found:
    data: str
    updated_at: datetime        # commit time from GitHub, mtime from disk
    source: str                 # "github" or "disk"
    problem: str = ""           # why GitHub was tried and not used, else ""

    def json(self):
        return json.loads(self.data)


def credentials() -> tuple[str, str]:
    repo = os.environ.get("GITHUB_REPO", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    # Only if the app already imported it: a desktop script must not pay for
    # importing streamlit just to learn it has no secrets.
    st = sys.modules.get("streamlit")
    if st is not None and not (repo and token):
        try:
            repo = repo or str(st.secrets.get("GITHUB_REPO", "") or "").strip()
            token = token or str(st.secrets.get("GITHUB_TOKEN", "") or "").strip()
        except Exception:                                   # noqa: BLE001
            pass     # st.secrets raises outright when no secrets file exists
    return repo, token


# Process-wide, like the Streamlit caches the pages use: every session and
# rerun shares one check per file per CHECK_SECONDS, and one body per file.
_lock = threading.Lock()
_checks: dict[str, tuple[float, tuple | None, str]] = {}    # rel -> (at, commit, why)
_bodies: dict[str, tuple[str, str]] = {}                    # rel -> (sha, text)
_failed: dict[str, tuple[float, str, str]] = {}             # rel -> (at, sha, why)


def clear() -> None:
    with _lock:
        _checks.clear()
        _bodies.clear()
        _failed.clear()


def _rel(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None


def _disk(path: Path) -> Found | None:
    try:
        mtime = path.stat().st_mtime
        return Found(path.read_text(), datetime.fromtimestamp(mtime, timezone.utc), "disk")
    except OSError:
        return None


def _commit(repo: str, token: str, rel: str, session) -> tuple[tuple | None, str]:
    now = time.monotonic()
    with _lock:
        hit = _checks.get(rel)
    if hit and now - hit[0] < CHECK_SECONDS:
        return hit[1], hit[2]
    # A failure is remembered for CHECK_SECONDS like a success is -- otherwise
    # every rerun during a GitHub outage would sit out the request timeout.
    try:
        commit, why = latest_commit(repo, token, rel, session=session), ""
    except Exception as exc:                                # noqa: BLE001
        commit, why = None, f"{type(exc).__name__}: {exc}"
    with _lock:
        _checks[rel] = (now, commit, why)
    return commit, why


def _body(repo: str, token: str, rel: str, sha: str, session) -> tuple[str | None, str]:
    with _lock:
        have = _bodies.get(rel)
        failed = _failed.get(rel)
    if have and have[0] == sha:
        return have[1], ""
    if failed and failed[1] == sha and time.monotonic() - failed[0] < CHECK_SECONDS:
        return None, failed[2]
    try:
        text = fetch(repo, token, sha, rel, session=session)
    except Exception as exc:                                # noqa: BLE001
        why = f"{type(exc).__name__}: {exc}"
        with _lock:
            _failed[rel] = (time.monotonic(), sha, why)
        return None, why
    with _lock:
        _bodies[rel] = (sha, text)
        _failed.pop(rel, None)
    return text, ""


def read(path: Path | str, session=None) -> Found | None:
    """The newest copy of a committed file this process can see, or None.

    Never raises for GitHub's sake: a failed check or download falls back to
    the disk copy with `problem` saying why, so a caller can tell the user
    they are looking at the deployed copy rather than guessing.
    """
    path = Path(path)
    rel = _rel(path)
    repo, token = credentials()
    if rel is None or not (repo and token):
        return _disk(path)

    commit, why = _commit(repo, token, rel, session)
    if commit is None:
        found = _disk(path)
        return replace(found, problem=why) if found and why else found
    sha, committed_at = commit
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if (mtime is not None and committed_at is not None
            and committed_at.timestamp() <= mtime - MTIME_MARGIN_SECONDS):
        return _disk(path)                   # the disk copy already has it

    text, why = _body(repo, token, rel, sha, session)
    if text is None:
        log.warning("repo_files: %s from GitHub failed (%s); using disk", rel, why)
        found = _disk(path)
        return replace(found, problem=why) if found else None
    return Found(text, committed_at or datetime.now(timezone.utc), "github")
