"""Free three-book arbitrage / middle / +EV scanner for Connecticut.

Connecticut licenses exactly three online sportsbooks -- DraftKings, FanDuel
and Fanatics -- and this reads all three from their own public endpoints, so a
scan costs no Odds API credits. Fanatics Markets (the prediction market) is
used as a vig-free fair-value anchor; it is never a bet leg, since CT
enforcement against sports event contracts is active.

Overlaps `edge.oddsmath` on odds conversion and de-vigging. That module serves
the +EV scanner; `edge.arb.oddsmath` adds stake allocation, arbitrage sums and
middle payoffs, and is kept self-contained so the arbitrage math carries its
own tests.
"""
from .config import ArbConfig

__all__ = ["ArbConfig"]
