"""One `--source` flag, and one factory behind it, for every script.

Six scripts each built their own `OddsAPIClient` with the same two lines and
their own `--from-cache` interpretation of it. Adding a third source to six
copies is how the two halves of the shared core drifted the first time
(HANDOFF.md section 1), so the decision lives here instead.

    from edge.odds.cli import add_source_args, client_from_args

    add_source_args(ap)
    client = client_from_args(args, "baseball_mlb")

`--from-cache` is kept as an alias rather than removed: it appears in
DFS_COMMANDS.md, in muscle memory, and in whatever shell history is driving a
slate at 6pm. It now means "do not spend", which is what it always meant.
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache"
LEDGER = ROOT / "data" / "odds_api_credits.json"

log = logging.getLogger("edge.odds.cli")

SOURCES = ("scrape", "oddsapi", "auto")


def add_source_args(ap: argparse.ArgumentParser, default: str = "auto") -> None:
    """Add --source (and the legacy --from-cache alias) to a parser."""
    ap.add_argument("--source", choices=SOURCES, default=default,
                    help="scrape = free, from data/odds.db; oddsapi = the paid "
                         "feed; auto = scrape, falling back to oddsapi cache "
                         "(default: %(default)s)")
    ap.add_argument("--from-cache", action="store_true",
                    help="never spend a credit (legacy alias; implied by "
                         "--source scrape)")
    ap.add_argument("--max-age", type=float, default=None,
                    help="refuse a scraped scan older than this many seconds "
                         "(default: the profile's own contract)")


def load_key() -> str | None:
    """Prefer the environment, then either repo's .env. Never printed.

    The key lives in ~/arbitrage/.env on this machine rather than in
    edge_search's; both are gitignored (HANDOFF.md section 6).
    """
    if os.environ.get("ODDS_API_KEY"):
        return os.environ["ODDS_API_KEY"]
    for env in (ROOT / ".env", Path.home() / "arbitrage" / ".env"):
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            if line.startswith("ODDS_API_KEY") and "=" in line:
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    os.environ["ODDS_API_KEY"] = key
                    return key
    return None


def paid_client(spend: bool = False, live_ttl: int | None = None):
    from edge.client import OddsAPIClient
    load_key()
    return OddsAPIClient(
        cache_dir=CACHE_DIR, ledger_path=LEDGER, dry_run=not spend,
        live_ttl=(600 if spend else 10 ** 9) if live_ttl is None else live_ttl)


def scraped_client(sport: str, consumer: str = "dfs",
                   max_age_seconds: float | None = None):
    """The free path, preferring the live store and falling back to a snapshot.

    TWO ENVIRONMENTS, ONE FUNCTION. On the desktop the store exists and is
    current. On Streamlit Cloud there is no store at all -- data/odds.db is
    gitignored on purpose (public repo, and the history cannot be re-derived)
    -- so the published snapshot is the only free source there. Without this
    fallback the cloud app silently reverts to the paid API, which is the whole
    thing being removed.
    """
    from edge import repo_files
    from edge.odds.profiles import for_sport
    from edge.odds.publish import SnapshotOddsClient, snapshot_path
    from edge.odds.source import ScrapedOddsClient
    from edge.odds.store import OddsStore

    prof = for_sport(sport, consumer)
    age = prof.max_age_seconds if max_age_seconds is None else max_age_seconds

    if (ROOT / "data" / "odds.db").exists():
        try:
            client = ScrapedOddsClient(OddsStore(), prof.name, max_age_seconds=age,
                                       main_line_only=not prof.full_ladders)
            if client.get_events(sport):
                return client
        except Exception as exc:
            log.info("odds store unusable (%s); trying the published snapshot", exc)

    snap = snapshot_path(prof.name)
    # main's copy on GitHub when it is newer than the deployed one -- Cloud's
    # redeploy-on-push is unreliable (edge/repo_files.py); the disk otherwise.
    found = repo_files.read(snap)
    if found is not None:
        # The snapshot is deliberately given a LOOSER age bound than the store.
        # It is published by a desktop collection and reaches the cloud through
        # a git push, so it is always somewhat behind by construction; holding
        # it to the store's contract would reject every snapshot that ever
        # arrives. What must not happen is serving yesterday's prices silently,
        # so the bound is real -- just sized for the transport.
        return SnapshotOddsClient(snap, max_age_seconds=max(age, 6 * 3600),
                                  payload=found.json(), problem=found.problem)

    raise RuntimeError(
        f"no free odds for {prof.name!r}: no data/odds.db and no {snap.name}. "
        f"Run `python3 scripts/odds_collect.py --profile {prof.name} --publish`.")


def client_from_args(args, sport: str, consumer: str = "dfs", spend: bool = False):
    """Resolve --source into a client. Both satisfy the same interface.

    "auto" prefers the free store and falls back to the paid client's CACHE
    (never a fresh paid pull) when the store has nothing current. That is the
    right default for an unattended script: a missed collection degrades to
    slightly stale free data, then to cached paid data, and only spends when
    the operator asked for it by name.
    """
    source = getattr(args, "source", "auto")
    from_cache = getattr(args, "from_cache", False)
    max_age = getattr(args, "max_age", None)

    if source in ("scrape", "auto"):
        try:
            client = scraped_client(sport, consumer, max_age)
            if client.get_events(sport):
                return client
            raise RuntimeError(f"store has no {sport} events in the latest scan")
        except Exception as exc:
            if source == "scrape":
                raise
            log.warning("scraped odds unavailable (%s); using the Odds API", exc)

    return paid_client(spend=spend and not from_cache)


def describe(client) -> str:
    """One line naming where prices came from, for a script to print.

    Worth printing every time: a run that silently fell back to the paid
    client and a run that used the free store are otherwise indistinguishable
    in the output, and they cost different amounts.
    """
    name = type(client).__name__
    if name == "ScrapedOddsClient":
        row = client.store.latest_scan(client.profile)
        when = row["finished_at"] if row else "?"
        return f"prices: FREE scrape (profile {client.profile}, scanned {when})"
    if name == "SnapshotOddsClient":
        # THIS BRANCH WAS MISSING AND THE BADGE LIED IN THE WORST DIRECTION.
        # A snapshot client fell through to the paid wording below and, because
        # SnapshotOddsClient sets dry_run = False, printed
        # "prices: Odds API (LIVE, will spend, ? credits left)" for a free,
        # committed file that cannot spend anything. Found 2026-09-24 on the
        # NCAAF CLI, but it was never NCAAF-specific: it fires for MLB and NFL
        # too, every time the local store goes stale between collections, which
        # is their normal state on the desktop.
        #
        # This module's own docstring says a silent fallback to the paid client
        # is the thing the badge exists to catch. A badge that reports the
        # opposite is worse than no badge, because it invites someone to go
        # looking for a spend that never happened -- or to distrust a free run.
        mins = client.age_seconds / 60.0
        return (f"prices: FREE published snapshot (profile {client.profile}, "
                f"{mins:.0f} min old) — spends nothing")
    rem = client.remaining_credits()
    mode = "cache only" if client.dry_run else "LIVE, will spend"
    return f"prices: Odds API ({mode}, {rem if rem is not None else '?'} credits left)"
