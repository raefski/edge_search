#!/usr/bin/env python3
"""Desktop side of the phone-triggered scan.

Polls the repo for a scan request written by the Streamlit app, runs the
scrapers here (where the books answer, unlike a cloud host), and pushes the
fresh snapshot back.

    python3 scripts/arb_agent.py                  # poll forever, 30s
    python3 scripts/arb_agent.py --once           # handle one pending request
    python3 scripts/arb_agent.py --interval 60
    python3 scripts/arb_agent.py --also-every 900 # ...and scan on your own every 15m

Run it under systemd so it survives a reboot:
    systemctl --user enable --now arb-agent

WHY A POLLER AND NOT A WEBHOOK
A home router has no inbound port, so nothing on the internet can reach this
machine. Polling is what works through NAT without exposing anything. The cost
is latency: a request is picked up within one interval, the scan itself takes
~40s, then Streamlit Cloud has to redeploy. Budget a couple of minutes.

WHAT IT COMMITS
Only data/arb_snapshot.json, and only by explicit path -- it will not sweep up
whatever else you were editing. It rebases on origin before pushing so a scan
never clobbers work pushed from elsewhere.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.arb import ArbConfig                              # noqa: E402
from edge.arb.run import snapshot as build_snapshot        # noqa: E402
from edge.arb.scan_request import (                         # noqa: E402
    REQUEST_PATH, ScanRequest, should_handle)

SNAPSHOT = ROOT / "data" / "arb_snapshot.json"
STATE = ROOT / "data" / ".arb_agent_state.json"   # gitignored: local bookkeeping


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}", flush=True)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, check=check)


def head() -> str:
    return git("rev-parse", "HEAD", check=False).stdout.strip()


def restart_if_code_changed(started_at: str) -> None:
    """Re-exec when the checkout has moved under us.

    The agent pulls before pushing, so a deploy lands in its working tree --
    but the running process keeps the modules it imported at startup, and goes
    on writing snapshots with the old code. That is invisible: the scan
    succeeds, the file is committed, and it is simply missing whatever the new
    code would have added. Caught after several phone-triggered scans wrote
    snapshots with no `prices` field at all, silently disabling the boost
    features they were meant to feed.

    execv rather than exiting for systemd to restart: no gap, and it works the
    same when run by hand.
    """
    now = head()
    if now and started_at and now != started_at:
        log(f"code changed ({started_at[:8]} -> {now[:8]}) — restarting to load it")
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable, *sys.argv])


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def write_state(**kw) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({**read_state(), **kw}, indent=1))


def pending_request() -> ScanRequest | None:
    """The request as it exists on the REMOTE, not the working tree.

    Read from origin/main rather than disk: the phone's commit lands on GitHub,
    and a working tree that has not been pulled would otherwise never see it.
    """
    git("fetch", "--quiet", "origin", "main", check=False)
    r = git("show", f"origin/main:{REQUEST_PATH}", check=False)
    return ScanRequest.parse(r.stdout if r.returncode == 0 else None)


def run_scan(cfg: ArbConfig) -> dict:
    log("scanning…")
    snap = build_snapshot(cfg, progress=lambda label, i, n: log(f"  [{i + 1}/{n}] {label}"))
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snap, indent=1))
    s = snap["stats"]
    log(f"  {s['quotes']:,} quotes · {s['events']} events · "
        f"{len(snap['opportunities'])} opportunities · "
        f"{len(snap.get('candidates') or [])} candidates")
    return snap


def publish(message: str) -> bool:
    """Commit the snapshot alone and push, rebasing on whatever landed first."""
    git("add", "--", str(SNAPSHOT.relative_to(ROOT)))
    staged = git("diff", "--cached", "--quiet", "--", str(SNAPSHOT.relative_to(ROOT)),
                 check=False)
    if staged.returncode == 0:
        log("  snapshot unchanged — nothing to push")
        return True
    git("commit", "--quiet", "-m", message, "--",
        str(SNAPSHOT.relative_to(ROOT)))
    # autostash so an unrelated dirty tree does not block the rebase, and is
    # put back exactly as it was afterwards
    pull = git("-c", "rebase.autoStash=true", "pull", "--rebase", "--quiet",
               "origin", "main", check=False)
    if pull.returncode != 0:
        log(f"  ! rebase failed, not pushing: {pull.stderr.strip()[:200]}")
        return False
    push = git("push", "--quiet", "origin", "main", check=False)
    if push.returncode != 0:
        log(f"  ! push failed: {push.stderr.strip()[:200]}")
        return False
    log("  pushed")
    return True


def tick(cfg: ArbConfig, max_age: float, also_every: float) -> None:
    state = read_state()
    req = pending_request()
    ok, why = should_handle(req, state.get("last_handled_id"), max_age_seconds=max_age)

    if ok:
        log(f"request {req.request_id} — {req.note or 'no note'}")
        scan_cfg = ArbConfig(**{})
        scan_cfg.__dict__.update(cfg.__dict__)
        if req.sports:
            scan_cfg.sports = req.sports
        # dataclasses.replace, not a mutation of scan_cfg.detect in place --
        # the __dict__.update above means scan_cfg.detect IS cfg.detect (same
        # object, shallow-copied reference), so writing through it would leak
        # this one request's date/live bounds into every later tick, request
        # or not.
        scan_cfg.detect = dataclasses.replace(
            cfg.detect, date_from=req.date_from, date_to=req.date_to,
            skip_live=req.skip_live)
        run_scan(scan_cfg)
        # record BEFORE pushing: a push that fails must not cause the same
        # request to be scraped again on the next tick
        write_state(last_handled_id=req.request_id,
                    last_handled_at=datetime.now(timezone.utc).isoformat())
        publish(f"arb: snapshot for scan request {req.request_id}")
        return

    if also_every > 0:
        last = state.get("last_auto_at")
        due = True
        if last:
            try:
                elapsed = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(last)).total_seconds()
                due = elapsed >= also_every
            except ValueError:
                due = True
        if due:
            log("scheduled scan")
            run_scan(cfg)
            write_state(last_auto_at=datetime.now(timezone.utc).isoformat())
            publish("arb: scheduled snapshot")
            return

    # Log a skip only when the reason CHANGES. A stale request that nobody
    # clears is otherwise reported every cycle -- at 30s that is ~2,900
    # identical lines a day, which buries the one line that matters.
    if why and why not in ("no request file", "already handled"):
        if why != state.get("last_skip_reason"):
            log(f"skip: {why}")
            write_state(last_skip_reason=why)
    elif state.get("last_skip_reason"):
        write_state(last_skip_reason="")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=30.0, help="poll seconds")
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--max-age", type=float, default=900.0,
                    help="ignore requests older than this many seconds")
    ap.add_argument("--also-every", type=float, default=0.0,
                    help="also scan unprompted every N seconds (0 = only on request)")
    ap.add_argument("--sports", nargs="+", help="default sports when a request names none")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    args = ap.parse_args()

    cfg = ArbConfig()
    if args.sports:
        cfg.sports = args.sports
    cfg.bankroll.total = args.bankroll

    started_head = head()
    log(f"agent up · {started_head[:8]} · poll {args.interval:g}s · sports {cfg.sports}"
        + (f" · auto-scan every {args.also_every:g}s" if args.also_every else ""))
    while True:
        try:
            tick(cfg, args.max_age, args.also_every)
        except KeyboardInterrupt:
            log("stopped")
            return 0
        except Exception as exc:                    # noqa: BLE001
            # one bad cycle (network blip, a 403, a push race) must not take the
            # agent down -- it is meant to run unattended for days
            log(f"! {type(exc).__name__}: {exc}")
        if args.once:
            return 0
        restart_if_code_changed(started_head)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
