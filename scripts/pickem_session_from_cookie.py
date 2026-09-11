#!/usr/bin/env python3
"""Build a CBS session from a browser you're ALREADY logged into -- for
when scripts/pickem_session_bootstrap.py's Playwright-driven login gets
stuck (2026-09-10: CAPTCHA passes, page never advances -- the signature of
detection deeper than `navigator.webdriver`, most likely the DevTools
protocol itself, which no amount of JS-property patching reaches).

    python3 scripts/pickem_session_from_cookie.py

See edge.pickem_cbs.save_cookie_session's docstring for why this sidesteps
the problem entirely instead of fighting it: fetch_pool_text only ever
REPLAYS a session for an ordinary page load, which CBS does not challenge
(a dead session quietly redirects to /join -- it does not CAPTCHA a page
view). So a cookie lifted from a browser that logged in on its own --
your everyday Edge or Chrome, already signed in -- works exactly as well as
one Playwright captured itself, without Playwright ever touching the login.

WHAT TO COPY, EXACTLY -- RECOMMENDED WAY (one specific place, no guessing
which of a page's many requests actually carries a cookie):
  1. In the browser you're logged into CBS with right now, open DevTools
     (F12 or Ctrl+Shift+I).
  2. Application tab (Chrome/Edge) -> Storage -> Cookies (left sidebar) ->
     click the entry for `https://picks.cbssports.com` specifically -- NOT
     `www.cbssports.com`, and NOT `embed.cbssports.com` (a video widget
     that loads on the page and never carries your login).
  3. Select all the rows in that table (click the first, shift-click the
     last, or Ctrl+A inside the table) and copy them.

ALTERNATIVE, if you'd rather use the Network tab: reload the Picks page,
click a request whose domain is `picks.cbssports.com` (most pages load
dozens of third-party requests -- video, analytics, ads -- that carry no
cookie at all, so this is easy to get wrong; the Application tab above
avoids that entirely), then Request Headers -> the line starting `Cookie:`
-> copy everything after the colon.

NOT "Copy as cURL" -- ~/arbitrage/HANDOFF.md section 6 names the reason for
sportsbooks and it applies here too: a cURL export drags the whole request,
headers well beyond the cookie jar. Either shape above is typed into THIS
terminal, not sent anywhere else, least of all into a chat transcript.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem_cbs import (  # noqa: E402
    SessionExpired, fetch_pool_text, pool_url, save_cookie_session,
)


def _read_paste() -> str:
    """A copied cookie TABLE is multiple lines -- a single input() call only
    ever captures the first one and silently drops the rest, which would
    have looked like "it worked" right up until fetch_pool_text found no
    session cookie among the one row that survived. Reads lines until a
    blank one, so both a one-line Cookie-header paste and a multi-row table
    paste work the same way: paste, Enter, Enter again to finish."""
    print("Paste below (one line, or multiple rows from a copied table). "
          "Press Enter on an empty line when done:")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line:
            if lines:
                break
            continue  # ignore a stray blank line before anything is typed
        lines.append(line)
    return "\n".join(lines).strip()


def main() -> None:
    print(__doc__)
    raw = _read_paste()
    if not raw:
        print("Nothing pasted -- nothing written.")
        sys.exit(1)

    try:
        path = save_cookie_session(raw)
    except ValueError as e:
        print(f"\n{e}")
        sys.exit(1)
    print(f"\nSaved to {path} (chmod 600).")

    url = pool_url()
    if not url:
        print("CBS_POOL_URL is not set (edge_search/.env) -- saved, but "
              "cannot verify it reaches your pool. Set it, then re-run "
              "scripts/pickem_pool_fetch.py --week auto to check.")
        return

    print(f"Verifying against {url} ...")
    try:
        text = fetch_pool_text(url)
    except SessionExpired as e:
        print(f"\nSTILL NOT WORKING: {e}")
        print("Double check you copied the Cookie REQUEST header (not "
              "Set-Cookie, not a cURL command) from a request to "
              "picks.cbssports.com specifically, while looking at the "
              "right pool.")
        sys.exit(1)

    print(f"\nVerified -- fetched {len(text)} characters from the picks page.")
    print("Test the full pipeline: python3 scripts/pickem_pool_fetch.py --week auto")


if __name__ == "__main__":
    main()
