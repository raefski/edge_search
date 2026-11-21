"""Build data/college_teams.json -- the college rosetta stone (edge/college_teams.py).

Sources, in order of authority:
  1. ESPN team lists (men's basketball D1 + college football): the school ids.
  2. SEED_ALIASES in edge/college_teams.py: spellings only a person knows.
  3. DK DFS draftables `competitions` for every college draft group we have logged
     (proj history / proj logs) plus today's lobby (draftables_snapshot/slates.json):
     DK teamId, school ("city"), mascot ("teamName"), code. Mapped to ESPN by school
     name, with the mascot deciding a shared school name. One request per draft
     group, >= 1.2 s apart (DRAFTKINGS_ACCESS.md); results cached per gid.
  4. Names the books, Torvik and ESPN scoreboards have used (data/odds.db events,
     data/ncaab_torvik_*.json) -- not added as aliases, AUDITED: every one must
     resolve to exactly one school, or it is printed for a SEED_ALIASES line.

    python3 scripts/college_teams_build.py            # build + audit
    python3 scripts/college_teams_build.py --offline  # no network: cached DK/ESPN only
Exit status 1 when any observed name is unresolved or ambiguous.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import college_teams as ct  # noqa: E402

CACHE = ROOT / "data" / "cache" / "college_teams"
ESPN = ("https://site.api.espn.com/apis/site/v2/sports/{path}/teams?limit=1000")
ESPN_PATHS = {"CBB": "basketball/mens-college-basketball", "CFB": "football/college-football"}
ODDS_SPORT = {"CBB": "basketball_ncaab", "CFB": "americanfootball_ncaaf"}
DK_PAUSE_S = 1.2


def _espn(sport: str, offline: bool) -> list[dict]:
    f = CACHE / f"espn_{sport}.json"
    if not offline:
        req = urllib.request.Request(ESPN.format(path=ESPN_PATHS[sport]),
                                     headers={"User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        f.write_text(json.dumps([x["team"] for x in d["sports"][0]["leagues"][0]["teams"]]))
    return json.loads(f.read_text())


def _gids() -> dict[str, set[str]]:
    out = {"CBB": set(), "CFB": set()}
    for sport, stem in (("CBB", "ncaab"), ("CFB", "ncaaf")):
        for f in glob.glob(str(ROOT / "data" / f"dfs_proj_*_{stem}*.csv")):
            with open(f, newline="") as fh:
                for r in csv.DictReader(fh):
                    g = (r.get("gid") or r.get("draft_group") or "").strip()
                    if g.isdigit():
                        out[sport].add(g)
    sl = ROOT / "data" / "draftables_snapshot" / "slates.json"
    if sl.exists():
        for sport, groups in json.loads(sl.read_text()).items():
            if sport in out:
                out[sport] |= set(groups)
    return out


def _dk_comps(sport: str, gids: set[str], offline: bool) -> list[dict]:
    from edge.dfs import _get
    teams = []
    for g in sorted(gids):
        f = CACHE / f"dk_{g}.json"
        if not f.exists():
            if offline:
                continue
            try:
                d = _get(f"https://api.draftkings.com/draftgroups/v1/draftgroups/{g}/draftables")
                comps = d.get("competitions") or []
                f.write_text(json.dumps([{"home": c.get("homeTeam"), "away": c.get("awayTeam"),
                                          "start": c.get("startTime")} for c in comps]))
            except Exception as e:  # one dead group must not stop the build
                print(f"  DK {g}: {e}", file=sys.stderr)
                continue
            time.sleep(DK_PAUSE_S)
        for c in json.loads(f.read_text()):
            for side in ("home", "away"):
                if c.get(side):
                    teams.append({**c[side], "gid": g, "start": c.get("start")})
    return teams


def build(offline: bool = False) -> tuple[dict, list[str]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    schools: dict[str, dict] = {}
    alias: dict[str, set[str]] = {}
    major: set[str] = set()

    def add(k_name: str, sid: str):
        k = ct.key(k_name)
        if k:
            alias.setdefault(k, set()).add(sid)

    for sport in ("CBB", "CFB"):
        for t in _espn(sport, offline):
            sid = str(t["id"])
            s = schools.setdefault(sid, {"name": t.get("location"), "mascot": t.get("name"),
                                         "espn_abbr": t.get("abbreviation"), "sports": [],
                                         "display": t.get("displayName")})
            s["sports"] = sorted(set(s["sports"]) | {sport})
            if sport == "CBB":
                major.add(sid)
            for fld in ("location", "displayName", "shortDisplayName", "nickname", "abbreviation"):
                if t.get(fld):
                    add(t[fld], sid)

    # SEED_ALIASES: the left side must be exactly one ESPN location.
    by_loc: dict[str, set[str]] = {}
    for sid, s in schools.items():
        by_loc.setdefault(ct.key(s["name"]), set()).add(sid)
    for loc, spellings in ct.SEED_ALIASES.items():
        ids = by_loc.get(ct.key(loc), set())
        ids = (ids & major) or ids
        if len(ids) != 1:
            problems.append(f"SEED '{loc}': ESPN location matches {len(ids)} schools")
            continue
        sid = next(iter(ids))
        for sp in spellings:
            alias[ct.key(sp)] = {sid}   # a seed is authoritative over automatic collisions

    # SHARED_ALIASES: added to every school named (a union, never pinned).
    for sp, locs in ct.SHARED_ALIASES.items():
        for loc in locs:
            ids = by_loc.get(ct.key(loc), set())
            ids = (ids & major) or ids
            if len(ids) != 1:
                problems.append(f"SHARED '{sp}' -> '{loc}': ESPN location matches {len(ids)} schools")
                continue
            alias.setdefault(ct.key(sp), set()).update(ids)

    # DK: teamId -> school by city, mascot breaking ties.
    dk_team: dict[str, str] = {}
    dk_code: dict[str, dict[str, set[str]]] = {"CBB": {}, "CFB": {}}
    gids = _gids()
    for sport in ("CBB", "CFB"):
        for t in _dk_comps(sport, gids[sport], offline):
            tid, city, masc, code = t.get("teamId"), t.get("city") or "", t.get("teamName") or "", t.get("abbreviation") or ""
            tmp = {k: set(v) for k, v in alias.items()}
            cand = set()
            for n in (f"{city} {masc}", city):
                cand = set(tmp.get(ct.key(n), ()))
                if cand:
                    break
            if len(cand) > 1:
                cand = {c for c in cand if ct.key(schools[c]["mascot"]) == ct.key(masc)} or cand
            if len(cand) > 1:
                cand = (cand & major) or cand
            if len(cand) != 1:
                problems.append(f"DK {sport} teamId {tid} '{city} {masc}' ({code}, gid {t['gid']}): "
                                f"{len(cand)} ESPN matches {sorted(cand)[:4]}")
                continue
            sid = next(iter(cand))
            dk_team[f"{sport}:{tid}"] = sid
            dk_code[sport].setdefault(code.upper(), set()).add(sid)
            major.add(sid)
            add(city, sid)
            s = schools[sid].setdefault("dk", {})
            s.setdefault(sport, {"team_ids": [], "codes": [], "city": city})
            for fld, v in (("team_ids", tid), ("codes", code.upper())):
                if v not in s[sport][fld]:
                    s[sport][fld].append(v)

    reg = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "n_schools": len(schools), "n_dk_teams": len(dk_team),
           "schools": schools,
           "alias": {k: sorted(v) for k, v in sorted(alias.items())},
           "dk_code": {sp: {c: sorted(v) for c, v in sorted(m.items())} for sp, m in dk_code.items()},
           "dk_team": dict(sorted(dk_team.items())),
           "major": sorted(major)}
    return reg, problems


def observed_names() -> dict[str, set[str]]:
    out = {"CBB": set(), "CFB": set()}
    db = ROOT / "data" / "odds.db"
    if db.exists():
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for sport, sk in ODDS_SPORT.items():
            for h, a in c.execute("select home_team, away_team from event where sport_key=?", (sk,)):
                out[sport] |= {h, a}
    for f in glob.glob(str(ROOT / "data" / "ncaab_torvik_*.json")):
        out["CBB"] |= {r.get("team") for r in json.loads(Path(f).read_text()) if r.get("team")}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    reg, problems = build(a.offline)
    ct.PATH.write_text(json.dumps(reg, indent=1, sort_keys=False) + "\n")
    ct.reload()
    bad = 0
    for sport, names in observed_names().items():
        au = ct.audit(names)
        n = len(names)
        print(f"{sport}: {n} observed names, {n - len(au['unknown']) - len(au['ambiguous'])} resolve to one school, "
              f"{len(au['unknown'])} unknown, {len(au['ambiguous'])} ambiguous")
        for nm in au["unknown"]:
            print(f"   unknown   {nm!r}")
        for nm, ids in au["ambiguous"].items():
            tag = "shared (needs the game)" if ct.key(nm) in {ct.key(x) for x in ct.SHARED_ALIASES} else "AMBIGUOUS"
            print(f"   {tag} {nm!r}: " + ", ".join(f"{i} {reg['schools'][i]['display']}" for i in ids))
        shared = {ct.key(x) for x in ct.SHARED_ALIASES}
        bad += len(au["unknown"]) + sum(ct.key(nm) not in shared for nm in au["ambiguous"])
    print(f"registry: {reg['n_schools']} schools, {reg['n_dk_teams']} DK teams, "
          f"{sum(len(m) for m in reg['dk_code'].values())} DK codes -> {ct.PATH.relative_to(ROOT)}")
    multi = {f"{sp}:{c}": v for sp, m in reg["dk_code"].items() for c, v in m.items() if len(v) > 1}
    for c, v in multi.items():
        print(f"   DK code {c} means {len(v)} schools: " + ", ".join(reg['schools'][i]['display'] for i in v))
    for p in problems:
        print("   PROBLEM", p)
    return 1 if (bad or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
