#!/usr/bin/env python3
"""College held-out lab: the SHIPPED board replayed on every graded slate, per slate.

    python3 scripts/ncaab_lab.py report            # shipped constants + leave-one-slate-out refit
    python3 scripts/ncaab_lab.py rows out.json     # every player-slate row, for a critic's own tests
    python3 scripts/ncaab_lab.py gate out.jsonl    # LOSO rows for the critique loop's gate

What "the shipped board" is here. The logged pre-lock projection (data/dfs_proj_log_ncaab.csv,
one row per player per draft group) as it ran -- before LAST_GAME: from 11/7 the log already
has it, and `base_projection` takes it back out (BAKED, below) --, then LAST_GAME (with tonight's implied team
points: the log's `implied` column, or data/ncaab_implied_backfill.json for slates logged
before it existed) applied exactly as
edge/dfs_run_ncaab.build_board applies it: the same last_game_lines (ESPN, games a day
before the slate, the >= 50% roster check) and the same apply_last_game. Truth is the ESPN
box (scripts/ncaab_box_grade.join: draft group + edge.names.norm, undrafted included, a
player missing from the box scored 0).

What it is NOT. 11/2-11/4 (155565, 156039, 156123) were logged before the 11/5 refit of
BLEND_RESULT / SALARY_TIER_LINE, so their blend and salary rows run the older, higher
model (priced bias +2.6..+3.4 then, -0.1/-0.8 after). LAST_GAME's shipped constants were
fitted on 11/3-11/6, so the "shipped" table is in sample for them; the "loso" table refits
LAST_GAME on the other slates each time and is the honest one. The slate is the unit:
under ~5 slates nothing here is called proven.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

import ncaab_box_grade as grader  # noqa: E402
from edge import dfs_run_ncaab as RC  # noqa: E402
from edge import ncaab_torvik  # noqa: E402
from edge.ncaab_names import Rosters  # noqa: E402

PRICED = ("props", "blend", "salary")
#: Implied team points for slates logged before the log had an `implied` column (cycle 2).
BACKFILL = ROOT / "data" / "ncaab_implied_backfill.json"

#: LAST_GAME AS IT RAN, for slates whose logged `proj` ALREADY HAS IT (critique cycle 3,
#: 2026-11-08). LAST_GAME shipped 11/7 14:27 ET; every build since logs the moved
#: projection, and this lab used to apply LAST_GAME to it a second time. On 11/7 (156344,
#: built 14:49 ET with cycle 1's constants) the bench rows whose team's last game gave them
#: 20+ minutes were logged at 15.5 points and replayed at ~5 -- the lab was grading a board
#: that never ran. The fix recovers the pre-LAST_GAME projection by inverting the affine
#: step with the constants that were live at the build (b > 0, a > 0, so it never clipped):
#:   - a `base_proj` column (logged since this cycle): exact, used as is;
#:   - 156344: cycle 1, (0.048, 0.359, 0.190, 0.374), no implied-points factor;
#:   - any other row with an `implied` column (cycle 2's log, 11/7 18:03 ET on): cycle 2,
#:     (0.029, 0.298, 0.128, 0.449) with the minutes term x implied / LEAGUE.team_points.
#: The inverted base carries the sim's noise / b (~0.6 points on a moved row; the logged
#: proj is a 2,000-sim mean) and comes back to within ~0.2 when re-applied. If LAST_GAME's
#: constants change on main before base_proj is logged, add that window here.
BAKED = {156344: ((0.048, 0.359, 0.190, 0.374), False)}
CYCLE2 = ((0.029, 0.298, 0.128, 0.449), True)


def _baked(gid: int, logged_implied) -> tuple | None:
    """(LAST_GAME constants, env on) already in a row's logged proj, or None. The log's
    `implied` COLUMN dates the build: cycle 2 added it with the implied-points factor."""
    if gid in BAKED:
        return BAKED[gid]
    if logged_implied not in (None, ""):
        return CYCLE2
    return None


def base_projection(gid: int, r: dict, logged_implied) -> float:
    """The pre-LAST_GAME projection for a loaded row (`last_min`/`last_fp`/`implied`
    resolved; `proj` as logged; `logged_implied` = the raw log column)."""
    if r["source"] == "props" or r["last_min"] is None:
        return r["proj"]                    # LAST_GAME never moves these rows
    if r.get("base_proj") not in (None, ""):
        return float(r["base_proj"])
    baked = _baked(gid, logged_implied)
    if baked is None:
        return r["proj"]
    (a, b, c, d), env_on = baked
    env = _env(r) if env_on else 1.0
    return (r["proj"] - a - c * r["last_fp"] - d * min(r["last_min"], RC.LAST_GAME_MIN_CAP) * env) / b


def slates() -> list[int]:
    """Draft groups with both a logged build and a saved box."""
    gids = sorted(int(p.stem) for p in grader.BOX_DIR.glob("*.json") if p.stem.isdigit())
    return [g for g in gids if grader.logged(g)]


def load(gid: int) -> list[dict]:
    """Logged rows with truth (act, min, started) and the team's last game (last_min,
    last_fp, None when the team had none), read the way build_board reads it."""
    rows = grader.logged(gid)
    box = json.loads((grader.BOX_DIR / f"{gid}.json").read_text())
    rows, _ = grader.join(rows, box)
    # One key per ROW (grader.keys), as the live board keeps two same-name teammates apart:
    # keyed by name|team, the second Christian Jones on 156660 (LBS, 11/16) fell out of this
    # dict, never got last_min, and the gate crashed the loop 3 nights (11/21, 11/25 x2). Both
    # stay in, so last_game_lines finds the ESPN name ambiguous and gives each 0 minutes --
    # exactly what the board did that night. A DK mislisting (grader.MISLISTED) is graded 0.
    salaries = {k: {"name": r["player"], "team": r["team"], "row": r}
                for k, r in zip(grader.keys(rows), rows)}
    teams = {r["team"] for r in rows}
    last, _audit = RC.last_game_lines(salaries, Rosters(salaries), teams, rows[0]["date"])
    backfill = json.loads(BACKFILL.read_text()).get(str(gid), {}) if BACKFILL.exists() else {}
    for key, info in salaries.items():
        r = info["row"]
        m, fp, _d = last.get(key, (None, None, None))
        logged_imp = r.get("implied")
        imp = float(logged_imp) if logged_imp not in (None, "") else backfill.get(r["team"])
        r.update(gid=gid, last_min=m, last_fp=fp, source=r["proj_source"], implied=imp,
                 logged_proj=r["proj"])
        r["proj"] = base_projection(gid, r, logged_imp)
    # Was DK's FPPG this season's on this slate? The board's own switch (RC.season_fppg_state)
    # on the logged rows, joined to Torvik the way build_board joins them (cycle 8, 11/25).
    sal = {k: {"name": r["player"], "team": r["team"], "salary": r["salary"],
               "dk_fppg": _num(r.get("dk_fppg"))} for k, r in zip(grader.keys(rows), rows)}
    history, _ = ncaab_torvik.join(sal, _torvik())
    fresh = RC.season_fppg_state(sal, history)["fresh"]
    for k, r in zip(grader.keys(rows), rows):
        r.update(dk_fppg=sal[k]["dk_fppg"], fppg_fresh=fresh)
    unknown = [r for r in rows if r["namesake"]]
    if unknown:                     # truth unknowable: say so, don't guess
        print(f"ncaab_lab: {gid} leaves out same-name teammates with one box line: "
              + ", ".join(sorted({f"{r['player']} ({r['team']})" for r in unknown})), file=sys.stderr)
    return [r for r in rows if not r["namesake"]]


def _env(r: dict) -> float:
    return (r["implied"] or RC.LEAGUE.team_points) / RC.LEAGUE.team_points


def _num(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


_TORVIK: list = []


def _torvik():
    if not _TORVIK:
        _TORVIK.append(ncaab_torvik.load())
    return _TORVIK[0]


def shipped(rows: list[dict], coef: tuple | None = None, season: tuple | None = None) -> list[float]:
    """The board's projection for each row of ONE slate: as logged, then LAST_GAME, then
    SEASON_FPPG when that slate's DK FPPG is this season's (`coef` / `season` to override)."""
    tmp = [{"source": r["source"], "fp": r["proj"], "last_min": r["last_min"], "last_fp": r["last_fp"],
            "team": r["team"], "dk_fppg": r.get("dk_fppg")} for r in rows]
    RC.apply_last_game(tmp, coef, {r["team"]: r["implied"] for r in rows if r["implied"]})
    if rows and rows[0].get("fppg_fresh"):
        RC.apply_season_fppg(tmp, season)
    return [t["fp"] for t in tmp]


def fit_season(data: dict, lg: tuple) -> tuple:
    """SEASON_FPPG's least squares (how the shipped constants were fit): on the fresh slates'
    non-props rows with a DK FPPG, against the board after LAST_GAME (`lg`)."""
    X, y = [], []
    for rows in data.values():
        if not (rows and rows[0].get("fppg_fresh")):
            continue
        for r, p in zip(rows, shipped(rows, lg, season=(0.0, 1.0, 0.0))):
            if r["source"] != "props" and (r.get("dk_fppg") or 0) > 0:
                X.append([1.0, p, r["dk_fppg"]])
                y.append(r["act"])
    if len(y) < 50:                     # too few fresh rows to fit: the layer stays off
        return (0.0, 1.0, 0.0)
    return tuple(float(c) for c in np.linalg.lstsq(np.array(X), np.array(y), rcond=None)[0])


def held_out(data: dict, gid: int) -> list[float]:
    """Slate `gid` with LAST_GAME and SEASON_FPPG both refit on the OTHER slates: the honest
    LOSO baseline the report, the gate and the lineup lab all score."""
    others = {h: rs for h, rs in data.items() if h != gid}
    lg = fit_last_game([r for rs in others.values() for r in rs])
    return shipped(data[gid], lg, fit_season(others, lg))


def fit_last_game(rows: list[dict]) -> tuple:
    """LAST_GAME's least squares on the as-run projection (how the shipped constants were fit)."""
    use = [r for r in rows if r["source"] != "props" and r["last_min"] is not None]
    X = np.array([[1.0, r["proj"], r["last_fp"], min(r["last_min"], RC.LAST_GAME_MIN_CAP) * _env(r)]
                  for r in use])
    return tuple(float(c) for c in np.linalg.lstsq(X, np.array([r["act"] for r in use]), rcond=None)[0])


def metrics(rows: list[dict], proj: list[float]) -> dict:
    def part(sel):
        pr = [(p, r["act"]) for p, r in zip(proj, rows) if sel(r)]
        if not pr:
            return {"n": 0}
        e = [p - a for p, a in pr]
        out = {"n": len(pr), "mae": statistics.mean(abs(x) for x in e), "bias": statistics.mean(e)}
        if len(pr) > 2:
            out["rho"] = float(spearmanr([p for p, _ in pr], [a for _, a in pr])[0])
        return out
    return {"all": part(lambda r: True), "priced": part(lambda r: r["source"] in PRICED),
            "bench": part(lambda r: r["source"] == "bench"),
            "moved": sum(r["source"] != "props" and r["last_min"] is not None for r in rows)}


def _line(gid: int, date: str, m: dict) -> str:
    a, p, b = m["all"], m["priced"], m["bench"]
    return (f"  {gid} {date}  all n={a['n']:>3} MAE {a['mae']:5.2f} bias {a['bias']:+5.2f} | "
            f"priced n={p['n']:>3} MAE {p['mae']:5.2f} bias {p['bias']:+5.2f} rho {p.get('rho', float('nan')):.3f} | "
            f"bench MAE {b['mae']:5.2f} bias {b['bias']:+5.2f} | moved {m['moved']}")


def report() -> int:
    data = {g: load(g) for g in slates()}
    print(f"{len(data)} graded slates: " + ", ".join(str(g) for g in data)
          + ("" if len(data) >= 5 else "  -- not enough data (under 5 slates)"))
    print("\nAS RUN (logged projection, before LAST_GAME):")
    for g, rows in data.items():
        print(_line(g, rows[0]["date"], metrics(rows, [r["proj"] for r in rows])))
    print(f"\nSHIPPED (LAST_GAME {RC.LAST_GAME}, in sample for 11/3-11/6; SEASON_FPPG {RC.SEASON_FPPG}"
          f" on fresh slates, in sample from 11/9):")
    for g, rows in data.items():
        print(_line(g, rows[0]["date"], metrics(rows, shipped(rows))))
    print("\nLOSO (LAST_GAME + SEASON_FPPG refit on the other slates each time): the held-out baseline")
    pooled = []
    for g, rows in data.items():
        proj = held_out(data, g)
        pooled += list(zip(proj, rows))
        print(_line(g, rows[0]["date"], metrics(rows, proj)))
    m = metrics([r for _, r in pooled], [p for p, _ in pooled])
    print(f"  pooled  all MAE {m['all']['mae']:.3f} | priced MAE {m['priced']['mae']:.3f} "
          f"bias {m['priced']['bias']:+.2f} | bench MAE {m['bench']['mae']:.3f}")
    return 0


def dump(path: str) -> int:
    out = []
    for g in slates():
        rows = load(g)
        for r, p in zip(rows, shipped(rows)):
            out.append({k: r.get(k) for k in ("gid", "date", "player", "team", "opp", "salary", "proj",
                                              "proj_source", "status", "starter", "own", "act", "min",
                                              "started", "last_min", "last_fp", "implied")} | {"shipped": p})
    Path(path).write_text(json.dumps(out))
    print(f"{len(out)} rows -> {path}")
    return 0


def gate(path: str) -> int:
    """Rows for the unattended critique loop's gate (harness/critique_gate.py): the LOSO
    table's projections (the honest held-out baseline) and actual DK points per
    player-slate (the gate scores absolute error), clustered by slate. Every graded slate is primary: the slate is the unit."""
    data = {g: load(g) for g in slates()}
    n = 0
    with open(path, "w") as fh:
        for g, rows in data.items():
            # The gate pairs base and candidate rows by key (a dict): two same-name teammates
            # (156660's Christian Joneses) need their salary in it or one silently replaces the other.
            dup = {k for k, n in collections.Counter((r["team"], r["player"]) for r in rows).items() if n > 1}
            for p, r in zip(held_out(data, g), rows):
                key = f"{g}|{r['team']}|{r['player']}"
                if (r["team"], r["player"]) in dup:
                    key += f"|{int(r['salary'])}"
                fh.write(json.dumps({"key": key, "cluster": str(g),
                                     "split": "primary", "state": "all", "pred": float(p),
                                     "actual": float(r["act"])}) + "\n")
                n += 1
    print(f"{n:,} gate rows ({len(data)} slates) -> {path}")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "report":
        return report()
    if argv[0] == "rows" and len(argv) == 2:
        return dump(argv[1])
    if argv[0] == "gate" and len(argv) == 2:
        return gate(argv[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
