"""Minimal requests-compatible shim over urllib.

edge/ has no third-party dependencies on purpose (see requirements.txt), and
the arbitrage scrapers were written against `requests`. Rather than rewrite
every call site, this exposes the small surface they use -- Session.get,
.status_code, .json(), .text, .headers, .raise_for_status() -- on top of the
standard library.
"""
from __future__ import annotations

import gzip
import json as _json
import urllib.error
import urllib.parse
import urllib.request


class RequestException(Exception):
    pass


class HTTPError(RequestException):
    def __init__(self, message: str, response: "Response | None" = None):
        super().__init__(message)
        self.response = response


class Response:
    def __init__(self, status_code: int, body: bytes, headers: dict, url: str):
        self.status_code = status_code
        self._body = body
        self.headers = headers
        self.url = url

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", "replace")

    def json(self):
        return _json.loads(self.text or "null")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"{self.status_code} for {self.url}", self)


# urllib announces itself as "Python-urllib/3.x", which these hosts reject
# outright (403). requests happened to work only because every provider set a
# UA explicitly; the shim must not depend on that.
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


class Session:
    def __init__(self):
        self.headers: dict[str, str] = {"User-Agent": DEFAULT_UA}

    def get(self, url: str, params=None, headers=None, timeout: float = 20.0,
            data=None) -> Response:
        if params:
            pairs = []
            for k, v in (params.items() if hasattr(params, "items") else params):
                if v is None:
                    continue
                if isinstance(v, (list, tuple)):
                    pairs.extend((k, str(x)) for x in v)
                else:
                    pairs.append((k, str(v)))
            sep = "&" if urllib.parse.urlparse(url).query else "?"
            url = url + sep + urllib.parse.urlencode(pairs)
        merged = dict(self.headers)
        merged.update(headers or {})
        merged.setdefault("Accept-Encoding", "gzip")
        if not any(k.lower() == "user-agent" for k in merged):
            merged["User-Agent"] = DEFAULT_UA
        req = urllib.request.Request(url, headers=merged, data=data, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return Response(r.status, raw, dict(r.headers), url)
        except urllib.error.HTTPError as exc:            # 4xx/5xx still carry a body
            raw = exc.read() or b""
            try:
                if exc.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            except OSError:
                pass
            return Response(exc.code, raw, dict(exc.headers or {}), url)
        except Exception as exc:                          # DNS, TLS, timeout
            raise RequestException(str(exc)) from exc

    def request(self, method, url, **kw) -> Response:
        return self.get(url, **kw)


def get(url: str, **kw) -> Response:
    return Session().get(url, **kw)
