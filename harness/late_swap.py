"""Late swap: re-optimise an entered lineup after late news, game by game.

    python3 -m harness late-swap nhl                      # today's logged GPP lineup, Main slate
    python3 -m harness late-swap nhl --improve            # ...and re-optimise the healthy slots
    python3 -m harness late-swap nhl --gid 154733 --players "Leo Carlsson, ..." --mode gpp
    python3 -m harness late-swap nhl --entry my_lineup.csv  # the app's ⬇️ CSV works as-is
    python3 -m harness late-swap mlb ...                  # hands off to scripts/dfs_swap.py

NHL first because its late news is binary and decisive: a projected starting
goalie who sits scores zero in the G slot. The slate is REBUILT from scratch
(fresh DailyFaceoff goalies and lines, fresh props), so the board only holds
players who are dressing and only each team's confirmed/projected starter.
Then, for the lineup you entered:

  * a player NOT on the fresh board is OUT -- he must be replaced if his game
    has not started; if it has, he is stuck and says so;
  * a player whose game has started is LOCKED (DK locks at each game's start);
  * OUT slots are filled with the best dressing player of the same position
    (any skater for a skater) whose game has not started, keeping the lineup
    DK-legal (positions, cap, 3 teams / 2 games, no skater facing your goalie).
    That is all by default: a late swap fixes what broke and keeps your
    leverage. `--improve` also hill-climbs the healthy unlocked slots on the
    lineup's objective (95th percentile of its simulated total for GPP, 25th
    for cash) -- which ignores ownership, so in a GPP it drifts toward chalk.

The result prints the swaps and writes data/harness/late_swap_nhl_<gid>.csv.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from harness.registry import ROOT
from harness.runner import STATE

ET = ZoneInfo("America/New_York")


def _norm(name: str) -> str:
    sys.path.insert(0, str(ROOT))
    from edge.names import norm
    return norm(name)


def entered_names(players: str | None, entry: str | None, date: str, mode: str) -> list[str]:
    """The lineup to repair: --players, an --entry CSV (the app's download or
    any CSV with a player column, optionally a mode column), or the lineup the
    autolog/app logged for today."""
    if players:
        return [p.strip() for p in players.split(",") if p.strip()]
    path = Path(entry) if entry else ROOT / "data" / f"dfs_lineups_nhl_{date}.csv"
    if not path.exists():
        raise SystemExit(f"no lineup: pass --players or --entry (looked for {path})")
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    if rows and "mode" in rows[0]:
        rows = [r for r in rows if r.get("mode") == mode]
    names = [r.get("player") or r.get("Name") or "" for r in rows]
    return [n for n in names if n]


def _pos(dk_position: str | None) -> str:
    p = (dk_position or "").split("/")[0].upper()
    return "W" if p in ("LW", "RW", "W") else (p[:1] or "W")


def swap(pool: list, sim: np.ndarray, entered: list[str], started_teams: set,
         mode: str = "gpp", dk_info: dict | None = None, improve: bool = False) -> dict:
    """The repaired lineup. `entered` are names; `started_teams` the teams
    whose game is under way (DK locks each player at his game's start);
    `dk_info` {norm name: {salary, pos, team}} for players no longer on the
    board, so their salary and slot are known. By default only OUT players
    are replaced; `improve` also re-optimises every unlocked slot (it ignores
    ownership, so in a GPP it pulls a lineup toward the chalk)."""
    from edge import dfs_nhl_theory as theory, dfs_opt_nhl as opt, nhl
    ix = {_norm(p["name"]): i for i, p in enumerate(pool)}
    keep, out, stuck = [], [], []
    for name in entered:
        i = ix.get(_norm(name))
        if i is not None:
            keep.append(i)
        else:
            info = (dk_info or {}).get(_norm(name), {})
            (stuck if info.get("team") in started_teams else out).append(
                {"name": name, "pos": info.get("pos"), "salary": info.get("salary", 0)})
    locked = {i for i in keep if pool[i]["team"] in started_teams}
    stuck_salary = sum(o["salary"] for o in stuck)
    q = theory.GPP_PCT if mode == "gpp" else theory.CASH_PCT
    free = [i for i, p in enumerate(pool) if p["team"] not in started_teams and p.get("salary")]

    def legal(idx):
        if stuck:      # a stuck player still holds a slot: check what can be checked
            return (len(set(idx)) == len(idx)
                    and sum(pool[i]["salary"] for i in idx) + stuck_salary <= nhl.SALARY_CAP)
        return opt._valid(pool, list(idx))

    def value(idx):
        return float(np.percentile(sim[:, list(idx)].sum(axis=1), q))

    cur = list(keep)
    moves: list[tuple] = []
    # Fill OUT slots with the best candidate of the same position (a goalie
    # for a goalie); the last one filled must leave a DK-legal lineup.
    for n, o in enumerate(out):
        last = n == len(out) - 1
        cands = sorted((j for j in free if j not in cur and pool[j]["pos"] == o["pos"]),
                       key=lambda j: -float(np.percentile(sim[:, j], q)))
        if o["pos"] != "G":           # he may have been the UTIL: any skater can do
            cands += sorted((j for j in free if j not in cur and j not in cands
                             and pool[j]["pos"] != "G"),
                            key=lambda j: -float(np.percentile(sim[:, j], q)))
        spent = sum(pool[i]["salary"] for i in cur) + stuck_salary
        left = len(out) - n - 1
        for j in cands:
            if spent + pool[j]["salary"] + left * 2500 > nhl.SALARY_CAP:
                continue
            if last and not legal(cur + [j]):
                continue
            cur.append(j)
            moves.append((o["name"], pool[j]["name"], "out"))
            break
    complete = len(cur) + len(stuck) == opt.ROSTER_SIZE
    best = value(cur) if cur else 0.0
    improved = complete and improve
    while improved:
        improved = False
        for k, i in enumerate(cur):
            if i in locked:
                continue
            for j in free:
                if j in cur or (pool[j]["pos"] == "G") != (pool[i]["pos"] == "G"):
                    continue
                trial = cur[:k] + [j] + cur[k + 1:]
                if not legal(trial):
                    continue
                v = value(trial)
                if v > best + 0.05:
                    best, cur = v, trial
                    moves.append((pool[i]["name"], pool[j]["name"], "better"))
                    improved = True
                    break
            if improved:
                break
    final = [pool[i] for i in cur]
    return {"lineup": final, "slots": opt.assign_slots(final) if complete and not stuck else None,
            "swaps": _chain(moves), "stuck": [s["name"] for s in stuck],
            "out": [o["name"] for o in out], "locked": [pool[i]["name"] for i in locked],
            "before": value(keep) if keep else 0.0, "after": best, "complete": complete,
            "salary": sum(p["salary"] for p in final) + stuck_salary, "mode": mode}


def _chain(moves: list[tuple]) -> list[tuple]:
    """Collapse A->B, B->C into A->C: what to change on DK, not search steps."""
    out: list[list] = []
    for a, b, why in moves:
        for m in out:
            if m[1] == a:
                m[1] = b
                break
        else:
            out.append([a, b, why])
    return [tuple(m) for m in out if m[0] != m[1]]


def nhl_main(args) -> int:
    sys.path.insert(0, str(ROOT))
    from edge import dfs, dfs_run_nhl as R, nhl
    date = datetime.now(ET).date().isoformat()
    groups = R.classic_groups(dfs.draft_groups(nhl.DK_SPORT))
    gid = args.gid or (groups[R.default_slate(groups)]["gid"] if groups else None)
    if gid is None:
        print("no NHL slate")
        return 1
    names = entered_names(args.players, args.entry, date, args.mode)
    print(f"rebuilding NHL draft group {gid} with fresh goalies, lines and props…")
    res = R.build_slate(draft_group=gid, n_sims=args.sims, iters=40, persist=False)
    if res.get("error") or res.get("unpriced"):
        print(res.get("error") or "slate not priced")
        return 1
    now = datetime.now(timezone.utc)
    started = set()
    for g in nhl.schedule(date):
        if g.get("start") and datetime.fromisoformat(g["start"].replace("Z", "+00:00")) <= now:
            started |= {g["home"], g["away"]}
    dk = {}
    try:
        for d in dfs.fetch_draftables(gid).values():
            dk[_norm(d.get("name") or "")] = {"salary": d.get("salary") or 0,
                                              "pos": _pos(d.get("position")), "team": d.get("team")}
    except Exception:                                       # noqa: BLE001
        pass
    r = swap(res["pool"], res["sim"], names, started, mode=args.mode, dk_info=dk,
             improve=args.improve)
    print(f"\n{args.mode.upper()} lineup, {len(started)} game(s) started · "
          f"{args.mode} objective {r['before']:.1f} → {r['after']:.1f}")
    if r["out"]:
        print("  OUT (not on tonight's board — scratched or not starting): " + ", ".join(r["out"]))
    if r["stuck"]:
        print("  STUCK (out, but his game already started): " + ", ".join(r["stuck"]))
    if r["locked"]:
        print("  locked (game started): " + ", ".join(r["locked"]))
    if not r["swaps"]:
        print("  no swaps needed." + ("" if args.improve else
                                       " (--improve also re-optimises the healthy slots)"))
    for a, b, why in r["swaps"]:
        print(f"  SWAP  {a}  →  {b}" + ("   (he is out)" if why == "out" else ""))
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / f"late_swap_nhl_{gid}.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["slot", "player", "team", "salary", "proj", "own"])
        for slot, p in (r["slots"] or [("", p) for p in r["lineup"]]):
            w.writerow([slot, p["name"], p["team"], p["salary"], p["proj"], p.get("own")])
    print(f"\n  ${r['salary']:,} / 50,000 · written to {path.relative_to(ROOT)}")
    return 0


def main(args) -> int:
    if args.sport == "nhl":
        return nhl_main(args)
    if args.sport == "mlb":
        return subprocess.call([sys.executable, "scripts/dfs_swap.py", *args.rest], cwd=ROOT)
    print(f"late swap for {args.sport} is not built yet (NHL and MLB are)")
    return 2
