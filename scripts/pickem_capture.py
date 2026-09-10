#!/usr/bin/env python3
"""Record a timestamped line snapshot -- the weekly habit that unblocks 5f.

One run per DEADLINE is the whole discipline, and deploy/pickem-capture@.service
now runs them on timers:

    # Tuesday, right after CBS posts (the freeze). Do this FIRST -- the
    # market reading is only useful if it is contemporaneous with CBS's.
    python3 scripts/pickem_capture.py --snapshot post --week 3 --confirm

    # Before the first game of each day, once inactives are out. GIVE EACH
    # DEADLINE ITS OWN LABEL: pickem_log.append de-dupes on (season, week,
    # snapshot, home_team), so a shared "lock" keeps only the first reading
    # of the week and silently drops Sunday's and Monday's. The six timers
    # use lock-wed / lock-thu / lock-sun / lock-mon for exactly this reason.
    python3 scripts/pickem_capture.py --snapshot lock-thu --week 3 --confirm

TWO HALVES, ONE LABEL. The market half is time-critical and automatable; the
CBS half needs a login and arrives later. Run the market half now with
--market-only, then re-run the SAME label once the CBS numbers are in
data/pickem_current_week.csv and the second run FILLS IN the blank columns:

    python3 scripts/pickem_capture.py --snapshot post --week 3 --market-only --confirm
    ... transcribe the CBS screenshot ...
    python3 scripts/pickem_capture.py --snapshot post --week 3 --confirm
    -> wrote 0 new, completed 16

BACKFILLING A MISSED DEADLINE. data/odds.db keeps 400 days of scans, so a
capture that never ran is still recoverable from the scan that did:

    python3 scripts/pickem_capture.py --snapshot post --week 1 \
        --scan-id 224 --market-only --confirm

`captured_at` IS ALWAYS THE SCAN'S FINISH TIME, backfill or not. Until
2026-09-09 that was true only of a --scan-id run and every other row was
stamped utcnow(), which was wrong by 6-13 hours on every timer: the board is
collected at 12:20 and 23:20, and the capture timers fired at 12:00 and 19:00.
A row claiming to be a Tuesday-1pm reading must actually carry Tuesday 1pm, or
scripts/pickem_transferability.py's before-kickoff guard trusts a moment that
never happened -- and the `midweek` snapshot, whose only purpose is the TIME
PROFILE of drift, measures nothing at all. `board_age_seconds` records how
stale the board already was, so `captured_at + board_age_seconds` still
recovers when the process ran. deploy/pickem-capture@.service now collects the
board immediately before capturing, so on the timers that age is ~0.

CBS's line and community percentages cannot be fetched -- they are behind a
login -- so they come from data/pickem_current_week.csv, which you fill in
from the pool screenshot. Everything else is pulled live.

FREE BY DEFAULT since 2026-09-08. The scraped board (edge/odds, profile
`pickem_nfl`, collected twice daily by odds-collect-pickem-nfl.timer) serves
the same Odds-API-shaped payload for nothing, so a capture no longer spends.
`--source paid` keeps the old path: it costs len(MARKETS) x len(regions) =
2 credits (spreads + totals, one region, one call for the whole slate),
because The Odds API bills per market per region, not per call.

DRY RUN BY DEFAULT, matching scripts/wnba_scout.py: without --confirm this
prints what it would do and writes nothing.

Order matters on Tuesday: transcribe the screenshot into
data/pickem_current_week.csv, then run this within a few minutes, or the
"market at the moment CBS posted" reading is no longer that.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem_free import filter_to_slate  # noqa: E402
from edge.pickem_live import MARKETS, REGIONS, fetch_week  # noqa: E402
from edge.pickem_log import LINE_LOG, Snapshot, append, complete, utcnow  # noqa: E402
# The pool calendar and the CBS/market team alias live in edge/pickem_week.py
# so that pages/4_🎯_Pickem.py runs the SAME logic. They were private to this
# script until 2026-09-09, and the page -- lacking them -- joined a two-week
# board by home team alone and printed the wrong side of BUF@HOU. Re-exported
# here because call sites and tests already import them from this module.
from edge.pickem_week import (  # noqa: E402,F401
    EASTERN, MARKET_TO_CBS_ABBR, SEASON_START, _cbs_abbr, current_week,
    week_window,
)

CURRENT_WEEK = ROOT / "data" / "pickem_current_week.csv"
CACHE_DIR = ROOT / "data" / "cache"
LEDGER = ROOT / "data" / "odds_api_credits.json"

#: Months (ET) in which "outside the season" is a BUG rather than a fact.
#: The NFL regular season runs September into early January, so a `--week auto`
#: that resolves to None inside this window means the CALENDAR is wrong, not
#: that there is no football. See _in_season_months.
SEASON_MONTHS = (9, 10, 11, 12, 1)


def _in_season_months(today: datetime.date | None = None) -> bool:
    today = today or datetime.datetime.now(EASTERN).date()
    return today.month in SEASON_MONTHS


def _is_deadline(snapshot: str) -> bool:
    """Is this label one a TIMER passes -- i.e. a reading nobody is watching?

    The six timers use `post`, `lock-wed`, `lock-thu`, `lock-sun`, `lock-mon`
    and `midweek`. Those are deadlines: the moment passes, and the market at
    that moment is gone for good, so an empty result has to go red.

    A free-form label is something a human typed and is watching. Making those
    fatal too would train the reflex that a red capture is normal, which is
    precisely what stops the real one being noticed.
    """
    label = (snapshot or "").strip().lower()
    return label == "post" or label.startswith("lock") or label == "midweek"


def _already_logged(season, week, snapshot, path=LINE_LOG) -> int:
    """How many rows the log already holds for this exact label.

    Distinguishes the two ways a run can change nothing: "this was already
    recorded in full" (the documented second half of a two-part capture --
    success) from "nothing was captured at all" (a permanently missing
    reading). Only the counters cannot tell them apart; the file can.
    """
    from pathlib import Path as _Path
    if not _Path(path).exists():
        return 0
    try:
        from edge.pickem_log import load
        return sum(1 for r in load(path)
                   if str(r.get("season", "")) == str(season)
                   and str(r.get("week", "")) == str(week)
                   and r.get("snapshot", "") == snapshot)
    except Exception:                                # unreadable == unknown
        return 0


def _missed(args, reason: str, remedy: str = "") -> None:
    """Report a capture that banked nothing, and FAIL if it was a deadline.

    THE BUG THIS FIXES, found 2026-09-10. Iteration 2 wired a whole alerting
    chain to this script -- deploy/pickem-capture-failed@.service via
    OnFailure=, data/pickem_capture_failures.log, a banner on the Pick'em page,
    Restart=on-failure -- and every link of it is armed by a NON-ZERO EXIT
    CODE. The likeliest miss exited 0:

        live slate for week 18 (Tue Jan 05 -> Tue Jan 12 ET): 0 of 29 board events
        Nothing on the live slate falls in week 18. Nothing written.
        >>> exit code 0

    So the timer went green, the failures log stayed empty, the page said
    nothing, and the reading was gone. data/odds.db can recover a scan that
    happened; it cannot recover a deadline nobody noticed.
    """
    print(f"\n{reason}")
    if remedy:
        print(remedy)
    if not _is_deadline(args.snapshot):
        print(f"(label {args.snapshot!r} is not one of the timer deadlines, so "
              "this is reported, not failed.)")
        return
    print(f"\nFAILING: '{args.snapshot}' is a DEADLINE label and this run banked "
          "nothing. The market at that moment is not recoverable, so this exits "
          "non-zero to arm deploy/pickem-capture-failed@.service, the failures "
          "log, and the banner on the Pick'em page.")
    raise SystemExit(1)


def load_cbs(week: int) -> list[dict]:
    if not CURRENT_WEEK.exists():
        return []
    with CURRENT_WEEK.open() as f:
        return [r for r in csv.DictReader(f) if int(r["week"]) == week]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _client(args):
    """The odds source, free by default.

    The paid branch calls edge.odds.cli.load_key() first. edge/client.py only
    ever reads ODDS_API_KEY from the environment, but the key lives in
    ~/arbitrage/.env on this machine -- so constructing OddsAPIClient directly
    raised NoApiKey for anyone who had not exported it by hand, which is the
    worst possible moment to discover a config problem: the Tuesday `post`
    reading is only useful within minutes of CBS posting.
    """
    if args.source == "paid":
        from edge.client import OddsAPIClient
        from edge.odds.cli import load_key
        if not load_key():
            print("\nNo ODDS_API_KEY found in the environment, ./.env or "
                  "~/arbitrage/.env. The free source needs no key -- drop "
                  "--source paid.")
            raise SystemExit(1)
        return OddsAPIClient(cache_dir=CACHE_DIR, ledger_path=LEDGER,
                             dry_run=False)

    from edge.odds.profiles import get as get_profile
    from edge.odds.source import ScrapedOddsClient
    from edge.odds.store import OddsStore
    prof = get_profile("pickem_nfl")
    store = OddsStore()
    # args.scan_id already carries anything --at resolved to: _resolve_at runs
    # in main() ahead of the dry-run return, so the resolution is visible
    # without --confirm.
    scan_id = args.scan_id
    if scan_id is not None:
        # A pinned scan is a deliberate reach into the past, so the freshness
        # contract -- which exists to stop yesterday's prices being served as
        # today's -- would reject exactly the row we are asking for.
        return ScrapedOddsClient(store, profile=prof.name, scan_id=scan_id)
    return ScrapedOddsClient(store, profile=prof.name,
                             max_age_seconds=prof.max_age_seconds)


def _board_instant(client) -> tuple[str, int | None]:
    """(captured_at, board_age_seconds) for the reading about to be banked.

    THE BUG THIS FIXES, found 2026-09-09. `captured_at` was `utcnow()` on every
    normal run, and the scan's own finish time was consulted ONLY when the
    client had been pinned with --scan-id. But the free client never reads
    "now" -- it reads the newest committed scan, and
    odds-collect-pickem-nfl.timer collects at 12:20 and 23:20 only. So:

        pickem-capture-lock-sun  fired Sun 12:00 -> Sat 23:20 board (12h40m)
        pickem-capture-midweek   fired Fri 12:00 -> Thu 23:20 board (12h40m)
        the three 19:00 lock timers                -> that day's 12:20 (6h40m)

    Every one of those rows claimed prices had been observed at an instant
    hours after they really were, and edge/odds/profiles.py gives this profile
    a deliberately generous max_age_seconds (86400 -- the pool line is frozen
    all week), so the freshness contract never objected.

    Why it matters beyond tidiness: the `midweek` snapshot exists for exactly
    one purpose, the TIME PROFILE of post-freeze drift (PICKEM_MODEL.md 5j
    round 6d), which the 2014-2024 archive can never supply because it holds
    two snapshots per game. A time profile built from timestamps each offset
    backwards by an unrecorded 6-13 hours measures nothing.

    So: the row carries the moment the PRICES were observed, and the second
    return value records how stale they already were, which is the number
    section 6 needs to answer "how late can we legally capture?".
    `captured_at + board_age_seconds` recovers the moment the process ran.

    The paid Odds API has no scan and no such gap: it returns now(), and the
    age column stays blank rather than being fabricated as zero.

    AND A BACKFILL RECORDS 0, NOT 35 HOURS. Found 2026-09-10. The page's
    capture-failure banner tells the reader to recover a missed deadline with
    `--at <the deadline> --confirm`, and `_scan_at` resolves that to the newest
    scan finishing AT OR BEFORE that instant -- i.e. the board as it stood at
    the deadline, by construction. But this function measured wall-clock-NOW
    minus board time, so a perfectly-targeted Tuesday-1:11pm recovery run on
    Wednesday night wrote board_age_seconds = 127928.

    That number is not staleness. It is when the RECOVERY ran, and section 6
    reads this column to answer "how late can we legally capture?" -- so every
    backfilled deadline would score as hopelessly late. A pinned scan is the
    board at the instant being reconstructed, so its age at that instant is 0.
    The wall-clock lag is still PRINTED by the caller; it is simply not a
    property of the reading.

    The 32 rows already on file are blank, which is honest, and are left alone.
    """
    getter = getattr(client, "scan_finished_at", None)
    finished = None
    if getter is not None:
        try:
            finished = getter()
        except Exception:                        # StaleOdds on a pinned miss
            finished = None
    if not finished:
        return utcnow(), None
    ts = (datetime.datetime.fromisoformat(finished.replace("Z", "+00:00"))
          .astimezone(datetime.timezone.utc))
    stamp = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    if getattr(client, "scan_id", None) is not None:
        return stamp, 0
    now = datetime.datetime.now(datetime.timezone.utc)
    return stamp, int(round((now - ts).total_seconds()))


def _resolve_at(args) -> None:
    """Turn --at into --scan-id BEFORE anything else can return.

    This lived inside `_client`, which runs after the dry-run return, so `--at`
    was inert in a dry run -- and DRY RUN BY DEFAULT is this project's whole
    convention. The point of printing which scan an instant resolves to is
    being able to check it before writing to an append-only log.
    """
    if args.scan_id is not None or not args.at or args.source == "paid":
        return
    from edge.odds.profiles import get as get_profile
    from edge.odds.store import OddsStore
    args.scan_id = _scan_at(OddsStore(), get_profile("pickem_nfl").name,
                            args.at)


def _scan_at(store, profile: str, at: str) -> int:
    """Newest committed scan for `profile` finishing at or before `at`.

    Lets a backfill be described the way it is actually remembered -- "the
    reading that should have been taken just after 1pm Tuesday" -- rather than
    by an opaque row id.
    """
    ts = datetime.datetime.fromisoformat(at.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    row = store.conn.execute(
        "SELECT id, finished_at FROM scan WHERE ok=1 AND profile=?"
        " AND finished_at<=? ORDER BY finished_at DESC LIMIT 1",
        (profile, ts.isoformat())).fetchone()
    if row is None:
        raise SystemExit(f"no committed {profile!r} scan finished at or before "
                         f"{ts.isoformat()}. data/odds.db keeps 400 days; "
                         f"anything older is genuinely gone.")
    print(f"--at {at} -> scan {row['id']} (finished {row['finished_at']})")
    return int(row["id"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True,
                    help="'post' (Tuesday freeze), 'lock' (pre-deadline), or a "
                         "free-form label for an extra mid-week reading")
    ap.add_argument("--week", required=True,
                    help="week number, or 'auto' to derive it from today's date "
                         "in ET (what the systemd timers pass)")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--regions", default=REGIONS,
                    help="paid source only: 'us' (2 credits) or 'us,eu' to "
                         "include Pinnacle (4 credits)")
    ap.add_argument("--push", action="store_true",
                    help="commit and push data/pickem_line_log.csv afterwards. "
                         "git IS the database for this file -- an uncommitted "
                         "row does not survive, and the Streamlit page reads "
                         "the repo. Safe on a timer: an unchanged log is a "
                         "no-op, not an empty commit.")
    ap.add_argument("--source", choices=("free", "paid"), default="free",
                    help="'free' (default) reads the scraped board in "
                         "data/odds.db -- DK, FanDuel and Fanatics, no credits. "
                         "'paid' calls The Odds API for ~10 books at "
                         "markets x regions credits.")
    ap.add_argument("--confirm", action="store_true", help="actually spend credits and write")
    ap.add_argument("--market-only", action="store_true",
                    help="capture the market WITHOUT CBS's lines, even if "
                         "data/pickem_current_week.csv has some. The market "
                         "half is time-critical and automatable; CBS's half "
                         "needs a login and gets filled in later by re-running "
                         "the same --snapshot label. They join on "
                         "(season, week, snapshot, home_team).")
    ap.add_argument("--scan-id", type=int, default=None,
                    help="BACKFILL: read one specific scan out of data/odds.db "
                         "instead of the newest. `captured_at` is then the "
                         "scan's own finish time, so a recovered reading is "
                         "stamped with the moment it was really taken. Free "
                         "source only. See `--at` to find one by clock time.")
    ap.add_argument("--at", default=None,
                    help="BACKFILL: pick the newest pickem_nfl scan finishing "
                         "at or before this ISO instant (e.g. "
                         "2026-09-08T17:15:00Z), instead of passing --scan-id.")
    args = ap.parse_args()
    if (args.scan_id or args.at) and args.source == "paid":
        # The Odds API has no notion of a past scan, so silently ignoring the
        # flag would stamp a backfill with the CURRENT market and the wrong
        # timestamp -- an unrecoverable lie in an append-only log.
        print("--scan-id/--at read data/odds.db, which only the free source "
              "has. Drop --source paid.")
        raise SystemExit(2)
    if str(args.week).lower() == "auto":
        args.week = current_week()
        if args.week is None:
            # SEASON_START is a single Tuesday, verified correct for 2026, and
            # is deliberately NOT changed here. But it goes stale: from
            # 2027-01-12 current_week() returns None forever, so through the
            # whole 2027 season all six timers would print this line and exit
            # 0. Inside the season months that is a broken calendar, not an
            # absence of football, and it has to go red.
            if _in_season_months():
                _missed(args,
                        "Outside the season -- but it is "
                        f"{datetime.datetime.now(EASTERN):%B}, which is IN the "
                        "NFL regular season.",
                        "edge/pickem_week.py's SEASON_START is anchored to one "
                        "Tuesday (2026-09-08) and current_week() returns None "
                        "past week 18 of that season. Re-anchor it to this "
                        "season's week-1 Tuesday, or pass --week explicitly.")
                return
            print("Outside the season -- nothing to capture.")
            return
    args.week = int(args.week)
    if not 1 <= args.week <= 18:
        print(f"Week {args.week} is outside the 18-week regular season.")
        return

    cost = (len(MARKETS) * len(args.regions.split(",")) if args.source == "paid"
            else 0)
    cbs_rows = load_cbs(args.week)
    print(f"snapshot '{args.snapshot}' | season {args.season} week {args.week}")
    print(f"CBS rows from {CURRENT_WEEK.name}: {len(cbs_rows)}"
          + (" (IGNORED -- --market-only)" if args.market_only else ""))
    if args.source == "paid":
        print(f"source: PAID Odds API | estimated cost: {cost} credits "
              f"({len(MARKETS)} markets x {len(args.regions.split(','))} region(s))")
    else:
        print("source: FREE scraped board (data/odds.db, profile pickem_nfl) "
              "| cost: 0 credits")

    if not cbs_rows and not args.market_only:
        print(f"\nNo week-{args.week} rows in {CURRENT_WEEK.name}. Transcribe the CBS "
              "screenshot there first -- a snapshot without CBS's number can't "
              "measure CBS bias, which is the point.")
        print("\nIf you just want to bank a TIMESTAMPED MARKET READING now and add "
              "CBS's numbers later, re-run with --market-only. The two join on "
              "(season, week, home_team), so the market half never has to wait "
              "for the manual half -- and a market reading missed is gone forever.")
        return

    # BEFORE the dry-run return, on purpose: see _resolve_at.
    _resolve_at(args)

    if not args.confirm:
        print("\nDRY RUN -- nothing pulled, nothing written. Re-run with --confirm.")
        return

    client = _client(args)
    start, end = week_window(args.week)
    try:
        board = fetch_week(client, regions=args.regions)
        # The same guard edge/pickem_free.py added on 2026-08-24, for the same
        # reason: a book's board is not one week, and a duplicate home team
        # means the window is catching two slates. It RAISES rather than
        # picking one, which is what stopped preseason lines shipping as Week 1.
        games = filter_to_slate(board, window_start=start, window_end=end)
    except Exception as e:                       # StaleOdds, NoApiKey, HTTP...
        print(f"\n{type(e).__name__}: {e}")
        print("\nNothing captured. The market reading is the time-critical "
              "half -- fix this and re-run before the deadline.")
        raise SystemExit(1)
    print(f"live slate for week {args.week} "
          f"({start:%a %b %d} -> {end:%a %b %d} ET): {len(games)} of "
          f"{len(board)} board events")
    # Keyed on (away, home): filter_to_slate has already guaranteed one game
    # per home team, and the pair makes the CBS-vocabulary join explicit.
    live = {(_cbs_abbr(g.away_abbr), _cbs_abbr(g.home_abbr)): g for g in games}

    # NO READING IS EVER "TAKEN NOW". The row must carry the moment the PRICES
    # were observed -- the scan's finish time -- whether this is a backfill or
    # the ordinary Sunday-noon run. See _board_instant for the 6-13 hour lie
    # this replaces.
    captured, board_age = _board_instant(client)
    pinned = getattr(client, "scan_id", None)
    if pinned is not None:
        lag = int((datetime.datetime.now(datetime.timezone.utc)
                   - datetime.datetime.fromisoformat(
                       captured.replace("Z", "+00:00"))).total_seconds())
        print(f"BACKFILL from scan {pinned}: captured_at = {captured} "
              "(the scan's own finish time, not now)")
        print(f"  board_age_seconds = 0: a pinned scan IS the board at that "
              f"instant. This recovery ran {lag // 3600}h{(lag % 3600) // 60:02d}m "
              "later, which is a fact about the recovery, not about the reading.")
    elif board_age is None:
        print(f"captured_at = {captured} (paid feed: pulled just now)")
    else:
        print(f"captured_at = {captured}  |  board was {board_age // 60} min "
              f"old when this ran")
        if board_age > 2 * 3600:
            # Not fatal -- an honestly-stamped stale reading is still a
            # reading, and the alternative is no reading at all. But the whole
            # point of PICKEM_MODEL.md section 6 is capturing as late as
            # legally possible, so a deadline read off a half-day-old board
            # has to be visible rather than inferred from a column.
            print(f"  WARNING: {board_age // 3600}h{(board_age % 3600) // 60:02d}m "
                  "stale. This deadline's reading is not the market at this "
                  "deadline. Collect first: "
                  "`python3 scripts/odds_collect.py --profile pickem_nfl` "
                  "(deploy/pickem-capture@.service now does this itself).")

    if args.market_only:
        # --market-only must SUPPRESS CBS's numbers, not merely tolerate their
        # absence. Until 2026-09-09 it was consulted only when the CSV was
        # empty, so the six timers would have banked the CSV's *provisional*
        # transcription as though it were verified -- into an append-only file,
        # with no provenance column to tell the two apart afterwards.
        cbs_rows = [{"away_abbr": a, "home_abbr": h} for (a, h) in live]
        print(f"MARKET-ONLY: banking {len(cbs_rows)} market readings and NO CBS "
              "numbers. Fill in data/pickem_current_week.csv later and re-run "
              "the same snapshot label to complete these rows.")
        if not cbs_rows:
            _missed(args,
                    f"Nothing on the live slate falls in week {args.week}. "
                    "Nothing written.",
                    f"The board held {len(board)} event(s) and none of them are "
                    f"in the week-{args.week} window. Either the week is wrong "
                    "or the board was never collected: "
                    "`python3 scripts/odds_collect.py --profile pickem_nfl`.")
            return

    snaps, missing = [], []
    for r in cbs_rows:
        g = live.get((_cbs_abbr(r["away_abbr"]), _cbs_abbr(r["home_abbr"])))
        if g is None:
            missing.append(f'{r["away_abbr"]}@{r["home_abbr"]}')
        snaps.append(Snapshot(
            season=args.season, week=args.week, snapshot=args.snapshot,
            captured_at=captured,
            away_team=r["away_abbr"], home_team=r["home_abbr"],
            kickoff_utc=(g.kickoff if g else r.get("kickoff_utc", "")),
            cbs_line_home=None if args.market_only else _f(r.get("cbs_line_home")),
            comm_pct_away=None if args.market_only else _f(r.get("comm_pct_away")),
            comm_pct_home=None if args.market_only else _f(r.get("comm_pct_home")),
            market_line_home=(g.live_line if g else None),
            market_line_mean=(g.live_line_mean if g else None),
            market_line_median=(g.live_line_median if g else None),
            market_total=(g.total if g else None),
            n_books=(g.n_books if g else 0),
            book_disagreement=(g.book_spread if g else None),
            book_lines=(g.book_lines if g else None),
            board_age_seconds=(board_age if g else None),
        ))

    # A DEADLINE WITH NO MARKET READING AT ALL MUST NOT BANK. With CBS rows on
    # file and an empty board, every Snapshot carries market_line_home=None,
    # append() writes them all, and those rows then OWN the
    # (season, week, snapshot) key. complete() fills the CBS half only
    # (edge/pickem_log.CBS_FILLABLE) because a market reading is a claim about
    # one instant and cannot be moved onto a row stamped with another -- so the
    # label is permanently occupied by rows that will never carry the number
    # the snapshot exists to record.
    #
    # The CBS half is re-derivable from data/pickem_current_week.csv whenever.
    # The market half is not. So refuse, and leave the label free for a re-run.
    if snaps and not any(s.market_line_home is not None for s in snaps):
        _missed(args,
                f"NOT ONE of the {len(snaps)} game(s) has a market reading. "
                "Nothing written.",
                "Banking CBS-only rows would take the "
                f"(season {args.season}, week {args.week}, "
                f"snapshot {args.snapshot!r}) key for good -- complete() can "
                "fill the CBS half of an existing row but never the market "
                "half. Collect the board and re-run this SAME label: "
                "`python3 scripts/odds_collect.py --profile pickem_nfl`.")
        return

    written = append(snaps)
    # append() DROPS a row whose key already exists, so on its own it cannot
    # carry out the workflow PICKEM_WEEKLY.md promises: bank the market now,
    # add CBS's numbers later under the same label. complete() is that second
    # half -- it fills columns never measured and refuses to overwrite ones
    # that were, so the log stays append-only in the sense that matters.
    completed, skipped = complete(snaps)
    print(f"\nwrote {written} new, completed {completed} -> {LINE_LOG.name}")
    if not written and not completed and not skipped:
        # Every counter zero has TWO causes and they are opposites. If the log
        # already holds this label, this is the documented second run of a
        # two-half capture and it is success. If it does not, this run banked
        # nothing -- and at a deadline that is the permanent miss.
        existing = _already_logged(args.season, args.week, args.snapshot)
        if existing:
            print(f"  (nothing to do: this snapshot was already recorded in "
                  f"full -- {existing} row(s) on file)")
        else:
            _missed(args,
                    f"wrote 0, completed 0, skipped 0, and no row for "
                    f"(season {args.season}, week {args.week}, "
                    f"snapshot {args.snapshot!r}) exists in {LINE_LOG.name}. "
                    "This run banked nothing.",
                    "Check that data/pickem_current_week.csv holds this week "
                    "(or pass --market-only) and that the board covers the "
                    "slate.")
            return
    elif completed:
        print("  (completed = existing rows that gained a column they were "
              "missing, e.g. CBS's line arriving after the market half)")
    if skipped:
        # complete() fills the CBS half only. A market half is a claim about
        # one instant and cannot be moved onto a row stamped with another.
        print(f"\n  {skipped} row(s) still have no market reading -- "
              f"re-capture under a NEW label (e.g. {args.snapshot}-2), not "
              f"this one.")
        print("  Merging today's prices into a row stamped with an earlier "
              "instant would make that row claim a price it never observed, "
              "which nothing downstream can detect (see edge/pickem_log.py, "
              "CBS_FILLABLE).")
    if args.push:
        # Reuses odds_collect's helper rather than a second implementation:
        # it stages one path by name, rebases with autostash, and no-ops on an
        # unchanged file -- all three of which a timer needs.
        from scripts.odds_collect import push_snapshot
        push_snapshot(LINE_LOG, f"pickem_nfl wk{args.week} {args.snapshot}")
    if missing:
        print(f"no live market found for: {', '.join(missing)} "
              "(logged with CBS data only)")

    print("\n(run `python3 scripts/pickem_blocked.py` to see how much closer this "
          "brings the blocked experiments)")

    with_both = [s for s in snaps if s.cbs_line_home is not None and s.market_line_home is not None]
    if with_both:
        # sign convention fixed 2026-08-23 to match edge.pickem_log.cbs_offset:
        # offset = market - cbs, so that offset + drift = total edge. See 5j r3b.
        biases = [s.market_line_home - s.cbs_line_home for s in with_both]
        print(f"\nCBS offset this snapshot (market - cbs), n={len(biases)}:")
        print(f"  mean {sum(biases)/len(biases):+.2f} pts | "
              f"min {min(biases):+.1f} | max {max(biases):+.1f}")
        if args.snapshot == "post":
            print("  ^ the number 5f waited for. Do NOT subtract it from the model's")
            print("    edge -- you are graded against CBS, so offset is worth as much")
            print("    as drift (5j round 3c). Run scripts/pickem_transferability.py.")


if __name__ == "__main__":
    main()
