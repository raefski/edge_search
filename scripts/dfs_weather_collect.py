#!/usr/bin/env python3
"""Collect per-game weather (temp, wind speed/direction) + venue for every game
in data/bt_boxscores, from the free statsapi boxscore `info` text fields.

Writes data/bt_weather.json: {pk: {temp, wind_mph, wind_dir, venue}}
Resumable -- skips pks already present in the output file.

Usage: python3 scripts/dfs_weather_collect.py
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOX = ROOT / "data" / "bt_boxscores"
OUT = ROOT / "data" / "bt_weather.json"


def parse_weather(info):
    temp = wind_mph = None
    wind_dir = cond = venue = None
    for i in info:
        lab, val = i.get("label"), i.get("value", "")
        if lab == "Weather":
            m = re.match(r"(\d+)\s*degrees,?\s*(.*?)\.?$", val)
            if m:
                temp = int(m.group(1))
                cond = m.group(2).strip()
        elif lab == "Wind":
            m = re.match(r"(\d+)\s*mph,?\s*(.*?)\.?$", val)
            if m:
                wind_mph = int(m.group(1))
                wind_dir = m.group(2).strip()
        elif lab == "Venue":
            venue = val.rstrip(".")
    return {"temp": temp, "wind_mph": wind_mph, "wind_dir": wind_dir,
            "cond": cond, "venue": venue}


def main():
    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    pks = sorted(int(f.stem) for f in BOX.glob("*.json"))
    todo = [pk for pk in pks if str(pk) not in out]
    print(f"{len(todo)} of {len(pks)} games to fetch", flush=True)
    for n, pk in enumerate(todo):
        try:
            d = json.load(urllib.request.urlopen(
                f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore", timeout=30))
            out[str(pk)] = parse_weather(d.get("info", []))
        except Exception as e:
            print(f"  {pk}: {e}", flush=True)
            out[str(pk)] = None
        if (n + 1) % 50 == 0:
            OUT.write_text(json.dumps(out))
            print(f"  {n+1}/{len(todo)}", flush=True)
            time.sleep(0.2)
    OUT.write_text(json.dumps(out))
    ok = sum(1 for v in out.values() if v and v.get("temp") is not None)
    print(f"DONE: {len(out)} games, {ok} with temp", flush=True)


if __name__ == "__main__":
    main()
