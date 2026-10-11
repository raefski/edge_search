#!/usr/bin/env python3
"""Find DK contests that are not filling: a guaranteed prize pool shared by
fewer entrants than it was priced for.

    python3 scripts/dfs_overlay.py                       # every sport, $5 and under, next 12h
    python3 scripts/dfs_overlay.py --sport NHL --max-fee 1 --hours 3
    python3 scripts/dfs_overlay.py --log                 # also append a fill snapshot

A GUARANTEED contest pays its prize table whether or not it fills -- but only
to positions that HAVE an entrant. With `nt` entries at fee `a`, the money
returned per dollar is (prizes for places 1..nt) / (nt * a): above 1.0 DK is
adding money. A double-up pays po / (2a) places, so with fewer entries than
places everyone cashes and it returns exactly 2.0 -- the 11-entry MMA double-up
that paid 10 (2026-10-10). Prize tables come from DK's contest endpoint (cached;
they do not change). RESIZABLE contests are flagged: DK may shrink one that does
not fill instead of paying the overlay. Non-guaranteed contests are skipped.

Numbers are AS OF NOW; contests fill late. `--log` appends every guaranteed
contest to data/contest_fill_log.csv so the final fill can be predicted from
how far along a contest is at a given time before lock (dfs_autolog.py logs
this automatically near each lock).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs  # noqa: E402

SPORTS = ("NHL", "NFL", "CFB", "NAS", "MMA", "MLB")
FILL_LOG = ROOT / "data" / "contest_fill_log.csv"
PAYOUTS = ROOT / "data" / "cache" / "contest_payouts.json"
#: Prize-table lookups per run, best candidates first (one DK request each).
MAX_DETAILS = 40
FILL_COLS = ("seen_at", "sport", "contest_id", "template", "draft_group", "name", "fee", "max_entries",
             "entries", "prize_pool", "lock", "double_up", "game_type")


def lock_time(c) -> datetime | None:
    m = re.search(r"\d+", str(c.get("sd") or ""))
    return datetime.fromtimestamp(int(m.group()) / 1000, timezone.utc) if m else None


def contests(sport: str) -> list[dict]:
    return dfs._get(f"https://www.draftkings.com/lobby/getcontests?sport={sport}").get("Contests", [])


def rows(sport: str, now: datetime, max_fee: float, hours: float, classic_only: bool) -> list[dict]:
    out = []
    for c in contests(sport):
        attr = c.get("attr") or {}
        if attr.get("IsGuaranteed") != "true" or not c.get("a") or not c.get("nt"):
            continue
        if classic_only and c.get("gameType") != "Classic":
            continue
        t = lock_time(c)
        if t is None or not (0 < (t - now).total_seconds() / 3600 <= hours):
            continue
        fee, nt, po, m = float(c["a"]), int(c["nt"]), float(c["po"] or 0), int(c["m"] or 0)
        dbl = attr.get("IsDoubleUp") == "true"
        places = int(po // (2 * fee)) if dbl else None
        out.append({"sport": sport, "id": c["id"], "tmpl": c.get("tmpl"), "dg": c.get("dg"),
                    "name": c.get("n", ""), "fee": fee, "m": m, "nt": nt, "po": po, "lock": t,
                    "dbl": dbl, "game_type": c.get("gameType"), "fee_ok": fee <= max_fee,
                    "bound": po / (nt * fee), "places": places, "resizable": None,
                    "return": 2.0 * min(nt, places) / nt if places else None,
                    "cash_rate": min(1.0, places / nt) if places else None})
    return out


def payout_table(cid: int, cache: dict) -> dict | None:
    """{"tiers": [[min, max, value], ...], "resizable": bool} for one contest."""
    key = str(cid)
    if key not in cache:
        d = dfs._get(f"https://api.draftkings.com/contests/v1/contests/{cid}?format=json")
        cd = d.get("contestDetail", d)
        tiers = [[t["minPosition"], t["maxPosition"],
                  sum(float(x.get("value") or 0) for x in t.get("payoutDescriptions", []))]
                 for t in cd.get("payoutSummary") or []]
        cache[key] = {"tiers": tiers, "resizable": bool(cd.get("isResizable"))}
    return cache[key]


def paid_out(tiers: list, entries: int) -> float:
    """Prizes for places 1..entries."""
    return sum(v * max(0, min(hi, entries) - lo + 1) for lo, hi, v in tiers)


def price(found: list[dict]) -> None:
    """Exact return and resizable flag for the best candidates (cached tables)."""
    import json
    cache = json.loads(PAYOUTS.read_text()) if PAYOUTS.exists() else {}
    cands = sorted((r for r in found if r["fee_ok"] and r["bound"] > 1.0), key=lambda r: -r["bound"])
    for r in cands[:MAX_DETAILS]:
        try:
            pt = payout_table(r["id"], cache)
        except Exception:                                   # noqa: BLE001
            continue
        r["resizable"] = pt["resizable"]
        if pt["tiers"]:
            r["return"] = paid_out(pt["tiers"], r["nt"]) / (r["nt"] * r["fee"])
            places = max(hi for _, hi, _ in pt["tiers"])
            r["cash_rate"] = min(1.0, places / r["nt"])
    PAYOUTS.parent.mkdir(parents=True, exist_ok=True)
    PAYOUTS.write_text(json.dumps(cache))


def log(found: list[dict], now: datetime) -> None:
    new = not FILL_LOG.exists()
    with FILL_LOG.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(FILL_COLS)
        for r in found:
            w.writerow([now.isoformat(timespec="seconds"), r["sport"], r["id"], r["tmpl"], r["dg"],
                        r["name"], r["fee"], r["m"], r["nt"], r["po"],
                        r["lock"].isoformat(timespec="seconds"), int(r["dbl"]), r["game_type"]])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", action="append", choices=SPORTS)
    ap.add_argument("--max-fee", type=float, default=5.0)
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--all-types", action="store_true", help="include Showdown/Captain etc.")
    ap.add_argument("--min-return", type=float, default=0.0,
                    help="only show contests returning at least this per $ if they locked now")
    ap.add_argument("--log", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    found = []
    for sport in args.sport or SPORTS:
        try:
            found += rows(sport, now, args.max_fee, args.hours, not args.all_types)
        except Exception as exc:                            # noqa: BLE001
            print(f"{sport}: lobby unavailable ({exc})")
    if args.log:
        log(found, now)
    price(found)
    show = sorted((r for r in found if r["fee_ok"] and r["return"] is not None
                   and r["return"] >= args.min_return), key=lambda r: -r["return"])
    print(f"{'sport':5} {'locks':>7} {'fee':>5} {'entries':>9} {'pool':>8} {'$ back/$1':>9} "
          f"{'cash%':>6} {'resize':>6}  contest")
    for r in show:
        mins = (r["lock"] - now).total_seconds() / 60
        cr = f"{r['cash_rate']:.0%}" if r["cash_rate"] is not None else ""
        rz = {True: "yes", False: "no", None: "?"}[r["resizable"]]
        print(f"{r['sport']:5} {mins:6.0f}m {r['fee']:5.2f} {r['nt']:4}/{r['m']:<4} "
              f"{r['po']:8,.0f} {r['return']:9.2f} {cr:>6} {rz:>6}  {r['name'][:44]} [{r['id']}]")
    print(f"\n{len(show)} guaranteed contests priced. $ back per $1 = what an average entry "
          "gets back if the contest locked NOW (above 1.00 = overlay). Most contests fill "
          "in the last hour, so read this close to lock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
