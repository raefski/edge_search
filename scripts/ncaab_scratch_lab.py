"""NCAAB late scratches: lineup slots lost to players ruled out after the build, and
whether a pulled player prop flags them in time (NCAAB_CRITIQUE_LOG.md cycle 11).

Why this exists. College basketball has no injury report: the board's only Out list is
RotoWire's, read once at build time. A player who is ruled out after that -- or after lock,
in a game that tips hours later -- stays in the lineup at full projection and scores 0.
Cycle 10's critic found 7 such slots in 24 slates and noticed that the books take a
scratched player's props down before tip while his teammates stay quoted. odds.db has
every dfs_ncaab scan (~every 15 min since 11/2), so the props state at any moment can be
replayed: this script is the timestamped pre-lock state log the availability critic asks
for, built from data we already collect.

    python3 scripts/ncaab_scratch_lab.py cache            # odds.db -> data/cache/ncaab_props_presence.json
    python3 scripts/ncaab_scratch_lab.py report [--cutoff 2026-11-18]
    python3 scripts/ncaab_scratch_lab.py rows OUT.csv     # one row per board player (for critics)

Definitions (all at a moment t, from the scans that STARTED before t):
  * a player is "quoted" at t when the latest scan of his game before t posts any player prop
    for him (any book, any market);
  * he is "pulled" at t when an earlier scan of the same game quoted him, the latest scan
    before t still quotes the game's other players (>= PULL_MIN_OTHERS of them), and he is not
    in it. A game whose props all vanish (the books closing the event at tip) is not a pull.
  * "build" = the last logged build of the draft group before lock (the history file's
    built_at); lock = the slate's first tip; "tip" = his own game's tip.
Truth: ESPN box minutes (data/ncaab_box, scripts/ncaab_box_grade.py join): 0 or absent = DNP.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from bisect import bisect_right
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ncaab_box_grade as grader                       # noqa: E402
from edge import ncaab_scratch                         # noqa: E402
from edge.ncaab_names import Rosters                   # noqa: E402

DB = ROOT / "data" / "odds.db"
CACHE = ROOT / "data" / "cache" / "ncaab_props_presence.json"
HISTORY = ROOT / "data" / "dfs_proj_history_ncaab.csv"
SPORT = "basketball_ncaab"
PROFILE = "dfs_ncaab"
PULL_MIN_OTHERS = ncaab_scratch.PULL_MIN_OTHERS


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- cache (odds.db, read-only)

def build_cache(path: Path = CACHE, quiet: bool = False) -> int:
    """Per dfs_ncaab scan: started_at and {event_id: [subjects with a player prop]}; per event
    its commence time and teams. Read-only on odds.db (hot) and the F: archive."""
    from edge.odds import archive
    old = json.loads(path.read_text()) if path.exists() else {"scans": {}, "events": {}}
    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)) as conn:
        scans = conn.execute("SELECT id, started_at FROM scan WHERE profile=? AND ok=1 ORDER BY id",
                             (PROFILE,)).fetchall()
        arch = dict(conn.execute("SELECT a.scan_id, a.file FROM archived a JOIN scan s ON s.id=a.scan_id "
                                 "WHERE s.profile=?", (PROFILE,)).fetchall())
        todo = [(i, st) for i, st in scans if str(i) not in old["scans"]]
        groups: dict = {}
        by_file: dict = defaultdict(list)
        for i, _st in todo:
            if i in arch:
                by_file[arch[i]].append(i)
            else:
                groups[i] = {r[0] for r in conn.execute(
                    "SELECT DISTINCT group_key FROM quote WHERE scan_id=? AND sport_key=? "
                    "AND market LIKE 'player_%'", (i, SPORT))}
        for f, ids in by_file.items():
            p = Path(f)
            if not p.is_absolute() or not p.exists():
                p = archive.ARCHIVE_DIR / Path(f).name
            groups.update(archive.scan_groups(p, ids, SPORT))
        ev_ids = set(old["events"])
        for i, st in todo:
            subj: dict = defaultdict(set)
            for gk in groups.get(i, ()):
                ev, market, subject, _pt = (gk.split("|") + ["", "", ""])[:4]
                if market.startswith("player_") and subject:
                    subj[ev].add(subject)
            old["scans"][str(i)] = {"t": st, "ev": {e: sorted(s) for e, s in subj.items()}}
            ev_ids.update(subj)
        need = [e for e in ev_ids if e not in old["events"]]
        for k in range(0, len(need), 500):
            chunk = need[k:k + 500]
            for e, ct, h, a in conn.execute(
                    f"SELECT event_id, commence_time, home_team, away_team FROM event WHERE event_id IN "
                    f"({','.join('?' * len(chunk))})", chunk):
                old["events"][e] = {"tip": ct, "home": h, "away": a}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old))
    if not quiet:
        print(f"cache: {len(old['scans'])} scans ({len(todo)} new), {len(old['events'])} events -> {path}")
    return 0


class Presence:
    """The cache as a timeline: for each event, its scans in time order."""

    def __init__(self, path: Path = CACHE):
        d = json.loads(path.read_text())
        self.events = d["events"]
        tl: dict = defaultdict(list)
        for _i, s in d["scans"].items():
            t = _ts(s["t"])
            for ev, subs in s["ev"].items():
                tl[ev].append((t, frozenset(subs)))
        self.timeline = {ev: sorted(v, key=lambda x: x[0]) for ev, v in tl.items()}
        self._times = {ev: [x[0] for x in v] for ev, v in self.timeline.items()}

    def tip(self, ev: str) -> datetime | None:
        e = self.events.get(ev)
        return _ts(e["tip"]) if e else None

    def state(self, ev: str, subject_set, t: datetime) -> str:
        """'quoted' / 'pulled' / 'unposted' (his game's props are up, he never was) / 'never'
        (no props for his game before t) / 'closed' (the game's board is gone or too thin to
        tell) for one player's subject names."""
        times = self._times.get(ev)
        if not times:
            return "never"
        i = bisect_right(times, t)
        if i == 0:
            return "never"
        seen = any(s in subs for _t, subs in self.timeline[ev][:i] for s in subject_set)
        _t, latest = self.timeline[ev][i - 1]
        if not seen:
            return "unposted" if len(latest) >= PULL_MIN_OTHERS else "never"
        if any(s in latest for s in subject_set):
            return "quoted"
        return "pulled" if len(latest) >= PULL_MIN_OTHERS else "closed"

    def first_pull(self, ev: str, subject_set, until: datetime) -> datetime | None:
        """The production rule (edge/ncaab_scratch.isolated_pull): pulled ALONE before `until`
        and still off at the last scan before it."""
        return ncaab_scratch.isolated_pull(self.timeline.get(ev), subject_set, until)


# ---------------------------------------------------------------- slates

def builds() -> dict:
    """{gid: sorted built_at times} from the build history file."""
    out: dict = defaultdict(set)
    with HISTORY.open() as f:
        for r in csv.DictReader(f):
            out[int(r["gid"])].add(_ts(r["built_at"]))
    return {g: sorted(v) for g, v in out.items()}


def history_rows(gid: int, built_at: datetime) -> list[dict]:
    with HISTORY.open() as f:
        return [r for r in csv.DictReader(f) if int(r["gid"]) == gid and _ts(r["built_at"]) == built_at]


def match_events(pres: Presence, rosters: Rosters, teams: set, window: tuple) -> dict:
    """{DK team: (event_id, {salary key: subjects})}: each prop event in the slate's window is
    assigned to the DK team pair whose rosters its subjects resolve to (as player_props does,
    by the subjects, never by a name-only join)."""
    lo, hi = window
    out: dict = {}
    for ev, tl in pres.timeline.items():
        tip = pres.tip(ev)
        if tip is None or not (lo <= tip <= hi):
            continue
        names = set().union(*(s for _t, s in tl))
        by_team: dict = defaultdict(dict)
        for name in names:
            info, _how = rosters.resolve(name, sorted(teams))
            if info is None:
                continue
            key = rosters.key(info)
            by_team[str(info["team"]).upper()].setdefault(key, set()).add(name)
        hits = sum(len(v) for v in by_team.values())
        if not hits or 2 * hits < len(names):
            continue
        for team_code, keys in by_team.items():
            if len(keys) >= 2 or team_code not in out:      # one stray namesake is not his game
                prev = out.get(team_code)
                if prev is None or len(keys) > len(prev[1]):
                    out[team_code] = (ev, keys)
    return out


def lineups(gid: int) -> list[dict]:
    """Logged lineup slots for the draft group (mode, player, team)."""
    out = []
    for p in sorted(ROOT.glob("data/dfs_lineups_ncaab_*.csv")):
        with p.open() as f:
            out += [r for r in csv.DictReader(f) if r.get("gid") == str(gid)]
    return out


def adam_slots(gid: int) -> dict:
    """{(norm name, mode): slots} of Adam's entered lineups in this draft group (contests.db,
    read-only) -- what was actually entered, not only what the app logged (T17)."""
    from edge.names import norm
    out: dict = defaultdict(int)
    db = ROOT / "data" / "contests.db"
    if not db.exists():
        return out
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
        for name, mode in conn.execute(
                "SELECT ep.player_norm, c.type FROM entry_player ep JOIN entry e USING (contest_id, entry_id) "
                "JOIN contest c USING (contest_id) WHERE e.is_adam=1 AND c.draft_group=?", (gid,)):
            out[(name, mode)] += 1
    return out


def slate(pres: Presence, gid: int, build_times: list) -> dict | None:
    """Every logged player of one slate with truth, his game's tip and his props state at the
    build, at lock and at his tip."""
    try:
        logged = grader.logged(gid)
        box = json.loads((grader.BOX_DIR / f"{gid}.json").read_text())
    except (OSError, ValueError):
        return None
    rows, _ = grader.join(logged, box)
    rows = [r for r in rows if not r.get("namesake")]
    if not rows:
        return None
    salaries = {k: {"name": r["player"], "team": r["team"], "row": r}
                for k, r in zip(grader.keys(rows), rows)}
    rosters = Rosters(salaries)
    teams = {str(r["team"]).upper() for r in rows}
    d0 = datetime.fromisoformat(rows[0]["date"]).replace(tzinfo=timezone.utc)
    window = (d0 + timedelta(hours=12), d0 + timedelta(hours=36))   # ET evening = next-day UTC early
    ev_of = match_events(pres, rosters, teams, window)
    tips = [pres.tip(ev) for ev, _k in ev_of.values()]
    lock = min(t for t in tips if t) if tips else None
    pre = [b for b in build_times if lock is None or b < lock]
    build = pre[-1] if pre else None
    slots = defaultdict(list)
    for s in lineups(gid):
        slots[(s["player"], s["team"])].append(s["mode"])
    from edge.names import norm
    adam = adam_slots(gid)
    flagged = flags_at_build(rows, pres)
    out = []
    for key, info in salaries.items():
        r = info["row"]
        team = str(r["team"]).upper()
        ev, keys = ev_of.get(team, (None, {}))
        subs = keys.get(key, set())
        tip = pres.tip(ev) if ev else None
        st = {}
        for name, t in (("build", build), ("lock", lock), ("tip", tip)):
            st[name] = pres.state(ev, subs, t) if (ev and t) else "never"
        pull_t = pres.first_pull(ev, subs, tip) if (ev and subs and tip) else None
        out.append({
            "gid": gid, "date": r["date"], "player": r["player"], "team": team,
            "salary": int(float(r["salary"])), "proj": float(r["proj"]), "source": r["proj_source"],
            "status": r.get("status") or "", "min": r.get("min") or 0.0, "act": r.get("act") or 0.0,
            "dnp": not (r.get("min") or 0) > 0, "event": ev or "",
            "build": build.isoformat() if build else "", "lock": lock.isoformat() if lock else "",
            "tip": tip.isoformat() if tip else "",
            "at_build": st["build"], "at_lock": st["lock"], "at_tip": st["tip"],
            "pulled_at": pull_t.isoformat() if pull_t else "",
            "flag_build": (r["player"], r["team"], int(float(r["salary"]))) in flagged,
            "cash_slots": slots.get((r["player"], r["team"]), []).count("cash"),
            "gpp_slots": slots.get((r["player"], r["team"]), []).count("gpp"),
            "adam_cash": adam.get((norm(r["player"]), "cash"), 0),
            "adam_gpp": adam.get((norm(r["player"]), "gpp"), 0),
        })
    return {"gid": gid, "date": rows[0]["date"], "build": build, "lock": lock, "rows": out,
            "events": len(ev_of)}


def flags_at_build(rows: list[dict], pres: Presence | None = None) -> set:
    """{(player, team, salary)} of one slate's logged rows whose props were pulled in
    isolation before the slate's last logged pre-lock build -- exactly what
    edge/dfs_run_ncaab.apply_props_pulled would have flagged then (same isolated_pull, same
    in-game name resolution). Empty when the slate has no build log (before 11/9). Never
    raises: scripts/ncaab_lab.py (the gate's lab) calls it."""
    try:
        if not rows:
            return set()
        gid = int(rows[0]["gid"])
        if pres is None:
            if not _PRES:
                # Once per process, incremental (~1 s): a stale cache would silently miss the
                # newest slates' flags; a missing one is built from odds.db (~30 s, read-only).
                try:
                    build_cache(quiet=True)
                except Exception as exc:                # noqa: BLE001
                    print(f"ncaab_scratch_lab: cache not refreshed ({exc})", file=sys.stderr)
                _PRES.append(Presence())
            pres = _PRES[0]
        bt = _BUILDS[0] if _BUILDS else builds()
        _BUILDS[:1] = [bt]
        if gid not in bt:
            return set()
        salaries = {i: {"name": r["player"], "team": r["team"], "row": r} for i, r in enumerate(rows)}
        rosters = Rosters(salaries)
        teams = {str(r["team"]).upper() for r in rows}
        d0 = datetime.fromisoformat(rows[0]["date"]).replace(tzinfo=timezone.utc)
        ev_of = match_events(pres, rosters, teams, (d0 + timedelta(hours=12), d0 + timedelta(hours=36)))
        tips = [pres.tip(ev) for ev, _k in ev_of.values() if pres.tip(ev)]
        lock = min(tips) if tips else None
        pre = [b for b in bt[gid] if lock is None or b < lock]
        if not pre:
            return set()
        build = pre[-1]
        opp = {str(r["team"]).upper(): str(r.get("opp") or "").upper() for r in rows}
        pairs = {ev: (t, opp.get(t)) for t, (ev, _k) in ev_of.items()}
        hit = ncaab_scratch.pulled_players(pres.timeline, pairs, rosters, build)
        return {(salaries[k]["row"]["player"], salaries[k]["row"]["team"],
                 int(float(salaries[k]["row"]["salary"]))) for k in hit}
    except Exception as exc:                                # noqa: BLE001
        print(f"ncaab_scratch_lab: no pull flags ({type(exc).__name__}: {exc})", file=sys.stderr)
        return set()


_PRES: list = []
_BUILDS: list = []


def all_rows(pres: Presence | None = None) -> list[dict]:
    pres = pres or Presence()
    bt = builds()
    out = []
    for gid in sorted(bt):
        s = slate(pres, gid, bt[gid])
        if s:
            out += s["rows"]
    return out


# ---------------------------------------------------------------- report

def _rate(rows, cond) -> str:
    sub = [r for r in rows if cond(r)]
    if not sub:
        return "0/0"
    k = sum(r["dnp"] for r in sub)
    return f"{k}/{len(sub)} ({100 * k / len(sub):.1f}%)"


def report(cutoff: str | None) -> int:
    rows = all_rows()
    if cutoff:
        print(f"split: train <= {cutoff} < test")
    gids = sorted({r["gid"] for r in rows})
    print(f"{len(gids)} boxed slates with a build, {len(rows)} player rows")
    for label, part in (("all", rows),) + ((("train", [r for r in rows if r["date"] <= cutoff]),
                                            ("test", [r for r in rows if r["date"] > cutoff])) if cutoff else ()):
        print(f"\n[{label}] {len({r['gid'] for r in part})} slates")
        print("  DNP rate by props state ($4K+):")
        big = [r for r in part if r["salary"] >= 4000]
        for when in ("at_build", "at_lock", "at_tip"):
            print(f"    {when:9s} " + "  ".join(
                f"{k} {_rate(big, lambda r, k=k: r[when] == k)}" for k in
                ("quoted", "pulled", "unposted", "never", "closed")))
        for name, sel in (("isolated pull before tip", lambda r: bool(r["pulled_at"])),
                          ("flagged at build (production rule)", lambda r: r["flag_build"])):
            fl = [r for r in part if sel(r)]
            if fl:
                ratio = sum(r["act"] for r in fl) / max(1e-9, sum(r["proj"] for r in fl))
                print(f"  {name}: {len(fl)} rows, DNP {sum(r['dnp'] for r in fl)}, "
                      f"realised/proj {ratio:.3f}, slots cash {sum(r['cash_slots'] for r in fl)} "
                      f"gpp {sum(r['gpp_slots'] for r in fl)}")
            else:
                print(f"  {name}: 0 rows")
        fl = [r for r in part if r["flag_build"]]
        if fl:
            a = sum(abs(r["proj"] - r["act"]) for r in fl) / len(fl)
            b = sum(abs(r["proj"] * ncaab_scratch.PULL_MULT - r["act"]) for r in fl) / len(fl)
            print(f"  flagged-at-build MAE: as logged {a:.2f} -> x{ncaab_scratch.PULL_MULT} {b:.2f} (n={len(fl)})"
                  + "".join(f"\n    {r['date']} {r['player']} {r['team']} ${r['salary']} proj {r['proj']:.1f} "
                            f"act {r['act']:.2f} min {r['min']}" for r in fl))
        lost = [r for r in part if r["dnp"] and (r["cash_slots"] or r["gpp_slots"])]
        print(f"  lineup slots on a DNP: {sum(r['cash_slots'] for r in lost)} cash / "
              f"{sum(r['gpp_slots'] for r in lost)} GPP ({len(lost)} players)")
        alost = [r for r in part if r["dnp"] and (r["adam_cash"] or r["adam_gpp"])]
        print(f"  Adam's entered slots on a DNP: {sum(r['adam_cash'] for r in alost)} cash / "
              f"{sum(r['adam_gpp'] for r in alost)} GPP"
              + "".join(f"\n    {r['date']} {r['player']} {r['team']} ${r['salary']} build {r['at_build']} "
                        f"lock {r['at_lock']} tip {r['at_tip']}" for r in alost))
        for r in sorted(lost, key=lambda r: r["date"]):
            print(f"    {r['date']} {r['player']:24s} {r['team']:6s} ${r['salary']:>5d} proj {r['proj']:5.1f} "
                  f"{r['source']:6s} cash {r['cash_slots']} gpp {r['gpp_slots']} | build {r['at_build']:6s} "
                  f"lock {r['at_lock']:6s} tip {r['at_tip']:6s} pulled {r['pulled_at'][5:16]} tip {r['tip'][5:16]}")
    return 0


def dump(path: str) -> int:
    rows = all_rows()
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (";".join(v) if isinstance(v, list) else v) for k, v in r.items()})
    print(f"{len(rows)} rows -> {path}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cache")
    rp = sub.add_parser("report")
    rp.add_argument("--cutoff")
    dp = sub.add_parser("rows")
    dp.add_argument("out")
    a = ap.parse_args(argv)
    if a.cmd == "cache":
        return build_cache()
    if a.cmd == "report":
        return report(a.cutoff)
    return dump(a.out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
