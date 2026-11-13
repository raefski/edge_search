"""Does Adam's DFS ROI rise with how much of the slate the sportsbooks price?

Adam, 2026-11-13: "so far I am doing really well in NFL DFS. I hypothesize this is because
there is more prop data or sportsbook data for NFL compared to other sports. Run a test to see
if I get better ROI if there is more available prop data."

PROP COVERAGE OF A SLATE
The share of the slate's projected DK points (our board: contests.db `projection`, one row per
player per draft group) that belongs to players with at least one player-prop line in the LAST
scan of the sport's dfs_<sport> profile that started before their game (books pull a player's
props at kickoff, so the last scan before the game is what a build could see; a scan more than
MAX_STALE_H older than the game counts as none). Read through OddsStore.scan_groups, which
covers the hot store and the F: archive alike. Alongside: the share of Adam's own rostered
players with props (exported contests only) and prop markets per covered player.

Every sport model that has props reads them (NFL offense is projected purely from props,
NBA blends P+R+A, NHL / NCAAB / NCAAF read their DK ladders), so coverage is also how much of
our projection is market-made.

THREE TESTS, cash and GPP never pooled
 1. Between sports (6 with props collected): descriptive only -- sports differ in everything
    (field skill, contest mix, model age), so this cannot separate "NFL" from "props".
 2. Within each sport, across slates: ROI and share of field beaten on the sport's own
    higher- vs lower-coverage slates (split at that sport's median), pooled over sports, plus a
    within-sport slope per +10 coverage points; 90% ranges bootstrapped by slate within sport.
 3. Mechanism: Adam's rostered players with vs without props, actual minus our projection.
    If props are the edge, the unpriced picks are where his lineups lose points.

Window: from 2026-09-06 (the first dfs_* scan); earlier slates have no prop record, not zero props.

    python3 scripts/dfs_props_roi.py            # -> data/harness/dfs_props_roi.json + tables
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from edge.names import norm                     # noqa: E402
from edge.odds.store import OddsStore           # noqa: E402

SPORTS = {"NFL": ("dfs_nfl", "americanfootball_nfl"), "NHL": ("dfs_nhl", "icehockey_nhl"),
          "NBA": ("dfs_nba", "basketball_nba"), "NCAAB": ("dfs_ncaab", "basketball_ncaab"),
          "NCAAF": ("dfs_ncaaf", "americanfootball_ncaaf"), "MLB": ("dfs_mlb", "baseball_mlb")}
START = "2026-09-06"            # first dfs_* scan in odds.db
MAX_STALE_H = 6                 # a scan this much older than the game does not count
GAME_MARKETS = {"h2h", "spreads", "totals"}
VARIANT_RANK = {"live": 0, "main": 1, "slate": 2, "recovered": 3, "rebuild": 4}
ET = ZoneInfo("America/New_York")
DB = ROOT / "data" / "contests.db"
OUT = ROOT / "data" / "harness" / "dfs_props_roi.json"
CACHE = ROOT / "data" / "harness" / "props_by_date.json"
CACHE_VERSION = 2               # 2: college team tags stripped before norm
B = 2000
#: college books spell "Arch Manning (TEX)" (edge/dfs_run_ncaaf._TEAM_SUFFIX); the board does not
_TAG = re.compile(r"\s*\([^)]*\)\s*$")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---- 1. who had props, per sport and ET date ----
def props_by_date(sport: str, store: OddsStore, cached: dict) -> dict:
    """{ET date: {norm name: [markets]}} -- player props posted in the last scan before the game.
    Dates older than two days come from the cache (archived scans never change)."""
    prof, sk = SPORTS[sport]
    now = datetime.now(timezone.utc)
    fresh_from = (now.astimezone(ET) - timedelta(days=2)).date().isoformat()
    keep = {d: v for d, v in cached.get(sport, {}).items() if d < fresh_from}
    scans = store.conn.execute("SELECT id, started_at FROM scan WHERE ok=1 AND profile=? "
                               "ORDER BY started_at", (prof,)).fetchall()
    starts = [_dt(r["started_at"]) for r in scans]
    want: dict[str, int] = {}
    ev_date: dict[str, str] = {}
    for r in store.conn.execute("SELECT event_id, commence_time FROM event WHERE sport_key=? "
                                "AND commence_time>=?", (sk, START)):
        t = _dt(r["commence_time"])
        d = t.astimezone(ET).date().isoformat()
        if t > now or d < START or d in keep:
            continue
        i = bisect.bisect_left(starts, t) - 1
        if i >= 0 and t - starts[i] <= timedelta(hours=MAX_STALE_H):
            want[r["event_id"]] = scans[i]["id"]
            ev_date[r["event_id"]] = d
    groups = store.scan_groups(sorted(set(want.values())), sk) if want else {}
    out: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for sid, gks in groups.items():
        for gk in gks:
            eid, market, subject, _ = gk.split("|", 3)
            if subject and market not in GAME_MARKETS and want.get(eid) == sid:
                out[ev_date[eid]][norm(_TAG.sub("", subject))].add(market)
    fresh = {d: {n: sorted(m) for n, m in v.items()} for d, v in out.items()}
    return {**keep, **fresh}


def games_by_date(sport: str, store: OddsStore) -> dict:
    """{ET date: events that day} -- how far a multi-day board (NFL Thu-Mon, Mon-Thu) reaches."""
    out: dict[str, int] = defaultdict(int)
    for (c,) in store.conn.execute("SELECT commence_time FROM event WHERE sport_key=? AND commence_time>=?",
                                   (SPORTS[sport][1], START)):
        out[_dt(c).astimezone(ET).date().isoformat()] += 1
    return out


class Have:
    """Who had props for a board: its date's players, and for a board with more games than its
    first day holds (an NFL Thu-Mon or Mon-Thu slate), the following days' until the games add up
    (at most 6 days: a football team plays once in that window)."""

    def __init__(self, P: dict, G: dict, conn):
        self.P, self.G = P, G
        self.games = dict(conn.execute("SELECT draft_group, COUNT(DISTINCT game) FROM slate_player "
                                       "WHERE game IS NOT NULL AND game != '' GROUP BY draft_group"))
        self.memo: dict = {}

    def __call__(self, sport: str, dg, date: str) -> dict | None:
        k = (sport, dg, date)
        if k not in self.memo:
            days, n, d = [], 0, datetime.fromisoformat(date).date()
            # only football boards span days; a basketball / hockey player plays again within the
            # week, so a later day's props would hide a missing one
            need = (self.games.get(dg) or 0) if sport in ("NFL", "NCAAF") else 0
            for i in range(7):
                day = (d + timedelta(days=i)).isoformat()
                days.append(day)
                n += self.G[sport].get(day, 0)
                if n >= need:
                    break
            got = [self.P[sport][x] for x in days if x in self.P[sport]]
            if not got:
                self.memo[k] = None
            else:
                merged: dict = {}
                for g in got:
                    for name, m in g.items():
                        merged[name] = sorted(set(merged.get(name, [])) | set(m))
                self.memo[k] = merged
        return self.memo[k]


# ---- 2. boards, Adam's entries, his lineups ----
def boards(conn) -> dict:
    """{(sport, draft_group): {player_norm: (proj, date, name)}}, one variant per board."""
    best: dict = {}
    for sport, dg, date, pn, name, proj, var, at in conn.execute(
            "SELECT sport, draft_group, date, player_norm, name, proj, variant, logged_at "
            "FROM projection WHERE source='ours' AND date>=?", (START,)):
        if sport not in SPORTS or proj is None:
            continue
        k = (sport, dg, pn)
        rank = (VARIANT_RANK.get(var, 9), -(datetime.fromisoformat(at).timestamp() if at else 0))
        if k not in best or rank < best[k][0]:
            best[k] = (rank, proj, date, name)
    out: dict = defaultdict(dict)
    for (sport, dg, pn), (_, proj, date, name) in best.items():
        out[(sport, dg)][pn] = (proj, date, name)
    return out


def board_coverage(board: dict, have: dict) -> dict:
    tot = sum(max(p, 0) for p, _, _ in board.values())
    cov = [(pn, p) for pn, (p, _, _) in board.items() if p > 0 and pn in have]
    return {"cov": sum(p for _, p in cov) / tot if tot else None,
            "markets": (sum(len(have[pn]) * p for pn, p in cov) / sum(p for _, p in cov)) if cov else 0.0,
            "players": len(board)}


def slates(rows: list[dict], have_for: Have, B_: dict, conn) -> list[dict]:
    """One row per (sport, type, date): fee, won, beat, coverage of the boards Adam played and
    of his own rostered players."""
    dg_of = dict(conn.execute("SELECT contest_id, draft_group FROM contest"))
    biggest = {}
    for (sport, dg), b in B_.items():
        d = next(iter(b.values()))[1]
        if len(b) > len(B_.get(biggest.get((sport, d)), {})):
            biggest[(sport, d)] = (sport, dg)
    picks = defaultdict(list)
    for cid, pn in conn.execute("SELECT ep.contest_id, ep.player_norm FROM entry_player ep JOIN entry e "
                                "USING (contest_id, entry_id) WHERE e.is_adam=1"):
        picks[cid].append(pn)
    agg: dict = {}
    for r in rows:
        if r["sport"] not in SPORTS or r["date"] < START or r["type"] not in ("cash", "gpp"):
            continue
        bk = (r["sport"], dg_of.get(r["contest_id"]))
        if bk not in B_:
            bk = biggest.get((r["sport"], r["date"]))
        have = have_for(r["sport"], bk[1] if bk else None, r["date"])
        if have is None:                         # no scan that day: unknown, not zero
            continue
        key = (r["sport"], r["type"], r["date"])
        a = agg.setdefault(key, {"sport": r["sport"], "type": r["type"], "date": r["date"],
                                 "entries": 0, "fee": 0.0, "won": 0.0, "beats": [], "cov_w": [],
                                 "mkt_w": [], "picks": 0, "picks_cov": 0})
        a["entries"] += 1
        a["fee"] += r["fee"]
        a["won"] += r["won"]
        if r["beat"] is not None:
            a["beats"].append(r["beat"])
        if bk in B_:
            c = board_coverage(B_[bk], have)
            if c["cov"] is not None:
                a["cov_w"].append((c["cov"], r["fee"]))
                a["mkt_w"].append((c["markets"], r["fee"]))
        for pn in picks.get(r["contest_id"], []):
            a["picks"] += 1
            a["picks_cov"] += pn in have
    out = []
    for a in agg.values():
        if not a["cov_w"]:
            continue
        w = sum(f for _, f in a["cov_w"]) or len(a["cov_w"])
        out.append({"sport": a["sport"], "type": a["type"], "date": a["date"], "entries": a["entries"],
                    "fee": round(a["fee"], 2), "won": round(a["won"], 2),
                    "roi": round(a["won"] / a["fee"] - 1, 4),
                    "beat": round(statistics.fmean(a["beats"]), 4) if a["beats"] else None,
                    "cov": round(sum(c * (f or 1) for c, f in a["cov_w"]) / w, 4),
                    "markets": round(sum(m * (f or 1) for m, f in a["mkt_w"]) / w, 2),
                    "picks": a["picks"],
                    "pick_cov": round(a["picks_cov"] / a["picks"], 4) if a["picks"] else None})
    return sorted(out, key=lambda s: (s["sport"], s["type"], s["date"]))


# ---- 3. the tests ----
def roi(ss):
    fee = sum(s["fee"] for s in ss)
    return sum(s["won"] for s in ss) / fee - 1 if fee else None


def _q(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))]


def boot(strata: dict, stat, n=B, seed=7):
    """90% range of stat(slates), resampling slates within each stratum (sport x type)."""
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        sample = []
        for ss in strata.values():
            sample += [rng.choice(ss) for _ in ss]
        v = stat(sample)
        if v is not None:
            vals.append(v)
    return (_q(vals, .05), _q(vals, .95)) if vals else (None, None)


def between(S: list[dict]) -> list[dict]:
    out = []
    for sport in SPORTS:
        for typ in ("cash", "gpp"):
            ss = [s for s in S if s["sport"] == sport and s["type"] == typ]
            if not ss:
                continue
            lo, hi = boot({0: ss}, roi)
            pc = [s["pick_cov"] for s in ss if s["pick_cov"] is not None]
            out.append({"sport": sport, "type": typ, "slates": len(ss),
                        "entries": sum(s["entries"] for s in ss),
                        "fee": round(sum(s["fee"] for s in ss), 2),
                        "roi": round(roi(ss), 4), "roi_lo": round(lo, 4), "roi_hi": round(hi, 4),
                        "beat": round(statistics.fmean(bs), 4) if (bs := [s["beat"] for s in ss if s["beat"] is not None]) else None,
                        "cov": round(statistics.fmean(s["cov"] for s in ss), 4),
                        "cov_sd": round(statistics.pstdev([s["cov"] for s in ss]), 4),
                        "pick_cov": round(statistics.fmean(pc), 4) if pc else None,
                        "markets": round(statistics.fmean(s["markets"] for s in ss), 2),
                        "enough_data": len(ss) >= 5})
    return out


def within(S: list[dict], typ: str, x: str = "cov") -> dict:
    """Higher- vs lower-coverage slates of each sport (split at its own median), pooled."""
    strata = defaultdict(list)
    for s in S:
        if s["type"] == typ and s.get(x) is not None:
            strata[s["sport"]].append(s)
    strata = {k: v for k, v in strata.items() if len(v) >= 4 and len({s[x] for s in v}) > 1}
    for k, ss in list(strata.items()):
        med = statistics.median(s[x] for s in ss)
        # above the sport's median; when most slates sit AT it (every pick priced), at-or-above
        cut = (lambda v: v > med) if any(s[x] > med for s in ss) else (lambda v: v >= med)
        if all(cut(s[x]) for s in ss):
            del strata[k]
            continue
        mean_x = statistics.fmean(s[x] for s in ss)
        bs = [s["beat"] for s in ss if s["beat"] is not None]
        mean_b = statistics.fmean(bs) if bs else 0
        for s in ss:
            s["_hi"] = cut(s[x])
            s["_dx"] = s[x] - mean_x
            s["_db"] = (s["beat"] - mean_b) if s["beat"] is not None else None

    def roi_diff(ss):
        hi, lo = [s for s in ss if s["_hi"]], [s for s in ss if not s["_hi"]]
        return roi(hi) - roi(lo) if hi and lo and roi(hi) is not None and roi(lo) is not None else None

    def beat_diff(ss):
        hi = [s["beat"] for s in ss if s["_hi"] and s["beat"] is not None]
        lo = [s["beat"] for s in ss if not s["_hi"] and s["beat"] is not None]
        return statistics.fmean(hi) - statistics.fmean(lo) if hi and lo else None

    def slope(ss):                               # beat per +10 coverage points, within sport
        pts = [(s["_dx"], s["_db"]) for s in ss if s["_db"] is not None]
        sxx = sum(dx * dx for dx, _ in pts)
        return 0.1 * sum(dx * db for dx, db in pts) / sxx if sxx else None

    allss = [s for v in strata.values() for s in v]
    if not allss:
        return {"type": typ, "x": x, "sports": [], "slates": 0}
    hi, lo = [s for s in allss if s["_hi"]], [s for s in allss if not s["_hi"]]
    per = {}
    for k, ss in strata.items():
        h, l_ = [s for s in ss if s["_hi"]], [s for s in ss if not s["_hi"]]
        per[k] = {"slates": len(ss), "cov_hi": round(statistics.fmean(s[x] for s in h), 3),
                  "cov_lo": round(statistics.fmean(s[x] for s in l_), 3),
                  "roi_hi": round(roi(h), 3), "roi_lo": round(roi(l_), 3),
                  "beat_diff": round(beat_diff(ss), 3) if beat_diff(ss) is not None else None}
    return {"type": typ, "x": x, "sports": sorted(strata), "slates": len(allss),
            "slates_hi": len(hi), "slates_lo": len(lo),
            "roi_hi": round(roi(hi), 4), "roi_lo": round(roi(lo), 4),
            "roi_diff": round(roi_diff(allss), 4), "roi_diff_90": [round(v, 4) for v in boot(strata, roi_diff)],
            "beat_diff": round(beat_diff(allss), 4), "beat_diff_90": [round(v, 4) for v in boot(strata, beat_diff)],
            "beat_per_10": round(slope(allss), 4), "beat_per_10_90": [round(v, 4) for v in boot(strata, slope)],
            "per_sport": per, "enough_data": len(allss) >= 10}


def mechanism(conn, have_for: Have, B_: dict) -> list[dict]:
    """Adam's rostered players with vs without props: actual minus our projection, by sport."""
    rows = defaultdict(list)
    q = ("SELECT c.contest_id, c.sport, c.type, c.slate_date, c.draft_group, ep.player_norm, pr.fpts "
         "FROM entry_player ep JOIN entry e USING (contest_id, entry_id) JOIN contest c USING (contest_id) "
         "JOIN player_result pr ON pr.contest_id = ep.contest_id AND pr.player_norm = ep.player_norm "
         "WHERE e.is_adam = 1 AND c.slate_date >= ?")
    for cid, sport, typ, date, dg, pn, fpts in conn.execute(q, (START,)):
        if sport not in SPORTS:
            continue
        have = have_for(sport, dg, date)
        b = B_.get((sport, dg), {})
        if have is None or pn not in b or fpts is None:
            continue
        rows[sport].append((date, pn in have, fpts - b[pn][0]))
    out = []
    for sport, rs in rows.items():
        by_date = defaultdict(list)
        for d, c, m in rs:
            by_date[d].append((c, m))

        def diff(dates):
            cov = [m for d in dates for c, m in by_date[d] if c]
            unc = [m for d in dates for c, m in by_date[d] if not c]
            return statistics.fmean(cov) - statistics.fmean(unc) if cov and unc else None
        dates = list(by_date)
        rng, vals = random.Random(11), []
        for _ in range(B):
            v = diff([rng.choice(dates) for _ in dates])
            if v is not None:
                vals.append(v)
        cov = [m for _, c, m in rs if c]
        unc = [m for _, c, m in rs if not c]
        out.append({"sport": sport, "dates": len(dates), "picks": len(rs), "picks_cov": len(cov),
                    "miss_cov": round(statistics.fmean(cov), 2) if cov else None,
                    "miss_uncov": round(statistics.fmean(unc), 2) if unc else None,
                    "diff": round(diff(dates), 2) if diff(dates) is not None else None,
                    "diff_90": [round(_q(vals, .05), 2), round(_q(vals, .95), 2)] if vals else None})
    return sorted(out, key=lambda r: list(SPORTS).index(r["sport"]))


def pct(v, sign=True):
    return "-" if v is None else (f"{v:+.0%}" if sign else f"{v:.0%}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    import dfs_roi_rows
    cached = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    cached = cached.get("props", {}) if cached.get("version") == CACHE_VERSION else {}
    store = OddsStore()
    P = {}
    for sport in SPORTS:
        P[sport] = props_by_date(sport, store, cached)
        print(f"{sport}: props on {len(P[sport])} dates", file=sys.stderr)
    CACHE.write_text(json.dumps({"version": CACHE_VERSION, "props": P}, separators=(",", ":")))
    have_for = Have(P, {s: games_by_date(s, store) for s in SPORTS}, sqlite3.connect(f"file:{DB}?mode=ro", uri=True))
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    B_ = boards(conn)
    S = slates(dfs_roi_rows.build(), have_for, B_, conn)
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "made_by": "scripts/dfs_props_roi.py", "window_from": START,
           "definitions": {
               "cov": "share of the board's projected DK points on players with >= 1 player-prop "
                      "line in the last dfs_<sport> scan before their game (fee-weighted over the boards played)",
               "pick_cov": "share of Adam's rostered players with props (exported contests)",
               "markets": "prop markets per covered player, projection-weighted",
               "roi_diff": "ROI on the sport's higher-coverage slates minus its lower-coverage ones, pooled over sports",
               "beat": "share of the field beaten, mean over entries",
               "miss": "actual DK points minus our projection, Adam's rostered players"},
           "between_sports": between(S),
           "within_sport": [within(S, t, x) for t in ("cash", "gpp") for x in ("cov", "pick_cov")],
           "mechanism": mechanism(conn, have_for, B_),
           "slates": [{k: v for k, v in s.items() if not k.startswith("_")} for s in S]}
    Path(a.out).write_text(json.dumps(res, indent=1))

    print("\n## 1. Between sports (descriptive), slates from 9/6\n")
    print("| sport | type | slates | entries | ROI [90%] | beat | board cov | your picks cov | markets/player |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in res["between_sports"]:
        print(f"| {r['sport']} | {r['type']} | {r['slates']} | {r['entries']} | {pct(r['roi'])} "
              f"[{pct(r['roi_lo'])}, {pct(r['roi_hi'])}] | {r['beat'] if r['beat'] is None else round(r['beat'], 2)} | {pct(r['cov'], False)} "
              f"(sd {r['cov_sd']:.2f}) | {pct(r['pick_cov'], False)} | {r['markets']} |")
    print("\n## 2. Within sport: higher- vs lower-coverage slates of the same sport\n")
    print("| type | coverage of | sports | slates hi / lo | ROI hi | ROI lo | ROI diff [90%] | beat diff [90%] | beat per +10 pts [90%] |")
    print("|---|---|---|---|---|---|---|---|---|")
    for w in res["within_sport"]:
        if not w["slates"]:
            continue
        print(f"| {w['type']} | {'board' if w['x'] == 'cov' else 'your picks'} | {', '.join(w['sports'])} | "
              f"{w['slates_hi']} / {w['slates_lo']} | {pct(w['roi_hi'])} | {pct(w['roi_lo'])} | "
              f"{pct(w['roi_diff'])} [{pct(w['roi_diff_90'][0])}, {pct(w['roi_diff_90'][1])}] | "
              f"{w['beat_diff']:+.3f} [{w['beat_diff_90'][0]:+.3f}, {w['beat_diff_90'][1]:+.3f}] | "
              f"{w['beat_per_10']:+.3f} [{w['beat_per_10_90'][0]:+.3f}, {w['beat_per_10_90'][1]:+.3f}] |")
    print("\n## 3. Your rostered players: actual minus our projection\n")
    print("| sport | dates | picks | with props | miss, props | miss, no props | diff [90%] |")
    print("|---|---|---|---|---|---|---|")
    for m in res["mechanism"]:
        d90 = m["diff_90"] or [None, None]
        print(f"| {m['sport']} | {m['dates']} | {m['picks']} | {m['picks_cov']} | {m['miss_cov']} | "
              f"{m['miss_uncov']} | {m['diff']} [{d90[0]}, {d90[1]}] |")
    print(f"\n{a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
