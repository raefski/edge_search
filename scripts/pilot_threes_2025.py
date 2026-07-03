#!/usr/bin/env python3
"""Lean historical pilot: does our scanner's WNBA threes flag predict positive CLV?

For ~100 sampled 2025 WNBA games we pull DK + sharp lines at T-3h (early) and at
tip (close), then:
  1. GROSS DRIFT: for every DK threes line, how does the Over/Under price move
     early->close? (Is there a baseline directional bias to exploit at all?)
  2. STRATEGY REPLAY: run the live scanner on the early snapshot (DK target,
     sharp consensus, +2% threshold) and grade each flag against DK's close.
     This is the real test — does a flag beat the closing line?
Splits results first-half vs second-half of season as a crude out-of-sample check.

Historical data is immutable, so every snapshot is cached forever -> re-runs cost 0.
"""
import sys
import csv
from datetime import datetime, timedelta, timezone

ROOT = "/home/asr/Downloads/edge_search"
sys.path.insert(0, ROOT)
from scripts.wnba_scout import load_env, SHARP_BOOKS  # noqa: E402
from edge.client import OddsAPIClient  # noqa: E402
from edge.scanner import scan  # noqa: E402
from edge.oddsmath import devig  # noqa: E402

SPORT = "basketball_wnba"
N_GAMES = 100
EARLY_H = 3
MIDSEASON = "2025-07-15"


def iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
def parse(t): return datetime.fromisoformat(t.replace("Z", "+00:00"))


def dk_threes(data):
    """{player: {point, Over, Under}} for DK's player_threes."""
    out = {}
    for bm in data.get("bookmakers", []):
        if bm.get("key") != "draftkings":
            continue
        for mk in bm.get("markets", []):
            if mk.get("key") != "player_threes":
                continue
            for o in mk.get("outcomes", []):
                d = out.setdefault(o.get("description"), {})
                d["point"] = o.get("point")
                d[o["name"]] = o["price"]
    return out


def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def pct_pos(xs): return 100 * sum(1 for x in xs if x > 0) / len(xs) if xs else float("nan")


def main():
    load_env()
    c = OddsAPIClient(cache_dir=f"{ROOT}/data/cache", ledger_path=f"{ROOT}/data/odds_api_credits.json",
                      dry_run=False)

    # sample game-days across the 2025 season
    day = datetime(2025, 5, 20, tzinfo=timezone.utc)
    end = datetime(2025, 9, 10, tzinfo=timezone.utc)
    games, seen = [], set()
    while day <= end and len(games) < N_GAMES:
        he = c.get_historical_events(SPORT, iso(day.replace(hour=15)))
        for g in he.get("data", []):
            if g["id"] not in seen:
                seen.add(g["id"]); games.append(g)
        day += timedelta(days=8)
    games = games[:N_GAMES]
    est = len(games) * 2 * 10
    print(f"Sampled {len(games)} games. Estimated odds cost: ~{est} cr (cached snapshots cost 0). "
          f"Remaining: {c.remaining_credits()}\n")

    drift, flags_graded = [], []
    for i, g in enumerate(games, 1):
        ct = parse(g["commence_time"])
        try:
            early = c.get_historical_event_odds(SPORT, g["id"], iso(ct - timedelta(hours=EARLY_H)), ["player_threes"], "us")["data"]
            close = c.get_historical_event_odds(SPORT, g["id"], iso(ct), ["player_threes"], "us")["data"]
        except Exception as e:
            print(f"  [{i}] skip {g['id'][:8]}: {e}"); continue
        half = "H1" if g["commence_time"][:10] < MIDSEASON else "H2"
        dke, dkc = dk_threes(early), dk_threes(close)

        # 1) gross drift on every DK threes line present at both times, same number
        for p, e in dke.items():
            k = dkc.get(p)
            if not k or e.get("point") != k.get("point"):
                continue
            for side in ("Over", "Under"):
                if side in e and side in k:
                    drift.append({"half": half, "side": side, "point": e["point"],
                                  "clv": e[side] / k[side] - 1})  # bet early, grade vs close

        # 2) strategy replay: scanner flags at early, graded vs DK close
        for f in scan([early], target_books={"draftkings"}, ref_books=set(SHARP_BOOKS),
                      ev_threshold=0.02, min_books=2):
            if not f["market"].startswith("player_threes"):
                continue
            k = dkc.get(f["subject"])
            opp_side = "Under" if f["side"] == "Over" else "Over"
            if not k or k.get("point") != f["point"] or f["side"] not in k or opp_side not in k:
                flags_graded.append({"half": half, "status": "no_close", "clv": None}); continue
            early_opp = dke[f["subject"]].get(opp_side)
            p_close = devig([k[f["side"]], k[opp_side]])[0]
            p_taken = devig([f["dec"], early_opp])[0] if early_opp else None
            flags_graded.append({"half": half, "status": "graded", "side": f["side"],
                                 "ev_at_scan": f["ev"], "clv": f["dec"] / k[f["side"]] - 1,
                                 "prob_clv": (p_close - p_taken) if p_taken else None})
        if i % 10 == 0:
            print(f"  [{i}/{len(games)}] spend so far {c.spent_this_session} cr")

    # ---- report ----
    print(f"\n===== GROSS DRIFT (bet every DK threes line early, grade vs close) =====")
    for side in ("Over", "Under"):
        xs = [d["clv"] for d in drift if d["side"] == side]
        print(f"  {side:5}  n={len(xs):4}  mean CLV={mean(xs)*100:+.2f}%  %positive={pct_pos(xs):.0f}%")
    lo = [d["clv"] for d in drift if d["side"] == "Over" and d["point"] <= 1.5]
    hi = [d["clv"] for d in drift if d["side"] == "Over" and d["point"] >= 2.5]
    print(f"  Over, low line (<=1.5, role players): n={len(lo)} mean CLV={mean(lo)*100:+.2f}%  %pos={pct_pos(lo):.0f}%")
    print(f"  Over, high line (>=2.5):              n={len(hi)} mean CLV={mean(hi)*100:+.2f}%  %pos={pct_pos(hi):.0f}%")

    graded = [f for f in flags_graded if f["status"] == "graded"]
    nomatch = sum(1 for f in flags_graded if f["status"] == "no_close")
    print(f"\n===== STRATEGY REPLAY (scanner +2% flags graded vs DK close) =====")
    print(f"  flags: {len(flags_graded)}  graded: {len(graded)}  line-moved-off-number: {nomatch}")
    if graded:
        xs = [f["clv"] for f in graded]
        print(f"  mean price CLV={mean(xs)*100:+.2f}%  %positive={pct_pos(xs):.0f}%  "
              f"beat-close rate={pct_pos(xs):.0f}%")
        for h in ("H1", "H2"):
            hx = [f["clv"] for f in graded if f["half"] == h]
            print(f"    {h}: n={len(hx)} mean CLV={mean(hx)*100:+.2f}% %pos={pct_pos(hx):.0f}%")

    with open(f"{ROOT}/data/hist_threes_2025.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["half", "status", "side", "ev_at_scan", "clv", "prob_clv"])
        w.writeheader()
        for f in flags_graded:
            w.writerow({k: f.get(k, "") for k in ["half", "status", "side", "ev_at_scan", "clv", "prob_clv"]})
    print(f"\nTotal pilot spend: {c.spent_this_session} cr  |  remaining: {c.remaining_credits()}")


if __name__ == "__main__":
    main()
