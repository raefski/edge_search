#!/usr/bin/env python3
"""What makes a big NHL skater night that is NOT a box-score stat or a book
price? Context features, fitted on 2025-26 and tested forward on 2026-27.

    python3 scripts/nhl_context_features.py               # everything
    python3 scripts/nhl_context_features.py --section rink
    python3 scripts/nhl_context_features.py --boot 1000
    python3 scripts/nhl_context_features.py --fetch       # cache context for new box scores first

Inputs, all cached (gitignored) under data/nhl_cache/:
  box/<game>.json      edge/nhl.boxscore -- per-skater goals/assists/shots/blocks
  context/<game>.json  NHL API gamecenter right-rail + landing: referees,
                       team game stats (power plays), start time, venue,
                       venue UTC offset (fetched 2026-10-19, see NHL_STATUS 6h)

FIRST PRINCIPLES
DK skater points = 8.5 G + 5 A + 1.5 SOG + 1.3 BLK (+2 SH point, +1.5 SO goal)
+ 3 each for a hat trick, 5+ shots, 3+ blocks, 3+ points. A big night needs
(a) chances to score -- ice time, power plays, a tired or weak goalie, a
tired opponent; (b) recorded VOLUME -- shots and blocks, which a human
scorer at the home rink counts; (c) bonus thresholds, which turn small
shifts in a rate into 3-point jumps. Every feature below is one of those
channels, and none is a production stat or a price:

  rink        the home rink's scorer: blocks/shots recorded per visiting
              team vs those teams' other road games (Schuckers & Macdonald
              2014 found blocks 0.54x-1.27x by rink, shots ~ +/-4%)
  schedule    rest days, back-to-back, 3-in-4, opponent rest, road-trip
              length, first home game after a trip
  travel      km since the last game, time-zone shift, local start hour in
              the team's HOME clock (body clock), altitude for visitors
  officials   the referee crew's power-play rate (leave-one-game-out)
  goalie ctx  opponent starts its backup / its starter played yesterday
              (identity and workload, not save percentage)
  player ctx  first game back after missing 3+ team games, first games for
              a new team, facing a former team

THE BASELINE (skill is not a feature)
Each skater-game's target is minus that player's mean in his OTHER games of
the same season (leave-one-out, shrunk toward his previous-season mean with
k = 10 games) and minus the opponent's leave-one-out
"allowed" residual -- so identity, not stats, carries skill, and what is
left is context. Effects are fitted on 2025-26 and checked on 2026-27 with
the 2025-26 coefficients untouched. Every interval resamples whole GAMES
(skaters in a game share one realisation).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.nhl import skater_points  # noqa: E402

BOX = ROOT / "data" / "nhl_cache" / "box"
CTX = ROOT / "data" / "nhl_cache" / "context"

# Home arenas: (lat, lon, elevation m). From the arenas' published locations;
# elevation only matters for the four above ~600 m. Neutral-site games
# (Global Series, outdoor games) get no travel/rink value rather than a guess.
ARENA = {
    "ANA": (33.808, -117.877, 50), "BOS": (42.366, -71.062, 5), "BUF": (42.875, -78.876, 180),
    "CGY": (51.037, -114.052, 1045), "CAR": (35.803, -78.722, 130), "CHI": (41.881, -87.674, 180),
    "COL": (39.749, -105.008, 1609), "CBJ": (39.969, -83.006, 230), "DAL": (32.791, -96.810, 140),
    "DET": (42.341, -83.055, 180), "EDM": (53.547, -113.498, 668), "FLA": (26.158, -80.326, 3),
    "LAK": (34.043, -118.267, 80), "MIN": (44.945, -93.101, 240), "MTL": (45.496, -73.569, 40),
    "NSH": (36.159, -86.779, 130), "NJD": (40.733, -74.171, 10), "NYI": (40.712, -73.726, 25),
    "NYR": (40.751, -73.993, 10), "OTT": (45.297, -75.927, 90), "PHI": (39.901, -75.172, 5),
    "PIT": (40.439, -79.989, 230), "SJS": (37.333, -121.901, 25), "SEA": (47.622, -122.354, 30),
    "STL": (38.627, -90.203, 140), "TBL": (27.943, -82.452, 5), "TOR": (43.644, -79.379, 80),
    "UTA": (40.768, -111.901, 1288), "VAN": (49.278, -123.109, 10), "VGK": (36.103, -115.178, 610),
    "WSH": (38.898, -77.021, 20), "WPG": (49.893, -97.144, 230),
}
COMPONENTS = ["dk", "goals", "assists", "shots", "blocks"]
DIVISION = {t: d for d, ts in {
    "ATL": "BOS BUF DET FLA MTL OTT TBL TOR", "MET": "CAR CBJ NJD NYI NYR PHI PIT WSH",
    "CEN": "CHI COL DAL MIN NSH STL UTA WPG", "PAC": "ANA CGY EDM LAK SJS SEA VAN VGK"}.items()
    for t in ts.split()}
# Rinks an NBA team shares (2025-26): the ice spends nights under a basketball floor.
NBA_SHARED = set("BOS CHI COL DAL DET LAK NYR PHI TOR UTA WSH".split())


def fetch_context(workers: int = 4) -> int:
    """Cache NHL API right-rail (referees, coaches, team game stats) + landing
    (start time, venue, UTC offset) for every cached box score that lacks it.
    api-web.nhle.com is the NHL's own public API -- not DraftKings -- and
    took 4 parallel workers without complaint on 2026-10-19 (1,451 games)."""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    CTX.mkdir(parents=True, exist_ok=True)

    def get(u):
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def one(f):
        out = CTX / f.name
        if out.exists():
            return 0
        gid = f.stem
        try:
            rr = get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/right-rail")
            la = get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/landing")
        except Exception as e:  # noqa: BLE001 -- one bad game must not stop the rest
            print(f"context {gid}: {e}", file=sys.stderr)
            return 0
        keep = {"id": int(gid), "gameInfo": rr.get("gameInfo"), "teamGameStats": rr.get("teamGameStats"),
                "startTimeUTC": la.get("startTimeUTC"), "venue": (la.get("venue") or {}).get("default"),
                "venueUTCOffset": la.get("venueUTCOffset"),
                "venueLocation": (la.get("venueLocation") or {}).get("default"),
                "gameType": la.get("gameType"), "neutralSite": la.get("neutralSite")}
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(keep))
        tmp.replace(out)
        return 1
    with ThreadPoolExecutor(workers) as ex:
        return sum(ex.map(one, sorted(BOX.glob("*.json"))))


def km(a, b) -> float:
    (la1, lo1, _), (la2, lo2, _) = a, b
    p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return 6371 * math.acos(max(-1, min(1, c)))


def utc_hours(off: str | None) -> float | None:
    if not off:
        return None
    sign = -1 if off.startswith("-") else 1
    h, m = off.lstrip("+-").split(":")
    return sign * (int(h) + int(m) / 60)


# --------------------------------------------------------------------------
# loading: one row per game, one per team-game, one per skater-game
# --------------------------------------------------------------------------
def load_games() -> pd.DataFrame:
    rows = []
    for f in sorted(BOX.glob("*.json")):
        b = json.loads(f.read_text())
        if b.get("state") not in ("OFF", "FINAL") or str(b["id"])[4:6] != "02":
            continue                      # regular season only
        c = CTX / f.name
        ctx = json.loads(c.read_text()) if c.exists() else {}
        gi = ctx.get("gameInfo") or {}
        tgs = {s["category"]: s for s in ctx.get("teamGameStats") or []}
        pp = tgs.get("powerPlay", {})
        opp = lambda v: int(str(v).split("/")[1]) if v and "/" in str(v) else np.nan  # noqa: E731
        rows.append({
            "game": b["id"], "season": int(str(b["id"])[:4]), "date": b["date"],
            "home": b["home"], "away": b["away"], "last_period": b.get("last_period"),
            "venue": ctx.get("venue"), "neutral": bool(ctx.get("neutralSite")),
            "start_utc": ctx.get("startTimeUTC"), "venue_utc": utc_hours(ctx.get("venueUTCOffset")),
            "refs": tuple(sorted(r["fullName"]["default"] for r in gi.get("referees", []))) or None,
            "pp_home": opp(pp.get("homeValue")), "pp_away": opp(pp.get("awayValue")),
            "coach_home": ((gi.get("homeTeam") or {}).get("headCoach") or {}).get("default"),
            "coach_away": ((gi.get("awayTeam") or {}).get("headCoach") or {}).get("default"),
            "has_ctx": bool(ctx),
            "box": b,
        })
    return pd.DataFrame(rows).sort_values(["date", "game"]).reset_index(drop=True)


def team_games(g: pd.DataFrame) -> pd.DataFrame:
    """Schedule / travel / body-clock features per (game, team)."""
    tz_home = {}   # a team's home clock = the venue offset of its home games (DST-aware per date)
    rows = []
    for r in g.itertuples():
        for side, team, opp in (("home", r.home, r.away), ("away", r.away, r.home)):
            sk = [x for x in r.box["skaters"] if x["team"] == team]
            n_d = sum(x.get("pos") == "D" for x in sk)
            rows.append({"game": r.game, "season": r.season, "date": r.date, "team": team, "opp": opp,
                         "is_home": side == "home", "venue_team": r.home, "neutral": r.neutral,
                         "venue": r.venue, "start_utc": r.start_utc, "venue_utc": r.venue_utc,
                         "went_ot": r.last_period in ("OT", "SO"),
                         "coach": r.coach_home if side == "home" else r.coach_away,
                         "n_f": len(sk) - n_d, "n_d": n_d})
    t = pd.DataFrame(rows).sort_values(["team", "date", "game"]).reset_index(drop=True)
    t["d"] = pd.to_datetime(t.date)
    # home clock: median venue offset of the team's own home games in that month
    hm = t[t.is_home & ~t.neutral].assign(m=t.d.dt.to_period("M")).groupby(["team", "m"]).venue_utc.median()
    t["m"] = t.d.dt.to_period("M")
    t["home_utc"] = [hm.get((a, m), np.nan) for a, m in zip(t.team, t.m)]
    t["home_utc"] = t.groupby("team").home_utc.transform(lambda s: s.ffill().bfill())
    out = []
    for team, s in t.groupby("team", sort=False):
        s = s.copy()
        prev_d = s.d.shift(1)
        same = s.season == s.season.shift(1)
        s["rest"] = np.where(same, (s.d - prev_d).dt.days - 1, np.nan)
        s["b2b"] = (s.rest == 0).astype(float).where(same)
        s["three_in_four"] = (((s.d - s.d.shift(2)).dt.days <= 3) & (s.season == s.season.shift(2))).astype(float)
        loc = [None if n else ARENA.get(v) for v, n in zip(s.venue_team, s.neutral)]
        prev = [None] + loc[:-1]
        s["travel_km"] = [km(a, b) if a and b and sm else np.nan for a, b, sm in zip(prev, loc, same)]
        s["tz_shift"] = (s.venue_utc - s.venue_utc.shift(1)).where(same)          # + = moved east
        # local start hour on the team's HOME clock (body clock); venue clock too
        st = pd.to_datetime(s.start_utc, utc=True, errors="coerce")
        s["start_home_clock"] = (st.dt.hour + st.dt.minute / 60 + s.home_utc) % 24
        s["start_local"] = (st.dt.hour + st.dt.minute / 60 + s.venue_utc) % 24
        s["matinee"] = (s.start_local < 16).astype(float).where(st.notna())
        s["altitude_visit"] = [(ARENA.get(v, (0, 0, 0))[2] >= 1000) and not h and not n
                               for v, h, n in zip(s.venue_team, s.is_home, s.neutral)]
        # road-trip position: consecutive road games so far, and first home game back
        trip, k = [], 0
        for h in s.is_home:
            k = 0 if h else k + 1
            trip.append(k)
        s["road_trip_game"] = trip
        prev_trip = pd.Series(trip, index=s.index).shift(1).fillna(0)
        s["home_after_trip"] = (s.is_home & (prev_trip >= 3)).astype(float)
        s["game_no"] = s.groupby("season").cumcount() + 1
        s["post_break"] = (s.rest >= 5).astype(float).where(same)
        s["prev_ot"] = (s.went_ot.shift(1).fillna(False).astype(bool) & (s.rest == 0)).astype(float)
        # new head coach: first 10 games under a coach who was not behind the
        # bench in this team's previous game of the same season
        ch = s.coach.ffill()
        changed = (ch != ch.shift(1)) & ch.shift(1).notna() & same
        s["coach_id"] = changed.cumsum()
        s["new_coach"] = ((s.coach_id > 0) & (s.groupby("coach_id").cumcount() < 10)).astype(float)
        s["f11"] = (s.n_f == 11).astype(float)
        out.append(s)
    t = pd.concat(out)
    opp = t[["game", "team", "rest", "b2b", "travel_km", "tz_shift"]].rename(
        columns={"team": "opp", "rest": "opp_rest", "b2b": "opp_b2b", "travel_km": "opp_travel_km",
                 "tz_shift": "opp_tz_shift"})
    t["division_game"] = [DIVISION.get(a) == DIVISION.get(b) for a, b in zip(t.team, t.opp)]
    t["nba_rink"] = t.venue_team.isin(NBA_SHARED) & ~t.neutral
    opp2 = t[["game", "team", "f11", "n_d"]].rename(columns={"team": "opp", "f11": "opp_f11", "n_d": "opp_n_d"})
    t = t.merge(opp, on=["game", "opp"], how="left").merge(opp2, on=["game", "opp"], how="left")
    return t.drop(columns=["d", "m"])


def goalie_context(g: pd.DataFrame) -> pd.DataFrame:
    """Per (game, team): did the team start its backup (starter share of the
    team's starts in the rest of the season < 40%), and did that goalie also
    start yesterday. Identity and workload only."""
    rows = []
    for r in g.itertuples():
        for gl in r.box["goalies"]:
            if gl.get("starter"):
                rows.append({"game": r.game, "season": r.season, "date": r.date, "team": gl["team"], "goalie": gl["id"]})
    s = pd.DataFrame(rows).drop_duplicates(["game", "team"])
    n_team = s.groupby(["season", "team"]).game.transform("size")
    n_g = s.groupby(["season", "team", "goalie"]).game.transform("size")
    s["start_share_loo"] = (n_g - 1) / (n_team - 1).clip(lower=1)
    s["backup"] = (s.start_share_loo < 0.40).astype(float)
    s = s.sort_values(["goalie", "date"])
    d = pd.to_datetime(s.date)
    s["goalie_b2b"] = ((d - d.groupby(s.goalie).shift(1)).dt.days == 1).astype(float)
    return s[["game", "team", "backup", "goalie_b2b"]]


def referee_rates(g: pd.DataFrame) -> pd.Series:
    """Leave-one-game-out crew power-play rate: mean total PP opportunities in
    each referee's OTHER games of the season, averaged over the two referees,
    minus the season mean. Index: game."""
    rows = [(r.game, r.season, ref, r.pp_home + r.pp_away) for r in g.itertuples()
            if r.refs and np.isfinite(r.pp_home) and np.isfinite(r.pp_away) for ref in r.refs]
    d = pd.DataFrame(rows, columns=["game", "season", "ref", "pp"])
    tot = d.groupby(["season", "ref"]).pp.agg(["sum", "size"])
    d = d.join(tot, on=["season", "ref"])
    # shrink each referee's rate toward the season mean: k = 20 games
    mean = d.groupby("season").pp.transform("mean")
    n = d["size"] - 1
    loo = (d["sum"] - d.pp) / n.clip(lower=1)
    d["rate"] = (n * loo + 20 * mean) / (n + 20) - mean
    return d.groupby("game").rate.mean()


def skater_games(g: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in g.itertuples():
        for s in r.box["skaters"]:
            toi = s.get("toi") or "0:00"
            mm, ss = (toi.split(":") + ["0"])[:2]
            t = int(mm) + int(ss) / 60 if mm.isdigit() else 0
            if t <= 0:
                continue
            rows.append({"game": r.game, "season": r.season, "date": r.date, "pid": s["id"], "name": s["name"],
                         "team": s["team"], "pos": s.get("pos"), "goals": s["goals"], "assists": s["assists"],
                         "shots": s["shots"], "blocks": s["blocks"], "toi": t})
    p = pd.DataFrame(rows)
    p["dk"] = skater_points(p.goals.to_numpy(), p.assists.to_numpy(), p.shots.to_numpy(), p.blocks.to_numpy())
    p["is_d"] = p.pos.eq("D").astype(float)
    return p


def player_context(p: pd.DataFrame, t: pd.DataFrame) -> pd.DataFrame:
    """first game back after missing 3+ of his team's games; first 3 games for
    a new team; facing a team he played for earlier in the data."""
    p = p.sort_values(["pid", "date", "game"]).copy()      # game_no came in with the team-game merge
    prev_team = p.groupby("pid").team.shift(1)
    prev_no = p.groupby("pid").game_no.shift(1)
    same_season = p.season == p.groupby("pid").season.shift(1)
    p["returning"] = ((p.team == prev_team) & same_season & (p.game_no - prev_no >= 4)).astype(float)
    new = (p.team != prev_team) & prev_team.notna()
    p["new_team_id"] = new.groupby(p.pid).cumsum()
    p["games_with_team"] = p.groupby(["pid", "new_team_id"]).cumcount()
    p["new_team"] = ((p.new_team_id > 0) & (p.games_with_team < 3)).astype(float)
    past = defaultdict(set)
    fmr = []
    for pid, team, o in zip(p.pid, p.team, p.opp):
        fmr.append(float(o in past[pid] and o != team))
        past[pid].add(team)
    p["vs_former"] = fmr
    return p


def absences(p: pd.DataFrame, t: pd.DataFrame) -> pd.DataFrame:
    """Per (game, team): is one of the team's two leading forwards out tonight,
    is the opponent's top-minutes defenceman out. 'Leading' is fixed for the
    season from ice time (deployment, not production) among skaters who
    played 40+ games for the team; 'out' = missed this game while playing for
    the team both before and after it (an absence, not a trade)."""
    reg = p.groupby(["season", "team", "pid"]).agg(n=("game", "size"), toi=("toi", "mean"),
                                                   d=("is_d", "max"), first=("date", "min"), last=("date", "max")).reset_index()
    # rank by LAST season's ice time where the player has one (a season in
    # progress has too few games), else this season's; a regular = 40+ games
    # for the team, or 3+ this season if he was a 20-game player last season
    prev = p.groupby(["season", "pid"]).agg(prev_toi=("toi", "mean"), prev_n=("game", "size")).reset_index()
    prev["season"] += 1
    reg = reg.merge(prev, on=["season", "pid"], how="left")
    known = reg.prev_n >= 20
    reg["toi"] = np.where(known, reg.prev_toi, reg.toi)
    reg = reg[(reg.n >= 40) | (known & (reg.n >= 3))]
    top_f = reg[reg.d == 0].sort_values("toi", ascending=False).groupby(["season", "team"]).head(2)
    top_d = reg[reg.d == 1].sort_values("toi", ascending=False).groupby(["season", "team"]).head(1)
    played = set(zip(p.game, p.pid))
    rows = []
    for r in t[["game", "season", "team", "date"]].itertuples():
        def out(tab):
            k = tab[(tab.season == r.season) & (tab.team == r.team)]
            return float(any((r.game, pid) not in played and f < r.date < l
                             for pid, f, l in zip(k.pid, k["first"], k["last"])))
        rows.append({"game": r.game, "team": r.team, "star_f_out": out(top_f), "top_d_out": out(top_d)})
    a = pd.DataFrame(rows)
    return a.merge(a.rename(columns={"team": "opp", "star_f_out": "opp_star_f_out", "top_d_out": "opp_top_d_out"}),
                   on="game").query("team != opp")


# --------------------------------------------------------------------------
# targets: leave-one-out player baseline + opponent allowed
# --------------------------------------------------------------------------
def residualise(p: pd.DataFrame) -> pd.DataFrame:
    for y in COMPONENTS:
        g = p.groupby(["season", "pid"])[y]
        n = g.transform("size") - 1
        loo = (g.transform("sum") - p[y]) / n.where(n > 0)
        # shrink toward last season's mean (else the position mean), k = 10 games,
        # so early 2026-27 games have a usable baseline without peeking at the game
        prior_tab = p.groupby(["season", "pid"])[y].mean()
        prev = pd.Series([prior_tab.get((s - 1, i), np.nan) for s, i in zip(p.season, p.pid)], index=p.index)
        pos_mean = p.groupby(["season", "is_d"])[y].transform("mean")
        prior = prev.fillna(pos_mean)
        base = (n * loo.fillna(0) + 10 * prior) / (n + 10)
        r = p[y] - base
        # opponent allowed (per position group), leave this game out
        key = [p.season, p.opp, p.is_d]
        s = r.groupby(key).transform("sum")
        gs = r.groupby([p.season, p.opp, p.is_d, p.game]).transform("sum")
        c = r.groupby(key).transform("count")
        gc = r.groupby([p.season, p.opp, p.is_d, p.game]).transform("count")
        allowed = (s - gs) / (c - gc).where(c - gc > 0)
        p[f"r_{y}"] = r - allowed.fillna(0)
        p[f"base_{y}"] = base
    # a skater needs 10+ games in the season or a prior season in the data
    n_games = p.groupby(["season", "pid"]).game.transform("size")
    has_prior = p.pid.isin(p[p.season == p.season.min()].pid) & (p.season > p.season.min())
    return p[(n_games >= 10) | has_prior]


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------
def game_boot(df: pd.DataFrame, col: str, mask: pd.Series, n_boot: int, seed=0):
    """Difference in mean `col` between mask and ~mask, CI resampling games."""
    d = df.assign(m=mask.astype(bool))
    a = d.groupby(["game", "m"])[col].agg(["sum", "count"]).unstack(fill_value=0)
    S1, C1 = a[("sum", True)].to_numpy(), a[("count", True)].to_numpy()
    S0, C0 = a[("sum", False)].to_numpy(), a[("count", False)].to_numpy()
    est = S1.sum() / max(C1.sum(), 1) - S0.sum() / max(C0.sum(), 1)
    rng = np.random.default_rng(seed)
    n = len(a)
    draws = []
    for _ in range(n_boot):
        w = np.bincount(rng.integers(0, n, n), minlength=n)
        if (w * C1).sum() == 0 or (w * C0).sum() == 0:
            continue
        draws.append((w * S1).sum() / (w * C1).sum() - (w * S0).sum() / (w * C0).sum())
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return est, lo, hi, int(C1.sum())


def fmt(e, lo, hi, nd=2):
    star = " **" if lo > 0 or hi < 0 else ""
    return f"{e:+.{nd}f} [{lo:+.{nd}f}, {hi:+.{nd}f}]{star}"


FEATURES = [
    # (label, column or callable -> bool mask, who)
    ("back-to-back (team)", lambda p: p.b2b == 1),
    ("opponent on a back-to-back", lambda p: p.opp_b2b == 1),
    ("3 games in 4 nights", lambda p: p.three_in_four == 1),
    ("rest edge: rested 2+ days vs opp b2b", lambda p: (p.rest >= 2) & (p.opp_b2b == 1)),
    ("home", lambda p: p.is_home),
    ("home, first game after 3+ road games", lambda p: p.home_after_trip == 1),
    ("road trip game 4+", lambda p: p.road_trip_game >= 4),
    ("travelled 1,500+ km since last game", lambda p: p.travel_km >= 1500),
    ("moved 2+ time zones EAST", lambda p: p.tz_shift >= 2),
    ("moved 2+ time zones WEST", lambda p: p.tz_shift <= -2),
    ("body clock: starts before 5 pm home time", lambda p: p.start_home_clock < 17),
    ("body clock: starts 9 pm+ home time", lambda p: p.start_home_clock >= 21),
    ("matinee (before 4 pm local)", lambda p: p.matinee == 1),
    ("visiting at altitude (DEN/UTA/CGY)", lambda p: p.altitude_visit),
    ("opponent starts its backup goalie", lambda p: p.opp_backup == 1),
    ("opponent goalie started yesterday", lambda p: p.opp_goalie_b2b == 1),
    ("referee crew in top third for PPs", lambda p: p.ref_rate >= p.ref_rate.quantile(2 / 3)),
    ("first game back after missing 3+", lambda p: p.returning == 1),
    ("first game after 5+ days off (break / rust)", lambda p: p.post_break == 1),
    ("b2b after an OT/SO game last night", lambda p: p.prev_ot == 1),
    ("division game", lambda p: p.division_game),
    ("new head coach (first 10 games)", lambda p: p.new_coach == 1),
    ("own team dresses 11 F / 7 D -- forwards", lambda p: (p.f11 == 1) & (p.is_d == 0)),
    ("own team dresses 11 F / 7 D -- defence", lambda p: (p.f11 == 1) & (p.is_d == 1)),
    ("a top-2-minutes teammate forward is out -- forwards", lambda p: (p.star_f_out == 1) & (p.is_d == 0)),
    ("a top-2-minutes teammate forward is out -- defence", lambda p: (p.star_f_out == 1) & (p.is_d == 1)),
    ("opponent's top-minutes D is out", lambda p: p.opp_top_d_out == 1),
    ("opponent's top-2 forward is out -- defence (blocks)", lambda p: (p.opp_star_f_out == 1) & (p.is_d == 1)),
    ("game at a rink shared with an NBA team", lambda p: p.nba_rink),
    ("first 3 games with a new team", lambda p: p.new_team == 1),
    ("facing a former team", lambda p: p.vs_former == 1),
]


def build() -> pd.DataFrame:
    g = load_games()
    t = team_games(g)
    gc = goalie_context(g).rename(columns={"team": "opp", "backup": "opp_backup", "goalie_b2b": "opp_goalie_b2b"})
    p = skater_games(g)
    p = p.merge(t, on=["game", "season", "date", "team"], how="left")
    p = p.merge(gc, on=["game", "opp"], how="left")
    p["ref_rate"] = p.game.map(referee_rates(g))
    p = p.merge(absences(p, t), on=["game", "team", "opp"], how="left")
    p = player_context(p, t)
    return g, t, residualise(p)


def features_section(p: pd.DataFrame, n_boot: int) -> list[str]:
    out = ["## Context features: DK points vs. the player's own baseline (fit 2025-26, test 2026-27)\n",
           "Each cell: mean (DK points minus the player's leave-one-out mean, minus the opponent's "
           "leave-one-out allowed) when the feature is ON minus when it is OFF, 95% CI resampling games. "
           "** = interval excludes 0. 'Holds' = same sign forward, inside the 2025-26 interval.\n",
           "| feature | 2025-26 skater-games ON | 2025-26 DK pts | 2026-27 ON | 2026-27 DK pts | holds | goals | shots | blocks (2025-26) |",
           "|---|---|---|---|---|---|---|---|---|"]
    fit, fwd = p[p.season == 2025], p[p.season == 2026]
    for label, fn in FEATURES:
        m_fit, m_fwd = fn(fit).fillna(False), fn(fwd).fillna(False)
        if m_fit.sum() < 100:
            continue
        e, lo, hi, n = game_boot(fit, "r_dk", m_fit, n_boot)
        comps = [game_boot(fit, f"r_{c}", m_fit, max(200, n_boot // 3), seed=1)[:3] for c in ("goals", "shots", "blocks")]
        if m_fwd.sum() >= 30 and (~m_fwd).sum() >= 30:
            e2, lo2, hi2, n2 = game_boot(fwd, "r_dk", m_fwd, n_boot, seed=2)
            holds = "yes" if np.sign(e2) == np.sign(e) and lo <= e2 <= hi + (hi - lo) else "no"
            fwd_s = fmt(e2, lo2, hi2)
        else:
            n2, fwd_s, holds = int(m_fwd.sum()), "-", "-"
        out.append(f"| {label} | {n:,} | {fmt(e, lo, hi)} | {n2:,} | {fwd_s} | {holds} | "
                   + " | ".join(fmt(*c, nd=3) for c in comps) + " |")
    return out


def rink_section(g: pd.DataFrame, p: pd.DataFrame) -> list[str]:
    """Visiting teams only: recorded blocks / shots at each rink vs the same
    teams' rate at their other road rinks. Multiplicative, shrunk."""
    out = ["## Rink scorer effects (visiting teams only, so neither the home team's skill nor the home-scorer tilt leaks in)\n"]
    vis = p[~p.is_home & ~p.neutral].groupby(["season", "game", "team", "venue_team"])[["blocks", "shots", "goals"]].sum().reset_index()
    res = {}
    for season, s in vis.groupby("season"):
        for col in ("blocks", "shots", "goals"):
            tm = s.groupby("team")[col].agg(["sum", "size"])
            # each visiting team's road rate EXCLUDING this rink
            at = s.groupby(["team", "venue_team"])[col].agg(["sum", "size"])
            j = at.join(tm, rsuffix="_t")
            exp_rate = (j["sum_t"] - j["sum"]) / (j["size_t"] - j["size"])
            j["exp"] = exp_rate * j["size"]
            v = j.groupby("venue_team")[["sum", "exp", "size"]].sum()
            # shrink toward 1 with k = 10 visiting games
            v["mult"] = (v["sum"] + 10 * v["exp"] / v["size"]) / (v["exp"] + 10 * v["exp"] / v["size"])
            res[(season, col)] = v
            # split-half: odd vs even games inside the season
            for half in (0, 1):
                hs = s[s.game % 2 == half]
                tm2 = hs.groupby("team")[col].agg(["sum", "size"])
                at2 = hs.groupby(["team", "venue_team"])[col].agg(["sum", "size"]).join(tm2, rsuffix="_t")
                at2["exp"] = (at2["sum_t"] - at2["sum"]) / (at2["size_t"] - at2["size"]) * at2["size"]
                vv = at2.groupby("venue_team")[["sum", "exp"]].sum()
                res[(season, col, half)] = vv["sum"] / vv["exp"]
    out += ["| event | 2025-26 range (shrunk) | split-half corr 2025-26 | 2025-26 -> 2026-27 corr (venues) | sd of multiplier |",
            "|---|---|---|---|---|"]
    for col in ("blocks", "shots", "goals"):
        a = res[(2025, col)].mult
        sh = res[(2025, col, 0)].corr(res[(2025, col, 1)])
        fwd = res.get((2026, col))
        fc = a.corr(fwd["sum"] / fwd["exp"]) if fwd is not None else np.nan
        out.append(f"| {col} | {a.min():.2f} ({a.idxmin()}) - {a.max():.2f} ({a.idxmax()}) | {sh:+.2f} | "
                   f"{fc:+.2f} | {a.std():.3f} |")
    b = res[(2025, "blocks")].sort_values("mult")
    sh = res[(2025, "shots")]
    out += ["\n2025-26 multipliers (recorded / expected for visitors, shrunk k=10):\n",
            "| rink | blocks | shots | rink | blocks | shots |", "|---|---|---|---|---|---|"]
    names = list(b.index)
    half = (len(names) + 1) // 2
    for i in range(half):
        l = names[i]
        r = names[i + half] if i + half < len(names) else None
        right = f"{r} | {b.mult[r]:.2f} | {sh.mult[r]:.2f}" if r else " | | "
        out.append(f"| {l} | {b.mult[l]:.2f} | {sh.mult[l]:.2f} | {right} |")
    gm = res[(2025, "goals")]
    nba = gm.index.isin(NBA_SHARED)
    out.append(f"\nVisitors' goals at NBA-shared rinks: {gm[nba]['sum'].sum() / gm[nba]['exp'].sum():.3f}x expected "
               f"({nba.sum()} rinks) vs {gm[~nba]['sum'].sum() / gm[~nba]['exp'].sum():.3f}x at hockey-only rinks.")
    return out, res


def rink_value(p: pd.DataFrame, res: dict, n_boot: int) -> list[str]:
    """Forward test of the useful version for our model: predict each skater's
    2026-27 blocks from his 2025-26 blocks/game. (a) as our model does now (raw
    last-season rate); (b) deflate his 2025-26 rate by the rinks he played in,
    then inflate by tonight's rink. Blocks have no market anywhere (NHL_STATUS
    section 4), so this is the one channel books cannot already price."""
    m = res[(2025, "blocks")].mult
    last = p[p.season == 2025].assign(mult=lambda d: d.venue_team.map(m).where(~d.neutral, 1.0).fillna(1.0))
    # visiting skaters see the venue multiplier; the HOME scorer also records the
    # home team, so the same rink multiplier applies to both teams' skaters.
    rate = last.groupby("pid").agg(b=("blocks", "mean"), mexp=("mult", "mean"), n=("blocks", "size"))
    rate = rate[rate.n >= 20]
    f = p[(p.season == 2026) & p.pid.isin(rate.index)].copy()
    f = f.join(rate, on="pid")
    f["tonight"] = f.venue_team.map(m).where(~f.neutral, 1.0).fillna(1.0)
    f["pred_raw"] = f.b
    f["pred_rink"] = f.b / f.mexp * f.tonight
    out = ["\n### Forward test on 2026-27 skaters (blocks/game from 2025-26, n>=20 games)\n",
           "| predictor | skater-games | MAE blocks | MSE blocks | P(3+ blocks) Brier (Poisson) |", "|---|---|---|---|---|"]
    def p3(lam):
        return 1 - np.exp(-lam) * (1 + lam + lam ** 2 / 2)
    y3 = (f.blocks >= 3).astype(float)
    for col in ("pred_raw", "pred_rink"):
        mae = (f[col] - f.blocks).abs().mean()
        mse = ((f[col] - f.blocks) ** 2).mean()
        br = ((p3(f[col]) - y3) ** 2).mean()
        out.append(f"| {col} | {len(f):,} | {mae:.4f} | {mse:.4f} | {br:.4f} |")
    # paired, game-clustered difference in squared error
    f["dse"] = (f.pred_rink - f.blocks) ** 2 - (f.pred_raw - f.blocks) ** 2
    g = f.groupby("game").dse.agg(["sum", "count"])
    rng = np.random.default_rng(3)
    draws = [(lambda w: (w * g["sum"]).sum() / (w * g["count"]).sum())(np.bincount(rng.integers(0, len(g), len(g)), minlength=len(g)))
             for _ in range(n_boot)]
    out.append(f"\nSquared-error change, rink minus raw: {g['sum'].sum() / g['count'].sum():+.4f} "
               f"[{np.percentile(draws, 2.5):+.4f}, {np.percentile(draws, 97.5):+.4f}] (negative = rink helps; "
               f"{len(g)} games resampled)")
    # what the adjustment is worth in DK points: 1.3 per block + 3 x change in P(3+)
    f["dk_adj"] = 1.3 * (f.pred_rink - f.pred_raw) + 3 * (p3(f.pred_rink) - p3(f.pred_raw))
    for lab, mk in (("D", f.is_d == 1), ("F", f.is_d == 0)):
        x = f[mk].dk_adj
        out.append(f"- {lab}: rink adjustment in DK points per game, p10 / p90: {x.quantile(.1):+.2f} / {x.quantile(.9):+.2f}")
    return out


def lineup_section(p: pd.DataFrame) -> list[str]:
    """Where the minutes go when a team dresses 11 F / 7 D, by depth slot
    (slot = rank of the player's season leave-one-out ice time within the
    team's dressed skaters that night). Compared with the same slot in a
    12 F / 6 D game, so the baseline's slot drift cancels."""
    f = p[p.season == 2025].copy()
    g = f.groupby("pid").toi
    n = g.transform("size")
    f["toi_base"] = (g.transform("sum") - f.toi) / (n - 1)
    f["r_toi"] = f.toi - f.toi_base
    out = ["## 11 F / 7 D lineups: where the minutes go (2025-26, vs the same depth slot in 12/6 games)\n",
           f"{int(f[f.is_d == 1].groupby(['game', 'team']).size().eq(7).sum())} team-games with 7 D.\n",
           "| slot | games (7 D) | ice time, min | DK pts | blocks | shots |", "|---|---|---|---|---|---|"]
    for is_d, cuts, labels in ((1, [0, 2, 4, 6], ["D top pair", "D 2nd pair", "D 3rd pair"]),
                               (0, [0, 3, 6, 9], ["F top 3", "F 4-6", "F 7-9"])):
        x = f[f.is_d == is_d].copy()
        x["slot"] = pd.cut(x.groupby(["game", "team"]).toi_base.rank(ascending=False, method="first"),
                           cuts, labels=labels)
        a = x.groupby(["slot", "f11"], observed=True)[["r_toi", "r_dk", "r_blocks", "r_shots"]].mean().unstack()
        n7 = x[x.f11 == 1].groupby("slot", observed=True).size()
        for sl in labels:
            d = {c: a.loc[sl, (c, 1.0)] - a.loc[sl, (c, 0.0)] for c in ("r_toi", "r_dk", "r_blocks", "r_shots")}
            out.append(f"| {sl} | {n7.get(sl, 0)} | {d['r_toi']:+.2f} | {d['r_dk']:+.2f} | {d['r_blocks']:+.2f} | {d['r_shots']:+.2f} |")
    return out


def referee_section(g: pd.DataFrame, n_boot: int) -> list[str]:
    r = referee_rates(g)
    d = g.set_index("game").assign(rate=r, pp=lambda x: x.pp_home + x.pp_away).dropna(subset=["rate", "pp"])
    out = ["## Referee crews and power plays\n",
           "| season | games | corr(crew LOO rate, game's PP opps) | PP opps, top vs bottom third of crews |", "|---|---|---|---|"]
    for s, x in d.groupby("season"):
        q = x.rate.quantile([1 / 3, 2 / 3])
        hi_, lo_ = x[x.rate >= q.iloc[1]].pp.mean(), x[x.rate <= q.iloc[0]].pp.mean()
        out.append(f"| {s} | {len(x)} | {x.rate.corr(x.pp):+.3f} | {hi_:.2f} vs {lo_:.2f} |")
    return out


JOINT = [label for label, _ in FEATURES]


def design(p: pd.DataFrame) -> np.ndarray:
    return np.column_stack([fn(p).fillna(False).astype(float).to_numpy() for _, fn in FEATURES])


def joint_section(p: pd.DataFrame, n_boot: int) -> tuple[list[str], dict]:
    """All features at once (they overlap: a b2b is often a road trip), OLS on
    the residual, coefficients from 2025-26 only, scored on 2026-27."""
    fit, fwd = p[p.season == 2025], p[p.season == 2026]
    X, Xf = design(fit), design(fwd)
    keep = X.sum(0) >= 100
    out = ["## Joint model: every feature at once, fitted on 2025-26, scored on 2026-27\n",
           "| target | 2025-26 coef (selected) | 2026-27 skater-games | MSE baseline | MSE + context | change [95% CI, games resampled] |",
           "|---|---|---|---|---|---|"]
    coefs = {}
    for y in ("dk", "blocks", "shots", "goals"):
        yv = fit[f"r_{y}"].to_numpy()
        b = np.linalg.lstsq(np.column_stack([np.ones(len(X)), X[:, keep]]), yv, rcond=None)[0]
        coefs[y] = (keep, b)
        pred = np.column_stack([np.ones(len(Xf)), Xf[:, keep]]) @ b
        r = fwd[f"r_{y}"].to_numpy()
        d = (r - pred) ** 2 - (r - b[0]) ** 2      # vs the intercept alone
        gsum = pd.Series(d).groupby(fwd.game.to_numpy()).agg(["sum", "count"])
        rng = np.random.default_rng(5)
        draws = []
        for _ in range(n_boot):
            w = np.bincount(rng.integers(0, len(gsum), len(gsum)), minlength=len(gsum))
            draws.append((w * gsum["sum"]).sum() / (w * gsum["count"]).sum())
        lo, hi = np.percentile(draws, [2.5, 97.5])
        big = sorted(zip(np.array(JOINT)[keep], b[1:]), key=lambda z: -abs(z[1]))[:4]
        out.append(f"| {y} | " + "; ".join(f"{n} {c:+.2f}" for n, c in big) +
                   f" | {len(r):,} | {np.mean((r - b[0]) ** 2):.3f} | {np.mean((r - pred) ** 2):.3f} | "
                   f"{d.mean():+.4f} [{lo:+.4f}, {hi:+.4f}]{' **' if hi < 0 or lo > 0 else ''} |")
    return out, coefs


def projection_section(p: pd.DataFrame, coefs: dict, n_boot: int) -> list[str]:
    """Is the context already in OUR pre-lock projection (prop-based for
    goals/assists/shots, last season for blocks)? Slope of (DK points minus
    our logged projection) on the joint model's context adjustment, on the
    logged 2026-27 nights. Slope ~1 = unpriced, add it; ~0 = already priced
    (or noise). Lineup features use the box score's dressed lineup -- the
    pre-lock DailyFaceoff lineup is right ~95% of the time, but this is mildly
    hindsight."""
    from scripts.nhl_ownership_signal import box_key
    log = pd.read_csv(ROOT / "data" / "dfs_proj_log_nhl.csv")
    log = log[log.dk_pos != "G"].groupby(["date", "team", "player"], as_index=False).proj.mean()
    log["key"] = [box_key(t, n) for t, n in zip(log.team, log.player)]
    f = p[p.season == 2026].copy()
    f["key"] = [box_key(t, n) for t, n in zip(f.team, f.name)]
    m = f.merge(log[["date", "key", "proj"]], on=["date", "key"])
    keep, b = coefs["dk"]
    m["adj"] = np.column_stack([np.ones(len(m)), design(m)[:, keep]]) @ b - b[0]
    m["res"] = m.dk - m.proj
    m["res"] -= m.groupby("date").res.transform("mean")
    m["adj_c"] = m.adj - m.groupby("date").adj.transform("mean")
    days = m.date.unique()

    def slope(idx_days):
        x = m[m.date.isin(idx_days)]
        return (x.adj_c * x.res).sum() / (x.adj_c ** 2).sum()
    rng = np.random.default_rng(6)
    draws = [slope(rng.choice(days, len(days))) for _ in range(n_boot)]
    lo, hi = np.percentile(draws, [2.5, 97.5])
    out = ["## Already in our projection? (logged 2026-27 nights)\n",
           f"{len(m):,} logged skater-games on {len(days)} days matched to box scores. Slope of "
           f"(DK points - our projection) on the 2025-26 context adjustment: **{slope(days):+.2f} "
           f"[{lo:+.2f}, {hi:+.2f}]** (1 = context unpriced by our projection, 0 = priced). "
           f"Adjustment spread p10/p90: {m.adj.quantile(.1):+.2f} / {m.adj.quantile(.9):+.2f} DK pts.\n",
           "| feature | logged skater-games ON | (points - projection) ON minus OFF, per day [95% CI] | 2025-26 effect vs own baseline |",
           "|---|---|---|---|"]
    for label, fn in FEATURES:
        mk = fn(m).fillna(False)
        if mk.sum() < 40:
            continue
        per = m.assign(on=mk).groupby(["date", "on"]).res.mean().unstack().dropna()
        if len(per) < 3:
            continue
        dd = (per[True] - per[False]).to_numpy()
        dr = [dd[rng.integers(0, len(dd), len(dd))].mean() for _ in range(n_boot)]
        l2, h2 = np.percentile(dr, [2.5, 97.5])
        j = JOINT.index(label)
        eff = b[1:][int(np.cumsum(keep)[j] - 1)] if keep[j] else np.nan
        out.append(f"| {label} | {int(mk.sum()):,} | {fmt(dd.mean(), l2, h2)} ({len(dd)} days) | {eff:+.2f} |")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--section", choices=["features", "lineup", "rink", "refs", "joint"], action="append")
    ap.add_argument("--fetch", action="store_true", help="cache NHL API context for new box scores first")
    ap.add_argument("--boot", type=int, default=500)
    a = ap.parse_args()
    secs = a.section or ["features", "lineup", "rink", "refs", "joint"]
    if a.fetch:
        print(f"fetched context for {fetch_context()} games", file=sys.stderr)
    g, t, p = build()
    out = [f"# NHL context features: {p[p.season == 2025].game.nunique():,} games 2025-26 (fit), "
           f"{p[p.season == 2026].game.nunique():,} games 2026-27 (forward); {len(p):,} skater-games; "
           f"context (refs/start/venue) on {g.has_ctx.mean():.0%} of games\n"]
    if "features" in secs:
        out += features_section(p, a.boot) + [""]
    if "lineup" in secs:
        out += lineup_section(p) + [""]
    if "rink" in secs:
        rows, res = rink_section(g, p)
        out += rows + rink_value(p, res, a.boot) + [""]
    if "refs" in secs:
        out += referee_section(g, a.boot) + [""]
    if "joint" in secs:
        rows, coefs = joint_section(p, a.boot)
        out += rows + [""] + projection_section(p, coefs, a.boot)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
