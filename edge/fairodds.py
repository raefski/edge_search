"""Fair-probability estimation by consensus of the *other* books.

For a soft-line hunt the fair estimate for a given outcome is the (optionally
weighted) average no-vig probability across every book EXCEPT the one we are
pricing — so we never grade a book against itself. A Pinnacle-anchored variant
can be obtained by passing weights={"pinnacle": large}, but for WNBA props
Pinnacle coverage is thin so an unweighted US-book consensus is the default.
"""
from __future__ import annotations


def consensus_prob(
    book_to_prob: dict[str, float],
    exclude: str,
    weights: dict[str, float] | None = None,
    min_books: int = 2,
) -> tuple[float | None, int]:
    """Return (consensus_fair_prob, n_books_used), excluding `exclude`.

    Returns (None, n) when fewer than `min_books` other books price the
    outcome — too thin a consensus to trust.
    """
    others = {b: p for b, p in book_to_prob.items() if b != exclude}
    if len(others) < min_books:
        return None, len(others)
    if weights:
        num = sum(weights.get(b, 1.0) * p for b, p in others.items())
        den = sum(weights.get(b, 1.0) for b in others)
        return (num / den if den else None), len(others)
    return sum(others.values()) / len(others), len(others)
