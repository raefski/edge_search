#!/usr/bin/env python3
"""NHL season lab: the 2025-26 season replayed through the skater projection, to
judge the parts of it no market prices (NHL_CRITIQUE_LOG.md cycle 3's rule).

    python3 scripts/nhl_season_lab.py report                      # shipped, both halves
    python3 scripts/nhl_season_lab.py report --variant no_role    # + a variant, paired
    python3 scripts/nhl_season_lab.py report --seeds 4

WHY A SECOND LAB
scripts/nhl_skater_lab.py replays our own 2026-27 slates with the real props, but
its 8 test nights resolve only ~+/-0.02 RMSE (cycle 3). A change to a component
no market prices -- blocks; a no-prop skater's goals, assists and shots; the
this-season role scaling; anything rink- or form-based -- can be judged on a whole
season instead: ~1,300 games, ~45,000 skater-games, ~190 game dates.

WHAT IS REPLAYED (each 2025-26 game, as of the morning of its date)
  dressed     the box score's skaters (the confirmed-lineup state; DailyFaceoff
              gives this before lock), 7+ D listed -> the 7-D scale
  base        2024-25 per-game rates (the 'last season' then), joined by NHL id
  role        this season's ice time from the box scores of earlier dates
  team goals  no odds exist before 2026-09-28, so a form estimate: each team's
              goals for and the opponent's goals against so far, shrunk to the
              league's to-date rate with TEAM_K games (shared by every variant)
  means       edge/dfs_run_nhl.skater_means with no props -- production's own
              recipe for a skater the books do not price
  simulate    edge/nhl_sim.simulate, one call per date (no lines / PP units: with
              the exact assist solve they move who assists with whom, not means)

Every skater is on the no-prop path here, so this lab speaks for that path (57%
of a 2026-27 board, mostly depth) and for blocks (everyone); a star's goals and
shots come from his props in production. Truth = box-score DK points.

THE SPLIT: H1 = dates before 2026-01-01 (fit), H2 = 2026-01-01 on (check). Game
dates are the bootstrap unit.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_run_nhl as R  # noqa: E402
from edge import nhl, nhl_sim  # noqa: E402
from scripts.contest_stats import cluster_bootstrap, spearman  # noqa: E402

BOX = ROOT / "data" / "nhl_cache" / "box"
SEASON, PRIOR = 2025, "20242025"
H2_FROM = "2026-01-01"
TEAM_K = 10            # games of league-average prior in each team's goals for / against
LEAGUE_K = 50          # team-games of prior in the league's to-date goals per team-game
#: The league's goals per team-game before any 2025-26 game: 2024-25 skater goals
#: 7,898 / (32 x 82) = 3.01 (season_skater_stats), +~0.03 for shootout winners in
#: the final score. A prior only: LEAGUE_K team-games wash it out in the first week.
LEAGUE_PRIOR = 3.04
POS = {"C": "C", "L": "W", "R": "W", "D": "D"}


def _toi(t: str | None) -> float:
    m, _, s = (t or "0:00").partition(":")
    return int(m) + int(s or 0) / 60


def load() -> dict:
    """{date: [game, ...]} for the regular season, each game with its skaters'
    box lines; plus 2024-25 rates by NHL id."""
    games = defaultdict(list)
    for gid in nhl.season_game_ids(SEASON):
        f = BOX / f"{gid}.json"
        if not f.exists():
            continue
        b = json.loads(f.read_text())
        if b.get("state") not in ("OFF", "FINAL"):
            continue
        games[b["date"]].append(b)
    rates = {pid: row for pid, r in nhl.season_skater_stats(PRIOR).items()
             if (row := R.rate_row(r)) is not None}
    return {"dates": dict(sorted(games.items())), "rates": rates}


# ──────────────────────────────────────────────────────────────────────────────
# variants: (ctx, prm) -> means. ctx = one skater on one date (see boards()).
# ──────────────────────────────────────────────────────────────────────────────
def season_sums(c: dict) -> tuple | None:
    """(games, goals, assists, shots, blocks) this season before the date -- what
    edge/dfs_run_nhl.current_production reads from the stats API live."""
    h = c["hist"]
    return (len(h), *(sum(x[i] for x in h) for i in (4, 5, 6, 7))) if h else None


def shipped(c: dict, prm: dict) -> dict:
    return R.skater_means(c["base"], c["pos"], c["scale"], toi_recent=c["toi_recent"],
                          seven_d=c["seven_d"], season=season_sums(c))[0]


VARIANTS: dict = {"shipped": shipped}


def variant(name):
    def wrap(fn):
        VARIANTS[name] = fn
        return fn
    return wrap


@variant("no_role")
def no_role(c, prm):
    """Cycle 1's ice-time scaling switched off."""
    return R.skater_means(c["base"], c["pos"], c["scale"], seven_d=c["seven_d"],
                          season=season_sums(c))[0]


@variant("no_season")
def no_season(c, prm):
    """Cycle 4's this-season production switched off (the recipe before it)."""
    return R.skater_means(c["base"], c["pos"], c["scale"], toi_recent=c["toi_recent"],
                          seven_d=c["seven_d"])[0]


# ──────────────────────────────────────────────────────────────────────────────
# replay
# ──────────────────────────────────────────────────────────────────────────────
def boards(D: dict):
    """Yield (date, sim games, [ctx, ...]) in date order, every ctx built only
    from games dated before it. ctx: id, name, team, opp, home, pos, base, scale,
    toi_recent (this season's minutes, oldest first), hist (this season's box
    lines before the date: (date, team, opp, home, goals, assists, shots, blocks,
    toi)), seven_d, lam (team goals), date, actual (DK points), parts."""
    hist: dict = defaultdict(list)
    gf, ga, gp = defaultdict(float), defaultdict(float), defaultdict(int)
    league_goals, league_games = 0.0, 0
    for date, day in D["dates"].items():
        lg = (league_goals + LEAGUE_K * LEAGUE_PRIOR) / (league_games + LEAGUE_K)

        def lam(t, o):
            att = (gf[t] + TEAM_K * lg) / (gp[t] + TEAM_K)
            dfn = (ga[o] + TEAM_K * lg) / (gp[o] + TEAM_K)
            return att * dfn / lg
        sim_games, ctxs = {}, []
        for b in day:
            h, a = b["home"], b["away"]
            lh, la = lam(h, a), lam(a, h)
            sim_games[str(b["id"])] = {"home": h, "away": a, "lam_home": lh, "lam_away": la}
            nd = defaultdict(int)
            for s in b["skaters"]:
                nd[s["team"]] += s.get("pos") == "D"
            for s in b["skaters"]:
                t = s["team"]
                home = t == h
                pos = POS.get(s.get("pos"), "W")
                ctxs.append({
                    "id": s["id"], "name": s["name"], "team": t, "opp": a if home else h, "home": home,
                    "pos": pos, "base": D["rates"].get(s["id"]) or {},
                    "scale": (lh if home else la) / R.LEAGUE_TEAM_GOALS,
                    "toi_recent": [x[8] for x in hist[s["id"]]] or None,
                    "hist": list(hist[s["id"]]), "seven_d": pos == "D" and nd[t] >= 7,
                    "lam": lh if home else la, "date": date,
                    "actual": float(nhl.skater_points(s["goals"], s["assists"], s["shots"], s["blocks"])),
                    "parts": (s["goals"], s["assists"], s["shots"], s["blocks"]),
                    "toi": _toi(s.get("toi"))})
        yield date, sim_games, ctxs
        for b in day:                               # the day's results join history after it
            h, a = b["home"], b["away"]
            for t, f_, a_ in ((h, b["home_goals"], b["away_goals"]), (a, b["away_goals"], b["home_goals"])):
                gf[t] += f_ or 0
                ga[t] += a_ or 0
                gp[t] += 1
                league_goals += f_ or 0
                league_games += 1
            for s in b["skaters"]:
                t = s["team"]
                hist[s["id"]].append((date, t, a if t == h else h, t == h, s["goals"], s["assists"],
                                      s["shots"], s["blocks"], _toi(s.get("toi"))))


def project(D: dict, names: list[str], prm: dict | None = None, seeds: int = 1,
            n_sims: int = 2000) -> dict:
    """{variant: frame of every skater-game with proj and actual}."""
    rows = {n: [] for n in names}
    for date, sim_games, ctxs in boards(D):
        for n in names:
            pool = [{"name": str(c["id"]), "team": c["team"], "pos": c["pos"], "line": None, "pp": None,
                     "means": VARIANTS[n](c, prm or {})} for c in ctxs]
            mean = np.mean([nhl_sim.simulate(pool, sim_games, n_sims=n_sims, seed=k).mean(axis=0)
                            for k in range(seeds)], axis=0)
            for c, p, m in zip(ctxs, pool, mean):
                g, a_, sh, bl = c["parts"]
                rows[n].append({"date": c["date"], "id": c["id"], "team": c["team"], "pos": c["pos"],
                                "base_toi": c["base"].get("toi"), "has_base": bool(c["base"]),
                                "n_prior": len(c["hist"]), "proj": float(m), "actual": c["actual"],
                                "goals": g, "assists": a_, "shots": sh, "blocks": bl,
                                "m_goals": p["means"]["goals"], "m_assists": p["means"]["assists"],
                                "m_shots": p["means"]["shots"], "m_blocks": p["means"]["blocks"]})
    return {n: pd.DataFrame(r) for n, r in rows.items()}


# ──────────────────────────────────────────────────────────────────────────────
# grading
# ──────────────────────────────────────────────────────────────────────────────
def halves(x: pd.DataFrame) -> dict:
    return {"H1": x[x.date < H2_FROM], "H2": x[x.date >= H2_FROM]}


def metrics(x: pd.DataFrame) -> dict:
    e = x.proj - x.actual
    rho = [spearman(g.proj, g.actual) for _, g in x.groupby("date") if len(g) >= 20]
    rho = [r for r in rho if r is not None]
    return {"n": len(x), "dates": x.date.nunique(), "mae": e.abs().mean(),
            "rmse": float(np.sqrt((e ** 2).mean())), "bias": e.mean(), "rho": float(np.mean(rho))}


def paired(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    """b minus a: squared error per skater-game, 95% interval resampling dates."""
    d = ((b.proj - b.actual) ** 2 - (a.proj - a.actual) ** 2).to_numpy()
    out = []
    for h, idx in (("H1", a.date < H2_FROM), ("H2", a.date >= H2_FROM)):
        dd, dates = d[idx.to_numpy()], a.date[idx].to_numpy()
        est, lo, hi, _ = cluster_bootstrap(dates, lambda i: dd[i].mean(), n_boot=2000, seed=7)
        per = pd.Series(dd).groupby(dates).mean()
        out.append(f"{h}: squared error {est:+.4f} [{lo:+.4f}, {hi:+.4f}], better on "
                   f"{(per < 0).sum()}/{len(per)} dates")
    return out


def slices(x: pd.DataFrame) -> dict:
    t = x.base_toi.fillna(0)
    fwd = x.pos != "D"
    return {**{f"pos {p}": x.pos == p for p in ("C", "W", "D")},
            "no 2024-25 rates": ~x.has_base,
            "F base toi < 13": fwd & x.has_base & (t < 13), "F 13-17": fwd & (t >= 13) & (t < 17),
            "F 17+": fwd & (t >= 17), "D < 19": ~fwd & x.has_base & (t < 19), "D 19+": ~fwd & (t >= 19),
            "first 5 games": x.n_prior < 5}


def report(names: list[str], seeds: int = 1, prm: dict | None = None) -> str:
    D = load()
    res = project(D, names, prm, seeds)
    out = ["# NHL season lab: 2025-26 replayed on the no-prop path\n",
           f"H1 < {H2_FROM} <= H2; seeds {seeds}; truth = box-score DK points.\n",
           "| variant | half | dates | skater-games | MAE | RMSE | bias | rank corr |",
           "|---|---|---|---|---|---|---|---|"]
    for n, x in res.items():
        for h, y in halves(x).items():
            m = metrics(y)
            out.append(f"| {n} | {h} | {m['dates']} | {m['n']:,} | {m['mae']:.3f} | {m['rmse']:.4f} | "
                       f"{m['bias']:+.3f} | {m['rho']:+.4f} |")
    for n in names[1:]:
        out += ["", f"{n} vs {names[0]}:"] + [f"- {x}" for x in paired(res[names[0]], res[n])]
    base = res[names[0]]
    out += ["", "| slice (H2) | n | " + " | ".join(f"{n} RMSE | {n} bias" for n in names) + " |",
            "|---|---|" + "---|---|" * len(names)]
    for lab, mask in slices(halves(base)["H2"]).items():
        cells = []
        for n in names:
            y = halves(res[n])["H2"]
            y = y[slices(y)[lab]]
            m = metrics(y)
            cells.append(f"{m['rmse']:.3f} | {m['bias']:+.3f}")
        out.append(f"| {lab} | {int(mask.sum()):,} | " + " | ".join(cells) + " |")
    comp = halves(base)["H2"]
    out += ["", "Components, H2 (" + names[0] + "): mean input vs actual -- " + ", ".join(
        f"{c} {comp['m_' + c].mean():.3f} vs {comp[c].mean():.3f}" for c in ("goals", "assists", "shots", "blocks"))]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--variant", action="append", default=[])
    r.add_argument("--seeds", type=int, default=1)
    a = ap.parse_args()
    print(report(["shipped"] + [v for v in a.variant if v != "shipped"], a.seeds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
