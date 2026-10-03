#!/usr/bin/env python3
"""Collect college-football DFS YouTube buzz for one Saturday slate, for free.

    python3 scripts/buzz_ncaaf.py                    # today's main slate (ET)
    python3 scripts/buzz_ncaaf.py --date 2026-10-03 --push

The NHL/NFL collectors' method (edge/buzz.py matcher + caption-spelling
learner, shared YouTube client in packages/transcripts) on college football's
calendar: one big Saturday slate, so videos are found by week and date and the
window is the 5 days before the main slate locks. Writes data/buzz_ncaaf.csv
and data/buzz_ncaaf_videos.csv (derived counts only; captions stay in the
gitignored data/cache/buzz/).

NAME SUFFIXES: DraftKings lists "Michael Hawkins Jr.", "Anthony Evans III",
"Re'Shaun Sanford II" -- 50+ on a main slate -- while a host says "Michael
Hawkins". edge.buzz._name_parts drops the suffix before building aliases, so
the spoken form counts; the output keeps DK's full name. Join to other tables
with edge.names.norm (suffix-free), never edge.dfs.norm. The run prints a
suffix check so a regression shows up here, not in a fit.

NOT YET IN THE OWNERSHIP MODEL: collected so the first NCAAF contest exports
can test it the way the NFL's were.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "transcripts"))
from edge import dfs, dfs_run_ncaaf  # noqa: E402
from edge.buzz import (  # noqa: E402
    _name_parts, aggregate, build_aliases, count_mentions, learn_variants, tokenize,
)
from edge.dfs_run_ncaaf import _parse_time  # noqa: E402
from transcripts import Blocked, Cache, YouTube  # noqa: E402

CACHE = ROOT / "data" / "cache" / "buzz"
OUT = ROOT / "data" / "buzz_ncaaf.csv"
OUT_VIDEOS = ROOT / "data" / "buzz_ncaaf_videos.csv"
PLAYER_FIELDS = ["date", "player", "team", "pos", "mentions", "videos", "channels", "reach"]
VIDEO_FIELDS = ["date", "video_id", "channel_id", "channel", "aired", "views", "title",
                "player", "mentions"]
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
WINDOW_DAYS = 5
MIN_VIEWS = 150

# DFS-specific only: "picks", "plays" and "value" are also how betting channels
# title spread picks, and one of those read 171 times in a row for a WR.
_DFS = re.compile(r"\b(dfs|draft\s?kings|fan\s?duel|dk|fd|gpps?|cash|lineups?|chalk|"
                  r"ownership|stacks?)\b", re.I)
_COLLEGE = re.compile(r"\b(cfb|ncaaf|college|college football)\b", re.I)
_OFF = re.compile(r"\b(nba|nfl|mlb|nhl|pga|golf|nascar|ufc|mma|soccer|showdown|"
                  r"single[- ]game|captain|prizepicks|underdog|props?|parlay|bets?|"
                  r"best ?ball|basketball|baseball|predictions?|spreads?|odds|ats|"
                  r"best bets?|locks?)\b", re.I)


def main_slate(slate: date):
    """(gid, lock) of DK's main CFB Classic group starting on `slate` (ET)."""
    best = None
    for g in dfs_run_ncaaf.classic_groups():
        start = _parse_time(g["start"])
        if start and start.astimezone(ZoneInfo("America/New_York")).date() == slate:
            key = (g["label"] == "Main", g["games"])
            if best is None or key > best[0]:
                best = (key, g["gid"], start)
    return (best[1], best[2]) if best else (None, None)


def queries(slate: date) -> list[str]:
    md = f"{slate.month}/{slate.day}"
    month = slate.strftime("%B")
    out = []
    for sport in ("CFB", "College Football"):
        out += [f"{sport} DFS picks {md}", f"{sport} DFS Saturday", f"{sport} DraftKings picks {md}",
                f"{sport} DFS {month} {slate.day}", f"{sport} DFS core plays", f"{sport} DFS week"]
    return out + ["NCAAF DFS picks", "college football DFS main slate", "CFB DFS chalk"]


def title_ok(title: str, slate: date) -> bool:
    if not (_COLLEGE.search(title) and _DFS.search(title)) or _OFF.search(title):
        return False
    if any(int(y) != slate.year for y in re.findall(r"\b(20\d\d)\b", title)):
        return False
    dates = re.findall(r"\b(\d{1,2})/(\d{1,2})\b", title)
    return not dates or any((int(m), int(d)) == (slate.month, slate.day) for m, d in dates)


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
        print(f"no DK CFB Classic slate on {slate}")
        return 0
    board = [{"name": v["name"], "pos": v.get("position"), "team": v.get("team")}
             for v in dfs.fetch_draftables(gid).values()]
    suffixed = [b["name"] for b in board if b["name"].split()[-1].lower().strip(".") in SUFFIXES]
    bad = [n for n in suffixed if any(t in SUFFIXES for t in _name_parts(n)[1])]
    firsts_hist: Counter = Counter()
    firsts = Counter(f[0] for f, _ in (_name_parts(b["name"]) for b in board) if len(f) == 1)
    # bare first names must be rare across a wider history than this one board
    # (where every first name is unique): past NFL DK pools, as the NFL does
    for f in glob.glob(str(ROOT / "data/nfl_dk_salaries/*.json")):
        for n in {r["name"] for r in json.loads(Path(f).read_text())}:
            fn = _name_parts(n)[0]
            if len(fn) == 1:
                firsts_hist[fn[0]] += 1
    known = {t for b in board for t, _, _ in tokenize(b["name"])}
    aliases = build_aliases(board, firsts_hist)
    print(f"{len(board)} names on the board, {len(suffixed)} with Jr./Sr./II/III/IV; "
          f"suffix leaking into a surname: {len(bad)}")
    # every suffixed player must be reachable by his spoken "First Last"
    spoken = {tuple(t for t, _, _ in tokenize(" ".join(n.split()[:-1]))): n for n in suffixed}
    unreachable = [n for k, n in spoken.items() if k not in aliases]
    print(f"suffixed players unreachable by spoken name: {len(unreachable)} {unreachable[:5]}")

    start = lock - timedelta(days=WINDOW_DAYS)
    yt = YouTube(Cache(CACHE), pace=3.0)
    cands: dict = {}
    for q in queries(slate):
        for sort in ("relevance", "date"):
            try:
                for v in yt.search(q, sort=sort, limit=30):
                    if v["id"] not in cands and title_ok(v["title"], slate) and v["views"] >= MIN_VIEWS:
                        cands[v["id"]] = v
            except Blocked as e:
                print(f"  YouTube stopped answering search ({e})")
                break
            except Exception as e:  # noqa: BLE001
                print(f"  search failed ({q}): {e}")
    videos, blocked = [], False
    for vid, v in sorted(cands.items(), key=lambda kv: -kv[1]["views"]):
        try:
            meta = yt.video(vid, v["title"])
        except Exception:  # noqa: BLE001
            continue
        aired = YouTube.aired(meta)
        if not aired or not start <= aired < lock:
            continue
        if blocked:
            continue
        try:
            segs = yt.transcript(vid, aired)
        except Blocked as e:
            blocked = True
            print(f"  YouTube stopped answering ({e}); counting what's cached")
            continue
        except Exception:  # noqa: BLE001
            continue
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
    got = [r for r in rows if r["player"] in set(suffixed)]
    print(f"suffixed players with buzz: {len(got)} "
          f"({', '.join(r['player'] for r in sorted(got, key=lambda r: -r['mentions'])[:6])})")
    for r in sorted(rows, key=lambda r: -r["mentions"])[:20]:
        print(f"  {r['player']:24} {r['team']:5} {r['pos']:3} {r['mentions']:4} mentions in {r['videos']} videos")
    if args.push:
        from scripts.odds_collect import push_snapshot
        for path in (OUT, OUT_VIDEOS):
            if not push_snapshot(path, "buzz_ncaaf"):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
