"""Build one DK NASCAR Classic slate end to end: board, cash lineup, GPP lineup.

The single entry point both scripts/dfs_lineups_nascar.py and
pages/6_🏁_NASCAR_DFS.py call, so a lineup on the phone and a lineup on the
desktop are the same lineup.

THE JOIN IS EXACT, WHICH NOTHING ELSE IN THIS REPO CAN SAY
DraftKings' NASCAR `playerId` IS NASCAR's own `driver_id`. Kyle Larson is 4030
in both feeds, Denny Hamlin 1361, Tyler Reddick 4065 -- verified across a full
DK board on 2026-09-24. So there is no name normalisation, no team-abbreviation
alias table, and no fuzzy matching anywhere in this module. Every other sport
here spends real effort on that problem; this one does not have it.

THE SLATE HAS TWO STATES AND THE APP HAS TO SAY WHICH ONE IT IS IN
DraftKings prices a NASCAR field days before qualifying runs. Until then
NASCAR's feed reports every entrant with starting_position = 0, and PLACE
DIFFERENTIAL DOES NOT EXIST YET -- it is start minus finish, and there is no
start. That is not a missing input to be imputed quietly; it is most of the
scoring for most of a lineup.

So:
  * BEFORE qualifying, every driver is simulated from an ESTIMATED starting
    position -- his practice rank if practice has run, otherwise his recent
    average finish -- and `provisional` is True on the result. The lineups are
    real but the place-differential half of them is a guess.
  * AFTER qualifying, starting positions are the real grid and `provisional`
    is False.

DraftKings does NOT allow late swap on NASCAR (gametype 173,
allowLateSwap=false), so there is no second look: whatever is entered at the
green flag is what runs. That makes the post-qualifying rebuild the single
most important action in the whole weekly loop, and it is why the page leads
with which state it is in.
"""
from __future__ import annotations

import collections
import csv
import logging
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from edge import dfs, dfs_nascar_theory as theory, dfs_opt_nascar, nascar, nascar_sim

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("edge.dfs_run_nascar")

DK_SPORT = nascar.DK_SPORT                    # "NAS"
CLASSIC_GAME_TYPE = nascar.CLASSIC_GAME_TYPE  # 173

#: How many of a driver's previous races feed his form. Matches the window
#: scripts/nascar_fit.py fitted the model's coefficients against; a different
#: window here would mean the coefficients are being applied to a different
#: quantity than the one they were measured on.
FORM_WINDOW = 12
MIN_FORM_RACES = 4

#: Seasons pulled to build form. Two is enough to give every full-time driver a
#: full window even in the first weeks of a season, and each is cached on disk.
FORM_SEASONS = 2


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---------------------------------------------------------------------------
# The slate, as DraftKings describes it
# ---------------------------------------------------------------------------
def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """Every NASCAR Classic draft group DK is listing, soonest first.

    The suffix carries the SERIES -- " (Cup)", " (Trucks)", " (Xfinity)" --
    rather than a slate variant the way football's does, because a NASCAR slate
    is one race by definition. So unlike the football builders there is no
    "Main" to resolve: the label is which championship it is, and picking one
    means picking a series.
    """
    groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    out = []
    for g in groups:
        if g.get("GameTypeId") != CLASSIC_GAME_TYPE:
            continue
        label = (g.get("ContestStartTimeSuffix") or "").strip().strip("()") or "Cup"
        out.append({
            "gid": g.get("DraftGroupId"),
            "label": label,
            "series": nascar.DK_SERIES.get(label, nascar.NASCAR_CUP),
            "races": g.get("GameCount") or 1,
            "start": g.get("StartDate"),
            "start_est": g.get("StartDateEst"),
            "featured": g.get("DraftGroupTag") == "Featured",
        })
    out.sort(key=lambda r: (r["start"] or "", r["label"]))
    return out


def resolve_slate(draft_group=None, groups: list[dict] | None = None):
    """(gid, meta) for the slate to build. Defaults to the soonest CUP race.

    Cup by default rather than "the biggest group" because every NASCAR group
    is one race and the Cup Series is the one with a real contest field behind
    it. A Trucks slate is a legitimate choice and has to be asked for.
    """
    rows = classic_groups(groups)
    if not rows:
        return None, {"error": "DraftKings is listing no NASCAR Classic slates."}
    if draft_group:
        match = next((r for r in rows if r["gid"] == int(draft_group)), None)
        return int(draft_group), (match or {"gid": int(draft_group),
                                            "label": "(explicit)",
                                            "series": nascar.NASCAR_CUP})
    cup = [r for r in rows if r["series"] == nascar.NASCAR_CUP]
    return (cup or rows)[0]["gid"], (cup or rows)[0]


# ---------------------------------------------------------------------------
# Matching the DK slate to NASCAR's own race
# ---------------------------------------------------------------------------
def find_race(meta: dict, season: int | None = None) -> dict | None:
    """NASCAR's own race record for a DK draft group, by series and date.

    Matched on start time rather than on the race's name: DraftKings writes
    "Hollywood Casino 400 " with a trailing space and NASCAR writes it without
    one, and sponsor names change mid-season while the date does not.
    """
    start = _parse_time(meta.get("start"))
    if start is None:
        return None
    season = season or start.year
    series = meta.get("series") or nascar.NASCAR_CUP
    best, best_gap = None, None
    for race in nascar.schedule(season, series, refresh=True):
        when = _parse_time(race.get("race_date"))
        if when is None:
            continue
        gap = abs((when - start.replace(tzinfo=timezone.utc)).total_seconds())
        if best_gap is None or gap < best_gap:
            best, best_gap = race, gap
    # Two days: NASCAR publishes race_date as local time with no zone, so the
    # gap against DraftKings' UTC start is systematically a few hours even for
    # the right race. A wrong race is a week away.
    return best if best_gap is not None and best_gap < 2 * 86400 else None


_FORM_CACHE: dict = {}


def driver_form(season: int, series: int = nascar.NASCAR_CUP,
                seasons: int = FORM_SEASONS) -> dict:
    """{driver_id: form dict} from that driver's most recent races.

    Prior races only, by construction -- this is built from completed races and
    applied to an upcoming one, so there is nothing to leak. A driver with
    fewer than MIN_FORM_RACES gets None and the simulator falls back to
    nascar_sim.DEFAULT_FORM, which is deliberately mediocre rather than
    average: an unknown part-time entry is far more likely to be a backmarker
    than a contender, and guessing average would put him in cash lineups.
    """
    ck = (season, series, seasons)
    if ck in _FORM_CACHE:
        return _FORM_CACHE[ck]
    rows: list[dict] = []
    for yr in range(season - seasons + 1, season + 1):
        try:
            rows.extend(nascar.season_rows(yr, series))
        except Exception as exc:                            # noqa: BLE001
            log.warning("nascar form: season %s unavailable (%s)", yr, exc)
    rows = [r for r in rows if r["scored"]]
    rows.sort(key=lambda r: (r["season"], r["race_date"] or "", r["race_id"]))

    by_driver: dict = collections.defaultdict(list)
    for r in rows:
        by_driver[r["driver_id"]].append(r)

    out = {}
    for did, races in by_driver.items():
        recent = races[-FORM_WINDOW:]
        if len(recent) < MIN_FORM_RACES:
            continue
        out[did] = {
            "form_finish": statistics.fmean(x["finish"] for x in recent),
            "form_led": statistics.fmean(
                x["laps_led"] / max(1, x["actual_laps"]) for x in recent),
            "form_fast": statistics.fmean(
                x["fast_laps"] / max(1, x["green_laps"]) for x in recent),
            "form_dnf": statistics.fmean(1.0 if x["dnf"] else 0.0 for x in recent),
            "form_races": len(recent),
        }
    # Memoised per process. Form only changes when a race finishes, and the
    # Streamlit page rebuilds on every interaction -- without this, picking a
    # different slate re-parses two seasons of cached JSON.
    _FORM_CACHE[ck] = out
    return out


def estimate_starts(drivers: list[dict], practice: dict,
                    practice_rank: dict) -> None:
    """Fill in a provisional starting position when qualifying has not run.

    In order of preference:
      1. the driver's PRACTICE rank, if a session has run. Practice speed is
         the signal NASCAR DFS writing leans on hardest and it is the best
         pre-qualifying read on car speed there is;
      2. his recent average finish, which is a decent stand-in for where a
         car of his quality tends to line up;
      3. the middle of the field.

    Whatever is used, the result is RANKED and renumbered 1..N so that the
    provisional grid is a real permutation. Feeding the simulator a set of
    "starting positions" with duplicates and gaps would make place differential
    incoherent across the field -- the thing it sums to zero over.
    """
    scored = []
    for d in drivers:
        did = d.get("driver_id")
        if practice_rank.get(did):
            key = (0, float(practice_rank[did]))
        elif practice.get(did):
            key = (0, -float(practice[did]))          # faster is better
        elif d.get("form_finish") is not None:
            key = (1, float(d["form_finish"]))
        else:
            key = (2, 20.0)
        scored.append((key, d))
    scored.sort(key=lambda t: t[0])
    for pos, (_key, d) in enumerate(scored, start=1):
        d["start"] = pos
        d["start_estimated"] = True


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
def build_board(gid: int, meta: dict, n_sims: int = 2000, seed: int = 0):
    """(pool, sim_matrix, info) for one DK NASCAR slate."""
    salaries = dfs.fetch_draftables(gid)
    priced = {k: v for k, v in salaries.items() if v.get("salary")}
    info = {"priced": len(priced), "listed": len(salaries), "provisional": True}
    if not priced:
        return [], None, {**info, "unpriced": True}

    race = find_race(meta)
    if race is None:
        return [], None, {**info, "error": (
            "No NASCAR race matches this DraftKings slate's start time. "
            "DraftKings may have posted a slate NASCAR has not scheduled yet.")}

    season = int(race.get("race_season") or datetime.now(timezone.utc).year)
    series = meta.get("series") or nascar.NASCAR_CUP
    rows = nascar.race_rows(race["race_id"], season, series, refresh=True)
    sess = nascar.sessions(race["race_id"], season, series, refresh=True)
    form = driver_form(season, series)

    entered = {r["driver_id"]: r for r in rows}
    qualifying = sess.get("qualifying") or {}
    info.update({
        "race": (race.get("race_name") or "").strip(),
        "track": race.get("track_name"),
        "track_type": nascar.track_type(race),
        "laps": int(race.get("scheduled_laps") or 0),
        "entered": len(entered),
        "practice": bool(sess.get("practice")),
        "qualified": bool(qualifying),
        "provisional": not qualifying,
    })

    pool = []
    for key, sal in priced.items():
        # DK's playerId IS NASCAR's driver_id -- see the module docstring.
        did = sal.get("player_id")
        row = entered.get(did)
        d = {
            "name": sal["name"], "driver_id": did, "salary": sal["salary"],
            "team": (row or {}).get("team"), "car": (row or {}).get("car"),
            "dk_fppg": sal.get("dk_fppg"),
            "entered": row is not None,
            **(form.get(did) or {}),
        }
        start = qualifying.get(did) or ((row or {}).get("start") or 0)
        d["start"] = int(start) if start else 0
        d["start_estimated"] = not start
        d["practice_rank"] = (sess.get("practice_rank") or {}).get(did)
        pool.append(d)

    # Drivers DraftKings prices but NASCAR has no entry for are dropped rather
    # than simulated: without an entry there is no grid slot, and a driver who
    # does not take the green flag scores nothing at all.
    dropped = [d["name"] for d in pool if not d["entered"]]
    pool = [d for d in pool if d["entered"]]
    info["not_entered"] = dropped

    if info["provisional"]:
        estimate_starts(pool, sess.get("practice") or {},
                        sess.get("practice_rank") or {})

    race_meta = {"track_type": info["track_type"],
                 "actual_laps": int(race.get("scheduled_laps") or 300),
                 "green_laps": nascar.green_laps(
                     {**race, "actual_laps": race.get("scheduled_laps")})}
    sim = nascar_sim.simulate(pool, race_meta, n_sims=n_sims, seed=seed)
    pool = nascar_sim.summarise(sim, pool)
    theory.add_ownership(pool)
    return pool, sim, info


# ---------------------------------------------------------------------------
# The whole slate, in one call
# ---------------------------------------------------------------------------
PROJ_LOG_COLS = ("date", "gid", "race", "track_type", "driver", "driver_id",
                 "start", "start_estimated", "salary", "proj", "sd", "floor",
                 "ceil", "own", "leverage")


def _slate_date(meta: dict) -> str | None:
    dt = _parse_time(meta.get("start"))
    if dt is None:
        return None
    try:
        return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:                                       # noqa: BLE001
        return dt.date().isoformat()


def log_forward_test(pool, cash, gpp, gid, meta, info,
                     root: Path | None = None) -> dict:
    """Persist a build, so a real contest result has something to be joined to.

    The same clock the other three sports start, and the one this build needs
    most: NOTHING in edge/dfs_nascar_theory.py's field model has ever been
    fitted, and a single NASCAR contest export is unusually informative because
    the whole field picks six drivers from about thirty-seven.

    A build for a PAST date never overwrites the log -- the same guard the MLB
    and NFL versions carry after a review rebuild clobbered real forward-test
    data once already.
    """
    root = root or ROOT
    result = {"logged": False, "n": 0, "date": None}
    date = _slate_date(meta)
    if date is None:
        return result
    result["date"] = date
    try:
        today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:                                       # noqa: BLE001
        today = datetime.now().date().isoformat()
    if date < today:
        result["skipped_past_date"] = True
        return result

    (root / "data").mkdir(parents=True, exist_ok=True)
    plog = root / "data/dfs_proj_log_nascar.csv"
    prior = list(csv.DictReader(open(plog))) if plog.exists() else []

    def _rows():
        for d in pool:
            yield {"date": date, "gid": gid, "race": info.get("race", ""),
                   "track_type": info.get("track_type", ""),
                   "driver": d["name"], "driver_id": d.get("driver_id", ""),
                   "start": d.get("start", ""),
                   "start_estimated": int(bool(d.get("start_estimated"))),
                   "salary": d.get("salary", ""), "proj": d.get("proj", ""),
                   "sd": d.get("sd", ""), "floor": d.get("floor", ""),
                   "ceil": d.get("ceil", ""), "own": d.get("own", ""),
                   "leverage": d.get("leverage", "")}

    with plog.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(PROJ_LOG_COLS))
        for r in [r for r in prior if r.get("date") != date] + list(_rows()):
            w.writerow([r.get(c, "") for c in PROJ_LOG_COLS])
    result["logged"], result["n"] = True, len(pool)

    if cash or gpp:
        lpath = root / f"data/dfs_lineups_nascar_{date}.csv"
        with lpath.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["mode", "driver", "start", "salary", "proj", "own"])
            for mode, res in (("cash", cash), ("gpp", gpp)):
                for d in (res or {}).get("lineup", []):
                    w.writerow([mode, d["name"], d.get("start"), d.get("salary"),
                                d.get("proj"), d.get("own")])
        result["lineup_file"] = str(lpath.relative_to(root))
    return result


def build_slate(draft_group=None, n_sims: int = 2000, iters: int = 400,
                seed: int = 0, own_weight: float = 0.0,
                groups: list[dict] | None = None, persist: bool = True,
                exhaustive: bool = False) -> dict:
    """Board + a CASH lineup + a GPP lineup for one DK NASCAR Classic slate.

    Takes no odds client: no sportsbook prices any of the three components
    DraftKings scores beyond the finish, so this sport reads NASCAR's own
    feeds and nothing else. That also means it costs nothing and works from any
    IP, unlike every other build here.
    """
    all_groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    gid, meta = resolve_slate(draft_group, all_groups)
    slates = classic_groups(all_groups)
    if gid is None:
        return {"error": meta.get("error"), "slates": slates}

    pool, sim, info = build_board(gid, meta, n_sims=n_sims, seed=seed)
    if info.get("unpriced"):
        return {"unpriced": True, "gid": gid, "meta": meta, "slates": slates,
                "stats": info}
    if info.get("error") or not pool:
        return {"error": info.get("error", "No drivers on this slate."),
                "gid": gid, "meta": meta, "slates": slates, "stats": info}

    opt = dfs_opt_nascar
    if exhaustive:
        cash = opt.optimize_exhaustive(pool, sim, mode="cash")
        gpp = opt.optimize_exhaustive(pool, sim, mode="gpp",
                                      own_weight=own_weight)
    else:
        cash = opt.optimize(pool, sim, mode="cash", iters=iters, seed=seed)
        gpp = opt.optimize(pool, sim, mode="gpp", iters=iters, seed=seed,
                           own_weight=own_weight)

    log_result = None
    if persist:
        try:
            log_result = log_forward_test(pool, cash, gpp, gid, meta, info)
        except Exception as exc:                            # noqa: BLE001
            log.warning("dfs_run_nascar: forward-test logging failed: %s", exc)

    return {"gid": gid, "meta": meta, "slates": slates, "pool": pool,
            "sim": sim, "stats": info, "cash": cash, "gpp": gpp,
            "log": log_result}


def lineup_rows(result: dict) -> list[dict]:
    """One lineup as flat display rows, best projection first."""
    if not result:
        return []
    return [{"driver": d["name"], "start": d.get("start", 0),
             "salary": d.get("salary", 0),
             "proj": round(float(d.get("proj") or 0), 1),
             "floor": round(float(d.get("floor") or 0), 1),
             "ceil": round(float(d.get("ceil") or 0), 1),
             "own": d.get("own", 0.0), "leverage": d.get("leverage", 0.0)}
            for d in result["lineup"]]
