"""python3 -m transcripts search "query" | podcast "show" -- see __init__.py."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone

from . import Podcasts, YouTube


def _hits(segments, pattern, width):
    text = " ".join(t for _, t in segments or [])
    if not pattern:
        return [text[:width]]
    return [text[max(0, m.start() - width // 2): m.end() + width // 2]
            for m in re.finditer(pattern, text, re.I)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="transcripts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="search YouTube, print transcript snippets")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--days", type=float, default=7, help="only videos aired in the last N days")
    s.add_argument("--sort", choices=("relevance", "date"), default="relevance")
    p = sub.add_parser("podcast", help="find a podcast, transcribe recent episodes")
    p.add_argument("show")
    p.add_argument("--latest", type=int, default=1)
    for a in (s, p):
        a.add_argument("--grep", default="", help="regex; print text around each match")
        a.add_argument("--width", type=int, default=240)
    args = ap.parse_args(argv)

    if args.cmd == "search":
        yt = YouTube()
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
        for v in yt.search(args.query, sort=args.sort, limit=args.limit):
            meta = yt.video(v["id"], v["title"])
            aired = YouTube.aired(meta)
            if not aired or aired < since:
                continue
            hits = _hits(yt.transcript(v["id"], aired), args.grep, args.width)
            print(f"\n## {meta['views']:,} views | {meta['channel']} | {aired:%m-%d %H:%M} | "
                  f"{meta['title']}\n   https://youtu.be/{v['id']}")
            for h in hits[:8]:
                print("   -", h.replace("\n", " "))
    else:
        pods = Podcasts()
        shows = pods.find(args.show)
        if not shows:
            print(f"no podcast found for {args.show!r}", file=sys.stderr)
            return 1
        show = shows[0]
        print(f"{show['name']} ({show['author']})")
        for ep in pods.episodes(show["feed"], limit=args.latest):
            print(f"\n## {ep['published'][:10]} | {ep['title']} ({ep['seconds'] // 60} min)")
            for h in _hits(pods.transcript(ep), args.grep, args.width)[:12]:
                print("   -", h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
