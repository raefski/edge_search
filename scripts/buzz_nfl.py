#!/usr/bin/env python3
"""Collect NFL DFS YouTube buzz for one main slate, for free.

    python3 scripts/buzz_nfl.py --date 2026-09-20

Finds the week's NFL DFS videos (YouTube search plus the channels in
data/buzz_channels_nfl.json), keeps the ones that aired before lock, pulls
their captions and counts player mentions with edge/buzz.py. Writes

    data/buzz_nfl.csv          one row per (slate, player): mentions, videos,
                               channels, reach -- what scripts/buzz_fit_nfl.py reads
    data/buzz_nfl_videos.csv   one row per (slate, video, player), so a channel
                               can be dropped or reweighted later without refetching

Both are derived counts. The captions themselves are other people's content
and this repo is public, so they stay in the gitignored data/cache/buzz/.

A backfill is as honest as a live run: every video is kept or dropped by when
it aired, and a live stream that ran past lock is cut at lock using the
caption timestamps. Sunday-morning live shows have no captions until they
end, so a run made BEFORE lock misses them; rerun after lock for the record.

Needs `pip install --user yt-dlp` for search and channel listing. Captions
are standard library only -- see Transcripts for which YouTube call still
answers and which ones don't.
"""
from __future__ import annotations

import argparse
import base64
import csv
import glob
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.buzz import (  # noqa: E402
    _name_parts, aggregate, build_aliases, count_mentions, learn_variants,
)
# Suffix-free, like the contest board's own keys: with edge.dfs.norm every
# "Jr."/"II" player was on the board twice ("Oronde Gadsden II" from the pool,
# "Oronde Gadsden" from the export), so his bare surname never counted.
from edge.names import norm  # noqa: E402
from edge.dfs_contest import parse_contest_file  # noqa: E402

CACHE = ROOT / "data/cache/buzz"
CHANNELS = ROOT / "data/buzz_channels_nfl.json"
OUT = ROOT / "data/buzz_nfl.csv"
OUT_VIDEOS = ROOT / "data/buzz_nfl_videos.csv"
PROJ_LOG = ROOT / "data/dfs_proj_log_nfl.csv"
GAMES_CSV = ROOT / "data/nflverse_games.csv"

QUERIES = [
    "NFL DFS week {w}", "NFL DFS picks week {w}", "week {w} NFL DFS draftkings",
    "week {w} NFL DFS core plays", "week {w} NFL DFS chalk", "week {w} NFL DFS GPP",
    "week {w} NFL DFS cash game", "week {w} NFL DFS ownership",
    "NFL DFS week {w} fanduel", "week {w} NFL DFS lineup",
]
PLAYER_FIELDS = ["date", "player", "dk_pos", "team", "in_pool",
                 "mentions", "videos", "channels", "reach"]
VIDEO_FIELDS = ["date", "video_id", "channel_id", "channel", "aired", "views", "live",
                "via", "title", "player", "mentions"]
SEARCH_N = 40
CHANNEL_LISTING_N = 100
WINDOW_DAYS = 6
MIN_VIEWS = 300

#: Seconds between requests. The caption API cut a home IP off after ~20
#: requests in two minutes, so nothing here is fired back to back.
PACE_PAGE = 3
PACE_CAPTIONS = 5

_DFS = re.compile(r"\b(dfs|draft\s?kings|fan\s?duel|dk|fd|gpps?|cash games?|lineups?|milly|"
                  r"millionaire|core plays?|chalk|ownership|value plays?|stacks?|stacking|"
                  r"tournaments?|leverage|optimal|contrarian|pivots?)\b", re.I)
_OFF_TOPIC = re.compile(r"\b(mlb|nba|nhl|wnba|cfb|college|ncaa[fb]?|pga|golf|nascar|ufc|mma|"
                        r"soccer|epl|mls|tennis|f1|best ?ball|dynasty|redraft|waivers?|"
                        r"showdown|single[- ]game|captain|tnf|snf|mnf|thursday|"
                        r"sunday night|monday night)\b", re.I)
_WEEK = re.compile(r"\b(?:week|wk)\s*(\d{1,2})\b", re.I)
_YEAR = re.compile(r"\b(20\d\d)\b")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}


def title_ok(title: str, week: int) -> bool:
    """A main-slate NFL DFS video. Single-game content is out: a Showdown
    show talks two teams all week and would read as buzz for both."""
    if not title or not _DFS.search(title) or _OFF_TOPIC.search(title):
        return False
    weeks = {int(w) for w in _WEEK.findall(title)}
    return not weeks or week in weeks


def nfl_week(slate: date) -> int:
    if not GAMES_CSV.exists():
        from edge.pickem_features import load_games
        load_games()
    with GAMES_CSV.open() as fh:
        for r in csv.DictReader(fh):
            if r["game_type"] == "REG" and r["gameday"] == slate.isoformat():
                return int(r["week"])
    raise SystemExit(f"{slate} is not a regular-season game day in {GAMES_CSV.name}")


def lock_utc(slate: date) -> datetime:
    """DK's Sunday main slate locks at the 1:00 PM ET kickoff."""
    local = datetime(slate.year, slate.month, slate.day, 13, 0, tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(timezone.utc)


# --- the slate's board ------------------------------------------------------

def load_pool(slate: str) -> list[dict]:
    with PROJ_LOG.open(newline="") as fh:
        return [r for r in csv.DictReader(fh) if r["date"] == slate]


def load_board(slate: str, pool: list[dict]) -> list[dict]:
    """Every DK name on the slate: the priced pool, plus the draftables
    snapshot when there is one, plus any contest export for the slate (a big
    GPP board lists nearly everyone anybody rostered)."""
    board = {norm(r["player"]): {"name": r["player"], "pos": r["dk_pos"]} for r in pool}
    gid = pool[0]["gid"] if pool else None
    snap = ROOT / f"data/draftables_snapshot/{gid}.json"
    if snap.exists():
        for p in json.loads(snap.read_text()):
            if (p.get("competition") or {}).get("name"):
                board.setdefault(norm(p["displayName"]), {"name": p["displayName"],
                                                          "pos": p.get("position")})
    dst_names = {norm(r["player"]) for r in pool if r["dk_pos"] == "DST"}
    for contest in contests_for(slate):
        for k, row in contest.items():
            if k not in board and k not in dst_names:
                board[k] = {"name": row["name"], "pos": None}
    return list(board.values())


def contests_for(slate: str) -> list[dict]:
    """The contest exports whose board matches THIS slate's pool better than
    any other logged slate's. NFL rosters barely change week to week, so a
    plain overlap threshold matched every export to every Sunday and put
    three weeks of names on each board."""
    pools: dict = {}
    with PROJ_LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            pools.setdefault(r["date"], set()).add(norm(r["player"]))
    mine = []
    for f in glob.glob(str(ROOT / "data/contest-standings-*.csv")):
        contest = parse_contest_file(f)
        if not contest:
            continue
        scores = {d: sum(k in keys for k in contest) / len(contest) for d, keys in pools.items()}
        best = max(scores, key=scores.get)
        if best == slate and scores[best] >= 0.3:
            mine.append(contest)
    return mine


def known_name_tokens() -> set:
    """Name tokens of every NFL player on file, so a real player off this
    slate is never learned as a misspelling of one on it."""
    from edge.buzz import tokenize
    names: set = set()
    for f in glob.glob(str(ROOT / "data/nfl_dk_salaries/*.json")):
        names.update(r["name"] for r in json.loads(Path(f).read_text()))
    for f in glob.glob(str(ROOT / "data/nfl_ground_truth/player_week_*.json")):
        names.update(r.get("player_display_name") or "" for r in json.loads(Path(f).read_text()))
    with PROJ_LOG.open(newline="") as fh:
        dates = set()
        for r in csv.DictReader(fh):
            names.add(r["player"])
            dates.add(r["date"])
    for d in dates:
        for contest in contests_for(d):
            names.update(row["name"] for row in contest.values())
    return {t for n in names for t, _, _ in tokenize(n)}


def first_name_history() -> Counter:
    names = set()
    for f in glob.glob(str(ROOT / "data/nfl_dk_salaries/*.json")):
        names.update(r["name"] for r in json.loads(Path(f).read_text()))
    counts: Counter = Counter()
    for n in names:
        first, _ = _name_parts(n)
        if len(first) == 1:
            counts[first[0]] += 1
    return counts


# --- YouTube ----------------------------------------------------------------

def _ydl(n: int):
    import yt_dlp
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True,
                             "skip_download": True, "playlistend": n})


def _keep(entry: dict, week: int, year: int, found: dict, via: str) -> None:
    """Title and reach checks that need no request of their own. A video too
    small to move ownership is not worth a page load or a caption fetch."""
    if not entry or not entry.get("id") or entry["id"] in found:
        return
    title = entry.get("title") or ""
    if not title_ok(title, week) or any(int(y) != year for y in _YEAR.findall(title)):
        return
    views = entry.get("view_count")
    if views is not None and views < MIN_VIEWS:
        return
    found[entry["id"]] = {"title": title, "via": via, "views": views or 0}


def search_candidates(week: int, year: int) -> dict:
    found: dict = {}
    with _ydl(SEARCH_N) as y:
        for q in QUERIES:
            q = urllib.parse.quote(q.format(w=week))
            # relevance order, then newest first (sp=CAI%3D)
            for url in (f"https://www.youtube.com/results?search_query={q}",
                        f"https://www.youtube.com/results?search_query={q}&sp=CAI%253D"):
                try:
                    res = y.extract_info(url, download=False)
                except Exception as e:  # noqa: BLE001 -- one failed query shouldn't end the run
                    print(f"  search failed: {e}")
                    continue
                for e in res.get("entries") or []:
                    _keep(e, week, year, found, "search")
                time.sleep(1)
    return found


def channel_candidates(channels: list[dict], week: int, year: int) -> dict:
    found: dict = {}
    with _ydl(CHANNEL_LISTING_N) as y:
        for ch in channels:
            if not ch.get("active", True):
                continue
            for tab in ("videos", "streams"):
                url = f"https://www.youtube.com/channel/{ch['id']}/{tab}"
                try:
                    res = y.extract_info(url, download=False)
                except Exception:  # noqa: BLE001 -- most channels have no streams tab
                    continue
                for e in res.get("entries") or []:
                    _keep(e, week, year, found, "channel")
                time.sleep(1)
    return found


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def video_meta(vid: str, title: str) -> dict:
    """Air time, views and channel from the watch page, cached. A live stream
    is timed by when it started, not by when it was scheduled."""
    path = CACHE / "meta" / f"{vid}.json"
    if path.exists():
        return json.loads(path.read_text())
    html = _get(f"https://www.youtube.com/watch?v={vid}")

    def grab(pat):
        m = re.search(pat, html)
        return m.group(1) if m else None

    owner = grab(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"') or ""
    meta = {
        "id": vid,
        "title": title,
        "channel_id": grab(r'"channelId":"(UC[\w-]{22})"'),
        "channel": json.loads('"' + owner + '"'),
        "published": grab(r'"publishDate":"([^"]+)"'),
        "live": grab(r'"isLiveContent":(true|false)') == "true",
        "live_start": grab(r'"startTimestamp":"([^"]+)"'),
        "live_end": grab(r'"endTimestamp":"([^"]+)"'),
        "views": int(grab(r'"viewCount":"(\d+)"') or 0),
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    time.sleep(PACE_PAGE)
    if meta["live"] and not meta["live_end"]:
        return meta   # still live or upcoming: don't cache, it will change
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta))
    return meta


def aired_at(meta: dict) -> datetime | None:
    stamp = (meta["live_start"] if meta["live"] else None) or meta["published"]
    return datetime.fromisoformat(stamp).astimezone(timezone.utc) if stamp else None


class Blocked(RuntimeError):
    pass


def cached_transcript(vid: str):
    """(True, segments-or-None) if this video was already fetched, else (False, None)."""
    path = CACHE / "transcripts" / f"{vid}.json"
    if path.exists():
        return True, json.loads(path.read_text()).get("segments")
    return False, None


def _store(vid: str, aired: datetime, segments: list | None, error: str = ""):
    """Cache a result. A missing track is only cached once the video is two
    days old -- a fresh live stream's captions arrive hours after it ends."""
    if not segments and datetime.now(timezone.utc) - aired < timedelta(days=2):
        return None
    path = CACHE / "transcripts" / f"{vid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"segments": segments or None, "error": error}))
    return segments or None


def _seconds(stamp: str) -> int:
    total = 0
    for part in stamp.split(":"):
        total = total * 60 + int(part)
    return total


class Transcripts:
    """Captions through youtubei/v1/get_panel -- the call YouTube's own
    "Show transcript" panel makes.

    The caption API (api/timedtext, what youtube-transcript-api and yt-dlp
    use) 429'd this home IP on 2026-09-27 and stayed blocked for 8+ hours,
    Chrome TLS impersonation and a real headless Chromium included. The
    panel's older call, get_transcript, answers FAILED_PRECONDITION to every
    client. get_panel answered a plain HTTP request with the full timestamped
    transcript. Its params are just the video id in a small protobuf, so no
    per-video token has to be scraped first.
    """
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")

    def __init__(self):
        import http.cookiejar
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.client = None

    def _session(self, vid: str) -> dict:
        """Client version and visitor id from one watch page, reused for the run."""
        if self.client is None:
            html = self.opener.open(urllib.request.Request(
                f"https://www.youtube.com/watch?v={vid}",
                headers={"User-Agent": self.UA, "Accept-Language": "en-US,en;q=0.9"}),
                timeout=30).read().decode("utf-8", "replace")
            self.client = {
                "version": re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html).group(1),
                "visitor": re.search(r'"VISITOR_DATA":"([^"]+)"', html).group(1),
            }
        return self.client

    @staticmethod
    def params(vid: str) -> str:
        inner = b"\x0a" + bytes([len(vid)]) + vid.encode() + b"\x18\x01"
        return base64.b64encode(b"\xaa\x09" + bytes([len(inner)]) + inner).decode()

    def fetch(self, vid: str, aired: datetime) -> list | None:
        c = self._session(vid)
        body = {"context": {"client": {"clientName": "WEB", "clientVersion": c["version"],
                                       "hl": "en", "gl": "US", "visitorData": c["visitor"]}},
                "panelId": "PAmodern_transcript_view", "params": self.params(vid)}
        req = urllib.request.Request(
            "https://www.youtube.com/youtubei/v1/get_panel?prettyPrint=false",
            data=json.dumps(body).encode(),
            headers={"User-Agent": self.UA, "Content-Type": "application/json",
                     "Origin": "https://www.youtube.com",
                     "Referer": f"https://www.youtube.com/watch?v={vid}",
                     "X-Youtube-Client-Name": "1", "X-Youtube-Client-Version": c["version"],
                     "X-Goog-Visitor-Id": c["visitor"]})
        try:
            data = json.loads(self.opener.open(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise Blocked(f"get_panel HTTP {e.code}") from e
            return _store(vid, aired, None, f"HTTP {e.code}")
        finally:
            time.sleep(PACE_CAPTIONS)
        segs: list = []

        def walk(o):
            if isinstance(o, dict):
                seg = o.get("transcriptSegmentViewModel")
                if seg and seg.get("timestamp"):
                    segs.append([_seconds(seg["timestamp"]), seg.get("simpleText", "")])
                    return
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(data)
        return _store(vid, aired, segs, "" if segs else "no transcript")


def previous_videos(slate: str):
    """(in_window, candidates) for the videos last counted on this slate."""
    seen: dict = {}
    if OUT_VIDEOS.exists():
        with OUT_VIDEOS.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r["date"] == slate:
                    seen.setdefault(r["video_id"], r)
    in_window, cands = [], {}
    for vid, r in seen.items():
        path = CACHE / "meta" / f"{vid}.json"
        if not path.exists():
            continue
        meta = json.loads(path.read_text())
        in_window.append((meta, aired_at(meta)))
        cands[vid] = {"via": r["via"], "title": r["title"], "views": meta["views"]}
    return in_window, cands


# --- output -----------------------------------------------------------------

def _rewrite(path: Path, fields: list[str], slate: str, rows: list[dict]) -> None:
    """Replace this slate's rows, keep every other slate's."""
    keep = []
    if path.exists():
        with path.open(newline="") as fh:
            keep = [r for r in csv.DictReader(fh) if r["date"] != slate]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(keep + rows, key=lambda r: (r["date"], r.get("video_id", ""),
                                                       r["player"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", default=None,
                    help="Sunday of the main slate, YYYY-MM-DD (default: the coming Sunday)")
    ap.add_argument("--push", action="store_true",
                    help="commit and push the buzz files for the cloud app")
    ap.add_argument("--no-channels", action="store_true", help="search only")
    ap.add_argument("--no-fetch", action="store_true",
                    help="no caption requests: count from the cache only")
    ap.add_argument("--recount", action="store_true",
                    help="no network at all: re-count this slate's last video list from the "
                         "cache (for comparing matcher changes on identical inputs)")
    args = ap.parse_args()

    if args.date is None:
        today = datetime.now(ZoneInfo("America/New_York")).date()
        args.date = (today + timedelta(days=(6 - today.weekday()) % 7)).isoformat()
    slate = date.fromisoformat(args.date)
    week, lock = nfl_week(slate), lock_utc(slate)
    start = lock - timedelta(days=WINDOW_DAYS)
    pool = load_pool(args.date)
    if not pool:
        raise SystemExit(f"no pool logged for {args.date} in {PROJ_LOG.name}")
    board = load_board(args.date, pool)
    aliases = build_aliases(board, first_name_history())
    print(f"week {week}, window {start:%a %m-%d %H:%M} -> {lock:%a %m-%d %H:%M} UTC, "
          f"{len(board)} names on the board")

    channels = json.loads(CHANNELS.read_text()) if CHANNELS.exists() else []
    if args.recount:
        in_window, cands = previous_videos(args.date)
        args.no_fetch = True
        print(f"recount: {len(in_window)} videos from {OUT_VIDEOS.name}")
    else:
        cands = search_candidates(week, slate.year)
        print(f"search: {len(cands)} candidate titles")
        if not args.no_channels and channels:
            extra = channel_candidates(channels, week, slate.year)
            new = {k: v for k, v in extra.items() if k not in cands}
            cands.update(new)
            print(f"channels: +{len(new)} candidates from {sum(c.get('active', True) for c in channels)}")

        in_window, skipped = [], Counter()
        for vid in sorted(cands, key=lambda k: -cands[k]["views"]):
            try:
                meta = video_meta(vid, cands[vid]["title"])
            except Exception as e:  # noqa: BLE001 -- one bad page shouldn't end the run
                skipped[f"page error: {type(e).__name__}"] += 1
                continue
            aired = aired_at(meta)
            if not aired or not start <= aired < lock:
                skipped["aired outside window"] += 1
                continue
            in_window.append((meta, aired))
        print(f"{len(in_window)} videos aired in the window; skipped: {dict(skipped)}")

    videos, missing = [], Counter()
    fetcher, blocked = Transcripts(), False
    for meta, aired in sorted(in_window, key=lambda m: -m[0]["views"]):
        vid = meta["id"]
        hit, segs = cached_transcript(vid)
        if not hit:
            if args.no_fetch or blocked:
                missing["not fetched"] += 1
                continue
            try:
                segs = fetcher.fetch(vid, aired)
            except Blocked as e:
                blocked = True
                print(f"  YouTube stopped answering ({e}); counting what's cached", flush=True)
                missing["not fetched"] += 1
                continue
            except Exception as e:  # noqa: BLE001 -- one bad video shouldn't end the run
                missing[f"fetch error: {type(e).__name__}"] += 1
                continue
        if not segs:
            missing["no captions"] += 1
            continue
        cutoff = (lock - aired).total_seconds()
        kept = [t for s, t in segs if s < cutoff]
        if len(kept) < len(segs):
            missing["(live stream, cut at lock)"] += 1
        videos.append({**meta, "aired": aired.isoformat(timespec="minutes"),
                       "via": cands[vid]["via"], "text": " ".join(kept)})
        if len(videos) % 10 == 0:
            print(f"  {len(videos)} videos read so far", flush=True)

    learned = learn_variants([v["text"] for v in videos], board, aliases, known_name_tokens())
    if learned:
        spelled = sorted({f"{k[0]}->{n}" for k, (n, _) in learned.items() if len(k) == 1})
        print(f"learned {len(spelled)} caption spellings: {', '.join(spelled[:20])}"
              f"{' ...' if len(spelled) > 20 else ''}")
    for v in videos:
        v["counts"] = count_mentions(v.pop("text"), {**aliases, **learned})

    seen = sum(v["views"] for v in videos)
    total = sum(m["views"] for m, _ in in_window) or 1
    print(f"{len(videos)} of {len(in_window)} videos counted ({seen / total:.0%} of their views); "
          f"{dict(missing)}")

    video_rows = [{"date": args.date, "video_id": v["id"], "channel_id": v["channel_id"],
                   "channel": v["channel"], "aired": v["aired"], "views": v["views"],
                   "live": int(v["live"]), "via": v["via"], "title": v["title"],
                   "player": name, "mentions": c}
                  for v in videos for name, c in (v["counts"].items() or [("", 0)])]
    _rewrite(OUT_VIDEOS, VIDEO_FIELDS, args.date, video_rows)

    feats = aggregate(videos)
    pos = {b["name"]: b["pos"] for b in board}
    pool_by_name = {r["player"]: r for r in pool}
    rows = []
    for name in sorted(set(feats) | set(pool_by_name)):
        f = feats.get(name, {"mentions": 0, "videos": 0, "channels": 0, "reach": 0.0})
        p = pool_by_name.get(name, {})
        rows.append({"date": args.date, "player": name, "dk_pos": p.get("dk_pos") or pos.get(name) or "",
                     "team": p.get("team", ""), "in_pool": int(bool(p)), **f})
    _rewrite(OUT, PLAYER_FIELDS, args.date, rows)

    per_channel = Counter(v["channel_id"] for v in videos)
    known = {c["id"] for c in channels}
    names = {v["channel_id"]: v["channel"] for v in videos}
    added = [{"id": cid, "name": names[cid], "active": True, "added": args.date}
             for cid, n in per_channel.items() if n >= 2 and cid and cid not in known]
    if added:
        CHANNELS.write_text(json.dumps(channels + added, indent=1) + "\n")
        print(f"added {len(added)} channels to {CHANNELS.name}: {', '.join(a['name'] for a in added)}")

    top = sorted(rows, key=lambda r: -r["mentions"])[:25]
    print(f"\n{'player':26} {'pos':4} {'ment':>5} {'vids':>5} {'chan':>5} {'our own':>8}")
    for r in top:
        own = pool_by_name.get(r["player"], {}).get("own", "")
        print(f"{r['player'][:26]:26} {r['dk_pos']:4} {r['mentions']:5} {r['videos']:5} "
              f"{r['channels']:5} {own:>8}")
    print(f"\n-> {OUT.relative_to(ROOT)}, {OUT_VIDEOS.relative_to(ROOT)}")
    if args.push:
        from scripts.odds_collect import push_snapshot
        for path, name in ((OUT, "buzz_nfl"), (OUT_VIDEOS, "buzz_nfl videos"),
                           (CHANNELS, "buzz_nfl channels")):
            if path.exists() and not push_snapshot(path, name):
                raise SystemExit(1)


if __name__ == "__main__":
    main()
