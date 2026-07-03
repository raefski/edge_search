"""+EV scanner: normalise odds payloads, de-vig per book, flag prices that beat
the cross-book consensus.

EV (per $1 stake) for taking outcome O at decimal price d, given the consensus
fair probability p (from the *other* books):

    EV = p * d - 1

A positive EV means the price implies a worse probability than the rest of the
market believes — i.e. a soft line. We flag EV >= threshold (default +2%).

Assumptions (documented on purpose):
  * Only clean two-way markets are de-vigged (a player line with both Over and
    Under, a single totals line, an h2h pair). Three-way / one-sided / alt
    ladders are skipped in this first pass.
  * Consensus only compares books quoting the SAME line (point). A +EV flag is
    therefore "this book is soft at this exact number," not line-shopping noise.
"""
from __future__ import annotations

from collections import defaultdict

from .oddsmath import decimal_to_american, two_way_fair
from .fairodds import consensus_prob


def _subkey(market: str, outcome: dict) -> tuple:
    """Group a book's outcomes into opposing two-way pairs."""
    if outcome.get("description") is not None:        # player prop
        return ("prop", outcome["description"], outcome.get("point"))
    if market == "spreads":                           # home -x / away +x
        return ("spread", abs(outcome.get("point") or 0))
    if outcome.get("point") is not None:              # totals (incl. alt lines)
        return ("total", outcome.get("point"))
    return ("h2h", None)                              # moneyline


def parse_event(event: dict, method: str = "multiplicative") -> list[dict]:
    recs: list[dict] = []
    label = f'{event.get("away_team")} @ {event.get("home_team")}'
    # Scope every outcome to its game. Without this, a team/player appearing in
    # two games on the same slate (e.g. a team playing a doubleheader) would
    # have its prices pooled across both games -> phantom +EV.
    event_id = event.get("id") or label
    for bm in event.get("bookmakers", []):
        book = bm.get("key")
        for mk in bm.get("markets", []):
            market = mk.get("key")
            groups: dict[tuple, list[dict]] = defaultdict(list)
            for o in mk.get("outcomes", []):
                groups[_subkey(market, o)].append(o)
            for sub, pair in groups.items():
                if len(pair) != 2:
                    continue
                f0, f1 = two_way_fair(pair[0]["price"], pair[1]["price"], method=method)
                subject = sub[1] if sub[0] == "prop" else ""
                for o, fair in ((pair[0], f0), (pair[1], f1)):
                    recs.append({
                        "event": label,
                        "event_id": event_id,
                        "commence": event.get("commence_time"),
                        "market": market,
                        "subject": subject or "",
                        "side": o.get("name"),
                        "point": o.get("point"),
                        "book": book,
                        "dec": o["price"],
                        "fair": fair,
                    })
    return recs


def scan(
    events: list[dict],
    method: str = "multiplicative",
    ev_threshold: float = 0.02,
    min_books: int = 2,
    weights: dict[str, float] | None = None,
    target_books: set[str] | None = None,
    ref_books: set[str] | None = None,
) -> list[dict]:
    """target_books: only flag prices we can actually bet (e.g. {"draftkings"}).
    ref_books: build the fair consensus from only these books (e.g. sharper
    books), so soft recreational books don't form a circular consensus."""
    recs: list[dict] = []
    for ev in events:
        recs.extend(parse_event(ev, method=method))

    # Index each outcome's per-book fair prob, keyed on an exact identity.
    idx: dict[tuple, dict[str, float]] = defaultdict(dict)
    for r in recs:
        ident = (r["event_id"], r["market"], r["subject"], r["side"], r["point"])
        idx[ident][r["book"]] = r["fair"]

    flagged: list[dict] = []
    for r in recs:
        if target_books and r["book"] not in target_books:
            continue  # not a price we can actually take
        ident = (r["event_id"], r["market"], r["subject"], r["side"], r["point"])
        pool = idx[ident]
        if ref_books:
            pool = {b: p for b, p in pool.items() if b in ref_books}
        cons, n = consensus_prob(pool, exclude=r["book"], weights=weights, min_books=min_books)
        if cons is None:
            continue
        ev_val = cons * r["dec"] - 1.0
        if ev_val >= ev_threshold:
            flagged.append({
                **r,
                "american": decimal_to_american(r["dec"]),
                "fair_consensus": cons,
                "n_books": n,
                "ev": ev_val,
            })
    flagged.sort(key=lambda x: x["ev"], reverse=True)
    return flagged
