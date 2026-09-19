"""Minimal requests-compatible shim over urllib, with curl for DraftKings.

edge/ has no third-party PYTHON dependencies on purpose (see requirements.txt),
and the arbitrage scrapers were written against `requests`. Rather than
rewrite every call site, this exposes the small surface they use --
Session.get, .status_code, .json(), .text, .headers, .raise_for_status() --
on top of the standard library.

WHY DRAFTKINGS GOES THROUGH curl, NOT urllib
2026-09-19: sportsbook-nash.draftkings.com started gating on the HTTP/2
handshake itself, separate from the IP-reputation block documented in
DRAFTKINGS_ACCESS.md. Confirmed by isolating every variable: the exact same
headers over HTTP/2 got a 200 with real JSON; the exact same headers and
client forced to HTTP/1.1 got the Akamai 403, whether that client was `curl
--http1.1` or Python's `urllib` (which can only ever speak HTTP/1.1 -- the
stdlib has no HTTP/2 support). No header combination fixed it; only the
protocol did. This is a deliberate browser-fingerprint match, not a bug
workaround for its own sake -- see DRAFTKINGS_ACCESS.md before touching this.

Shelling out to the system `curl` binary (present on the desktop already;
this repo's own diagnostics depend on it) gets HTTP/2 without adding a PyPI
dependency. Scoped to *.draftkings.com only -- the GitHub PUT in
scan_request.py also rides this shim and runs from Streamlit Cloud (when the
phone taps "Request a desktop scan"), where a `curl` binary is not a given
and HTTP/2 buys that call nothing anyway. Everything else keeps the plain
urllib path unchanged.
"""
from __future__ import annotations

import gzip
import json as _json
import os
import subprocess
import tempfile
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
        return self.request("GET", url, params=params, headers=headers,
                            timeout=timeout, data=data)

    def put(self, url: str, params=None, headers=None, timeout: float = 20.0,
            data=None, json=None) -> Response:
        return self.request("PUT", url, params=params, headers=headers,
                            timeout=timeout, data=data, json=json)

    def post(self, url: str, params=None, headers=None, timeout: float = 20.0,
             data=None, json=None) -> Response:
        return self.request("POST", url, params=params, headers=headers,
                            timeout=timeout, data=data, json=json)

    def request(self, method: str, url: str, params=None, headers=None,
                timeout: float = 20.0, data=None, json=None) -> Response:
        """One transport for every verb.

        `json=` serialises the body and sets Content-Type, the way requests
        does -- the GitHub contents API (scan_request.py) needs a real PUT with
        a JSON body, and routing that through `get` would have sent it as a GET.
        """
        if json is not None:
            data = _json.dumps(json).encode()
            headers = dict(headers or {})
            headers.setdefault("Content-Type", "application/json")
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
        host = urllib.parse.urlparse(url).hostname or ""
        if is_draftkings(host) and have_curl():
            return _request_curl(method, url, merged, data, timeout)
        return _request_urllib(method, url, merged, data, timeout)


def is_draftkings(host: str) -> bool:
    """Hosts that need the HTTP/2 path. Used by edge/dfs.py too, so the rule
    lives in one place rather than being re-spelled per call site."""
    host = (host or "").lower()
    return host == "draftkings.com" or host.endswith(".draftkings.com")


_CURL: bool | None = None


def have_curl() -> bool:
    """Whether a usable `curl` binary exists, probed once per process.

    Streamlit Cloud is the reason this is a fallback and not an assert: the
    www.draftkings.com lobby endpoint is NOT blocked from Cloud and is served
    fine over HTTP/1.1, so a missing `curl` there must degrade to urllib
    rather than raise -- raising would take out a call that currently works.
    The api.draftkings.com calls that genuinely need HTTP/2 fail closed on
    their own (403 -> snapshot fallback in edge/dfs.py)."""
    global _CURL
    if _CURL is None:
        try:
            _CURL = subprocess.run(["curl", "--version"], capture_output=True,
                                   timeout=5).returncode == 0
        except (OSError, subprocess.SubprocessError):
            _CURL = False
    return _CURL


def _request_urllib(method: str, url: str, headers: dict, data, timeout: float) -> Response:
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return Response(r.status, raw, dict(r.headers), url)
    except urllib.error.HTTPError as exc:                # 4xx/5xx still carry a body
        raw = exc.read() or b""
        try:
            if exc.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        except OSError:
            pass
        return Response(exc.code, raw, dict(exc.headers or {}), url)
    except Exception as exc:                              # DNS, TLS, timeout
        raise RequestException(str(exc)) from exc


def _parse_curl_headers(raw: str) -> tuple[int, dict]:
    """The last header block in a -D dump (curl follows redirects; a 1xx
    Expect/Continue or a 3xx hop each leave their own block before it)."""
    blocks = [b for b in raw.split("\r\n\r\n") if b.strip()]
    if not blocks:
        return 0, {}
    lines = blocks[-1].split("\r\n")
    parts = lines[0].split(None, 2)                       # "HTTP/2 403" / "HTTP/1.1 200 OK"
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    hdrs = {}
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            hdrs[k.strip()] = v.strip()
    return status, hdrs


def _request_curl(method: str, url: str, headers: dict, data, timeout: float) -> Response:
    """Same contract as _request_urllib, HTTP/2 handshake instead of 1.1.

    -K/config intentionally NOT used for headers: this repo's own DK headers
    carry no secret, and plain argv is far less fiddly to get right than
    hand-escaping curl's config-file quoting. The one caller that does put a
    secret through this shim (scan_request.py's GitHub PAT) is GitHub, not
    draftkings.com, so it never reaches this function -- see the module
    docstring for the host-based split.
    """
    fd, hdr_path = tempfile.mkstemp(prefix="dk_hdr_")
    os.close(fd)
    argv = ["curl", "-sS", "--compressed", "--location", "--http2",
            "-X", method, "--max-time", str(timeout), "-D", hdr_path]
    for k, v in headers.items():
        argv += ["-H", f"{k}: {v}"]
    if data is not None:
        argv += ["--data-binary", "@-"]
    argv.append(url)
    try:
        proc = subprocess.run(argv, input=data, capture_output=True, timeout=timeout + 5)
    except FileNotFoundError as exc:
        raise RequestException("curl not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RequestException(f"curl timed out after {timeout}s") from exc
    finally:
        hdr_text = ""
        try:
            with open(hdr_path, "r", errors="replace") as f:
                hdr_text = f.read()
        except OSError:
            pass
        os.unlink(hdr_path)
    if proc.returncode != 0:
        raise RequestException(
            f"curl exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    status, hdrs = _parse_curl_headers(hdr_text)
    return Response(status, proc.stdout, hdrs, url)


def get(url: str, **kw) -> Response:
    return Session().get(url, **kw)


def put(url: str, **kw) -> Response:
    return Session().put(url, **kw)


def post(url: str, **kw) -> Response:
    return Session().post(url, **kw)
