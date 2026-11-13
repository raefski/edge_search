#!/usr/bin/env python3
"""NHL skater lab: a live-faithful, held-out backtest of the skater projection
(edge/dfs_run_nhl.build_board -> edge/nhl_sim.simulate), the harness the critique
loop (NHL_CRITIQUE_LOG.md, .claude/skills/dfs-critique) measures every change on.

    python3 scripts/nhl_skater_lab.py cache              # rebuild every logged slate (both states)
    python3 scripts/nhl_skater_lab.py report             # the shipped model's standard table
    python3 scripts/nhl_skater_lab.py report --variant <name> [--variant <name> ...]
    python3 scripts/nhl_skater_lab.py parity             # lab == shipped build_board, slate by slate
    python3 scripts/nhl_skater_lab.py gate out.jsonl     # TEST rows for the critique loop's gate

WHAT IS PROJECTED, AND WHEN
Every slate in data/dfs_proj_log_nhl.csv is rebuilt from its pinned inputs
(scripts/nhl_rebuild.py: DK draftables and the DailyFaceoff snapshot from git,
odds from the store's last dfs_nhl scan before the cut), in two information states:

  lock    inputs as they stood at the slate's lock (confirmed lines and goalies)
  early   inputs three hours before lock (the afternoon phone build)

The cache (data/nhl_cache/lab/<state>/<gid>.json, gitignored, rebuilt by `cache`)
holds the board BEFORE simulation -- each skater's means, the raw DK prop markets,
last season's per-game rates, line / PP / injury, implied team goals -- so a variant
can recompute the means from the same raw inputs and re-simulate (seed 0, 2,000
games, as shipped).

WHAT IS GRADED
Skaters only (goalies are reported on one line: their points barely beat a
constant, NHL_STATUS.md 6e). Truth = DK points from the NHL box score (goals,
assists, shots, blocks + bonuses; no SH points / shootout goals, which the model
does not simulate either). A night's early and main slates share games, so each
(date, team, player) is graded once, from the LATEST-locking slate that holds him.
Board skaters whose team played but who are not in the box score (scratched) are
counted separately, not graded as 0: late swap is allowed, and availability is a
different question from the projection.

THE SPLIT
The slate night is the unit. TRAIN = nights before 2026-10-13 (fit anything
there), TEST = 2026-10-13 on (report only). Rank corr is within night, averaged.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import gzip
import io
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# one BLAS thread per process: `cache` builds slates across processes (all cores); numpy's own
# threads on top of ~18 workers only oversubscribe (as scripts/nba_lineup_lab.py)
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_run_nhl as R  # noqa: E402
from edge import nhl, nhl_sim  # noqa: E402
from edge.names import norm  # noqa: E402
from scripts import nhl_rebuild as RB  # noqa: E402
from scripts.contest_stats import spearman  # noqa: E402
from scripts.nhl_ownership_signal import box_key  # noqa: E402

LAB = ROOT / "data" / "nhl_cache" / "lab"
BOX = ROOT / "data" / "nhl_cache" / "box"
TEST_FROM = "2026-10-13"
STATES = {"lock": 0, "early": 3}           # hours before lock the inputs are pinned
N_SIMS = 2000


# ──────────────────────────────────────────────────────────────────────────────
# cache: rebuild each logged slate, keep the board before simulation
# ──────────────────────────────────────────────────────────────────────────────
SCANS = LAB / "scans"


class _PinnedStore:
    """The OddsStore for one pinned scan, each query's answer kept on local disk.

    Why (2026-11-12): scans older than 14 days live in the F: archive (edge/odds/archive.py),
    and WSL reads /mnt/f cold at ~20 s a query. One board's build asks the store 29 times
    (events() re-reads the whole scan each call), so an October slate took 323 s, and the
    gate's `cache` of 69 slates x 2 states ran past its 3-hour limit and crashed the night's
    NHL cycle. A scan never changes once taken, so each distinct query's rows are kept in
    data/nhl_cache/lab/scans/<id>.json.gz and served from there afterwards; the gate's
    worktrees copy data/nhl_cache, so they start warm.

    EACH QUERY'S ROWS, IN THE STORE'S OWN ORDER -- not one read of the scan filtered here.
    The same 621 h2h/totals rows came back in a different order, the odds client's payload
    follows row order, and the board moved (a skater 10.34 -> 10.58). Kept per exact call,
    a rebuild is the original build by construction (parity: `cache` twice, compare)."""

    def __init__(self, store, scan_id: int):
        self._store, self._scan = store, scan_id
        self._path = SCANS / f"{scan_id}.json.gz"
        try:
            self._memo = json.loads(gzip.decompress(self._path.read_bytes()))
        except (OSError, ValueError):
            self._memo = {}

    def _ask(self, key: list, fetch):
        k = json.dumps(key)
        if k not in self._memo:
            self._memo[k] = [dict(r) for r in fetch()]
            try:                                # merge with what other workers wrote meanwhile
                on_disk = json.loads(gzip.decompress(self._path.read_bytes()))
            except (OSError, ValueError):
                on_disk = {}
            self._memo = {**on_disk, **self._memo}
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_bytes(gzip.compress(json.dumps(self._memo).encode()))
            tmp.replace(self._path)             # whole file or none
        return self._memo[k]

    def quotes(self, scan_id, sport_key=None, markets=None, event_ids=None):
        if scan_id != self._scan:
            return self._store.quotes(scan_id, sport_key, markets, event_ids)
        args = (sport_key, list(markets) if markets else None, list(event_ids) if event_ids else None)
        return self._ask(["quotes", *args], lambda: self._store.quotes(scan_id, *args))

    def events(self, scan_id, sport_key=None):
        if scan_id != self._scan:
            return self._store.events(scan_id, sport_key)
        return self._ask(["events", sport_key], lambda: self._store.events(scan_id, sport_key))

    def __getattr__(self, name):
        return getattr(self._store, name)


def _capture(gid: int, hours_before: int) -> dict:
    lock = RB.lock_time(gid)
    at = RB.utc((datetime.fromisoformat(lock.rstrip("Z")) - timedelta(hours=hours_before))
                .strftime("%Y-%m-%dT%H:%M"))
    _, draftables = RB.git_json(f"data/draftables_snapshot/{gid}.json", at)
    _, lines = RB.git_json("data/nhl_lines_snapshot.json", at)
    prof = RB.for_sport(R.SPORT, "dfs")
    scan_id, scan_at = RB.scan_before(at, prof.name)

    def offline(url):
        raise RuntimeError("lab: pinned")
    RB.dfs._draftables_raw = lambda g: draftables
    RB.nhl._next_data = offline
    RB.nhl._snapshot = lambda: lines
    got: dict = {}
    real_props, real_sim = R.player_props, R.nhl_sim.simulate

    def props(*a, **k):
        out = real_props(*a, **k)
        got["props"] = {norm(n): m for n, m in out[0].items()}
        return out

    def sim(pool, games, **k):
        got["pool"], got["games"] = copy.deepcopy(pool), copy.deepcopy(games)
        return real_sim(pool, games, **k)
    R.player_props, R.nhl_sim.simulate = props, sim
    try:
        client = RB.ScrapedOddsClient(_PinnedStore(RB.OddsStore(), scan_id), prof.name, scan_id=scan_id,
                                      main_line_only=not prof.full_ladders)
        with contextlib.redirect_stdout(io.StringIO()):
            pool, _, info = R.build_board(gid, {"gid": gid, "start": lock, "label": "lab"}, client,
                                          n_sims=N_SIMS, seed=0)
    finally:
        R.player_props, R.nhl_sim.simulate = real_props, real_sim
    rates = R._season_rates()
    shipped = {(p["team"], p["name"]): p["proj"] for p in pool}
    rows = []
    for p in got["pool"]:
        key = norm(p["name"])
        p["raw_props"] = got["props"].get(key) or {}
        p["base"] = rates.get(key) or {}
        p["proj_shipped"] = shipped.get((p["team"], p["name"]))
        p.pop("buzz", None)
        rows.append(p)
    return {"gid": gid, "date": info["date"], "lock": lock, "at": at, "scan": [scan_id, scan_at],
            "implied": info["implied"], "seven_d": info["seven_d"], "games": got["games"],
            "pool": rows}


def _cache_one(job: tuple) -> str:
    """Build one (state, slate) board and write it; the line to print. A worker process: the
    pinned inputs _capture sets are module globals, so slates never share a process mid-build."""
    state, hrs, gid = job
    try:
        d = _capture(gid, hrs)
    except (SystemExit, Exception) as e:  # noqa: BLE001 -- a slate without pinned inputs is skipped
        return f"{state} {gid}: skipped ({e})"
    (LAB / state / f"{gid}.json").write_text(json.dumps(d, default=sorted))   # slots: a set
    return f"{state} {gid} {d['date']}: {len(d['pool'])} on board, scan {d['scan'][1][:16]}"


def cache(gids: list[int], states=STATES, workers: int | None = None) -> None:
    """Rebuild every (state, slate) board. Across processes, all cores but two: the critique
    gate runs this in BOTH checkouts before it can compare them, and on 2026-11-12 the serial
    loop (69 slates x 2 states) outran the gate's 3-hour limit on main's side and crashed the
    night's NHL cycle. Each board is built and written independently, so the files are the
    same whatever the order."""
    for state in states:
        (LAB / state).mkdir(parents=True, exist_ok=True)
    jobs = [(state, hrs, int(gid)) for state, hrs in states.items() for gid in gids]
    workers = workers or max(1, min(len(jobs), (os.cpu_count() or 2) - 2))
    if workers == 1:
        lines = map(_cache_one, jobs)
    else:
        ex = ProcessPoolExecutor(workers)
        lines = ex.map(_cache_one, jobs)
    try:
        for line in lines:
            print(line, file=sys.stderr, flush=True)
    finally:
        if workers != 1:
            ex.shutdown()


def load(state: str) -> list[dict]:
    return sorted((json.loads(f.read_text()) for f in (LAB / state).glob("*.json")),
                  key=lambda d: d["lock"])


# ──────────────────────────────────────────────────────────────────────────────
# variants: functions (slate, params) -> slate with new means. "shipped" = as built.
# ──────────────────────────────────────────────────────────────────────────────
VARIANTS: dict = {"shipped": lambda s, prm: s}


def variant(name):
    def wrap(fn):
        VARIANTS[name] = fn
        return fn
    return wrap


TOI_FILE = LAB / f"toi_{R.CURRENT_SEASON}.json"
_memo: dict = {}


def season_toi(refresh: bool = False) -> list[dict]:
    """This season's game-level ice time (nhl.skater_game_toi), cached once; a
    slate sees only the games dated before it, as build_board does live."""
    if "toi" not in _memo:
        if refresh or not TOI_FILE.exists():
            TOI_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOI_FILE.write_text(json.dumps(nhl.skater_game_toi(
                R.CURRENT_SEASON, (datetime.now() + timedelta(days=1)).date().isoformat())))
        _memo["toi"] = json.loads(TOI_FILE.read_text())
        _memo["rates"] = R._season_rates()
    return _memo["toi"]


@variant("toi_role")
def toi_role(s, prm):
    """Cycle 1 (NHL_CRITIQUE_LOG.md): last-season means scaled by this season's
    ice time, through the production helpers. params K, LAST, B (goals, assists,
    shots), B_B (blocks); defaults = shipped. A board cached after the ship
    already carries `role` and is left alone."""
    rows = season_toi()
    toi = R.group_toi([r for r in rows if r["date"] < s["date"]])
    power = {"goals": prm.get("B", R.ROLE_POWER["goals"]), "assists": prm.get("B", R.ROLE_POWER["assists"]),
             "shots": prm.get("B", R.ROLE_POWER["shots"]), "blocks": prm.get("B_B", R.ROLE_POWER["blocks"])}
    for p in s["pool"]:
        if p["pos"] == "G" or "role" in p:
            continue
        r = R.role_ratio((_memo["rates"].get(norm(p["name"])) or {}).get("toi"),
                         toi.get((norm(p["name"]), "D" if p["pos"] == "D" else "F")),
                         k=prm.get("K", R.ROLE_K), last=prm.get("LAST", R.ROLE_LAST))
        p["means"] = R.scale_role(p["means"], r, p.get("imputed") or [], power)
    return s


def _inputs(date: str) -> dict:
    """What build_board read for a slate on `date` besides the cached board: this
    season's ice time (fresh, not TOI_FILE, which stops where it was fetched) and
    production before the date (edge/dfs_run_nhl.current_production), memoised."""
    if "toi_all" not in _memo:
        _memo["toi_all"] = nhl.skater_game_toi(R.CURRENT_SEASON,
                                               (datetime.now() + timedelta(days=1)).date().isoformat())
        raw = {}
        for r in nhl.season_skater_stats(R.LAST_SEASON).values():
            row = R.rate_row(r)
            if row is not None:
                raw[norm(r["skaterFullName"])] = row
        _memo["raw_rates"] = raw
    key = ("prod", date)
    if key not in _memo:
        rows = [r for r in _memo["toi_all"] if r["date"] < date]
        _memo[key] = {"toi": R.group_toi(rows), "rows": rows, "prod": R.current_production(date) or {}}
    return _memo[key]


def _recompute(s: dict, rates: dict) -> dict:
    """Every skater's means rebuilt through production's skater_means from the
    cached props and `rates` (keyed as R._season_rates, aliases as build_board)."""
    inp = _inputs(s["date"])
    aliases = R.name_aliases(inp["rows"], rates)
    dk_keys: dict = {}
    for p in s["pool"]:
        k = R._initial_last(p["team"], p["name"])
        dk_keys[k] = dk_keys.get(k, 0) + 1
    aliases = {k: v for k, v in aliases.items() if dk_keys.get(k, 0) <= 1}
    implied, seven = s["implied"], set(s["seven_d"])
    for p in s["pool"]:
        if p["pos"] == "G":
            continue
        key, t, pos = norm(p["name"]), p["team"], p["pos"]
        rkey = key if key in rates else aliases.get(R._initial_last(t, p["name"]), key)
        pm = p.get("raw_props") or {}
        fd = "D" if pos == "D" else "F"
        means, imputed, r = R.skater_means(
            rates.get(rkey) or {}, pos, implied[t] / R.LEAGUE_TEAM_GOALS,
            nhl_sim.market_mean(pm.get("player_shots_on_goal")),
            nhl_sim.market_mean(pm.get("player_points")), nhl_sim.market_mean(pm.get("player_assists")),
            inp["toi"].get((rkey, fd)), pos == "D" and t in seven, inp["prod"].get((rkey, fd)))
        p["means"], p["imputed"] = means, imputed
    return s


@variant("recompute")
def recompute(s, prm):
    """The cached means rebuilt from raw inputs with last season's raw rates: must
    equal `shipped` on a cache built before RATE_K (checks _recompute itself)."""
    _inputs(s["date"])
    return _recompute(s, _memo["raw_rates"])


@variant("regressed_rates")
def regressed_rates(s, prm):
    """Last season's rates regressed toward ice time x position (RATE_K, or prm K),
    on a cache built with raw rates. Applied as a DELTA on the cached means --
    rebuilt(regressed) - rebuilt(raw) -- because older boards in the cache predate
    later ships (the first lock nights lack cycle 4's season blend, every board
    cycle 10's top stretch): the variant moves only what the rates move. On a
    cache built from the current code (early state: all no-prop means rebuild to
    1e-9) the delta is the production change exactly."""
    _inputs(s["date"])
    k = prm.get("K", R.RATE_K)
    if ("reg", k) not in _memo:
        _memo[("reg", k)] = R.regress_rates(_memo["raw_rates"], k)
    new = _recompute(copy.deepcopy(s), _memo[("reg", k)])
    old = _recompute(copy.deepcopy(s), _memo["raw_rates"])
    for p, a, b in zip(s["pool"], new["pool"], old["pool"]):
        if p["pos"] != "G":
            p["means"] = {c: v + a["means"][c] - b["means"][c] for c, v in p["means"].items()}
    return s


@variant("shots_power")
def shots_power(s, prm):
    """Cycle 12 (not shipped): a skater with no shots prop gets his per-game shot rate x
    scale ** e instead of x scale (scale = implied team goals / LEAGUE_TEAM_GOALS) --
    production's elasticity of 1 was never fitted. Every later step on an imputed shots
    mean (ice-time role, season blend at the same scale, 7-D) is multiplicative or
    linear in the scaled terms, so the board's shots x scale ** (e - 1) is the rebuilt
    mean, up to the goals + 0.05 floor (== a production knob to 9e-16 on all 20,152 cached
    skater rows, 2026-11-08). e defaults to 0.5, the TRAIN fit (lock + early RMSE, 4
    seeds). Optional prm k: shrink_imputed_shots after."""
    e = prm.get("e", 0.5)
    for p in s["pool"]:
        if p["pos"] != "G" and "shots" in (p.get("imputed") or []):
            mu = p["means"]
            scale = s["implied"][p["team"]] / R.LEAGUE_TEAM_GOALS
            mu["shots"] = max(mu["shots"] * scale ** (e - 1.0), mu["goals"] + 0.05)
    if prm.get("k") is not None:
        shrink_imputed_shots(s, prm["k"])
    return s


def shrink_imputed_shots(s, k: float):
    """Cycle 12 (b): imputed shots -> m + k (shots - m), m = the slate's mean imputed
    shots for the position (C / W / D); never below goals + 0.05."""
    grp = defaultdict(list)
    for p in s["pool"]:
        if p["pos"] != "G" and "shots" in (p.get("imputed") or []):
            grp[p["pos"]].append(p)
    for ps in grp.values():
        m = float(np.mean([p["means"]["shots"] for p in ps]))
        for p in ps:
            mu = p["means"]
            mu["shots"] = max(m + k * (mu["shots"] - m), mu["goals"] + 0.05)


# ──────────────────────────────────────────────────────────────────────────────
# grading
# ──────────────────────────────────────────────────────────────────────────────
def box_by_date() -> tuple[dict, dict]:
    """({date: {box_key: dk_points}}, {date: set(teams that played)})."""
    pts, teams = defaultdict(dict), defaultdict(set)
    for f in BOX.glob("2026*.json"):
        b = json.loads(f.read_text())
        d = b.get("date")
        teams[d] |= {b["home"], b["away"]}
        for s in b["skaters"]:
            pts[d][box_key(s["team"], s["name"])] = float(nhl.skater_points(
                s["goals"], s["assists"], s["shots"], s["blocks"]))
        for g in b["goalies"]:
            if g.get("starter"):
                won = g.get("decision") == "W"
                otl = g.get("decision") == "O"
                ga = g["goals_against"]
                full = int((g.get("toi") or "0:00").split(":")[0]) >= 58   # played it all
                pts[d][("G",) + box_key(g["team"], g["name"])] = float(nhl.goalie_points(
                    g["saves"], ga, won, otl, ga == 0 and full))
    return pts, teams


def project(slates: list[dict], name: str = "shipped", prm: dict | None = None,
            n_sims: int = N_SIMS, seeds: int = 1) -> pd.DataFrame:
    """One row per board player per slate: the variant's simulated mean + the inputs.
    `seeds` > 1 averages seeds 0..seeds-1: one seed's Monte Carlo noise moves train
    RMSE by ~0.01 (seeds 0-3: 6.093-6.104, cycle 2), as much as the changes judged,
    so decisions are taken at 4 seeds; 1 seed is the production build (parity)."""
    fn = VARIANTS[name]
    rows = []
    for s in slates:
        s2 = fn(copy.deepcopy(s), prm or {})
        pool = s2["pool"]
        mean = np.mean([nhl_sim.simulate(pool, s2["games"], n_sims=n_sims, seed=k).mean(axis=0)
                        for k in range(seeds)], axis=0)
        for i, p in enumerate(pool):
            rows.append({"gid": s["gid"], "date": s["date"], "lock": s["lock"], "name": p["name"],
                         "team": p["team"], "opp": p.get("opp"), "pos": p["pos"],
                         "salary": p["salary"], "line": p.get("line") or "", "pp": p.get("pp") or "",
                         "props": not ({"shots", "points"} & set(p.get("imputed") or [])),
                         "seven_d": bool(p.get("seven_d")), "proj": float(mean[i]),
                         "proj_shipped": p.get("proj_shipped")})
    return pd.DataFrame(rows)


def grade(df: pd.DataFrame, box: tuple | None = None) -> tuple[pd.DataFrame, dict]:
    """(played skater-games with `actual`, {dnp, dnp_share, goalies: frame})."""
    pts, teams = box or box_by_date()
    df = df.sort_values("lock").drop_duplicates(["date", "team", "name"], keep="last")
    sk = df[df.pos != "G"].copy()
    sk["actual"] = [pts[d].get(box_key(t, n)) for d, t, n in zip(sk.date, sk.team, sk.name)]
    played_team = [t in teams.get(d, ()) for d, t in zip(sk.date, sk.team)]
    dnp = sk[np.array(played_team) & sk.actual.isna()]
    g = df[df.pos == "G"].copy()
    g["actual"] = [pts[d].get(("G",) + box_key(t, n)) for d, t, n in zip(g.date, g.team, g.name)]
    return sk.dropna(subset=["actual"]), {"dnp": len(dnp), "dnp_proj": float(dnp.proj.sum()),
                                           "goalies": g.dropna(subset=["actual"])}


def metrics(x: pd.DataFrame, col: str = "proj") -> dict:
    e = x[col] - x.actual
    rho = [spearman(g[col], g.actual) for _, g in x.groupby("date") if len(g) >= 10]
    rho = [r for r in rho if r is not None]
    return {"n": len(x), "nights": x.date.nunique(), "mae": e.abs().mean(),
            "rmse": float(np.sqrt((e ** 2).mean())), "bias": e.mean(),
            "rho": float(np.mean(rho)) if rho else float("nan")}


def split(x: pd.DataFrame) -> dict:
    return {"train": x[x.date < TEST_FROM], "test": x[x.date >= TEST_FROM]}


def slices(x: pd.DataFrame) -> dict:
    sal = pd.cut(x.salary, [0, 3499, 4999, 6499, 99999], labels=["<3.5k", "3.5-5k", "5-6.5k", "6.5k+"])
    lg = x.line.where(x.line != "", "none")
    return {
        **{f"pos {p}": x.pos == p for p in ("C", "W", "D")},
        "props": x.props, "no props": ~x.props,
        **{f"line {l}": lg == l for l in ("F1", "F2", "F3", "F4", "D1", "D2", "D3", "D4", "none")},
        "PP1": x.pp == "PP1", "PP2": x.pp == "PP2", "no PP": ~x.pp.isin(["PP1", "PP2"]),
        **{f"salary {s}": sal == s for s in sal.cat.categories},
        "7-D team D": x.seven_d,
    }


def table(results: dict, state: str) -> list[str]:
    """results: {variant: graded frame}."""
    out = [f"### {state}\n", "| variant | split | nights | skater-games | MAE | RMSE | bias | rank corr |",
           "|---|---|---|---|---|---|---|---|"]
    for name, x in results.items():
        for sp, y in split(x).items():
            m = metrics(y)
            out.append(f"| {name} | {sp} | {m['nights']} | {m['n']:,} | {m['mae']:.3f} | {m['rmse']:.3f} | "
                       f"{m['bias']:+.3f} | {m['rho']:+.3f} |")
    return out


def slice_table(results: dict) -> list[str]:
    names = list(results)
    head = "| slice (test) | n | " + " | ".join(f"{n} MAE | {n} bias" for n in names) + " |"
    out = [head, "|---|---|" + "---|---|" * len(names)]
    base = split(results[names[0]])["test"]
    for lab, mask in slices(base).items():
        cells = []
        for n in names:
            y = split(results[n])["test"]
            y = y[slices(y)[lab]]
            m = metrics(y) if len(y) else None
            cells.append(f"{m['mae']:.3f} | {m['bias']:+.3f}" if m else "-- | --")
        out.append(f"| {lab} | {int(mask.sum())} | " + " | ".join(cells) + " |")
    return out


def paired(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    """Variant b minus a: squared error per skater-game on each split, its per-night
    mean with a 95% night-bootstrap interval, and nights better."""
    from scripts.contest_stats import cluster_bootstrap
    m = b.merge(a[["date", "team", "name", "proj"]], on=["date", "team", "name"], suffixes=("", "_0"))
    m["d"] = (m.proj - m.actual) ** 2 - (m.proj_0 - m.actual) ** 2
    out = []
    for sp, y in split(m).items():
        per = y.groupby("date").d.mean().to_numpy()
        est, lo, hi, _ = cluster_bootstrap(np.arange(len(per)), lambda i: per[i].mean(), n_boot=4000, seed=7)
        out.append(f"{sp}: squared error per night {est:+.3f} [{lo:+.3f}, {hi:+.3f}], "
                   f"better {(per < 0).sum()}/{len(per)} nights")
    return out


def report(names: list[str], states=("lock", "early"), prm: dict | None = None, seeds: int = 1) -> str:
    box = box_by_date()
    out = ["# NHL skater lab: DK points vs pre-lock projection, held out\n",
           f"TRAIN nights < {TEST_FROM} <= TEST. Played skaters only; each (date, team, player) once. "
           f"Seeds averaged: {seeds}.\n"]
    for state in states:
        slates = load(state)
        if not slates:
            out.append(f"_{state}: no cache -- run `cache`_\n")
            continue
        results, extra = {}, {}
        for n in names:
            results[n], extra[n] = grade(project(slates, n, prm, seeds=seeds), box)
        out += table(results, state)
        for n in names[1:]:
            out += ["", f"{n} vs {names[0]}, {state}:"] + [f"- {x}" for x in paired(results[names[0]], results[n])]
        e = extra[names[0]]
        g = e["goalies"]
        if len(g):
            sp = split(g)["test"]
            const = split(g)["train"].actual.mean()
            out.append(f"\nscratched (on board, team played, not in box): {e['dnp']} "
                       f"({e['dnp_proj']:.0f} projected pts). Goalies test: n {len(sp)}, MAE "
                       f"{(sp.proj - sp.actual).abs().mean():.2f} vs train-mean constant "
                       f"{(const - sp.actual).abs().mean():.2f}\n")
        if state == "lock":
            out += ["", *slice_table(results), ""]
    return "\n".join(out)


def goalie_stats(x: pd.DataFrame, col: str = "proj") -> dict:
    """Goalie DK points: error, and rank corr WITHIN each slate (slates with >= 2 starters)."""
    e = x[col] - x.actual
    rho = [spearman(g[col], g.actual) for _, g in x.groupby("date") if len(g) >= 2]
    rho = [r for r in rho if r is not None]
    return {"n": len(x), "slates": x.date.nunique(), "mae": e.abs().mean(),
            "rmse": float(np.sqrt((e ** 2).mean())), "bias": e.mean(),
            "rho": float(np.mean(rho)) if rho else float("nan"), "n_rho": len(rho)}


def goalie_report(names: list[str], states=("lock", "early"), seeds: int = 1) -> str:
    """Goalies (starters only), held out. Constant = the TRAIN mean DK points (the bar
    a projection must beat). Rank corr is within slate, averaged over slates."""
    box = box_by_date()
    out = ["# NHL goalies: DK points vs pre-lock projection, held out\n",
           f"TRAIN nights < {TEST_FROM} <= TEST. Starters in the box score only. Seeds: {seeds}.\n"]
    for state in states:
        slates = load(state)
        if not slates:
            out.append(f"_{state}: no cache -- run `cache`_\n")
            continue
        out += [f"### {state}\n", "| variant | split | slates | goalie-games | MAE | RMSE | bias | rank corr (slates) |",
                "|---|---|---|---|---|---|---|---|"]
        goalies = {}
        for n in names:
            goalies[n] = grade(project(slates, n, None, seeds=seeds), box)[1]["goalies"]
            for sp, y in split(goalies[n]).items():
                m = goalie_stats(y)
                out.append(f"| {n} | {sp} | {m['slates']} | {m['n']} | {m['mae']:.3f} | {m['rmse']:.3f} | "
                           f"{m['bias']:+.3f} | {m['rho']:+.3f} ({m['n_rho']}) |")
        const = split(goalies[names[0]])["train"].actual.mean()
        t = split(goalies[names[0]])["test"]
        out.append(f"| constant (train mean {const:.2f}) | test | {t.date.nunique()} | {len(t)} | "
                   f"{(const - t.actual).abs().mean():.3f} | {np.sqrt(((const - t.actual) ** 2).mean()):.3f} | "
                   f"{const - t.actual.mean():+.3f} | -- |")
        out.append("")
    return "\n".join(out)


def parity(state: str = "lock") -> str:
    """The lab's re-simulated shipped projection vs the board build_board produced
    (same seed, same pool order): any difference means the lab is not the model."""
    df = project(load(state))
    d = (df.proj - df.proj_shipped).abs()
    return f"{state}: {len(df)} board rows, max |lab - shipped| {d.max():.4f}, mean {d.mean():.5f}"


def gate(path: str, seeds: int = 4) -> int:
    """Rows for the unattended critique loop's gate (harness/critique_gate.py): the shipped
    variant on the TEST nights, both states, projected and actual DK points per
    skater-game (the gate squares the error), clustered by night; 4 seeds, as decisions are taken here (one seed moves RMSE ~0.01)."""
    import json
    box, n = box_by_date(), 0
    with open(path, "w") as fh:
        for state in ("lock", "early"):
            slates = load(state)
            if not slates:
                continue
            x, _ = grade(project(slates, "shipped", seeds=seeds), box)
            for r in split(x)["test"].itertuples():
                fh.write(json.dumps({"key": f"{state}|{r.date}|{r.team}|{r.name}", "cluster": r.date,
                                     "split": "primary", "state": state,
                                     "pred": float(r.proj), "actual": float(r.actual)}) + "\n")
                n += 1
    print(f"{n:,} gate rows -> {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cache")
    c.add_argument("gids", nargs="*", type=int)
    c.add_argument("--state", choices=list(STATES))
    c.add_argument("--workers", type=int, help="processes (default: all cores but two; 1 = serial)")
    r = sub.add_parser("report")
    r.add_argument("--variant", action="append", default=[])
    r.add_argument("--state", choices=list(STATES))
    r.add_argument("--seeds", type=int, default=1, help="average seeds 0..n-1 (decisions: 4)")
    r.add_argument("--lab", help="another cache directory (e.g. a copy taken before a ship)")
    g = sub.add_parser("goalies", help="starting goalies' DK points vs projection (held out)")
    g.add_argument("--variant", action="append", default=[])
    g.add_argument("--state", choices=list(STATES))
    g.add_argument("--seeds", type=int, default=1, help="average seeds 0..n-1 (decisions: 4)")
    sub.add_parser("parity")
    gt = sub.add_parser("gate", help="TEST rows for harness/critique_gate.py")
    gt.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "gate":
        return gate(a.out)
    if a.cmd == "cache":
        gids = a.gids or sorted(pd.read_csv(ROOT / "data" / "dfs_proj_log_nhl.csv").gid.unique())
        cache([int(g) for g in gids], {a.state: STATES[a.state]} if a.state else STATES, workers=a.workers)
    elif a.cmd == "report":
        names = ["shipped"] + [v for v in a.variant if v != "shipped"]
        if a.lab:
            global LAB
            LAB = Path(a.lab)
        print(report(names, (a.state,) if a.state else ("lock", "early"), seeds=a.seeds))
    elif a.cmd == "goalies":
        names = ["shipped"] + [v for v in a.variant if v != "shipped"]
        print(goalie_report(names, (a.state,) if a.state else ("lock", "early"), seeds=a.seeds))
    else:
        print(parity("lock"))
        print(parity("early"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
