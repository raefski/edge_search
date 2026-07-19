#!/usr/bin/env python3
"""Calibrate + validate edge/dfs_sim.py against REAL data, two layers:

1. 2025 lab layer (free, large-n): rebuild real team-games from bt_boxscores,
   simulate them with the production-shape projections, and check:
     - team-run distribution (mean/std) vs real
     - per-player score marginals by projection bucket vs 25,086 real rows
     - teammate score correlation by batting-order distance vs the §18
       measured targets (+0.167 adjacent, decaying to +0.107 at distance 4)
     - pitcher vs opposing-stack anti-correlation (sign + size)
2. Real-contest layer (2026): for every DK standings export on record, build
   the slate pool from the proj log, use the contest's own ACTUAL ownership
   for the field model (isolating field-shape error from ownership-model
   error), simulate the field, and compare simulated field score quantiles
   and our lineup's simulated percentile against what actually happened.

Also measures the field's primary-stack-size distribution from the real
standings exports (feeds dfs_sim.STACK_DIST).

Usage: python3 scripts/dfs_sim_validate.py [--games N] [--sims N]
"""
import argparse
import csv
import glob
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs, dfs_sim  # noqa: E402
from edge.dfs import norm  # noqa: E402
from scripts.dfs_component_eval import (  # noqa: E402
    ROWS, skill_eb, k9_blend, era_blend, clamp, pa_for, LG_ERA,
)
from edge.dfs import LG_K9, PLATOON_CELL, HOME_QUALITY  # noqa: E402
from scripts.dfs_calibration import (  # noqa: E402
    parse_contest_file, load_proj_log, infer_date_by_ground_truth,
)
from scripts.dfs_roi_backtest import load_our_lineup, parse_leaderboard  # noqa: E402

PA, S1, S2, S3, HR, BB, HBP, SB, SO, R, RBI = range(11)


def prod_proj(r):
    """The SHIPPED production model shape (marcel + home-quality + platoon cell)."""
    se = skill_eb(r, K=60, w23=0.5, w24=1.0)
    of = clamp(1 - 0.3 * (k9_blend(r) / LG_K9 - 1), 0.82, 1.18)
    rf = clamp(1 + 0.2 * (era_blend(r) / LG_ERA - 1), 0.85, 1.15)
    p = se * pa_for(r) * r["park"] * of * rf
    p *= HOME_QUALITY[bool(r["home"])]
    p *= PLATOON_CELL.get((r["bhand"], r["sp_hand"]), 1.0)
    return p


def sr_from_row(r):
    """season_hitting-shaped stat dict from the row's c24+c25 component sums."""
    c = [a + b for a, b in zip(r["c24"], r["c25"])]
    if c[PA] < 30:
        return None
    return {"plateAppearances": c[PA], "hits": c[S1] + c[S2] + c[S3] + c[HR],
            "doubles": c[S2], "triples": c[S3], "homeRuns": c[HR],
            "baseOnBalls": c[BB], "hitByPitch": c[HBP], "stolenBases": c[SB]}


def lab_layer(n_games=40, n_sims=3000, seed=3):
    rowix = {(r["date"], r["pid"]): r for r in ROWS}
    files = sorted((ROOT / "data/bt_boxscores").glob("*.json"))
    games = [json.loads(f.read_text()) for f in files]
    games = [g for g in games if g["date"] >= "2025-06-01"]     # test window only
    rng = np.random.default_rng(seed)
    games = list(rng.choice(games, size=min(n_games, len(games)), replace=False))

    # real team-run distribution over the SAME window (hlog per-game runs)
    cache = json.loads((ROOT / "data/backtest_cache.json").read_text())
    hlog = {}
    for k, v in cache.items():
        if k.startswith("hlog:"):
            hlog[int(k.split(":")[1])] = {d: st for d, st in v}
    real_runs = []
    for g in games:
        for side in ("home", "away"):
            tot = 0
            for pid, _n, _s in g[side]["lineup"]:
                st = hlog.get(pid, {}).get(g["date"])
                tot += (st.get("runs", 0) or 0) if st else 0
            real_runs.append(tot)

    sim_runs, d_corr, opp_corr = [], defaultdict(list), []
    marg = {"real": defaultdict(list), "sim": defaultdict(list)}
    for g in games:
        pool = []
        for side, other in (("home", "away"), ("away", "home")):
            team = side  # per-game pseudo team key
            for pid, name, slot in g[side]["lineup"]:
                r = rowix.get((g["date"], pid))
                if r is None:
                    continue
                pool.append({"name": f"{pid}", "pos": {"OF"}, "team": team,
                             "slot": slot, "home": side == "home",
                             "proj": prod_proj(r), "salary": 4000,
                             "_actual": r["actual"], "_sr": sr_from_row(r)})
            pool.append({"name": f"SP{side}", "pos": {"P"}, "team": team,
                         "opp_team": other, "proj": 12.0, "salary": 8000,
                         "outs_mean": 17.0})
        season_rates = {p["name"]: p.get("_sr") for p in pool if p.get("_sr")}
        scores, meta = dfs_sim.simulate_slate(pool, n_sims=n_sims, seed=seed,
                                              season_rates=season_rates)
        for t in ("home", "away"):
            sim_runs.extend(meta["team_runs"][t].tolist())
        # marginals by proj bucket
        for i, p in enumerate(pool):
            if "P" in p["pos"]:
                continue
            b = "lo" if p["proj"] < 6 else ("mid" if p["proj"] < 9 else "hi")
            marg["real"][b].append(p["_actual"])
            marg["sim"][b].append(scores[:, i])
        # teammate corr by batting distance + pitcher-vs-stack corr
        for t, other in (("home", "away"), ("away", "home")):
            hs = [i for i, p in enumerate(pool) if p["team"] == t and "P" not in p["pos"]]
            hs = sorted(hs, key=lambda i: pool[i]["slot"])
            for a in range(len(hs)):
                for b in range(a + 1, len(hs)):
                    d = min(abs(pool[hs[a]]["slot"] - pool[hs[b]]["slot"]),
                            9 - abs(pool[hs[a]]["slot"] - pool[hs[b]]["slot"]))
                    if 1 <= d <= 4:
                        c = np.corrcoef(scores[:, hs[a]], scores[:, hs[b]])[0, 1]
                        d_corr[d].append(c)
            spi = next(i for i, p in enumerate(pool) if p["team"] == other and "P" in p["pos"])
            stack = scores[:, hs].sum(1)
            opp_corr.append(np.corrcoef(scores[:, spi], stack)[0, 1])

    print(f"\n== LAB LAYER ({len(games)} games x {n_sims} sims) ==")
    print(f"team runs   real mean {statistics.mean(real_runs):.2f} std {statistics.stdev(real_runs):.2f}"
          f"   sim mean {statistics.mean(sim_runs):.2f} std {statistics.stdev(sim_runs):.2f}")
    for b in ("lo", "mid", "hi"):
        ra = np.array(marg["real"][b], float)
        sa = np.concatenate(marg["sim"][b]) if marg["sim"][b] else np.array([0.0])
        print(f"marginals[{b}] n={len(ra)}  P(<=1): real {np.mean(ra <= 1):.3f} sim {np.mean(sa <= 1):.3f}"
              f"   P(>=10): real {np.mean(ra >= 10):.3f} sim {np.mean(sa >= 10):.3f}"
              f"   P(>=20): real {np.mean(ra >= 20):.3f} sim {np.mean(sa >= 20):.3f}"
              f"   std: real {ra.std():.2f} sim {sa.std():.2f}")
    tgt = {1: 0.167, 2: 0.144, 3: 0.122, 4: 0.107}
    for d in (1, 2, 3, 4):
        print(f"teammate corr d={d}: sim {statistics.mean(d_corr[d]):+.3f}  (real target {tgt[d]:+.3f})")
    print(f"pitcher vs opposing stack corr: sim {statistics.mean(opp_corr):+.3f}  (real measured: -0.672)")


# --------------------------------------------------------------------------
def measure_stack_dist():
    cnt = Counter()
    for f in sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv"))):
        with open(f, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                lu = (row.get("Lineup") or "").strip()
                if not lu:
                    continue
                # tokens like: 1B Name Name 2B Name ... P Name ... -- split on
                # position tags, count hitters per team is impossible without a
                # roster map; instead count duplicate LASTNAME heuristic? No --
                # use the per-date proj log to map names to teams below.
                pass
    # do it properly: per contest -> date -> proj-log team map
    by_date = load_proj_log()
    candidate_dates = sorted(by_date.keys())
    POS = {"P", "C", "1B", "2B", "3B", "SS", "OF"}
    for f in sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv"))):
        contest = parse_contest_file(f)
        date, frac, _ = infer_date_by_ground_truth(contest, candidate_dates)
        if frac < 0.7:
            continue
        team_of = {k: v.get("team") for k, v in by_date[date].items()}
        seen = set()
        with open(f, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                lu = (row.get("Lineup") or "").strip()
                eid = row.get("EntryId")
                if not lu or eid in seen:
                    continue
                seen.add(eid)
                toks = lu.split()
                names, cur_pos, cur = [], None, []
                for t in toks:
                    if t in POS:
                        if cur_pos and cur:
                            names.append((cur_pos, " ".join(cur)))
                        cur_pos, cur = t, []
                    else:
                        cur.append(t)
                if cur_pos and cur:
                    names.append((cur_pos, " ".join(cur)))
                tc = Counter()
                for pos, nm in names:
                    if pos == "P":
                        continue
                    t = team_of.get(norm(nm))
                    if t:
                        tc[t] += 1
                if tc:
                    cnt[max(tc.values())] += 1
    tot = sum(cnt.values())
    print(f"\n== FIELD PRIMARY-STACK DISTRIBUTION ({tot} real entries) ==")
    for k in sorted(cnt):
        print(f"  {k}-stack: {cnt[k] / tot:.3f}")
    return {k: round(v / tot, 3) for k, v in cnt.items()}


# --------------------------------------------------------------------------
def contest_layer(n_sims=2000, field_mult=3.0, seed=11):
    by_date = load_proj_log()
    candidate_dates = sorted(by_date.keys())
    sh = {}
    try:
        raw = json.loads((ROOT / "data/dfs_season_hitting.json").read_text())
        for k, v in (raw.get("d") or raw).items():
            if isinstance(v, dict):
                nm = k.split("|", 1)[1] if "|" in k else k
                sh[norm(nm)] = v
    except Exception:
        pass
    from scripts.dfs_calibration import load_contest_type
    _raw_meta = json.loads((ROOT / "data/contest_meta.json").read_text())
    meta_types = {cid: (v if isinstance(v, str) else v.get("type", "unknown"))
                  for cid, v in _raw_meta.items()}

    print(f"\n== CONTEST REPLAY LAYER ==")
    print(f"{'date':11} {'type':4} {'entries':>7} | {'field p50':>9} {'real p50':>8} | "
          f"{'field p90':>9} {'real p90':>8} | {'sim pct':>7} {'real pct':>8}")
    pooled_gap = []
    for f in sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv"))):
        cid = Path(f).stem.split("-")[-1]
        contest = parse_contest_file(f)
        date, frac, _ = infer_date_by_ground_truth(contest, candidate_dates)
        if frac < 0.7:
            continue
        leaderboard = parse_leaderboard(f)
        if len(leaderboard) < 15:
            continue
        rows = by_date[date]
        our = load_our_lineup(date)
        mode = meta_types.get(cid, "unknown")
        our_names = {norm(t) for t in (our.get("cash" if mode == "cash" else "gpp") or [])} if our else set()
        pool, name_ix = [], {}
        for key, r in rows.items():
            # slate-consistency guard (§25's lesson): the proj log for a date can
            # mix a main-slate verification build with the sub-slate actually
            # contested. Only players on THIS contest's own ownership board (or
            # in our logged lineup) belong in the replayed field pool.
            if key not in contest and key not in our_names:
                continue
            try:
                salary = int(float(r["salary"])); proj = float(r["proj"])
            except (TypeError, ValueError):
                continue
            raw_pos = (r.get("pos") or "").strip().upper()
            # the proj log stores an ALREADY-PARSED pos set ("P", "1B/OF"), and
            # parse_pos doesn't map a literal "P" (only SP/RP) -- handle it
            pos = {"P"} if raw_pos == "P" else dfs.parse_pos(raw_pos)
            if not pos:
                continue
            slot = None
            conf = r.get("conf") or ""
            if "slot" in conf:
                try:
                    slot = int(conf.split("slot")[1].rstrip("*PROJ").rstrip("*"))
                except ValueError:
                    slot = None
            own_real = contest.get(key, {}).get("pct_drafted")
            entry = {"name": r["player"], "pos": pos, "team": r["team"], "salary": salary,
                     "proj": proj, "slot": slot,
                     "own": own_real if own_real is not None else float(r.get("own") or 1.0)}
            name_ix[key] = len(pool)
            pool.append(entry)
        if sum(1 for p in pool if "P" in p["pos"]) < 2 or len(pool) < 30:
            continue
        # opp/home wiring from the real schedule (free, cached)
        sched_p = ROOT / "data" / "actuals_cache" / f"sched_{date}.json"
        try:
            if sched_p.exists():
                sched = json.loads(sched_p.read_text())
            else:
                sched = dfs._get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}")
                sched_p.write_text(json.dumps(sched))
            abbr = dfs.team_id_to_abbr()
            opp, home = {}, {}
            for d0 in sched.get("dates", []):
                for g in d0.get("games", []):
                    if g.get("gameType") != "R":
                        continue
                    h = abbr.get(str(g["teams"]["home"]["team"]["id"]))
                    a = abbr.get(str(g["teams"]["away"]["team"]["id"]))
                    if h and a:
                        opp[h], opp[a] = a, h
                        home[h], home[a] = True, False
            for p in pool:
                p["opp_team"] = opp.get(p["team"])
                p["home"] = home.get(p["team"], False)
        except Exception:
            pass
        season_rates = {p["name"]: sh.get(norm(p["name"])) for p in pool}
        scores, _ = dfs_sim.simulate_slate(pool, n_sims=n_sims, seed=seed,
                                           season_rates=season_rates)
        rng = np.random.default_rng(seed)
        field = dfs_sim.generate_field(pool, int(min(len(leaderboard) * field_mult, 1200)), rng=rng)
        if len(field) < 20:
            print(f"{date:11} {meta_types.get(cid, '?'):4} field generation failed"); continue
        our_ix = [name_ix[nm] for nm in our_names if nm in name_ix]
        real_pts = sorted(r[2] for r in leaderboard)
        rp50 = float(np.percentile(real_pts, 50)); rp90 = float(np.percentile(real_pts, 90))
        if len(our_ix) >= 9:
            eq = dfs_sim.contest_equity(scores, our_ix, field)
            # our real percentile
            import bisect
            our_real_score = None
            key_scores = {}
            act_rows = rows
            sim_pct, real_pct = eq["mean_pct"], None
            our_score_real = sum(float((contest.get(nm) or {}).get("fpts") or 0) for nm in our_names if nm in contest)
            if our_score_real:
                pos_ct = sum(1 for s in real_pts if s < our_score_real)
                real_pct = 100 * pos_ct / len(real_pts)
            fq = eq["field_q"]
            gap50 = fq[50] - rp50; gap90 = fq[90] - rp90
            pooled_gap.append((gap50, gap90))
            print(f"{date:11} {mode:4} {len(leaderboard):>7} | {fq[50]:>9.1f} {rp50:>8.1f} | "
                  f"{fq[90]:>9.1f} {rp90:>8.1f} | {sim_pct:>6.1f}% "
                  f"{('%.1f%%' % real_pct) if real_pct is not None else 'n/a':>8}")
        else:
            fq_scores = np.stack([scores[:, lu].sum(1) for lu in field], 1)
            fq50 = float(np.percentile(fq_scores, 50)); fq90 = float(np.percentile(fq_scores, 90))
            pooled_gap.append((fq50 - rp50, fq90 - rp90))
            print(f"{date:11} {mode:4} {len(leaderboard):>7} | {fq50:>9.1f} {rp50:>8.1f} | "
                  f"{fq90:>9.1f} {rp90:>8.1f} | (no logged lineup match)")
    if pooled_gap:
        g50 = statistics.mean(a for a, _ in pooled_gap)
        g90 = statistics.mean(b for _, b in pooled_gap)
        print(f"\npooled field-quantile bias: p50 {g50:+.1f} pts, p90 {g90:+.1f} pts  (0 = perfectly calibrated)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--sims", type=int, default=3000)
    ap.add_argument("--skip-lab", action="store_true")
    args = ap.parse_args()
    if not args.skip_lab:
        lab_layer(n_games=args.games, n_sims=args.sims)
    measure_stack_dist()
    contest_layer()
