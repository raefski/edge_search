"""Our own NFL research tools -- injuries, matchup ratings, team pace, usage.

Adam, 2026-10-16: Fantasy Guru (Jeff Mans' shop) sells an NFL tool set --
schedule, standings, player + team stats, injuries, SMASH ratings / matchups /
WR coverage, props, a prop finder, odds and futures comparison. "Some of these
we have; if the others are useful for projections and ownership, build our own
for the NFL DFS model."

What we already had, and where:
  * props / prop finder / odds comparison -- edge/odds/ + the arb pages; the
    NFL projection IS the props (edge/dfs_project.project), so every matchup
    a book prices is already inside it.
  * schedule -- DraftKings' own draftables (edge/dfs_run_nfl.py) + nflverse.
  * player stats -- data/nfl_ground_truth/ (graders only, until now).
  * standings, futures -- no DFS use; not built.

What this file adds, all from free nflverse releases + ESPN's public injury
feed, each computed strictly from games BEFORE the week asked about so a
backtest cannot see its own answer:

  injuries()          the live report (ESPN), and injuries_week() for a past
                      week (nflverse's Friday report_status) -- what the field
                      knew at lock.
  team_table()        pace (offensive plays / game), neutral-situation pass
                      rate, pass rate over expected (nflverse xpass), points.
  matchup_table()     our SMASH: DK points a defence allows to each position
                      ABOVE what those same offences score against everyone
                      else ("points allowed over expected"), shrunk toward 0.
  usage_table()       per player: snap %, target share, carry share, red-zone
                      share, last game's and average DK points.
  vacated()           the injury -> opportunity link: how much of a team's
                      recent target+carry volume belongs to teammates ruled
                      Out/Doubtful, by position group.

WR coverage (man/zone, shadow corners) is NOT built: it needs per-play
coverage charting, which nflverse publishes only after a season ends
(pbp_participation_2026 is a 404) and FTN's free charting does not record.
Building it off 2025 coverage tendencies would be a stale guess dressed as a
matchup, so it is left out on purpose.

Whether any of this earns a place in the projection or the ownership model is
scripts/nfl_intel_backtest.py's job, held out by slate. This file only
measures.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from edge.names import norm
from edge.nfl import actual_offense_points

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "nflverse"
RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"

#: nflverse rebuilds the in-season files nightly; past seasons never change.
CURRENT_MAX_AGE_H = 12.0

#: A prior-season game counts this much against a current-season one. The skill
#: backtest found ~10%/week decay does all the work (DFS_STATUS.md, "Recency is
#: the whole gain"); 18+ weeks back at 0.9/week is ~0.15, so 0.2 is that decay
#: rounded up for the roster churn it cannot see either way. Only matters in
#: weeks 1-3, before the season has its own sample.
PRIOR_SEASON_WEIGHT = 0.2
#: Recency inside the season, per week back (same source as above).
WEEK_DECAY = 0.9
#: Shrinkage for a defence's points-allowed-over-expected, in games. A single
#: blowout swings one position by 10+ DK points; 3 phantom average games keep a
#: two-week sample from reading as a matchup.
MATCHUP_K = 3.0

POSITIONS = ("QB", "RB", "WR", "TE")
#: nflverse team code -> DraftKings'. The Rams are "LA" in nflverse, "LAR" on DK.
NFLVERSE_TO_DK = {"LA": "LAR"}


def dk_team(code: str | None) -> str:
    return NFLVERSE_TO_DK.get(code or "", code or "")
#: ESPN + nflverse statuses that mean the player will not play.
OUT_STATUSES = {"out", "doubtful", "injured reserve", "ir", "pup", "suspension",
                "physically unable to perform", "reserve/covid-19"}


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def _fetch(release: str, name: str, season: int, current: int | None = None) -> Path:
    """Path to a cached nflverse release file, downloading it when missing or,
    for the season in progress, older than CURRENT_MAX_AGE_H."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    current = current if current is not None else time.gmtime().tm_year
    stale = (season >= current and path.exists()
             and time.time() - path.stat().st_mtime > CURRENT_MAX_AGE_H * 3600)
    if path.exists() and not stale:
        return path
    url = f"{RELEASES}/{release}/{name}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "edge_search/nfl_intel"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    except Exception as exc:                                # noqa: BLE001
        if not path.exists():
            raise
        # A stale copy beats nothing, but say so -- the fallback must be visible.
        log.warning("nfl_intel: %s refresh failed (%s); using copy from %s", name, exc,
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)))
    return path


def _csv(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Per-game building blocks (cached per season)
# ---------------------------------------------------------------------------
_MEMO: dict = {}


#: A finished season's player-game table, frozen into the repo (freeze()): a
#: past season never changes, and the phone app should not download 11 MB of
#: 2025 box scores (23 s on this connection) to rate a 2026 matchup.
FROZEN = ROOT / "data" / "nfl_player_games_{season}.csv"
FROZEN_COLS = ("week", "pid", "player", "pos", "team", "opp", "dk", "targets", "carries",
               "attempts", "air_yards", "snap_pct")


def freeze(season: int) -> Path:
    """Write a finished season's player_games() to FROZEN (run once per season)."""
    rows = player_games(season)
    path = Path(str(FROZEN).format(season=season))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FROZEN_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def player_games(season: int) -> list[dict]:
    """One row per player-game: DK points and the volume behind them."""
    key = ("pg", season)
    if key in _MEMO:
        return _MEMO[key]
    frozen = Path(str(FROZEN).format(season=season))
    if frozen.exists():
        out = []
        for r in _csv(frozen):
            snap = r.pop("snap_pct")
            out.append({**r, "week": int(r["week"]), "norm": norm(r["player"]),
                        **{k: float(r[k]) for k in ("dk", "targets", "carries", "attempts",
                                                    "air_yards")},
                        "snap_pct": float(snap) if snap else None})
        _MEMO[key] = out
        return out
    rows = _csv(_fetch("stats_player", f"stats_player_week_{season}.csv", season))
    snaps = {}
    try:
        for r in _csv(_fetch("snap_counts", f"snap_counts_{season}.csv", season)):
            snaps[(norm(r["player"]), dk_team(r["team"]), int(r["week"]))] = _f(r["offense_pct"])
    except Exception as exc:                                # noqa: BLE001
        log.warning("nfl_intel: no snap counts for %s: %s", season, exc)
    out = []
    for r in rows:
        if r.get("season_type", "REG") != "REG" or r.get("position") not in POSITIONS:
            continue
        wk = int(r["week"])
        k = norm(r["player_display_name"])
        out.append({
            "week": wk, "pid": r["player_id"], "player": r["player_display_name"], "norm": k,
            "pos": r["position"], "team": dk_team(r["team"]), "opp": dk_team(r["opponent_team"]),
            "dk": actual_offense_points(r), "targets": _f(r.get("targets")),
            "carries": _f(r.get("carries")), "attempts": _f(r.get("attempts")),
            "air_yards": _f(r.get("receiving_air_yards")),
            "snap_pct": snaps.get((k, dk_team(r["team"]), wk)),
        })
    _MEMO[key] = out
    return out


def _pbp(season: int) -> list[dict]:
    """The play-by-play columns this file needs, cached as compact JSON (the
    raw 2025 file is 19 MB gzipped and takes ~20 s to parse)."""
    key = ("pbp", season)
    if key in _MEMO:
        return _MEMO[key]
    raw = _fetch("pbp", f"play_by_play_{season}.csv.gz", season)
    small = CACHE / f"pbp_small_{season}.json"
    if small.exists() and small.stat().st_mtime >= raw.stat().st_mtime:
        out = json.loads(small.read_text())
    else:
        keep = ("game_id", "week", "posteam", "defteam", "pass", "rush", "xpass", "wp",
                "down", "half_seconds_remaining", "yardline_100", "receiver_player_id",
                "rusher_player_id", "season_type", "posteam_score_post", "two_point_attempt")
        out = []
        for r in _csv(raw):
            if r.get("season_type") != "REG" or not r.get("posteam"):
                continue
            if r.get("pass") != "1" and r.get("rush") != "1":
                continue
            out.append({k: r.get(k) for k in keep})
        small.write_text(json.dumps(out))
    _MEMO[key] = out
    return out


def _redzone(season: int) -> dict:
    """{(player_id, week): red-zone targets + carries} (inside the 20)."""
    out: dict = defaultdict(float)
    try:
        plays = _pbp(season)
    except Exception as exc:                                # noqa: BLE001
        log.warning("nfl_intel: no pbp for %s: %s", season, exc)
        return out
    for p in plays:
        if _f(p["yardline_100"]) > 20 or p.get("two_point_attempt") == "1":
            continue
        for pid in (p.get("receiver_player_id"), p.get("rusher_player_id")):
            if pid and pid != "NA":
                out[(pid, int(p["week"]))] += 1
    return out


def team_games(season: int) -> list[dict]:
    """One row per team-game: plays, neutral pass rate, pass over expected."""
    key = ("tg", season)
    if key in _MEMO:
        return _MEMO[key]
    agg: dict = defaultdict(lambda: {"plays": 0, "n_neutral": 0, "pass_neutral": 0,
                                     "n_xpass": 0, "pass_oe": 0.0})
    for p in _pbp(season):
        if p.get("two_point_attempt") == "1":
            continue
        a = agg[(p["game_id"], int(p["week"]), p["posteam"], p["defteam"])]
        a["plays"] += 1
        wp, down = _f(p["wp"]), p.get("down")
        # Neutral: early down, game in doubt, not the two-minute drill -- the
        # situations where pass rate is a coach's choice rather than the score's.
        if down in ("1", "2") and 0.2 <= wp <= 0.8 and _f(p["half_seconds_remaining"]) > 120:
            a["n_neutral"] += 1
            a["pass_neutral"] += p["pass"] == "1"
        if p.get("xpass") not in (None, "", "NA"):
            a["n_xpass"] += 1
            a["pass_oe"] += (p["pass"] == "1") - _f(p["xpass"])
    out = [{"game_id": g, "week": w, "team": dk_team(t), "opp": dk_team(o), **v}
           for (g, w, t, o), v in agg.items()]
    _MEMO[key] = out
    return out


def _weights(season: int, week: int, rows: list[dict]):
    """Yield (row, weight) for every game before `week` of `season`, plus the
    prior season down-weighted -- the leak-free history a week-`week` slate had."""
    for r in rows:
        if r["_season"] == season and r["week"] < week:
            yield r, WEEK_DECAY ** (week - 1 - r["week"])
        elif r["_season"] == season - 1:
            yield r, PRIOR_SEASON_WEIGHT


def _history(fn, season: int) -> list[dict]:
    rows = []
    for s in (season - 1, season):
        try:
            rows += [dict(r, _season=s) for r in fn(s)]
        except Exception as exc:                            # noqa: BLE001
            log.warning("nfl_intel: %s(%s) unavailable: %s", fn.__name__, s, exc)
    return rows


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------
def team_table(season: int, week: int) -> dict:
    """{team: {plays_pg, neutral_pass, proe, games}} from games before `week`."""
    acc: dict = defaultdict(lambda: defaultdict(float))
    for r, w in _weights(season, week, _history(team_games, season)):
        a = acc[r["team"]]
        a["w"] += w
        a["plays"] += w * r["plays"]
        a["n_neutral"] += w * r["n_neutral"]
        a["pass_neutral"] += w * r["pass_neutral"]
        a["n_xpass"] += w * r["n_xpass"]
        a["pass_oe"] += w * r["pass_oe"]
        a["games"] += r["_season"] == season
    out = {}
    for team, a in acc.items():
        out[team] = {
            "plays_pg": round(a["plays"] / a["w"], 1),
            "neutral_pass": round(a["pass_neutral"] / a["n_neutral"], 3) if a["n_neutral"] else None,
            "proe": round(100 * a["pass_oe"] / a["n_xpass"], 1) if a["n_xpass"] else None,
            "games": int(a["games"]),
        }
    return out


def matchup_table(season: int, week: int) -> dict:
    """Our SMASH matchup: {(defence, pos): DK points allowed per game over expected}.

    Expected = what each offence the defence faced scored at that position in
    its OTHER games, so a defence is not credited for having drawn bad
    offences. Positive = soft matchup. Shrunk toward 0 by MATCHUP_K games.
    """
    pgs = _history(player_games, season)
    by_game: dict = defaultdict(float)                     # (season, wk, off, pos) -> DK pts
    for r in pgs:
        by_game[(r["_season"], r["week"], r["team"], r["opp"], r["pos"])] += r["dk"]
    games = [{"_season": s, "week": wk, "off": off, "def": d, "pos": pos, "pts": pts}
             for (s, wk, off, d, pos), pts in by_game.items()]
    hist = list(_weights(season, week, games))
    # each offence's weighted average at each position, over all its games
    off_tot: dict = defaultdict(lambda: [0.0, 0.0])
    for g, w in hist:
        t = off_tot[(g["off"], g["pos"])]
        t[0] += w * g["pts"]
        t[1] += w
    acc: dict = defaultdict(lambda: [0.0, 0.0])
    for g, w in hist:
        t = off_tot[(g["off"], g["pos"])]
        if t[1] - w <= 0:
            continue
        expected = (t[0] - w * g["pts"]) / (t[1] - w)     # leave this game out
        a = acc[(g["def"], g["pos"])]
        a[0] += w * (g["pts"] - expected)
        a[1] += w
    return {k: round(s / (n + MATCHUP_K), 2) for k, (s, n) in acc.items()}


def usage_table(season: int, week: int, recent: int = 3, redzone: bool = False) -> dict:
    """{norm name: usage} from the player's games before `week` this season
    (falling back to last season for a week-1 slate).

    Shares are of the TEAM's volume in the same games, over the player's last
    `recent` games, so a role change (a starter hurt in week 3) shows by week 5
    instead of being averaged away. `redzone` adds red-zone share, which needs
    the 5-25 MB play-by-play file -- off by default, since nothing in the
    ownership model uses it and the phone app should not wait on it.
    """
    pgs = player_games(season)
    use_season = season
    if not any(r["week"] < week for r in pgs):
        pgs, use_season, week = player_games(season - 1), season - 1, 99
    rz = _redzone(use_season) if redzone else {}
    pgs = [dict(r, rz_opps=rz.get((r["pid"], r["week"]), 0.0)) for r in pgs]
    team_vol: dict = defaultdict(lambda: defaultdict(float))
    for r in pgs:
        t = team_vol[(r["team"], r["week"])]
        t["targets"] += r["targets"]
        t["carries"] += r["carries"]
        t["air_yards"] += r["air_yards"]
        t["rz"] += r["rz_opps"]
    by_player: dict = defaultdict(list)
    for r in pgs:
        if r["week"] < week:
            by_player[r["norm"]].append(r)
    out = {}
    for k, games in by_player.items():
        games.sort(key=lambda r: r["week"])
        last = games[-recent:]

        def share(field, tfield=None):
            num = sum(g[field] for g in last)
            den = sum(team_vol[(g["team"], g["week"])][tfield or field] for g in last)
            return round(num / den, 3) if den else 0.0
        snaps = [g["snap_pct"] for g in last if g["snap_pct"] is not None]
        out[k] = {
            "team": games[-1]["team"], "pos": games[-1]["pos"], "season": use_season,
            "games": len(games), "last_week": games[-1]["week"],
            "dk_last": games[-1]["dk"], "dk_avg": round(sum(g["dk"] for g in games) / len(games), 2),
            "snap_last": games[-1]["snap_pct"],
            "snap_avg": round(sum(snaps) / len(snaps), 3) if snaps else None,
            "tgt_share": share("targets"), "carry_share": share("carries"),
            "air_share": share("air_yards"),
            "rz_share": share("rz_opps", "rz") if redzone else None,
            "opp_pg": round(sum(g["targets"] + g["carries"] for g in last) / len(last), 1),
        }
    return out


# ---------------------------------------------------------------------------
# Injuries
# ---------------------------------------------------------------------------
def injuries_week(season: int, week: int) -> dict:
    """{norm name: {team, pos, status}} from nflverse's weekly report -- the
    final (Friday) game status the field saw before a Sunday lock."""
    out = {}
    for r in _csv(_fetch("injuries", f"injuries_{season}.csv", season)):
        if int(r["week"]) != week or r.get("game_type", "REG") != "REG":
            continue
        status = (r.get("report_status") or "").strip()
        if not status:
            continue
        out[norm(r["full_name"])] = {"player": r["full_name"], "team": dk_team(r["team"]),
                                     "pos": r["position"], "status": status,
                                     "injury": r.get("report_primary_injury") or ""}
    return out


#: The report changes by the minute only around 90 min before kickoff, when a
#: rebuild is what Adam taps anyway; 5 minutes spares repeat page reruns.
ESPN_TTL_S = 300
_ESPN_MEMO: list = []


def injuries(timeout: float = 20.0) -> dict:
    """{norm name: {team, pos, status, injury, detail}} from ESPN's live report.

    ESPN's endpoint is the same JSON its injuries page renders; it needs no key
    and is not DraftKings, so the DK pacing rules do not apply.
    """
    from edge.nfl import TEAM_NAME_TO_ABBR
    now = time.time()
    if _ESPN_MEMO and now - _ESPN_MEMO[0] < ESPN_TTL_S:
        return _ESPN_MEMO[1]
    # gzip: 0.35 MB in 0.5 s instead of 8.7 MB in 5.5 s
    req = urllib.request.Request(ESPN_INJURIES, headers={"User-Agent": "Mozilla/5.0",
                                                         "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    data = json.loads(raw)
    out = {}
    for team in data.get("injuries", []):
        abbr = TEAM_NAME_TO_ABBR.get(team.get("displayName", ""), "")
        for inj in team.get("injuries", []):
            ath = inj.get("athlete", {})
            name = ath.get("displayName")
            if not name:
                continue
            out[norm(name)] = {
                "player": name, "team": dk_team(abbr or (ath.get("team") or {}).get("abbreviation", "")),
                "pos": (ath.get("position") or {}).get("abbreviation", ""),
                "status": inj.get("status", ""),
                "injury": (inj.get("details") or {}).get("type", ""),
                "detail": inj.get("shortComment", ""),
                "date": inj.get("date", ""),
            }
    _ESPN_MEMO[:] = [now, out]
    return out


def is_out(status: str | None) -> bool:
    """Will not play, for VACATED volume (Doubtful counts: ~3 in 4 sit)."""
    return (status or "").strip().lower() in OUT_STATUSES


#: Statuses definite enough to take a player off the DFS board.
RULED_OUT = OUT_STATUSES - {"doubtful"}


def is_ruled_out(status: str | None) -> bool:
    return (status or "").strip().lower() in RULED_OUT


def vacated(usage: dict, inj: dict) -> dict:
    """{team: {"pass": share, "rush": share}} of recent volume belonging to
    players ruled Out/Doubtful -- targets (pass) and carries (rush) someone
    else on the roster has to absorb this week."""
    out: dict = defaultdict(lambda: {"pass": 0.0, "rush": 0.0, "who": []})
    for k, i in inj.items():
        u = usage.get(k)
        # A quarterback's volume goes to his backup, not to the skill players.
        if not u or u["pos"] == "QB" or not is_out(i["status"]):
            continue
        v = out[u["team"]]
        v["pass"] += u["tgt_share"]
        v["rush"] += u["carry_share"]
        if u["tgt_share"] + u["carry_share"] >= 0.05:
            v["who"].append(i["player"])
    return dict(out)


def player_vacated(p: dict, vac: dict) -> float:
    """The vacated volume a player is positioned to absorb: pass volume for a
    WR/TE, carries plus a share of targets for a RB, nothing for a QB."""
    v = vac.get(p.get("team"))
    if not v:
        return 0.0
    pos = (p.get("dk_pos") or p.get("pos") or "").split("/")[0]
    if pos in ("WR", "TE"):
        return round(v["pass"], 3)
    if pos == "RB":
        return round(v["rush"] + 0.25 * v["pass"], 3)
    return 0.0


# ---------------------------------------------------------------------------
# One call for a slate
# ---------------------------------------------------------------------------
def season_week(date: str) -> tuple[int, int] | None:
    """(season, week) of the NFL week a calendar date falls in, from nflverse's
    schedule (games.csv). A Sunday main slate shares its week's Thursday game."""
    from datetime import date as _d
    path = ROOT / "data" / "nflverse_games.csv"
    try:
        target = _d.fromisoformat(date)
    except ValueError:
        return None
    best = None
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("game_type") != "REG":
                continue
            gd = _d.fromisoformat(r["gameday"])
            gap = abs((gd - target).days)
            if gap <= 3 and (best is None or gap < best[0]):
                best = (gap, int(r["season"]), int(r["week"]))
    return (best[1], best[2]) if best else None


def attach(pool: list, date: str | None, live_injuries: dict | None = None,
           research: bool = False) -> dict:
    """Annotate pool players in place with matchup / pace / usage / injury
    fields and return a summary. The fields: `matchup` (DK pts over expected
    the opponent allows this position), `team_plays`, `team_proe`, `snap`,
    `tgt_share`, `carry_share`, `rz_share`, `dk_last`, `dk_avg`,
    `inj_status`, `vacated`. `team_plays`, `team_proe` and `rz_share` only with
    research=True (play-by-play; the ownership model does not use them).

    `live_injuries` defaults to ESPN for a slate in the future and nflverse's
    report for a past one; pass {} to skip injuries.
    """
    sw = season_week(date) if date else None
    if not sw:
        return {"ok": False, "reason": f"no NFL week for {date}"}
    season, week = sw
    teams = team_table(season, week) if research else {}
    dvp = matchup_table(season, week)
    usage = usage_table(season, week, redzone=research)
    source = "none"
    if live_injuries is None:
        from datetime import date as _d
        from zoneinfo import ZoneInfo
        from datetime import datetime
        today = datetime.now(ZoneInfo("America/New_York")).date()
        if _d.fromisoformat(date) < today:
            # a finished slate: the report the field had at lock, not today's
            live_injuries, source = injuries_week(season, week), "nflverse"
        else:
            try:
                live_injuries, source = injuries(), "espn"
            except Exception as exc:                        # noqa: BLE001
                log.warning("nfl_intel: ESPN injuries unavailable: %s", exc)
                live_injuries, source = {}, "unavailable"
    elif live_injuries:
        source = "given"
    vac = vacated(usage, live_injuries)
    for p in pool:
        pos = (p.get("dk_pos") or "").split("/")[0]
        k = norm(p.get("name") or p.get("player") or "")
        u = usage.get(k, {})
        t = teams.get(p.get("team"), {})
        p["matchup"] = dvp.get((p.get("opp_team"), pos)) if pos in POSITIONS else None
        p["team_plays"] = t.get("plays_pg")
        p["team_proe"] = t.get("proe")
        for f in ("snap_avg", "tgt_share", "carry_share", "rz_share", "dk_last", "dk_avg"):
            p[f] = u.get(f)
        inj = live_injuries.get(k) or {}
        # same name on another roster is not this player
        p["inj_status"] = inj.get("status", "") if inj.get("team") in ("", p.get("team")) else ""
        p["vacated"] = player_vacated(p, vac)
    # every slate team's matchup row, whether or not its players have props yet
    pairs = {(p.get("team"), p.get("opp_team")) for p in pool if p.get("team") and p.get("opp_team")}
    matchups = {t: {"opp": o, **{pos: dvp.get((o, pos)) for pos in POSITIONS}} for t, o in pairs}
    return {"ok": True, "season": season, "week": week, "injury_source": source,
            "injuries": len(live_injuries), "matchups": matchups,
            "vacated": {t: v for t, v in vac.items() if v["who"]}}
