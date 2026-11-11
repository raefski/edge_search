"""Fresh NBA props at 60, 30, 15, 10 and 5 minutes before every tip.

The calendar timers (odds-publish-dfs-nba.timer) publish every 30 minutes in
the evening, so a phone build at lock could read props up to ~30 min old and
miss the last scratch-driven line moves. Lock times move night to night, so
this runs every minute (edge-nba-props-lock.timer), reads tonight's tip times
from NBA.com, and starts odds-publish@dfs_nba.service (collect + push to the
data branch) once per mark. Every distinct tip counts, not just slate locks:
a later tip is a late-swap deadline. Adam's ask, 2026-11-10.

    python3 scripts/nba_props_lock.py            # what the timer runs
    python3 scripts/nba_props_lock.py --dry-run  # print tonight's marks, start nothing
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.nba_lineups import fetch, parse  # noqa: E402
from harness.nba_scratch import ET, tip_time  # noqa: E402

STATE = ROOT / "data" / "harness" / "nba_props_lock_state.json"
#: Adam 11/10: an hour out, half an hour out, then every 5 min from 15. Earlier
#: than an hour stays on the calendar timers (odds-publish-dfs-nba.timer).
MARKS_MIN = (60, 30, 15, 10, 5)
#: A mark still fires if the timer wakes this late (WSL timers drift ~1 min);
#: past it the next mark is closer and that one fires instead.
GRACE = timedelta(minutes=3)
#: Start this far ahead so the props LAND on the mark: a dfs_nba collect+push
#: took 61 s on 2026-11-10 (scans 25574-25575), and the 7:25 mark that night
#: started at 7:26:46 without a lead.
SCAN_LEAD = timedelta(seconds=75)
#: Tip times are re-read this often; NBA.com rarely moves one on the day.
REFETCH = timedelta(minutes=60)
UNIT = "odds-publish@dfs_nba.service"


def due_marks(tips: list[datetime], fired: set[str], now: datetime) -> list[datetime]:
    """Marks whose time has come (within GRACE) and that have not fired yet."""
    marks = {t - timedelta(minutes=m) for t in tips for m in MARKS_MIN}
    return sorted(m for m in marks
                  if m - SCAN_LEAD <= now < m - SCAN_LEAD + GRACE
                  and m.isoformat() not in fired)


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    day = now.astimezone(ET).date()
    st = _load()
    if st.get("day") != day.isoformat():
        st = {"day": day.isoformat(), "tips": [], "fetched_at": None, "fired": []}
    stale = (not st["fetched_at"]
             or now - datetime.fromisoformat(st["fetched_at"]) > REFETCH)
    if stale or a.dry_run:
        try:
            games = parse(fetch(day))
            # tip_time is None once a game starts, so keep tips seen earlier.
            found = {t.isoformat() for t in (tip_time(g["status"], day) for g in games) if t}
            st["tips"] = sorted(set(st["tips"]) | found)
            st["fetched_at"] = now.isoformat()
        except Exception as exc:                            # noqa: BLE001
            print(f"NBA.com tip times unavailable ({exc}); using {len(st['tips'])} known")
    tips = [datetime.fromisoformat(t) for t in st["tips"]]
    if a.dry_run:
        for t in tips:
            print(t.astimezone(ET).strftime("tip %-I:%M %p ET  marks ")
                  + ", ".join((t - timedelta(minutes=m)).astimezone(ET).strftime("%-I:%M")
                              for m in MARKS_MIN))
        return 0
    due = due_marks(tips, set(st["fired"]), now)
    if due:
        # --no-block: if a calendar run is already scraping, that run is the fresh one.
        r = subprocess.run(["systemctl", "--user", "start", "--no-block", UNIT],
                           capture_output=True, text=True)
        print(f"{now.astimezone(ET):%H:%M} ET: started {UNIT} for "
              + ", ".join(m.astimezone(ET).strftime("%H:%M") for m in due)
              + ("" if r.returncode == 0 else f" -- FAILED: {r.stderr.strip()}"))
        if r.returncode == 0:
            st["fired"] += [m.isoformat() for m in due]
    STATE.write_text(json.dumps(st, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
