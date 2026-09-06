#!/usr/bin/env python3
"""Build today's DK MLB DFS pitcher value board (Vegas-implied projections).

Salaries from the public draftables API (0 credits). Projections from sportsbook
pitcher props (Odds API). Value = projected DK pts per $1,000 of salary.
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.wnba_scout import load_env  # noqa: E402
from edge.odds.cli import (add_source_args, client_from_args,  # noqa: E402
                          describe)
from edge import dfs  # noqa: E402

SPORT = "baseball_mlb"


def pmkts_for(dk_markets, pitcher):
    """Collect a pitcher's prop markets from a DK bookmaker payload."""
    out = {}
    for m in dk_markets:
        d = {}
        for o in m.get("outcomes", []):
            if o.get("description") == pitcher:
                d[o["name"]] = o["price"]
                d["point"] = o.get("point")
        if d:
            out[m["key"]] = d
    return out


def main():
    ap = argparse.ArgumentParser()
    add_source_args(ap)
    args = ap.parse_args()
    load_env()
    c = client_from_args(args, SPORT, spend=True)
    print(describe(c))

    gid = dfs.main_slate_group(dfs.mlb_draft_groups())
    salaries = dfs.fetch_draftables(gid)
    print(f"draft group {gid}: {len(salaries)} players w/ salaries (0 cr)")

    events = c.get_events(SPORT)
    print(f"MLB games: {len(events)} | est ~{len(events)*len(dfs.P_MARKETS)} cr | rem {c.remaining_credits()}\n")
    rows = []
    for ev in events:
        eo = c.get_event_odds(SPORT, ev["id"], dfs.P_MARKETS, "us")
        dk = next((b for b in eo.get("bookmakers", []) if b["key"] == "draftkings"), None)
        if not dk:
            continue
        pitchers = {o.get("description") for m in dk["markets"] for o in m.get("outcomes", [])
                    if o.get("description")}
        for pit in pitchers:
            proj = dfs.project_pitcher(pmkts_for(dk["markets"], pit))
            sal = salaries.get(dfs.norm(pit))
            if proj["proj"] is None or not sal or not sal.get("salary"):
                continue
            value = proj["proj"] / (sal["salary"] / 1000.0)
            rows.append({"pitcher": pit, "team": sal["team"], "salary": sal["salary"],
                         "proj": proj["proj"], "value": round(value, 2),
                         "imputed": "/".join(proj["imputed"]) or "-", "components": proj["components"]})

    rows.sort(key=lambda r: -r["value"])
    out = ROOT / "data/dfs_pitcher_board.csv"
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["pitcher", "team", "salary", "proj_pts", "value_per_1k", "imputed"])
        for r in rows:
            w.writerow([r["pitcher"], r["team"], r["salary"], r["proj"], r["value"], r["imputed"]])
    print(f"saved -> {out}\n")
    print(f"{'pitcher':22} {'tm':3} {'salary':>6} {'proj':>5} {'val/1k':>6} {'imputed':>10}  components")
    print("-" * 96)
    for r in rows[:18]:
        print(f"{r['pitcher'][:22]:22} {str(r['team']):3} {r['salary']:>6} {r['proj']:>5} "
              f"{r['value']:>6.2f} {r['imputed']:>10}  {r['components']}")
    print(f"\nSpent {c.spent_this_session} cr | remaining {c.remaining_credits()}")


if __name__ == "__main__":
    main()
