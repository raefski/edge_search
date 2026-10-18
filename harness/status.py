"""One screen: what is locking, what is running, what is stale, what broke.

    python3 -m harness status            # print
    python3 -m harness status --write    # also data/harness/STATUS.md
    python3 -m harness status --live     # ask DK's lobby for slates (1 call per sport)

This is the generated replacement for a hand-kept MEMORY.md of "current slate
locks / scraping failures / milestones": those change hourly, so the harness
reads them from where they actually live --

  * slates: the autolog's own lobby cache (data/cache/dfs_autolog_slates.json,
    refreshed at most hourly per sport -- DRAFTKINGS_ACCESS.md) and which of
    them it has already built (data/cache/dfs_autolog.json);
  * timers: systemd's own record of every deploy/*.timer and its service;
  * data: the odds store's newest good scan per profile, the published
    snapshots the phone apps read, NHL lines/goalies, buzz, projection logs,
    contest exports;
  * failures: data/harness/runs.jsonl (harness run) and alerts.jsonl (monitor).

Durable knowledge -- what was learned and why -- stays in Claude's auto-memory
and the *_STATUS.md write-ups; CLAUDE.md points at both.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from harness.registry import ROOT
from harness.runner import STATE, runs

ET = ZoneInfo("America/New_York")
DATA = ROOT / "data"
ALERTS = STATE / "alerts.jsonl"

#: Ages past which a source is flagged, hours.
STALE_H = {"odds": 6, "snapshot": 8, "lines": 3, "buzz": 30}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        if "." in s and "+" in s.split(".", 1)[1]:
            head, tail = s.split(".", 1)
            s = head + "+" + tail.split("+", 1)[1]
        elif "." in s:
            s = s.split(".", 1)[0] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age(dt: datetime | None) -> str:
    if dt is None:
        return "never"
    h = (_now() - dt).total_seconds() / 3600
    return f"{h * 60:.0f}m" if h < 1 else (f"{h:.1f}h" if h < 48 else f"{h / 24:.0f}d")


def _flag(dt: datetime | None, hours: float) -> str:
    return "⚠️ " if dt is None or (_now() - dt) > timedelta(hours=hours) else "   "


# ── slates ──────────────────────────────────────────────────────────────────
def slates(hours: float = 36, live: bool = False) -> list[dict]:
    """Classic slates locking in the next `hours`, every sport, soonest first."""
    rows = []
    built = {}
    try:
        built = json.loads((DATA / "cache" / "dfs_autolog.json").read_text())
    except (OSError, ValueError):
        pass
    listing = {}
    if live:
        listing = _live_listing()
    else:
        try:
            listing = {k: v.get("slates", []) for k, v in
                       json.loads((DATA / "cache" / "dfs_autolog_slates.json").read_text()).items()}
        except (OSError, ValueError):
            pass
    horizon = _now() + timedelta(hours=hours)
    for sport, items in listing.items():
        for gid, start, label in items:
            t = _parse(start)
            if t and _now() - timedelta(hours=1) <= t <= horizon:
                rows.append({"sport": sport, "gid": gid, "label": label, "lock": t,
                             "logged": bool(built.get(f"{sport}:{gid}"))})
    return sorted(rows, key=lambda r: r["lock"])


def _live_listing() -> dict:
    import sys
    sys.path.insert(0, str(ROOT))
    from edge import dfs
    out = {}
    for sport, code in (("nhl", "NHL"), ("nfl", "NFL"), ("ncaaf", "CFB"), ("nascar", "NAS"),
                        ("mma", "MMA"), ("mlb", "MLB"), ("nba", "NBA"), ("ncaab", "CBB")):
        try:
            gs = dfs.draft_groups(code)
        except Exception:                                   # noqa: BLE001
            continue
        out[sport] = [[g.get("DraftGroupId"), g.get("StartDate"),
                       f"{g.get('ContestStartTimeSuffix') or ''} {g.get('GameCount')}g".strip()]
                      for g in gs if g.get("GameTypeId") in (1, 2, 4, 6, 7, 70, 94, 96, 114, 145, 158,
                                                             168, 173)
                      or g.get("GameTypeId") in dfs.classic_game_types(code)]
    return out


# ── timers ──────────────────────────────────────────────────────────────────
def _show(units: list[str], props: list[str]) -> list[dict]:
    if not units:
        return []
    try:
        out = subprocess.run(["systemctl", "--user", "show", *units, *[f"-p{p}" for p in props]],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    blocks, cur = [], {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    if cur:
        blocks.append(cur)
    return blocks


def timers() -> list[dict]:
    """Every timer in deploy/, its last trigger and its service's last result."""
    names = sorted(p.name for p in (ROOT / "deploy").glob("*.timer"))
    tinfo = _show(names, ["Id", "Unit", "LastTriggerUSec", "ActiveState", "UnitFileState"])
    sinfo = {b.get("Id"): b for b in _show([t.get("Unit") for t in tinfo if t.get("Unit")],
                                          ["Id", "Result", "ExecMainStatus", "ActiveState"])}
    rows = []
    for t in tinfo:
        s = sinfo.get(t.get("Unit"), {})
        rows.append({"timer": t.get("Id"), "enabled": t.get("UnitFileState"),
                     "active": t.get("ActiveState"), "last": t.get("LastTriggerUSec") or "n/a",
                     "result": s.get("Result"), "exit": s.get("ExecMainStatus"),
                     "running": s.get("ActiveState") in ("activating", "active"),
                     "ok": s.get("Result") in (None, "", "success") and s.get("ExecMainStatus") in
                     (None, "", "0")})
    return rows


# ── data freshness ──────────────────────────────────────────────────────────
def freshness() -> list[tuple[str, datetime | None, float, str]]:
    """(source, last updated, stale-after hours, note)."""
    out = []
    try:
        con = sqlite3.connect(f"file:{DATA / 'odds.db'}?mode=ro", uri=True, timeout=5)
        for prof, ts, n in con.execute("SELECT profile, MAX(started_at), SUM(quote_count) "
                                       "FROM scan WHERE ok = 1 GROUP BY profile"):
            if prof != "arb":
                out.append((f"odds store {prof}", _parse(ts), STALE_H["odds"], ""))
        con.close()
    except sqlite3.Error as exc:
        out.append(("odds store", None, 0, f"unreadable: {exc}"))
    for snap in sorted(DATA.glob("odds_snapshot_*.json")):
        try:
            ts = json.loads(snap.read_text()).get("generated_at")
        except (OSError, ValueError):
            ts = None
        out.append((f"cloud snapshot {snap.stem.replace('odds_snapshot_', '')}", _parse(ts),
                    STALE_H["snapshot"], "what the phone apps read"))
    try:
        ts = json.loads((DATA / "nhl_lines_snapshot.json").read_text()).get("generated_at")
        out.append(("NHL lines + goalies", _parse(ts), STALE_H["lines"], "DailyFaceoff"))
    except (OSError, ValueError):
        pass
    for f in sorted(DATA.glob("buzz_*.csv")):
        if "videos" not in f.name:
            out.append((f.stem.replace("_", " "), datetime.fromtimestamp(f.stat().st_mtime, timezone.utc),
                        STALE_H["buzz"], "only matters on its sport's slate days"))
    exports = sorted(DATA.glob("contest-standings-*.csv"), key=lambda p: p.stat().st_mtime)
    if exports:
        out.append((f"latest contest export ({len(exports)} total)",
                    datetime.fromtimestamp(exports[-1].stat().st_mtime, timezone.utc), 24 * 7,
                    exports[-1].name))
    db = DATA / "contests.db"
    out.append(("contest database", datetime.fromtimestamp(db.stat().st_mtime, timezone.utc)
                if db.exists() else None, 24 * 7, "scripts/contest_db.py"))
    return out


def odds_storage() -> list[str]:
    """The hot store vs the F: archive (edge/odds/archive.py)."""
    from edge.odds import archive
    out = []
    db = DATA / "odds.db"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        has = con.execute("SELECT 1 FROM sqlite_master WHERE name='archived'").fetchone()
        oldest = con.execute("SELECT MIN(started_at) FROM scan WHERE ok=1" + (
            " AND id NOT IN (SELECT scan_id FROM archived)" if has else "")).fetchone()[0]
        n_arch = con.execute("SELECT COUNT(*) FROM archived").fetchone()[0] if has else 0
        con.close()
    except sqlite3.Error as exc:
        return [f"odds store unreadable: {exc}"]
    size = db.stat().st_size / 1e9 if db.exists() else 0
    age = (_now() - _parse(oldest)).days if oldest else 0
    late = age > archive.HOT_DAYS + 8
    out.append(f"{'✗' if late else '✓'} hot data/odds.db {size:.1f} GB, oldest unarchived scan "
               f"{age} days (archive keeps the hot store to {archive.HOT_DAYS})"
               + (" -- ARCHIVE OVERDUE: harness run odds-archive" if late else ""))
    if archive.available():
        files = sorted(archive.ARCHIVE_DIR.glob("odds_*.db"))
        gb = sum(f.stat().st_size for f in files) / 1e9
        out.append(f"✓ archive {archive.ARCHIVE_DIR}: {len(files)} month file(s), {gb:.1f} GB, "
                   f"{n_arch} scans")
    else:
        out.append(f"✗ archive {archive.ARCHIVE_DIR} NOT MOUNTED -- old scans stay hot until it is")
    return out


def proj_logs() -> list[tuple[str, str]]:
    """Last slate date logged per sport's forward projection log."""
    out = []
    for f in sorted(DATA.glob("dfs_proj_log_*.csv")):
        if f.stem.count("_") > 3:          # per-slate MLB files
            continue
        try:
            with f.open() as fh:
                last = max((r.get("date") or "" for r in csv.DictReader(fh)), default="")
        except (OSError, csv.Error):
            last = "?"
        out.append((f.stem.replace("dfs_proj_log_", ""), last))
    return out


def contest_inbox() -> list[str]:
    """Contest exports Adam dropped in data/contest_inbox/<sport>/<cash|gpp>/, and
    exports in data/ never tagged from DK -- what the next import-contests files."""
    try:
        from scripts.dfs_import_contests import pending
        return pending()
    except Exception as exc:                                # noqa: BLE001
        return [f"(inbox unreadable: {exc})"]


def _git() -> list[str]:
    """Code vs origin/main (as of the last fetch -- edge-code-sync fetches every
    10 min) and the data branch's last publish (edge/data_branch.py)."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              timeout=20).stdout
    try:
        st = run("status", "-sb", "--porcelain").splitlines()
        counts = run("rev-list", "--left-right", "--count", "HEAD...origin/main").split()
        data = run("log", "-1", "--format=%cI|%s", "refs/remotes/origin/data").strip() or \
            run("log", "-1", "--format=%cI|%s", "refs/heads/data").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ["git: unavailable"]
    head = st[0] if st else ""
    dirty = [l for l in st[1:] if l.strip()]
    code = [l for l in dirty if l[3:].endswith((".py", ".md", ".json")) and not l[3:].startswith("data/")]
    out = [f"{head.lstrip('# ')} · {len(dirty)} changed paths ({len(code)} code/docs outside data/)"]
    if len(counts) == 2:
        ahead, behind = int(counts[0]), int(counts[1])
        note = ("up to date" if not (ahead or behind) else
                f"{behind} behind" + (f", {ahead} LOCAL commit(s) -- code-sync waits until "
                                      "they are pushed" if ahead else " -- code-sync fast-forwards"))
        out.append(f"code vs origin/main: {note}")
    if data:
        when, _, subject = data.partition("|")
        out.append(f"data branch: last publish {_age(_parse(when))} ago ({subject[:60]})")
    else:
        out.append("data branch: none yet (edge/data_branch.py)")
    return out


def _alerts(hours: float = 24) -> list[dict]:
    if not ALERTS.exists():
        return []
    cutoff = _now() - timedelta(hours=hours)
    out = []
    for line in ALERTS.read_text().splitlines():
        try:
            a = json.loads(line)
        except ValueError:
            continue
        if (_parse(a.get("at")) or cutoff) > cutoff:
            out.append(a)
    return out


def report(live: bool = False) -> str:
    now_et = _now().astimezone(ET)
    L = [f"# edge_search status — {now_et:%a %b %-d, %-I:%M %p} ET", ""]
    L.append("## Slates locking in the next 36h")
    sl = slates(live=live)
    if not sl:
        L.append("  (none in the autolog's lobby cache — `harness status --live` asks DK)")
    for r in sl:
        L.append(f"  {r['lock'].astimezone(ET):%a %-I:%M %p}  {r['sport'].upper():7} {r['label']:<14} "
                 f"gid {r['gid']}  {'logged ✓' if r['logged'] else 'autolog builds ~12 min before lock'}")
    L += ["", "## Timers (deploy/*.timer)"]
    for t in timers():
        mark = "✓" if t["ok"] else "✗"
        L.append(f"  {mark} {t['timer']:<34} {t['enabled'] or '?':<9} last {t['last']:<28} "
                 f"{'running' if t['running'] else (t['result'] or '')} {'' if t['ok'] else 'exit ' + str(t['exit'])}")
    L += ["", "## Data freshness"]
    for name, dt, hours, note in freshness():
        L.append(f"  {_flag(dt, hours)}{name:<34} {_age(dt):>7} ago  {note}")
    L.append("  projection logs (last slate date): "
             + ", ".join(f"{s} {d}" for s, d in proj_logs()))
    waiting = contest_inbox()
    if waiting:
        L.append(f"  ✗ {len(waiting)} contest export(s) waiting: {', '.join(waiting[:4])}"
                 f"{' …' if len(waiting) > 4 else ''} -- python3 -m harness run import-contests")
    bad = [r for r in runs(since_hours=24) if r.get("rc")]
    L += ["", "## Odds store"] + ["  " + line for line in odds_storage()]
    L += ["", f"## harness runs, last 24h: {len(runs(since_hours=24))} ({len(bad)} failed)"]
    for r in bad[-8:]:
        L.append(f"  ✗ {r['job']} rc={r['rc']} at {_parse(r['start']).astimezone(ET):%-I:%M %p} — "
                 f"{(r.get('tail') or [''])[-1][:100]}  ({r.get('log')})")
    al = _alerts()
    L += ["", f"## Monitor alerts, last 24h: {len(al)}"]
    for a in al[-10:]:
        L.append(f"  {_parse(a['at']).astimezone(ET):%-I:%M %p} {a.get('title')}: {a.get('body', '')[:110]}")
    L += ["", "## Repo"] + ["  " + line for line in _git()]
    return "\n".join(L)


def write(text: str) -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / "STATUS.md"
    path.write_text(text + "\n")
    return path
