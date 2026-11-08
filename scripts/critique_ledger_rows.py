#!/usr/bin/env python3
"""The critique loop's ledger as the ROI dashboard's "Is the model improving?" rows.

    python3 scripts/critique_ledger_rows.py                 # -> data/harness/dfs_roi/critique_cycles.json
    python3 scripts/critique_ledger_rows.py add --sport nba --result shipped --target "minutes RMSE" \\
        --state "tip-90 expected" --before 5.843 --after 5.710 --n 3521 --clusters 14 \\
        --hole "..." --fix "..." [--cycle 25] [--beyond-noise]    # a hand-run cycle

Asked for 2026-11-07: "track improvements in the dashboard, and if we are not improving
that is good to know too". Every cycle is a row, shipped or not; a night that finds
nothing is a result. The unattended loop (harness/critique_loop.py) appends to the
ledger itself; a cycle run by hand (/dfs-critique) is added with `add`.

One row per cycle: the date (Eastern), sport, result, the sport's target metric, the
held-out state the change moved most (the gate's primary group with the largest
improvement, else its first), before / after in the metric's own units (RMSE for a
squared loss, MAE for an absolute one), the change, n and clusters (slates / nights /
races), the hole and the fix. A later merge or drop updates its cycle's result.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "harness" / "critique_ledger.jsonl"
OUT = ROOT / "data" / "harness" / "dfs_roi" / "critique_cycles.json"
ET = ZoneInfo("America/New_York")
#: what each sport's gate measures (harness/critique_gate.SPECS): the dashboard's words
TARGET = {"nba": "minutes", "ncaab": "DK points", "nhl": "skater DK points", "ncaaf": "DK points",
          "nascar": "DK points", "nfl": "DK points", "mma": "DK points", "mlb": "hitter DK points"}
RESULT = {"pass": "Passed, re-check in 7 days", "review": "Waiting on Adam", "record": "Not shipped",
          "fail": "Not shipped", "no result": "No result", "usage limit": "Stopped: usage limit",
          "shipped": "Shipped", "not shipped": "Not shipped"}


def _best(groups: list[dict]) -> dict | None:
    prim = [g for g in groups if g.get("split") == "primary"]
    if not prim:
        return None
    return min(prim, key=lambda g: g["d"]) if any(g["d"] < 0 for g in prim) else prim[0]


def rows(ledger: list[dict]) -> list[dict]:
    out, open_ = [], {}
    for e in ledger:
        at = datetime.fromisoformat(e["at"]).astimezone(ET)
        sport = e["sport"]
        if e["event"] in ("merged", "dropped"):
            r = open_.pop((sport, e.get("hole")), None)
            if r:
                r["result"] = "Shipped" if e["event"] == "merged" else "Dropped at re-check"
                r["shipped"] = e["event"] == "merged"
            continue
        g = _best(e.get("groups") or [])
        sq = e.get("loss", "sq") == "sq"
        conv = (lambda x: math.sqrt(x)) if sq else (lambda x: x)
        before = e.get("before", conv(g["base"]) if g else None)
        after = e.get("after", conv(g["cand"]) if g else None)
        unit = e.get("target") or (f"{TARGET.get(sport, 'DK points')} {'RMSE' if sq else 'MAE'}")
        r = {"key": f"{sport}|{e['at']}", "date": at.date().isoformat(), "sport": sport.upper(),
             "result": RESULT.get(e.get("verdict"), e.get("verdict") or "?"), "target": unit,
             "state": e.get("state") or (g["state"] if g else ""),
             "before": None if before is None else round(before, 3),
             "after": None if after is None else round(after, 3),
             "change": None if before is None or after is None else round(after - before, 3),
             "change_pct": None if not before or after is None else round((after - before) / before, 4),
             "beyond_noise": bool(e.get("beyond_noise")) if "beyond_noise" in e else bool(g and g["hi"] < 0),
             "n": e.get("n", g["n"] if g else None), "clusters": e.get("clusters", g["clusters"] if g else None),
             "hole": e.get("hole") or "", "fix": e.get("fix") or "", "cycle": e.get("cycle"),
             "shipped": e.get("verdict") == "shipped", "source": "by hand" if e["event"] == "manual" else "nightly"}
        out.append(r)
        if e.get("verdict") in ("pass", "review"):
            open_[(sport, e.get("hole"))] = r
    return out


def build(ledger_path: Path = LEDGER, out: Path = OUT) -> list[dict]:
    led = [json.loads(x) for x in ledger_path.read_text().splitlines() if x.strip()] if ledger_path.exists() else []
    rs = rows(led)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rs, indent=1))
    return rs


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    a_ = sub.add_parser("add", help="record a hand-run cycle")
    a_.add_argument("--sport", required=True)
    a_.add_argument("--result", required=True, choices=["shipped", "not shipped"])
    a_.add_argument("--target", required=True, help='e.g. "minutes RMSE"')
    a_.add_argument("--state", default="")
    a_.add_argument("--before", type=float)
    a_.add_argument("--after", type=float)
    a_.add_argument("--n", type=int)
    a_.add_argument("--clusters", type=int)
    a_.add_argument("--beyond-noise", action="store_true")
    a_.add_argument("--hole", required=True)
    a_.add_argument("--fix", default="")
    a_.add_argument("--cycle")
    a_.add_argument("--at", help="ISO time (default now)")
    a = ap.parse_args(argv)
    if a.cmd == "add":
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        e = {"at": a.at or datetime.now(timezone.utc).isoformat(), "event": "manual", "sport": a.sport,
             "verdict": a.result, "target": a.target, "state": a.state, "before": a.before, "after": a.after,
             "n": a.n, "clusters": a.clusters, "beyond_noise": a.beyond_noise, "hole": a.hole, "fix": a.fix,
             "cycle": a.cycle}
        with LEDGER.open("a") as fh:
            fh.write(json.dumps(e) + "\n")
    rs = build()
    print(f"{len(rs)} cycle rows -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
