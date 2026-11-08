#!/usr/bin/env python3
"""NBA minutes in the state production mostly builds in: the five as NBA.com showed it
`cutoff` minutes before tip (usually EXPECTED, not yet confirmed), replayed through the
PRODUCTION per-team step (edge/dfs_run_nba.team_minutes -> NM.project_uncertain ->
project() with the ML correction). Critique cycle 25, 2026-11-07.

    python3 scripts/nba_expected_replay.py report [--cutoff 45]     # EXPECTED_HEDGE off vs on
    python3 scripts/nba_expected_replay.py gate out.jsonl           # rows for harness/critique_gate.py

Why it exists. The lab (nba_minutes_lab.py) and the ML fit score two states: the five
known (the TRUE starters) and no five. In 2026-27 the live board is built ~45 min before
tip (median of the logged builds) when the five is usually NBA.com's EXPECTED one --
wrong in ~30% of team-games -- and 71% of the live board's squared error sat on rows
built that way (critic, cycle 25). Only 2026-27 has the inputs: data/nba_lineup_log.csv
(every lineup snapshot, timestamped) starts with this season.

What is replayed, per 2026-27 regular-season team-game in the lab (histories from earlier
games only, as the lab builds them):
  - the five: the lineup log's last 5-starter snapshot at or before tip - cutoff (tips:
    data/nba_ground_truth/schedule_tips.json, Eastern clock), per player Confirmed /
    Expected as logged; fewer than 5 starters = no five;
  - Out / Doubtful / Questionable / Probable: the official injury report (cache,
    edge.nba_injury_report) latest at or before the cutoff, 15-minute steps back up to 4 h;
    joined on edge.names.norm. ESPN / DK statuses are not archived: the report stands in;
  - "stale": sat his team's previous game; no inactive list yet (it posts ~30 min out),
    so team_minutes' roster cut runs as it does live.
Truth: box-score minutes, a player who did not dress = 0 (board scoring: the hedge is
about who dresses). Every roster row of the team-game is scored, projected 0 if cut / Out.
Clusters for the paired bootstrap: the night.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import nba_minutes_lab as L                        # noqa: E402
import nba_minutes_ml_fit as FIT                   # noqa: E402
from edge import dfs_run_nba as RUN                # noqa: E402
from edge import nba_injury_report as IR           # noqa: E402
from edge import nba_minutes as NM                 # noqa: E402
from edge.names import norm                        # noqa: E402

ET = ZoneInfo("America/New_York")
LOG = ROOT / "data" / "nba_lineup_log.csv"
TIPS = ROOT / "data" / "nba_ground_truth" / "schedule_tips.json"
SEASON = "2026-27"


def lineup_snapshots() -> dict:
    """{(game id, team): [(seen_at UTC, {player id: (starter, confirmed)})]} sorted."""
    by = defaultdict(lambda: defaultdict(dict))
    with LOG.open() as fh:
        for r in csv.DictReader(fh):
            if not r["game_id"].startswith("002"):
                continue
            t = datetime.fromisoformat(r["seen_at"])
            by[(r["game_id"], r["team"])][t][r["player_id"]] = (r["starter"] == "True", r["confirmed"] == "True")
    return {k: sorted(v.items()) for k, v in by.items()}


_REPORTS: dict = {}


def report_at(t_et: datetime) -> dict | None:
    """{norm name: status} from the latest cached official report at or before t."""
    for back in range(0, 17):
        tt = t_et - timedelta(minutes=15 * back)
        got = IR.report(tt.date().isoformat(), tt.hour, (tt.minute // 15) * 15, fetch=False)
        if got:
            if got[0] not in _REPORTS:
                _REPORTS[got[0]] = {norm(IR.full_name(a, b)): st for a, b, st, _ in IR.parse(got[1])["rows"]}
            return _REPORTS[got[0]]
    return None


def names() -> dict:
    out = {}
    for f in sorted(L.CACHE.glob("*.json")):
        try:
            for g_ in L.parse(json.loads(f.read_text())):
                for p_ in g_["players"]:
                    out[str(p_["id"])] = p_["name"]
        except (ValueError, KeyError):
            continue
    return out


def team_games(D: dict, cutoff: int) -> list[dict]:
    """The board rows team_minutes takes, per replayable team-game, + truth."""
    snaps, tips, nm = lineup_snapshots(), json.loads(TIPS.read_text()), names()
    prev = L._prev_inactive(D)
    rows, out = D["rows"], []
    for g in D["games"]:
        if g["season"] != SEASON or g["gid"] not in tips:
            continue
        tip = datetime.fromisoformat(tips[g["gid"]].replace("Z", "")).replace(tzinfo=ET)
        cut = tip - timedelta(minutes=cutoff)
        # the log interleaves five snapshots with bench / roster ones (0 starters): the
        # five as it stood = the last snapshot at or before the cutoff that lists 5 starters
        snap = [s for t, s in snaps.get((g["gid"], g["team"]), [])
                if t <= cut and sum(st for st, _ in s.values()) == 5]
        five = {pid: c for pid, (st, c) in (snap[-1] if snap else {}).items() if st}
        rep = report_at(cut)
        if rep is None:
            continue
        pids = {rows[i]["pid"]: i for i in g["idx"]}
        if len(five) == 5 and not set(five) <= set(pids):
            continue                                    # the five did not join the box roster
        announced = len(five) == 5
        state = ("confirmed" if all(five.values()) else "expected") if announced else "none"
        team = {x["id"]: x for x in FIT.team_input(D, g, "inactives")}
        ps = []
        for i in g["idx"]:
            r, x = rows[i], team[i]
            st = rep.get(norm(nm.get(r["pid"], "")))
            ps.append({"name": i, "out": st == "Out", "inactive_listed": False,
                       "starter": r["pid"] in five if announced else False,
                       "confirmed": five.get(r["pid"], False) if announced else None,
                       "injury": st, "missed_last": bool(prev.get(i)), "nba_pos": x["position"],
                       "window": NM.SEASON_START_WINDOW if r["season_games"] < 5 else None,
                       "history": [(m, s, None) for m, s in x["history"]], "cur_n": x["cur_n"],
                       "n_pre": x["n_pre"], "since_return": x["since_return"], "pick": x["pick"],
                       "salary": None,
                       "actual": 0.0 if r["actual"] is None else float(r["actual"])})
        out.append({"gid": g["gid"], "team": g["team"], "date": g["date"], "state": state, "ps": ps})
    return out


def predict(tg: list[dict], hedge: bool) -> list[dict]:
    """[{key, cluster, state, pred, actual, exp_q}] for every roster row."""
    RUN.EXPECTED_HEDGE = hedge
    res = []
    for t in tg:
        ps = [dict(r) for r in t["ps"]]
        mins, _, _ = RUN.team_minutes(ps, True)
        for r in ps:
            pred = 0.0 if r["out"] or (r.get("not_dressing") and r["name"] not in mins) \
                else mins.get(r["name"], {}).get("minutes", 0.0)
            res.append({"key": f"{t['gid']}|{t['team']}|{r['name']}", "cluster": t["date"], "state": t["state"],
                        "pred": float(pred), "actual": r["actual"],
                        "exp_q": t["state"] == "expected" and bool(r["starter"]) and not r["confirmed"]
                        and r["injury"] in ("Doubtful", "Questionable", "Probable")})
    return res


def _m(e: np.ndarray) -> str:
    return f"{np.sqrt((e ** 2).mean()):.3f} / {np.abs(e).mean():.3f} / {e.mean():+.2f}" if len(e) else "-"


def lab(path: str | None = None) -> dict:
    """The lab's nights; `path` = a pickle of an earlier L.load() (the lab's own cache key
    moves with every 15-minute injury report, so a night of experiments loads once)."""
    if path:
        import pickle
        with open(path, "rb") as fh:
            return pickle.load(fh)
    return L.load(verbose=False)


def report(cutoff: int, lab_path: str | None = None) -> int:
    from harness.critique_gate import _boot
    D = lab(lab_path)
    tg = team_games(D, cutoff)
    a, b = predict(tg, False), predict(tg, True)
    print(f"# Expected-state replay, {SEASON}, five + report as of tip - {cutoff} min "
          f"({len(tg)} team-games, {len({t['date'] for t in tg})} nights)\n")
    print("| slice | team-games | rows | hedge off RMSE / MAE / bias | hedge on | sq. error diff per row [90%, by night] |")
    print("|---|---|---|---|---|---|")
    sl = {"all": lambda r: True, **{f"state {s}": (lambda s: lambda r: r["state"] == s)(s)
                                    for s in ("expected", "confirmed", "none")},
          "Expected starter on report": lambda r: r["exp_q"]}
    for name, f in sl.items():
        ix = [k for k, r in enumerate(a) if f(r)]
        if not ix:
            continue
        ea = np.array([a[k]["pred"] - a[k]["actual"] for k in ix])
        eb = np.array([b[k]["pred"] - b[k]["actual"] for k in ix])
        cl = np.array([a[k]["cluster"] for k in ix])
        d, lo, hi = _boot(cl, eb ** 2 - ea ** 2)
        n_tg = len({a[k]["key"].rsplit("|", 1)[0] for k in ix})
        print(f"| {name} | {n_tg} | {len(ix):,} | {_m(ea)} | {_m(eb)} | {d:+.3f} [{lo:+.3f}, {hi:+.3f}] |")
    return 0


def gate(path: str, cutoffs=(45, 90)) -> int:
    """Rows for harness/critique_gate.py: the shipped settings, every replayed row, per
    cutoff (45 = the median live build; 90 = an earlier phone build), state = the five's
    state at the cutoff."""
    D = L.load(verbose=False)
    n = 0
    with open(path, "w") as fh:
        for c in cutoffs:
            for r in predict(team_games(D, c), RUN.EXPECTED_HEDGE):
                fh.write(json.dumps({"key": f"{c}|{r['key']}", "cluster": r["cluster"], "split": "primary",
                                     "state": f"tip-{c} {r['state']}", "pred": r["pred"],
                                     "actual": r["actual"]}) + "\n")
                n += 1
    print(f"{n:,} gate rows -> {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--cutoff", type=int, default=45, help="minutes before tip (live builds: median 44)")
    r.add_argument("--lab", help="pickle of an earlier nba_minutes_lab.load()")
    g = sub.add_parser("gate")
    g.add_argument("out")
    a = ap.parse_args()
    return report(a.cutoff, a.lab) if a.cmd == "report" else gate(a.out)


if __name__ == "__main__":
    raise SystemExit(main())
