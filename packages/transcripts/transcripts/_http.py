"""One polite HTTP client for every source: a cookie jar, a browser user agent,
a minimum gap between requests, and a hard stop when a site refuses us.
Standard library only."""
from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")


class Blocked(RuntimeError):
    """The site refused us (HTTP 403/429). Stop and come back later --
    retrying is what turns a short rate limit into a long one."""


class Http:
    def __init__(self, pace: float = 1.0):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.pace = pace
        self._last = 0.0

    def _open(self, req: urllib.request.Request, timeout: float) -> bytes:
        wait = self.pace - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise Blocked(f"HTTP {e.code} from {req.full_url.split('?')[0]}") from e
            raise
        finally:
            self._last = time.monotonic()

    def get(self, url: str, headers: dict | None = None, timeout: float = 30) -> bytes:
        h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", **(headers or {})}
        return self._open(urllib.request.Request(url, headers=h), timeout)

    def post_json(self, url: str, body: dict, headers: dict | None = None,
                  timeout: float = 60) -> dict:
        h = {"User-Agent": UA, "Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h)
        return json.loads(self._open(req, timeout))
