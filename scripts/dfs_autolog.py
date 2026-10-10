#!/usr/bin/env python3
"""Build and LOG every DK slate shortly before it locks, for every sport.

    python3 scripts/dfs_autolog.py              # what the timer runs
    python3 scripts/dfs_autolog.py --dry-run    # list what is due, build nothing
    python3 scripts/dfs_autolog.py --window 600 # look further ahead (minutes)

Phone builds on Streamlit Cloud never reach the local projection logs, so a
night built only on the phone has nothing for calibration to grade, and the
graders then silently match the export to some other night (NHL_STATUS.md 6b).
This runs each sport's own lineup script -- the same builder the app uses --
for every Classic slate locking within the next WINDOW minutes, once per slate,
with fresh odds. The scripts log by default (date + draft group keyed).

Run from deploy/dfs-autolog.timer every 5 minutes, 08:00-23:55. State (what was built):
data/cache/dfs_autolog.json. Each build's log files are committed locally
(never pushed here; the odds-publish timers push them with their next commit).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATE = ROOT / "data" / "cache" / "dfs_autolog.json"
SLATES = ROOT / "data" / "cache" / "dfs_autolog_slates.json"
PY = sys.executable

#: Build when a slate locks within (MIN_LEAD, WINDOW] minutes. Five-minute
#: timer: the first chance lands 10-15 minutes before lock, when most goalies
#: and lineups are confirmed, with a second chance if that build fails.
MIN_LEAD, WINDOW = 3, 15
#: DK's lobby is fetched at most this often per sport. A broad high-volume
#: pattern earns a residential block for hours (DRAFTKINGS_ACCESS.md), and
#: start times do not move, so one listing an hour is plenty.
LIST_TTL_MIN = 60
#: Collect odds first unless the profile's newest scan is younger than this.
ODDS_FRESH_MIN = 15


def _slates_nhl():
    from edge import dfs_run_nhl as R
    return [(r["gid"], r["start"], f"{r['label']} {r['games']}g") for r in R.classic_groups()]


def _slates_nfl():
    from edge import dfs_run_nfl as R
    return [(r["gid"], r["start"], r.get("label", "")) for r in R.classic_groups()]


def _slates_ncaaf():
    from edge import dfs_run_ncaaf as R
    return [(r["gid"], r["start"], r.get("label", "")) for r in R.classic_groups()]


def _slates_nascar():
    from edge import dfs_run_nascar as R
    return [(r["gid"], r["start"], r.get("label", "")) for r in R.classic_groups()]


def _slates_mma():
    from edge import dfs_run_mma as R
    return [(r["gid"], r["start"], r.get("label", "")) for r in R.classic_groups()]


def _slates_mlb():
    from edge import dfs
    # Classic only (GameTypeId 2). The lobby also lists single-game showdowns
    # (114), snake drafts (179) and "RBIs" (342), which the Classic builder can't build.
    return [(g["DraftGroupId"], g.get("StartDate"),
             ((g.get("ContestStartTimeSuffix") or "").strip("() ") or "Main") + f" {g.get('GameCount')}g")
            for g in dfs.mlb_draft_groups() if g.get("GameTypeId") == 2]


#: sport -> (slate lister, command for one draft group, odds profile or None, log files)
SPORTS = {
    "nhl": (_slates_nhl, ["scripts/dfs_lineups_nhl.py", "--draft-group"], "dfs_nhl",
            ["data/dfs_proj_log_nhl.csv", "data/dfs_lineups_nhl_*.csv"]),
    "nfl": (_slates_nfl, ["scripts/dfs_lineups_nfl.py", "--draft-group"], "dfs_nfl",
            ["data/dfs_proj_log_nfl.csv", "data/dfs_lineups_nfl_*.csv"]),
    "ncaaf": (_slates_ncaaf, ["scripts/dfs_lineups_ncaaf.py", "--draft-group"], "dfs_ncaaf",
              ["data/dfs_proj_log_ncaaf.csv", "data/dfs_lineups_ncaaf_*.csv"]),
    "nascar": (_slates_nascar, ["scripts/dfs_lineups_nascar.py", "--draft-group"], None,
               ["data/dfs_proj_log_nascar.csv", "data/dfs_lineups_nascar_*.csv"]),
    "mma": (_slates_mma, ["scripts/dfs_lineups_mma.py", "--capture", "--draft-group"], None,
            ["data/dfs_proj_log_mma.csv", "data/dfs_lineups_mma_*.csv"]),
    "mlb": (_slates_mlb, ["scripts/dfs_lineups.py", "--draft-group"], "dfs_mlb",
            ["data/dfs_proj_log.csv", "data/dfs_proj_log_*_g*.csv", "data/dfs_lineups_2*.csv"]),
}


def parse_start(s) -> datetime | None:
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    if "." in s:                                  # DK: 2026-10-10T23:00:00.0000000+00:00
        head, tail = s.split(".", 1)
        tz = tail[tail.find("+"):] if "+" in tail else ""
        s = head + tz
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def scan_age_min(profile: str) -> float | None:
    db = ROOT / "data" / "odds.db"
    if not db.exists():
        return None
    row = sqlite3.connect(db).execute(
        "SELECT finished_at FROM scan WHERE ok=1 AND profile=? ORDER BY finished_at DESC LIMIT 1",
        (profile,)).fetchone()
    if not row:
        return None
    return (datetime.now(timezone.utc) - parse_start(row[0])).total_seconds() / 60


def commit_logs(patterns: list[str], msg: str) -> None:
    files = sorted({str(p.relative_to(ROOT)) for pat in patterns for p in ROOT.glob(pat)})
    if not files:
        return
    git = ["git", "-C", str(ROOT)]
    if subprocess.run([*git, "add", "--", *files], capture_output=True).returncode:
        print("   (git busy; logs left uncommitted)")
        return
    if subprocess.run([*git, "diff", "--cached", "--quiet"]).returncode == 0:
        return
    r = subprocess.run([*git, "commit", "-q", "-m", msg, "--", *files], capture_output=True, text=True)
    print("   committed logs" if r.returncode == 0 else f"   (commit skipped: {r.stderr.strip()[:120]})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--window", type=float, default=WINDOW, help="minutes ahead")
    ap.add_argument("--sport", choices=SPORTS, action="append")
    args = ap.parse_args()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    cache = json.loads(SLATES.read_text()) if SLATES.exists() else {}
    now = datetime.now(timezone.utc)
    collected = set()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    for sport in args.sport or SPORTS:
        lister, cmd, profile, logs = SPORTS[sport]
        hit = cache.get(sport)
        if hit and (now - parse_start(hit["at"])).total_seconds() < LIST_TTL_MIN * 60:
            slates = hit["slates"]
        else:
            try:
                slates = [list(s) for s in lister()]
            except Exception as exc:                        # noqa: BLE001
                print(f"{sport}: cannot list slates ({exc})")
                continue
            cache[sport] = {"at": now.isoformat(), "slates": slates}
            SLATES.write_text(json.dumps(cache))
        for gid, start, label in slates:
            t = parse_start(start)
            key = f"{sport}:{gid}"
            if t is None or key in state:
                continue
            lead = (t - now).total_seconds() / 60
            if not (MIN_LEAD < lead <= args.window):
                continue
            print(f"{now:%Y-%m-%d %H:%M}Z {sport} {gid} {label}: locks in {lead:.0f} min")
            if args.dry_run:
                continue
            if profile and profile not in collected:
                age = scan_age_min(profile)
                if age is None or age > ODDS_FRESH_MIN:
                    subprocess.run([PY, "scripts/odds_collect.py", "--profile", profile],
                                   cwd=ROOT, capture_output=True, timeout=600)
                collected.add(profile)
            r = subprocess.run([PY, *cmd, str(gid)], cwd=ROOT, capture_output=True, text=True,
                               timeout=1200)
            tail = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-2:]
            print("   " + " | ".join(tail)[:300])
            if r.returncode == 0:
                state[key] = datetime.now(timezone.utc).isoformat()
                STATE.write_text(json.dumps(state, indent=1))
                commit_logs(logs, f"dfs autolog: {sport} {gid} {label} ({t:%Y-%m-%d %H:%M}Z lock)")
            else:
                print(f"   FAILED (exit {r.returncode}); will retry while the slate is in the window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
