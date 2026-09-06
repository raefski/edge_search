-- Market data store: normalised sportsbook quotes, append-only.
--
-- WHY A STORE AND NOT JUST A SNAPSHOT
-- data/arb_snapshot.json holds *opportunities*, which are derived. Three
-- consumers (arbitrage, DFS, pick'em) all need the same underlying fact -- a
-- price, on a side, at a book, at a time -- and each was deriving it from a
-- different source. This table is that fact, once.
--
-- WHY APPEND-ONLY
-- Scraping cannot retro-fetch. The Odds API's /historical endpoints could
-- (at 10x cost), and dropping them means the only history that will ever
-- exist is the history collected from today forward. So every scan appends
-- rather than replaces, even before anything reads it back: a day not
-- collected is a day that cannot be recovered at any price.

CREATE TABLE IF NOT EXISTS scan (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    profile      TEXT    NOT NULL,
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    quote_count  INTEGER NOT NULL DEFAULT 0,
    event_count  INTEGER NOT NULL DEFAULT 0,
    -- Copied out of stats_json into its own column because it is the one
    -- number that must be checked after every scan. In a one-shot scan it
    -- can only mean two different bets landed on one GroupKey; see
    -- HANDOFF.md section 8.
    conflicts    INTEGER NOT NULL DEFAULT 0,
    ok           INTEGER NOT NULL DEFAULT 0,   -- 0 until finish_scan commits
    stats_json   TEXT
);

CREATE TABLE IF NOT EXISTS event (
    event_id      TEXT PRIMARY KEY,
    sport_key     TEXT NOT NULL,
    sport_title   TEXT,
    commence_time TEXT NOT NULL,
    home_team     TEXT,
    away_team     TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);

-- One row per (scan, market group, side, book).
--
-- `group_key` is the canonical string form of arb's GroupKey --
-- "event_id|market|subject|point". It is stored rather than recomputed
-- because it is both the primary key component that makes NULL subjects and
-- NULL points behave (SQLite treats NULLs as distinct inside a composite
-- PRIMARY KEY, so a NULL subject would defeat de-duplication entirely) and
-- the natural join key for reading one market's history back out.
CREATE TABLE IF NOT EXISTS quote (
    scan_id     INTEGER NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    group_key   TEXT    NOT NULL,
    event_id    TEXT    NOT NULL REFERENCES event(event_id),
    sport_key   TEXT    NOT NULL,
    market      TEXT    NOT NULL,   -- canonical key, e.g. pitcher_strikeouts
    subject     TEXT,               -- player/team the prop is about; NULL for game markets
    point       REAL,               -- the line both sides of the group share
    side        TEXT    NOT NULL,   -- over|under|home|away|yes|no|draw|<slug>
    book        TEXT    NOT NULL,
    decimal     REAL    NOT NULL,
    captured_at TEXT    NOT NULL,
    PRIMARY KEY (scan_id, group_key, side, book)
) WITHOUT ROWID;

-- Reads this store serves, and the index each one needs:
--   "every quote from the latest scan for MLB"      -> idx_quote_scan_sport
--   "this player's prop history across scans"       -> idx_quote_subject
--   "this market group's line movement"             -> idx_quote_group
CREATE INDEX IF NOT EXISTS idx_quote_scan_sport ON quote(scan_id, sport_key, market);
CREATE INDEX IF NOT EXISTS idx_quote_subject    ON quote(sport_key, subject, market);
CREATE INDEX IF NOT EXISTS idx_quote_group      ON quote(group_key, captured_at);
CREATE INDEX IF NOT EXISTS idx_event_sport_time ON event(sport_key, commence_time);
CREATE INDEX IF NOT EXISTS idx_scan_profile     ON scan(profile, ok, id DESC);
