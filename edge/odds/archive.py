"""Old odds scans, kept forever on the F: drive as runs of unchanged prices.

WHY
data/odds.db grew ~4 GB a week (24 GB by 2026-10-16, 6 weeks in) and 90% of
each scan repeats the scan before it: a price that did not move is written
again every collection. The history is the modeling record (props grading,
sigma fits, NHL rebuilds at lock, pick'em backfills, line movement) and can
never be re-scraped, so nothing is dropped -- it moves:

    data/odds.db          the HOT store: every scan of the last HOT_DAYS, at full
                          fidelity, where the collectors write (WAL on ext4)
    <ARCHIVE>/odds_YYYY-MM.db   older scans, one file per month, as RUNS
    <ARCHIVE>/../raw/     an exact copy of the store as it was before the first
                          archive (2026-10-16), untouched

The store's readers do not change: OddsStore.quotes / events / history read
an archived scan from here transparently, and the `scan` and `event` tables
stay complete in the hot store, so every "which scan ran before lock" query
still works. SQLite's WAL cannot run on F: (a 9p mount), which is why the
collectors' store stays on the Linux disk and only finished months move.

A RUN IS ONE PRICE HELD ACROSS CONSECUTIVE SCANS
(profile, group_key, side, book) at one decimal price, from the first scan of
that profile it appeared in to the last, with no scan of the profile in
between missing it. Every column of the original row is a function of the
run except `captured_at`: the run keeps the exact first and last observation
times, and an interior scan's row carries that scan's start time (within the
scan's ~1-2 minute window). The raw copy keeps the exact seconds. A price that
disappears for one scan (a book that failed) and comes back starts a new run,
so a scan is rebuilt exactly as it was collected -- verified on every archive
run before the hot rows are deleted (verify()).

LOOKUP
`span_idx` is an R*Tree over (profile number, scan sequence): "every run alive
at scan s of profile p" is one index probe, not a scan of the month.
`span(group_key, side, book)` serves a market's history.
"""
from __future__ import annotations

import os
import random
import sqlite3
from contextlib import closing
from pathlib import Path

ARCHIVE_DIR = Path(os.environ.get("EDGE_ODDS_ARCHIVE", "/mnt/f/edge_search/odds/archive"))
#: Scans younger than this stay in the hot store at full fidelity: everything
#: live reads (latest scan, 12 h line moves, this week's pick'em backfill).
HOT_DAYS = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan (
    id INTEGER PRIMARY KEY, profile TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, quote_count INTEGER, event_count INTEGER, conflicts INTEGER,
    ok INTEGER, stats_json TEXT,
    seq INTEGER NOT NULL                       -- ordinal within profile, this file
);
CREATE UNIQUE INDEX IF NOT EXISTS scan_seq ON scan(profile, seq);
CREATE TABLE IF NOT EXISTS event (
    event_id TEXT PRIMARY KEY, sport_key TEXT NOT NULL, sport_title TEXT,
    commence_time TEXT NOT NULL, home_team TEXT, away_team TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_no (profile TEXT PRIMARY KEY, no INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS span (
    id INTEGER PRIMARY KEY, profile TEXT NOT NULL, group_key TEXT NOT NULL,
    event_id TEXT NOT NULL, sport_key TEXT NOT NULL, market TEXT NOT NULL,
    subject TEXT, point REAL, side TEXT NOT NULL, book TEXT NOT NULL,
    decimal REAL NOT NULL,
    first_scan INTEGER NOT NULL, last_scan INTEGER NOT NULL,
    first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL,
    first_captured TEXT NOT NULL, last_captured TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS span_group ON span(group_key, side, book);
CREATE INDEX IF NOT EXISTS span_sport ON span(sport_key, market);
CREATE VIRTUAL TABLE IF NOT EXISTS span_idx USING rtree_i32(id, p0, p1, s0, s1);
"""

#: The quote columns, in the hot store's order.
COLS = ("scan_id", "group_key", "event_id", "sport_key", "market", "subject", "point",
        "side", "book", "decimal", "captured_at")


def month_file(started_at: str, root: Path = ARCHIVE_DIR) -> Path:
    return root / f"odds_{started_at[:7]}.db"


def available(root: Path = ARCHIVE_DIR) -> bool:
    return root.is_dir()


def _open(path: Path, write: bool = False) -> sqlite3.Connection:
    if write:
        conn = sqlite3.connect(str(path), timeout=60.0)
        conn.execute("PRAGMA journal_mode=DELETE")      # no WAL on a 9p mount
        conn.executescript(SCHEMA)
    else:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60.0)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _scan_ref(conn, scan_id: int):
    row = conn.execute("SELECT s.profile, s.seq, s.started_at, p.no FROM scan s "
                       "JOIN profile_no p ON p.profile = s.profile WHERE s.id=?",
                       (scan_id,)).fetchone()
    return row


def quotes(path: Path, scan_id: int, sport_key: str | None = None, markets=None,
           event_ids=None) -> list[dict]:
    """One archived scan's quotes, as the hot store's rows (dicts, COLS)."""
    with closing(_open(path)) as conn:
        ref = _scan_ref(conn, scan_id)
        if ref is None:
            return []
        sql = ("SELECT sp.* FROM span_idx ix JOIN span sp ON sp.id = ix.id "
               "WHERE ix.p0 <= ? AND ix.p1 >= ? AND ix.s0 <= ? AND ix.s1 >= ?")
        args: list = [ref["no"], ref["no"], ref["seq"], ref["seq"]]
        if sport_key:
            sql += " AND sp.sport_key=?"
            args.append(sport_key)
        if markets:
            sql += f" AND sp.market IN ({','.join('?' * len(markets))})"
            args.extend(markets)
        if event_ids:
            sql += f" AND sp.event_id IN ({','.join('?' * len(event_ids))})"
            args.extend(event_ids)
        return [_row(sp, scan_id, ref["started_at"]) for sp in conn.execute(sql, args)]


def _row(sp, scan_id: int, scan_started: str) -> dict:
    if scan_id == sp["first_scan"]:
        captured = sp["first_captured"]
    elif scan_id == sp["last_scan"]:
        captured = sp["last_captured"]
    else:
        captured = scan_started
    return {"scan_id": scan_id, "group_key": sp["group_key"], "event_id": sp["event_id"],
            "sport_key": sp["sport_key"], "market": sp["market"], "subject": sp["subject"],
            "point": sp["point"], "side": sp["side"], "book": sp["book"],
            "decimal": sp["decimal"], "captured_at": captured}


def events(path: Path, scan_id: int, sport_key: str | None = None) -> list[dict]:
    """Events that carry a quote in this archived scan, by commence time."""
    ids = {q["event_id"] for q in quotes(path, scan_id, sport_key)}
    if not ids:
        return []
    with closing(_open(path)) as conn:
        rows = conn.execute(f"SELECT * FROM event WHERE event_id IN ({','.join('?' * len(ids))}) "
                            "ORDER BY commence_time", list(ids)).fetchall()
        return [dict(r) for r in rows]


def history(group_key: str, side: str | None = None, book: str | None = None,
            files: list[Path] | None = None) -> list[dict]:
    """Every archived observation of one market group, one row per scan, from
    `files` (the store's `archived` table names them; default: ARCHIVE_DIR)."""
    out = []
    for path in files if files is not None else sorted(ARCHIVE_DIR.glob("odds_*.db")):
        with closing(_open(path)) as conn:
            sql = ("SELECT sp.*, s.id AS sid, s.started_at AS sstart FROM span sp "
                   "JOIN scan s ON s.profile = sp.profile AND s.seq BETWEEN sp.first_seq AND sp.last_seq "
                   "WHERE sp.group_key=?")
            args: list = [group_key]
            if side:
                sql += " AND sp.side=?"
                args.append(side)
            if book:
                sql += " AND sp.book=?"
                args.append(book)
            out += [_row(r, r["sid"], r["sstart"]) for r in conn.execute(sql, args)]
    return out


def iter_quotes(sport_key: str, markets=None, files: list[Path] | None = None):
    """Every archived quote of one sport (optionally some markets), one row per
    (scan, group, side, book) -- what a whole-history fit reads."""
    for path in files if files is not None else sorted(ARCHIVE_DIR.glob("odds_*.db")):
        with closing(_open(path)) as conn:
            sql = ("SELECT sp.*, s.id AS sid, s.started_at AS sstart FROM span sp "
                   "JOIN scan s ON s.profile = sp.profile AND s.seq BETWEEN sp.first_seq AND sp.last_seq "
                   "WHERE sp.sport_key=?")
            args: list = [sport_key]
            if markets:
                sql += f" AND sp.market IN ({','.join('?' * len(markets))})"
                args.extend(markets)
            for r in conn.execute(sql, args):
                yield _row(r, r["sid"], r["sstart"])


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _key(q) -> tuple:
    return (q["group_key"], q["side"], q["book"])


def _value(q) -> tuple:
    return (q["event_id"], q["sport_key"], q["market"], q["subject"], q["point"], q["decimal"])


def archive_scans(hot: sqlite3.Connection, scan_ids: list[int], path: Path) -> dict:
    """Append these scans (one month's, id order) to `path` as runs. Returns
    counts. Does NOT touch the hot store -- the caller verifies, then deletes."""
    hot.row_factory = sqlite3.Row
    scans = [hot.execute("SELECT * FROM scan WHERE id=?", (sid,)).fetchone() for sid in scan_ids]
    out = _open(path, write=True)
    n_spans = n_quotes = 0
    try:
        out.execute("BEGIN")
        for prof in sorted({s["profile"] for s in scans}):
            no = out.execute("SELECT no FROM profile_no WHERE profile=?", (prof,)).fetchone()
            if no is None:
                no = (out.execute("SELECT COALESCE(MAX(no), 0) + 1 FROM profile_no").fetchone()[0],)
                out.execute("INSERT INTO profile_no VALUES (?, ?)", (prof, no[0]))
            no = no[0]
            seq = out.execute("SELECT COALESCE(MAX(seq), 0) FROM scan WHERE profile=?",
                              (prof,)).fetchone()[0]
            open_runs: dict = {}

            def close(key):
                nonlocal n_spans
                r = open_runs.pop(key)
                cur = out.execute(
                    "INSERT INTO span (profile, group_key, event_id, sport_key, market, subject,"
                    " point, side, book, decimal, first_scan, last_scan, first_seq, last_seq,"
                    " first_captured, last_captured) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (prof, key[0], *r["value"][:5], key[1], key[2], r["value"][5],
                     r["first_scan"], r["last_scan"], r["first_seq"], r["last_seq"],
                     r["first_captured"], r["last_captured"]))
                out.execute("INSERT INTO span_idx VALUES (?, ?, ?, ?, ?)",
                            (cur.lastrowid, no, no, r["first_seq"], r["last_seq"]))
                n_spans += 1

            for s in (x for x in scans if x["profile"] == prof):
                seq += 1
                out.execute("INSERT INTO scan (id, profile, started_at, finished_at, quote_count,"
                            " event_count, conflicts, ok, stats_json, seq) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (s["id"], s["profile"], s["started_at"], s["finished_at"],
                             s["quote_count"], s["event_count"], s["conflicts"], s["ok"],
                             s["stats_json"], seq))
                seen = set()
                for q in hot.execute("SELECT * FROM quote WHERE scan_id=?", (s["id"],)):
                    n_quotes += 1
                    k, v = _key(q), _value(q)
                    seen.add(k)
                    r = open_runs.get(k)
                    if r is not None and r["value"] == v and r["last_seq"] == seq - 1:
                        r.update(last_scan=s["id"], last_seq=seq, last_captured=q["captured_at"])
                        continue
                    if r is not None:
                        close(k)
                    open_runs[k] = {"value": v, "first_scan": s["id"], "last_scan": s["id"],
                                    "first_seq": seq, "last_seq": seq,
                                    "first_captured": q["captured_at"],
                                    "last_captured": q["captured_at"]}
                for k in [k for k in open_runs if k not in seen]:
                    close(k)                     # absent this scan: the run ends
            for k in list(open_runs):
                close(k)
        ev_ids = {r[0] for sid in scan_ids
                  for r in hot.execute("SELECT DISTINCT event_id FROM quote WHERE scan_id=?", (sid,))}
        for eid in ev_ids:
            e = hot.execute("SELECT * FROM event WHERE event_id=?", (eid,)).fetchone()
            if e is not None:
                out.execute("INSERT OR REPLACE INTO event VALUES (?,?,?,?,?,?,?,?)", tuple(e))
        out.execute("COMMIT")
    except Exception:
        out.execute("ROLLBACK")
        raise
    finally:
        out.close()
    return {"scans": len(scans), "quotes": n_quotes, "spans": n_spans}


def verify(hot: sqlite3.Connection, scan_ids: list[int], path: Path, sample: int = 0,
           seed: int = 0) -> list[int]:
    """Scans whose archived rebuild differs from the hot rows (every column but
    an interior captured_at). `sample`=0 checks every scan. Empty = lossless."""
    ids = list(scan_ids)
    if sample and len(ids) > sample:
        ids = random.Random(seed).sample(ids, sample)
    bad = []
    for sid in ids:
        want = {tuple(r[c] for c in COLS[:-1]) for r in hot.execute(
            "SELECT * FROM quote WHERE scan_id=?", (sid,))}
        got = {tuple(r[c] for c in COLS[:-1]) for r in quotes(path, sid)}
        if want != got:
            bad.append(sid)
    return bad


def verify_counts(hot: sqlite3.Connection, scan_ids: list[int], path: Path) -> list[int]:
    """Scans whose archived rebuild has a different number of quotes than the
    hot store holds -- every scan, one R*Tree count each."""
    bad = []
    with closing(_open(path)) as conn:
        for sid in scan_ids:
            ref = _scan_ref(conn, sid)
            n_hot = hot.execute("SELECT COUNT(*) FROM quote WHERE scan_id=?", (sid,)).fetchone()[0]
            n_arch = 0 if ref is None else conn.execute(
                "SELECT COUNT(*) FROM span_idx WHERE p0 <= ? AND p1 >= ? AND s0 <= ? AND s1 >= ?",
                (ref["no"], ref["no"], ref["seq"], ref["seq"])).fetchone()[0]
            if n_hot != n_arch:
                bad.append(sid)
    return bad
