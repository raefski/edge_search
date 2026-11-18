"""Other companies' free NBA DFS projections, captured beside ours (Adam, 2026-11-18:
"whose optimizer is my optimizer resembling?").

Four sources serve a full slate with no login (probed 2026-11-18, one request each):

  rotowire    www.rotowire.com/daily/nba/api/{slate-list,players}.php -- DK projection,
              projected ownership ("rostership") and minutes for every player on a slate
  linestar    www.linestarapp.com/.../GetSalariesV5 -- projection, floor, ceiling; its
              slate list names DK's draft group (DfsSlateId)
  numberfire  FanDuel Research's GraphQL (the numberFire projections page) -- a projected
              STAT line + minutes; turned into DK points here (DK scoring, no DD/TD bonus:
              an expected-value line rarely shows one)
  dff         DailyFantasyFuel's projections page -- only the page's first 25 rows are in
              the HTML (the rest load on scroll), so it is PARTIAL: the biggest names
  (linestar is partial too: the free feed is truncated to ~31 players, IsTruncated)

Paywalled, not captured: RotoGrinders, FantasyLabs, SaberSim, Establish the Run,
Stokastic (its free CSV is one game). Each source is fetched once per build with a pause
between hosts; a source that fails is logged as failed (the row count shows it), never
filled in. Names join to DK's pool with edge.names.norm + team, then first-name
nicknames (edge.names.first_names_agree) when exactly one teammate fits.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from edge.names import first_names_agree, norm

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "dfs_outside_nba.csv"
COLS = ("date", "gid", "source", "player", "team", "salary", "proj", "own", "minutes",
        "floor", "ceil", "captured_at")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36")
PAUSE = 1.5

#: Team codes the sites spell differently from DK.
TEAM_FIX = {"NO": "NOP", "NOR": "NOP", "GS": "GSW", "SA": "SAS", "NY": "NYK", "PHO": "PHX",
            "UTAH": "UTA", "WSH": "WAS", "BRK": "BKN", "CHO": "CHA"}


def _team(t: str | None) -> str:
    t = (t or "").upper().strip()
    return TEAM_FIX.get(t, t)


def _get(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# ---- sources: each returns [{"player", "team", "proj", ...}] ----------------------------
def rotowire(lock: datetime | None, n_games: int | None) -> list[dict]:
    """The Classic slate whose start matches DK's lock (and game count when known)."""
    slates = json.loads(_get("https://www.rotowire.com/daily/nba/api/slate-list.php?siteID=1"))["slates"]
    classic = [s for s in slates if s.get("contestType") == "Classic"]
    if not classic:
        return []

    def fit(s):
        st = datetime.strptime(s["startDate"], "%Y-%m-%d %H:%M:%S")   # US Eastern, naive
        off = abs((st - lock.replace(tzinfo=None)).total_seconds()) if lock else 0
        return (off, abs(len(s.get("games") or []) - (n_games or 0)))
    s = min(classic, key=fit)
    time.sleep(PAUSE)
    rows = json.loads(_get(f"https://www.rotowire.com/daily/nba/api/players.php?slateID={s['slateID']}"))
    return [{"player": f"{p['firstName']} {p['lastName']}", "team": _team(p["team"]["abbr"]),
             "salary": p.get("salary"), "proj": float(p.get("pts") or 0),
             "own": p.get("rostership"), "minutes": p.get("minutes")} for p in rows]


def linestar(gid) -> list[dict]:
    """LineStar's current period; players on DK's draft group `gid` when it lists it."""
    url = ("https://www.linestarapp.com/DesktopModules/DailyFantasyApi/API/Fantasy/GetSalariesV5"
           "?periodId={}&site=1&sport=2")
    j = json.loads(_get(url.format(0)))
    period = j.get("PeriodId")
    sal = j.get("SalaryContainerJson")
    sal = json.loads(sal) if isinstance(sal, str) else (sal or {})
    if not sal.get("Salaries") and period:
        time.sleep(PAUSE)
        j = json.loads(_get(url.format(period)))
        sal = j.get("SalaryContainerJson")
        sal = json.loads(sal) if isinstance(sal, str) else (sal or {})
    out = []
    for p in sal.get("Salaries") or []:
        if not p.get("PP"):
            continue
        out.append({"player": p["Name"], "team": _team(p.get("PTEAM")), "salary": p.get("SAL"),
                    "proj": float(p["PP"]), "floor": p.get("Floor"), "ceil": p.get("Ceil")})
    return out


_NF_FIELDS = ("minutes points rebounds assists steals blocks turnovers threePointsMade")
_NF_QUERY = ("query GetProjections($input: ProjectionsInput!) { getProjections(input: $input) { "
             "... on NbaPlayer { player { name } team { abbreviation } " + _NF_FIELDS + " } } }")


def numberfire() -> list[dict]:
    """FanDuel Research's main-slate stat projections, scored the DK way."""
    page = _get("https://www.fanduel.com/research/nba/fantasy/dfs-projections").decode("utf-8", "replace")
    m = re.search(r'"selectedSlate":"(\d+)"', page)
    if not m:
        return []
    time.sleep(PAUSE)
    body = json.dumps({"query": _NF_QUERY, "variables": {"input": {
        "type": "DAILY", "position": "NBA_PLAYER", "sport": "NBA", "slateId": m.group(1)}}}).encode()
    j = json.loads(_get("https://www.fanduel.com/research/api/graphql", body, {
        "content-type": "application/json",
        "referer": "https://www.fanduel.com/research/nba/fantasy/dfs-projections"}))
    out = []
    for p in (j.get("data") or {}).get("getProjections") or []:
        if not p or "points" not in p:
            continue
        dk = (p["points"] + 1.25 * p["rebounds"] + 1.5 * p["assists"] + 2 * p["steals"]
              + 2 * p["blocks"] - 0.5 * p["turnovers"] + 0.5 * p["threePointsMade"])
        out.append({"player": p["player"]["name"], "team": _team((p.get("team") or {}).get("abbreviation")),
                    "proj": round(dk, 2), "minutes": p.get("minutes")})
    return out


def dff() -> list[dict]:
    html = _get("https://www.dailyfantasyfuel.com/nba/projections/draftkings").decode("utf-8", "replace")
    out = []
    for tag in re.findall(r"<tr[^>]*data-name=[^>]*>", html):
        a = dict(re.findall(r'data-([a-z_]+)= ?"([^"]*)"', tag))
        if a.get("ppg_proj"):
            out.append({"player": a["name"], "team": _team(a.get("team")), "salary": a.get("salary"),
                        "proj": float(a["ppg_proj"])})
    return out


# ---- join + log --------------------------------------------------------------------------
def join(rows: list[dict], pool: list[dict]) -> list[dict]:
    """Rows keyed to the DK pool's names; outside-only players dropped."""
    by = {(norm(p["name"]), p["team"]): p for p in pool}
    team_of: dict = {}
    for p in pool:
        team_of.setdefault(p["team"], []).append(p)
    out = []
    for r in rows:
        p = by.get((norm(r["player"]), r["team"]))
        if p is None:
            # norm() joins the words, so split the raw names: surname + nickname first name
            w = r["player"].lower().replace(".", "").split()
            cands = [q for q in team_of.get(r["team"], ())
                     if len(w) > 1 and q["name"].lower().replace(".", "").split()[-1] == w[-1]
                     and first_names_agree(w[0], q["name"].lower().split()[0])]
            p = cands[0] if len(cands) == 1 else None
        if p is not None:
            out.append({**r, "player": p["name"], "team": p["team"], "salary": p["salary"]})
    return out


SOURCES = {"rotowire": lambda ctx: rotowire(ctx["lock"], ctx["games"]),
           "linestar": lambda ctx: linestar(ctx["gid"]),
           "numberfire": lambda ctx: numberfire(),
           "dff": lambda ctx: dff()}


def capture(pool: list[dict], gid, date: str, lock: datetime | None, path: Path = LOG,
            fetchers: dict | None = None) -> dict:
    """Fetch every source, join to the pool, append to the log. {source: rows joined |
    "failed: ..."}. A failure is recorded per source, never filled from another."""
    ctx = {"gid": gid, "lock": lock, "games": len({p.get("game") for p in pool})}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result, out = {}, []
    for name, fetch in (fetchers or SOURCES).items():
        try:
            got = join(fetch(ctx), pool)
            result[name] = len(got)
            out += [{"date": date, "gid": gid, "source": name, "captured_at": now, **r} for r in got]
        except Exception as exc:                            # noqa: BLE001
            result[name] = f"failed: {type(exc).__name__}"
            log.warning("outside projections: %s failed (%s)", name, exc)
        time.sleep(PAUSE)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(out)
    return result


def load(path: Path = LOG) -> dict:
    """{gid: {source: {player: row}}}, the LAST capture per (gid, source)."""
    out: dict = {}
    if not path.exists():
        return out
    for r in csv.DictReader(path.open()):
        out.setdefault(r["gid"], {}).setdefault(r["source"], {})
        cur = out[r["gid"]][r["source"]]
        if cur and next(iter(cur.values()))["captured_at"] < r["captured_at"]:
            cur.clear()
        cur[r["player"]] = r
    return out


# ---- each full-slate source's own optimal lineups ----------------------------------------
LINEUPS = ROOT / "data" / "dfs_outside_lineups_nba.csv"
LINEUP_COLS = ("date", "gid", "source", "mode", "slot", "player", "team", "salary", "proj")
#: Sources whose free feed covers the slate (LineStar's free feed stops at ~31 players,
#: DFF's page at 25: their "optimal" lineup would be built from the stars only).
FULL = ("rotowire", "numberfire")
MIN_COVER = 0.8         # share of our 15+ point players the source must project


def source_lineups(pool: list[dict], rows: list[dict], iters: int = 60, seed: int = 0) -> dict:
    """{mode: result} built by OUR sim + optimizer on THIS source's projections; players it
    does not project are left out (proj 0), never filled with ours."""
    from edge import dfs_nba_theory as theory, dfs_opt_nba
    proj = {r["player"]: float(r["proj"]) for r in rows}
    big = [p for p in pool if p["proj"] >= 15]
    if not big or sum(p["name"] in proj for p in big) < MIN_COVER * len(big):
        return {}
    alt = [{**p, "proj": proj.get(p["name"], 0.0)} for p in pool]
    sim = theory.simulate(alt, n_sims=1500, seed=seed)
    theory.summarise(sim, alt)
    return {m: dfs_opt_nba.optimize(alt, sim, mode=m, iters=iters, seed=seed) for m in ("cash", "gpp")}


def capture_with_lineups(pool, gid, date, lock, path: Path = LOG, lpath: Path = LINEUPS,
                         fetchers: dict | None = None) -> dict:
    """capture() + each full source's cash/GPP lineup appended to `lpath`."""
    result = capture(pool, gid, date, lock, path, fetchers)
    got = load(path).get(str(gid), {})
    out = []
    for src in FULL:
        rows = list(got.get(src, {}).values())
        try:
            lus = source_lineups(pool, rows)
        except Exception as exc:                            # noqa: BLE001
            log.warning("outside lineups: %s failed (%s)", src, exc)
            continue
        for mode, res in lus.items():
            for slot, p in (res or {}).get("slots") or []:
                out.append({"date": date, "gid": gid, "source": src, "mode": mode, "slot": slot,
                            "player": p["name"], "team": p["team"], "salary": p["salary"],
                            "proj": round(p["proj"], 2)})
        result[f"{src}_lineups"] = len(lus)
    new = not lpath.exists()
    with lpath.open("a", newline="") as f:
        w = csv.DictWriter(f, LINEUP_COLS)
        if new:
            w.writeheader()
        w.writerows(out)
    return result
