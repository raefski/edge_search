#!/usr/bin/env python3
"""Parse the user's DraftKings entry-history export into (a) auto-filled
contest metadata and (b) the real-money ROI report the whole system answers to
(DFS_IMPROVEMENT_PLAN §5: contest selection is the cheapest durable lever).

Input:  data/draftkings-contest-entry-history.csv (user-downloaded from DK)
Effects:
  * data/contest_meta.json gains/updates entry_fee, field, places_paid,
    prize_pool for every contest id present in the history (type preserved;
    payout rank->$ tables still aren't in this export, but real pool +
    places-paid pin the synthetic curve to the right mass and cash line).
  * prints dollar ROI: overall, since the DFS project started (2026-06-28),
    by contest type, by entry-fee tier, by field size.

Usage: python3 scripts/dfs_entry_history.py [--since 2026-06-28]
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HIST = ROOT / "data/draftkings-contest-entry-history.csv"
META = ROOT / "data/contest_meta.json"
PROJECT_START = "2026-06-28"


def dollars(s):
    try:
        return float(str(s).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def classify(name: str) -> str:
    n = name.lower()
    if "double up" in n or "50/50" in n or "double-up" in n:
        return "cash"
    return "gpp"


def load_entries():
    rows = []
    with open(HIST, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if (r.get("Sport") or "").upper() != "MLB":
                continue
            rows.append({
                "contest_id": str(r.get("Contest_Key", "")).strip(),
                "name": r.get("Entry", ""),
                "date": (r.get("Contest_Date_EST") or "")[:10],
                "place": int(r["Place"]) if (r.get("Place") or "").strip().isdigit() else None,
                "points": dollars(r.get("Points")),
                "win": dollars(r.get("Winnings_Non_Ticket")) + dollars(r.get("Winnings_Ticket")),
                "entries": int(dollars(r.get("Contest_Entries"))),
                "fee": dollars(r.get("Entry_Fee")),
                "pool": dollars(r.get("Prize_Pool")),
                "paid": int(dollars(r.get("Places_Paid"))),
            })
    return rows


def update_meta(rows):
    meta = json.loads(META.read_text()) if META.exists() else {}
    by_cid = {}
    for e in rows:
        by_cid.setdefault(e["contest_id"], e)
    changed = 0
    for cid, e in by_cid.items():
        cur = meta.get(cid)
        if cur is None and not (ROOT / f"data/contest-standings-{cid}.csv").exists():
            continue    # only track contests we have (or had) standings for
        base = {"type": classify(e["name"])} if cur is None else (
            {"type": cur} if isinstance(cur, str) else dict(cur))
        base.update({"entry_fee": e["fee"], "field": e["entries"],
                     "places_paid": e["paid"], "prize_pool": e["pool"]})
        if meta.get(cid) != base:
            meta[cid] = base
            changed += 1
    META.write_text(json.dumps(meta, indent=1))
    return changed


def report(rows, since):
    def block(label, es):
        fees = sum(e["fee"] for e in es)
        wins = sum(e["win"] for e in es)
        cashed = sum(1 for e in es if e["win"] > 0)
        if not es or fees == 0:
            return
        print(f"  {label:28} {len(es):>4} entries  ${fees:>8.2f} in  ${wins:>8.2f} out  "
              f"net ${wins-fees:>+8.2f}  ROI {100*(wins/fees-1):>+6.1f}%  cash-rate {cashed/len(es):.0%}")

    for title, es in (("ALL MLB HISTORY", rows),
                      (f"SINCE PROJECT START ({since})", [e for e in rows if e["date"] >= since])):
        print(f"\n== {title} ==")
        block("total", es)
        for t in ("cash", "gpp"):
            block(f"  {t}", [e for e in es if classify(e["name"]) == t])
        tiers = [(0, 1.01, "  fee <= $1"), (1.01, 5.01, "  fee $1-5"), (5.01, 1e9, "  fee > $5")]
        for lo, hi, lab in tiers:
            block(lab, [e for e in es if lo <= e["fee"] < hi])
        sizes = [(0, 100, "  field <= 100"), (100, 1000, "  field 101-1000"), (1000, 10**9, "  field > 1000")]
        for lo, hi, lab in sizes:
            block(lab, [e for e in es if lo < e["entries"] <= hi])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=PROJECT_START)
    args = ap.parse_args()
    rows = load_entries()
    print(f"{len(rows)} MLB entries in history")
    changed = update_meta(rows)
    print(f"contest_meta.json: {changed} contest(s) updated with fee/field/places/pool")
    report(rows, args.since)


if __name__ == "__main__":
    main()
