#!/usr/bin/env python3
"""DK same-game-parlay price collector — SCAFFOLD (you run this, not me).

================================ READ THIS ==================================
Scraping DraftKings violates their Terms of Service and is the fastest way to
get your account limited or closed. There is no public SGP feed: each combo is
priced on demand by DK's parlay engine behind an authenticated, geo-fenced,
bot-protected endpoint. So:

  * Run this ONLY on your own logged-in session, in a state where you're a
    legal DK customer.
  * You provide the request DK's own site makes. Open the bet slip in your
    browser, add a same-game parlay, open DevTools -> Network, find the
    parlay-pricing XHR, and copy its URL + headers + body into capture below.
  * This scaffold deliberately contains NO anti-bot / detection-evasion logic.
    Be a polite client: low volume, real delays, your real session. If DK
    blocks it, stop — don't escalate. This is research tooling, not a weapon.
============================================================================

Pipeline role: the correlation MODEL (edge/sgp.py + your true-phi estimates)
picks a few high-correlation candidate combos per game; this script fetches
DK's price for ONLY those; then sgp_edge() flags the +EV ones. Model-driven,
low-volume — not brute force.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data/dk_sgp_quotes.jsonl"


def fetch_sgp_price(session, capture: dict, selection_ids: list[str]) -> dict:
    """Ask DK to price one specific combo.

    `capture` is the request YOU copied from your browser DevTools for a real
    SGP pricing call: {"url":..., "method":..., "headers":{...}, "body_template": ...}
    `selection_ids` are DK's internal IDs for the legs you want combined.

    This is a placeholder: plug your captured request shape in. I'm not
    hardcoding DK's internal endpoint/payload because it changes and guessing
    it would be wrong — capture the real one from your own session.
    """
    raise NotImplementedError(
        "Paste your captured DK parlay-pricing request into `capture` and map "
        "selection_ids into its body, then perform session.request(...) here."
    )


def record(game_id: str, legs: list[dict], dk_decimal: float, marginals: list[float]):
    """Append one SGP quote for later grading by edge.sgp.sgp_edge()."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as f:
        f.write(json.dumps({
            "ts": time.time(), "game_id": game_id, "legs": legs,
            "dk_decimal": dk_decimal, "marginals": marginals,
        }) + "\n")


if __name__ == "__main__":
    print(__doc__)
    print(f"\nQuotes would be appended to: {OUT}")
    print("This is a scaffold — wire in your captured DK request before use.")
