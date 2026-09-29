#!/usr/bin/env python3
"""Collect NHL DFS YouTube buzz for one night's main slate, for free.

    python3 scripts/buzz_nhl.py                    # tonight's main slate (ET)
    python3 scripts/buzz_nhl.py --date 2026-09-29 --push

The NFL collector's method (scripts/buzz_nfl.py, which lifted NFL ownership
MAE 15% held out) on hockey's calendar: slates are nightly, not weekly, so
videos are found by DATE ("NHL DFS Picks 9/29", "Tuesday NHL DFS") and the
window is the 36 hours before the slate locks. Same matcher and caption-
spelling learner (edge/buzz.py), same shared YouTube client
(packages/transcripts). Writes data/buzz_nhl.csv and data/buzz_nhl_videos.csv.

NOT YET IN THE OWNERSHIP MODEL: no NHL contest export exists to fit its weight
against. It is collected from night one so the first exports can be tested
against it the way the NFL's were, and the app shows it as a chalk signal.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "transcripts"))
from edge import dfs, nhl  # noqa: E402
from edge.buzz import _name_parts, aggregate, build_aliases, count_mentions, learn_variants, tokenize  # noqa: E402
from edge.dfs_run_nfl import _parse_time  # noqa: E402
from transcripts import Cache, YouTube  # noqa: E402

CACHE = ROOT / "data" / "cache" / "buzz"
OUT = ROOT / "data" / "buzz_nhl.csv"
OUT_VIDEOS = ROOT / "data" / "buzz_nhl_videos.csv"
PLAYER_FIELDS = ["date", "player", "team", "pos", "mentions", "videos", "channels", "reach"]
VIDEO_FIELDS = ["date", "video_id", "channel_id", "channel", "aired", "views", "title",
                "player", "mentions"]
WINDOW_HOURS = 36
MIN_VIEWS = 150

_DFS = re.compile(r"\b(dfs|draft\s?kings|fan\s?duel|dk|fd|gpps?|cash|lineups?|core|chalk|"
                  r"ownership|value|stacks?|picks)\b", re.I)
_HOCKEY = re.compile(r"\b(nhl|hockey)\b", re.I)
_OFF = re.compile(r"\b(nba|nfl|mlb|cfb|college|ncaa|pga|nascar|ufc|mma|soccer|showdown|"
                  r"single[- ]game|captain|prizepicks|underdog|props?|parlay|bets?)\b", re.I)


def main_slate(slate: date) -> tuple[int | None, datetime | None]:
    """DK's main NHL Classic group starting on `slate` (ET), and its lock."""
    from edge import dfs_run_nhl
    best = None
    for g in dfs_run_nhl.classic_groups():
        start = _parse_time(g["start"])
        if start and start.astimezone(ZoneInfo("America/New_York")).date() == slate:
            key = (g["label"] == "Main", g["games"])
            if best is None or key > best[0]:
                best = (key, g["gid"], start)
    return (best[1], best[2]) if best else (None, None)


def queries(slate: date) -> list[str]:
    md, mdy = f"{slate.month}/{slate.day}", f"{slate.month}/{slate.day}/{slate:%y}"
    day, month = slate.strftime("%A"), slate.strftime("%B")
    return [f"NHL DFS picks {md}", f"NHL DFS {mdy}", f"NHL DFS {day}", f"NHL DraftKings picks {md}",
            f"NHL DFS {month} {slate.day}", f"NHL DFS core plays {day}", "NHL DFS picks today"]


def title_ok(title: str, slate: date) -> bool:
    if not (_HOCKEY.search(title) and _DFS.search(title)) or _OFF.search(title):
        return False
    dates = re.findall(r"\b(\d{1,2})/(\d{1,2})\b", title)
    return not dates or any((int(m), int(d)) == (slate.month, slate.day) for m, d in dates)


def known_tokens(board: list[dict]) -> tuple[set, Counter]:
    names = {b["name"] for b in board}
    try:
        names |= {r.get("skaterFullName") or "" for r in nhl.season_skater_stats("20252026").values()}
    except Exception:                                       # noqa: BLE001
        pass
    firsts = Counter(_name_parts(n)[0][0] for n in names if n and len(_name_parts(n)[0]) == 1)
    return {t for n in names for t, _, _ in tokenize(n)}, firsts


def _rewrite(path: Path, fields: list[str], slate: str, rows: list[dict]) -> None:
    keep = []
    if path.exists():
        with path.open(newline="") as fh:
            keep = [r for r in csv.DictReader(fh) if r["date"] != slate]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(keep + rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", default=None, help="slate date (ET), default today")
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    slate = date.fromisoformat(args.date) if args.date else datetime.now(ZoneInfo("America/New_York")).date()
    gid, lock = main_slate(slate)
    if not gid:
        print(f"no DK NHL Classic slate on {slate}")
        return 0
    board = [{"name": v["name"], "pos": v.get("position"), "team": nhl.team(v.get("team"))}
             for v in dfs.fetch_draftables(gid).values()]
    known, firsts = known_tokens(board)
    aliases = build_aliases(board, firsts)
    start = lock - timedelta(hours=WINDOW_HOURS)
    yt = YouTube(Cache(CACHE), pace=3.0)
    cands: dict = {}
    for q in queries(slate):
        for sort in ("relevance", "date"):
            for v in yt.search(q, sort=sort, limit=30):
                if v["id"] not in cands and title_ok(v["title"], slate) and v["views"] >= MIN_VIEWS:
                    cands[v["id"]] = v
    videos = []
    for vid, v in sorted(cands.items(), key=lambda kv: -kv[1]["views"]):
        meta = yt.video(vid, v["title"])
        aired = YouTube.aired(meta)
        if not aired or not start <= aired < lock:
            continue
        segs = yt.transcript(vid, aired)
        if not segs:
            continue
        cutoff = (lock - aired).total_seconds()
        videos.append({**meta, "aired": aired.isoformat(timespec="minutes"),
                       "text": " ".join(t for s, t in segs if s < cutoff)})
    learned = learn_variants([v["text"] for v in videos], board, aliases, known)
    for v in videos:
        v["counts"] = count_mentions(v.pop("text"), {**aliases, **learned})
    print(f"{slate}: draft group {gid}, lock {lock:%H:%M} UTC, {len(cands)} candidate titles, "
          f"{len(videos)} videos counted, {len(learned)} learned spellings")
    d = slate.isoformat()
    _rewrite(OUT_VIDEOS, VIDEO_FIELDS, d, [
        {"date": d, "video_id": v["id"], "channel_id": v.get("channel_id") or "",
         "channel": v.get("channel", ""), "aired": v["aired"], "views": v.get("views", 0),
         "title": v.get("title", ""), "player": n, "mentions": c}
        for v in videos for n, c in (v["counts"].items() or [("", 0)])])
    feats = aggregate([{"channel": v.get("channel_id"), "views": v.get("views"),
                        "counts": v["counts"]} for v in videos])
    info = {b["name"]: b for b in board}
    rows = [{"date": d, "player": n, "team": info.get(n, {}).get("team", ""),
             "pos": info.get(n, {}).get("pos", ""), **f} for n, f in sorted(feats.items())]
    _rewrite(OUT, PLAYER_FIELDS, d, rows)
    for r in sorted(rows, key=lambda r: -r["mentions"])[:15]:
        print(f"  {r['player']:24} {r['team']:3} {r['mentions']:4} mentions in {r['videos']} videos")
    if args.push:
        from scripts.odds_collect import push_snapshot
        for path in (OUT, OUT_VIDEOS):
            if not push_snapshot(path, "buzz_nhl"):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
