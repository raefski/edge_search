"""SQLite-backed market data store: the one place a price lives.

WHY SQLITE
One writer (the systemd agent on one machine), several readers (the Streamlit
app, the CLI, backtest scripts). That is exactly the shape SQLite is best at,
it needs no server to babysit, and the file is trivially copyable. Postgres
would buy concurrent writers, which nothing here has.

WHY NOT KEEP USING data/arb_snapshot.json
The snapshot holds *opportunities* -- a derived, arbitrage-specific view. DFS
and pick'em need the prices underneath it, not the arbitrage conclusions, and
they need them from more than the current instant. A 4.6MB JSON re-parsed on
every read also cannot answer "what was this line yesterday" at all.

THE WRITE IS TRANSACTIONAL AND THE SCAN IS THE UNIT
A scan row is written with ok=0, quotes are inserted, and only then is the
scan marked ok=1. Readers only ever see ok=1 scans. A crashed or half-written
scan is therefore invisible rather than partially visible, which matters
because a partial board looks exactly like a real board with thin coverage --
the failure mode this repo keeps rediscovering (HANDOFF.md section 8: the
silent bug is the expensive one).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

SCHEMA = Path(__file__).with_name("schema.sql")
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "odds.db"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class QuoteRow:
    """One price, on one side, at one book, at one time."""
    group_key: str
    event_id: str
    sport_key: str
    market: str
    subject: str | None
    point: float | None
    side: str
    book: str
    decimal: float
    captured_at: str

    def as_tuple(self, scan_id: int) -> tuple:
        return (scan_id, self.group_key, self.event_id, self.sport_key,
                self.market, self.subject, self.point, self.side, self.book,
                self.decimal, self.captured_at)


@dataclass(frozen=True)
class EventRow:
    event_id: str
    sport_key: str
    sport_title: str | None
    commence_time: str
    home_team: str | None
    away_team: str | None


class OddsStore:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30.0,
                                    detect_types=0, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        # WAL so the Streamlit app can read while the agent writes. Without it
        # a scan in progress blocks every read for its whole duration, which
        # for the wide arb profile is ~200 seconds of a dead page.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA.read_text())

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "OddsStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- writing -------------------------------------------------------------

    def begin_scan(self, profile: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO scan (profile, started_at, ok) VALUES (?, ?, 0)",
            (profile, _iso(utcnow())))
        return int(cur.lastrowid)

    def write_events(self, events: Iterable[EventRow]) -> int:
        now = _iso(utcnow())
        rows = [(e.event_id, e.sport_key, e.sport_title, e.commence_time,
                 e.home_team, e.away_team, now, now) for e in events]
        if not rows:
            return 0
        # first_seen is preserved on conflict: it is the only record of when a
        # fixture was first priced, which is what makes an opening line
        # identifiable later.
        self.conn.executemany(
            "INSERT INTO event (event_id, sport_key, sport_title, commence_time,"
            " home_team, away_team, first_seen, last_seen)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(event_id) DO UPDATE SET"
            "   commence_time=excluded.commence_time,"
            "   home_team=COALESCE(excluded.home_team, event.home_team),"
            "   away_team=COALESCE(excluded.away_team, event.away_team),"
            "   last_seen=excluded.last_seen",
            rows)
        return len(rows)

    def write_quotes(self, scan_id: int, quotes: Iterable[QuoteRow]) -> int:
        rows = [q.as_tuple(scan_id) for q in quotes]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT OR REPLACE INTO quote (scan_id, group_key, event_id,"
            " sport_key, market, subject, point, side, book, decimal, captured_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def finish_scan(self, scan_id: int, stats: dict | None = None) -> None:
        """Mark a scan readable. Until this runs, readers cannot see it."""
        stats = stats or {}
        counts = self.conn.execute(
            "SELECT COUNT(*) AS q, COUNT(DISTINCT event_id) AS e"
            " FROM quote WHERE scan_id=?", (scan_id,)).fetchone()
        self.conn.execute(
            "UPDATE scan SET finished_at=?, quote_count=?, event_count=?,"
            " conflicts=?, stats_json=?, ok=1 WHERE id=?",
            (_iso(utcnow()), counts["q"], counts["e"],
             int(stats.get("price_conflicts") or 0),
             json.dumps(stats, default=str), scan_id))

    def abandon_scan(self, scan_id: int) -> None:
        """Delete a scan that failed. Leaving it at ok=0 would also hide it,
        but it would keep its quotes on disk forever for no reader."""
        self.conn.execute("DELETE FROM quote WHERE scan_id=?", (scan_id,))
        self.conn.execute("DELETE FROM scan WHERE id=?", (scan_id,))

    # --- reading -------------------------------------------------------------

    def scan(self, scan_id: int) -> sqlite3.Row | None:
        """One scan by id, committed or not -- callers check `ok` themselves.

        The counterpart to latest_scan, for a reader that wants a SPECIFIC
        moment rather than the newest one: a capture missed at its deadline is
        still recoverable from the scan that ran then: scan rows are kept
        forever, and an old scan's quotes are read from the F: archive. Returns the raw row so `finished_at` is available to stamp
        the backfilled record with the time it was really taken.
        """
        return self.conn.execute("SELECT * FROM scan WHERE id=?",
                                 (scan_id,)).fetchone()

    def latest_scan(self, profile: str | None = None,
                    max_age_seconds: float | None = None) -> sqlite3.Row | None:
        """The newest committed scan, optionally for one profile.

        `max_age_seconds` is the freshness contract: a caller that would rather
        have nothing than a stale price (DFS before lock) passes a tight one, a
        caller for whom yesterday is fine (a backtest) passes None.
        """
        sql = "SELECT * FROM scan WHERE ok=1"
        args: list = []
        if profile:
            sql += " AND profile=?"
            args.append(profile)
        sql += " ORDER BY id DESC LIMIT 1"
        row = self.conn.execute(sql, args).fetchone()
        if row is None or max_age_seconds is None:
            return row
        age = (utcnow() - datetime.fromisoformat(row["finished_at"])).total_seconds()
        return row if age <= max_age_seconds else None

    def archive_files(self) -> list[Path]:
        """Every archive file this store's old scans live in."""
        return [Path(r["file"]) for r in self.conn.execute(
            "SELECT DISTINCT file FROM archived ORDER BY file")]

    def iter_quotes(self, sport_key: str, markets: Sequence[str] | None = None):
        """Every quote of one sport, hot and archived, one row per (scan, group,
        side, book) -- for whole-history fits (scripts/nfl_sigma_fit.py)."""
        sql = "SELECT * FROM quote WHERE sport_key=?"
        args: list = [sport_key]
        if markets:
            sql += f" AND market IN ({','.join('?' * len(markets))})"
            args.extend(markets)
        yield from self.conn.execute(sql, args)
        files = self.archive_files()
        if files:
            from edge.odds import archive
            yield from archive.iter_quotes(sport_key, markets, files)

    def archived_file(self, scan_id: int) -> Path | None:
        """Where an archived scan's quotes live (edge/odds/archive.py), or None."""
        row = self.conn.execute("SELECT file FROM archived WHERE scan_id=?", (scan_id,)).fetchone()
        return Path(row["file"]) if row else None

    def quotes(self, scan_id: int, sport_key: str | None = None,
               markets: Sequence[str] | None = None,
               event_ids: Sequence[str] | None = None) -> list[sqlite3.Row]:
        """A scan's quotes -- from the archive on F: when the scan is older than
        archive.HOT_DAYS (same columns, dict rows)."""
        arch = self.archived_file(scan_id)
        if arch is not None:
            from edge.odds import archive
            return archive.quotes(arch, scan_id, sport_key, markets, event_ids)
        sql = "SELECT * FROM quote WHERE scan_id=?"
        args: list = [scan_id]
        if sport_key:
            sql += " AND sport_key=?"
            args.append(sport_key)
        if markets:
            sql += f" AND market IN ({','.join('?' * len(markets))})"
            args.extend(markets)
        if event_ids:
            sql += f" AND event_id IN ({','.join('?' * len(event_ids))})"
            args.extend(event_ids)
        return self.conn.execute(sql, args).fetchall()

    def events(self, scan_id: int, sport_key: str | None = None) -> list[sqlite3.Row]:
        """Events that actually carry a quote in this scan, joined to their meta."""
        arch = self.archived_file(scan_id)
        if arch is not None:
            from edge.odds import archive
            return archive.events(arch, scan_id, sport_key)
        sql = ("SELECT e.* FROM event e JOIN (SELECT DISTINCT event_id FROM quote"
               " WHERE scan_id=?")
        args: list = [scan_id]
        if sport_key:
            sql += " AND sport_key=?"
            args.append(sport_key)
        sql += ") q ON q.event_id = e.event_id ORDER BY e.commence_time"
        return self.conn.execute(sql, args).fetchall()

    def history(self, group_key: str, side: str | None = None,
                book: str | None = None, since: datetime | None = None) -> list[sqlite3.Row]:
        """Every observation of one market group, oldest first.

        This is the replacement for the Odds API's /historical endpoints: it
        cannot reach backwards, only forwards from the first scan, which is
        why collection should start before it is needed.
        """
        sql = "SELECT * FROM quote WHERE group_key=?"
        args: list = [group_key]
        if side:
            sql += " AND side=?"
            args.append(side)
        if book:
            sql += " AND book=?"
            args.append(book)
        if since:
            sql += " AND captured_at>=?"
            args.append(_iso(since))
        rows = list(self.conn.execute(sql, args).fetchall())
        files = self.archive_files()
        if files:
            from edge.odds import archive
            old = archive.history(group_key, side, book, files)
            if since:
                old = [r for r in old if r["captured_at"] >= _iso(since)]
            rows = old + rows
        return sorted(rows, key=lambda r: r["captured_at"])

    def opening_and_latest(self, group_key: str, side: str, book: str | None = None
                           ) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
        """First and last observed price for one side -- the two ends of a line
        move, which is the feature pick'em is actually built on."""
        rows = self.history(group_key, side=side, book=book)
        return (rows[0], rows[-1]) if rows else (None, None)

    # --- maintenance ---------------------------------------------------------

    def archive_old(self, hot_days: int | None = None, root: Path | None = None,
                    verify_sample: int = 300, log=print) -> dict:
        """Move every scan older than `hot_days` to the F: archive, month by
        month (edge/odds/archive.py). For each month: write the runs, check that
        EVERY archived scan rebuilds to its quote count and that a sample
        rebuilds row for row, and only then delete the hot rows. Nothing is
        deleted without a verified copy; a failed check stops the move."""
        from edge.odds import archive
        hot_days = archive.HOT_DAYS if hot_days is None else hot_days
        root = root or archive.ARCHIVE_DIR
        if not archive.available(root):
            return {"skipped": f"archive dir {root} not mounted"}
        cutoff = _iso(utcnow() - timedelta(days=hot_days))
        todo = self.conn.execute(
            "SELECT id, started_at FROM scan WHERE ok=1 AND started_at < ? AND id NOT IN"
            " (SELECT scan_id FROM archived) ORDER BY id", (cutoff,)).fetchall()
        by_month: dict = {}
        for r in todo:
            by_month.setdefault(r["started_at"][:7], []).append(r["id"])
        done = {"scans": 0, "quotes": 0, "spans": 0, "months": []}
        import shutil
        import tempfile
        for month, ids in sorted(by_month.items()):
            path = archive.month_file(month, root)
            # Built on the Linux disk and copied over whole: SQLite's page writes
            # to the 9p mount run at ~1 MB/s, a sequential copy at ~45 MB/s.
            with tempfile.TemporaryDirectory(dir=self.path.parent) as tmp:
                local = Path(tmp) / path.name
                if path.exists():
                    shutil.copyfile(path, local)          # appending to this month
                got = archive.archive_scans(self.conn, ids, local)
                counts_bad = archive.verify_counts(self.conn, ids, local)
                rows_bad = archive.verify(self.conn, ids, local, sample=verify_sample)
                if counts_bad or rows_bad:
                    raise RuntimeError(f"archive {path.name}: {len(counts_bad)} scans with the "
                                       f"wrong count, {len(rows_bad)} that rebuild differently "
                                       "-- hot rows kept")
                part = path.with_name(path.name + ".part")
                shutil.copyfile(local, part)
                part.replace(path)
            # and once more against the copy that will be read from now on
            if archive.verify(self.conn, ids, path, sample=min(verify_sample, 25) or 25, seed=1):
                raise RuntimeError(f"archive {path}: the copy on the archive drive does not "
                                   "rebuild -- hot rows kept")
            now = _iso(utcnow())
            for sid in ids:
                self.conn.execute("BEGIN")
                self.conn.execute("INSERT INTO archived VALUES (?, ?, ?)", (sid, str(path), now))
                self.conn.execute("DELETE FROM quote WHERE scan_id=?", (sid,))
                self.conn.execute("COMMIT")
            log(f"  {month}: {got['scans']} scans, {got['quotes']:,} quotes -> "
                f"{got['spans']:,} runs in {path}")
            for k in ("scans", "quotes", "spans"):
                done[k] += got[k]
            done["months"].append(month)
        return done

    def prune(self, keep_days: int = 400) -> int:
        """Retired 2026-10-16: old scans are ARCHIVED (archive_old), never
        deleted -- the history cannot be re-scraped."""
        raise RuntimeError("OddsStore.prune is retired: use archive_old() "
                           "(python3 -m harness run odds-archive)")

    def summary(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) n, MIN(started_at) first, MAX(started_at) last,"
            " SUM(quote_count) quotes FROM scan WHERE ok=1").fetchone()
        return {"scans": row["n"], "first_scan": row["first"],
                "last_scan": row["last"], "quotes": row["quotes"] or 0,
                "path": str(self.path),
                "size_mb": round(self.path.stat().st_size / 1e6, 1)
                if self.path.exists() else 0.0}
