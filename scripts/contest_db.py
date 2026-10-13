#!/usr/bin/env python3
"""Build data/contests.db: every DK contest export, every pre-lock projection,
and the per-entry features the GPP-meta and accuracy scripts read.

    python3 scripts/contest_db.py                    # build / update (idempotent)
    python3 scripts/contest_db.py --rebuild          # drop everything, start over
    python3 scripts/contest_db.py --fetch-payouts 3  # also ask DK for <= 3 missing payout tables
    python3 scripts/contest_db.py --report           # print what is in the DB, incl. match table

WHAT GOES IN
  * data/contest-standings-<id>.csv + data/contest_meta.json  -> contest, entry,
    entry_player, player_result (the board), player_slot (%Drafted per roster slot)
  * data/dfs_proj_log*.csv (ours) and Dan's sheets in the repo root -> projection
  * DK draftables snapshots, current dir AND git history (salary/team/opp per draft
    group) -> slate_player, with the projection logs filling what they lack
  * payout tables: data/cache/dk_payouts/<cid>.json (DK's own, cached), copied to
    contests that share the template (fee, pool, places paid), flat for double-ups,
    else a rank-scaled copy of the nearest DK table (flagged) -> payout
  * entry_feature: summed / log-product ownership, our PRE-LOCK projected total,
    salary, duplicate count, payout, stack shape -- one row per entry

MATCHING A CONTEST TO ITS PROJECTIONS IS BY DRAFT GROUP, never by "nearest slate"
(the pitfall that voided NHL_STATUS.md 6b's first grading). The one exception is
MLB's main log, which never recorded a draft group: it matches on the contest's own
slate DATE (from DK's metadata) and is then CHECKED -- the board's ownership must be
>= 80% covered by the log, and if the log carries teams nobody on the board was
drafted from, the log was a different (bigger) slate and its ownership is not
graded against this contest. Every match and every rejection is in
contest_match, with the reason.

Idempotent: a contest whose export file is unchanged (sha1) is not re-read.
Projections, slate players, payouts, matches and features are recomputed every
run (seconds). The DB is gitignored (data/*.db) -- Dan's paid sheet may live in it.
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402  (the board's own key; NEVER edge.dfs.norm here)

DB_PATH = ROOT / "data" / "contests.db"
META = ROOT / "data" / "contest_meta.json"
PAYOUT_CACHE = ROOT / "data" / "cache" / "dk_payouts"
AUTOLOG = ROOT / "data" / "cache" / "dfs_autolog.json"
USER = "gorillabiscuit"
SCHEMA_VERSION = 2

#: DK roster slot codes per sport, as they appear in an export's Lineup column.
SLOTS = {
    "MLB": {"P", "C", "1B", "2B", "3B", "SS", "OF"},
    "NFL": {"QB", "RB", "WR", "TE", "FLEX", "DST"},
    "NCAAF": {"QB", "RB", "WR", "FLEX", "S-FLEX"},
    "NHL": {"C", "W", "D", "G", "UTIL"},
    "NBA": {"PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"},
    "NASCAR": {"D"},
    "MMA": {"F", "CPT"},
}
CPT_MULT = 1.5

#: sport -> [(log glob, player column, draft-group column or None[, variant])]
OUR_LOGS = {
    "NFL": [("data/dfs_proj_log_nfl.csv", "player", "gid")],
    "NCAAF": [("data/dfs_proj_log_ncaaf.csv", "player", "gid")],
    "NHL": [("data/dfs_proj_log_nhl.csv", "player", "gid")],
    "NASCAR": [("data/dfs_proj_log_nascar.csv", "driver", "gid")],
    "MMA": [("data/dfs_proj_log_mma.csv", "fighter", "gid")],
    "MLB": [("data/dfs_proj_log.csv", "player", None),
            ("data/dfs_proj_log_*_g*.csv", "player", "FILENAME"),
            # 7/25-7/31 pre-lock rows lost from dfs_proj_log.csv, recovered from the
            # calibration dashboard (identical to the log on every overlapping row).
            # Board players only, so they grade POINTS, never ownership.
            ("data/dfs_proj_log_mlb_recovered.csv", "player", None, "recovered")],
}
#: Dan's paid sheets (gitignored): (glob, sport, date regex)
DAN_FILES = [("Dan*_Projections_*.csv", "NFL"), ("Dans_Projections_*.csv", "NFL"),
             ("Dans_cfb-projections-*.csv", "NCAAF")]

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS contest (
    contest_id INTEGER PRIMARY KEY, sport TEXT, type TEXT, name TEXT,
    field INTEGER,            -- DK's entry count (contest_meta.json)
    entries_in_file INTEGER,  -- rows in the export
    live_entries INTEGER,     -- rows with a lineup (empty lineups never played)
    entry_fee REAL, places_paid INTEGER, prize_pool REAL, draft_group INTEGER,
    slate_date TEXT, guaranteed INTEGER, max_entries INTEGER, max_entries_seen INTEGER,
    n_entrants INTEGER, source_file TEXT, file_sha1 TEXT, imported_at TEXT,
    unfinished_entries INTEGER,  -- rows with TimeRemaining > 0: exported before the end
    final INTEGER);              -- 1 when no entry was still in progress at export
CREATE TABLE IF NOT EXISTS entry (
    contest_id INTEGER, entry_id INTEGER, rank INTEGER, entry_name TEXT,
    entrant TEXT, entry_index INTEGER, entry_of INTEGER, points REAL,
    lineup_raw TEXT, n_players INTEGER, parse_ok INTEGER, is_adam INTEGER,
    PRIMARY KEY (contest_id, entry_id));
CREATE TABLE IF NOT EXISTS entry_player (
    contest_id INTEGER, entry_id INTEGER, slot TEXT, player_norm TEXT);
CREATE INDEX IF NOT EXISTS ix_ep ON entry_player (contest_id, entry_id);
CREATE TABLE IF NOT EXISTS player_result (
    contest_id INTEGER, player_norm TEXT, name TEXT, roster_positions TEXT,
    pct_drafted REAL, cpt_pct REAL, fpts REAL,
    PRIMARY KEY (contest_id, player_norm));
CREATE TABLE IF NOT EXISTS player_slot (
    contest_id INTEGER, player_norm TEXT, roster_position TEXT, pct REAL);
CREATE TABLE IF NOT EXISTS payout (
    contest_id INTEGER, rank_lo INTEGER, rank_hi INTEGER, amount REAL, source TEXT);
CREATE TABLE IF NOT EXISTS projection (
    sport TEXT, draft_group INTEGER, date TEXT, player_norm TEXT, name TEXT,
    team TEXT, opp TEXT, pos TEXT, salary REAL, proj REAL, sd REAL, floor REAL,
    ceil REAL, own_pred REAL, own_cash_pred REAL, source TEXT, variant TEXT,
    log_file TEXT, logged_at TEXT, extra TEXT);
CREATE INDEX IF NOT EXISTS ix_proj ON projection (sport, draft_group, date);
CREATE TABLE IF NOT EXISTS slate_player (
    draft_group INTEGER, player_norm TEXT, name TEXT, team TEXT, opp TEXT,
    position TEXT, salary REAL, game TEXT, line TEXT, pp TEXT, bout TEXT, source TEXT,
    PRIMARY KEY (draft_group, player_norm));
CREATE TABLE IF NOT EXISTS contest_match (
    contest_id INTEGER PRIMARY KEY, sport TEXT, type TEXT, draft_group INTEGER,
    slate_date TEXT, method TEXT, proj_draft_group INTEGER, proj_date TEXT,
    proj_variant TEXT, n_board INTEGER, n_board_matched INTEGER, own_coverage REAL,
    n_log INTEGER, log_teams_not_on_board INTEGER, ok_points INTEGER, ok_own INTEGER,
    note TEXT);
CREATE TABLE IF NOT EXISTS entry_feature (
    contest_id INTEGER, entry_id INTEGER, rank INTEGER, points REAL, live INTEGER,
    own_sum REAL, own_logprod REAL, own_max REAL, own_min REAL,
    proj_sum REAL, proj_cov REAL, proj_imp_sum REAL, salary REAL, salary_cov REAL,
    dup_count INTEGER, payout REAL, cash INTEGER, top1 INTEGER, win INTEGER,
    stack1 INTEGER, stack2 INTEGER, n_teams INTEGER, qb_mates INTEGER,
    bringback INTEGER, line_max INTEGER, pp_max INTEGER, vs_own_anchor INTEGER,
    same_bout INTEGER, team_cov REAL, is_adam INTEGER,
    PRIMARY KEY (contest_id, entry_id));
"""


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    """Repo-relative path when the file lives in the repo, else absolute."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(Path(path).resolve())


def parse_entry_name(s: str) -> tuple[str, int | None, int | None]:
    """'TNeidert (3/3)' -> ('TNeidert', 3, 3); 'jim' -> ('jim', None, None)."""
    m = re.match(r"^(.*?)\s*\((\d+)/(\d+)\)\s*$", s or "")
    if m:
        return m.group(1).strip(), int(m.group(2)), int(m.group(3))
    return (s or "").strip(), None, None


def parse_lineup(text: str, slots: set, known: set) -> list[tuple[str, str]] | None:
    """'QB Josh Allen RB ... DST Jets ' -> [(slot, name), ...].

    Slot codes and name tokens can collide ("C", "D", "G", "F"), so a name ends
    at the FIRST later slot token where the words so far are a known board name
    (the board lists every drafted player), else at the first slot token."""
    toks = (text or "").split()
    if not toks:
        return []
    out, i = [], 0
    while i < len(toks):
        slot = toks[i]
        if slot not in slots:
            return None
        bounds = [k for k in range(i + 2, len(toks)) if toks[k] in slots] + [len(toks)]
        cut = next((k for k in bounds if norm(" ".join(toks[i + 1:k])) in known), bounds[0])
        out.append((slot, " ".join(toks[i + 1:cut])))
        i = cut
    return out


def read_export(path: Path) -> tuple[list[dict], list[dict]]:
    """(entries, board rows) from one DK contest-standings CSV (the two tables
    are interleaved in the same rows; see edge/dfs_contest.py)."""
    entries, board = [], []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if (row.get("EntryId") or "").strip():
                entries.append(row)
            name = (row.get("Player") or "").strip()
            pct = (row.get("%Drafted") or "").strip()
            if name and pct.endswith("%"):
                try:
                    board.append({"name": name, "slot": (row.get("Roster Position") or "").strip(),
                                  "pct": float(pct.rstrip("%")), "fpts": float(row.get("FPTS") or "nan")})
                except ValueError:
                    pass
    return entries, board


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# ingest: contests
# --------------------------------------------------------------------------
def max_entries_from_name(name: str) -> int | None:
    n = (name or "").lower()
    if "single entry" in n:
        return 1
    m = re.search(r"(\d+)\s*entry max", n)
    return int(m.group(1)) if m else None


def ingest_contest(conn, cid: int, path: Path, meta: dict) -> str:
    """(Re)load one export. Returns 'skip' | 'new' | 'updated'."""
    digest = sha1(path)
    row = conn.execute("SELECT file_sha1 FROM contest WHERE contest_id=?", (cid,)).fetchone()
    if row and row[0] == digest:
        # metadata can change without the file changing (a --retag)
        conn.execute("""UPDATE contest SET sport=?, type=?, name=?, field=?, entry_fee=?,
                        places_paid=?, prize_pool=?, draft_group=?, slate_date=?, guaranteed=?
                        WHERE contest_id=?""",
                     (meta.get("sport"), meta.get("type"), meta.get("name"), meta.get("field"),
                      meta.get("entry_fee"), meta.get("places_paid"), meta.get("prize_pool"),
                      meta.get("draft_group"), (meta.get("slate") or "")[:10] or None,
                      int(bool(meta.get("guaranteed"))), cid))
        return "skip"
    status = "updated" if row else "new"
    for t in ("contest", "entry", "entry_player", "player_result", "player_slot"):
        conn.execute(f"DELETE FROM {t} WHERE contest_id=?", (cid,))
    sport = meta.get("sport") or "?"
    slots = SLOTS.get(sport, set().union(*SLOTS.values()))
    entries, board = read_export(path)

    # the board: per player (pct summed over slots, CPT share kept apart) and per slot
    agg: dict = {}
    for b in board:
        k = norm(b["name"])
        cpt = b["slot"].upper() == "CPT"
        a = agg.setdefault(k, {"name": b["name"], "pct": 0.0, "cpt": 0.0, "fpts": None, "slots": []})
        a["pct"] += b["pct"]
        if cpt:
            a["cpt"] += b["pct"]
            if a["fpts"] is None:
                a["fpts"] = b["fpts"] / CPT_MULT
        else:
            a["fpts"] = b["fpts"]
        if b["slot"] not in a["slots"]:
            a["slots"].append(b["slot"])
    conn.executemany("INSERT INTO player_result VALUES (?,?,?,?,?,?,?)",
                     [(cid, k, a["name"], "/".join(a["slots"]), round(a["pct"], 4),
                       round(a["cpt"], 4), a["fpts"]) for k, a in agg.items()])
    conn.executemany("INSERT INTO player_slot VALUES (?,?,?,?)",
                     [(cid, norm(b["name"]), b["slot"], b["pct"]) for b in board])
    known = set(agg)

    e_rows, p_rows, seen_m, entrants, live = [], [], 0, set(), 0
    bad = 0
    for e in entries:
        eid = int(e["EntryId"])
        who, idx, of = parse_entry_name(e.get("EntryName") or "")
        entrants.add(who.lower())
        seen_m = max(seen_m, of or 1)
        lu = parse_lineup(e.get("Lineup") or "", slots, known)
        ok = lu is not None and all(norm(n) in known for _, n in (lu or []))
        if lu:
            live += 1
        if lu and not ok:
            bad += 1
        e_rows.append((cid, eid, int(e["Rank"]), e.get("EntryName"), who, idx, of,
                       _f(e.get("Points")) or 0.0, (e.get("Lineup") or "").strip(),
                       len(lu or []), int(bool(ok)), int(who.lower() == USER)))
        for slot, name in lu or []:
            p_rows.append((cid, eid, slot, norm(name)))
    conn.executemany("INSERT INTO entry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", e_rows)
    conn.executemany("INSERT INTO entry_player VALUES (?,?,?,?)", p_rows)
    unfinished = sum(1 for e in entries if (e.get("TimeRemaining") or "0").strip() not in ("0", ""))
    conn.execute("INSERT INTO contest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (cid, sport, meta.get("type"), meta.get("name"), meta.get("field"),
                  len(entries), live, meta.get("entry_fee"), meta.get("places_paid"),
                  meta.get("prize_pool"), meta.get("draft_group"),
                  (meta.get("slate") or "")[:10] or None, int(bool(meta.get("guaranteed"))),
                  max_entries_from_name(meta.get("name")), seen_m, len(entrants),
                  _rel(path), digest,
                  datetime.now(timezone.utc).isoformat(timespec="seconds"), unfinished,
                  int(unfinished == 0)))
    if unfinished:
        print(f"  ! {cid}: exported before the contest ended ({unfinished} entries still live) -- "
              f"ownership is final, ranks/points are NOT")
    if bad:
        print(f"  ! {cid}: {bad} lineups did not parse cleanly against the board")
    return status


# --------------------------------------------------------------------------
# ingest: projections (ours + Dan), slate players
# --------------------------------------------------------------------------
def _autolog_times() -> dict:
    try:
        d = json.loads(AUTOLOG.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_projections(conn) -> int:
    conn.execute("DELETE FROM projection")
    built = _autolog_times()
    rows = []
    for sport, specs in OUR_LOGS.items():
        for pattern, pcol, gcol, *var_override in specs:
            for f in sorted(glob.glob(str(ROOT / pattern))):
                fp = Path(f)
                if gcol == "FILENAME" and not re.search(r"_g\d+\.csv$", fp.name):
                    continue
                file_gid = re.search(r"_g(\d+)\.csv$", fp.name)
                with fp.open(newline="") as fh:
                    for r in csv.DictReader(fh):
                        name = (r.get(pcol) or "").strip()
                        if not name:
                            continue
                        if gcol == "FILENAME":
                            gid, variant = int(file_gid.group(1)), "slate"
                        elif gcol:
                            # NHL's log marks after-the-fact rebuilds (source=rebuild)
                            gid = int(r[gcol])
                            variant = (r.get("source") or "live") if sport == "NHL" else "live"
                        else:
                            gid, variant = None, (var_override[0] if var_override else "main")
                        own_cash = _f(r.get("own_cash"))
                        known = {pcol, "date", "gid", "team", "opp", "opp_team", "dk_pos", "pos",
                                 "salary", "proj", "sd", "floor", "ceil", "ceiling", "own",
                                 "own_cash", "source", "pos"}
                        extra = {k: v for k, v in r.items() if k not in known and v not in ("", None)}
                        rows.append((sport, gid, r["date"], norm(name), name, r.get("team"),
                                     r.get("opp") or r.get("opp_team"),
                                     r.get("dk_pos") or r.get("pos") or ("D" if sport == "NASCAR" else
                                                                         "F" if sport == "MMA" else None),
                                     _f(r.get("salary")), _f(r.get("proj")), _f(r.get("sd")),
                                     _f(r.get("floor")), _f(r.get("ceil") or r.get("ceiling")),
                                     _f(r.get("own")), own_cash, "ours", variant,
                                     str(fp.relative_to(ROOT)),
                                     built.get(f"{sport.lower()}:{gid}"), json.dumps(extra)))
    for pattern, sport in DAN_FILES:
        for f in sorted(set(glob.glob(str(ROOT / pattern)))):
            fp = Path(f)
            m = re.search(r"(\d{4}-\d{2}-\d{2})", fp.name)
            if not m:
                continue
            date = m.group(1)
            with fp.open(newline="", encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    if sport == "NFL":
                        name, pos, team = r.get("Player"), r.get("Pos"), r.get("Team")
                        sal, proj, own = r.get("DK Salary"), r.get("DK Proj"), r.get("Est Own%")
                        opp = r.get("Opponent")
                    else:
                        name, pos, team = r.get("Name"), r.get("Position"), r.get("Team")
                        sal, proj, own, opp = r.get("Salary"), r.get("Proj"), r.get("Ownership"), None
                    if not name or (pos or "").strip() == "K":
                        continue
                    rows.append((sport, None, date, norm(name), name.strip(), team, opp,
                                 "DST" if pos in ("DST", "D/ST", "DEF") else pos,
                                 _f(sal), _f(proj), None, None, None,
                                 _f((own or "").rstrip("%")), None, "dan", "sheet",
                                 fp.name, None, "{}"))
    # one row per (source, sport, draft group/date, player): a sheet can list a
    # player twice (Dan's lists DST under the team and the nickname); keep the first
    seen, out = set(), []
    for r in rows:
        k = (r[15], r[0], r[1], r[2], r[3])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    conn.executemany("INSERT INTO projection VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    return len(out)


def _git_snapshot(gid: int) -> list | None:
    """Raw DK draftables for a draft group: the working copy, else its newest
    version in git history (the publisher prunes old snapshots)."""
    rel = f"data/draftables_snapshot/{gid}.json"
    p = ROOT / rel
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    commits = subprocess.run(["git", "log", "--all", "--pretty=%h", "-n", "3", "--", rel], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    for c in commits:
        blob = subprocess.run(["git", "show", f"{c}:{rel}"], cwd=ROOT, capture_output=True,
                              text=True).stdout
        if blob.strip():
            try:
                return json.loads(blob)
            except ValueError:
                continue
    return None


def load_slate_players(conn) -> dict:
    """slate_player for every contest draft group: DK draftables (salary, team,
    opponent, game) where a snapshot exists, else the projection log; NHL line /
    PP unit and MMA bout always from the log."""
    gids = [r[0] for r in conn.execute("SELECT DISTINCT draft_group FROM contest WHERE draft_group IS NOT NULL")]
    have = {r[0] for r in conn.execute("SELECT DISTINCT draft_group FROM slate_player WHERE source='draftables'")}
    stats = Counter()
    for gid in gids:
        if gid in have:
            stats["draftables(cached)"] += 1
            continue
        conn.execute("DELETE FROM slate_player WHERE draft_group=?", (gid,))
        raw = _git_snapshot(gid)
        rows = {}
        for p in raw or []:
            comp = p.get("competition") or {}
            if not comp.get("name"):
                continue
            k = norm(p.get("displayName"))
            if k in rows:
                continue
            team = p.get("teamAbbreviation")
            m = re.match(r"\s*(\S+)\s*@\s*(\S+)", comp.get("name") or "")
            opp = None
            if m and team:
                opp = m.group(2) if m.group(1) == team else m.group(1) if m.group(2) == team else None
            rows[k] = [gid, k, p.get("displayName"), team, opp, p.get("position"),
                       _f(p.get("salary")), str(comp.get("competitionId")), None, None, None,
                       "draftables"]
        stats["draftables" if rows else "log-only"] += 1
        conn.executemany("INSERT OR REPLACE INTO slate_player VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         rows.values())
    return dict(stats)


def fill_slate_from_logs(conn, match: dict) -> None:
    """Players the draftables lack (or slates with no snapshot): take team /
    salary / position from the matched projection log; NHL line + PP and MMA
    bout always come from the log."""
    for cid, m in match.items():
        gid = m["draft_group"]
        if gid is None or not m.get("proj_key"):
            continue
        sport, pg, pdate = m["proj_key"]
        q = ("SELECT player_norm, name, team, opp, pos, salary, extra FROM projection "
             "WHERE source='ours' AND sport=? AND date=? AND " +
             ("draft_group=?" if pg is not None else "draft_group IS NULL"))
        args = (sport, pdate, pg) if pg is not None else (sport, pdate)
        for k, name, team, opp, pos, sal, extra in conn.execute(q, args).fetchall():
            ex = json.loads(extra or "{}")
            cur = conn.execute("SELECT team, salary FROM slate_player WHERE draft_group=? AND player_norm=?",
                               (gid, k)).fetchone()
            if cur is None:
                conn.execute("INSERT INTO slate_player VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (gid, k, name, team, opp, pos, sal, ex.get("game"), ex.get("line"),
                              ex.get("pp"), ex.get("bout"), "proj_log"))
            else:
                conn.execute("""UPDATE slate_player SET line=COALESCE(line, ?), pp=COALESCE(pp, ?),
                                bout=COALESCE(bout, ?), opp=COALESCE(opp, ?)
                                WHERE draft_group=? AND player_norm=?""",
                             (ex.get("line"), ex.get("pp"), ex.get("bout"), opp, gid, k))


# --------------------------------------------------------------------------
# contest <-> projection matching (by draft group; MLB main log by checked date)
# --------------------------------------------------------------------------
def match_contests(conn) -> dict:
    conn.execute("DELETE FROM contest_match")
    by_gid = defaultdict(list)
    by_date_main = defaultdict(list)
    for sport, gid, date, k, team in conn.execute(
            "SELECT sport, draft_group, date, player_norm, team FROM projection WHERE source='ours'"):
        if gid is not None:
            by_gid[(sport, gid)].append((date, k, team))
        else:
            by_date_main[(sport, date)].append((date, k, team))
    out = {}
    for cid, sport, ctype, gid, date in conn.execute(
            "SELECT contest_id, sport, type, draft_group, slate_date FROM contest ORDER BY contest_id").fetchall():
        board = {k: (pct, None) for k, pct in conn.execute(
            "SELECT player_norm, pct_drafted FROM player_result WHERE contest_id=?", (cid,))}
        tot = sum(p for p, _ in board.values()) or 1.0
        rec = {"contest_id": cid, "sport": sport, "type": ctype, "draft_group": gid,
               "slate_date": date, "method": "none", "proj_key": None, "proj_variant": None,
               "n_board": len(board), "n_board_matched": 0, "own_coverage": 0.0, "n_log": 0,
               "extra_teams": None, "ok_points": 0, "ok_own": 0, "note": ""}
        log, method = None, None
        if (sport, gid) in by_gid:
            log, method = by_gid[(sport, gid)], "draft_group"
            rec["proj_key"] = (sport, gid, log[0][0])
        elif (sport, date) in by_date_main:
            log, method = by_date_main[(sport, date)], "date(main log)"
            rec["proj_key"] = (sport, None, date)
        if log is None:
            rec["note"] = ("no projection log for this sport" if not any(s == sport for s, _ in by_gid)
                           and not any(s == sport for s, _ in by_date_main)
                           else "no log for this draft group/date")
            out[cid] = rec
            continue
        keys = {k for _, k, _ in log}
        teams = defaultdict(int)
        for _, k, t in log:
            teams[t] += k in board
        matched = [k for k in board if k in keys]
        cov = sum(board[k][0] for k in matched) / tot
        extra = sum(1 for t, n in teams.items() if t and n == 0)
        rec.update(method=method, n_board_matched=len(matched), own_coverage=round(cov, 4),
                   n_log=len(keys), extra_teams=extra)
        variants = {v for (v,) in conn.execute(
            "SELECT DISTINCT variant FROM projection WHERE source='ours' AND sport=? AND date=? AND "
            + ("draft_group=?" if rec["proj_key"][1] is not None else "draft_group IS NULL"),
            (sport, rec["proj_key"][2]) + ((rec["proj_key"][1],) if rec["proj_key"][1] is not None else ()))}
        rec["proj_variant"] = ",".join(sorted(variants))
        if method == "draft_group":
            rec["ok_points"] = int(cov >= 0.5)
            rec["ok_own"] = int(cov >= 0.5)
            rec["note"] = "" if cov >= 0.8 else f"log covers only {cov:.0%} of board ownership"
        else:
            rec["ok_points"] = int(cov >= 0.8)
            # small cash fields leave some teams undrafted, so allow one
            rec["ok_own"] = int(cov >= 0.8 and extra <= (1 if ctype == "gpp" else 2)
                                and "recovered" not in variants)
            if not rec["ok_points"]:
                rec["note"] = (f"date match rejected: log covers {cov:.1%} of board ownership "
                               f"({'another slate' if extra else 'partial pool or another slate'})")
            elif not rec["ok_own"] and "recovered" in variants:
                rec["note"] = ("points only: projections recovered from the calibration dashboard hold "
                               "board players only, so undrafted players' 0% cannot be graded")
            elif not rec["ok_own"]:
                rec["note"] = (f"points only: log has {extra} teams nobody on the board was drafted "
                               f"from -> logged ownership was for a different slate")
        out[cid] = rec
    conn.executemany("INSERT INTO contest_match VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(r["contest_id"], r["sport"], r["type"], r["draft_group"], r["slate_date"],
                       r["method"], r["proj_key"][1] if r["proj_key"] else None,
                       r["proj_key"][2] if r["proj_key"] else None, r["proj_variant"], r["n_board"],
                       r["n_board_matched"], r["own_coverage"], r["n_log"], r["extra_teams"],
                       r["ok_points"], r["ok_own"], r["note"]) for r in out.values()])
    return out


# --------------------------------------------------------------------------
# payouts
# --------------------------------------------------------------------------
def fetch_payouts(conn, max_calls: int) -> int:
    """Ask DK for at most `max_calls` payout tables, one per TEMPLATE (fee,
    pool, places paid) that has no table yet, most-used template first, >= 2 s
    apart (DRAFTKINGS_ACCESS.md). Cached to data/cache/dk_payouts/<cid>.json."""
    if max_calls <= 0:
        return 0
    from edge.dfs_contest import lookup
    PAYOUT_CACHE.mkdir(parents=True, exist_ok=True)
    have = {int(p.stem) for p in PAYOUT_CACHE.glob("*.json")}
    tmpl = defaultdict(list)
    for cid, fee, pool, paid in conn.execute(
            "SELECT contest_id, entry_fee, prize_pool, places_paid FROM contest WHERE type='gpp' "
            "AND prize_pool IS NOT NULL ORDER BY contest_id DESC"):
        tmpl[(fee, pool, paid)].append(cid)
    todo = [cids for cids in sorted(tmpl.values(), key=len, reverse=True)
            if not any(c in have for c in cids)]
    calls = 0
    for cids in todo[:max_calls]:
        cid = cids[0]
        if calls:
            time.sleep(2.0)
        calls += 1
        try:
            info = lookup(str(cid))
        except Exception as exc:  # noqa: BLE001  -- a 403 means stop, not retry
            print(f"  DK lookup {cid} failed ({exc}); stopping")
            break
        tiers = [list(t) for t in info.get("payouts") or []]
        if tiers:
            (PAYOUT_CACHE / f"{cid}.json").write_text(json.dumps(tiers))
            print(f"  DK payout table {cid}: {len(tiers)} tiers, top {tiers[0][2]}, "
                  f"paid {max(t[1] for t in tiers)} (template shared by {len(cids)} contests)")
    return calls


def _cached_table(cid: int) -> list | None:
    p = PAYOUT_CACHE / f"{cid}.json"
    if not p.exists():
        return None
    t = [tuple(x) for x in json.loads(p.read_text())]
    return [(int(a), int(b), float(c)) for a, b, c in t if float(c) > 0] or None


def build_payouts(conn) -> Counter:
    conn.execute("DELETE FROM payout")
    rows = conn.execute("SELECT contest_id, type, entry_fee, prize_pool, places_paid, name "
                        "FROM contest").fetchall()
    exact = {cid: t for cid, *_ in rows if (t := _cached_table(cid))}
    by_tmpl = {}
    for cid, ctype, fee, pool, paid, _ in rows:
        if cid in exact:
            by_tmpl.setdefault((fee, pool, paid), (cid, exact[cid]))
    stats = Counter()
    out = []
    for cid, ctype, fee, pool, paid, name in rows:
        if cid in exact:
            tiers, src = exact[cid], "dk"
        elif (fee, pool, paid) in by_tmpl:
            src_cid, tiers = by_tmpl[(fee, pool, paid)]
            src = f"template:{src_cid}"
        elif ctype == "cash" and pool and paid:
            tiers, src = [(1, int(paid), round(pool / paid, 4))], "cash_flat"
        elif pool and paid and re.search(r"top\s*(\d+)\s*win\s*\$?([\d.]+)", name or "", re.I):
            m = re.search(r"top\s*(\d+)\s*win\s*\$?([\d.]+)", name, re.I)
            tiers, src = [(1, int(m.group(1)), float(m.group(2)))], "name_flat"
        elif pool and paid and by_tmpl:
            # The shape of the DK table with the closest number of paid places,
            # stretched over this contest's paid places, with DK's own min-cash
            # (2x the fee under $1, 1.5x from $1 -- every DK table and every one
            # of Adam's min-cashes on file) and the rest of the pool spread
            # in proportion to the template's excess over its own min-cash.
            def n_paid(t):
                return max(b for _, b, _ in t[1])
            best = min(by_tmpl.values(), key=lambda t: abs(math.log(n_paid(t) / paid)))
            ref_paid, ref = n_paid(best), best[1]
            shape = []
            for r in range(1, int(paid) + 1):
                rr = min(ref_paid, 1 + int((r - 1) * ref_paid / paid))   # rank 1 -> rank 1
                shape.append(next(v for a, b, v in ref if a <= rr <= b))
            lo = min(shape)
            min_cash = (2.0 if (fee or 0) < 1 else 1.5) * (fee or 0)
            excess = sum(v - lo for v in shape)
            if pool <= paid * min_cash or not excess:
                amt = [pool / paid] * int(paid)
            else:
                k = (pool - paid * min_cash) / excess
                amt = [min_cash + (v - lo) * k for v in shape]
            tiers, i = [], 0
            while i < len(amt):
                j = i
                while j + 1 < len(amt) and abs(amt[j + 1] - amt[i]) < 1e-9:
                    j += 1
                tiers.append((i + 1, j + 1, round(amt[i], 4)))
                i = j + 1
            src = f"scaled:{best[0]}"
        else:
            stats["none"] += 1
            continue
        stats[src.split(":")[0]] += 1
        out += [(cid, a, b, v, src) for a, b, v in tiers]
    conn.executemany("INSERT INTO payout VALUES (?,?,?,?,?)", out)
    return stats


def tie_payouts(ranks: list[int], tiers: list) -> dict:
    """{rank: payout per entry} with DK's tie rule: tied entries split the
    prizes of the places they jointly occupy."""
    if not tiers:
        return {}
    top = max(b for _, b, _ in tiers)
    pay = [0.0] * (len(ranks) + top + 2)
    for a, b, v in tiers:
        for r in range(a, min(b, len(pay) - 1) + 1):
            pay[r] = v
    cnt = Counter(ranks)
    return {r: sum(pay[r:r + n]) / n for r, n in cnt.items()}


# --------------------------------------------------------------------------
# per-entry features
# --------------------------------------------------------------------------
CATCHERS = {"NFL": {"WR", "TE"}, "NCAAF": {"WR"}}


def _imputer(rows: list) -> callable:
    """proj ~ a + b*salary per position, from the logged pool, for players on
    the board the log never carried (no salary -> that position's 10th pct)."""
    by = defaultdict(list)
    for pos, sal, proj in rows:
        if sal and proj is not None:
            by[(pos or "?").split("/")[0]].append((sal, proj))
    fits = {}
    for pos, xs in by.items():
        if len(xs) >= 5:
            n = len(xs)
            mx, my = sum(s for s, _ in xs) / n, sum(p for _, p in xs) / n
            vx = sum((s - mx) ** 2 for s, _ in xs)
            b = sum((s - mx) * (p - my) for s, p in xs) / vx if vx else 0.0
            lo = sorted(p for _, p in xs)[max(0, n // 10)]
            fits[pos] = (my - b * mx, b, lo)
    allp = sorted(p for _, _, p in rows if p is not None) or [0.0]
    floor = allp[len(allp) // 10]

    def f(pos, sal):
        a = fits.get((pos or "?").split("/")[0])
        if a is None:
            return floor
        if not sal:
            return a[2]
        return max(0.0, a[0] + a[1] * sal)
    return f


def build_features(conn, match: dict) -> int:
    conn.execute("DELETE FROM entry_feature")
    n = 0
    for (cid, sport, ctype, gid, fee, paid, live_n, final) in conn.execute(
            "SELECT contest_id, sport, type, draft_group, entry_fee, places_paid, live_entries, final "
            "FROM contest ORDER BY contest_id").fetchall():
        m = match.get(cid, {})
        board = {k: (pct, cpt) for k, pct, cpt in conn.execute(
            "SELECT player_norm, pct_drafted, cpt_pct FROM player_result WHERE contest_id=?", (cid,))}
        field = max(live_n or 1, 1)
        floor_pct = 100.0 / field
        sp = {r[0]: r[1:] for r in conn.execute(
            "SELECT player_norm, team, opp, position, salary, line, pp, bout FROM slate_player "
            "WHERE draft_group=?", (gid,))}
        proj = {}
        if m.get("proj_key") and m.get("ok_points"):
            s, pg, pdate = m["proj_key"]
            q = ("SELECT player_norm, proj, pos, salary FROM projection WHERE source='ours' AND sport=? "
                 "AND date=? AND " + ("draft_group=?" if pg is not None else "draft_group IS NULL"))
            prow = conn.execute(q, (s, pdate, pg) if pg is not None else (s, pdate)).fetchall()
            proj = {k: p for k, p, _, _ in prow if p is not None}
            imp = _imputer([(pos, sal, p) for _, p, pos, sal in prow])
        tiers = [(a, b, v) for a, b, v in conn.execute(
            "SELECT rank_lo, rank_hi, amount FROM payout WHERE contest_id=?", (cid,))]
        entries = conn.execute("SELECT entry_id, rank, points, is_adam, n_players FROM entry "
                               "WHERE contest_id=?", (cid,)).fetchall()
        lineups = defaultdict(list)
        for eid, slot, k in conn.execute("SELECT entry_id, slot, player_norm FROM entry_player "
                                         "WHERE contest_id=?", (cid,)):
            lineups[eid].append((slot, k))
        keyed = {eid: tuple(sorted(("*" if s == "CPT" else "") + k for s, k in lu))
                 for eid, lu in lineups.items()}
        dups = Counter(keyed.values())
        pay = tie_payouts([r for _, r, _, _, _ in entries], tiers)
        top1_cut = max(1, math.ceil(0.01 * field))
        rows = []
        for eid, rank, pts, is_adam, nplay in entries:
            lu = lineups.get(eid, [])
            live = int(bool(lu))
            o = []
            for s, k in lu:
                pct, cpt = board.get(k, (0.0, 0.0))
                o.append(cpt if s == "CPT" else (pct - cpt if sport == "MMA" and cpt else pct))
            o = [max(x, floor_pct) for x in o]
            pj = [proj.get(k) for _, k in lu]
            mult = [CPT_MULT if s == "CPT" else 1.0 for s, _ in lu]
            have = [x is not None for x in pj]
            proj_sum = sum(x * w for x, w in zip(pj, mult) if x is not None) if proj else None
            imp_sum = None
            if proj:
                imp_sum = sum((x if x is not None else imp((sp.get(k) or (None,) * 7)[2],
                                                             (sp.get(k) or (None,) * 7)[3])) * w
                              for x, (_, k), w in zip(pj, lu, mult))
            sal = [(sp.get(k) or (None,) * 7)[3] for _, k in lu]
            team = [(sp.get(k) or (None,) * 7)[0] for _, k in lu]
            st = stack_shape(sport, lu, sp)
            # an export taken mid-contest has provisional ranks: no outcome at all
            payout = (pay.get(rank, 0.0) if tiers else None) if live and final else None
            rows.append((cid, eid, rank, pts, live,
                         round(sum(o), 3) if live else None,
                         round(sum(math.log(x / 100.0) for x in o), 4) if live else None,
                         max(o) if o else None, min(o) if o else None,
                         round(proj_sum, 3) if proj_sum is not None and live else None,
                         round(sum(have) / len(lu), 3) if lu and proj else None,
                         round(imp_sum, 3) if imp_sum is not None and live else None,
                         sum(x for x in sal if x) if live else None,
                         round(sum(x is not None for x in sal) / len(lu), 3) if lu else None,
                         dups[keyed[eid]] if live else None,
                         round(payout, 4) if payout is not None else None,
                         int(payout > 0) if payout is not None else None,
                         int(rank <= top1_cut) if live and final else None,
                         int(rank == 1) if live and final else None,
                         st["stack1"], st["stack2"], st["n_teams"], st["qb_mates"], st["bringback"],
                         st["line_max"], st["pp_max"], st["vs_own_anchor"], st["same_bout"],
                         round(sum(t is not None for t in team) / len(lu), 3) if lu else None,
                         is_adam))
        conn.executemany("INSERT INTO entry_feature VALUES (" + ",".join("?" * 30) + ")", rows)
        n += len(rows)
    return n


def stack_shape(sport: str, lu: list, sp: dict) -> dict:
    """Stack descriptors for one lineup. Unknown teams never count toward a stack."""
    out = dict.fromkeys(("stack1", "stack2", "n_teams", "qb_mates", "bringback", "line_max",
                         "pp_max", "vs_own_anchor", "same_bout"))
    if not lu:
        return out
    info = [(s, k, sp.get(k) or (None,) * 7) for s, k in lu]
    # team stacks over the players that stack: hitters (MLB), skaters (NHL), all (others)
    anchor_pos = {"MLB": "P", "NHL": "G"}.get(sport)

    def is_anchor(s, pos):
        return anchor_pos is not None and (s == anchor_pos or (pos or "") == anchor_pos)
    core = [(s, k, i) for s, k, i in info if not is_anchor(s, i[2]) and not (sport == "NFL" and s == "DST")]
    tc = Counter(i[0] for _, _, i in core if i[0])
    top = tc.most_common(2)
    out["stack1"] = top[0][1] if top else 0
    out["stack2"] = top[1][1] if len(top) > 1 else 0
    out["n_teams"] = len({i[0] for _, _, i in info if i[0]})
    if sport in ("NFL", "NCAAF"):
        qb = next(((k, i) for s, k, i in info if s == "QB"), None)
        if qb and qb[1][0]:
            t, opp = qb[1][0], qb[1][1]
            cats = CATCHERS[sport]
            out["qb_mates"] = sum(1 for s, k, i in info if k != qb[0] and i[0] == t
                                  and ((i[2] or "").split("/")[0] in cats or s in cats))
            out["bringback"] = sum(1 for s, k, i in info if opp and i[0] == opp and s != "DST")
    if sport == "NHL":
        lines = Counter((i[0], i[4]) for s, k, i in info if i[0] and (i[4] or "").startswith("F"))
        pps = Counter((i[0], i[5]) for s, k, i in info if i[0] and (i[5] or "").startswith("PP"))
        out["line_max"] = max(lines.values()) if lines else 0
        out["pp_max"] = max(pps.values()) if pps else 0
    if anchor_pos:
        anchors = [i for s, k, i in info if is_anchor(s, i[2])]
        opp_teams = {i[1] for i in anchors if i[1]}
        out["vs_own_anchor"] = (sum(1 for s, k, i in core if i[0] in opp_teams)
                                if anchors and all(i[1] for i in anchors) else None)
    if sport == "MMA":
        bouts = Counter(i[6] for _, _, i in info if i[6])
        out["same_bout"] = sum(1 for v in bouts.values() if v >= 2)
    return out


# --------------------------------------------------------------------------
def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def build(db_path: Path = DB_PATH, rebuild: bool = False, fetch: int = 0,
          files: list | None = None, meta_path: Path = META, quiet: bool = False) -> dict:
    if rebuild and db_path.exists():
        db_path.unlink()
    conn = connect(db_path)
    ver = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if ver and int(ver[0]) != SCHEMA_VERSION:
        conn.close()
        db_path.unlink()
        conn = connect(db_path)
    meta = json.loads(Path(meta_path).read_text())
    files = files or sorted(glob.glob(str(ROOT / "data" / "contest-standings-*.csv")))
    status = Counter()
    untagged = []
    for f in files:
        p = Path(f)
        m = re.search(r"contest-standings-(\d+)\.csv$", p.name)
        if not m:
            continue
        cid = int(m.group(1))
        if str(cid) not in meta:
            untagged.append(cid)
            continue
        status[ingest_contest(conn, cid, p, meta[str(cid)])] += 1
    conn.commit()
    if fetch:
        fetch_payouts(conn, fetch)
    pstats = build_payouts(conn)
    nproj = load_projections(conn)
    sstats = load_slate_players(conn)
    match = match_contests(conn)
    fill_slate_from_logs(conn, match)
    nfeat = build_features(conn, match)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)",
                 (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
    conn.commit()
    summary = {"contests": dict(status), "untagged": untagged, "payouts": dict(pstats),
               "projection_rows": nproj, "slate_sources": sstats, "features": nfeat,
               "matched": sum(1 for r in match.values() if r["ok_points"]),
               "unmatched": [cid for cid, r in match.items() if not r["ok_points"]]}
    conn.close()
    if not quiet:
        print(json.dumps(summary, indent=1, default=str))
    return summary


def report(db_path: Path = DB_PATH) -> None:
    conn = sqlite3.connect(str(db_path))
    print("contest -> projection match (draft group / date actually graded)")
    print(f"{'contest':>10} {'sport':6} {'type':4} {'gid':>7} {'date':10} {'method':15} "
          f"{'log gid':>7} {'variant':8} {'board':>5} {'match':>5} {'own cov':>7} "
          f"{'xteams':>6} pts own  note")
    for r in conn.execute("SELECT contest_id, sport, type, draft_group, slate_date, method, "
                          "proj_draft_group, proj_variant, n_board, n_board_matched, own_coverage, "
                          "log_teams_not_on_board, ok_points, ok_own, note FROM contest_match "
                          "ORDER BY sport, slate_date, contest_id"):
        print(f"{r[0]:>10} {r[1]:6} {r[2]:4} {r[3] or '':>7} {r[4] or '':10} {r[5]:15} "
              f"{r[6] or '':>7} {(r[7] or '')[:8]:8} {r[8]:5} {r[9]:5} {r[10]:7.0%} "
              f"{'' if r[11] is None else r[11]:>6} {r[12]:3} {r[13]:3}  {r[14]}")
    print()
    for sport, ctype, n, ok in conn.execute(
            "SELECT sport, type, COUNT(*), SUM(ok_points) FROM contest_match GROUP BY sport, type"):
        print(f"  {sport:6} {ctype:4} {n:3} contests, {ok or 0:3} with a matched pre-lock projection")
    print("\npayout sources:", dict(conn.execute("SELECT source, COUNT(DISTINCT contest_id) FROM payout "
                                                 "GROUP BY source").fetchall()))
    conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--fetch-payouts", type=int, default=0, metavar="N",
                    help="ask DK for at most N missing payout tables (>= 2 s apart)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()
    if not args.report:
        build(Path(args.db), rebuild=args.rebuild, fetch=args.fetch_payouts)
    report(Path(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
