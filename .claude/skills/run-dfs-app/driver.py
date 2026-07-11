#!/usr/bin/env python3
"""REPL driver for the DK MLB DFS Streamlit app (app.py), for headless
agent use: launch the real Streamlit server + a headless Chromium against
it, and catch the exact tracebacks a phone user would see -- without a
phone. Wrap in tmux, send-keys one command at a time.

Usage:
    python3 .claude/skills/run-dfs-app/driver.py
    driver> launch
    driver> ss 01-landing
    driver> click text=Refresh
    driver> errors
    driver> quit
"""
import os
import readline  # noqa: F401 -- enables arrow-key history in the REPL
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
PORT = int(os.environ.get("DFS_APP_PORT", "8765"))
URL = f"http://localhost:{PORT}"
SHOT_DIR = Path(os.environ.get("SCREENSHOT_DIR", "/tmp/shots"))
SHOT_DIR.mkdir(parents=True, exist_ok=True)

state = {"proc": None, "pw": None, "browser": None, "page": None, "console_errors": []}


def cmd_launch(_arg=""):
    if state["proc"] is not None:
        print("already launched")
        return
    env = dict(os.environ)
    # No ODDS_API_KEY needed to reach the main page in cache/dry-run mode --
    # the sidebar just won't show remaining credits. Set it if you want the
    # "Pull fresh props" (paid) path exercised too.
    proc = subprocess.Popen(
        ["streamlit", "run", "app.py", "--server.headless", "true",
         "--server.port", str(PORT), "--server.address", "localhost",
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    state["proc"] = proc
    deadline = time.time() + 30
    ok = False
    while time.time() < deadline:
        try:
            import urllib.request
            urllib.request.urlopen(URL, timeout=1)
            ok = True
            break
        except Exception:
            time.sleep(0.5)
    if not ok:
        print("TIMEOUT waiting for streamlit to serve -- check `tail` for startup errors")
        return
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page()
    page.on("console", lambda msg: state["console_errors"].append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: state["console_errors"].append(f"pageerror: {exc}"))
    page.goto(URL, wait_until="networkidle", timeout=30_000)
    state.update(pw=pw, browser=browser, page=page)
    print(f"launched. serving {URL}, pid {proc.pid}")


def cmd_ss(name=""):
    page = state["page"]
    if not page:
        print("ERROR: launch first")
        return
    f = SHOT_DIR / f"{name or 'ss-' + str(int(time.time()))}.png"
    page.screenshot(path=str(f), full_page=True)
    print(f"screenshot: {f}")


def cmd_click(sel):
    page = state["page"]
    if not page:
        print("ERROR: launch first")
        return
    try:
        page.click(sel, timeout=10_000)
        page.wait_for_load_state("networkidle", timeout=15_000)
        print("clicked:", sel)
    except Exception as e:
        print("ERROR:", e)


def cmd_wait(sel):
    page = state["page"]
    if not page:
        print("ERROR: launch first")
        return
    try:
        page.wait_for_selector(sel, timeout=10_000)
        print("found:", sel)
    except Exception:
        print("TIMEOUT:", sel)


def cmd_text(sel=""):
    page = state["page"]
    if not page:
        print("ERROR: launch first")
        return
    try:
        el = page.query_selector(sel) if sel else page.query_selector("body")
        print(el.inner_text() if el else "(not found)")
    except Exception as e:
        print("ERROR:", e)


def cmd_errors(_arg=""):
    """The whole point: Streamlit's app-crashed box AND any JS console
    error/traceback, without a phone screenshot in between."""
    page = state["page"]
    if page:
        try:
            box = page.query_selector("text=has encountered an error")
            if box:
                print("STREAMLIT ERROR BOX detected on page:")
                print(page.inner_text("body")[:3000])
        except Exception:
            pass
    if state["console_errors"]:
        print("console/page errors:")
        for e in state["console_errors"]:
            print(" ", e)
    else:
        print("no console/page errors captured")


def cmd_logs(_arg=""):
    """Tail the streamlit process's own stdout (server-side tracebacks --
    more detail than the redacted browser-side error box)."""
    proc = state["proc"]
    if not proc:
        print("ERROR: launch first")
        return
    print(f"(reading available output from streamlit pid {proc.pid}; non-blocking)")
    proc.stdout.flush()


def cmd_quit(_arg=""):
    if state["browser"]:
        state["browser"].close()
    if state["pw"]:
        state["pw"].stop()
    if state["proc"]:
        state["proc"].terminate()
        try:
            state["proc"].wait(timeout=5)
        except subprocess.TimeoutExpired:
            state["proc"].kill()
    print("stopped")


COMMANDS = {
    "launch": cmd_launch, "ss": cmd_ss, "click": cmd_click, "wait": cmd_wait,
    "text": cmd_text, "errors": cmd_errors, "logs": cmd_logs, "quit": cmd_quit,
}


def main():
    print("dfs-app driver -- 'help' for commands, 'launch' to start")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            print("driver> ", end="", flush=True)
            continue
        parts = line.split(maxsplit=1)
        cmd, arg = parts[0], (parts[1] if len(parts) > 1 else "")
        if cmd == "help":
            print("commands:", ", ".join(COMMANDS))
        elif cmd in COMMANDS:
            try:
                COMMANDS[cmd](arg)
            except Exception as e:
                print("ERROR:", e)
        else:
            print("unknown:", cmd, "-- try: help")
        if cmd == "quit":
            break
        print("driver> ", end="", flush=True)


if __name__ == "__main__":
    main()
