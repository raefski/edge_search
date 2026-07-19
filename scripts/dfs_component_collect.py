#!/usr/bin/env python3
"""Extend data/model_lab_rows.json with COMPONENT-level features, mostly from
data already sitting in backtest_cache.json (leak-free, ~10 free bulk calls).

Per row adds:
  c25: hitter 2025 components strictly-before-date [pa,s1,s2,s3,hr,bb,hbp,sb,so,r,rbi]
  c23, c24: same shape, full prior seasons (bulk season stats)
  bhand: batter side (L/R/S)
  sp25c: opp SP 2025 strictly-before-date [outs,bf,h,hr,bb,so,go,ao]
  sp24c: opp SP full 2024, same shape

Writes data/model_lab_rows_v2.json
Usage: python3 scripts/dfs_component_collect.py
"""
import json
import sys
from bisect import bisect_left
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs  # noqa: E402
from scripts.dfs_hitter_backtest import load_cache, save_cache  # noqa: E402

ROWS_IN = ROOT / "data" / "model_lab_rows.json"
ROWS_OUT = ROOT / "data" / "model_lab_rows_v2.json"

HIT_KEYS = ("plateAppearances", "hits", "doubles", "triples", "homeRuns",
            "baseOnBalls", "hitByPitch", "stolenBases", "strikeOuts", "runs", "rbi")
PIT_KEYS = ("battersFaced", "hits", "homeRuns", "baseOnBalls", "strikeOuts",
            "groundOuts", "airOuts")


def hit_components(st):
    g = lambda k: st.get(k, 0) or 0
    s1 = g("hits") - g("doubles") - g("triples") - g("homeRuns")
    return [g("plateAppearances"), s1, g("doubles"), g("triples"), g("homeRuns"),
            g("baseOnBalls"), g("hitByPitch"), g("stolenBases"), g("strikeOuts"),
            g("runs"), g("rbi")]


def pit_components(st):
    g = lambda k: st.get(k, 0) or 0
    try:
        outs = dfs.ip_to_outs(st.get("inningsPitched", "0"))
    except Exception:
        outs = 0
    bf = g("battersFaced") or (g("atBats") + g("baseOnBalls") + g("hitByPitch"))
    return [outs, bf, g("hits"), g("homeRuns"), g("baseOnBalls"), g("strikeOuts"),
            g("groundOuts"), g("airOuts")]


def prefix_sums(entries, comp_fn):
    """[(date, stat)] -> (sorted_dates, [cumulative component vector at i])."""
    entries = sorted(entries, key=lambda e: e[0])
    dates, cums, acc = [], [], None
    for d, st in entries:
        v = comp_fn(st)
        acc = v if acc is None else [a + b for a, b in zip(acc, v)]
        dates.append(d)
        cums.append(list(acc))
    return dates, cums


def upto(dates, cums, cutoff, width):
    """Cumulative vector strictly before cutoff."""
    i = bisect_left(dates, cutoff)
    return cums[i - 1] if i > 0 else [0] * width


def season_components(season, group, cache):
    """Full-season component vectors per player from one bulk call (cached)."""
    key = f"rawfull:{group}:{season}"
    if key not in cache:
        sp = dfs._get(f"https://statsapi.mlb.com/api/v1/stats?stats=season&season={season}"
                      f"&group={group}&sportId=1&limit=3000&playerPool=All")["stats"][0]["splits"]
        fn = hit_components if group == "hitting" else pit_components
        cache[key] = {str(s["player"]["id"]): fn(s["stat"]) for s in sp}
        save_cache(cache)
    return cache[key]


def batter_hands(pids, cache):
    """Bulk-fetch batSide for every pid not already cached under bhand:."""
    todo = [p for p in pids if f"bhand:{p}" not in cache]
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        try:
            ppl = dfs._get("https://statsapi.mlb.com/api/v1/people?personIds="
                           + ",".join(str(p) for p in chunk))["people"]
        except Exception:
            ppl = []
        got = {p["id"]: (p.get("batSide") or {}).get("code") for p in ppl}
        for p in chunk:
            cache[f"bhand:{p}"] = got.get(p)
        save_cache(cache)
    return {p: cache.get(f"bhand:{p}") for p in pids}


def main():
    rows = json.loads(ROWS_IN.read_text())
    cache = load_cache()
    hit23 = season_components(2023, "hitting", cache)
    hit24 = season_components(2024, "hitting", cache)
    pit24 = season_components(2024, "pitching", cache)

    hpids = sorted({r["pid"] for r in rows})
    hands = batter_hands(hpids, cache)
    print(f"{len(hpids)} hitters, hands resolved: {sum(1 for v in hands.values() if v)}", flush=True)

    # prefix sums per hitter (2025 game log) and per opposing SP
    hpre = {}
    for pid in hpids:
        entries = cache.get(f"hlog:{pid}:2025", [])
        hpre[pid] = prefix_sums(entries, hit_components)
    sp_pre = {}
    sp_all = sorted({k.split(":")[1] for k in cache if k.startswith("plog:")})
    for spid in sp_all:
        entries = cache.get(f"plog:{spid}:2025", [])
        sp_pre[spid] = prefix_sums(entries, pit_components)

    n_sp_missing = 0
    for r in rows:
        pid = r["pid"]
        dates, cums = hpre[pid]
        r["c25"] = upto(dates, cums, r["date"], len(HIT_KEYS))
        r["c23"] = hit23.get(str(pid), [0] * len(HIT_KEYS))
        r["c24"] = hit24.get(str(pid), [0] * len(HIT_KEYS))
        r["bhand"] = hands.get(pid)
        r["sp25c"] = None
        r["sp24c"] = None
    # second pass: the rows never stored the opposing starter's id -- recover it
    # from the bt_boxscores files ((date, hitter_pid) -> other side's starter_id)
    box = ROOT / "data" / "bt_boxscores"
    stmap = {}  # (date, hitter_pid) -> sp_id
    for f in box.glob("*.json"):
        g = json.loads(f.read_text())
        for side, other in (("home", "away"), ("away", "home")):
            spid = g[other]["starter_id"]
            for hpid, _name, _slot in g[side]["lineup"]:
                stmap[(g["date"], hpid)] = spid
    for r in rows:
        spid = stmap.get((r["date"], r["pid"]))
        if spid is None:
            n_sp_missing += 1
            continue
        r["sp_id"] = spid
        if str(spid) in sp_pre:
            dates, cums = sp_pre[str(spid)]
            r["sp25c"] = upto(dates, cums, r["date"], len(PIT_KEYS))
        r["sp24c"] = pit24.get(str(spid))
    print(f"rows: {len(rows)}, sp unmatched: {n_sp_missing}", flush=True)
    ROWS_OUT.write_text(json.dumps(rows))
    print(f"-> {ROWS_OUT}", flush=True)


if __name__ == "__main__":
    main()
