"""Publish one scan as a file the cloud app can read.

WHY THIS IS NEEDED AT ALL
Streamlit Cloud deploys from git and runs on a datacenter IP, so it can
neither scrape (DraftKings 403s it -- HANDOFF.md section 2) nor read
data/odds.db, which is deliberately gitignored: the store is an accumulating
corpus of scraped prices and this repo is public. Without a bridge the cloud
app falls back to the paid Odds API, which is the thing being removed.

So the same trick the arbitrage page already uses: the desktop scrapes, writes
a small derived JSON, and commits it; the cloud reads that file. See
scripts/arb_agent.py, which has published data/arb_snapshot.json this way for
months.

WHAT IS AND IS NOT PUBLISHED, and why that distinction matters
Published: ONE scan, the current prices, only the markets a consumer needs.
Not published: the store itself. A single scan is ephemeral and the method is
already readable in this public repo; the accumulated HISTORY is the one asset
here that cannot be re-derived, because scraping cannot reach backwards.
Committing the database would publish that permanently and irreversibly.

THE SHAPE CANNOT DRIFT
The export runs ScrapedOddsClient's own payload builder rather than a second
serialiser, so a snapshot is byte-for-byte the shape the desktop serves. A
second implementation is how the two copies of the shared core drifted the
first time (HANDOFF.md section 1).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .source import ScrapedOddsClient
from .store import OddsStore

ROOT = Path(__file__).resolve().parents[2]


def snapshot_path(profile: str, root: Path | None = None) -> Path:
    return (root or ROOT) / "data" / f"odds_snapshot_{profile}.json"


def export(store: OddsStore, profile: str, markets: list[str] | None = None,
           path: Path | None = None, sport_key: str | None = None) -> dict:
    """Write the latest committed scan for `profile` as an Odds-API payload.

    `markets` trims the file to what the consumer actually reads -- pick'em
    needs spreads and totals and nothing else, which is the difference between
    a 100KB file and a 5MB one. None publishes everything in the scan.
    """
    row = store.latest_scan(profile)
    if row is None:
        raise ValueError(f"no committed scan for profile {profile!r}")
    scan_id = int(row["id"])

    client = ScrapedOddsClient(store, profile)
    sports = ([sport_key] if sport_key else
              sorted({r["sport_key"] for r in store.events(scan_id)}))

    events: list[dict] = []
    for sport in sports:
        rows = store.quotes(scan_id, sport, markets)
        events.extend(client._build(rows, client._event_meta(scan_id, sport)))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "scan_id": scan_id,
        "scan_finished_at": row["finished_at"],
        "markets": sorted(markets) if markets else None,
        "events": events,
    }
    out = path or snapshot_path(profile)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Compact: this file is committed on every collection, so whitespace is
    # pure git history weight.
    out.write_text(json.dumps(payload, separators=(",", ":")))
    return {"path": str(out), "events": len(events), "scan_id": scan_id,
            "bytes": out.stat().st_size}


class SnapshotOddsClient:
    """Serves a published snapshot, with the same surface as every other client.

    This is what runs on Streamlit Cloud. It holds one scan and cannot reach
    the store, so there is no history and no freshness negotiation beyond the
    age of the file itself -- `age_seconds` is exposed so a page can say how
    old its prices are rather than implying they are live.
    """

    def __init__(self, path: str | Path, max_age_seconds: float | None = None):
        self.path = Path(path)
        self.payload = json.loads(self.path.read_text())
        self.profile = self.payload.get("profile")
        self.generated_at = self.payload.get("generated_at")
        self.max_age_seconds = max_age_seconds
        self.spent_this_session = 0
        self.dry_run = False
        self._events = self.payload.get("events") or []
        if max_age_seconds is not None and self.age_seconds > max_age_seconds:
            from .source import StaleOdds
            raise StaleOdds(
                f"snapshot {self.path.name} was generated {self.generated_at}, "
                f"older than this caller's {max_age_seconds}s contract.")

    @property
    def age_seconds(self) -> float:
        try:
            when = datetime.fromisoformat(self.generated_at)
        except (TypeError, ValueError):
            return float("inf")
        return (datetime.now(timezone.utc) - when).total_seconds()

    def remaining_credits(self) -> int | None:
        return None

    def _for_sport(self, sport: str) -> list[dict]:
        return [e for e in self._events if e.get("sport_key") == sport]

    @staticmethod
    def _trim(event: dict, markets: list[str] | None) -> dict:
        if not markets:
            return event
        wanted = set(markets)
        books = []
        for bk in event.get("bookmakers", []):
            mk = [m for m in bk.get("markets", []) if m.get("key") in wanted]
            if mk:
                books.append({**bk, "markets": mk})
        return {**event, "bookmakers": books}

    # --- OddsAPIClient surface ---------------------------------------------

    def get_sports(self) -> list[dict]:
        seen = {e.get("sport_key"): e.get("sport_title") for e in self._events}
        return [{"key": k, "title": v, "active": True} for k, v in seen.items() if k]

    def get_events(self, sport: str) -> list[dict]:
        return [{k: e.get(k) for k in ("id", "sport_key", "sport_title",
                                       "commence_time", "home_team", "away_team")}
                for e in self._for_sport(sport)]

    def get_event_odds(self, sport: str, event_id: str, markets: list[str],
                       regions: str = "us") -> dict:
        for e in self._for_sport(sport):
            if e.get("id") == event_id:
                return self._trim(e, markets)
        return {"id": event_id, "bookmakers": []}

    def get_featured_odds(self, sport: str, markets: list[str],
                          regions: str = "us") -> list[dict]:
        out = [self._trim(e, markets) for e in self._for_sport(sport)]
        return [e for e in out if e.get("bookmakers")]

    @staticmethod
    def estimate_event_props(n_events: int, n_markets: int, regions: str) -> int:
        return 0

    @staticmethod
    def estimate_featured(n_markets: int, regions: str) -> int:
        return 0
