"""DK NBA Classic: slate -> board -> simulated players -> lineups, one entry point.

    from edge.dfs_run_nba import build_slate
    out = build_slate()            # tonight's main slate: pool, cash, gpp, stats

The app (pages/12_🏀_NBA_DFS.py) and the CLI (scripts/dfs_lineups_nba.py) both call
build_slate, so the phone and the desktop cannot produce different lineups.

WHERE EACH PLAYER'S NUMBER COMES FROM
    minutes   edge/nba_minutes.py: his recent role, re-shared for tonight's news --
              NBA.com's starting five (Expected / Confirmed) and inactive list, and
              DraftKings' OUT flags. A bench player starting tonight is "promoted".
    rate      edge/nba_project.py: his DK points per minute over recent games,
              x the opponent's points allowed, x tonight's implied team total
              when the books have one.
    history   data/nba_player_history.json (scripts/nba_history_publish.py): the
              same file on the desktop and the cloud. Rookies use preseason games.
    no data   DK's own price: IMPUTED_PER_K points per $1,000, flagged `imputed`.

Held out on 2025-26: 7.65 DK points mean |error|, 0.765 correlation, against
7.88 / 0.743 for a last-10 average (scripts/nba_projection_backtest.py).
"""
from __future__ import annotations

import collections

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from edge import dfs_slate
from edge import dfs_forward_log
from edge import dfs, dfs_nba_theory as theory, dfs_opt_nba, nba_lineups, nba_minutes as NM
from edge import nba_project as NP
from edge.dfs_slate import parse_time as _parse_time, slate_games
from edge import nba
from edge.nba import TEAM_NAME_TO_ABBR
from edge.names import norm

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SPORT = "basketball_nba"
DK_SPORT = "NBA"
CLASSIC = 70
HISTORY = ROOT / "data" / "nba_player_history.json"
BUZZ_FILE = ROOT / "data" / "buzz_nba.csv"
#: DK points per $1,000 for a player with no history at all (rookie, no preseason).
IMPUTED_PER_K = 4.5
#: Players kept per team before an inactive list is known (largest roles, announced
#: starters first). 13 = the median game-night roster, but NBA.com's feed carries no
#: inactive list in 2026-27, so this cut now runs all day after DK's and ESPN's Out lists
#: (critique cycle 3). Live-state replay with an injury-list-like Out list (inactives with
#: role >= 10), 2024-25 fit: cut 13 / 14 / 15 / none RMSE 6.478 / 6.467 / 6.465 / 6.477;
#: 2025-26 held out 6.655 / 6.634 / 6.634 / 6.644 (scripts/nba_minutes_lab.py live).
ACTIVE_ROSTER = 15
ET = ZoneInfo("America/New_York")
#: First regular-season night. Before it, DK's slates are PRESEASON games, where
#: starters play 15-25 minutes -- the projections assume regular-season roles.
REGULAR_SEASON_START = "2026-10-20"


#: DK -> NBA.com team codes (GS -> GSW, PHO -> PHX, ...): one table, edge/nba.DK_ALIAS.
team = nba.team


def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """The lobby's Classic slates, earliest first (edge/dfs_slate.py)."""
    groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    return dfs_slate.classic_rows(groups, CLASSIC)


def resolve_slate(draft_group=None, groups: list[dict] | None = None):
    """(gid, meta): the explicit group, else the next Main slate to lock."""
    rows = classic_groups(groups)
    if draft_group:
        return dfs_slate.explicit(rows, draft_group)
    if not rows:
        return None, {"error": "DraftKings is listing no NBA Classic slates."}
    pick = rows[dfs_slate.next_main(rows)]
    return pick["gid"], pick


def load_history() -> dict:
    """data/nba_player_history.json, from GitHub when the disk copy is stale (cloud)."""
    from edge import repo_files
    found = repo_files.read(HISTORY)
    if found is None:
        return {"players": {}, "names": {}, "teams": {}, "league_pm": None}
    return found.json()


def attach_buzz(pool: list, date: str, path: Path = BUZZ_FILE) -> int:
    """Tonight's NBA DFS show mentions (scripts/buzz_nba.py: YouTube + podcasts,
    only what was readable before lock) as `buzz` on every row, for ownership
    (dfs_nba_theory.BUZZ_BETA). No collection for the date -> `buzz` stays None
    and ownership is the prior. Returns how many players the night has."""
    import io

    from edge import repo_files
    found = repo_files.read(path)
    if found is None:
        return 0
    rows = [r for r in csv.DictReader(io.StringIO(found.data)) if r.get("date") == date]
    mentions = {norm(r["player"]): int(float(r.get("mentions") or 0)) for r in rows}
    for p in pool:
        p["buzz"] = mentions.get(norm(p["name"]), 0) if rows else None
    return len(rows)


def game_lines(client, games: dict) -> dict:
    """{team: implied points} from the books' total and spread, when posted."""
    want = {frozenset((team(g["home"]), team(g["away"]))) for g in games.values()}
    out: dict = {}
    try:
        events = client.get_featured_odds(SPORT, ["spreads", "totals"], "us")
    except Exception as exc:                                # noqa: BLE001
        log.warning("nba: no game lines (%s)", exc)
        return out
    for ev in events:
        home = TEAM_NAME_TO_ABBR.get(ev.get("home_team") or "")
        away = TEAM_NAME_TO_ABBR.get(ev.get("away_team") or "")
        if not home or not away or frozenset((home, away)) not in want:
            continue
        spread = total = None
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                for o in m.get("outcomes", []):
                    if m["key"] == "totals" and o.get("name") == "Over" and total is None:
                        total = o.get("point")
                    if (m["key"] == "spreads" and spread is None
                            and TEAM_NAME_TO_ABBR.get(o.get("name") or "") == home):
                        spread = o.get("point")
        if total and spread is not None:
            out[home] = round(total / 2 - spread / 2, 1)
            out[away] = round(total / 2 + spread / 2, 1)
    return out


def _sig(name: str) -> tuple:
    """(norm(last name), first initial): the fallback join key when DK and NBA.com
    spell a first name differently (Alexandre / Alex). Suffixes dropped."""
    parts = [w for w in (name or "").replace(".", " ").split() if w.lower() not in ("jr", "sr", "ii", "iii", "iv")]
    return (norm(parts[-1]), parts[0][:1].lower()) if len(parts) >= 2 else (norm(name), "")


def tonight_status(date: str) -> dict:
    """{(norm name, team): {"starter", "confirmed", "inactive", "id"}} from NBA.com."""
    raw = nba_lineups.tonight(date)
    out = {}
    for g in nba_lineups.parse(raw):
        for p in g["players"]:
            out[(norm(p["name"]), p["team"])] = {"starter": p["starter"], "confirmed": p["confirmed"],
                                                 "inactive": p["inactive"], "id": str(p["id"]),
                                                 "sig": _sig(p["name"])}
    return out


def official_index(rows, known: set) -> tuple[dict, dict]:
    """({norm name: status}, {(last, first initial): [status]}) from the official
    injury report's rows. The initial fallback exists for a name the report and DK
    spell differently (Alex / Alexandre Sarr); the report carries no team, so a
    report name that is itself a known NBA player is never offered to it.
    2026-10-27: "Justin Champagnie" (WAS) Out matched ("champagnie", "j") and took
    Julian Champagnie (SAS, 37.8 DK pts, in 34-40% of the Night GPPs' top lineups)
    off our board -- unique on his DK team, so the 10/26 guard didn't catch it."""
    from edge import nba_injury_report
    official, official_sig = {}, {}
    for last, first, st_, _why in rows:
        full = nba_injury_report.full_name(last, first)
        official[norm(full)] = st_
        if norm(full) not in known:
            official_sig.setdefault(_sig(full), []).append(st_)
    return official, official_sig


def build_board(gid: int, meta: dict, client=None) -> tuple[list, dict]:
    """(pool, info) for one DK NBA Classic slate: every playable player projected."""
    salaries = dfs.fetch_draftables(gid)
    info = {"draftables_source": dfs.LAST_DRAFTABLES_SOURCE}
    games = slate_games(salaries)
    start = _parse_time(meta.get("start") or next(iter(games.values()), {}).get("start"))
    date = (start.astimezone(ET).date() if start else datetime.now(ET).date()).isoformat()
    hist = load_history()
    status = tonight_status(date)
    info["lineups_source"] = nba_lineups.LAST_SOURCE
    # NBA.com's feed has no inactive list in 2026-27 (critique cycle 3): ESPN's Out list
    # stands in before tip, shown on the page with when it was read
    from edge import nba_availability
    espn_rows = nba_availability.tonight_out() if date >= datetime.now(ET).date().isoformat() else []
    info["injuries_source"] = nba_availability.LAST_SOURCE if espn_rows or date >= datetime.now(ET).date().isoformat() \
        else "past slate (not read)"
    espn_out = {(norm(n), t) for n, t in espn_rows}
    espn_sig = {(_sig(n), t) for n, t in espn_rows}
    # Critique cycle 7: the NBA's official injury report (every 15 min) -- Out, plus
    # Doubtful / Questionable / Probable, two-ways included -- keyed by norm name and,
    # for a name the two spell differently, (last name, first initial) when unique.
    official, official_sig = {}, {}
    if date >= datetime.now(ET).date().isoformat():
        from edge import nba_injury_report
        got = nba_injury_report.latest()
        if got:
            known = {norm(hp.get("name") or "") for hp in hist["players"].values()}
            official, official_sig = official_index(got[1]["rows"], known)
        info["injuries_source"] = f"{nba_injury_report.LAST_SOURCE} + {info['injuries_source']}"

    def report_status(name, sig_ok=True):
        if norm(name) in official:
            return official[norm(name)]
        alt = official_sig.get(_sig(name), []) if sig_ok else []
        return alt[0] if len(alt) == 1 else None
    implied = game_lines(client, games) if client is not None else {}
    league_pm = hist.get("league_pm")
    info["preseason"] = date < REGULAR_SEASON_START
    info.update({"date": date, "games": len(games), "priced": len(salaries), "implied": implied,
                 "imputed": [], "out": [], "history_date": hist.get("generated_at")})

    # Everyone DK priced, matched to his NBA history and tonight's NBA.com status.
    # History is several seasons of NBA.com ids, so a name can belong to more
    # than one player (Maxwell Lewis: LAL, and a London preseason opponent):
    # `names` kept whichever came last. A shared name now resolves only to the
    # one on this DK team, and to nobody when neither is -- never a guess.
    # The NCAAF build lost a cash game to a name-only join on 2026-10-16.
    by_name: dict = {}
    for hp_id, hp in hist["players"].items():
        by_name.setdefault(norm(hp.get("name") or ""), []).append((hp_id, hp.get("team")))

    def history_id(key, t, name=""):
        cands = by_name.get(key, [])
        if len(cands) == 1:
            return cands[0][0]
        mine = [i for i, tm in cands if tm == t]
        if len(mine) == 1:
            return mine[0]
        # DK "Alexandre Sarr" is NBA.com's "Alex Sarr" (2026-10-21: a projected
        # starter left off the board): same team, last name and first initial,
        # and only if exactly one player fits -- never a guess.
        sig = _sig(name)
        alt = [i for i, hp in hist["players"].items() if hp.get("team") == t and _sig(hp.get("name") or "") == sig]
        return alt[0] if len(alt) == 1 else None

    def status_of(key, t, name, sig_ok=True):
        if (key, t) in status:
            return status[(key, t)]
        if not sig_ok:
            return {}
        sig = _sig(name)
        alt = [v for (k2, t2), v in status.items() if t2 == t and v.get("sig") == sig]
        return alt[0] if len(alt) == 1 else {}

    # The (last name, first initial) fallbacks below may only name a DK player
    # whose signature is unique on his DK team. 2026-10-26 OKC had Jalen
    # Williams (Out) and Jaylin Williams (active, 48.7% owned in the GPP that
    # paid 2nd $1,500): ESPN's "Jalen Williams" Out matched ("williams", "j",
    # OKC) and took Jaylin off our board too. The official-report fallback
    # had the same hole -- it only checked uniqueness on the report's side.
    dk_sigs = collections.Counter((_sig(v["name"]), team(v.get("team")))
                                  for v in salaries.values() if v.get("salary"))

    rows = []
    for sal in salaries.values():
        if not sal.get("salary"):
            continue
        t = team(sal.get("team"))
        key = norm(sal["name"])
        sig_ok = dk_sigs[(_sig(sal["name"]), t)] == 1
        st = status_of(key, t, sal["name"], sig_ok)
        pid = st.get("id") or history_id(key, t, sal["name"])
        h = hist["players"].get(pid, {}) if pid else {}
        opp = next((team(g["away"]) if team(g["home"]) == t else team(g["home"])
                    for g in games.values() if t in (team(g["home"]), team(g["away"]))), None)
        espn = (key, t) in espn_out or (sig_ok and (_sig(sal["name"]), t) in espn_sig)
        injury = report_status(sal["name"], sig_ok)
        out = bool(st.get("inactive")) or sal.get("dk_status") in ("OUT", "O") or espn or injury == "Out"
        # `start` lets the optimiser put the LATEST tips in G / F / UTIL (DK's
        # late-swap convention, edge/dfs_opt_climb.assign_slots). Missing until
        # 2026-10-20, so every NBA lineup kept projection order in the flex slots.
        rows.append({"name": sal["name"], "team": t, "opp": opp, "game": sal.get("game"),
                     "start": sal.get("start"),
                     "salary": sal["salary"], "dk_pos": sal.get("position"),
                     "elig": dfs_opt_nba.eligible(sal.get("position")),
                     "pid": pid, "nba_pos": h.get("pos"), "history": [tuple(x) for x in h.get("hist", [])],
                     "preseason": bool(h.get("preseason")), "out": out,
                     # the minutes model's inputs (edge/nba_minutes.project, NBA_MINUTES_METHOD.md)
                     "cur_n": h.get("cur_n"), "n_pre": h.get("n_pre") or 0, "pick": h.get("pick"),
                     "since_return": h.get("since_return") if h.get("team") == t else None,
                     "missed_last": bool(h.get("missed_last")) and h.get("team") == t,
                     "injury": injury,
                     "dk_status": sal.get("dk_status"), "starter": st.get("starter"),
                     "confirmed": st.get("confirmed"), "inactive_listed": bool(st.get("inactive")),
                     "window": (NM.SEASON_START_WINDOW if h.get("season_games", 0) < 5 else None)})

    # Minutes per team, from the whole team's news at once.
    by_team: dict = {}
    for r in rows:
        by_team.setdefault(r["team"], []).append(r)
    for t, ps in by_team.items():
        announced = sum(1 for r in ps if r["starter"]) == 5
        listed = any(r["inactive_listed"] for r in ps)
        if not listed:
            # Until NBA.com posts the inactive list (~30 min before tip), DK's ~20-man
            # list includes players who will not dress. A game-night roster is 13
            # active and its roles add to ~241 minutes (2025-26 median; 230 opening
            # week), so keep the 13 most-used and treat the rest as not dressing.
            # Without this the team was held to 240 over 21 roles summing to 300-360,
            # and every starter lost a quarter of his minutes.
            healthy = [r for r in ps if not r["out"]]
            # NBA.com's announced starters always dress: a rookie on preseason
            # minutes (or a name the join missed) must not be cut by his role.
            ranked = sorted(healthy, key=lambda r: (not r["starter"], -NM.role_v2(
                [(m, s) for m, s, _ in r["history"]], r["cur_n"], r["n_pre"])["base"]))
            for r in ranked[ACTIVE_ROSTER:]:
                r["not_dressing"] = True
        team_in = [{"id": r["name"], "position": r["nba_pos"],
                    "inactive": r["out"] or r.get("not_dressing", False),
                    "starter": r["starter"] if announced else None, "window": r["window"],
                    # NBA.com's Expected five is a guess (wrong in 7 of 28 team-games on
                    # 2026-10-20/21): project() blends it with no five until it's confirmed
                    "confirmed": bool(r["confirmed"]) if announced else None,
                    "history": [(m, s) for m, s, _ in r["history"]], "cur_n": r["cur_n"],
                    "n_pre": r["n_pre"], "since_return": r["since_return"], "pick": r["pick"],
                    "salary": r["salary"]} for r in ps]
        # Critique cycles 6-7: a kept player who is Doubtful / Questionable / Probable on
        # the official report, or not listed but sat his team's latest game ("stale"),
        # may not dress: he gets P(dress) x his minutes, his teammates their expected
        # minutes over his two cases (NM.project_uncertain). Not an announced starter.
        unc = {}
        for r in ps:
            if r["out"] or r.get("not_dressing") or (announced and r["starter"]):
                continue
            inj = {"Available": "Probable"}.get(r["injury"], r["injury"])
            if inj in ("Doubtful", "Questionable", "Probable"):
                unc[r["name"]] = inj
            elif r["missed_last"]:
                unc[r["name"]] = "stale"
        keeps = ({k: {x["name"] for x in [x for x in ranked if x["name"] != k][:ACTIVE_ROSTER]} for k in unc}
                 if unc and not listed else None)
        mode_ = "confirmed" if announced else "inactives"
        probs = NM.REPORT_P[mode_] if official else {
            **NM.REPORT_P[mode_], ("stale", False): NM.AVAIL_P[mode_][0], ("stale", True): NM.AVAIL_P[mode_][1]}
        mins, avail = NM.project_uncertain(team_in, unc, mode_, keeps, probs)
        for r in ps:
            if r["name"] in avail:
                r["avail"], r["avail_why"] = avail[r["name"]], unc[r["name"]]
        if unc:
            info.setdefault("uncertain", []).extend(f"{k} ({v})" for k, v in sorted(unc.items()))
        # "Usual" = the same team with no news: everyone healthy who dresses, no
        # starting five announced. Δ = proj - usual is then what tonight's scratches
        # and lineup changes did, not the 240-minute sharing every team goes through.
        calm_in = [{**x, "inactive": bool(next(r for r in ps if r["name"] == x["id"]).get("not_dressing")),
                    "starter": None} for x in team_in]
        calm = NM.recency(NM.project(calm_in), calm_in, "inactives")   # same recency term (cycle 18)
        allowed = hist["teams"].get(ps[0]["opp"]) if ps else None
        of = NP.opponent_factor(allowed[0] / allowed[1] if allowed and allowed[1] else None, league_pm)
        ef = NP.env_factor(implied.get(t))
        for r in ps:
            r["lineup"] = ("Confirmed" if r["confirmed"] else "Expected") if announced else ""
            if r["out"]:
                info["out"].append(r["name"])
                continue
            if r.get("not_dressing") and r["name"] not in mins:
                r["proj"] = 0.0
                continue
            m = mins.get(r["name"], {})
            r["minutes"] = m.get("minutes", 0.0)
            r["base_min"] = calm.get(r["name"], {}).get("minutes", m.get("base", 0.0))
            r["promoted"] = bool(m.get("promoted"))
            r["rate"] = round(NP.rate(r["history"], r["nba_pos"]), 3)
            r["recent"] = theory.recent_dk(r["history"], r["cur_n"] or 0, r["n_pre"] if r["preseason"] else 0)   # ownership (SLATE_OWN)
            r["imputed"] = []
            if r["history"]:
                r["proj"] = round(r["minutes"] * r["rate"] * of * ef, 2)
                r["usual"] = round(r["base_min"] * r["rate"] * of * ef, 2)
            else:
                r["proj"] = r["usual"] = round(r["salary"] / 1000.0 * IMPUTED_PER_K, 2)
                r["imputed"].append("no NBA history")
                info["imputed"].append(r["name"])
    # Every priced player's salary / position / tip, so a late swap can hold the
    # slot of an entered player who is OUT but already locked (late_swap below).
    info["priced_rows"] = {r["name"]: {"salary": r["salary"], "dk_pos": r["dk_pos"], "team": r["team"],
                                       "start": r["start"], "game": r["game"]} for r in rows}
    pool = [r for r in rows if not r["out"] and r.get("proj", 0) > 0 and r["elig"]]
    for r in pool:
        r["pos"] = sorted(r["elig"], key=["PG", "SG", "SF", "PF", "C"].index)[0]
        r["value"] = round(r["proj"] / (r["salary"] / 1000.0), 2)
        r["gain"] = round(r["proj"] - r["usual"], 1)
    info["pool"] = len(pool)
    return pool, info


def late_swap(pool: list, sim, entered: list[str], priced: dict, mode: str = "gpp",
              now: datetime | None = None, improve: bool = False, iters: int = 200,
              seed: int = 0, own_weight: float = 0.0) -> dict:
    """Re-optimise an ENTERED lineup after late news (DK locks each game at tip).

    `entered` = the 8 names in the DK lineup; `pool`/`sim` = a fresh build_slate
    (who is dressing and projected now); `priced` = its stats["priced_rows"].
      * an entered player whose game has started is LOCKED;
      * one who is no longer on the board (OUT, not dressing) and whose game has
        not started is REPLACED; if it has started he is STUCK -- he keeps his
        slot and salary at 0 points;
      * the rest stay unless `improve`, which re-optimises every unlocked slot
        (the lineup's own objective, so in a GPP it drifts toward chalk).
    Returns {"lineup", "swaps": [(out, in)], "locked", "stuck", "replace", "error"}."""
    import numpy as np
    now = now or datetime.now(ET)
    board = {p["name"]: p for p in pool}

    def started(name: str) -> bool:
        st = _parse_time((board.get(name) or priced.get(name) or {}).get("start"))
        return bool(st and st <= now)

    locked = [n for n in entered if n in board and started(n)]
    stuck = [n for n in entered if n not in board and started(n)]
    replace = [n for n in entered if n not in board and not started(n)]
    keep = [] if improve else [n for n in entered if n in board and not started(n)]
    pool2, sim2 = list(pool), sim
    for n in stuck:                         # his slot and salary stay, at zero points
        r = priced.get(n) or {}
        pool2.append({"name": n, "team": r.get("team"), "salary": r.get("salary") or 0,
                      "dk_pos": r.get("dk_pos"), "elig": dfs_opt_nba.eligible(r.get("dk_pos")),
                      "start": r.get("start"), "game": r.get("game"), "proj": 0.0, "floor": 0.0,
                      "ceil": 0.0, "own": 0.0, "out": True})
        sim2 = np.hstack([sim2, np.zeros((sim2.shape[0], 1))])
    res = dfs_opt_nba.optimize(pool2, sim2, mode=mode, iters=iters, seed=seed, own_weight=own_weight,
                               locked=set(locked) | set(stuck) | set(keep))
    out = {"locked": locked, "stuck": stuck, "replace": replace, "lineup": res, "swaps": [], "error": None}
    if res is None:
        out["error"] = "No legal lineup keeps the locked players (cap, positions or 2-game rule)."
        return out
    final = [p["name"] for _, p in res["slots"]]
    gone = [n for n in entered if n not in final]
    new = [n for n in final if n not in entered]
    out["swaps"] = list(zip(gone, new))
    return out


PROJ_LOG_COLS = ("date", "gid", "player", "team", "opp", "dk_pos", "salary", "minutes", "proj",
                 "sd", "floor", "ceil", "own", "promoted", "lineup", "imputed", "source", "starter", "recent")


def optimal_columns(pool: list, sim, nights: int | None = None) -> dict:
    """{name: {optimal, opt_leverage, boom, bust}} (edge/dfs_optimal.py, Stokastic's
    columns, 2026-10-27). Not part of build_slate: it is ~130 ms of MILP per night,
    so the page runs it only when the GPP view is open -- inside every build it
    helped get the Cloud app CPU-throttled (10/27). 500 nights on the desktop
    (~13 s on 8 cores), 150 single-process on Cloud's ~2 cores."""
    import copy
    import os
    from edge import dfs_optimal
    many = (os.cpu_count() or 1) >= 8
    nights = nights or (500 if many else 150)
    rows = copy.deepcopy(pool)
    dfs_optimal.annotate(rows, sim, dfs_opt_nba.SLOTS, dfs_opt_nba.SLOT_OK, dfs_opt_nba.SALARY_CAP,
                         n=nights, workers=None if many else 1)
    return {"nights": nights, "rows": {r["name"]: {k: r[k] for k in ("optimal", "opt_leverage", "boom", "bust")}
                                       for r in rows}}


def log_forward_test(pool, cash, gpp, gid, info, root: Path | None = None) -> dict:
    """Persist a build so a contest export has something to be joined to --
    edge/dfs_forward_log.py's rules (past dates never, this draft group only)."""
    date = info.get("date")
    rows = [{"date": date, "gid": gid, "player": p["name"], "team": p["team"], "opp": p.get("opp"),
             "dk_pos": p.get("dk_pos"), "salary": p["salary"], "minutes": p.get("minutes"),
             "proj": p["proj"], "sd": p.get("sd"), "floor": p.get("floor"), "ceil": p.get("ceil"),
             "own": p.get("own", ""), "promoted": int(bool(p.get("promoted"))),
             "lineup": p.get("lineup", ""), "imputed": "|".join(p.get("imputed", [])),
             "source": "live",
             # ownership inputs (SLATE_OWN, CHEAP_STARTER) so the lab replays them
             "starter": int(bool(p.get("starter"))),
             "recent": "" if p.get("recent") is None else round(p["recent"], 2)} for p in pool]
    lineups = [{"mode": mode, "slot": slot, "player": p["name"], "team": p["team"],
                "salary": p["salary"], "proj": p["proj"], "own": p.get("own")}
               for mode, res in (("cash", cash), ("gpp", gpp))
               for slot, p in (res or {}).get("slots") or []]
    return dfs_forward_log.write("nba", date, gid, PROJ_LOG_COLS, rows,
                                 ("mode", "slot", "player", "team", "salary", "proj", "own"),
                                 lineups, root=root)


def build_slate(draft_group=None, client=None, n_sims: int = 3000, iters: int = 200, seed: int = 0,
                own_weight: float = 0.0, groups: list[dict] | None = None,
                persist: bool = True) -> dict:
    """Board + a CASH lineup + a GPP lineup for one DK NBA Classic slate."""
    if client is None:
        try:
            from edge.odds.cli import scraped_client
            client = scraped_client(SPORT, "dfs")
        except Exception as exc:                            # noqa: BLE001
            log.warning("nba: no odds client (%s); projecting without game lines", exc)
            client = None
    all_groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    gid, meta = resolve_slate(draft_group, all_groups)
    slates = classic_groups(all_groups)
    if gid is None:
        return {"error": meta.get("error"), "slates": slates}
    try:
        pool, info = build_board(gid, meta, client)
    except dfs.DraftablesUnavailable as exc:
        return {"unpriced": True, "unpriced_reason": str(exc), "gid": gid, "meta": meta,
                "slates": slates}
    if not pool:
        return {"error": "No NBA players could be projected for this slate.", "gid": gid,
                "meta": meta, "slates": slates, "stats": info}
    sim = theory.simulate(pool, n_sims=n_sims, seed=seed)
    theory.summarise(sim, pool)
    try:
        info["buzz_rows"] = attach_buzz(pool, info["date"])
    except Exception as exc:                                # noqa: BLE001 -- buzz is optional
        log.warning("nba: no buzz (%s)", exc)
        info["buzz_rows"] = 0
    theory.add_ownership(pool)
    cash = dfs_opt_nba.optimize(pool, sim, mode="cash", iters=iters, seed=seed)
    gpp = dfs_opt_nba.optimize(pool, sim, mode="gpp", iters=iters, seed=seed, own_weight=own_weight)
    log_result = None
    if persist:
        try:
            log_result = log_forward_test(pool, cash, gpp, gid, info)
        except Exception as exc:                            # noqa: BLE001
            log.warning("dfs_run_nba: forward-test logging failed: %s", exc)
    return {"gid": gid, "meta": meta, "slates": slates, "pool": pool, "sim": sim,
            "stats": info, "cash": cash, "gpp": gpp, "log": log_result}
