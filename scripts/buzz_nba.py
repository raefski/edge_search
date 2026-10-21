#!/usr/bin/env python3
"""Collect NBA DFS buzz -- YouTube shows and podcasts -- for one night's slate.

    python3 scripts/buzz_nba.py                          # tonight's main slate (ET)
    python3 scripts/buzz_nba.py --date 2025-12-25 --gid 139243     # a past night (backfill)
    python3 scripts/buzz_nba.py --contests               # every NBA GPP night in contests.db
    python3 scripts/buzz_nba.py --push                   # ...and publish data/buzz_nba*.csv

The NHL/NFL collectors' method (scripts/buzz_nhl.py: date-keyed YouTube search,
edge/buzz.py's matcher and caption-spelling learner, packages/transcripts), plus
the NBA DFS podcasts that are still active (PODCASTS). Basketball shows run in
the morning and early afternoon of slate day and a "Live Before Lock" stream
right up to (and past) the first tip, so WHEN a transcript exists matters:

  ready   the words could have been read before lock: an upload published
          before lock, a live stream that had ENDED before lock, or a podcast
          episode published before lock (the feed's transcript, or Whisper)
  late    said before lock but only readable after it: a live stream still
          running at lock (YouTube posts its transcript after it ends), or
          a podcast repost of one

Every video/episode row keeps that flag, so scripts/buzz_fit_nba.py can score
the honest version (ready only) and the upper bound (all pre-lock talk) from one
collection. Words are always cut at lock. Writes data/buzz_nba.csv (per player,
ready only -- what a live build would see) and data/buzz_nba_videos.csv (per
source x player, both kinds).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "transcripts"))
from edge import dfs, nba  # noqa: E402
from edge.buzz import _name_parts, aggregate, build_aliases, count_mentions, learn_variants, tokenize  # noqa: E402
from edge.dfs_slate import parse_time as _parse_time  # noqa: E402
from transcripts import Cache, YouTube  # noqa: E402
from transcripts.podcasts import Podcasts  # noqa: E402

ET = ZoneInfo("America/New_York")
CACHE = ROOT / "data" / "cache" / "buzz"
DRAFTABLES = ROOT / "data" / "cache" / "nba_draftables"     # scripts/nba_ownership_fit.py's cache
OUT = ROOT / "data" / "buzz_nba.csv"
OUT_VIDEOS = ROOT / "data" / "buzz_nba_videos.csv"
PLAYER_FIELDS = ["date", "player", "team", "pos", "mentions", "videos", "channels", "reach"]
VIDEO_FIELDS = ["date", "source", "video_id", "channel_id", "channel", "aired", "ended", "ready",
                "views", "title", "player", "mentions"]
#: Previews post the night before ("NBA DFS DraftKings Preview ... Christmas Day"
#: aired 36.5 h before the 12/25/25 noon lock), so the window is two days.
WINDOW_HOURS = 48
MIN_VIEWS = 150
#: YouTube posts a live stream's transcript some time after it ends; a stream
#: counts as readable before lock only if it ended this long before.
LIVE_TRANSCRIPT_DELAY = timedelta(minutes=45)
#: Active NBA DFS podcasts (Apple directory, 2026-10-21). Stokastic's are reposts
#: of its YouTube streams, Ship It Nation's too (with a feed transcript); both are
#: kept as their own source because a repost published before lock is readable
#: even when the stream's YouTube transcript is not.
PODCASTS = [("Stokastic NBA DFS", "Stokastic NBA DFS"),
            ("Ship It Nation", "Daily Fantasy Sports by Ship It Nation")]

_DFS = re.compile(r"\b(dfs|draft\s?kings|fan\s?duel|dk|fd|gpps?|cash|lineups?|core|chalk|"
                  r"ownership|value|plays|picks|slate|lock)\b", re.I)
_HOOPS = re.compile(r"\b(nba|basketball|hoops)\b", re.I)
_OFF = re.compile(r"\b(nfl|mlb|nhl|cfb|college|ncaa[bf]?|pga|nascar|ufc|mma|soccer|showdown|"
                  r"single[- ]game|captain|prizepicks|underdog|props?|parlay|bets?|wnba|"
                  r"season[- ]long|waiver|dynasty)\b", re.I)


def main_slate(slate: date) -> tuple[int | None, datetime | None]:
    """DK's main NBA Classic group starting on `slate` (ET), and its lock."""
    from edge import dfs_run_nba
    best = None
    for g in dfs_run_nba.classic_groups(dfs.draft_groups(dfs_run_nba.DK_SPORT)):
        start = _parse_time(g["start"])
        if start and start.astimezone(ET).date() == slate:
            key = (g["label"] == "Main", g["games"])
            if best is None or key > best[0]:
                best = (key, g["gid"], start)
    return (best[1], best[2]) if best else (None, None)


def board_for(gid: int) -> tuple[list[dict], datetime | None]:
    """Board and lock from the cached draftables when we have them (backfill),
    else DK live."""
    path = DRAFTABLES / f"{gid}.json"
    if path.exists():
        rows = json.loads(path.read_text())
        real = dfs._draftables_raw
        dfs._draftables_raw = lambda g: rows
        try:
            sal = dfs.fetch_draftables(gid)
        finally:
            dfs._draftables_raw = real
    else:
        sal = dfs.fetch_draftables(gid)
    starts = sorted(_parse_time(v.get("start")) for v in sal.values() if v.get("start"))
    board = [{"name": v["name"], "pos": v.get("position"), "team": nba.team(v.get("team"))}
             for v in sal.values() if v.get("salary")]
    return board, (starts[0] if starts else None)


def queries(slate: date, today: bool) -> list[str]:
    md, mdy = f"{slate.month}/{slate.day}", f"{slate.month}/{slate.day}/{slate:%y}"
    day, month = slate.strftime("%A"), slate.strftime("%B")
    # with the year: a bare "12/25" mostly returns every earlier season's 12/25
    q = [f"NBA DFS picks {mdy}", f"NBA DFS {mdy}", f"NBA DFS strategy {day} {mdy}", f"NBA DraftKings picks {mdy}",
         f"NBA DFS {month} {slate.day} {slate.year}", f"NBA DFS live before lock {day} {mdy}",
         f"NBA DFS picks {md}", f"NBA DFS {day} {month} {slate.day}"]
    if today:
        q += ["NBA DFS picks today", "NBA DFS live before lock"]
    return q


def title_ok(title: str, slate: date) -> bool:
    if not (_HOOPS.search(title) and _DFS.search(title)) or _OFF.search(title):
        return False
    dates = re.findall(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2}|\d{4}))?\b", title)
    return not dates or any((int(m), int(d)) == (slate.month, slate.day)
                            and (not y or int(y) % 100 == slate.year % 100) for m, d, y in dates)


_AGE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago")
_UNIT = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30.4, "year": 365}


def age_ok(rel: str | None, slate: date) -> bool:
    """YouTube's relative date ("9 months ago") against the slate's age, to skip
    an earlier season's video before paying for its watch page. Coarse on purpose:
    "9 months" covers 9.0-9.9, "1 year" 12-23 months."""
    m = _AGE.search(rel or "")
    if not m:
        return True
    n, unit = int(m.group(1)), m.group(2)
    want = (date.today() - slate).days
    slack = max(3, _UNIT[unit])           # YouTube rounds loosely ("10 months" at 300 days)
    return n * _UNIT[unit] - slack <= want <= (n + 1) * _UNIT[unit] + slack


def known_tokens(board: list[dict]) -> tuple[set, Counter]:
    names = {b["name"] for b in board}
    hist = ROOT / "data" / "nba_player_history.json"
    if hist.exists():
        names |= {p.get("name") or "" for p in json.loads(hist.read_text())["players"].values()}
    firsts = Counter(_name_parts(n)[0][0] for n in names if n and len(_name_parts(n)[0]) == 1)
    return {t for n in names for t, _, _ in tokenize(n)}, firsts


def youtube_items(yt: YouTube, slate: date, lock: datetime, today: bool) -> list[dict]:
    start = lock - timedelta(hours=WINDOW_HOURS)
    cands: dict = {}
    for q in queries(slate, today):
        for sort in ("relevance", "date"):
            for v in yt.search(q, sort=sort, limit=30):
                if (v["id"] not in cands and title_ok(v["title"], slate) and v["views"] >= MIN_VIEWS
                        and age_ok(v.get("published"), slate)):
                    cands[v["id"]] = v
    out = []
    for vid, v in sorted(cands.items(), key=lambda kv: -kv[1]["views"]):
        meta = yt.video(vid, v["title"])
        aired = YouTube.aired(meta)
        if not aired or not start <= aired < lock:
            continue
        segs = yt.transcript(vid, aired)
        if not segs:
            continue
        ended = None
        if meta.get("live"):
            ended = (datetime.fromisoformat(meta["live_end"]).astimezone(timezone.utc) if meta.get("live_end")
                     else aired + timedelta(seconds=meta.get("seconds") or 0))
        ready = ended is None or ended + LIVE_TRANSCRIPT_DELAY <= lock
        cutoff = (lock - aired).total_seconds()
        out.append({"source": "youtube", "id": vid, "channel_id": meta.get("channel_id") or "",
                    "channel": meta.get("channel", ""), "aired": aired, "ended": ended, "ready": ready,
                    "views": meta.get("views", 0), "title": meta.get("title", ""),
                    "text": " ".join(t for s, t in segs if s < cutoff)})
    return out


def podcast_items(pods: Podcasts, slate: date, lock: datetime, whisper: bool) -> list[dict]:
    start = lock - timedelta(hours=WINDOW_HOURS)
    out = []
    for term, show in PODCASTS:
        found = [r for r in pods.find(term, limit=10) if (r["name"] or "").startswith(show[:20])]
        if not found:
            continue
        for ep in pods.episodes(found[0]["feed"]):
            pub = datetime.fromisoformat(ep["published"]).astimezone(timezone.utc) if ep["published"] else None
            if not pub or not start <= pub < lock + timedelta(hours=3) or not title_ok(ep["title"], slate):
                continue
            ready = pub < lock
            if not ep.get("transcript_url") and not whisper:
                continue
            if not ready and not ep.get("transcript_url"):
                continue                    # an after-lock repost is never worth a Whisper run
            segs = pods.transcript(ep)
            # a repost of a live show: its audio starts when the show started; cut
            # at lock using the episode's length back from publication
            show_start = pub - timedelta(seconds=ep.get("seconds") or 0)
            cutoff = (lock - show_start).total_seconds()
            out.append({"source": "podcast", "id": ep["id"], "channel_id": show, "channel": show,
                        "aired": show_start, "ended": pub, "ready": ready, "views": 0, "title": ep["title"],
                        "text": " ".join(t for s, t in segs if s < cutoff)})
    return out


def _rewrite(path: Path, fields: list[str], slate: str, rows: list[dict]) -> None:
    keep = []
    if path.exists():
        with path.open(newline="") as fh:
            keep = [r for r in csv.DictReader(fh) if r["date"] != slate]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(keep + rows)


def collect(slate: date, gid: int | None, whisper: bool, today: bool) -> int:
    if gid is None:
        gid, _ = main_slate(slate)
    if not gid:
        print(f"no DK NBA Classic slate on {slate}")
        return 0
    board, lock = board_for(gid)
    known, firsts = known_tokens(board)
    aliases = build_aliases(board, firsts)
    items = youtube_items(YouTube(Cache(CACHE), pace=3.0), slate, lock, today)
    try:
        items += podcast_items(Podcasts(Cache(CACHE), pace=1.0), slate, lock, whisper)
    except Exception as exc:  # noqa: BLE001 -- a dead feed must not lose the YouTube collection
        print(f"podcasts skipped: {exc}")
    learned = learn_variants([v["text"] for v in items], board, aliases, known)
    for v in items:
        v["counts"] = count_mentions(v.pop("text"), {**aliases, **learned})
    d = slate.isoformat()
    _rewrite(OUT_VIDEOS, VIDEO_FIELDS, d, [
        {"date": d, "source": v["source"], "video_id": v["id"], "channel_id": v["channel_id"],
         "channel": v["channel"], "aired": v["aired"].isoformat(timespec="minutes"),
         "ended": v["ended"].isoformat(timespec="minutes") if v["ended"] else "", "ready": int(v["ready"]),
         "views": v["views"], "title": v["title"], "player": n, "mentions": c}
        for v in items for n, c in (v["counts"].items() or [("", 0)])])
    ready = [v for v in items if v["ready"]]
    feats = aggregate([{"channel": v["channel_id"], "views": v["views"], "counts": v["counts"]} for v in ready])
    info = {b["name"]: b for b in board}
    rows = [{"date": d, "player": n, "team": info.get(n, {}).get("team", ""),
             "pos": info.get(n, {}).get("pos", ""), **f} for n, f in sorted(feats.items())]
    _rewrite(OUT, PLAYER_FIELDS, d, rows)
    by = Counter((v["source"], v["ready"]) for v in items)
    print(f"{slate}: gid {gid}, lock {lock.astimezone(ET):%H:%M} ET | youtube {by[('youtube', True)]} ready + "
          f"{by[('youtube', False)]} late | podcasts {by[('podcast', True)]} ready + {by[('podcast', False)]} late | "
          f"{len(learned)} learned spellings")
    for r in sorted(rows, key=lambda r: -r["mentions"])[:8]:
        print(f"  {r['player']:24} {r['team']:3} {r['mentions']:4} mentions in {r['videos']} sources")
    return gid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", default=None, help="slate date (ET), default today")
    ap.add_argument("--gid", type=int, default=None, help="draft group (default: that day's main)")
    ap.add_argument("--contests", action="store_true", help="every NBA GPP night in data/contests.db")
    ap.add_argument("--whisper", action="store_true", help="transcribe podcast audio with no feed transcript")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    if a.contests:
        conn = sqlite3.connect(ROOT / "data" / "contests.db")
        for d, gid in conn.execute("SELECT DISTINCT slate_date, draft_group FROM contest "
                                   "WHERE sport='NBA' AND type='gpp' AND final=1 ORDER BY slate_date"):
            collect(date.fromisoformat(d), int(gid), a.whisper, today=False)
    else:
        slate = date.fromisoformat(a.date) if a.date else datetime.now(ET).date()
        collect(slate, a.gid, a.whisper, today=a.date is None)
    if a.push:
        from edge.data_branch import publish_one
        for path in (OUT, OUT_VIDEOS):
            if not publish_one(path, "buzz_nba"):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
