"""Podcasts: find a show, list its episodes, get a transcript -- all free.

  * find()        Apple's public podcast directory (itunes.apple.com/search).
  * episodes()    the show's own RSS feed.
  * transcript()  in order of preference:
      1. a transcript the host publishes in the feed (<podcast:transcript>,
         VTT/SRT/JSON/text) -- instant and exact;
      2. local speech-to-text with faster-whisper on this machine's CPU.
         Free but heavy: it runs at low priority on a few threads, keeps only
         the text, and deletes the audio. `pip install faster-whisper`.

Many shows (One MANS Opinion with Jeff Mans, measured 2026-09-28) publish no
transcript and are not on YouTube, so 2 is the only free route for them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ._http import Http
from .cache import Cache

NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
      "podcast": "https://podcastindex.org/namespace/1.0"}
#: The Podcasting 2.0 namespace's original URI, still declared by older feeds.
#: No Agenda (measured 2026-09-30) uses it, and matched on the new URI alone
#: its SRT captions looked absent, sending every episode to speech-to-text.
NS_LEGACY = {"podcast": "https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md"}

#: CPU threads for speech-to-text. The machine this was built on has 20; a
#: few keeps the fan quiet and the desktop usable while an episode runs.
WHISPER_THREADS = 4
WHISPER_MODEL = "base.en"


def _stamp(s: str) -> float:
    h, m, rest = ("0:" * (2 - s.count(":")) + s.replace(",", ".")).split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parse_caption_file(text: str) -> list:
    """VTT, SRT, Podcasting 2.0 JSON, or plain text -> [[start_seconds, text], ...]."""
    body = text.strip()
    if body.startswith("{"):
        segs = json.loads(body).get("segments", [])
        return [[float(s.get("startTime", 0)), s.get("body", "")] for s in segs if s.get("body")]
    out, start, buf = [], None, []
    for line in body.splitlines():
        m = re.match(r"\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d+)\s*-->", line)
        if m:
            if buf and start is not None:
                out.append([start, " ".join(buf)])
            start, buf = _stamp(m.group(1)), []
        elif line.strip() and not line.strip().isdigit() and line.strip() != "WEBVTT":
            buf.append(re.sub(r"<[^>]+>", "", line.strip()))
    if buf:
        out.append([start or 0.0, " ".join(buf)])
    return out


class Podcasts:
    def __init__(self, cache: Cache | None = None, pace: float = 1.0):
        self.http = Http(pace)
        self.cache = cache or Cache()

    def find(self, term: str, limit: int = 5) -> list[dict]:
        """[{name, author, feed}] from Apple's podcast directory."""
        url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
            {"term": term, "media": "podcast", "limit": limit})
        data = json.loads(self.http.get(url))
        return [{"name": r.get("collectionName"), "author": r.get("artistName"),
                 "feed": r.get("feedUrl")} for r in data.get("results", []) if r.get("feedUrl")]

    def episodes(self, feed_url: str, limit: int | None = None) -> list[dict]:
        """Newest first: [{id, title, published (UTC ISO), audio, seconds, transcript_url, show}]."""
        root = ET.fromstring(self.http.get(feed_url))
        show = root.findtext("channel/title") or ""
        out = []
        for item in root.findall("channel/item")[:limit]:
            enc = item.find("enclosure")
            if enc is None or not enc.get("url"):
                continue
            guid = item.findtext("guid") or enc.get("url")
            tr = item.find("podcast:transcript", NS)
            if tr is None:
                tr = item.find("podcast:transcript", NS_LEGACY)
            dur = (item.findtext("itunes:duration", default="", namespaces=NS) or "").strip()
            pub = item.findtext("pubDate")
            out.append({
                "id": hashlib.sha1(guid.encode()).hexdigest()[:16],
                "show": show, "title": (item.findtext("title") or "").strip(),
                "published": parsedate_to_datetime(pub).isoformat() if pub else None,
                "audio": enc.get("url"),
                "seconds": int(_stamp(dur)) if ":" in dur else int(dur or 0),
                "transcript_url": tr.get("url") if tr is not None else None,
            })
        return out

    def transcript(self, episode: dict, model: str = WHISPER_MODEL,
                   threads: int = WHISPER_THREADS) -> list:
        """[[start_seconds, text], ...] for one episode, cached."""
        hit = self.cache.get("podcast_transcripts", episode["id"])
        if hit is not None:
            return hit["segments"]
        if episode.get("transcript_url"):
            segs, source = parse_caption_file(
                self.http.get(episode["transcript_url"]).decode("utf-8", "replace")), "feed"
        else:
            segs, source = self._speech_to_text(episode, model, threads), f"whisper:{model}"
        self.cache.put("podcast_transcripts", episode["id"],
                       {"segments": segs, "source": source, "title": episode["title"],
                        "show": episode.get("show"), "published": episode.get("published")})
        return segs

    def _speech_to_text(self, episode: dict, model: str, threads: int) -> list:
        from faster_whisper import WhisperModel             # optional dependency
        audio = self.cache.root / "audio" / f"{episode['id']}.mp3"
        audio.parent.mkdir(parents=True, exist_ok=True)
        try:
            audio.write_bytes(self.http.get(episode["audio"], timeout=600))
            try:
                os.nice(10)             # yield the CPU to anything interactive
            except OSError:
                pass
            whisper = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
            segments, _info = whisper.transcribe(str(audio), language="en", vad_filter=True)
            return [[round(s.start, 1), s.text.strip()] for s in segments]
        finally:
            audio.unlink(missing_ok=True)
