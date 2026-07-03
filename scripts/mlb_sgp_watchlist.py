#!/usr/bin/env python3
"""Today's MLB cross-team SGP watchlist (starter Over outs/Ks + OPPONENT under).

The Odds API gives the legs, not DK's parlay price -- so this produces the
TARGET: for each candidate it prints the FAIR correlated price (legs de-vigged
from DK's own singles + measured phi). You then check DK's actual SGP price in
the app; if DK's price is LONGER than fair, it's +EV.

`room%` = the EV you'd capture if DK priced the parlay as if independent
(no correlation adjustment) -- the ceiling, and a ranking of where to look.
"""
import argparse
import csv
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.wnba_scout import load_env  # noqa: E402
from edge.client import OddsAPIClient  # noqa: E402
from edge.oddsmath import devig, decimal_to_american  # noqa: E402
from edge.sgp import load_phi_table, lookup_phi, joint_prob  # noqa: E402

SPORT = "baseball_mlb"
MARKETS = ["pitcher_outs", "pitcher_strikeouts", "team_totals"]


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def probable_pitchers(d):
    """{normalized pitcher name: team name} from the free MLB schedule."""
    url = (f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d}"
           "&hydrate=probablePitcher")
    with urllib.request.urlopen(url, timeout=30) as r:
        sched = json.load(r)
    out = {}
    for day in sched.get("dates", []):
        for g in day.get("games", []):
            for side in ("home", "away"):
                t = g["teams"][side]
                pp = t.get("probablePitcher")
                if pp:
                    out[norm(pp["fullName"])] = t["team"]["name"]
    return out


def two_way(market, desc=None):
    """{'Over':dec,'Under':dec,'point':x} for a (market, desc) from DK."""
    d = {}
    for o in market.get("outcomes", []):
        if desc is None or o.get("description") == desc:
            d[o["name"]] = o["price"]
            d["point"] = o.get("point")
    return d if "Over" in d and "Under" in d else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true",
                    help="rebuild the file from the last cached pull (0 credits)")
    args = ap.parse_args()
    load_env()
    c = OddsAPIClient(cache_dir=ROOT / "data/cache", ledger_path=ROOT / "data/odds_api_credits.json",
                      dry_run=args.from_cache, live_ttl=10**9 if args.from_cache else 600)
    table = load_phi_table(ROOT / "data/phi_table.csv")
    pp = probable_pitchers(date.today().isoformat())

    events = c.get_events(SPORT)
    print(f"MLB games: {len(events)}  | est cost ~{len(events)*len(MARKETS)} cr | remaining {c.remaining_credits()}\n")
    cand = []
    for ev in events:
        eo = c.get_event_odds(SPORT, ev["id"], MARKETS, "us")
        dk = next((b for b in eo.get("bookmakers", []) if b["key"] == "draftkings"), None)
        if not dk:
            continue
        mk = {m["key"]: m for m in dk["markets"]}
        teams = {eo["home_team"], eo["away_team"]}
        tt = mk.get("team_totals")
        for prop_key, leg_type, combo_types in (
            ("pitcher_outs", "OutsOver", {"starter_outs_over", "opp_total_under"}),
            ("pitcher_strikeouts", "KsOver", {"starter_ks_over", "opp_total_under"}),
        ):
            if prop_key not in mk or not tt:
                continue
            pitchers = {o.get("description") for o in mk[prop_key]["outcomes"]}
            for pit in pitchers:
                team = pp.get(norm(pit))
                if team not in teams:
                    continue
                opp = (teams - {team}).pop()
                pl = two_way(mk[prop_key], pit)
                ol = two_way(tt, opp)
                if not pl or not ol:
                    continue
                p = devig([pl["Over"], pl["Under"]])[0]            # starter over
                q = devig([ol["Over"], ol["Under"]])[1]            # opponent UNDER
                phi = lookup_phi(table, combo_types, ol["point"])
                if phi is None:
                    continue
                fair = 1.0 / joint_prob(p, q, phi)
                indep = 1.0 / (p * q)
                cand.append({
                    "game": f'{eo["away_team"]} @ {eo["home_team"]}',
                    "parlay": f'{pit} Over {pl["point"]:g} {prop_key.split("_")[1]} + {opp} Under {ol["point"]:g}',
                    "p": p, "q": q, "phi": phi, "fair": fair, "indep": indep,
                    "room": indep / fair - 1,
                })

    cand.sort(key=lambda x: -x["room"])

    out = ROOT / "data/mlb_sgp_watchlist.csv"
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "room_pct", "fair_odds", "indep_odds", "parlay",
                    "p", "q", "phi", "fair_american", "fair_decimal", "game"])
        for i, x in enumerate(cand, 1):
            w.writerow([i, round(x["room"] * 100, 1), decimal_to_american(x["fair"]),
                        decimal_to_american(x["indep"]), x["parlay"], round(x["p"], 3),
                        round(x["q"], 3), x["phi"], decimal_to_american(x["fair"]),
                        round(x["fair"], 3), x["game"]])
    print(f"Watchlist saved -> {out}  ({len(cand)} parlays)\n")

    print(f"{'room%':>6} {'fair':>6} {'indep':>6}  parlay")
    print("-" * 96)
    for x in cand:
        print(f'{x["room"]*100:>5.0f}% {decimal_to_american(x["fair"]):>+6d} {decimal_to_american(x["indep"]):>+6d}  '
              f'{x["parlay"]}  (p={x["p"]:.2f} q={x["q"]:.2f} φ={x["phi"]:.2f})')
    print(f"\n{len(cand)} candidates. Check DK's SGP price for the top ones: if DK's price is")
    print("LONGER than 'fair', it's +EV. 'room%' = ceiling EV if DK ignores correlation entirely.")
    print(f"Spent: {c.spent_this_session} cr | remaining: {c.remaining_credits()}")


if __name__ == "__main__":
    main()
