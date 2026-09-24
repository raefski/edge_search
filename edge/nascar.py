"""DK NASCAR Classic scoring + free ground truth (NASCAR's own timing feeds).

WHY NASCAR IS NOT A VARIANT OF THE OTHER SPORTS IN THIS REPO
Every other DFS build here projects players from a SPORTSBOOK. MLB reads
pitcher props, the NFL reads yardage lines, college reads milestone ladders.
NASCAR cannot: three of the four things DraftKings scores -- place
differential, laps led, fastest laps -- have no betting market at any book, at
any price. A book prices who wins, who finishes top-5, and driver-vs-driver
matchups, and none of those is what a DFS lineup is paid for.

So this sport is modelled from RESULTS rather than from prices, and NASCAR
publishes the results for free with no key:

    cacher/{yr}/{series}/race_list_basic.json      schedule, caution laps, track
    cacher/{yr}/{series}/{race}/weekend-feed.json  start, finish, laps led,
                                                   status, AND the practice and
                                                   qualifying sessions
    loopstats/prod/{yr}/{series}/{race}.json       fastest laps, lead laps,
                                                   average running position,
                                                   passes, driver rating

`weekend-feed` for an UPCOMING race returns the entry list with
starting_position = 0, and fills in once qualifying runs. That is the whole
shape of the app: before qualifying there is no place differential to project,
and after it there is.

THE JOIN IS EXACT, WHICH NO OTHER SPORT HERE CAN SAY
DraftKings' NASCAR `playerId` IS NASCAR's own `driver_id` -- Kyle Larson is
4030 in both, Denny Hamlin 1361, Tyler Reddick 4065. No name normalisation, no
fuzzy matching, no alias table that rots. Verified across a full DK board on
2026-09-24.

SCORING, PINNED DOWN AND DOUBLE-VALIDATED
See FINISH_POINTS below. The short version is that the table every strategy
article prints -- "45 for the win, 42 for second, then one less per place" --
is WRONG below tenth, and wrong by up to three points on exactly the deep
finishers a place-differential play is built around.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: DK Classic, read off api.draftkings.com/lineups/v1/gametypes/173/rules.
ROSTER = {"D": 6}
SLOTS = ["D"] * 6
SALARY_CAP = 50000
#: DraftKings' lobby code for NASCAR. "NASCAR" returns every sport in the
#: lobby, which looks like it worked -- the same trap as "NCAAF" for college.
DK_SPORT = "NAS"
#: 173 is Classic. 376 is a Snake draft with no salary cap at all.
CLASSIC_GAME_TYPE = 173
#: DraftKings does NOT allow late swap on NASCAR (gametype rules,
#: allowLateSwap=false). Lock is the green flag and there is no second look --
#: which is why the qualifying-order pull matters so much more here than a
#: late inactive report does in football.
ALLOW_LATE_SWAP = False

NASCAR_CUP = 1
NASCAR_XFINITY = 2
NASCAR_TRUCKS = 3
#: DraftKings' own suffix on the draft group -> NASCAR's series id.
DK_SERIES = {"Cup": NASCAR_CUP, "Xfinity": NASCAR_XFINITY, "Trucks": NASCAR_TRUCKS}

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/140.0.0.0 Safari/537.36")}
CACHE_DIR = ROOT / "data" / "nascar_cache"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
#: DK points for a finishing position.
#:
#: NOT a flat -1 per place. Every public description of this scoring says "45
#: for the win, 42 for second, then one less point per place", and that is
#: right for the top ten and wrong after it: there is an EXTRA -1 at each
#: decade boundary. Flat overprices a 37th-place finish by 3 points and a 40th
#: by 3 -- on exactly the deep finishers a place-differential play is built
#: around, which is most of a NASCAR lineup.
#:
#:     P = 1        45
#:     P = 2..10    44 - P     42 41 40 39 38 37 36 35 34
#:     P = 11..20   43 - P     32 31 30 29 28 27 26 25 24 23
#:     P = 21..30   42 - P     21 20 19 18 17 16 15 14 13 12
#:     P = 31..40   41 - P     10  9  8  7  6  5  4  3  2  1
#:
#: VALIDATION 1: reproduces all sixteen per-position values published in a
#: third-party per-race points table, including the 30th/31st step (12 -> 10)
#: that the flat rule cannot produce.
#:
#: VALIDATION 2, and this one is against DraftKings itself: recomputing each
#: driver's 2026 season fantasy-points-per-race from raw NASCAR results and
#: comparing to DK's OWN published FPPG stat (draftStatAttributes id 653) for
#: the 36 drivers on its board --
#:     flat -1 per place : MAE 1.233, bias +0.948
#:     this table        : MAE 0.996, bias -0.423
#: and that is with only 29 of DK's ~32 scored races available.
def finish_points(position: int) -> float:
    if position <= 0:
        return 0.0
    if position == 1:
        return 45.0
    if position <= 10:
        return 44.0 - position
    if position <= 20:
        return 43.0 - position
    if position <= 30:
        return 42.0 - position
    if position <= 40:
        return 41.0 - position
    return max(0.0, 40.0 - position)


#: The other three constants, anchored by a worked example rather than assumed:
#: Joey Logano, North Wilkesboro 2026 -- started 11, finished 1, led 323 laps,
#: 100 fastest laps, published total 180.75.
#:     45 (finish) + 10 (PD) + 323*0.25 = 80.75 + 100*0.45 = 45.00  ->  180.75
POINTS_PER_POSITION_GAINED = 1.0
POINTS_PER_LAP_LED = 0.25
POINTS_PER_FASTEST_LAP = 0.45


def actual_points(start: int, finish: int, laps_led: float,
                  fast_laps: float) -> float:
    """DK NASCAR points for one driver-race.

    Place differential is start MINUS finish and is signed: a driver who starts
    5th and finishes 30th loses 25 points, which is most of a lineup slot. That
    sign is the reason a cheap deep starter and an expensive front-runner are
    not two points on one spectrum but two different bets.
    """
    return round(
        finish_points(int(finish))
        + POINTS_PER_POSITION_GAINED * (int(start) - int(finish))
        + POINTS_PER_LAP_LED * float(laps_led or 0.0)
        + POINTS_PER_FASTEST_LAP * float(fast_laps or 0.0), 2)


# ---------------------------------------------------------------------------
# Track types -- the one categorical that changes every strategy
# ---------------------------------------------------------------------------
#: Superspeedways. Pack racing, so the field runs nose-to-tail, laps led and
#: fastest laps scatter across many drivers, and a wreck can take out half the
#: field at once. Place differential dominates and dominator points barely
#: exist. `restrictor_plate` in race_list_basic marks Daytona and Talladega;
#: Atlanta has been repaved into the same style of racing without carrying the
#: flag, so it is named.
SUPERSPEEDWAYS = {"Daytona International Speedway", "Talladega Superspeedway",
                  "Atlanta Motor Speedway"}
#: Road and street courses. Attrition and skill separate the field; track
#: position matters less than at an oval because passing is possible.
ROAD_COURSES = {"Watkins Glen International", "Sonoma Raceway",
                "Circuit of The Americas", "Road America",
                "Charlotte Motor Speedway Road Course", "Indianapolis Motor Speedway",
                "Chicago Street Course", "Autódromo Hermanos Rodríguez",
                "Autodromo Hermanos Rodriguez", "Portland International Raceway"}
#: Short tracks, under a mile. Track position is everything, one car can lead
#: 300 laps, and the dominator is the whole lineup.
SHORT_TRACKS = {"Bristol Motor Speedway", "Martinsville Speedway",
                "Richmond Raceway", "Phoenix Raceway", "Bowman Gray Stadium",
                "Iowa Speedway", "New Hampshire Motor Speedway",
                "Lucas Oil Indianapolis Raceway Park", "North Wilkesboro Speedway"}


def track_type(race: dict) -> str:
    """'superspeedway' | 'road' | 'short' | 'intermediate'.

    Four buckets rather than a continuous distance because the STRATEGY is
    discrete: a superspeedway spreads laps led across the field and a short
    track concentrates it in one car, and no interpolation between them
    describes a real race. `restrictor_plate` is trusted first because it is
    NASCAR's own flag, then the name lists, then the length.
    """
    name = (race.get("track_name") or "").strip()
    if race.get("restrictor_plate") or name in SUPERSPEEDWAYS:
        return "superspeedway"
    if name in ROAD_COURSES or "Road Course" in name or "Street" in name:
        return "road"
    if name in SHORT_TRACKS:
        return "short"
    try:
        length = float(race.get("scheduled_distance") or 0) / max(
            1, int(race.get("scheduled_laps") or 1))
    except (TypeError, ValueError, ZeroDivisionError):
        length = 1.5
    if length < 1.0:
        return "short"
    return "intermediate"


def green_laps(race: dict) -> int:
    """Laps run under green -- the pool fastest-lap points are drawn from.

    NOT the race distance, and the difference is large. A fastest lap is only
    awarded on a green-flag lap, so the total number of fastest-lap points in
    a race is the green laps rather than the actual laps. Verified against the
    loop data: summing every driver's `fast_laps` comes to the actual laps
    MINUS roughly the caution laps, every race, never to the actual laps.
    """
    actual = int(race.get("actual_laps") or race.get("scheduled_laps") or 0)
    caution = int(race.get("number_of_caution_laps") or 0)
    return max(1, actual - caution)


# ---------------------------------------------------------------------------
# The feeds
# ---------------------------------------------------------------------------
def _get(url: str, cache: Path | None, refresh: bool = False):
    """Fetch and cache one NASCAR JSON feed.

    Cached aggressively because a completed race's result never changes -- the
    same invariant that lets DraftKings' draftables snapshot be exact. The
    CURRENT race's feed does change (entry list -> qualified -> final), so a
    caller reading a live weekend passes refresh=True.
    """
    if cache is not None and cache.exists() and not refresh:
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=40).read()
    data = json.loads(raw.decode("utf-8", "replace"))
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    return data


def schedule(season: int, series: int = NASCAR_CUP, refresh: bool = False) -> list[dict]:
    """Every race NASCAR lists for one season, points races and exhibitions."""
    return _get(f"https://cf.nascar.com/cacher/{season}/{series}/race_list_basic.json",
                CACHE_DIR / f"schedule_{series}_{season}.json", refresh)


def points_races(season: int, series: int = NASCAR_CUP, refresh: bool = False) -> list[dict]:
    """Points-paying races only. race_type_id 1; 2 is the Clash and the All-Star.

    Exhibitions are excluded from FITS because their rules differ (no points,
    different formats, sometimes a different tyre) while still appearing on a
    DraftKings board -- so they are a legitimate slate and an illegitimate
    training row.
    """
    return [r for r in schedule(season, series, refresh) if r.get("race_type_id") == 1]


def weekend(race_id: int, season: int, series: int = NASCAR_CUP,
            refresh: bool = False) -> dict:
    """One race weekend: the race (or its entry list) plus practice/qualifying."""
    return _get(
        f"https://cf.nascar.com/cacher/{season}/{series}/{race_id}/weekend-feed.json",
        CACHE_DIR / f"weekend_{series}_{season}_{race_id}.json", refresh)


def loopstats(race_id: int, season: int, series: int = NASCAR_CUP,
              refresh: bool = False) -> list[dict]:
    """Loop data: fastest laps, lead laps, average running position, passes.

    `fast_laps` here is the count of green-flag laps on which this driver set
    the fastest lap of the field -- the quantity DraftKings pays 0.45 for.
    Returns [] when NASCAR has not published it, which happens for the most
    recent race for a few hours and for every race before 2020.
    """
    try:
        data = _get(
            f"https://cf.nascar.com/loopstats/prod/{season}/{series}/{race_id}.json",
            CACHE_DIR / f"loop_{series}_{season}_{race_id}.json", refresh)
    except Exception:                                       # noqa: BLE001
        return []
    if isinstance(data, list) and data:
        return data[0].get("drivers") or []
    return []


#: A finishing status that is not "Running" means the car did not finish.
#: This is the single largest source of variance in a NASCAR lineup and it is
#: NOT symmetric with a bad finish: a DNF on lap 10 from 5th on the grid costs
#: the finishing points AND the whole place differential at once.
DNF_STATUSES = ("Accident", "Engine", "DVP", "Crash", "Suspension",
                "Transmission", "Brakes", "Overheating", "Electrical",
                "Rear Gear", "Vibration", "Handling", "Fuel Pump", "Clutch",
                "Retired", "Parked", "Oil Leak", "Steering", "Ignition")


def is_dnf(status: str | None) -> bool:
    s = (status or "").strip()
    return bool(s) and s.lower() != "running"


def race_rows(race_id: int, season: int, series: int = NASCAR_CUP,
              refresh: bool = False) -> list[dict]:
    """One row per driver in one race, results joined to loop data.

    Rows for an UPCOMING race are the entry list: finish and start are 0 and
    `scored` is False. Callers fitting a model must filter on `scored`, and
    the app relies on the same rows to know who is entered before qualifying.
    """
    feed = weekend(race_id, season, series, refresh)
    blocks = feed.get("weekend_race") or []
    if not blocks:
        return []
    race = blocks[0]
    loop = {d["driver_id"]: d for d in loopstats(race_id, season, series, refresh)}
    ttype = track_type(race)
    green = green_laps(race)

    rows = []
    for r in race.get("results") or []:
        lp = loop.get(r.get("driver_id")) or {}
        finish = int(r.get("finishing_position") or 0)
        start = int(r.get("starting_position") or 0)
        scored = finish > 0 and start > 0
        row = {
            "race_id": race_id, "season": season, "series": series,
            "race_name": (race.get("race_name") or "").strip(),
            "track": race.get("track_name"), "track_type": ttype,
            "actual_laps": int(race.get("actual_laps") or 0),
            "green_laps": green,
            "cars": int(race.get("number_of_cars_in_field") or 0),
            "race_date": race.get("race_date"),
            "driver_id": r.get("driver_id"), "driver": r.get("driver_fullname"),
            "team": r.get("team_name"), "car": r.get("car_number"),
            "manufacturer": r.get("car_make"),
            "start": start, "finish": finish,
            "laps_led": float(r.get("laps_led") or 0),
            "laps_completed": float(r.get("laps_completed") or 0),
            "status": r.get("finishing_status"),
            "dnf": is_dnf(r.get("finishing_status")),
            "fast_laps": float(lp.get("fast_laps") or 0),
            "avg_pos": lp.get("avg_ps"), "rating": lp.get("rating"),
            "quality_passes": lp.get("quality_passes"),
            "top15_laps": lp.get("top15_laps"),
            "passes_gf": lp.get("passes_gf"), "passed_gf": lp.get("passed_gf"),
            "scored": scored,
        }
        row["dk"] = (actual_points(start, finish, row["laps_led"],
                                   row["fast_laps"]) if scored else None)
        rows.append(row)
    return rows


def sessions(race_id: int, season: int, series: int = NASCAR_CUP,
             refresh: bool = False) -> dict:
    """{'practice': {driver_id: best_lap_speed}, 'qualifying': {driver_id: pos}}.

    Practice speed is the signal NASCAR DFS writing leans on hardest, and
    qualifying is where a starting position -- and therefore any place
    differential at all -- first exists. Both are empty until the sessions run,
    which is the app's pre/post-qualifying split.
    """
    feed = weekend(race_id, season, series, refresh)
    out: dict = {"practice": {}, "qualifying": {}, "practice_rank": {}}
    for run in feed.get("weekend_runs") or []:
        kind = run.get("run_type")
        for r in run.get("results") or []:
            did = r.get("driver_id")
            if did is None:
                continue
            if kind == 1:
                out["practice"][did] = float(r.get("best_lap_speed") or 0.0)
                out["practice_rank"][did] = int(r.get("finishing_position") or 0)
            elif kind == 2:
                out["qualifying"][did] = int(r.get("finishing_position") or 0)
    return out


def season_rows(season: int, series: int = NASCAR_CUP, gap: float = 0.15,
                refresh: bool = False) -> list[dict]:
    """Every driver-race in one season. Cached per race, so cheap to re-run.

    TWO THINGS HERE ARE ABOUT LATENCY, AND THEY MATTERED: this is called on
    every app build to compute driver form, and the first version took 90
    seconds.

    1. A race whose date is in the FUTURE is skipped without a request. Its
       feed 403s rather than 404s, and a failed request is not cached, so
       every call re-attempted the remaining races of the season over the
       network -- about seven round trips per season, every build, forever.
    2. The inter-request gap is only taken when a request was actually MADE.
       Sleeping 0.15s per race between reads of files already on disk is 11
       seconds of nothing across two seasons.
    """
    rows = []
    now = _dt.datetime.now().isoformat()
    for race in points_races(season, series, refresh):
        when = (race.get("race_date") or "")
        if when and when > now and not refresh:
            continue
        cached = (CACHE_DIR /
                  f"weekend_{series}_{season}_{race['race_id']}.json").exists()
        try:
            rows.extend(race_rows(race["race_id"], season, series, refresh))
        except Exception:                                   # noqa: BLE001
            continue
        if not cached or refresh:
            time.sleep(gap)
    return rows
