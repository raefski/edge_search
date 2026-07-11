"""Thin The Odds API v4 client: auth, on-disk cache, credit accounting.

Cost model (verified against the live `x-requests-last` response header, which
is the ground truth this client logs after every paid call):

  * /sports and /sports/{sport}/events .......... FREE (0 credits)
  * LIVE  /sports/{sport}/odds .................. markets x regions   (per call, all games)
  * LIVE  /sports/{sport}/events/{id}/odds ..... markets x regions   (PER EVENT)
  * HISTORICAL endpoints ........................ 10x the live cost

Guardrails baked in:
  1. Key comes from the environment / .env only — never hardcoded.
  2. dry_run=True (default) refuses to make any paid (cost>0) call; it only
     reads cache or free endpoints. Flip to dry_run=False to actually spend.
  3. Every response is cached by md5(path+params) so re-runs cost 0 credits.
     Live endpoints honour a TTL so stale prices aren't served as fresh.
  4. Remaining credits are read from x-requests-remaining on every paid call,
     persisted to a local ledger, and the call aborts if they fall below floor.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = "https://api.the-odds-api.com/v4"


class CreditFloorError(RuntimeError):
    """Raised when a paid call would drop us below the configured credit floor."""


class DryRunBlocked(RuntimeError):
    """Raised when a paid call is attempted while dry_run is on."""


class NoApiKey(RuntimeError):
    """Raised when a real network call is attempted with no API key configured.

    The constructor used to raise this unconditionally, which meant a missing
    key broke the ENTIRE app -- including free salary/lineup data that never
    touches the Odds API at all, and even a warm cache hit that would have
    needed no network call whatsoever. Found live 2026-07-11: a Streamlit
    Cloud reboot lost the key (it was only ever pasted into the sidebar each
    session, not a persistent secret) and every mode, including CACHE, hard
    crashed. Deferred to the point of an actual network call instead, so
    build_slate's existing per-event handling (it already tolerates
    DryRunBlocked the same way -- see edge/dfs_run.py) degrades to "no
    pitcher props this build" rather than killing the whole page."""


class OddsAPIClient:
    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: str | Path = "data/cache",
        ledger_path: str | Path = "data/odds_api_credits.json",
        credits_floor: int | None = None,
        dry_run: bool = True,
        live_ttl: int = 600,  # seconds; live odds re-used within this window cost 0
    ):
        # No longer raises here -- see NoApiKey's docstring. self.api_key can
        # legitimately be None; every call site that actually needs it checks
        # at the point of use (_request), where a cache hit can still avoid
        # ever needing a key at all.
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = Path(ledger_path)
        self.credits_floor = (
            credits_floor
            if credits_floor is not None
            else int(os.environ.get("EDGE_CREDITS_FLOOR", "5000"))
        )
        self.dry_run = dry_run
        self.live_ttl = live_ttl
        self.spent_this_session = 0

    # --- cache helpers -------------------------------------------------------

    def _cache_path(self, path: str, params: dict) -> Path:
        keyed = {k: v for k, v in params.items() if k != "apiKey"}
        raw = path + "?" + json.dumps(keyed, sort_keys=True)
        h = hashlib.md5(raw.encode()).hexdigest()[:16]
        return self.cache_dir / f"odds_{h}.json"

    def _read_cache(self, cp: Path, ttl: int | None) -> Any | None:
        if not cp.exists():
            return None
        if ttl is not None and (time.time() - cp.stat().st_mtime) > ttl:
            return None
        try:
            return json.loads(cp.read_text())["data"]
        except Exception:
            return None

    def _write_cache(self, cp: Path, data: Any) -> None:
        cp.write_text(json.dumps({"fetched_at": time.time(), "data": data}))

    # --- credit ledger -------------------------------------------------------

    def remaining_credits(self) -> int | None:
        if self.ledger_path.exists():
            try:
                return json.loads(self.ledger_path.read_text()).get("remaining")
            except Exception:
                return None
        return None

    def _update_ledger(self, headers) -> None:
        remaining = headers.get("x-requests-remaining")
        used = headers.get("x-requests-used")
        last = headers.get("x-requests-last")
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps({
            "remaining": int(remaining) if remaining is not None else None,
            "used": int(used) if used is not None else None,
            "last": int(last) if last is not None else None,
            "updated_at": time.time(),
        }))

    # --- core request --------------------------------------------------------

    def _request(self, path: str, params: dict, cost: int, ttl: int | None) -> Any:
        """cost is the *expected* credit cost (0 for free endpoints)."""
        params = {**params, "apiKey": self.api_key}
        cp = self._cache_path(path, params)

        cached = self._read_cache(cp, ttl)
        if cached is not None:
            return cached  # 0 credits

        if not self.api_key:
            # Deferred from __init__ (see NoApiKey docstring) -- a cache hit
            # above never needed to reach this line. Applies even to cost=0
            # endpoints (get_events/get_sports): they're free of CREDITS but
            # the real Odds API still requires the key on every request.
            raise NoApiKey(f"no ODDS_API_KEY configured; can't make a live call to {path}")

        if cost > 0:
            if self.dry_run:
                raise DryRunBlocked(
                    f"dry_run blocked a {cost}-credit call to {path}. "
                    f"Re-run with confirm=True to spend."
                )
            remaining = self.remaining_credits()
            if remaining is not None and (remaining - cost) < self.credits_floor:
                raise CreditFloorError(
                    f"call would leave {remaining - cost} < floor {self.credits_floor}"
                )

        url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=45) as r:
            headers = r.headers
            data = json.load(r)

        if cost > 0:
            self._update_ledger(headers)
            try:
                self.spent_this_session += int(headers.get("x-requests-last", 0))
            except (TypeError, ValueError):
                pass

        self._write_cache(cp, data)
        return data

    # --- endpoints -----------------------------------------------------------

    def get_sports(self) -> list[dict]:
        return self._request("/sports", {}, cost=0, ttl=3600)

    def get_events(self, sport: str) -> list[dict]:
        return self._request(f"/sports/{sport}/events", {}, cost=0, ttl=300)

    def get_featured_odds(self, sport: str, markets: list[str], regions: str = "us") -> list[dict]:
        cost = len(markets) * len(regions.split(","))
        return self._request(
            f"/sports/{sport}/odds",
            {"regions": regions, "markets": ",".join(markets), "oddsFormat": "decimal"},
            cost=cost,
            ttl=self.live_ttl,
        )

    def get_event_odds(self, sport: str, event_id: str, markets: list[str], regions: str = "us") -> dict:
        cost = len(markets) * len(regions.split(","))
        return self._request(
            f"/sports/{sport}/events/{event_id}/odds",
            {"regions": regions, "markets": ",".join(markets), "oddsFormat": "decimal"},
            cost=cost,
            ttl=self.live_ttl,
        )

    # --- historical (10x cost; immutable -> cached forever) ------------------

    def get_historical_events(self, sport: str, date: str) -> dict:
        """List events active at a past snapshot. Returns {timestamp,
        previous_timestamp, next_timestamp, data:[events]}. Cheap (logged from
        header); we pass cost=1 only to gate dry-run/floor."""
        return self._request(f"/historical/sports/{sport}/events", {"date": date},
                             cost=1, ttl=None)

    def get_historical_event_odds(self, sport: str, event_id: str, date: str,
                                  markets: list[str], regions: str = "us") -> dict:
        """One event's odds at a past snapshot. The API returns the closest
        snapshot <= date, so date=commence_time gives the closing line."""
        cost = 10 * len(markets) * len(regions.split(","))
        return self._request(
            f"/historical/sports/{sport}/events/{event_id}/odds",
            {"date": date, "regions": regions, "markets": ",".join(markets), "oddsFormat": "decimal"},
            cost=cost, ttl=None,
        )

    # --- estimation ----------------------------------------------------------

    @staticmethod
    def estimate_event_props(n_events: int, n_markets: int, regions: str) -> int:
        """Live per-event prop cost = events x markets x regions."""
        return n_events * n_markets * len(regions.split(","))

    @staticmethod
    def estimate_featured(n_markets: int, regions: str) -> int:
        """Live featured cost = markets x regions (one call covers all games)."""
        return n_markets * len(regions.split(","))
