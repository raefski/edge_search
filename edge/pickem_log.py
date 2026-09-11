"""Append-only snapshot log -- the data that unblocks PICKEM_MODEL.md 5f.

Four experiments are currently impossible because the data was never
recorded. Each row written here is a down payment on one of them:

  * CBS post-offset isolation. There are NO historical CBS lines anywhere --
    the backtest's "pool line" is a sportsbook opener used as a proxy. To
    separate CBS's own house shading from genuine post-Tuesday drift we need
    CBS's number AND a market reading from the same moment. That is the
    `post` snapshot: cbs_line_home alongside market_line_home.
  * Line-movement velocity. Needs more than two readings per game. Every
    extra snapshot between post and lock builds that series.
  * Sharp-book agreement. The historical file is single-book. `book_lines`
    stores each book's number, so cross-book disagreement becomes
    measurable after the fact.
  * Public-pick fading. CBS community percentages exist nowhere
    historically. Captured here per game, per snapshot.

WHERE THIS LIVES, and why not in data/pickem/: everything written here is
public market information (lines, totals, CBS's community percentages), so
it is COMMITTED -- it needs to accumulate across a season, survive a
Streamlit Cloud rebuild, and be diffable. Adam's own picks, standings, and
money stay in data/pickem/, which is gitignored. Do not merge the two.

Append-only on purpose: a snapshot is a claim about what the world looked
like at one instant, and rewriting history would quietly destroy exactly
the drift signal this exists to measure. `complete` is the one narrow
exception and stays inside that rule: it fills columns that were never
measured and refuses to touch ones that were.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
LINE_LOG = ROOT / "data" / "pickem_line_log.csv"

#: Where a capture that did not happen gets recorded. Read and rendered at the
#: top of pages/4_*_Pickem.py, and appended to by
#: deploy/pickem-capture-failed@.service.
#:
#: ONE IMPLEMENTATION, because there are now three writers -- this module's
#: callers, scripts/pickem_pool_fetch.py, and the systemd unit -- and the
#: module docstring of edge/pickem_week.py records what happened the last time
#: two copies of a shared rule were allowed to drift.
FAILURES_LOG = ROOT / "data" / "pickem_capture_failures.log"


def record_failure(source: str, reason: str,
                   path: Path | str | None = None) -> None:
    """Append one line about a capture that did not happen. Never raises.

    WHY A LOG LINE AND NOT JUST A NON-ZERO EXIT
    The alerting chain (OnFailure= -> pickem-capture-failed@.service -> this
    file -> the page banner) is armed by the UNIT failing. Two of the unit's
    four commands are deliberately soft-failing `ExecStartPre=-` /
    `ExecStartPost=-`, so that a dead CBS session can never cancel the
    market-half capture -- which means a failure in one of those exits
    non-zero into a `-` that discards it, the unit goes green, and nothing
    anywhere says a word.

    That mattered more after 2026-09-11. comm_pct_* used to be backfillable
    from pickem_current_week.csv at any later time, so a lost merge pass cost
    nothing; now that `complete` refuses a non-contemporaneous CBS half (see
    CBS_TIMED_FILLABLE), a merge that silently does not happen at the deadline
    loses those percentages permanently.

    Best-effort by construction: this runs on the failure path, and a problem
    writing the log must not replace the original error with a confusing
    second one.
    """
    target = Path(path) if path is not None else FAILURES_LOG
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        one_line = " ".join(str(reason).split())[:300]
        with target.open("a") as fh:
            fh.write(f"{stamp} | {source} | {one_line}\n")
    except OSError:
        pass

# 'post' = the moment CBS's line is first seen (Tuesday, the freeze).
# 'lock' = last reading before that day's picks deadline.
# anything else = a free-form mid-week reading, useful for velocity.
SNAPSHOTS = ("post", "lock")

FIELDS = [
    "season", "week", "snapshot", "captured_at",
    "away_team", "home_team", "kickoff_utc",
    "cbs_line_home", "comm_pct_away", "comm_pct_home",
    "market_line_home", "market_line_mean", "market_line_median",
    "market_total", "n_books", "book_disagreement", "book_lines_json",
    # ADDED 2026-09-09, deliberately at the END so every row already written
    # keeps its meaning -- a column inserted in the middle would silently
    # re-key the whole committed history.
    #
    # How stale the board already was when the capture ran, in seconds:
    # captured_at + board_age_seconds = the moment the capture process ran.
    # Before this column existed, captured_at was utcnow() and the answer was
    # unrecoverable: every lock/midweek row was 6-13 hours older than it
    # claimed (the collection timer runs 12:20 and 23:20; lock-sun fired at
    # 12:00). Blank means there was no scan to age -- the paid Odds API.
    #
    # 0 has TWO meanings and both are "the board was current":
    #   * a timer run whose service collected the board immediately before
    #     capturing (deploy/pickem-capture@.service does exactly that), and
    #   * a BACKFILL. `--at <deadline>` resolves to the newest scan finishing
    #     at or before that instant, so a pinned scan IS the board as it stood
    #     then. Measuring wall-clock-now minus board time there recorded when
    #     the RECOVERY ran -- 127928 seconds for a well-targeted Tuesday
    #     reading rescued on Wednesday night -- which would have made every
    #     backfilled deadline look hopelessly late to section 6's "how late can
    #     we legally capture?" analysis. The recovery lag is printed by
    #     scripts/pickem_capture.py instead; it is not a property of the
    #     reading. See scripts/pickem_capture._board_instant.
    "board_age_seconds",
]


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Snapshot:
    season: int
    week: int
    snapshot: str
    away_team: str
    home_team: str
    captured_at: str = ""
    kickoff_utc: str = ""
    cbs_line_home: float | None = None
    comm_pct_away: float | None = None
    comm_pct_home: float | None = None
    market_line_home: float | None = None
    market_line_mean: float | None = None
    market_line_median: float | None = None
    market_total: float | None = None
    n_books: int = 0
    book_disagreement: float | None = None
    book_lines: dict[str, float] | None = None
    board_age_seconds: int | None = None
    #: When the CBS half of this snapshot was actually read, ISO-8601. Not a
    #: log column -- it is the evidence `complete` uses to decide whether
    #: comm_pct_* may be merged into an existing row. Empty means unknown,
    #: which is treated as "cannot prove contemporaneous" and so refuses.
    cbs_fetched_at: str = ""

    def as_row(self) -> dict:
        d = {
            "season": self.season, "week": self.week, "snapshot": self.snapshot,
            "captured_at": self.captured_at or utcnow(),
            "away_team": self.away_team, "home_team": self.home_team,
            "kickoff_utc": self.kickoff_utc,
            "cbs_line_home": _num(self.cbs_line_home),
            "comm_pct_away": _num(self.comm_pct_away),
            "comm_pct_home": _num(self.comm_pct_home),
            "market_line_home": _num(self.market_line_home, 3),
            "market_line_mean": _num(self.market_line_mean, 3),
            "market_line_median": _num(self.market_line_median, 3),
            "market_total": _num(self.market_total, 3),
            "n_books": self.n_books,
            "book_disagreement": _num(self.book_disagreement, 2),
            "book_lines_json": json.dumps(self.book_lines, sort_keys=True) if self.book_lines else "",
            "board_age_seconds": ("" if self.board_age_seconds is None
                                  else int(self.board_age_seconds)),
        }
        return d


def _num(v, places: int = 2):
    return "" if v is None else round(float(v), places)


def append(snapshots: list[Snapshot], path: Path | str = LINE_LOG) -> int:
    """Append rows, creating the file with a header if needed.

    De-dupes on (season, week, snapshot, home_team): re-running a capture
    for a snapshot already recorded is a no-op rather than a second row, so
    an accidental double-run cannot corrupt the series. Use a distinct
    snapshot label (e.g. 'wed-am') for a genuinely new reading.

    A colliding row is DROPPED, not merged -- so this alone cannot carry out
    the "bank the market now, add CBS's numbers later" workflow, which is the
    whole point of --market-only. Call `complete` after it for that half; the
    two together are what scripts/pickem_capture.py runs.
    """
    path = Path(path)
    _migrate_header(path)
    existing = set()
    if path.exists():
        with path.open() as f:
            for r in csv.DictReader(f):
                existing.add((r["season"], r["week"], r["snapshot"], r["home_team"]))

    fresh = [s for s in snapshots
             if (str(s.season), str(s.week), s.snapshot, s.home_team) not in existing]
    if not fresh:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        for s in fresh:
            w.writerow(s.as_row())
    return len(fresh)


def _migrate_header(path: Path) -> None:
    """Widen an existing log to the current FIELDS. Values are never touched.

    A column added to FIELDS would otherwise corrupt the file on the very next
    append: DictWriter emits a row of N+1 values under a header of N names, so
    every row written after the change is off by one from the header that
    describes it -- in a committed, append-only CSV that git is the database
    for, and with no error anywhere.

    Only ADDITIONS are migrated, and only by appending the new names to the
    header; a file carrying a column this module no longer knows about is
    refused rather than silently narrowed, because dropping a recorded
    measurement is exactly what this log exists not to do.
    """
    if not path.exists():
        return
    with path.open() as f:
        header = next(csv.reader(f), None)
    if header is None or header == FIELDS:
        return
    unknown = [c for c in header if c not in FIELDS]
    if unknown or header != FIELDS[:len(header)]:
        raise ValueError(
            f"{path} has columns this build does not know how to widen: "
            f"header={header}. Refusing to rewrite it -- reconcile by hand "
            f"rather than lose a recorded measurement.")

    with path.open() as f:
        rows = list(csv.DictReader(f))
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: (r.get(k) or "") for k in FIELDS})
    os.replace(tmp, path)


#: The columns that identify a row. Everything else is a MEASUREMENT, and
#: `complete` will fill a blank one but never overwrite a recorded one.
KEY_FIELDS = ("season", "week", "snapshot", "home_team")

#: THE ONLY COLUMNS `complete` MAY FILL, and why the list is this short.
#:
#: A row's `captured_at` is a claim about EVERY number in that row: they were
#: all observed at that one instant. That is not decoration -- `cbs_offset`
#: below is only meaningful because market_line_home and cbs_line_home share
#: an instant, and scripts/pickem_transferability.py decides whether a reading
#: was actionable by comparing captured_at to kickoff.
#:
#: CBS's line is FROZEN from Tuesday until the games are played, and the
#: community percentages and the kickoff time are properties of the week, not
#: of a moment. Transcribing them on Thursday records the same values that
#: existed on Tuesday, so filling them later keeps the row's instant true.
#: That is exactly the "bank the market now, add CBS's numbers later"
#: workflow PICKEM_WEEKLY.md promises, and it is safe.
#:
#: THE MARKET IS NOT LIKE THAT. It moves by the minute. A capture that found
#: no board and banked market_line_home="" and n_books=0 at Tuesday 17:11 must
#: NOT have Friday's -7.0 merged into it three days later, because the row
#: would then carry a Friday price under a Tuesday timestamp -- an
#: undetectable falsehood in an append-only log, and the precise failure
#: scripts/pickem_capture.py's --scan-id branch was written to prevent.
#: A genuinely new market reading needs a NEW LABEL (lock-sun-2), which
#: `append` will happily write as its own row with its own captured_at.
#: SPLIT 2026-09-11. These two groups used to be one tuple, and the whole
#: group was fillable whenever it was blank.
#:
#: cbs_line_home and kickoff_utc are FROZEN FACTS. CBS sets its line once and
#: never moves it -- that is the entire premise of this project -- and a
#: kickoff is the published schedule. Learning either one later does not make
#: it less true of the moment the row describes, so they fill from any
#: reading, at any distance in time.
#:
#: comm_pct_* are NOT frozen. They move all week as the pool votes. On this
#: file's own week 1, TEN went 23/77 Thursday to 38/62 Friday -- fifteen
#: points in a day -- and LAR 19/81 to 27/73.
#:
#: Filling them from a later reading is therefore the SAME bug MARKET_FIELDS
#: exists to refuse, just wearing the CBS half's clothes: the Tuesday `post`
#: row would carry Friday's percentages under a Tuesday captured_at, and
#: nothing downstream could tell. It was live and one command away -- the
#: project's stated next step is re-running the `post` label to fill in CBS's
#: verified lines, and doing that on 2026-09-11 would have written Friday's
#: numbers into a Tuesday row.
CBS_FROZEN_FILLABLE = ("cbs_line_home", "kickoff_utc")
CBS_TIMED_FILLABLE = ("comm_pct_away", "comm_pct_home")

#: Kept as the union so existing callers and tests that ask "what is the CBS
#: half" still get one answer.
CBS_FILLABLE = CBS_FROZEN_FILLABLE + CBS_TIMED_FILLABLE

#: How far apart a CBS reading and a row's captured_at may be and still
#: describe the same instant.
#:
#: The normal flow is one systemd unit run: ExecStartPre fetches CBS,
#: ExecStart banks the market half, ExecStartPost merges them -- minutes
#: apart, and TimeoutStartSec is 300s. The gap this must REJECT is days.
#: Two hours is comfortably above the first and far below the second; it is
#: not a claim that percentages are stable for two hours, only that beyond
#: it there is no case for calling two readings contemporaneous.
CONTEMPORANEOUS_SECONDS = 2 * 3600

#: Filling any of these would date-stamp a measurement with someone else's
#: instant. `complete` counts them and refuses.
MARKET_FIELDS = ("market_line_home", "market_line_mean", "market_line_median",
                 "market_total", "n_books", "book_disagreement",
                 "book_lines_json", "board_age_seconds")


class CompleteResult(NamedTuple):
    """What `complete` did: rows filled, and rows it refused to fill.

    Deliberately NOT an int. The old signature returned one number and every
    call site read it as "rows changed"; a skip is a different event that the
    operator has to act on (re-capture under a new label), so it must not be
    expressible as a quieter version of the same count.
    """

    changed: int
    skipped: int
    #: Rows whose comm_pct_* were left blank because the CBS reading offered
    #: for them came from a different moment. Reported rather than silently
    #: dropped: it means those percentages were never measured at that
    #: deadline and never can be, which is an operator fact, not a no-op.
    refused_timed: int = 0


def _parse_iso(value: str):
    """ISO-8601 with or without a trailing Z, or None if it is not a time."""
    if not value:
        return None
    try:
        ts = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)


def _contemporaneous(fetched_at: str, captured_at: str) -> bool:
    """Do these two readings describe the same instant?

    Unknown is NOT contemporaneous. A CBS half with no timestamp is every row
    written before 2026-09-11, including the whole committed week 1, and
    guessing "probably fine" for those is how a fifteen-point swing gets
    filed under the wrong day.
    """
    a, b = _parse_iso(fetched_at), _parse_iso(captured_at)
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= CONTEMPORANEOUS_SECONDS


def _is_blank(field: str, value) -> bool:
    """Has this column never been measured?

    `n_books` is the one field where 0 is an absence rather than a reading:
    it counts the books that priced the game, so zero means "no market half
    here yet", not "a market reading of zero books". Preserving it would make
    a row permanently unfillable.
    """
    if value in ("", None):
        return True
    return field == "n_books" and str(value).strip() in ("0", "0.0")


def complete(snapshots: list[Snapshot],
             path: Path | str = LINE_LOG) -> CompleteResult:
    """Fill the CBS half of rows that already exist. Returns (changed, skipped).

    WHY THIS EXISTS -- the bug it fixes, 2026-09-09
    `append` de-dupes on (season, week, snapshot, home_team) and DROPS a
    colliding row. PICKEM_WEEKLY.md promises the opposite: "capture the market
    now, fill CBS in afterwards with the same --snapshot label". Under append
    alone that second run wrote ZERO rows, so every market-only row the timers
    banked stayed CBS-less forever -- and scripts/pickem_transferability.py
    skips any game with no CBS line, so it printed "Nothing to measure yet"
    against a log that was filling up nicely. The project's only accumulating
    dataset was being silently half-discarded.

    WHY IT STILL RESPECTS APPEND-ONLY
    A snapshot is a claim about one instant, and rewriting one destroys the
    drift signal the log exists to measure. So this fills ONLY columns that are
    currently blank and NEVER overwrites a recorded measurement -- including
    `captured_at`. Two runs of the same label cannot disagree about a number,
    only complete each other. A genuinely new reading needs a new label.

    AND WHY "BLANK" IS NOT ENOUGH -- the second bug, found 2026-09-09
    The first version filled ANY blank column, which quietly reopened the hole
    from the other side. A market-only capture that found no board banks
    market_line_home="" and n_books=0; three days later the same label is
    re-run, the board is up, and the market half was merged into a row whose
    captured_at still said Tuesday. Demonstrated:

        after tuesday: captured_at 2026-09-08T17:11:20Z, market_line_home ''
        complete() changed: 1
        after friday:  captured_at 2026-09-08T17:11:20Z, market_line_home -7.0

    Nothing downstream can detect that, and `cbs_offset` consumes it directly.
    So the fillable set is now explicit (CBS_FILLABLE) rather than "whatever
    happens to be empty", and a refused market half is COUNTED and reported --
    it means a reading is genuinely missing, which is an operator action (a new
    label), not a silent nothing.

    The rewrite is atomic (tmp file + os.replace) because this is the one place
    that touches an existing row: a crash mid-write on an append-only log that
    git is the database for would be unrecoverable.
    """
    path = Path(path)
    if not path.exists():
        return CompleteResult(0, 0)
    _migrate_header(path)

    with path.open() as f:
        rows = list(csv.DictReader(f))

    incoming = {tuple(str(getattr(s, k)) for k in KEY_FIELDS): (s.as_row(), s)
                for s in snapshots}
    changed = skipped = refused_timed = 0
    for r in rows:
        found = incoming.get(tuple(str(r.get(k, "")) for k in KEY_FIELDS))
        if found is None:
            continue
        new, snap = found
        touched = False
        for field in CBS_FROZEN_FILLABLE:
            if _is_blank(field, r.get(field)) and not _is_blank(field, new.get(field)):
                r[field] = new[field]
                touched = True

        # The moving half. Only merge it when the CBS reading and this row
        # describe the same instant; otherwise leave the column blank and say
        # so. Blank is the honest answer -- those percentages were never read
        # at this deadline, and unlike the frozen fields they cannot be
        # recovered from a later look.
        offered = [f for f in CBS_TIMED_FILLABLE
                   if _is_blank(f, r.get(f)) and not _is_blank(f, new.get(f))]
        if offered:
            if _contemporaneous(snap.cbs_fetched_at, r.get("captured_at", "")):
                for field in offered:
                    r[field] = new[field]
                touched = True
            else:
                refused_timed += 1
        changed += touched
        # A market half this row does not have and cannot be given. Keyed on
        # market_line_home alone -- the number the model actually consumes --
        # rather than on any MARKET_FIELD, because board_age_seconds is blank
        # on every row written before 2026-09-09 and would otherwise report
        # the whole committed history as unrecorded on the next re-run.
        # Counted so the caller can name WHICH deadline is still missing: an
        # unbanked market reading is the one thing here that cannot be
        # recovered later at any price.
        if (_is_blank("market_line_home", r.get("market_line_home"))
                and not _is_blank("market_line_home",
                                  new.get("market_line_home"))):
            skipped += 1

    if not changed:
        return CompleteResult(0, skipped, refused_timed)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, path)
    return CompleteResult(changed, skipped, refused_timed)


def load(path: Path | str = LINE_LOG) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def cbs_offset(season: int, week: int, home_team: str,
               path: Path | str = LINE_LOG) -> float | None:
    """How far the market sat from CBS at post time: market_at_post - cbs_line.

    Sign convention chosen so that the two components of an edge simply ADD,
    which is what makes the decomposition safe to reason about:

        total_edge = market_at_lock - cbs_line
                   = (market_at_post - cbs_line) + (market_at_lock - market_at_post)
                   =        cbs_offset          +            drift

    READ THIS BEFORE USING IT -- two traps, both of which this project fell into.

    1. SIGN. An earlier version returned `cbs_line - market_at_post` and the
       docs prescribed `true_edge = (market_now - cbs_line) - cbs_bias`.
       Substituting gives `market_now + market_at_post - 2*cbs_line`, which
       DOUBLES the offset instead of removing it, and is wrong in exactly the
       case the correction exists for (CBS off the market, no drift: returns
       -2.0 where the answer is 0.0). Fixed 2026-08-23; nothing consumed it
       while it was wrong. Regression-tested in tests/test_pickem_infra.py.

    2. DO NOT SUBTRACT THIS FROM THE MODEL'S EDGE. The pool grades against
       CBS's number, so a point of offset is worth exactly as much as a point
       of drift -- both measure the same thing: how far the number you are
       scored on sits from the market's best estimate. Netting the offset out
       would DELETE real value, not isolate it. PICKEM_MODEL.md 5f originally
       framed it as noise to remove; see 5j round 3 for why that was wrong.

    What the decomposition is legitimately FOR: testing whether the two
    components are worth the same per point. Log both, and after a season
    regress the cover outcome on each separately. The null worth testing is
    `beta_offset == beta_drift`, not `beta_offset == 0`.

    Returns None until a `post` snapshot exists with both numbers.
    """
    for r in load(path):
        if (int(r["season"]) == season and int(r["week"]) == week
                and r["snapshot"] == "post" and r["home_team"] == home_team):
            if r["cbs_line_home"] and r["market_line_home"]:
                return float(r["market_line_home"]) - float(r["cbs_line_home"])
    return None
