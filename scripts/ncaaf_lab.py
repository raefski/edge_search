#!/usr/bin/env python3
"""Held-out harness for the college build's target metric: DK points vs the
pre-lock projection, per laddered player.

    python3 scripts/ncaaf_lab.py report                 # shipped model, every slate
    python3 scripts/ncaaf_lab.py report --cutoff 2026-10-11
    python3 scripts/ncaaf_lab.py dump out.csv           # the joined rows, for a critic
    python3 scripts/ncaaf_lab.py gate out.jsonl         # reprojected held-out rows, for the critique gate

WHY LOGGED PROJECTIONS AND NOT A REPLAY
`data/dfs_proj_log_ncaaf.csv` is what the app actually showed before lock, so
grading it is live-faithful by construction: no leak from a later odds scan,
no rebuild with today's code. A candidate fix is evaluated as a transform of
these logged rows (plus whatever else was knowable pre-lock), fitted on slates
before `--cutoff` and reported on the slates after it -- the slate is the unit.

TRUTH
DK's own FPTS from the contest exports in `data/contests.db`, joined by DRAFT
GROUP (never "the nearest slate"; the old calibration script's Jaccard match is
exactly the trap CLAUDE.md warns about). The export only lists drafted players,
so the graded population is "logged AND drafted by someone in a contest on that
draft group". `report` prints the share of logged players that survive, so the
selection is visible: an undrafted laddered player who scored 0 is missing,
which biases the graded set slightly toward players who played.
"""
from __future__ import annotations

import argparse
import json
import collections
import csv
import math
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.names import norm  # noqa: E402
from edge.ncaaf import base_position  # noqa: E402

PROJ_LOG = ROOT / "data" / "dfs_proj_log_ncaaf.csv"
CONTESTS = ROOT / "data" / "contests.db"

#: 10/11 splits the first three Saturdays (9/26, 10/3; 10/10 Main was never
#: logged) from everything since. Chosen before looking at any error.
DEFAULT_CUTOFF = "2026-10-11"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def truth() -> dict:
    """{draft_group: {norm_name: fpts}} from every NCAAF export, cash and GPP.
    A player's FPTS is the same in every contest on one draft group."""
    out: dict = collections.defaultdict(dict)
    con = sqlite3.connect(CONTESTS)
    q = ("select c.draft_group, p.name, p.fpts from player_result p "
         "join contest c using(contest_id) where c.sport='NCAAF' "
         "and c.draft_group is not null")
    for dg, name, fpts in con.execute(q):
        if fpts is not None:
            out[int(dg)][norm(name)] = float(fpts)
    return dict(out)


def joined() -> tuple[list[dict], dict]:
    """Logged rows that have a truth, plus {gid: (logged, graded)} coverage."""
    tr = truth()
    rows, cover = [], collections.Counter(),
    logged = collections.Counter()
    graded = collections.Counter()
    with PROJ_LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                gid = int(r["gid"])
            except (TypeError, ValueError):
                continue
            if gid not in tr or _f(r.get("proj")) is None:
                continue
            logged[gid] += 1
            act = tr[gid].get(norm(r["player"]))
            if act is None:
                continue
            graded[gid] += 1
            rows.append({
                "date": r["date"], "gid": gid, "player": r["player"],
                "team": r["team"], "opp": r["opp_team"],
                "pos": base_position(r["dk_pos"]), "salary": _f(r["salary"]),
                "proj": _f(r["proj"]), "sd": _f(r["sd"]),
                "band": _f(r.get("band_points")), "dk_fppg": _f(r.get("dk_fppg")),
                "games": _f(r.get("games")), "actual": act,
            })
    return rows, {g: (logged[g], graded[g]) for g in logged}


def stats(pairs: list[tuple[float, float]]) -> dict:
    n = len(pairs)
    if not n:
        return {"n": 0}
    err = [a - p for p, a in pairs]
    return {"n": n, "mae": sum(map(abs, err)) / n,
            "rmse": math.sqrt(sum(e * e for e in err) / n),
            "bias": sum(err) / n}


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Rank correlation of projection and actual (average ranks for ties)."""
    if len(pairs) < 3:
        return None
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out
    a, b = ranks([p for p, _ in pairs]), ranks([y for _, y in pairs])
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else None


def table(title: str, groups: dict) -> None:
    print(f"\n{title}")
    print(f"  {'slice':<22}{'n':>5}{'MAE':>8}{'RMSE':>8}{'bias(a-p)':>11}{'rho':>7}")
    for k, pairs in groups.items():
        s = stats(pairs)
        if s["n"]:
            rho = spearman(pairs)
            print(f"  {str(k):<22}{s['n']:>5}{s['mae']:>8.2f}{s['rmse']:>8.2f}"
                  f"{s['bias']:>+11.2f}" + (f"{rho:>7.2f}" if rho is not None else f"{'':>7}"))


def report(rows: list[dict], cover: dict, cutoff: str, pred=None) -> None:
    pred = pred or (lambda r: r["proj"])
    train = [r for r in rows if r["date"] < cutoff]
    test = [r for r in rows if r["date"] >= cutoff]
    print(f"cutoff {cutoff}: train {len({r['gid'] for r in train})} slates / "
          f"{len(train)} players, test {len({r['gid'] for r in test})} slates / "
          f"{len(test)} players")
    print("coverage (logged -> graded):",
          ", ".join(f"{g} {a}->{b}" for g, (a, b) in sorted(cover.items())))
    for name, part in (("TRAIN", train), ("TEST (held out)", test)):
        groups = {"all": [(pred(r), r["actual"]) for r in part]}
        for pos in ("QB", "RB", "WR"):
            groups[pos] = [(pred(r), r["actual"]) for r in part if r["pos"] == pos]
        for lo, hi in ((0, 5000), (5000, 7000), (7000, 99999)):
            groups[f"${lo//1000}k-{hi//1000 if hi < 99999 else '+'}"] = [
                (pred(r), r["actual"]) for r in part if lo <= (r["salary"] or 0) < hi]
        for gid in sorted({r["gid"] for r in part}):
            d = next(r["date"] for r in part if r["gid"] == gid)
            groups[f"{d} {gid}"] = [(pred(r), r["actual"]) for r in part
                                   if r["gid"] == gid]
        table(name, groups)


def stretch_pred(rows: list[dict], cutoff: str):
    """Critique cycle 1 (not shipped): p' = pivot + k (p - pivot) for QB/RB,
    pivot = train mean projection, k = OLS slope of actual on proj after
    removing each train slate's mean from both (so the old-code level bias
    on train is not refitted). Fitted on train only."""
    train = [r for r in rows if r["date"] < cutoff]
    params = {}
    for pos in ("QB", "RB"):
        part = [r for r in train if r["pos"] == pos]
        by = collections.defaultdict(list)
        for r in part:
            by[r["gid"]].append(r)
        xs, ys = [], []
        for g in by.values():
            mp = sum(r["proj"] for r in g) / len(g)
            ma = sum(r["actual"] for r in g) / len(g)
            xs += [r["proj"] - mp for r in g]
            ys += [r["actual"] - ma for r in g]
        k = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
        params[pos] = (sum(r["proj"] for r in part) / len(part), k)
    print("stretch (pivot, k):", {p: tuple(round(v, 2) for v in t)
                                  for p, t in params.items()})

    def pred(r):
        if r["pos"] not in params:
            return r["proj"]
        pivot, k = params[r["pos"]]
        return pivot + k * (r["proj"] - pivot)
    return pred


# ---------------------------------------------------------------------------
# Critique cycle 3: a QB's rushing touchdowns from his own season rate
# ---------------------------------------------------------------------------
#: replayed components per draft group (production build_pool at the last
#: dfs_ncaaf scan before lock, draftables as of the last commit before lock)
REPLAY_DIR = ROOT / "data" / "harness" / "ncaaf_lab"
QB_RUSH_TD_MIN_G = 2                  # team games before the slate, else unchanged
QB_RUSH_TD_K_GRID = (0, 1, 2, 3, 4, 6, 8)


def replay_components(gid: int) -> dict:
    """{norm name: rush-TD points the model imputed} for one draft group, cached.

    The log keeps the projection, not its parts, so the imputed term is rebuilt
    by replaying production. On 9 of 11 graded groups the replay reproduces the
    logged projection exactly (critic, 2026-11-07); 9/26 and 10/3 were logged by
    older code, whose rush-TD rule was the same yards/110."""
    path = REPLAY_DIR / f"replay_{gid}.json"
    if path.exists():
        got = json.loads(path.read_text())
        if all("components" in v for v in got.values()):
            return got                           # older caches lack components: rebuild
    import ncaaf_names_lab as NL
    from edge import dfs
    import edge.dfs_run_ncaaf as R
    from edge.odds.source import ScrapedOddsClient
    from edge.odds.store import OddsStore
    lock, rows = NL.board_at_lock(gid)
    store = OddsStore()
    scan = NL.scan_before(store, lock) if rows else None
    if not scan:
        return {}
    dfs._draftables_raw = lambda g, rows=rows: rows
    pool, _ = R.build_pool(ScrapedOddsClient(store, "dfs_ncaaf", scan_id=scan[0],
                                             main_line_only=False), dfs.fetch_draftables(gid))
    out = {norm(p["name"]): {"rush_td_pts": (p.get("components") or {}).get("rush TD", 0.0),
                             "proj": p["proj"], "components": p.get("components") or {}}
           for p in pool}
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def season_rates(rows: list[dict]):
    """(slate week per gid, rate(name, week) -> (rush TDs per team game, G) or None).

    The slate's cfbfastR week is READ, not assumed (STATUS §10 trap 1): the week
    whose box-score DK points best match the export's FPTS for the graded
    players. The rate is production's own edge.ncaaf.qb_rush_td_rates on the
    weeks before it, so lab and build cannot drift."""
    from edge import ncaaf
    plays = ncaaf.fetch_season(2026)
    box = ncaaf.boxscores(plays)
    week_of = {r["game_id"]: int(r["week"]) for r in plays}
    by_name: dict = collections.defaultdict(list)
    for v in box.values():
        by_name[norm(v["player"])].append((week_of[v["game_id"]], v))
    slate_week = {}
    for gid in sorted({r["gid"] for r in rows}):
        part = [r for r in rows if r["gid"] == gid]
        best = None
        for w in sorted(set(week_of.values())):
            errs = [abs(v["dk"] - r["actual"]) for r in part
                    for wk, v in by_name.get(norm(r["player"]), []) if wk == w]
            if len(errs) >= 5:
                mae = sum(errs) / len(errs)
                if best is None or mae < best[1]:
                    best = (w, mae, len(errs))
        # A slate after the file's last week fits no week (11/5: best MAE 8.3
        # on week 9); it is the week after the last one, not the closest one.
        if best and best[1] <= 3.0:
            slate_week[gid] = best
        else:
            slate_week[gid] = (max(week_of.values()) + 1, float("nan"), 0)
    tables: dict = {}

    def rate(name: str, week: int):
        if week not in tables:
            tables[week] = ncaaf.qb_rush_td_rates(plays, before_week=week)
        got = tables[week].get(norm(name))
        return (got["rate"], got["team_games"]) if got else None
    return slate_week, rate


def qb_rush_td_pred(rows: list[dict], cutoff: str, k: float | None = None):
    """pred = proj - imputed rush-TD points + 6 x shrunk season rush TDs per team
    game, QBs with >= QB_RUSH_TD_MIN_G team games only. Shrinkage toward the
    imputed rate: td = (TDs + k imp) / (G + k); k picked on TRAIN by MAE."""
    slate_week, rate = season_rates(rows)
    print("slate week (cfbfastR, MAE vs DK FPTS, n):",
          ", ".join(f"{g} w{w} {m:.2f}/{n}" for g, (w, m, n) in sorted(slate_week.items())))
    comp = {gid: replay_components(gid) for gid in sorted({r["gid"] for r in rows})}
    feat = {}
    for r in rows:
        if r["pos"] != "QB" or r["gid"] not in slate_week:
            continue
        c = comp[r["gid"]].get(norm(r["player"]))
        got = rate(r["player"], slate_week[r["gid"]][0])
        if c is None or got is None or got[1] < QB_RUSH_TD_MIN_G:
            continue
        feat[id(r)] = (c["rush_td_pts"], got[0], got[1])
    def make(kk):
        def pred(r):
            f = feat.get(id(r))
            if f is None:
                return r["proj"]
            imp_pts, td_pg, g = f
            td = (td_pg * g + kk * imp_pts / 6.0) / (g + kk)
            return r["proj"] - imp_pts + 6.0 * td
        return pred
    train = [r for r in rows if r["date"] < cutoff]
    n_tr = sum(1 for r in train if id(r) in feat)
    n_te = sum(1 for r in rows if r["date"] >= cutoff and id(r) in feat)
    if k is None:
        scores = {kk: stats([(make(kk)(r), r["actual"]) for r in train])["mae"]
                  for kk in QB_RUSH_TD_K_GRID}
        k = min(scores, key=scores.get)
        print("train MAE by k:", ", ".join(f"{kk}: {m:.4f}" for kk, m in scores.items()),
              f"-> k={k}")
    print(f"QBs re-rated: train {n_tr} / {sum(r['pos'] == 'QB' for r in train)}, "
          f"test {n_te} / {sum(r['pos'] == 'QB' and r['date'] >= cutoff for r in rows)}")
    return make(k)


# ---------------------------------------------------------------------------
# Re-projection from raw ladders (cycle 4 runner-ups): every variant below is
# production project_player on the slate's own ladders, with one knob moved.
# ---------------------------------------------------------------------------
def replay_ladders(gid: int) -> dict:
    """{norm name: {pos, ladders: {market: [(line, decimal), ...]}}} for one
    draft group's pool at lock, plus "__lines__"/"__games__" (the build's game
    lines as it computed them), cached like replay_components."""
    path = REPLAY_DIR / f"ladders_{gid}.json"
    if path.exists():
        return json.loads(path.read_text())
    import ncaaf_names_lab as NL
    from edge import dfs
    import edge.dfs_run_ncaaf as R
    from edge.odds.source import ScrapedOddsClient
    from edge.odds.store import OddsStore
    lock, rows = NL.board_at_lock(gid)
    store = OddsStore()
    # Replaying an archived scan over /mnt/f is ~100x slower (STATUS §10 trap 2):
    # NCAAF_LAB_ARCH names a local folder holding copies of the month files.
    arch = os.environ.get("NCAAF_LAB_ARCH")
    if arch:
        orig = store.archived_file
        store.archived_file = lambda sid, _o=orig: (
            Path(arch) / _o(sid).name if _o(sid) is not None
            and (Path(arch) / _o(sid).name).exists() else _o(sid))
    scan = NL.scan_before(store, lock) if rows else None
    if not scan:
        return {}
    cap = {}
    orig_lines, orig_pp = R.game_lines, R.project_player

    def lines(client, games, ep, sal):
        out, rep = orig_lines(client, games, ep, sal)
        cap["lines"] = {str(k): v for k, v in out.items()}
        cap["games"] = {str(g): {k: v for k, v in info.items() if isinstance(v, (str, int, float))}
                        for g, info in games.items()}
        return out, rep

    def pp(ladders, sport, position=None, rush_td_mean=None):
        out = orig_pp(ladders, sport, position=position, rush_td_mean=rush_td_mean)
        for m, v in (out.get("ladders") or {}).items():
            v["rungs_raw"] = ladders.get(m)
        return out
    R.game_lines, R.project_player = lines, pp
    try:
        dfs._draftables_raw = lambda g, rows=rows: rows
        pool, _ = R.build_pool(ScrapedOddsClient(store, "dfs_ncaaf", scan_id=scan[0],
                                                 main_line_only=False),
                               dfs.fetch_draftables(gid), qb_rates={})
    finally:
        R.game_lines, R.project_player = orig_lines, orig_pp
    out = {"__lines__": cap.get("lines", {}), "__games__": cap.get("games", {})}
    for p in pool:
        out[norm(p["name"])] = {"pos": p["dk_pos"], "team": p["team"], "game": str(p.get("game")),
                                "ladders": {m: v.get("rungs_raw") for m, v in (p.get("ladders") or {}).items()}}
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, default=str))
    return out


def reproject_pred(rows: list[dict], cutoff: str, qb_overround: dict | None = None,
                   count_tail: dict | None = None, quiet: bool = False):
    """Shipped model rebuilt from each slate's raw ladders: production
    project_player, the cycle-3 QB rush-TD rate, and optionally
      qb_overround  {market: overround} for QUARTERBACKS only (runner-up 1)
      count_tail    {market: E[N - k_max | N >= k_max]} (runner-up 2).
    Rows without cached ladders keep the logged projection."""
    import edge.dfs_ladder as DL
    import edge.dfs_run_ncaaf as R
    from edge import dfs_sport
    sport = dfs_sport.get(R.SPORT)
    slate_week, rate = season_rates(rows)
    lad = {gid: replay_ladders(gid) for gid in sorted({r["gid"] for r in rows})}
    cache: dict = {}

    def project(r):
        key = (r["gid"], norm(r["player"]))
        if key in cache:
            return cache[key]
        ent = lad[r["gid"]].get(norm(r["player"]))
        if not ent or not ent.get("ladders") or any(v is None for v in ent["ladders"].values()):
            cache[key] = None
            return None
        ladders = {m: [tuple(x) for x in v] for m, v in ent["ladders"].items()}
        td = None
        if r["pos"] == "QB":
            got = rate(r["player"], slate_week[r["gid"]][0])
            if got and got[1] >= QB_RUSH_TD_MIN_G:
                td = got[0]
        old_or, old_tail = dict(DL.LADDER_OVERROUND), dict(DL.COUNT_TAIL_EXCESS)
        try:
            if qb_overround and r["pos"] == "QB":
                DL.LADDER_OVERROUND.update(qb_overround)
            if count_tail:
                DL.COUNT_TAIL_EXCESS.update(count_tail)
            res = R.project_player(ladders, sport, position=ent["pos"], rush_td_mean=td)
        finally:
            DL.LADDER_OVERROUND.clear(); DL.LADDER_OVERROUND.update(old_or)
            DL.COUNT_TAIL_EXCESS.clear(); DL.COUNT_TAIL_EXCESS.update(old_tail)
        cache[key] = res.get("proj")
        return cache[key]

    def pred(r):
        p = project(r)
        return r["proj"] if p is None else p
    return pred


def qb_implied_pred(rows: list[dict], cutoff: str, base):
    """Cycle 4 runner-up 3: a QB's implied TEAM total from the game's main
    total and spread (dfs_run_ncaaf.main_line, fixed 2026-11-07), as a term
    beside the shipped projection: pred = base + k (implied - pivot), k = slope
    of the slate-demeaned train residual on slate-demeaned implied, pivot =
    train mean. The ladders price a QB's own volume; this asks whether the
    team's scoring expectation carries anything they do not."""
    lad = {gid: replay_ladders(gid) for gid in sorted({r["gid"] for r in rows})}

    def implied(r):
        ent = lad[r["gid"]].get(norm(r["player"]))
        if r["pos"] != "QB" or not ent:
            return None
        line = lad[r["gid"]].get("__lines__", {}).get(str(ent.get("game")))
        if not line or line.get("total") is None or line.get("spread_home") is None:
            return None
        sign = -1 if ent.get("team") == line.get("home") else 1
        return (line["total"] + sign * line["spread_home"]) / 2.0
    train = [r for r in rows if r["date"] < cutoff and implied(r) is not None]
    by = collections.defaultdict(list)
    for r in train:
        by[r["gid"]].append((implied(r), r["actual"] - base(r)))
    xs, ys = [], []
    for g in by.values():
        mx, my = sum(x for x, _ in g) / len(g), sum(y for _, y in g) / len(g)
        xs += [x - mx for x, _ in g]
        ys += [y - my for _, y in g]
    k = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
    pivot = sum(implied(r) for r in train) / len(train)
    n_te = sum(1 for r in rows if r["date"] >= cutoff and implied(r) is not None)
    print(f"QB implied team total (train {len(train)} QBs / {len(by)} slates, test {n_te}): "
          f"k={k:.3f} per point, pivot={pivot:.2f}")

    def pred(r):
        x = implied(r)
        return base(r) if x is None else base(r) + k * (x - pivot)
    return pred


def qb_pass_stretch_pred(rows: list[dict], cutoff: str, base):
    """Critique cycle 4 (QB) candidate: the passing part of a QB projection
    (pass yds + pass TD + INT points, from the replayed components) is spread
    too narrowly between quarterbacks. pred = base + k (pass_pts - pivot), k =
    OLS slope of the slate-demeaned residual of `base` on slate-demeaned
    pass_pts, pivot = mean pass_pts; both from TRAIN QBs only."""
    comp = {gid: replay_components(gid) for gid in sorted({r["gid"] for r in rows})}

    def pass_pts(r):
        c = (comp[r["gid"]].get(norm(r["player"])) or {}).get("components")
        if r["pos"] != "QB" or not c:
            return None
        return c.get("pass yds", 0.0) + c.get("pass TD", 0.0) + c.get("INT", 0.0)
    train = [r for r in rows if r["date"] < cutoff and pass_pts(r) is not None]
    by = collections.defaultdict(list)
    for r in train:
        by[r["gid"]].append((pass_pts(r), r["actual"] - base(r)))
    xs, ys = [], []
    for g in by.values():
        mx, my = sum(x for x, _ in g) / len(g), sum(y for _, y in g) / len(g)
        xs += [x - mx for x, _ in g]
        ys += [y - my for _, y in g]
    k = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
    pivot = sum(pass_pts(r) for r in train) / len(train)
    print(f"QB pass stretch (train, {len(train)} QBs / {len(by)} slates): "
          f"k={k:.3f} pivot={pivot:.2f}")

    def pred(r):
        pp = pass_pts(r)
        return base(r) if pp is None else base(r) + k * (pp - pivot)
    return pred


def gate(path: str) -> int:
    """Rows for the unattended critique loop's gate (harness/critique_gate.py): today's
    code rebuilt from each slate's raw ladders (reproject_pred: production
    project_player, so a model change on a branch moves it; the logged projection
    would not), per graded player on the held-out slates (date >= DEFAULT_CUTOFF);
    the gate scores absolute error, clustered by slate. Players with no cached
    ladders keep their logged projection in both checkouts."""
    rows, _ = joined()
    pred = reproject_pred(rows, DEFAULT_CUTOFF, quiet=True)
    n = 0
    with open(path, "w") as fh:
        for r in rows:
            if r["date"] < DEFAULT_CUTOFF or r["actual"] is None:
                continue
            fh.write(json.dumps({"key": f"{r['gid']}|{norm(r['player'])}", "cluster": str(r["gid"]),
                                 "split": "primary", "state": "all", "pred": float(pred(r)),
                                 "actual": float(r["actual"])}) + "\n")
            n += 1
    print(f"{n:,} gate rows -> {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    rp.add_argument("--stretch", action="store_true",
                    help="cycle 1 candidate: stretch QB/RB around the train "
                         "position mean by the slate-demeaned train OLS slope")
    rp.add_argument("--qb-rush-td", nargs="?", const=-1.0, type=float, default=None,
                    help="cycle 3 candidate: QB rush TDs from his season rate "
                         "(optional shrinkage k; omitted = chosen on train)")
    rp.add_argument("--qb-pass-stretch", action="store_true",
                    help="cycle 4 (QB) candidate, on top of --qb-rush-td: stretch "
                         "QB passing points around the train mean by the train slope")
    rp.add_argument("--reproject", action="store_true",
                    help="rebuild every projection from the slate's raw ladders "
                         "(today's code + cycle-3 QB rush-TD rate): the base for "
                         "--qb-rush-overround / --pass-td-tail")
    rp.add_argument("--qb-rush-overround", type=float,
                    help="cycle 4 runner-up 1: player_rush_yds overround for QBs only")
    rp.add_argument("--pass-td-tail", type=float,
                    help="cycle 4 runner-up 2: COUNT_TAIL_EXCESS for player_pass_tds")
    rp.add_argument("--qb-implied", action="store_true",
                    help="cycle 4 runner-up 3: QB implied team total term (needs --reproject base)")
    rp.add_argument("--pos", help="report one position only (QB, RB, WR)")
    rp.add_argument("--dedup", action="store_true",
                    help="drop a player-date already graded on an earlier draft group "
                         "of the same day (one realisation counted once)")
    dp = sub.add_parser("dump")
    dp.add_argument("out")
    gp = sub.add_parser("gate", help="held-out rows for harness/critique_gate.py")
    gp.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "gate":
        return gate(a.out)
    rows, cover = joined()
    if a.cmd == "report":
        if a.dedup:
            seen, keep = set(), []
            for r in sorted(rows, key=lambda r: (r["date"], -r["gid"])):
                if (r["date"], norm(r["player"])) not in seen:
                    seen.add((r["date"], norm(r["player"])))
                    keep.append(r)
            rows = keep
        pred = None
        if a.stretch:
            pred = stretch_pred(rows, a.cutoff)
        elif a.qb_rush_td is not None:
            pred = qb_rush_td_pred(rows, a.cutoff, None if a.qb_rush_td < 0 else a.qb_rush_td)
        if a.reproject:
            pred = reproject_pred(
                rows, a.cutoff,
                qb_overround=({"player_rush_yds": a.qb_rush_overround}
                              if a.qb_rush_overround is not None else None),
                count_tail=({"player_pass_tds": a.pass_td_tail}
                            if a.pass_td_tail is not None else None))
        if a.qb_implied:
            pred = qb_implied_pred(rows, a.cutoff, pred or (lambda r: r["proj"]))
        if a.qb_pass_stretch:
            pred = qb_pass_stretch_pred(rows, a.cutoff, pred or (lambda r: r["proj"]))
        if a.pos:
            # AFTER the predictor is built: the slate-week read needs every graded
            # player (>= 5 per slate), and a 3-QB slate falling back to "the week
            # after the file" would put that slate's own game into its rates.
            rows = [r for r in rows if r["pos"] == a.pos.upper()]
        report(rows, cover, a.cutoff, pred)
    else:
        with open(a.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"{len(rows)} rows -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
