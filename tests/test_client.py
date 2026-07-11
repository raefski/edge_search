"""edge/client.py: OddsAPIClient credit/cache/auth behavior.

Regression coverage for the 2026-07-11 incident: a missing ODDS_API_KEY
(lost across a Streamlit Cloud reboot, since it was only ever pasted into
the sidebar, not a persistent secret) crashed the ENTIRE app, including
free salary/lineup data that never touches the Odds API. The client now
defers the key check to the point of an actual network call.
"""
import json

import pytest

from edge.client import CreditFloorError, DryRunBlocked, NoApiKey, OddsAPIClient


def test_construction_does_not_require_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    # must NOT raise -- this is the exact fix for the 2026-07-11 crash
    c = OddsAPIClient(cache_dir=tmp_path / "cache", ledger_path=tmp_path / "ledger.json")
    assert c.api_key is None


def test_cache_hit_never_needs_a_key(tmp_path, monkeypatch):
    # A warm cache entry must be servable with NO key at all -- this is
    # exactly the CACHE-mode promise app.py's sidebar warning makes.
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    c = OddsAPIClient(cache_dir=tmp_path / "cache", ledger_path=tmp_path / "ledger.json",
                      dry_run=True, live_ttl=10**9)
    cp = c._cache_path("/sports/baseball_mlb/events", {})
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({"fetched_at": 0, "data": [{"id": "ev1"}]}))
    out = c.get_events("baseball_mlb")
    assert out == [{"id": "ev1"}]


def test_uncached_call_with_no_key_raises_no_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    c = OddsAPIClient(cache_dir=tmp_path / "cache", ledger_path=tmp_path / "ledger.json",
                      dry_run=True, live_ttl=10**9)
    with pytest.raises(NoApiKey):
        c.get_events("baseball_mlb")   # cost=0, but still a real network call


def test_dry_run_still_blocks_paid_calls_when_key_present(tmp_path, monkeypatch):
    # NoApiKey must not shadow the existing DryRunBlocked path for a client
    # that DOES have a key but is in dry-run/cache mode.
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    c = OddsAPIClient(api_key="fake-key", cache_dir=tmp_path / "cache",
                      ledger_path=tmp_path / "ledger.json", dry_run=True, live_ttl=10**9)
    with pytest.raises(DryRunBlocked):
        c.get_event_odds("baseball_mlb", "ev1", ["pitcher_outs"], "us")


def test_credit_floor_error_still_raised_with_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"remaining": 10, "used": 100, "last": 5, "updated_at": 0}))
    c = OddsAPIClient(api_key="fake-key", cache_dir=tmp_path / "cache", ledger_path=ledger,
                      dry_run=False, credits_floor=5000, live_ttl=10**9)
    with pytest.raises(CreditFloorError):
        c.get_event_odds("baseball_mlb", "ev1", ["pitcher_outs"], "us")
