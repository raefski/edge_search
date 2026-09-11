#!/usr/bin/env python3
"""One-time (and whenever CBS logs you out) step: save a CBS login session
for scripts/pickem_pool_fetch.py to reuse.

    python3 scripts/pickem_session_bootstrap.py

This is the ONLY place in this project that is ever near your CBS password
-- and even here it isn't, quite: it opens a REAL, visible browser window
and waits for YOU to log in inside it, exactly as if you'd opened
picks.cbssports.com in your own Chrome. Nothing in this script's code path
reads, stores, or transmits what you type. Once you confirm you're in, it
asks the browser to write down the cookies that login already produced --
that's the "session".

WHERE THE SESSION LIVES, AND WHY. edge/pickem_cbs.DEFAULT_SESSION_PATH
points OUTSIDE both ~/edge_search and ~/arbitrage on purpose -- not merely
gitignored, simply never inside a repo's working tree, so there is no path
by which a commit could ever pick it up. This is the one authenticated
session this project stores anywhere; see that constant's docstring for why
CBS's pick'em pool is a deliberate, scoped exception to the sportsbook rule
of never storing one.

Run this again whenever scripts/pickem_pool_fetch.py reports
SessionExpired -- CBS sessions do not last forever, and there is no way to
renew one without a human logging in.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem_cbs import (  # noqa: E402
    DEFAULT_SESSION_PATH, STEALTH_ARGS, UA, ensure_chromium_libs,
    patch_automation_tells, pool_url,
)

LOGIN_URL = "https://www.cbssports.com/login/"


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Needs playwright: pip install playwright && "
              "playwright install chromium")
        sys.exit(1)

    url = pool_url()
    session_path = Path(DEFAULT_SESSION_PATH)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    print("Opening a real, visible browser window. Log in there exactly as "
          "you would in your own browser -- this script never sees what you "
          "type.")
    if url:
        print(f"Once you can see the pool's Picks page ({url}), come back "
              "here and press Enter.\n")
    else:
        print("CBS_POOL_URL is not set (edge_search/.env), so this can only "
              "save the session -- it cannot verify it reaches your pool. "
              "Log in, then come back here and press Enter.\n")

    # Same loader fix the automated fetch needs. Harmless here (this one
    # is always run by hand, from a shell that has it) but keeping the
    # two launch paths identical is what stops one from being fixed alone.
    ensure_chromium_libs()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, args=STEALTH_ARGS)
        ctx = b.new_context(user_agent=UA)
        patch_automation_tells(ctx)
        pg = ctx.new_page()
        pg.goto(url or LOGIN_URL, wait_until="domcontentloaded")
        input("Press Enter once logged in and the Picks page is visible... ")

        ctx.storage_state(path=str(session_path))
        try:
            session_path.chmod(0o600)
        except OSError:
            pass  # best-effort; not fatal on filesystems that don't support it

        ok = True
        if url:
            # CBS 200s a dead session with a /join redirect rather than an
            # error (this module's own top docstring), so "the browser
            # closed without complaining" proves nothing -- check where it
            # actually lands before declaring success.
            pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_timeout(3000)
            landed = pg.url
            if "/join" in landed or "/login" in landed:
                print(f"\nSaved, but the pool URL redirected to {landed} -- "
                      "double check CBS_POOL_URL and that you're a member of "
                      "this pool under the account you just logged into.")
                ok = False
        b.close()

    print(f"\nSaved session to {session_path} (chmod 600).")
    if ok and url:
        print("Verified: the pool page loaded under this session.")
    print("Test it: python3 scripts/pickem_pool_fetch.py --week auto")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
