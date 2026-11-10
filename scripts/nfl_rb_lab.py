#!/usr/bin/env python3
"""NFL running backs, held out: the shipped props projection rebuilt from the pre-lock
scan of every logged board, graded with DK's own points, vs. a rushing-TD rule that also
reads the rush-attempts prop.

    python3 scripts/nfl_rb_lab.py extract            # pre-lock prop means per logged board -> cache
    python3 scripts/nfl_rb_lab.py parity             # rebuilt shipped proj == the logged proj?
    python3 scripts/nfl_rb_lab.py fit                # rush-TD-from-attempts rule, nflverse 2023-25 only
    python3 scripts/nfl_rb_lab.py skew               # refit dfs_sport.NFL_RB_YDS_SKEW, nflverse 2023-25
    python3 scripts/nfl_rb_lab.py report [--pos RB] [--cand shipped|att] [--since DATE]
        # held-out 2026 boards: the model before cycle 2 vs this checkout (or the attempts
        # rule): bias / MAE / RMSE, SSE change per slate with a 90% bootstrap over slates
    python3 scripts/nfl_rb_lab.py gate OUT           # rows for harness/critique_gate.py

Why it exists (NFL critique cycle 2, 2026-11-10, target picked by dfs_target_scout): RBs
run ~+1.3 DK pts under-projected on the 2026 boards and rushing TDs -- the largest imputed
term, 6 points each -- are scaled off projected rush yards alone (dfs_sport._nfl_impute,
1 TD / 135 yds for a back). The player_rush_attempts prop is collected every hour and read
by nothing. Carries are where goal-line work shows up, yards are not.

TRAIN / TEST. The rule is fitted on nflverse 2023-25 regular seasons (data/nfl_ground_truth,
leave-one-out season means as the stand-in for the market's implied mean, exactly as
scripts/nfl_td_fit.py fitted the shipped rate). It is scored on the 2026 live boards
(9/13 onward), which it never saw. The 2026 file is not used for fitting.

PRE-LOCK STATE. Per board (gid in data/dfs_proj_log_nfl.csv): the last finished dfs_nfl
scan before the board's first kickoff (kickoffs read off that scan's events), each
player's markets from HIS game's event only (team code via dfs_run_nfl.abbr), DraftKings'
book as production. `parity` reports how close the rebuilt shipped projection lands to the
logged one -- the logged proj is the last pre-lock build, which may be a scan or two
earlier or later, so parity is a check, not an identity.

TRUTH. DK's own FPTS for the draft group (contests.db player_result, max over the exported
contests on it), matched by draft group, as scripts/nfl_lineup_lab.py. A projected player
no export lists has no known score and is dropped AND COUNTED. Cluster = slate date.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from edge import dfs_project, dfs_sport            # noqa: E402
from edge.dfs_run_nfl import abbr                  # noqa: E402
from edge.names import norm                        # noqa: E402

LOG = ROOT / "data" / "dfs_proj_log_nfl.csv"
DB = ROOT / "data" / "contests.db"
ODDS = ROOT / "data" / "odds.db"
GT = ROOT / "data" / "nfl_ground_truth"
#: pre-lock markets per logged board (`extract`; boards already cached are skipped, so a
#: re-run only reads the new ones). gitignored with the rest of data/harness/.
CACHE = ROOT / "data" / "harness" / "nfl_rb_lab_boards.json"
SPORT_KEY = "americanfootball_nfl"
PROFILE = "dfs_nfl"
ATT = "player_rush_attempts"
#: SD of an RB's carries around his expected count, used only to turn the attempts line
#: + prices into a mean (dfs_project.implied_mean). nflverse 2023-25 RB/FB weeks with a
#: leave-one-out season mean >= 8 carries: residual SD 5.5 (n 1,934; an upper bound, since
#: the LOO mean is itself noisier than the market's). It only moves the mean when the two
#: sides are priced unevenly, so the choice is minor.
ATT_SIGMA = 5.5
TRAIN_SEASONS = (2023, 2024, 2025)
LOCK_MARGIN_MIN = 40
MIN_GAMES = 6


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


# --------------------------------------------------------------------------- boards
def boards() -> dict:
    by = defaultdict(list)
    with LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            gid = int(r["gid"]) if (r.get("gid") or "").isdigit() else None
            if gid is None or "DST" in r["dk_pos"]:
                continue
            by[gid].append({"name": r["player"], "team": r["team"], "opp": r.get("opp_team"),
                            "dk_pos": r["dk_pos"], "salary": int(_f(r["salary"], 0)),
                            "proj": _f(r["proj"]), "date": r["date"]})
    return dict(by)


def _scans(con, lo: str, hi: str) -> list:
    return con.execute("select id, finished_at from scan where profile=? and ok=1 and "
                       "finished_at >= ? and finished_at <= ? order by finished_at",
                       (PROFILE, lo, hi)).fetchall()


def _events_for(store, sid: int, cache: dict) -> list:
    if sid not in cache:
        cache[sid] = [dict(r) for r in store.events(sid, SPORT_KEY)]
    return cache[sid]


def extract() -> dict:
    from edge.odds import OddsStore, ScrapedOddsClient
    from edge import dfs
    sport = dfs_sport.get(SPORT_KEY)
    markets = list(sport.market_keys()) + [ATT]
    B = boards()
    con = sqlite3.connect(f"file:{ODDS}?mode=ro", uri=True)
    out = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    cache_ev, cache_scan = {}, {}
    with OddsStore(ODDS) as store:
        for gid, rows in sorted(B.items()):
            if str(gid) in out:
                continue
            date = rows[0]["date"]
            d0 = datetime.fromisoformat(date)
            teams = {r["team"] for r in rows}
            # kickoffs: from a scan on the board's date (any), then the last scan before lock
            cand = _scans(con, (d0 - timedelta(days=1)).isoformat(),
                          (d0 + timedelta(days=1, hours=12)).isoformat())
            if not cand:
                print(f"{gid} {date}: no scans"); continue
            # the earliest scan in the window: a mid-day scan has dropped games already
            # kicked off (no quotes -> no event), which moves "lock" late (9/13)
            evs = _events_for(store, cand[0][0], cache_ev)
            kos = []
            for e in evs:
                ko = e.get("commence_time") or ""
                if ko[:10] < date or ko > (d0 + timedelta(days=1, hours=12)).isoformat():
                    continue
                if {abbr(e.get("home_team")), abbr(e.get("away_team"))} & teams:
                    kos.append(ko)
            if not kos:
                print(f"{gid} {date}: no events for teams"); continue
            lock = min(kos).replace("Z", "+00:00")
            # 40 min before the first kickoff: a scan in the last minutes finds the props
            # of the about-to-start games already pulled (9/13: 52 of 160 players priced
            # 1 min out); the logged build is the last one before lock, usually earlier.
            cut = (datetime.fromisoformat(lock) - timedelta(minutes=LOCK_MARGIN_MIN)).isoformat()
            pre = [s for s in cand if s[1] < cut]
            if not pre:
                print(f"{gid} {date}: no scan before lock {lock}"); continue
            sid, fin = pre[-1]
            # ONE read per scan (get_featured_odds): the per-event client path re-reads
            # an archived scan from F: once per event (~50 s each, cold)
            if sid not in cache_scan:
                client = ScrapedOddsClient(store, PROFILE, scan_id=sid)
                cache_scan.clear()
                cache_scan[sid] = client.get_featured_odds(SPORT_KEY, markets)
            payload = cache_scan[sid]
            by_event, kick, ev_teams = {}, {}, {}
            for ev in payload:
                kick[ev["id"]] = ev.get("commence_time")
                ev_teams[ev["id"]] = {abbr(ev.get("home_team")), abbr(ev.get("away_team"))}
                for bk in ev.get("bookmakers", []):
                    if bk.get("key") != "draftkings":
                        continue
                    subj = {o["description"] for m in bk.get("markets", [])
                            for o in m.get("outcomes", []) if o.get("description")}
                    by_event[ev["id"]] = {n: (dfs.player_markets(bk, n) or {}) for n in subj}
            names = {norm(r["name"]): r for r in rows}
            got = {}
            # each player's markets from his own game's event: the earliest event at or
            # after the board date holding his team
            for eid in sorted(by_event, key=lambda e: kick.get(e) or ""):
                if (kick.get(eid) or "")[:10] < date:
                    continue
                for subj, mk in by_event[eid].items():
                    r = names.get(norm(subj))
                    if r is None or norm(subj) in got or r["team"] not in ev_teams.get(eid, ()):
                        continue
                    got[norm(subj)] = mk
            out[str(gid)] = {"date": date, "scan": sid, "scan_at": fin, "lock": lock,
                             "players": {r["name"]: got.get(norm(r["name"])) for r in rows}}
            n = sum(1 for v in out[str(gid)]["players"].values() if v)
            print(f"{gid} {date}: scan {sid} {fin[:16]} lock {lock[:16]}  {n}/{len(rows)} priced")
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(out))
    return out


# --------------------------------------------------------------------------- truth
def truth() -> dict:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = defaultdict(dict)
    for dg, pn, f in c.execute(
            "select k.draft_group, pr.player_norm, max(pr.fpts) from player_result pr "
            "join contest k using(contest_id) where k.sport='NFL' and pr.fpts is not null "
            "group by k.draft_group, pr.player_norm"):
        out[int(dg)][pn] = float(f)
    return dict(out)


# --------------------------------------------------------------------------- the rule
def _sport(variant: str):
    """'shipped' = edge/dfs_sport.py on this checkout; 'pre' = the same with no
    median->mean shift (the model before cycle 2)."""
    from dataclasses import replace
    sp = dfs_sport.get(SPORT_KEY)
    return replace(sp, mean_shift=None) if variant == "pre" else sp


def project(markets: dict, dk_pos: str, rule: dict | None, variant: str = "pre") -> dict:
    """The projection of `variant`, optionally with a back's rush TDs = a*yds + b*att
    when the attempts prop is posted (`rule`; yards-only fallback = unchanged)."""
    res = dfs_project.project(markets, _sport(variant), position=dk_pos)
    if res["proj"] is None or rule is None:
        return res
    parts = {t.strip().upper() for t in dk_pos.split("/")}
    m = res["means"]
    if not (parts & {"RB", "FB"}) or "rush TD" not in res.get("imputed", []):
        return res
    att = att_mean(markets)
    yds = m.get("player_rush_yds")
    if att is None or yds is None:
        return res
    new = rule["a"] * yds + rule["b"] * att
    old = m["player_rush_tds"]
    out = dict(res)
    out["proj"] = res["proj"] + 6.0 * (new - old)
    out["rush_td"] = (old, new)
    return out


def att_mean(markets: dict):
    md = (markets or {}).get(ATT) or {}
    pair = dfs_project._pair(md)
    if not pair or md.get("point") is None:
        return None
    return dfs_project.implied_mean(*pair, float(md["point"]), ATT_SIGMA)


# --------------------------------------------------------------------------- fit (train)
def _season_rows(season: int) -> list:
    p = GT / f"stats_player_week_{season}.csv"
    if p.exists():
        return list(csv.DictReader(p.open(newline="")))
    p = GT / f"player_week_{season}.json"
    raw = json.loads(p.read_text())
    return raw if isinstance(raw, list) else (raw.get("rows") or list(raw.values())[0])


def train_rows(seasons=TRAIN_SEASONS, floor_yds: float = 20.0) -> list:
    """(loo mean rush yds, loo mean carries, rush TDs this game, season) for RB weeks."""
    out = []
    for s in seasons:
        by = defaultdict(list)
        for r in _season_rows(s):
            if r.get("season_type") != "REG" or r.get("position") not in ("RB", "FB"):
                continue
            by[r.get("player_id")].append((_f(r.get("rushing_yards"), 0), _f(r.get("carries"), 0),
                                           _f(r.get("rushing_tds"), 0)))
        for games in by.values():
            n = len(games)
            if n < MIN_GAMES:
                continue
            ty = sum(g[0] for g in games); tc = sum(g[1] for g in games)
            for y, c, td in games:
                ly, lc = (ty - y) / (n - 1), (tc - c) / (n - 1)
                if ly >= floor_yds:
                    out.append((ly, lc, td, s))
    return out


def fit(rows=None) -> dict:
    rows = rows if rows is not None else train_rows()
    X = np.array([[r[0], r[1]] for r in rows]); y = np.array([r[2] for r in rows])
    a_only = float((X[:, 0] @ y) / (X[:, 0] @ X[:, 0]))
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return {"a": float(coef[0]), "b": float(coef[1]), "yds_only": a_only, "n": len(rows)}


def skew_fit(seasons=TRAIN_SEASONS, min_games: int = 8) -> dict:
    """dfs_sport.NFL_RB_YDS_SKEW, refitted: per RB/FB player-season with >= min_games,
    (season mean - season median) regressed on the median, over medians 15-120 rush
    yds and 3-50 rec yds (outside them the few seasons are role changes, not skew).
    Train seasons only."""
    import statistics
    by = defaultdict(lambda: {"ry": [], "rcy": []})
    for s in seasons:
        for r in _season_rows(s):
            if r.get("season_type") != "REG" or r.get("position") not in ("RB", "FB"):
                continue
            k = (s, r.get("player_id"))
            by[k]["ry"].append(_f(r.get("rushing_yards"), 0))
            by[k]["rcy"].append(_f(r.get("receiving_yards"), 0))
    out = {}
    for st, market, lo, hi in (("ry", "player_rush_yds", 15, 120),
                               ("rcy", "player_reception_yds", 3, 50)):
        xs, ys = [], []
        for v in by.values():
            g = v[st]
            if len(g) < min_games:
                continue
            med = statistics.median(g)
            if lo <= med <= hi:
                xs.append(med); ys.append(statistics.fmean(g) - med)
        b, c = np.polyfit(xs, ys, 1)
        out[market] = (round(float(c), 2), round(float(b), 4), len(xs))
    return out


# --------------------------------------------------------------------------- grade
def graded(cache: dict, rule: dict | None, pos: str = "RB", variant: str = "pre") -> list:
    T = truth()
    B = boards()
    out, missing = [], 0
    for gid_s, b in cache.items():
        gid = int(gid_s)
        tr = T.get(gid)
        if not tr:
            continue
        for r in B.get(gid, []):
            if pos not in r["dk_pos"].split("/"):
                continue
            mk = b["players"].get(r["name"])
            if not mk:
                continue
            base = project(mk, r["dk_pos"], None)
            if base["proj"] is None:
                continue
            act = tr.get(norm(r["name"]))
            if act is None:
                missing += 1
                continue
            cand = project(mk, r["dk_pos"], rule, variant)
            out.append({"gid": gid, "date": b["date"], "name": r["name"], "logged": r["proj"],
                        "base": base["proj"], "cand": cand["proj"], "actual": act,
                        "has_att": att_mean(mk) is not None})
    graded.missing = missing
    return out


def _boot(per_slate: dict, n=4000, seed=7):
    keys = sorted(per_slate)
    v = np.array([per_slate[k] for k in keys])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), (n, len(v)))
    s = v[idx].sum(1)
    return float(v.sum()), float(np.quantile(s, .05)), float(np.quantile(s, .95))


def report(pos: str, rule: dict | None, variant: str = "pre", since: str = "") -> dict:
    cache = json.loads(CACHE.read_text())
    G = [g for g in graded(cache, rule, pos, variant) if g["date"] >= since]
    by = defaultdict(list)
    for g in G:
        by[g["date"]].append(g)
    def stats(key):
        e = np.array([g["actual"] - g[key] for g in G])
        return {"n": len(e), "bias": float(e.mean()), "mae": float(np.abs(e).mean()),
                "rmse": float(np.sqrt((e ** 2).mean()))}
    res = {"pos": pos, "rule": rule, "slates": len(by), "missing_truth": graded.missing,
           "with_att": sum(g["has_att"] for g in G),
           "logged": stats("logged"), "base": stats("base"), "cand": stats("cand")}
    dsse = {d: sum((g["actual"] - g["cand"]) ** 2 - (g["actual"] - g["base"]) ** 2 for g in gs)
            for d, gs in by.items()}
    res["dSSE"] = _boot(dsse)
    res["per_slate"] = {d: {"n": len(gs),
                            "bias_base": float(np.mean([g["actual"] - g["base"] for g in gs])),
                            "bias_cand": float(np.mean([g["actual"] - g["cand"] for g in gs])),
                            "dSSE": dsse[d]} for d, gs in sorted(by.items())}
    return res


def gate(out: str, pos: str = "RB") -> int:
    """One JSON line per held-out RB on a graded board, the PRODUCTION projection
    (dfs_project.project with whatever edge/dfs_sport.py ships on this checkout) from the
    cached pre-lock markets -- so main and a branch are measured on identical inputs.
    key gid:player, cluster = slate date, split primary, state "pre-lock".
    The cache (data/harness/, gitignored) is built on first use: ~40 min cold, most of it
    reading September-October scans off the F: archive."""
    cache = extract()
    T, B = truth(), boards()
    sport = dfs_sport.get(SPORT_KEY)
    n = 0
    with open(out, "w") as fh:
        for gid_s, b in sorted(cache.items()):
            tr = T.get(int(gid_s))
            if not tr:
                continue
            for r in B.get(int(gid_s), []):
                if pos not in r["dk_pos"].split("/"):
                    continue
                mk = b["players"].get(r["name"])
                act = tr.get(norm(r["name"]))
                if not mk or act is None:
                    continue
                p = dfs_project.project(mk, sport, position=r["dk_pos"])["proj"]
                if p is None:
                    continue
                fh.write(json.dumps({"key": f"{gid_s}:{r['name']}", "cluster": b["date"],
                                     "split": "primary", "state": "pre-lock",
                                     "pred": p, "actual": act}) + "\n")
                n += 1
    print(f"{n} rows -> {out}")
    return n


def parity() -> None:
    cache = json.loads(CACHE.read_text())
    B = boards()
    for gid_s, b in sorted(cache.items()):
        d = []
        for r in B.get(int(gid_s), []):
            mk = b["players"].get(r["name"])
            if not mk:
                continue
            p = project(mk, r["dk_pos"], None)["proj"]
            if p is not None and r["proj"] is not None:
                d.append(p - r["proj"])
        d = np.array(d) if d else np.zeros(1)
        print(f"{gid_s} {b['date']} scan {b['scan_at'][:16]} lock {b['lock'][:16]}  "
              f"n {len(d):3d}  |rebuilt-logged| mean {np.abs(d).mean():.2f}  "
              f"exact(<0.05) {np.mean(np.abs(d) < .05):.0%}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("extract", "parity", "fit", "skew", "report", "gate"))
    ap.add_argument("out", nargs="?")
    ap.add_argument("--pos", default="RB")
    ap.add_argument("--cand", choices=("shipped", "att"), default="shipped",
                    help="report: base = the model before cycle 2 (no median->mean shift); "
                         "cand = 'shipped' (this checkout) or 'att' (cycle-2 attempts rule)")
    ap.add_argument("--since", default="", help="report: slates on/after this date only")
    a = ap.parse_args()
    if a.cmd == "extract":
        extract()
    elif a.cmd == "parity":
        parity()
    elif a.cmd == "fit":
        rows = train_rows()
        f = fit(rows)
        X = np.array([[r[0], r[1]] for r in rows]); y = np.array([r[2] for r in rows])
        print(json.dumps(f, indent=1))
        print("shipped rate 1/135 =", 1 / 135, " refit yards-only rate = 1 /", 1 / f["yds_only"])
        for nm, pred in (("shipped 1/135", X[:, 0] / 135), ("yds-only refit", X[:, 0] * f["yds_only"]),
                         ("yds+att", X @ np.array([f["a"], f["b"]]))):
            print(f"  {nm:15s} mean pred {pred.mean():.4f} vs actual {y.mean():.4f}  "
                  f"SSE {((y - pred) ** 2).sum():.1f}")
    elif a.cmd == "gate":
        if not a.out:
            raise SystemExit("gate OUT")
        gate(a.out, a.pos)
    elif a.cmd == "skew":
        print(json.dumps(skew_fit(), indent=1))
    elif a.cmd == "report":
        if a.cand == "att":
            print(json.dumps(report(a.pos, fit(), "pre", a.since), indent=1))
        else:
            print(json.dumps(report(a.pos, None, "shipped", a.since), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
