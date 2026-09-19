"""DraftKings transport + poll-volume regressions (2026-09-19).

Two separate outages a day apart, with the same 403/AkamaiGHost symptom and
different causes, cost most of two days. These lock in what was learned:

  * api.draftkings.com enforces HTTP/2 AND a full browser User-Agent, and
    fails BOTH the same indistinguishable way. edge/dfs.py had its own private
    urllib `_get` with UA "Mozilla/5.0", so it failed both, and the 09-19 fix
    to edge/arb/http.py did not reach it.
  * That failure was invisible, because _draftables_raw silently served the
    previous day's snapshot.
  * The snapshot poll was described as "1-2 calls per tick" and was really ~62,
    ~2,100 requests/day.
"""
import argparse
import json
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from edge import dfs                       # noqa: E402
from edge.arb import http as arb_http      # noqa: E402


# --- transport ---------------------------------------------------------------

def test_draftkings_hosts_take_the_http2_path_and_others_do_not():
    assert arb_http.is_draftkings("api.draftkings.com")
    assert arb_http.is_draftkings("sportsbook-nash.draftkings.com")
    assert arb_http.is_draftkings("www.draftkings.com")
    assert arb_http.is_draftkings("draftkings.com")
    # A lookalike must NOT be routed there, and neither must anything else.
    assert not arb_http.is_draftkings("draftkings.com.evil.test")
    assert not arb_http.is_draftkings("statsapi.mlb.com")
    assert not arb_http.is_draftkings("api.github.com")
    assert not arb_http.is_draftkings("")


def test_dfs_get_sends_a_real_browser_user_agent():
    # "Mozilla/5.0" alone gets a 403 from api.draftkings.com even over HTTP/2 --
    # isolated live 2026-09-19. The UA has to look like a real Chrome build.
    ua = dfs._UA["User-Agent"]
    assert ua == arb_http.DEFAULT_UA
    assert "Chrome/" in ua and "AppleWebKit/" in ua
    assert ua != "Mozilla/5.0"


def test_dfs_get_routes_draftkings_through_the_shim_and_mlb_through_urllib(monkeypatch):
    seen = {}

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            seen["shim"] = (url, dict(headers or {}))
            return arb_http.Response(200, b'{"draftables": [1]}', {}, url)

    monkeypatch.setattr(arb_http, "Session", FakeSession)
    monkeypatch.setattr(dfs._http, "Session", FakeSession)
    assert dfs._get("https://api.draftkings.com/x")["draftables"] == [1]
    assert seen["shim"][0] == "https://api.draftkings.com/x"
    assert "Chrome/" in seen["shim"][1]["User-Agent"]

    # statsapi must not pay for a subprocess: it is not gated and never was.
    def boom(*a, **kw):
        raise AssertionError("statsapi must not go through the curl shim")

    monkeypatch.setattr(dfs._http, "Session", boom)
    monkeypatch.setattr(dfs.urllib.request, "urlopen",
                        lambda *a, **kw: types.SimpleNamespace(
                            read=lambda: b'{"teams": []}',
                            __enter__=lambda s: s, __exit__=lambda *x: None))
    # json.load needs a file-like; use a tiny stand-in.
    import io
    monkeypatch.setattr(dfs.urllib.request, "urlopen",
                        lambda *a, **kw: io.BytesIO(b'{"teams": []}'))
    assert dfs._get("https://statsapi.mlb.com/api/v1/teams") == {"teams": []}


def test_dfs_get_raises_on_a_draftkings_403_rather_than_returning_junk(monkeypatch):
    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            return arb_http.Response(403, b"<HTML>Access Denied</HTML>", {}, url)

    monkeypatch.setattr(dfs._http, "Session", FakeSession)
    with pytest.raises(Exception):
        dfs._get("https://api.draftkings.com/x")


# --- the silent-staleness trap ----------------------------------------------

def test_draftables_fallback_announces_itself(monkeypatch, tmp_path, capsys):
    snap = tmp_path / "1234.json"
    snap.write_text(json.dumps([{"displayName": "Stale Guy"}]))
    monkeypatch.setattr(dfs, "_SNAP_DIR", tmp_path)
    monkeypatch.setattr(dfs, "_get", lambda url: (_ for _ in ()).throw(RuntimeError("403")))

    rows = dfs._draftables_raw(1234)

    assert rows == [{"displayName": "Stale Guy"}]
    assert dfs.LAST_DRAFTABLES_SOURCE.startswith("snapshot ")
    # It must SAY so. Serving day-old salaries as though they were live is the
    # thing that hid a broken transport for a whole day.
    assert "falling back to snapshot" in capsys.readouterr().err


def test_draftables_live_path_marks_itself_live(monkeypatch, tmp_path):
    monkeypatch.setattr(dfs, "_SNAP_DIR", tmp_path)
    monkeypatch.setattr(dfs, "_get", lambda url: {"draftables": [{"a": 1}]})
    monkeypatch.setattr(dfs, "LAST_DRAFTABLES_SOURCE", "snapshot stale")
    assert dfs._draftables_raw(1) == [{"a": 1}]
    assert dfs.LAST_DRAFTABLES_SOURCE == "live"


# --- the poll-volume throttle ------------------------------------------------

def _args(**kw):
    base = dict(refresh_hours=6.0, hot_hours=36.0, cold_hours=6.0)
    base.update(kw)
    return argparse.Namespace(**base)


def _group(start_epoch):
    from datetime import datetime, timezone
    return {"StartDate": datetime.fromtimestamp(start_epoch, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.0000000Z")}


@pytest.fixture()
def dp(monkeypatch, tmp_path):
    import draftables_publish as mod
    monkeypatch.setattr(mod.dfs, "_SNAP_DIR", tmp_path)
    return mod


def test_priced_group_is_not_refetched_before_the_heartbeat(dp, tmp_path):
    (tmp_path / "7.json").write_text("[1]")
    # Salaries are frozen once posted, so this request could not return
    # anything new -- it is the bulk of what made the poll ~2,100/day.
    assert dp._should_fetch(7, _group(time.time() + 3600), {}, _args()) is False
    old = time.time() - 7 * 3600
    import os
    os.utime(tmp_path / "7.json", (old, old))
    assert dp._should_fetch(7, _group(time.time() + 3600), {}, _args()) is True


def test_a_brand_new_group_is_always_fetched_on_the_tick_it_appears(dp):
    # The whole point of the 30-minute timer. A gid with no history and no
    # snapshot must never be throttled, however far out it starts.
    assert dp._should_fetch(99, _group(time.time() + 20 * 86400), {}, _args()) is True


def test_a_group_that_keeps_coming_back_unpriced_backs_off(dp):
    now = time.time()
    g = _group(now + 3600)                       # inside the hot window
    # 1 miss -> 30 min, 2 -> 1h, 3 -> 2h ... capped at cold_hours.
    assert dp._should_fetch(5, g, {5: (now - 600, 1)}, _args()) is False
    assert dp._should_fetch(5, g, {5: (now - 1900, 1)}, _args()) is True
    assert dp._should_fetch(5, g, {5: (now - 1900, 3)}, _args()) is False
    assert dp._should_fetch(5, g, {5: (now - 5 * 3600, 6)}, _args()) is False
    assert dp._should_fetch(5, g, {5: (now - 7 * 3600, 6)}, _args()) is True


def test_a_far_out_unpriced_group_backs_off_further_than_a_near_one(dp):
    now = time.time()
    near, far = _group(now + 3600), _group(now + 20 * 86400)
    state = {5: (now - 7 * 3600, 9)}
    assert dp._should_fetch(5, near, state, _args()) is True     # cap 6h
    assert dp._should_fetch(5, far, state, _args()) is False     # cap 24h


def test_attempt_state_round_trips_and_drops_groups_that_left_the_lobby(dp, monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "_ATTEMPTS", tmp_path / ".attempts.json")
    dp._save_attempts({1: [111.0, 2], 2: [222.0, 0]}, live={1})
    assert dp._load_attempts() == {1: [111.0, 2]}


def test_corrupt_attempt_state_is_ignored_not_fatal(dp, monkeypatch, tmp_path):
    p = tmp_path / ".attempts.json"
    p.write_text("{not json")
    monkeypatch.setattr(dp, "_ATTEMPTS", p)
    assert dp._load_attempts() == {}


def test_start_epoch_parses_draftkings_seven_digit_fractional_seconds(dp):
    # DK sends 7 fractional digits; datetime.fromisoformat accepts 3 or 6.
    assert dp._start_epoch({"StartDate": "2026-09-20T17:00:00.0000000Z"}) is not None
    assert dp._start_epoch({"StartDate": ""}) is None
    assert dp._start_epoch({}) is None
