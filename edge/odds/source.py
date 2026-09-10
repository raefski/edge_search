"""A scraped odds source shaped exactly like `edge.client.OddsAPIClient`.

WHY THIS SHAPE AND NOT A BETTER ONE
`edge.dfs_run.build_slate` and `edge.pickem_live.fetch_week` both take a
`client` and call `get_events` / `get_event_odds` / `get_featured_odds` on it,
then walk the Odds API's `bookmakers[].markets[].outcomes[]` payload. That
payload shape is load-bearing in code with real backtested behaviour behind it
(`project_pitcher`, `_parse_events`, the book weighting). Replacing the SOURCE
and the SHAPE at once would mean any change in model output could be either --
and there would be no way to tell which.

So this class is deliberately Liskov-substitutable for the paid client:

    build_slate(OddsAPIClient(...), date)      # paid, unchanged
    build_slate(ScrapedOddsClient(store), date) # free, same code path

which makes the swap a one-line change at each call site and makes
`edge/odds/parity.py` able to run both over one slate and diff the outputs.
The nicer API can come later, once the free path has earned trust.

THE ONE PLACE THIS IS NOT A PURE TRANSLATION
An alternate ladder carries many rungs for one (book, market, subject).
`dfs.player_markets` builds `{market: {side: price, "point": x}}` and the last
outcome it sees wins `point` -- so handing it a whole ladder would mix a price
from one rung with the line from another. That is precisely the "two different
bets on one GroupKey" shape every price bug in this system has had
(HANDOFF.md section 8). `main_line_only` (default on) collapses each ladder to
its main rung first: the one priced closest to even on both sides, since
alternates are deliberately lopsided.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from .store import OddsStore

#: board side -> the name the Odds API payload uses. Team sides are resolved
#: per event, since their name is the team's.
SIDE_NAMES = {"over": "Over", "under": "Under", "yes": "Yes", "no": "No",
              "draw": "Draw"}


class StaleOdds(RuntimeError):
    """No committed scan is fresh enough for this caller's contract.

    Mirrors edge.client's DryRunBlocked/CreditFloorError: callers already
    degrade gracefully on an exception from the client rather than crashing,
    so a stale store behaves like an unavailable paid source instead of
    silently serving yesterday's prices as today's.
    """


def _main_point(rungs: dict[float, dict[str, float]]) -> float | None:
    """The main rung of a ladder: the one priced closest to even on both sides.

    Same rule as edge.pickem_free._main_line, which was written for the same
    reason -- without it a -8.5 alternate is as likely to be chosen as the -3
    the market is actually on.
    """
    best, best_gap = None, None
    for point, sides in rungs.items():
        if len(sides) < 2:
            continue
        gap = abs(max(sides.values()) - min(sides.values()))
        if best_gap is None or gap < best_gap:
            best, best_gap = point, gap
    if best is None and rungs:
        # one-sided market (DraftKings prices props as "2+" thresholds and the
        # opposing Under comes from another book) -- keep the only rung there is
        best = next(iter(rungs))
    return best


class ScrapedOddsClient:
    """Serves Odds-API-shaped payloads out of the scrape store. Costs nothing.

    `profile` pins which collection this client reads. A DFS client must not
    silently fall back to the wide arbitrage scan, whose
    `prop_events_per_league=6` cap would cover part of a slate and look like a
    thin one.

    `scan_id` PINS the client to one historical scan instead of the newest.
    That is what makes a MISSED capture recoverable: the store keeps 400 days
    of scans, so a snapshot that should have been taken at 1:11pm Tuesday can
    still be reconstructed from the scan that ran then -- see
    scripts/pickem_capture.py --scan-id. A backfill MUST also stamp the row
    with `scan_finished_at()`, not with now(), or the log lies about when the
    reading was taken and every downstream "was this before kickoff?" test
    reads a timestamp that never happened.
    """

    def __init__(self, store: OddsStore, profile: str | None = None,
                 max_age_seconds: float | None = None,
                 main_line_only: bool = True,
                 scan_id: int | None = None):
        self.store = store
        self.profile = profile
        self.max_age_seconds = max_age_seconds
        self.main_line_only = main_line_only
        self.scan_id = scan_id
        # ONE CLIENT SERVES ONE SCAN. `_scan_id()` used to re-resolve on every
        # call, and the two calls a capture makes -- get_featured_odds() for
        # the PRICES and scan_finished_at() for the TIMESTAMP -- each ran
        # store.latest_scan() independently:
        #
        #     _scan_id resolved 1 times   ->   after scan_finished_at: 2
        #
        # scripts/arb_agent.py publishes a scan on phone demand, i.e. exactly
        # when someone is looking at their phone on a Sunday at noon, which is
        # when the lock timers fire. A scan committing between those two calls
        # stamped scan N's prices with scan N+1's finish time -- a row claiming
        # prices it never observed, at a moment it never observed them, and
        # nothing downstream can detect it.
        #
        # Memoised per INSTANCE, not per process: scraped_client() builds a new
        # client per call and the Streamlit page builds one per render, so the
        # next run still picks up the newer scan.
        self._resolved_scan_id: int | None = None
        # API-compatibility surface: the app prints these next to the paid
        # client's numbers, so they must exist and must not lie.
        self.spent_this_session = 0
        self.dry_run = False

    def remaining_credits(self) -> int | None:
        return None      # not metered

    # --- scan resolution -----------------------------------------------------

    @property
    def resolved_scan_id(self) -> int | None:
        """The scan this client has committed to, or None before first use."""
        return self._resolved_scan_id

    def _scan_id(self) -> int:
        if self._resolved_scan_id is not None:
            return self._resolved_scan_id
        self._resolved_scan_id = self._resolve_scan_id()
        return self._resolved_scan_id

    def _resolve_scan_id(self) -> int:
        if self.scan_id is not None:
            row = self.store.scan(self.scan_id)
            if row is None or not row["ok"]:
                raise StaleOdds(
                    f"scan {self.scan_id} is not a committed scan in this "
                    f"store. Readers only ever see ok=1 scans; a crashed or "
                    f"half-written one is invisible on purpose.")
            if self.profile and row["profile"] != self.profile:
                raise StaleOdds(
                    f"scan {self.scan_id} belongs to profile "
                    f"{row['profile']!r}, not {self.profile!r}. Profiles have "
                    f"different coverage caps, so serving one as the other "
                    f"looks like a thin slate rather than the wrong scan.")
            return int(row["id"])
        row = self.store.latest_scan(self.profile, self.max_age_seconds)
        if row is None:
            newest = self.store.latest_scan(self.profile)
            if newest is None:
                raise StaleOdds(
                    f"no committed scan for profile {self.profile!r}. Run "
                    f"`python3 scripts/odds_collect.py --profile {self.profile}`.")
            raise StaleOdds(
                f"newest {self.profile!r} scan finished {newest['finished_at']}, "
                f"older than this caller's {self.max_age_seconds}s contract. "
                f"Collect a fresh one with `python3 scripts/odds_collect.py "
                f"--profile {self.profile}`, or pass --max-age to accept it.")
        return int(row["id"])

    def scan_finished_at(self) -> str | None:
        """When the scan this client is serving actually finished, ISO UTC.

        A backfill has to stamp its rows with this rather than with now(), or
        the log records a Tuesday-afternoon market reading as having been taken
        on Wednesday evening -- and scripts/pickem_transferability.py's
        before-kickoff guard then trusts a timestamp that never happened.
        """
        row = self.store.scan(self._scan_id())
        return row["finished_at"] if row is not None else None

    # --- payload construction ------------------------------------------------

    def _event_meta(self, scan_id: int, sport: str) -> dict[str, sqlite3.Row]:
        return {r["event_id"]: r for r in self.store.events(scan_id, sport)}

    def _build(self, rows: list[sqlite3.Row], meta: dict[str, sqlite3.Row]) -> list[dict]:
        # event -> book -> market -> subject -> point -> side -> decimal
        tree: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(
            lambda: defaultdict(lambda: defaultdict(dict)))))
        for r in rows:
            tree[r["event_id"]][r["book"]][r["market"]][r["subject"]][r["point"]][r["side"]] = r["decimal"]

        events = []
        for event_id, books in tree.items():
            ev = meta.get(event_id)
            if ev is None:
                continue
            home, away = ev["home_team"], ev["away_team"]
            bookmakers = []
            for book, markets in books.items():
                out_markets = []
                for market, subjects in markets.items():
                    outcomes = []
                    for subject, rungs in subjects.items():
                        points = ([_main_point(rungs)] if self.main_line_only
                                  else list(rungs))
                        for point in points:
                            if point not in rungs:
                                continue
                            outcomes.extend(
                                self._outcomes(market, subject, point,
                                               rungs[point], home, away))
                    if outcomes:
                        out_markets.append({"key": market, "outcomes": outcomes})
                if out_markets:
                    bookmakers.append({"key": book, "title": book,
                                       "markets": out_markets})
            if bookmakers:
                events.append({
                    "id": event_id,
                    "sport_key": ev["sport_key"],
                    "sport_title": ev["sport_title"],
                    "commence_time": ev["commence_time"],
                    "home_team": home, "away_team": away,
                    "bookmakers": bookmakers,
                })
        return events

    @staticmethod
    def _outcomes(market: str, subject: str | None, point: float | None,
                  sides: dict[str, float], home: str | None, away: str | None) -> list[dict]:
        out = []
        for side, dec in sides.items():
            if side == "home":
                name, pt = (home or "Home"), point
            elif side == "away":
                # Spreads are stored folded onto the home axis, so the away
                # side's own posted line is the negation. Displaying the raw
                # stored number is how a spread once showed BOTH teams laying
                # points (HANDOFF.md section 8).
                name, pt = (away or "Away"), (None if point is None else -point)
            else:
                name, pt = SIDE_NAMES.get(side, side.replace("_", " ").title()), point
            o = {"name": name, "price": dec}
            if pt is not None:
                o["point"] = float(pt)
            if subject:
                o["description"] = subject
            out.append(o)
        return out

    # --- OddsAPIClient surface ----------------------------------------------

    def get_sports(self) -> list[dict]:
        scan_id = self._scan_id()
        rows = self.store.conn.execute(
            "SELECT DISTINCT e.sport_key, e.sport_title FROM event e"
            " JOIN quote q ON q.event_id=e.event_id WHERE q.scan_id=?",
            (scan_id,)).fetchall()
        return [{"key": r["sport_key"], "title": r["sport_title"], "active": True}
                for r in rows]

    def get_events(self, sport: str) -> list[dict]:
        scan_id = self._scan_id()
        return [{"id": r["event_id"], "sport_key": r["sport_key"],
                 "sport_title": r["sport_title"],
                 "commence_time": r["commence_time"],
                 "home_team": r["home_team"], "away_team": r["away_team"]}
                for r in self.store.events(scan_id, sport)]

    def get_event_odds(self, sport: str, event_id: str, markets: list[str],
                       regions: str = "us") -> dict:
        scan_id = self._scan_id()
        rows = self.store.quotes(scan_id, sport, markets, [event_id])
        built = self._build(rows, self._event_meta(scan_id, sport))
        # The paid API 404s an event with no markets posted; build_slate
        # already treats an exception there as "skip this event". An empty
        # payload is the gentler equivalent and every caller handles it.
        return built[0] if built else {"id": event_id, "bookmakers": []}

    def get_featured_odds(self, sport: str, markets: list[str],
                          regions: str = "us") -> list[dict]:
        scan_id = self._scan_id()
        rows = self.store.quotes(scan_id, sport, markets)
        return self._build(rows, self._event_meta(scan_id, sport))

    # --- estimation (API compatibility; everything here is free) ------------

    @staticmethod
    def estimate_event_props(n_events: int, n_markets: int, regions: str) -> int:
        return 0

    @staticmethod
    def estimate_featured(n_markets: int, regions: str) -> int:
        return 0


def client_for(consumer: str, sport_key: str, store: OddsStore | None = None,
               **kw) -> ScrapedOddsClient:
    """Build a client the way a consumer thinks about it: 'I am DFS, I need MLB'.

    Resolves the profile, and with it that profile's freshness contract, so a
    call site never has to restate how stale is too stale -- which is the kind
    of number that gets copied to four places and then updated in three.
    """
    from .profiles import for_sport
    prof = for_sport(sport_key, consumer)
    kw.setdefault("max_age_seconds", prof.max_age_seconds)
    return ScrapedOddsClient(store or OddsStore(), prof.name, **kw)
