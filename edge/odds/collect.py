"""Run one collection profile and commit it to the store.

This is the only writer. It reuses `edge.arb.run.scan` rather than calling the
providers itself, so there is exactly one scraping code path in the system and
every fix to it -- the flat-ladder guard, the period-market refusal, the
conflict counter -- protects the model data too, automatically.

`scan()` also runs arbitrage detection on the board it built. For a model
profile those opportunities are discarded. That is a few hundred milliseconds
on a one-league board and it buys a single code path; duplicating scan() to
skip it is the trade that produced two copies of the shared core last time.
"""
from __future__ import annotations

import logging

from edge.arb.config import ArbConfig
from edge.arb.run import scan

from .ingest import board_to_rows
from .profiles import Profile, get
from .store import OddsStore

log = logging.getLogger("edge.odds.collect")


class CollectionError(RuntimeError):
    pass


def collect(profile: Profile | str, store: OddsStore,
            base_cfg: ArbConfig | None = None, progress=None,
            strict: bool = True) -> dict:
    """Scrape one profile and append it to the store.

    Returns the scan's stats plus the ids and counts written.

    `strict` refuses to commit a scan that is missing a market the profile
    declared REQUIRED. That is the guard against the failure this repo keeps
    meeting: a scrape that succeeds, writes a file, and is quietly missing
    something a downstream model needs -- which looks exactly like a thin
    slate rather than a broken parser.
    """
    prof = get(profile) if isinstance(profile, str) else profile
    cfg = prof.config(base_cfg)

    scan_id = store.begin_scan(prof.name)
    try:
        _opps, stats, board = scan(cfg, progress=progress, return_board=True)
        events, quotes = board_to_rows(board)

        found = {q.market for q in quotes}
        missing = [m for m in prof.required_markets if m not in found]
        if missing and strict:
            raise CollectionError(
                f"profile {prof.name!r} produced no quotes for required "
                f"market(s) {missing}. Committing this scan would give the "
                f"model a board that looks thin rather than broken. Re-run, "
                f"or pass strict=False if the slate genuinely has none.")
        if missing:
            log.warning("profile %s missing required markets %s (strict=False)",
                        prof.name, missing)

        store.write_events(events)
        n = store.write_quotes(scan_id, quotes)

        stats = dict(stats)
        stats["profile"] = prof.name
        stats["missing_required"] = missing
        store.finish_scan(scan_id, stats)
    except Exception:
        store.abandon_scan(scan_id)
        raise

    # price_conflicts must be 0 in a one-shot scan -- one book quoting one
    # side of one group at two prices 25% apart can only be two different
    # bets on one key. Surfaced here as well as in the arb logs because the
    # model consumers now depend on the same mapping being right.
    if stats.get("price_conflicts"):
        log.warning("scan %d (%s): %d price conflicts -- two markets on one "
                    "GroupKey? See HANDOFF.md section 8.",
                    scan_id, prof.name, stats["price_conflicts"])

    return {"scan_id": scan_id, "profile": prof.name, "quotes": n,
            "events": len(events), "stats": stats}
