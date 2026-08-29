"""The phone-asks/desktop-scrapes handshake.

Only the decision logic is tested here -- whether a request is worth acting on,
and whether one has been answered. The git and GitHub calls are thin wrappers
and are exercised by running the thing; what must not break is the logic that
decides to scrape, because both ends read it and a disagreement means either a
scan that never happens or a poller that scrapes in a loop.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edge.arb.scan_request import (
    ScanRequest, should_handle, snapshot_is_newer)


def _req(minutes_ago: float = 0.0, rid: str = "r1", sports=None) -> ScanRequest:
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return ScanRequest(requested_at=ts.isoformat(), request_id=rid,
                       sports=list(sports or []))


def test_a_fresh_unseen_request_is_handled():
    ok, why = should_handle(_req(), last_handled_id=None)
    assert ok and why == ""


def test_the_same_request_is_never_handled_twice():
    """The file stays in the repo after a scan. Without this the poller
    rescrapes it every cycle, forever."""
    ok, why = should_handle(_req(rid="r1"), last_handled_id="r1")
    assert not ok and why == "already handled"


def test_a_new_request_after_a_handled_one_is_picked_up():
    ok, _ = should_handle(_req(rid="r2"), last_handled_id="r1")
    assert ok


def test_a_stale_request_is_refused():
    """A desktop that was asleep must not wake and act on last night's request:
    the user stopped looking hours ago and those games have started."""
    ok, why = should_handle(_req(minutes_ago=60), last_handled_id=None,
                            max_age_seconds=900)
    assert not ok and "stale" in why


def test_a_request_just_inside_the_window_is_still_handled():
    ok, _ = should_handle(_req(minutes_ago=14), last_handled_id=None,
                          max_age_seconds=900)
    assert ok


def test_no_request_file_is_not_an_error():
    ok, why = should_handle(None, last_handled_id=None)
    assert not ok and why == "no request file"


@pytest.mark.parametrize("raw", ["", None, "not json", "[]", "{}",
                                 '{"requested_at": "2026-01-01T00:00:00+00:00"}'])
def test_malformed_request_files_are_ignored_not_acted_on(raw):
    """A half-written or hand-edited file is not a reason to scrape."""
    assert ScanRequest.parse(raw) is None
    ok, why = should_handle(ScanRequest.parse(raw), last_handled_id=None)
    assert not ok and why == "no request file"


def test_a_request_round_trips_through_json():
    req = ScanRequest.new(sports=["basketball_wnba"], note="from the app")
    back = ScanRequest.parse(req.to_json())
    assert back.request_id == req.request_id
    assert back.sports == ["basketball_wnba"]
    assert back.note == "from the app"
    assert back.age_seconds() < 5


def test_request_ids_are_unique_across_rapid_presses():
    ids = {ScanRequest.new().request_id for _ in range(50)}
    assert len(ids) == 50, "two presses would collide and the second be ignored"


# --- what the phone shows while it waits -----------------------------------
def test_a_newer_snapshot_answers_the_request():
    req = _req(minutes_ago=2)
    later = datetime.now(timezone.utc).isoformat()
    assert snapshot_is_newer(later, req)


def test_an_older_snapshot_does_not_answer_it():
    """The pre-existing snapshot must not read as a fresh answer, or the phone
    reports success without the desktop having done anything."""
    req = _req()
    earlier = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    assert not snapshot_is_newer(earlier, req)


def test_a_naive_timestamp_is_treated_as_utc_not_rejected():
    """Snapshots written by an older build carry no timezone; comparing them
    raises rather than returning a wrong answer, so they are coerced."""
    req = _req(minutes_ago=5)
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    assert snapshot_is_newer(naive, req)


@pytest.mark.parametrize("snap", [None, "", "not a date"])
def test_an_unreadable_snapshot_timestamp_reads_as_unanswered(snap):
    assert not snapshot_is_newer(snap, _req())


# --- the transport the request rides on ------------------------------------
def test_http_shim_sends_a_real_put_with_a_json_body():
    """`Session.request` used to forward every verb to `get`, so a PUT went out
    as a GET and the GitHub contents API would have refused it. Exercised
    against a local server rather than by reading the code, so the method and
    body are what actually crossed the socket."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from edge.arb import http

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen["method"] = self.command
            seen["body"] = self.rfile.read(n)
            seen["ctype"] = self.headers.get("Content-Type")
            seen["auth"] = self.headers.get("Authorization")
            body = b'{"content": {"sha": "abc"}}'
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        r = http.Session().put(f"http://127.0.0.1:{srv.server_port}/x",
                               json={"message": "hi", "content": "eyJ4IjogMX0="},
                               headers={"Authorization": "Bearer t"}, timeout=5)
        assert r.status_code == 201
        assert r.json()["content"]["sha"] == "abc"
    finally:
        srv.server_close()

    assert seen["method"] == "PUT", f"went out as {seen['method']}"
    assert seen["ctype"] == "application/json"
    assert seen["auth"] == "Bearer t"
    assert _json.loads(seen["body"])["message"] == "hi"


def test_get_still_behaves_after_the_shim_refactor():
    """Every scraper depends on Session.get; the PUT support rerouted it
    through `request`, so the repeated-param encoding has to still hold."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from edge.arb import http

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["method"] = self.command
            seen["query"] = self.path.split("?", 1)[1] if "?" in self.path else ""
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        http.Session().get(f"http://127.0.0.1:{srv.server_port}/x",
                           params={"bettypeIds": [1, 525, 526]}, timeout=5)
    finally:
        srv.server_close()
    assert seen["method"] == "GET"
    assert seen["query"].count("bettypeIds=") == 3


# --- credential validation -------------------------------------------------
def test_the_documented_placeholder_does_not_pass_as_a_token():
    """The example value in secrets.toml.example is the literal string
    "github_pat_...", which is truthy. A plain non-empty check therefore left
    the button enabled and failing with a bare 401 from GitHub -- an error that
    tells the user nothing about the real cause."""
    from edge.arb.scan_request import check_credentials
    assert "placeholder" in check_credentials("raefski/edge_search", "github_pat_...")


@pytest.mark.parametrize("repo,token,expect", [
    ("raefski/edge_search", "github_pat_11ABCDEF0abcdefghij", ""),
    ("raefski/edge_search", "ghp_abcdefghijklmnop", ""),
    ("", "github_pat_x", "GITHUB_REPO is not set"),
    ("raefski/edge_search", "", "GITHUB_TOKEN is not set"),
])
def test_check_credentials_verdicts(repo, token, expect):
    from edge.arb.scan_request import check_credentials
    assert check_credentials(repo, token) == expect


@pytest.mark.parametrize("repo", ["edge_search", "raefski/", "/edge_search",
                                  "raefski/edge/search"])
def test_a_malformed_repo_is_named_rather_than_sent(repo):
    """owner/name or nothing -- a bare repo name would 404 against the API and
    look like a permissions problem."""
    from edge.arb.scan_request import check_credentials
    assert "owner/name" in check_credentials(repo, "github_pat_real_looking_value")


def test_a_non_github_token_is_rejected():
    from edge.arb.scan_request import check_credentials
    assert "does not look like" in check_credentials("a/b", "hunter2")


def test_whitespace_only_secrets_are_treated_as_unset():
    """Pasting into the Secrets box picks up stray spaces and newlines."""
    from edge.arb.scan_request import check_credentials
    assert check_credentials("  ", " ") == "GITHUB_REPO is not set"
    assert check_credentials("a/b", "   ") == "GITHUB_TOKEN is not set"


def test_the_committed_placeholder_reads_as_no_request():
    """data/scan_request.json is committed so the path exists in a fresh clone,
    but a repo nobody has asked anything of must not look like it has a scan
    pending. Without the sentinel the placeholder parses as a real request,
    fails the freshness check, and the poller logs a stale-request line every
    cycle forever -- 2,900 a day at a 30s interval."""
    from edge.arb.scan_request import PLACEHOLDER_ID, ScanRequest, should_handle
    placeholder = ScanRequest(requested_at="1970-01-01T00:00:00+00:00",
                              request_id=PLACEHOLDER_ID, sports=[])
    ok, why = should_handle(placeholder, last_handled_id=None)
    assert not ok and why == "no request file"


def test_the_real_placeholder_file_on_disk_is_inert():
    """Guards the actual committed file, not just a reconstruction of it."""
    import json as _json
    from pathlib import Path

    from edge.arb.scan_request import REQUEST_PATH, ScanRequest, should_handle
    path = Path(__file__).resolve().parents[1] / REQUEST_PATH
    if not path.exists():
        pytest.skip("no committed request file")
    req = ScanRequest.parse(path.read_text())
    if req is None or req.request_id != "none":
        pytest.skip("a real request is currently pending")
    ok, why = should_handle(req, last_handled_id=None)
    assert not ok and why == "no request file"


# --- the boost sport picker -------------------------------------------------
def test_sport_choices_do_not_depend_on_the_last_snapshot():
    """A boost is tied to a sport you hold a token for, which may be one the
    last scan never covered. Deriving the list from the snapshot meant a
    WNBA-only snapshot left WNBA as the ONLY selectable sport -- and an empty
    snapshot left none at all, so the picker offered nothing but 'every sport'."""
    from edge.arb.scan_request import sport_choices
    empty = sport_choices(None, {})
    assert "basketball_wnba" in empty and "baseball_mlb" in empty
    assert len(empty) >= 7

    wnba_only = sport_choices(None, {"candidates": [{"sport_key": "basketball_wnba"}]})
    assert set(empty) <= set(wnba_only), "a narrow snapshot must not shrink the list"


def test_sport_choices_include_configured_and_snapshot_sports():
    from edge.arb.scan_request import sport_choices

    class Cfg:
        sports = ["soccer_epl"]

    got = sport_choices(Cfg(), {"candidates": [{"sport_key": "tennis_atp"}]})
    assert "soccer_epl" in got and "tennis_atp" in got
    assert got["soccer_epl"] == "EPL", "an unknown key still gets a readable name"


def test_sport_choices_are_named_not_raw_keys():
    from edge.arb.scan_request import sport_choices
    got = sport_choices(None, {})
    assert got["basketball_wnba"] == "WNBA"
    assert got["americanfootball_ncaaf"] == "NCAAF"


# --- GitHub refusals, translated into the setting to change ----------------
def test_a_403_on_write_names_the_read_only_contents_permission():
    """Seen live: the token could read the repo but Contents was Read-only, so
    the PUT came back 403. A bare '403' sends you to the wrong screen."""
    from edge.arb.scan_request import _explain
    msg = _explain(403, "write", "raefski/edge_search")
    assert "Read and write" in msg and "Contents" in msg


def test_a_403_on_read_is_a_different_message_from_one_on_write():
    """Same status, opposite causes -- the phase is what disambiguates."""
    from edge.arb.scan_request import _explain
    assert (_explain(403, "read", "a/b") != _explain(403, "write", "a/b"))


def test_a_404_explains_that_fine_grained_tokens_hide_repos():
    """GitHub returns 404 rather than 403 for a repo the token was not granted,
    so it reads as 'does not exist' when it means 'not selected'."""
    from edge.arb.scan_request import _explain
    msg = _explain(404, "read", "raefski/edge_search")
    assert "raefski/edge_search" in msg and "Repository access" in msg


class _Resp:
    """Just the surface get_file_sha/put_request use from the http shim."""

    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            from edge.arb.http import HTTPError
            raise HTTPError(f"{self.status_code}", self)


def test_a_write_refusal_raises_rather_than_returning_a_bad_result():
    """The read succeeds and the write is refused -- the live failure mode when
    Contents is left on Read-only."""
    from edge.arb.scan_request import RequestRefused, ScanRequest, put_request

    class FakeSession:
        def get(self, *a, **k):
            return _Resp(200, {"sha": "deadbeef"})

        def put(self, *a, **k):
            return _Resp(403)

    with pytest.raises(RequestRefused) as e:
        put_request("a/b", "github_pat_x", ScanRequest.new(), session=FakeSession())
    assert "Read and write" in str(e.value)


def test_a_read_refusal_is_reported_as_a_read_problem():
    from edge.arb.scan_request import RequestRefused, ScanRequest, put_request

    class FakeSession:
        def get(self, *a, **k):
            return _Resp(403)

        def put(self, *a, **k):
            raise AssertionError("must not attempt the write after a failed read")

    with pytest.raises(RequestRefused) as e:
        put_request("a/b", "github_pat_x", ScanRequest.new(), session=FakeSession())
    assert "at least Read-only" in str(e.value)


def test_a_missing_file_is_created_without_a_sha():
    """First ever request: the file does not exist, so no sha is sent. Sending
    one for a file that is not there is a 422."""
    from edge.arb.scan_request import ScanRequest, put_request
    sent = {}

    class FakeSession:
        def get(self, *a, **k):
            return _Resp(404)

        def put(self, *a, **k):
            sent.update(k.get("json") or {})
            return _Resp(201, {"commit": {"sha": "abc123"}})

    out = put_request("a/b", "github_pat_x", ScanRequest.new(), session=FakeSession())
    assert out["commit"]["sha"] == "abc123"
    assert "sha" not in sent


def test_an_existing_file_is_updated_with_its_sha():
    """Without the sha GitHub refuses the overwrite, which is what stops two
    phones clobbering each other."""
    from edge.arb.scan_request import ScanRequest, put_request
    sent = {}

    class FakeSession:
        def get(self, *a, **k):
            return _Resp(200, {"sha": "deadbeef"})

        def put(self, *a, **k):
            sent.update(k.get("json") or {})
            return _Resp(200, {"commit": {"sha": "abc123"}})

    put_request("a/b", "github_pat_x", ScanRequest.new(), session=FakeSession())
    assert sent.get("sha") == "deadbeef"
