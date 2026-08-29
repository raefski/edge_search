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
