#!/usr/bin/env python3
"""Held-out harness for the college build's NAME JOIN: every DraftKings ladder
subject in a slate game, as the board at lock joined it (or did not).

    python3 scripts/ncaaf_names_lab.py report                     # every slate on file
    python3 scripts/ncaaf_names_lab.py report --cutoff 2026-10-11 --arch <dir>
    python3 scripts/ncaaf_names_lab.py report --gid 155277 -v     # one slate, every miss

WHY A REPLAY
The forward log holds only players that joined, so a ladder that never found
its draftable leaves no trace there. Every input of the join survives, though:

    DK draftables   data/draftables_snapshot/<gid>.json, last commit before lock (git --all)
    ladders         data/odds.db, last ok dfs_ncaaf scan before lock (archive on F:
                    for older scans -- copy it to local disk and pass --arch, STATUS §10)

and the join is replayed through production code (dfs_run_ncaaf.join_ladders,
the one function build_pool calls), so a candidate fix is a change to
production code measured here before and after.

METRIC (per slate, the slate is the unit)
  subjects   DK ladder subjects named in an odds event that is a slate game
  joined     ... that build_pool would project: dfs_run_ncaaf.join_ladders, the
             exact join (subject_events owner == event) plus alias_joins
  missed     the rest, by reason: no_key (no DK name matches), ambiguous (two
             DK players share the key, both dropped), refused (the key is a
             player in ANOTHER game)
  recall     joined / subjects
  wrong      joined subjects whose book team (the "(MSU)" suffix, mapped to a
             DK team by majority of the event's joined subjects) is not the DK
             player's team -- a namesake from the other sideline, or an alias
             onto the wrong roster; listed with -v so it can be read
  lost       for each miss: the ladder projection, the most similar DK name in
             that game, and that player's DK FPTS from the contest exports
  unpriced   slate games with no odds event at all (no ladders posted: coverage,
             not names -- reported so it is not mistaken for a join failure)
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs, dfs_slate, dfs_sport  # noqa: E402
from edge import dfs_run_ncaaf as R  # noqa: E402
from edge.names import norm  # noqa: E402
from edge.odds.source import ScrapedOddsClient  # noqa: E402
from edge.odds.store import OddsStore  # noqa: E402

PROJ_LOG = ROOT / "data" / "dfs_proj_log_ncaaf.csv"
CONTESTS = ROOT / "data" / "contests.db"
DEFAULT_CUTOFF = "2026-10-11"           # same split as scripts/ncaaf_lab.py
_SUFFIX = re.compile(r"\(([^)]*)\)\s*$")


def slates() -> list[int]:
    """Every NCAAF Classic draft group we logged or hold an export for."""
    gids = set()
    if PROJ_LOG.exists():
        with PROJ_LOG.open(newline="") as fh:
            gids |= {int(r["gid"]) for r in csv.DictReader(fh) if r.get("gid", "").isdigit()}
    con = sqlite3.connect(CONTESTS)
    gids |= {int(g) for (g,) in con.execute(
        "select distinct draft_group from contest where sport='NCAAF' and draft_group is not null")}
    return sorted(gids)


def truth() -> dict:
    """{draft_group: {norm_name: fpts}} from the DK exports."""
    out: dict = collections.defaultdict(dict)
    con = sqlite3.connect(CONTESTS)
    for dg, name, fpts in con.execute(
            "select c.draft_group, p.name, p.fpts from player_result p join contest c "
            "using(contest_id) where c.sport='NCAAF' and c.draft_group is not null"):
        if fpts is not None:
            out[int(dg)][norm(name)] = float(fpts)
    return out


def _git_json(path: str, before: str | None):
    cut = [f"--before={before}"] if before else []
    h = subprocess.run(["git", "log", "--all", "-1", "--diff-filter=AM", *cut,
                        "--pretty=%h %cI", "--", path],
                       cwd=ROOT, capture_output=True, text=True).stdout.split()
    if not h:
        return None, None
    raw = subprocess.run(["git", "show", f"{h[0]}:{path}"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return h, json.loads(raw)


def board_at_lock(gid: int):
    """(lock UTC, draftables rows as of the last commit before lock) or (None, None)."""
    path = f"data/draftables_snapshot/{gid}.json"
    _, rows = _git_json(path, None)
    if not rows:
        return None, None
    starts = sorted(r["competition"]["startTime"] for r in rows if r.get("competition"))
    if not starts:
        return None, None
    lock = starts[0][:19] + "Z"
    _, rows = _git_json(path, lock)
    return lock, rows


def scan_before(store: OddsStore, at: str):
    return store.conn.execute(
        "SELECT id, finished_at FROM scan WHERE ok=1 AND profile='dfs_ncaaf' AND finished_at < ?"
        " ORDER BY finished_at DESC LIMIT 1", (at.rstrip("Z"),)).fetchone()


def team_map(spelled: dict, joined: dict) -> dict:
    """{book team suffix: DK team} by majority of the event's joined subjects."""
    votes: dict = collections.defaultdict(collections.Counter)
    for k, info in joined.items():
        m = _SUFFIX.search(spelled.get(k, ""))
        if m:
            votes[m.group(1)][info.get("team")] += 1
    return {k: c.most_common(1)[0][0] for k, c in votes.items()}


def replay(gid: int, store: OddsStore, fpts: dict) -> dict | None:
    lock, rows = board_at_lock(gid)
    if rows is None:
        return None
    scan = scan_before(store, lock)
    if not scan:
        return None
    dfs._draftables_raw = lambda g: rows
    sal = dfs.fetch_draftables(gid)
    games = dfs_slate.slate_games(sal)
    client = ScrapedOddsClient(store, "dfs_ncaaf", scan_id=scan[0], main_line_only=False)
    spelled: dict = {}
    by_event, kick = R.collect_ladders_by_event(client, subjects=spelled)
    stats: dict = {}
    joins = R.join_ladders(by_event, kick, sal, spelled, stats)       # production
    joined_ids = {id(bm): info for bm, info in joins}
    by_key, _ = R.salary_index(sal)
    ev_game = dfs_slate.event_games({ev: set(p) for ev, p in by_event.items()},
                                    by_key.get, kick, games, R.MATCH_TOLERANCE)
    roster = collections.defaultdict(list)
    for info in sal.values():
        roster[info.get("game")].append(info)
    sport = dfs_sport.get(R.SPORT)
    out = {"gid": gid, "lock": lock, "date": None, "scan": scan[0], "games": len(games),
           "unpriced": sorted(g["matchup"] for gid_, g in games.items()
                              if gid_ not in set(ev_game.values())),
           "subjects": 0, "joined": 0, "missed": [], "wrong": [],
           "alias": stats.get("name_alias", [])}
    with PROJ_LOG.open(newline="") as fh:
        out["date"] = next((r["date"] for r in csv.DictReader(fh) if r.get("gid") == str(gid)),
                           lock[:10])
    for ev, game in ev_game.items():
        players = by_event[ev]
        sp = spelled.get(ev, {})
        joined = {k: joined_ids[id(bm)] for k, bm in players.items() if id(bm) in joined_ids}
        tmap = team_map(sp, joined)
        for k in sorted(players):
            s = sp.get(k, k)
            out["subjects"] += 1
            m = _SUFFIX.search(s)
            team = tmap.get(m.group(1)) if m else None
            if k in joined:
                out["joined"] += 1
                if team and team != joined[k].get("team"):
                    out["wrong"].append((s, joined[k]["name"], joined[k].get("team")))
                continue
            info = by_key.get(k)
            why = ("ambiguous" if k in stats.get("ambiguous_names", []) else
                   "no_key" if not info else "refused")
            bare = _SUFFIX.sub("", s).strip()
            # the most similar DK name on HIS team (book suffix -> DK team), else the game
            pool = [i for i in roster[game] if i.get("team") == team] or roster[game]
            cand = max(pool, key=lambda i: difflib.SequenceMatcher(
                None, norm(bare), norm(i["name"])).ratio())
            ratio = difflib.SequenceMatcher(None, norm(bare), norm(cand["name"])).ratio()
            proj = None
            if players[k]:
                proj = R.project_player(players[k], sport,
                                        position=cand.get("position")).get("proj")
            out["missed"].append({
                "why": why, "subject": s, "cand": cand["name"], "team": cand.get("team"),
                "pos": cand.get("position"), "salary": cand.get("salary"), "ratio": ratio,
                "proj": proj, "fpts": fpts.get(gid, {}).get(norm(cand["name"]))})
    return out


def report(results: list[dict], cutoff: str, verbose: bool) -> None:
    for name, part in (("TRAIN", [r for r in results if r["date"] < cutoff]),
                       ("TEST (held out)", [r for r in results if r["date"] >= cutoff])):
        print(f"\n{name}: {len(part)} slates")
        print(f"  {'slate':<20}{'games':>6}{'unpr':>5}{'subj':>6}{'join':>6}{'miss':>5}"
              f"{'recall':>8}{'wrong':>6}  misses (proj -> DK fpts)")
        tot = collections.Counter()
        for r in part:
            tot.update(subjects=r["subjects"], joined=r["joined"], missed=len(r["missed"]),
                       wrong=len(r["wrong"]))
            rec = r["joined"] / r["subjects"] if r["subjects"] else float("nan")
            short = "; ".join(f"{m['why']} {m['subject']}" for m in r["missed"])
            print(f"  {r['date']} {r['gid']:<9}{r['games']:>6}{len(r['unpriced']):>5}"
                  f"{r['subjects']:>6}{r['joined']:>6}{len(r['missed']):>5}{rec:>8.3f}"
                  f"{len(r['wrong']):>6}  {short}")
            if verbose:
                for m in r["missed"]:
                    print(f"      {m['why']:<9} {m['subject']:<30} ~ {m['cand']} ({m['team']} "
                          f"{m['pos']} ${m['salary']}, sim {m['ratio']:.2f}) proj "
                          f"{m['proj'] if m['proj'] is None else round(m['proj'], 1)}"
                          f" -> fpts {m['fpts']}")
                for w in r["wrong"]:
                    print(f"      WRONG? {w}")
                for al in r["alias"]:
                    print(f"      alias  {al}")
                if r["unpriced"]:
                    print(f"      unpriced games: {r['unpriced']}")
        if tot["subjects"]:
            lost = [m for r in part for m in r["missed"]]
            print(f"  total: subjects {tot['subjects']}, joined {tot['joined']}, missed "
                  f"{tot['missed']} (recall {tot['joined'] / tot['subjects']:.4f}), wrong "
                  f"{tot['wrong']}; projected pts lost "
                  f"{sum(m['proj'] or 0 for m in lost):.1f}, DK fpts of the missed "
                  f"{sum(m['fpts'] or 0 for m in lost):.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    rp.add_argument("--gid", type=int, action="append")
    rp.add_argument("--arch", help="directory holding local copies of the F: odds archive")
    rp.add_argument("-v", "--verbose", action="store_true")
    rp.add_argument("--json", help="also write the per-slate results here")
    a = ap.parse_args()

    store = OddsStore()
    if a.arch:
        orig = store.archived_file

        def local(scan_id, _orig=orig, _dir=Path(a.arch)):
            p = _orig(scan_id)
            return _dir / p.name if p is not None and (_dir / p.name).exists() else p
        store.archived_file = local
    fpts = truth()
    results = []
    for gid in a.gid or slates():
        r = replay(gid, store, fpts)
        if r is None:
            print(f"{gid}: no draftables snapshot or scan before lock -- skipped", file=sys.stderr)
            continue
        results.append(r)
    results.sort(key=lambda r: (r["date"], r["gid"]))
    report(results, a.cutoff, a.verbose)
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
