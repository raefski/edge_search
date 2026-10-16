#!/usr/bin/env python3
"""Reverse-engineer covers: invent new team statistics, test them honestly.

Adam, 2026-10-12: "reverse engineer covers and beat the spreads into novel
statistics that haven't been discovered yet. Be creative."

The bet behind every statistic here: the line is built from what is VISIBLE
(scores, records, yards, last week) and some of that is luck that will not
repeat. Each stat measures a gap between what happened and what the team's
play actually earned, or a style trait the market may misprice. All are
computed from raw nflverse play-by-play, season-to-date, from games strictly
BEFORE the week being predicted, then compared home-minus-away.

    luck / regression
      pts_luck_off     points scored beyond what offensive EPA earned
      pts_luck_def     points allowed beyond what the defence's EPA allowed
      fumble_luck      fumble recoveries beyond a coin flip (all fumbles in its games)
      kick_luck        own FGs made minus expected by distance
      opp_kick_luck    opponents' FGs made minus expected (negative = lucky)
      third_luck       3rd-down conversion rate beyond early-down success
      rz_luck_off      red-zone TD rate (drives) minus league
      rz_luck_def      red-zone TD rate allowed minus league
      one_score_luck   wins in one-score games beyond half of them
      pythag_luck      win% beyond the points-based Pythagorean expectation
      record_gap       win% z minus net-EPA z: the record outruns the play
      turnover_margin  takeaways minus giveaways per game
    padding / fragility
      garbage_pad      EPA/play overall minus EPA/play outside garbage time
      explosive_dep    share of yards from 20+ yard plays
      penalty_fd       first downs by penalty gained minus allowed, per game
      leverage_epa     4th-quarter one-score EPA/play, offence minus defence
      volatility       SD of game net EPA/play
    style / decision-making
      go_rate          4th-and-short go-for-it rate in plus territory
      pass_oe          neutral-situation pass rate over expected (xpass)
      sack_diff        sack rate created minus sack rate allowed
      pace             scrimmage plays per game
    market memory
      ats_momentum     average cover margin so far this season
      last_cover       last game's cover margin
    control
      net_epa          offence minus defence EPA/play (DVOA-like: known dead, 5a)

TESTS
  Target: the home team's cover margin, (home margin - closing spread).
  Discover on 2013-2019, confirm on 2020-2022, final check on 2025.
  2023-2024 not read (pick'em holdout, already looked at). Weeks 1-3 skipped
  (too little season-to-date data).
  * per stat: correlation with cover margin in each period; a survivor
    clears Benjamini-Hochberg q < 0.10 in discovery AND keeps its sign in
    both later periods;
  * NULL: the same pipeline with cover margins shuffled within season, 200
    times -- how many "survivors" luck alone produces;
  * COMBINED: ridge regression on all stats, fitted on 2013-2019 only, then
    picking sides in 2020-22 and 2025.

    python3 scripts/nfl_discovery.py            # downloads ~220MB of pbp once
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.nfl_script_backtest import load_games  # noqa: E402

PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{}.csv.gz"
CACHE = ROOT / "data" / "cache" / "pbp"
PERIODS = {"discover 2013-19": range(2013, 2020), "confirm 2020-22": range(2020, 2023),
           "fresh 2025": range(2025, 2026)}
SEASONS = [s for yrs in PERIODS.values() for s in yrs]
FEATURES = ["pts_luck_off", "pts_luck_def", "fumble_luck", "kick_luck", "opp_kick_luck",
            "third_luck", "rz_luck_off", "rz_luck_def", "one_score_luck", "pythag_luck",
            "record_gap", "turnover_margin", "garbage_pad", "explosive_dep", "penalty_fd",
            "leverage_epa", "volatility", "go_rate", "pass_oe", "sack_diff", "pace",
            "ats_momentum", "last_cover", "net_epa"]


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ------------------------------------------------------------- aggregation

def aggregate_season(season: int) -> dict:
    """{game_id: {team: counts}} plus FG attempts, from one raw pbp file."""
    gz = CACHE / f"play_by_play_{season}.csv.gz"
    if not gz.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        print(f"  downloading {season} pbp", flush=True)
        gz.write_bytes(urllib.request.urlopen(PBP_URL.format(season), timeout=300).read())
    games: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    meta: dict = {}
    drives_seen = set()
    with gzip.open(gz, "rt") as fh:
        for p in csv.DictReader(fh):
            if p["season_type"] != "REG":
                continue
            gid, off, dfn = p["game_id"], p["posteam"], p["defteam"]
            meta[gid] = (int(p["week"]), p["home_team"], p["away_team"])
            if not off or not dfn:
                continue
            o = games[gid][off]
            # fumbles: every fumble in the game, credited to whoever recovered
            if p["fumble"] == "1":
                games[gid]["_fumbles"]["n"] += 1
                rec = p["fumble_recovery_1_team"]
                if rec:
                    games[gid][rec]["fum_rec"] += 1
            if p["field_goal_attempt"] == "1" and p["kick_distance"]:
                o.setdefault("fg", [])
                o["fg_list"] = o.get("fg_list", "") + \
                    f"{int(_f(p['kick_distance']))}:{int(p['field_goal_result'] == 'made')},"
            if p["down"] == "4" and _f(p["ydstogo"], 99) <= 2 and _f(p["yardline_100"], 100) <= 50:
                o["go_opp"] += 1
                if p["play_type"] in ("pass", "run"):
                    o["go"] += 1
            if p["play"] != "1" or p["play_type"] not in ("pass", "run"):
                if p["first_down_penalty"] == "1" and off:
                    o["pen_fd"] += 1
                continue
            epa, wp = _f(p["epa"]), _f(p["wp"], .5)
            o["plays"] += 1
            o["epa"] += epa
            o["yards"] += _f(p["yards_gained"])
            if _f(p["yards_gained"]) >= 20:
                o["explosive_yds"] += _f(p["yards_gained"])
            if p["first_down_penalty"] == "1":
                o["pen_fd"] += 1
            if p["qb_dropback"] == "1":
                o["dropbacks"] += 1
                o["sacks"] += p["sack"] == "1"
            o["ints"] += p["interception"] == "1"
            o["fum_lost"] += p["fumble_lost"] == "1"
            if not (.1 <= wp <= .9):
                o["gt_plays"] += 1
                o["gt_epa"] += epa
            down = p["down"]
            if down in ("1", "2"):
                o["early"] += 1
                o["early_succ"] += _f(p["success"])
                if .2 <= wp <= .8 and p["xpass"]:
                    o["neutral"] += 1
                    o["neutral_pass_oe"] += _f(p["pass"]) - _f(p["xpass"])
            elif down == "3":
                o["third"] += 1
                o["third_conv"] += p["third_down_converted"] == "1"
            if p["qtr"] == "4" and abs(_f(p["score_differential"], 99)) <= 8:
                o["lev_plays"] += 1
                o["lev_epa"] += epa
            dkey = (gid, p["fixed_drive"])
            if p["drive_inside20"] == "1" and dkey not in drives_seen:
                drives_seen.add(dkey)
                o["rz_drives"] += 1
                o["rz_td"] += p["fixed_drive_result"] == "Touchdown"
    out = {}
    for gid, teams in games.items():
        week, home, away = meta[gid]
        nf = teams.get("_fumbles", {}).get("n", 0)
        out[gid] = {"week": week, "home": home, "away": away,
                    "teams": {t: {**dict(v), "fumbles_in_game": nf}
                              for t, v in teams.items() if t != "_fumbles"}}
    return out


def load_aggregates() -> dict[int, dict]:
    out = {}
    for s in SEASONS:
        path = CACHE / f"team_game_{s}.json"
        if not path.exists():
            print(f"aggregating {s}", flush=True)
            path.write_text(json.dumps(aggregate_season(s)))
        out[s] = json.loads(path.read_text())
    return out


# -------------------------------------------------------------- features

def fg_curve(aggs: dict) -> dict[int, float]:
    """P(make) by 5-yard distance bucket, fitted on the DISCOVERY seasons only."""
    made, att = defaultdict(int), defaultdict(int)
    for s in PERIODS["discover 2013-19"]:
        for g in aggs[s].values():
            for t in g["teams"].values():
                for k in (t.get("fg_list") or "").split(","):
                    if k:
                        d, m = k.split(":")
                        b = min(60, int(d) // 5 * 5)
                        made[b] += int(m)
                        att[b] += 1
    return {b: made[b] / att[b] for b in att}


def build_rows(aggs: dict, sched: dict, curve: dict) -> list[dict]:
    """One row per game (week >= 4): home-minus-away stat differences and the
    home cover margin. Every stat uses only games before this week."""
    rows = []
    for s in SEASONS:
        games = aggs[s]
        weeks = sorted({g["week"] for g in games.values()})
        hist: dict = defaultdict(list)      # team -> per-game records so far
        for wk in weeks:
            this = [(gid, g) for gid, g in games.items() if g["week"] == wk]
            lg = _league(hist)
            for gid, g in this:
                key = (s, wk, g["home"])
                sc = sched.get(key)
                if wk < 4 or sc is None or lg is None:
                    continue
                fh = _team_feats(hist[g["home"]], lg, curve)
                fa = _team_feats(hist[g["away"]], lg, curve)
                if fh is None or fa is None:
                    continue
                row = {k: fh[k] - fa[k] for k in FEATURES}
                row.update(season=s, week=wk, cover=sc["margin"] - sc["spread"])
                rows.append(row)
            for gid, g in this:                   # then add this week's games
                sc = sched.get((s, wk, g["home"]))
                for side, t, o in (("home", g["home"], g["away"]), ("away", g["away"], g["home"])):
                    me = g["teams"].get(t, {})
                    opp = g["teams"].get(o, {})
                    rec = {"me": me, "opp": opp}
                    if sc:
                        m = sc["margin"] if side == "home" else -sc["margin"]
                        rec.update(pf=int(sc["home_score"] if side == "home" else sc["away_score"]),
                                   pa=int(sc["away_score"] if side == "home" else sc["home_score"]),
                                   margin=m,
                                   cover=(sc["margin"] - sc["spread"]) * (1 if side == "home" else -1))
                    hist[t].append(rec)
    return rows


def _league(hist) -> dict | None:
    recs = [r for rs in hist.values() for r in rs if "pf" in r]
    if len(recs) < 32:
        return None
    me = [r["me"] for r in recs]
    tot = lambda k: sum(m.get(k, 0) for m in me)  # noqa: E731
    return {"ppg": sum(r["pf"] for r in recs) / len(recs),
            "rz": tot("rz_td") / max(1, tot("rz_drives")),
            "third": tot("third_conv") / max(1, tot("third")),
            "early": tot("early_succ") / max(1, tot("early"))}


def _team_feats(recs, lg, curve) -> dict | None:
    recs = [r for r in recs if "pf" in r]
    n = len(recs)
    if n < 3:
        return None
    me = lambda k: sum(r["me"].get(k, 0) for r in recs)      # noqa: E731
    op = lambda k: sum(r["opp"].get(k, 0) for r in recs)     # noqa: E731
    plays, oplays = max(1, me("plays")), max(1, op("plays"))
    off_epa, def_epa = me("epa") / plays, op("epa") / oplays
    game_net = [r["me"].get("epa", 0) / max(1, r["me"].get("plays", 0))
                - r["opp"].get("epa", 0) / max(1, r["opp"].get("plays", 0)) for r in recs]
    wins = sum(1 if r["margin"] > 0 else .5 if r["margin"] == 0 else 0 for r in recs)
    pf, pa = sum(r["pf"] for r in recs), sum(r["pa"] for r in recs)
    pyth = pf ** 2.37 / max(1e-9, pf ** 2.37 + pa ** 2.37)
    close = [r for r in recs if abs(r["margin"]) <= 8]

    def kick(side):
        exp = made = 0.0
        for r in recs:
            for k in (r[side].get("fg_list") or "").split(","):
                if k:
                    d, m = k.split(":")
                    exp += curve.get(min(60, int(d) // 5 * 5), .5)
                    made += int(m)
        return (made - exp) / n

    fum_total = sum(r["me"].get("fumbles_in_game", 0) for r in recs)
    nongt = max(1, me("plays") - me("gt_plays"))
    return {
        "pts_luck_off": (pf / n - lg["ppg"]) - me("epa") / n,
        "pts_luck_def": -((pa / n - lg["ppg"]) - op("epa") / n),
        "fumble_luck": (me("fum_rec") - .5 * fum_total) / n,
        "kick_luck": kick("me"),
        "opp_kick_luck": -kick("opp"),
        "third_luck": (me("third_conv") / max(1, me("third")) - lg["third"])
        - (me("early_succ") / max(1, me("early")) - lg["early"]),
        "rz_luck_off": me("rz_td") / max(1, me("rz_drives")) - lg["rz"],
        "rz_luck_def": -(op("rz_td") / max(1, op("rz_drives")) - lg["rz"]),
        "one_score_luck": (sum(1 for r in close if r["margin"] > 0) - .5 * len(close)) / n,
        "pythag_luck": wins / n - pyth,
        "record_gap": wins / n - (.5 + 2.5 * (off_epa - def_epa)),
        "turnover_margin": ((op("ints") + op("fum_lost")) - (me("ints") + me("fum_lost"))) / n,
        "garbage_pad": off_epa - (me("epa") - me("gt_epa")) / nongt,
        "explosive_dep": me("explosive_yds") / max(1, me("yards")),
        "penalty_fd": (me("pen_fd") - op("pen_fd")) / n,
        "leverage_epa": me("lev_epa") / max(1, me("lev_plays")) - op("lev_epa") / max(1, op("lev_plays")),
        "volatility": float(np.std(game_net)),
        "go_rate": me("go") / max(1, me("go_opp")),
        "pass_oe": me("neutral_pass_oe") / max(1, me("neutral")),
        "sack_diff": op("sacks") / max(1, op("dropbacks")) - me("sacks") / max(1, me("dropbacks")),
        "pace": (me("plays") + op("plays")) / n,
        "ats_momentum": sum(r["cover"] for r in recs) / n,
        "last_cover": recs[-1]["cover"],
        "net_epa": off_epa - def_epa,
    }


# -------------------------------------------------------------- testing

def period_of(season):
    return next(p for p, yrs in PERIODS.items() if season in yrs)


def zscore_within_season(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["season"]].append(r)
    for rs in by.values():
        for k in FEATURES:
            v = np.array([r[k] for r in rs])
            mu, sd = v.mean(), v.std() or 1
            for r in rs:
                r[k] = (r[k] - mu) / sd


def corr_p(x, y):
    r = float(np.corrcoef(x, y)[0, 1])
    n = len(x)
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    p = math.erfc(abs(t) / math.sqrt(2))
    return r, p


def survivors(rows, covers) -> tuple[list, dict]:
    """Features with BH q<0.10 in discovery and the same sign later."""
    idx = {p: [i for i, r in enumerate(rows) if period_of(r["season"]) == p] for p in PERIODS}
    stats = {}
    for k in FEATURES:
        stats[k] = {}
        for p, ii in idx.items():
            x = np.array([rows[i][k] for i in ii])
            y = covers[ii]
            stats[k][p] = corr_p(x, y)
    disc = list(PERIODS)[0]
    ps = sorted((stats[k][disc][1], k) for k in FEATURES)
    m = len(ps)
    passed = set()
    for rank, (p, k) in enumerate(ps, 1):
        if p <= .10 * rank / m:
            passed = {kk for _, kk in ps[:rank]}
    surv = [k for k in passed
            if all(np.sign(stats[k][p][0]) == np.sign(stats[k][disc][0]) for p in PERIODS)]
    return surv, stats


def ats_record(pred, covers):
    w = int(((pred > 0) & (covers > 0)).sum() + ((pred < 0) & (covers < 0)).sum())
    l = int(((pred > 0) & (covers < 0)).sum() + ((pred < 0) & (covers > 0)).sum())
    return w, l


def main() -> None:
    sched = {(g["season"], g["week"], g["home_team"]): g for g in load_games()}
    aggs = load_aggregates()
    curve = fg_curve(aggs)
    rows = build_rows(aggs, sched, curve)
    zscore_within_season(rows)
    covers = np.array([r["cover"] for r in rows], float)
    surv, stats = survivors(rows, covers)

    print("=" * 100)
    print("NOVEL STATISTICS vs THE HOME COVER MARGIN (corr; + = the home team higher on the stat covers more)")
    print("=" * 100)
    n_by = {p: sum(period_of(r["season"]) == p for r in rows) for p in PERIODS}
    print(f"{'stat':<17}" + "".join(f"{p + f' n={n_by[p]}':>27}" for p in PERIODS))
    for k in sorted(FEATURES, key=lambda k: stats[k][list(PERIODS)[0]][1]):
        cells = []
        for p in PERIODS:
            r, pv = stats[k][p]
            cells.append(f"{r:+.3f} (p={pv:.3f})")
        flag = "  <-- SURVIVES" if k in surv else ""
        print(f"{k:<17}" + "".join(f"{c:>27}" for c in cells) + flag)

    # the null: how many survivors does shuffling produce?
    rng = np.random.default_rng(7)
    seasons = np.array([r["season"] for r in rows])
    null_counts = []
    for _ in range(200):
        sh = covers.copy()
        for s in np.unique(seasons):
            ii = np.where(seasons == s)[0]
            sh[ii] = rng.permutation(sh[ii])
        null_counts.append(len(survivors(rows, sh)[0]))
    nc = np.array(null_counts)
    print(f"\nSurvivors found: {len(surv)} {surv}")
    print(f"Shuffled-outcome null, 200 runs: mean {nc.mean():.2f} survivors, "
          f"P(>= {len(surv)}) = {(nc >= len(surv)).mean():.2f}")

    # combined ridge model, fitted on discovery only
    X = np.array([[r[k] for k in FEATURES] for r in rows])
    disc = np.array([period_of(r["season"]) == list(PERIODS)[0] for r in rows])
    lam = 50.0
    Xd, yd = X[disc], covers[disc]
    beta = np.linalg.solve(Xd.T @ Xd + lam * np.eye(X.shape[1]), Xd.T @ (yd - yd.mean()))
    pred = X @ beta
    print("\nCOMBINED (ridge on all stats, fitted 2013-19 only) -- take the side it favours:")
    for p in PERIODS:
        ii = np.array([period_of(r["season"]) == p for r in rows])
        w, l = ats_record(pred[ii], covers[ii])
        thr = np.quantile(np.abs(pred[ii]), 2 / 3)
        top = ii & (np.abs(pred) >= thr)
        w2, l2 = ats_record(pred[top], covers[top])
        tag = " (in-sample)" if p == list(PERIODS)[0] else ""
        print(f"  {p:<17} all {w}-{l} {w / max(1, w + l):.1%}   most confident third "
              f"{w2}-{l2} {w2 / max(1, w2 + l2):.1%}{tag}")
    print("  largest weights: " + ", ".join(
        f"{FEATURES[i]} {beta[i]:+.2f}" for i in np.argsort(-np.abs(beta))[:6]))


if __name__ == "__main__":
    main()
