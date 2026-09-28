"""Free transcripts of YouTube videos and podcasts, for any project.

    pip install "git+https://github.com/raefski/edge_search.git#subdirectory=packages/transcripts"

    from transcripts import YouTube, Podcasts, Ledger
    yt = YouTube()
    for v in yt.search("Eagles Bears pick", sort="date", limit=20):
        meta = yt.video(v["id"], v["title"])
        segments = yt.transcript(v["id"], YouTube.aired(meta))   # [[seconds, text], ...]

    pods = Podcasts()
    show = pods.find("Jeff Mans")[0]
    latest = pods.episodes(show["feed"], limit=1)[0]
    segments = pods.transcript(latest)          # speech-to-text if the feed has none

    python3 -m transcripts search "Eagles Bears pick" --grep "Bears|Eagles"
    python3 -m transcripts podcast "Jeff Mans" --latest 1 --grep "sleeper"

Standard library only, except podcast speech-to-text (`pip install faster-whisper`).
Everything fetched is cached (default ~/.cache/transcripts, or $TRANSCRIPTS_CACHE,
or pass Cache(dir)), so each transcript is downloaded once. See youtube.py for
which YouTube calls answer and which ones do not.
"""
from ._http import Blocked
from .cache import Cache
from .podcasts import Podcasts
from .sources import Ledger
from .youtube import YouTube

__all__ = ["Blocked", "Cache", "Ledger", "Podcasts", "YouTube"]
