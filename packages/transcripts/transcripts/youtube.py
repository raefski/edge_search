"""YouTube search, channel listings, video details and transcripts -- through
the same JSON calls youtube.com's own pages make ("innertube"), so there is
nothing to install and no API key.

Which calls work (measured 2026-09-27/28 from a home IP):
  * youtubei/v1/search, /browse, /get_panel -- answered a plain HTTP client.
  * api/timedtext (what youtube-transcript-api and yt-dlp subtitles use)
    returned 429 after ~20 quick requests and stayed blocked 8+ hours, TLS
    impersonation and a real headless Chromium included.
  * youtubei/v1/get_transcript (the older panel) -- FAILED_PRECONDITION to
    every client.
So transcripts come from get_panel, the call the "Show transcript" button makes.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone

from ._http import Blocked, Http
from .cache import Cache

SORT = {"relevance": "", "date": "CAI%3D"}
TABS = {"videos": "EgZ2aWRlb3PyBgQKAjoA", "streams": "EgdzdHJlYW1z8gYECgJ6AA%3D%3D"}


def _walk(o, key: str):
    """Every value stored under `key`, anywhere in a JSON tree."""
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                yield v
            else:
                yield from _walk(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, key)


def _text(node) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    if "content" in node:
        return node["content"]
    return "".join(r.get("text", "") for r in node.get("runs", []))


def parse_views(text: str | None) -> int:
    """'10,768 views' -> 10768, '14K views' -> 14000, 'No views' -> 0."""
    m = re.search(r"([\d.,]+)\s*([KMB]?)", (text or "").replace(",", ""))
    if not m:
        return 0
    mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
    return int(float(m.group(1)) * mult)


def _seconds(stamp: str) -> int:
    total = 0
    for part in stamp.split(":"):
        total = total * 60 + int(part)
    return total


def transcript_params(video_id: str) -> str:
    """get_panel's params: the video id in a small protobuf (captured from
    YouTube's own request)."""
    inner = b"\x0a" + bytes([len(video_id)]) + video_id.encode() + b"\x18\x01"
    return base64.b64encode(b"\xaa\x09" + bytes([len(inner)]) + inner).decode()


def _videos(page) -> list[dict]:
    """Videos in a search or channel response, in either layout YouTube
    serves -- it A/B tests them per request (seen 2026-09-28: the same
    date-sorted search came back as videoRenderer one call and
    lockupViewModel the next)."""
    out = []
    for v in _walk(page, "videoRenderer"):
        if not v.get("videoId"):
            continue
        owner = (v.get("ownerText", {}).get("runs") or [{}])[0]
        out.append({"id": v["videoId"], "title": _text(v.get("title")),
                    "channel": owner.get("text", ""),
                    "channel_id": owner.get("navigationEndpoint", {})
                    .get("browseEndpoint", {}).get("browseId", ""),
                    "views": parse_views(_text(v.get("viewCountText"))),
                    "published": _text(v.get("publishedTimeText"))})
    for lk in _walk(page, "lockupViewModel"):
        if not lk.get("contentId") or lk.get("contentType", "LOCKUP_CONTENT_TYPE_VIDEO") \
                != "LOCKUP_CONTENT_TYPE_VIDEO":
            continue
        md = lk.get("metadata", {}).get("lockupMetadataViewModel", {})
        parts = [p.get("text", {}).get("content", "")
                 for r in md.get("metadata", {}).get("contentMetadataViewModel", {})
                 .get("metadataRows", []) for p in r.get("metadataParts", [])]
        views = next((p for p in parts if "view" in p), "")
        when = next((p for p in parts if "ago" in p or p.startswith("Streamed")), "")
        ids = re.findall(r'"browseId":"(UC[\w-]{22})"', json.dumps(lk, separators=(",", ":")))
        out.append({"id": lk["contentId"], "title": _text(md.get("title")),
                    "channel": next((p for p in parts if p and p not in (views, when)), ""),
                    "channel_id": ids[0] if ids else "",
                    "views": parse_views(views), "published": when})
    return out


class YouTube:
    def __init__(self, cache: Cache | None = None, pace: float = 3.0):
        self.http = Http(pace)
        self.cache = cache or Cache()
        self._client: dict | None = None

    # -- plumbing ----------------------------------------------------------
    def _session(self) -> dict:
        if self._client is None:
            html = self.http.get("https://www.youtube.com/").decode("utf-8", "replace")
            self._client = {
                "version": re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html).group(1),
                "visitor": re.search(r'"VISITOR_DATA":"([^"]+)"', html).group(1),
            }
        return self._client

    def _call(self, endpoint: str, body: dict, referer: str = "https://www.youtube.com/") -> dict:
        c = self._session()
        body = {"context": {"client": {"clientName": "WEB", "clientVersion": c["version"],
                                       "hl": "en", "gl": "US", "visitorData": c["visitor"]}},
                **body}
        return self.http.post_json(
            f"https://www.youtube.com/youtubei/v1/{endpoint}?prettyPrint=false", body,
            headers={"Origin": "https://www.youtube.com", "Referer": referer,
                     "X-Youtube-Client-Name": "1", "X-Youtube-Client-Version": c["version"],
                     "X-Goog-Visitor-Id": c["visitor"]})

    def _pages(self, endpoint: str, body: dict, limit: int):
        """Responses for a listing, following continuation tokens until
        `limit` videos have been seen or the listing ends.

        Two quirks, both seen 2026-09-28: a date-sorted search sometimes comes
        back as a page of ads and filters with no videos at all (so an empty
        first page is retried), and a channel page carries several
        continuation tokens, only one of which pages the video grid (so each
        is tried until one returns videos not seen yet)."""
        for _attempt in range(3):
            data = self._call(endpoint, body)
            if _videos(data):
                break
        seen = {v["id"] for v in _videos(data)}
        while True:
            yield data
            tokens = [c["continuationEndpoint"]["continuationCommand"]["token"]
                      for c in _walk(data, "continuationItemRenderer")
                      if "continuationCommand" in c.get("continuationEndpoint", {})]
            if len(seen) >= limit:
                return
            for token in reversed(tokens):
                nxt = self._call(endpoint, {"continuation": token})
                new = {v["id"] for v in _videos(nxt)} - seen
                if new:
                    seen |= new
                    data = nxt
                    break
            else:
                return

    # -- listings ----------------------------------------------------------
    def search(self, query: str, sort: str = "relevance", limit: int = 40) -> list[dict]:
        """[{id, title, channel, channel_id, views, published}] -- `published`
        is YouTube's relative text ("3 days ago"); video() has the exact time."""
        out: dict = {}
        for page in self._pages("search", {"query": query, "params": SORT[sort]}, limit):
            for v in _videos(page):
                out.setdefault(v["id"], v)
        return list(out.values())[:limit]

    def channel_videos(self, channel_id: str, tab: str = "videos", limit: int = 60) -> list[dict]:
        """A channel's uploads (or live streams), newest first: [{id, title, views, published}]."""
        out: dict = {}
        try:
            pages = list(self._pages("browse", {"browseId": channel_id, "params": TABS[tab]}, limit))
        except Blocked:
            raise
        except Exception:                                   # noqa: BLE001 -- e.g. no streams tab
            return []
        for page in pages:
            for v in _videos(page):
                out.setdefault(v["id"], v)
        return list(out.values())[:limit]

    # -- one video ---------------------------------------------------------
    def video(self, video_id: str, title: str = "") -> dict:
        """Air time, views, channel and live status from the watch page,
        cached once the video is final. A live stream is timed by when it
        STARTED (it is scheduled days earlier); `aired` is that UTC time."""
        meta = self.cache.get("meta", video_id)
        if meta:
            return meta
        html = self.http.get(f"https://www.youtube.com/watch?v={video_id}").decode("utf-8", "replace")

        def grab(pat):
            m = re.search(pat, html)
            return m.group(1) if m else None

        owner = grab(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"') or ""
        meta = {
            "id": video_id, "title": title or json.loads(
                '"' + (grab(r'"title":\{"simpleText":"((?:[^"\\]|\\.)*)"') or "") + '"'),
            "channel_id": grab(r'"channelId":"(UC[\w-]{22})"'),
            "channel": json.loads('"' + owner + '"'),
            "published": grab(r'"publishDate":"([^"]+)"'),
            "live": grab(r'"isLiveContent":(true|false)') == "true",
            "live_start": grab(r'"startTimestamp":"([^"]+)"'),
            "live_end": grab(r'"endTimestamp":"([^"]+)"'),
            "views": int(grab(r'"viewCount":"(\d+)"') or 0),
            "seconds": int(grab(r'"lengthSeconds":"(\d+)"') or 0),
            "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if not (meta["live"] and not meta["live_end"]):     # still live: will change
            self.cache.put("meta", video_id, meta)
        return meta

    @staticmethod
    def aired(meta: dict) -> datetime | None:
        stamp = (meta["live_start"] if meta.get("live") else None) or meta.get("published")
        return datetime.fromisoformat(stamp).astimezone(timezone.utc) if stamp else None

    def transcript(self, video_id: str, aired: datetime | None = None) -> list | None:
        """[[start_seconds, text], ...] or None when the video has none.
        A missing transcript is only cached once the video is two days old --
        a live stream's arrives hours after it ends."""
        hit = self.cache.get("transcripts", video_id)
        if hit is not None:
            return hit.get("segments")
        data = self._call("get_panel", {"panelId": "PAmodern_transcript_view",
                                        "params": transcript_params(video_id)},
                          referer=f"https://www.youtube.com/watch?v={video_id}")
        segs = [[_seconds(s["timestamp"]), s.get("simpleText", "")]
                for s in _walk(data, "transcriptSegmentViewModel") if s.get("timestamp")]
        if segs or (aired and datetime.now(timezone.utc) - aired > timedelta(days=2)):
            self.cache.put("transcripts", video_id,
                           {"segments": segs or None, "error": "" if segs else "no transcript"})
        return segs or None
