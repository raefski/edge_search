"""Board -> store rows.

The arbitrage scrapers already produce the canonical object this whole system
needs: `edge.arb.models.Board`, keyed by GroupKey (event, market, subject,
point) with one Quote per (side, book). This module is the only place that
flattens it for storage, so the storage shape and the in-memory shape cannot
drift the way five separate CSV parsers did (see edge/pickem_data.py).

Nothing here interprets prices. Ingestion that computes is ingestion that can
be wrong in a way you cannot see later; every derivation (devig, consensus,
projection) happens on read.
"""
from __future__ import annotations

from edge.arb.models import Board, GroupKey

from .store import EventRow, QuoteRow


def group_key_str(key: GroupKey) -> str:
    """Canonical string form of a GroupKey.

    Stored rather than recomputed on read because SQLite treats NULLs inside a
    composite PRIMARY KEY as distinct from one another -- a NULL subject (every
    game-level market) would defeat de-duplication entirely, and two reads of
    the same total in one scan would both persist.
    """
    subject = key.subject or ""
    point = "" if key.point is None else f"{float(key.point):g}"
    return f"{key.event_id}|{key.market}|{subject}|{point}"


def board_to_rows(board: Board) -> tuple[list[EventRow], list[QuoteRow]]:
    events = [
        EventRow(
            event_id=e.event_id,
            sport_key=e.sport_key,
            sport_title=e.sport_title,
            commence_time=e.commence_time.isoformat(),
            home_team=e.home_team,
            away_team=e.away_team,
        )
        for e in board.events.values()
    ]

    quotes: list[QuoteRow] = []
    for group in board.groups.values():
        gk = group_key_str(group.key)
        for side, per_book in group.quotes.items():
            for book, q in per_book.items():
                quotes.append(QuoteRow(
                    group_key=gk,
                    event_id=group.key.event_id,
                    sport_key=group.event.sport_key,
                    market=group.key.market,
                    subject=group.key.subject,
                    point=group.key.point,
                    side=side,
                    book=book,
                    decimal=q.decimal,
                    # the quote's OWN fetch time, not the scan's -- a wide scan
                    # takes minutes and its first league is genuinely older
                    # than its last.
                    captured_at=q.last_update.isoformat(),
                ))
    return events, quotes
