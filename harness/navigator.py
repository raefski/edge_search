"""Find the request a website's own page makes, so a scraper can replay it.

    python3 -m harness navigate https://www.dailyfaceoff.com/starting-goalies
    python3 -m harness navigate <url> --match api --click "Load more" --wait 8
    python3 -m harness navigate <url> --profile ~/.cache/edge_nav   # keep cookies between runs

The lesson this repo learned the hard way (YouTube transcripts 2026-09-28,
DK's HTTP/2 gate 2026-09-19): when a scrape is blocked, do not hand the job
back to a person -- load the page in a real browser, capture what IT asks
for, and call that endpoint directly. This opens the page in headless
Chromium (Playwright), optionally clicks through, and records every XHR/fetch
response: method, URL, status, content type, size, the request headers that
usually matter (user-agent, referer, origin, x-*), and the first characters
of a JSON body. Output: a ranked endpoint table on stdout and the full capture
in data/harness/navigate/<host>-<time>.json.

It only reads. It never submits a form or a login; for a signed-in page, sign
in once in a --profile directory by hand (`--headed`), and later runs reuse
those cookies.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from harness.runner import STATE

KEEP_HEADERS = ("user-agent", "referer", "origin", "accept", "content-type", "authorization")


def capture(url: str, match: str | None = None, clicks: list[str] | None = None,
            wait: float = 6.0, profile: str | None = None, headed: bool = False) -> list[dict]:
    from playwright.sync_api import sync_playwright
    out: list[dict] = []

    def on_response(resp):
        req = resp.request
        if req.resource_type not in ("xhr", "fetch") and "json" not in (resp.headers.get("content-type") or ""):
            return
        if match and match not in resp.url:
            return
        body, size = "", None
        try:
            raw = resp.body()
            size = len(raw)
            if "json" in (resp.headers.get("content-type") or ""):
                body = raw[:400].decode("utf-8", "replace")
        except Exception:                                   # noqa: BLE001
            pass
        hdrs = {k: v for k, v in req.headers.items()
                if k.lower() in KEEP_HEADERS or k.lower().startswith("x-")}
        out.append({"method": req.method, "url": resp.url, "status": resp.status,
                    "type": resp.headers.get("content-type"), "bytes": size,
                    "post": req.post_data[:400] if req.post_data else None,
                    "headers": hdrs, "body_head": body})

    with sync_playwright() as p:
        if profile:
            ctx = p.chromium.launch_persistent_context(str(Path(profile).expanduser()),
                                                       headless=not headed)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        else:
            browser = p.chromium.launch(headless=not headed)
            ctx = browser.new_context()
            page = ctx.new_page()
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(wait)
        for text in clicks or []:
            try:
                page.get_by_text(text, exact=False).first.click(timeout=10_000)
                time.sleep(wait)
            except Exception as exc:                        # noqa: BLE001
                print(f"  could not click {text!r}: {exc}")
        ctx.close()
    return out


def summarise(rows: list[dict]) -> str:
    """Endpoints ranked JSON-first, then by size: the data the page renders."""
    seen, lines = set(), []
    for r in sorted(rows, key=lambda r: (not (r["type"] or "").startswith("application/json"),
                                         -(r["bytes"] or 0))):
        key = (r["method"], r["url"].split("?")[0])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  {r['status']} {r['method']:<4} {(r['bytes'] or 0):>8,}B  {r['url'][:150]}")
    return "\n".join(lines) or "  (no XHR/fetch responses captured)"


def run(url: str, **kw) -> Path:
    rows = capture(url, **kw)
    host = urlparse(url).netloc.replace(":", "_")
    path = STATE / "navigate" / f"{host}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "captured": rows}, indent=1))
    print(f"{len(rows)} responses from {url}\n{summarise(rows)}\n\nfull capture: {path}")
    return path
