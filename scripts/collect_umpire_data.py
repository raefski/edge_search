#!/usr/bin/env python3
"""One-time collection: home-plate umpire -> per-game K/BB/PA totals, for
building an umpire strikeout/walk-zone tendency factor.

Free (statsapi), but needs ONE boxscore call PER GAME (no bulk endpoint has
both officials + real strikeout/walk counts), so this is a slow, one-time
background job, not something to run inline in the pipeline.

Usage: python3 scripts/collect_umpire_data.py 2025-05-01 2025-08-31
Writes data/umpire_games.json: [{"game":pk,"ump_id":id,"ump_name":..,"k":.., "bb":.., "pa":..}]
"""
import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs  # noqa: E402

OUT = ROOT / "data/umpire_games.json"


def main():
    start, end = sys.argv[1], sys.argv[2]
    sched = dfs._get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={start}&endDate={end}&hydrate=officials")
    games = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            hp = next((o["official"] for o in g.get("officials", []) if o.get("officialType") == "Home Plate"), None)
            if hp:
                games.append((g["gamePk"], hp["id"], hp["fullName"]))
    print(f"{len(games)} finished games with an HP umpire in {start}..{end}", flush=True)

    out = []
    if OUT.exists():
        out = json.loads(OUT.read_text())
        done = {r["game"] for r in out}
        games = [g for g in games if g[0] not in done]
        print(f"resuming: {len(done)} already collected, {len(games)} left", flush=True)

    for i, (pk, ump_id, ump_name) in enumerate(games):
        try:
            box = dfs._get(f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore")
        except Exception:
            continue
        k = bb = pa = 0
        for side in ("home", "away"):
            for pl in box["teams"][side]["players"].values():
                bt = pl.get("stats", {}).get("batting", {})
                k += bt.get("strikeOuts", 0)
                bb += bt.get("baseOnBalls", 0)
                pa += bt.get("plateAppearances", 0)
        if pa:
            out.append({"game": pk, "ump_id": ump_id, "ump_name": ump_name, "k": k, "bb": bb, "pa": pa})
        if (i + 1) % 100 == 0:
            OUT.write_text(json.dumps(out))
            print(f"  {i+1}/{len(games)} done", flush=True)
        time.sleep(0.05)

    OUT.write_text(json.dumps(out))
    print(f"done: {len(out)} games total -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
