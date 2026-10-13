"""Run one cataloged job and record what happened.

    python3 -m harness run autolog-plan
    python3 -m harness run calibrate-nhl -- data/contest-standings-196557074.csv

Each run: the job's own command (registry.JOBS) plus any extra arguments, in
the repo root, under its timeout. stdout+stderr go to the terminal AND to
data/harness/logs/<date>/<job>-<HHMMSS>.log; one line per run is appended to
data/harness/runs.jsonl (job, argv, start, seconds, exit code, last lines of
output). `harness status` reads that ledger, so a failure anywhere -- a timer,
a phone-triggered rebuild, a hand run -- shows up in one place.
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from harness.registry import JOBS, ROOT

STATE = ROOT / "data" / "harness"
LEDGER = STATE / "runs.jsonl"
TAIL = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def record(entry: dict, ledger: Path | None = None) -> None:
    ledger = ledger or LEDGER
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def runs(since_hours: float | None = None, ledger: Path | None = None) -> list[dict]:
    """Ledger entries, newest last, optionally only the last `since_hours`."""
    ledger = ledger or LEDGER
    if not ledger.exists():
        return []
    out = []
    cutoff = _now().timestamp() - since_hours * 3600 if since_hours else None
    for line in ledger.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if cutoff is None or datetime.fromisoformat(e["start"]).timestamp() >= cutoff:
            out.append(e)
    return out


def run(name: str, extra: list[str] | None = None, quiet: bool = False,
        timeout: int | None = None) -> int:
    """Run job `name`; returns its exit code (124 on timeout, 127 if unknown)."""
    job = JOBS.get(name)
    if job is None:
        print(f"unknown job {name!r}; `python3 -m harness list --jobs` shows them")
        return 127
    argv = [*job.argv, *(extra or [])]
    start = _now()
    logdir = STATE / "logs" / start.strftime("%Y-%m-%d")
    logdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / f"{name}-{start.strftime('%H%M%S')}.log"
    lines: list[str] = []
    t0 = time.time()
    rc = 0
    with logfile.open("w") as log:
        log.write(f"$ {' '.join(argv)}\n")
        try:
            proc = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            deadline = t0 + (timeout or job.timeout)
            for line in proc.stdout:
                log.write(line)
                lines.append(line.rstrip("\n"))
                if len(lines) > 400:
                    lines = lines[-TAIL:]
                if not quiet:
                    print(line, end="")
                if time.time() > deadline:
                    proc.kill()
                    rc = 124
                    break
            proc.wait(timeout=30)
            rc = rc or proc.returncode
        except FileNotFoundError as exc:
            rc = 127
            lines.append(str(exc))
    seconds = round(time.time() - t0, 1)
    record({"job": name, "argv": argv, "start": start.isoformat(), "seconds": seconds,
            "rc": rc, "log": _rel(logfile), "tail": lines[-TAIL:]})
    if rc and not quiet:
        print(f"\n✗ {name} exited {rc} after {seconds}s -- log: {_rel(logfile)}")
    return rc
