"""Snapshot DraftKings draftables (salaries) for every lobby slate and push them.

DK's draftables API 403s Streamlit Cloud's datacenter IPs (first seen live
2026-09-16, minutes before a 7:10 PM lock), so the cloud app reads
data/draftables_snapshot/<gid>.json instead -- see edge/dfs.py::_draftables_raw.
That fallback is only as good as the snapshot being there before the phone
asks, so this runs on a timer from this machine (deploy/draftables-publish.timer).

Salaries are frozen per draft group, so most ticks change nothing and push
nothing; a commit lands when DK posts a new slate. Groups that have left the
lobby are pruned once their LAST game has started. DK drops a slate at its FIRST
tip, and pruning then took away the salaries a late swap needs: on 2026-11-01 the
8:30 Night slate left the lobby with TOR@LAL still to tip at 9:00, Poeltl was ruled
out at 8:44, and the phone could not build the slate. slates.json in the same
directory lists the priced groups, so the page can offer them after lock.

    python3 scripts/draftables_publish.py            # snapshot + prune, no git
    python3 scripts/draftables_publish.py --push     # ...and commit/push
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from edge import dfs  # noqa: E402
from edge.data_branch import publish_one  # noqa: E402
from edge.dfs_slate import parse_time  # noqa: E402

#: DK lobby codes to publish salaries for. NOT the odds-feed sport keys --
#: college football is "CFB" here and NASCAR is "NAS"; passing "NCAAF" returns
#: every sport in the lobby, which looks like it worked and is not.
#:
#: Each sport costs one lobby call plus one draftables call per NEW or
#: still-unpriced draft group; a group DK has already priced is frozen and
#: drops to a 6-hour heartbeat. CFB and NAS are both small lobbies -- ~18 and
#: ~4 groups against the NFL's 62 -- so the two of them together add well
#: under 10% to this timer's measured ~160 requests/day. See
#: DRAFTKINGS_ACCESS.md §2 before raising it further; this poll was once the
#: largest single source of DK traffic in the repo, by 30x, because nobody
#: had counted the groups.
#: MMA added 2026-09-26: its lobby is ~3 draft groups, one card a week.
#: NHL added 2026-09-28, the eve of opening night: ~9 groups on a 5-game night.
#: NBA and CBB added 2026-10-13 (seasons open ~10/20 and ~11/3). Both lobbies
#: are big on a full night -- an NBA Tuesday lists ~10 groups, a college
#: Saturday more -- but priced groups drop to the 6-hour heartbeat, so the
#: steady cost is the new groups per day, not the lobby size.
SPORTS = ("MLB", "NFL", "CFB", "NAS", "MMA", "NHL", "NBA", "CBB")

#: A slate's snapshot outlives its last game's start by this much: the swap window
#: closes at that start, and this is only slack for the page's own 5-minute slate cache.
KEEP_AFTER_LAST_START = timedelta(hours=1)
#: The lobby fields the page's slate picker reads (edge/dfs_slate.group_row). Nothing
#: volatile: this file must not change, and so commit, on a tick when no slate did.
INDEX_FIELDS = ("DraftGroupId", "GameTypeId", "GameCount", "ContestStartTimeSuffix",
                "StartDate", "StartDateEst", "DraftGroupTag")


# Last attempt per draft group, for groups DK has not priced yet (those write
# no snapshot file, so there is nothing else to date them by). Outside
# _SNAP_DIR on purpose: publish_one publishes that whole directory as it is on disk.
_ATTEMPTS = ROOT / "data" / ".draftables_attempts.json"


def _load_attempts() -> dict:
    """{gid: [last_attempt_epoch, consecutive_misses]}."""
    try:
        raw = json.loads(_ATTEMPTS.read_text())
        return {int(k): [float(v[0]), int(v[1])] for k, v in raw.items()}
    except (OSError, ValueError, TypeError, AttributeError, IndexError, KeyError):
        return {}


def _save_attempts(attempts: dict, live: set) -> None:
    try:
        _ATTEMPTS.parent.mkdir(parents=True, exist_ok=True)
        _ATTEMPTS.write_text(json.dumps(
            {str(k): [round(v[0], 1), v[1]] for k, v in attempts.items() if k in live}))
    except OSError:
        pass


def _start_epoch(g: dict) -> float | None:
    raw = (g.get("StartDate") or "").replace("Z", "+00:00")
    # DK sends 7 fractional digits; fromisoformat takes 3 or 6.
    raw = re.sub(r"\.(\d{6})\d+", r".\1", raw)
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def _should_fetch(gid: int, g: dict, attempts: dict, args) -> bool:
    """Whether this group is worth spending a DraftKings request on.

    THE THROTTLE. Before 2026-09-19 this file fetched every draft group in the
    lobby on every tick: ~62 requests, 34 ticks a day, ~2,100 requests/day to
    api.draftkings.com -- while DRAFTKINGS_ACCESS.md described this poll as
    "1-2 calls per tick". Almost all of it was provably wasted, in two ways:

      * A PRICED group cannot change. DK salaries are frozen once posted, and
        that invariant is what makes the snapshot fallback exact at all
        (edge/dfs.py). Re-fetching one cannot return anything new, so it drops
        to a slow --refresh-hours heartbeat.
      * Most UNPRICED groups are never going to be priced. Roughly 45 of the 62
        are Tiers / Snake / Best Ball / season-long game types that carry no
        salary field at all, so they returned "unpriced" every 30 minutes
        forever.

    The second rule is deliberately MEASURED, not a guess at DK's game-type
    taxonomy: a group is backed off because it has actually answered without
    salaries N times in a row, doubling 30min -> 1h -> 2h -> 4h -> cap. That
    can never starve a group the app needs, because the counter resets the
    moment DK prices it, and because a gid seen for the FIRST time has zero
    misses and so is always fetched on the tick it appears -- which is the one
    case the 30-minute timer exists for. Filtering by GameTypeId instead would
    have been fewer requests and a standing risk of silently dropping a slate
    the picker offers (MLB alone lists priced groups under two game types).
    """
    path = dfs._SNAP_DIR / f"{gid}.json"
    now = time.time()
    if path.exists() and path.stat().st_size > 0:            # priced: frozen
        return args.refresh_hours <= 0 or (
            now - path.stat().st_mtime) >= args.refresh_hours * 3600
    last, misses = attempts.get(gid, (0.0, 0))
    start = _start_epoch(g)
    hot = start is None or (start - now) <= args.hot_hours * 3600
    cap = (args.cold_hours if hot else args.cold_hours * 4) * 3600
    # misses-1 so the FIRST retry still lands on the next 30-minute tick:
    # a slate DK prices minutes after listing it must not wait an hour.
    return (now - last) >= min(1800 * (2 ** max(0, misses - 1)), cap)


def _last_start(path: Path) -> str | None:
    """The latest game start in a snapshot, as DK wrote it (None = unreadable, or a
    group with no game times)."""
    try:
        rows = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    starts = [(p.get("competition") or {}).get("startTime") for p in rows if isinstance(p, dict)]
    return max((s for s in starts if s and parse_time(s)), key=parse_time, default=None)


def prunable(path: Path, live: set, now: datetime) -> bool:
    """Out of the lobby and past its last game's start. A snapshot we cannot date goes,
    as every group out of the lobby used to."""
    if int(path.stem) in live:
        return False
    last = _last_start(path)
    return last is None or now >= parse_time(last) + KEEP_AFTER_LAST_START


def update_index(index: dict, seen: dict) -> dict:
    """The new slates.json: this tick's lobby rows (`seen` = {sport: [group]}) over the
    last one's, so a slate that left the lobby stays. Only groups with a snapshot on
    disk, so a pruned one leaves the index with its salaries."""
    out = {sport: dict(rows) for sport, rows in index.items()}
    for sport, groups in seen.items():
        rows = out.setdefault(sport, {})
        for g in groups:
            gid = g.get("DraftGroupId")
            path = dfs._SNAP_DIR / f"{gid}.json"
            if gid is not None and path.exists():
                rows[str(gid)] = {**{k: g.get(k) for k in INDEX_FIELDS}, "last_start": _last_start(path)}
    return {sport: {gid: row for gid, row in rows.items() if (dfs._SNAP_DIR / f"{gid}.json").exists()}
            for sport, rows in out.items()}


def write_index(seen: dict) -> None:
    path = dfs._SNAP_DIR / dfs.SLATE_INDEX_NAME
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        old = {}
    new = update_index(old, seen)
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(new, sort_keys=True, indent=0))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--push", action="store_true", help="commit and push the snapshot dir")
    ap.add_argument("--refresh-hours", type=float, default=6.0,
                    help="re-fetch an already-priced group at most this often "
                         "(salaries are frozen per group; 0 = every tick)")
    ap.add_argument("--gap", type=float, default=0.5,
                    help="seconds between DK requests")
    ap.add_argument("--hot-hours", type=float, default=36.0,
                    help="an unpriced slate starting within this many hours is "
                         "checked every tick")
    ap.add_argument("--cold-hours", type=float, default=6.0,
                    help="how often to re-check an unpriced slate further out")
    args = ap.parse_args()

    attempts = _load_attempts()
    live, failed, fetched, skipped = set(), False, 0, 0
    seen: dict = {}
    for sport in SPORTS:
        try:
            groups = dfs.draft_groups(sport)
        except Exception as e:
            print(f"{sport}: lobby fetch failed ({e}) -- keeping existing snapshots")
            failed = True
            continue
        seen[sport] = groups
        for g in groups:
            gid = g.get("DraftGroupId")
            if gid is None:
                continue
            gid = int(gid)
            live.add(gid)
            if not _should_fetch(gid, g, attempts, args):
                skipped += 1
                continue
            prev_misses = attempts.get(gid, (0.0, 0))[1]
            attempts[gid] = [time.time(), prev_misses + 1]
            try:
                n = dfs.save_draftables_snapshot(gid)
                if n:
                    attempts[gid][1] = 0          # priced: reset the backoff
                print(f"  {sport} {gid}: {n or 'unpriced'}")
            except Exception as e:
                print(f"  {sport} {gid}: fetch failed ({e})")
                failed = True
            finally:
                # ALWAYS, including after a failure. This used to sit after the
                # success print and be jumped by `continue` on the error path,
                # so a 403 storm fired every request back-to-back with no gap
                # -- the exact burst shape most likely to earn an IP-reputation
                # flag, happening precisely when already being refused.
                fetched += 1
                time.sleep(args.gap)

    _save_attempts(attempts, live)
    print(f"  DK draftables requests this tick: {fetched} "
          f"({skipped} skipped -- priced already, or not close enough to matter)")

    if not failed:
        now = datetime.now(timezone.utc)
        for f in dfs._SNAP_DIR.glob("*.json"):
            if f.stem.isdigit() and prunable(f, live, now):
                f.unlink()
                print(f"  pruned {f.name}")
    write_index(seen)

    if args.push and dfs._SNAP_DIR.exists():
        return 0 if publish_one(dfs._SNAP_DIR, "draftables") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
