#!/usr/bin/env python3
"""College held-out lab: the SHIPPED board replayed on every graded slate, per slate.

    python3 scripts/ncaab_lab.py report            # shipped constants + leave-one-slate-out refit
    python3 scripts/ncaab_lab.py rows out.json     # every player-slate row, for a critic's own tests
    python3 scripts/ncaab_lab.py gate out.jsonl    # LOSO rows for the critique loop's gate

What "the shipped board" is here. The logged pre-lock projection (data/dfs_proj_log_ncaab.csv,
one row per player per draft group) as it ran, then LAST_GAME (with tonight's implied team
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
from edge.names import norm  # noqa: E402
from edge.ncaab_names import Rosters  # noqa: E402

PRICED = ("props", "blend", "salary")
#: Implied team points for slates logged before the log had an `implied` column (cycle 2).
BACKFILL = ROOT / "data" / "ncaab_implied_backfill.json"


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
    salaries = {f"{norm(r['player'])}|{r['team']}": {"name": r["player"], "team": r["team"], "row": r}
                for r in rows}
    teams = {r["team"] for r in rows}
    last, _audit = RC.last_game_lines(salaries, Rosters(salaries), teams, rows[0]["date"])
    backfill = json.loads(BACKFILL.read_text()).get(str(gid), {}) if BACKFILL.exists() else {}
    for key, info in salaries.items():
        r = info["row"]
        m, fp, _d = last.get(key, (None, None, None))
        imp = float(r["implied"]) if r.get("implied") not in (None, "") else backfill.get(r["team"])
        r.update(gid=gid, last_min=m, last_fp=fp, source=r["proj_source"], implied=imp)
    return rows


def _env(r: dict) -> float:
    return (r["implied"] or RC.LEAGUE.team_points) / RC.LEAGUE.team_points


def shipped(rows: list[dict], coef: tuple | None = None) -> list[float]:
    """The board's projection for each row: as logged, then LAST_GAME (`coef` to override)."""
    tmp = [{"source": r["source"], "fp": r["proj"], "last_min": r["last_min"], "last_fp": r["last_fp"],
            "team": r["team"]} for r in rows]
    RC.apply_last_game(tmp, coef, {r["team"]: r["implied"] for r in rows if r["implied"]})
    return [t["fp"] for t in tmp]


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
    print(f"\nSHIPPED (LAST_GAME {RC.LAST_GAME}; in sample for 11/3-11/6):")
    for g, rows in data.items():
        print(_line(g, rows[0]["date"], metrics(rows, shipped(rows))))
    print("\nLOSO (LAST_GAME refit on the other slates each time): the held-out baseline")
    pooled = []
    for g, rows in data.items():
        train = [r for h, rs in data.items() if h != g for r in rs]
        proj = shipped(rows, fit_last_game(train))
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
            train = [r for h, rs in data.items() if h != g for r in rs]
            for p, r in zip(shipped(rows, fit_last_game(train)), rows):
                fh.write(json.dumps({"key": f"{g}|{r['team']}|{r['player']}", "cluster": str(g),
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
