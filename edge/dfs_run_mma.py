"""Build one DK MMA Classic slate end to end: board, cash lineup, GPP lineup.

The single entry point both scripts/dfs_lineups_mma.py and
pages/7_🥊_MMA_DFS.py call, so a lineup on the phone and a lineup on the
desktop are the same lineup.

THE PIPELINE
  DK draftables     salaries, the bouts (competitionId), fight order, weigh-in
                    weight, DK's own FPPF, who is scratched
  DK Sportsbook     the calibrated outcome table per bout (edge/mma_market.py),
                    from data/odds_snapshot_dfs_mma.json
  UFCStats          each fighter's style index (edge/mma_sim.py)
  simulator         N whole cards -> a (sims x fighters) DK-points matrix
  field             two simulated fields (cash-sharp, GPP) from projected
                    ownership, scored on the SAME cards
  optimizer         every legal lineup, exhaustively, on P(cash) and P(top 1%)

THREE THINGS THAT GO WRONG ON A REAL CARD, AND WHAT THIS DOES ABOUT EACH
  * A SCRATCH. DraftKings keeps the scratched fighter on the board with no
    competition and isDisabled=true (Mickey Gall, 2026-09-26). Dropped, and
    listed on the page.
  * A REPLACEMENT OPPONENT. On the same card DK DFS had Amaya vs TINA BLACK
    while the sportsbook still had Amaya vs Valesca Machado. Those prices are
    for a different fight and are NOT used: the bout falls back to the
    no-market model and the page flags it, because a confident number for the
    wrong opponent is worse than an honest "no market".
  * THREE SPELLINGS. See edge/mma.py match_fighter.

NO LATE SWAP (gametype 168, allowLateSwap=false). Lock is the first bell of
the first fight on the slate; everything after that is fixed.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from edge import dfs, dfs_mma_theory as theory, dfs_opt_mma, mma
from edge import mma_market as mk, mma_sim as msim

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("edge.dfs_run_mma")
ET = ZoneInfo("America/New_York")

DK_SPORT = mma.DK_SPORT
CLASSIC_GAME_TYPE = mma.CLASSIC_GAME_TYPE
SNAPSHOT = ROOT / "data" / "odds_snapshot_dfs_mma.json"

#: DK playerGameAttributes ids on an MMA board (read off a live payload).
ATTR_OPPONENT = 111
ATTR_FIGHT_NUMBER = 115     # 1 = the main event
ATTR_WEIGHT = 150           # weigh-in weight, lbs

#: How closely a sportsbook event's two names must match a DK bout's to use
#: its prices. A replacement opponent scores ~0.35 on his side (Tina Black vs
#: Valesca Machado); a spelling variant scores 0.79+ (Mahammadali /
#: Mehemmedeli Osmanli).
BOUT_MATCH = 0.72

#: A snapshot older than this is shown as stale on the page. Not refused:
#: fight lines move, but a six-hour-old price is still the best free number,
#: and the page says how old it is.
STALE_HOURS = 6.0


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---------------------------------------------------------------------------
# The slate
# ---------------------------------------------------------------------------
CAPTAIN_GAME_TYPE = mma.CAPTAIN_GAME_TYPE
CPT_SLOT = 614


def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """Every DK MMA salary-cap draft group this builds, soonest first: Classic
    (168) and Captain Mode (169). The main card is a Classic group with an
    empty suffix; DK's " (Late)" slate is CAPTAIN MODE -- the late fights only,
    one fighter at 1.5x points for 1.5x salary."""
    groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    out = []
    for g in groups:
        gt = g.get("GameTypeId")
        if gt not in (CLASSIC_GAME_TYPE, CAPTAIN_GAME_TYPE):
            continue
        suffix = (g.get("ContestStartTimeSuffix") or "").strip().strip("()")
        captain = gt == CAPTAIN_GAME_TYPE
        label = (suffix or "Main") + (" · Captain" if captain else "")
        out.append({"gid": g.get("DraftGroupId"), "label": label,
                    "mode": "captain" if captain else "classic",
                    "fights": g.get("GameCount") or 0, "start": g.get("StartDate"),
                    "featured": g.get("DraftGroupTag") == "Featured"})
    out.sort(key=lambda r: (r["start"] or "", r["mode"] != "classic"))
    return out


def resolve_slate(draft_group=None, groups=None):
    rows = classic_groups(groups)
    if draft_group:
        match = next((r for r in rows if r["gid"] == int(draft_group)), None)
        return int(draft_group), (match or {"gid": int(draft_group), "label": "(explicit)"})
    if not rows:
        return None, {"error": "DraftKings is listing no MMA Classic slates."}
    main = [r for r in rows if r["label"] == "Main"]
    return (main or rows)[0]["gid"], (main or rows)[0]


def _attrs(p: dict) -> dict:
    return {a.get("id"): a.get("value") for a in p.get("playerGameAttributes") or []}


def read_board(gid: int) -> tuple[list[dict], list[dict], list[str]]:
    """(bouts, fighters, scratched) from DK's draftables for one group.

    A bout is two ENABLED fighters sharing a competitionId. Anything else --
    a disabled fighter, one with no competition, a competition with only one
    fighter left -- is a scratch.
    """
    try:
        raw = dfs._draftables_raw(gid)
    except Exception:                                       # noqa: BLE001
        # Streamlit Cloud cannot reach DK's draftables (Akamai 403s a
        # datacenter IP) and reads the desktop's snapshot instead -- which
        # does not exist for a group DK has not PRICED yet. That is "not
        # priced", not a crash (2026-09-26: next week's card, the day before).
        return [], [], []
    by_comp: dict = {}
    scratched = []
    for p in raw:
        comp = p.get("competition") or {}
        if not comp.get("competitionId") or p.get("isDisabled"):
            scratched.append(p.get("displayName"))
            continue
        st = {a.get("id"): a.get("value") for a in p.get("draftStatAttributes") or []}
        try:
            fppf = float(st.get(mma.FPPF_STAT_ID))
        except (TypeError, ValueError):
            fppf = None
        at = _attrs(p)
        try:
            weight = int(float(at.get(ATTR_WEIGHT) or 0))
        except ValueError:
            weight = 0
        cpt = p.get("rosterSlotId") == CPT_SLOT
        prev = by_comp.get(comp.get("competitionId"), {}).get(p.get("displayName"))
        f = {"name": p.get("displayName"), "dk_id": p.get("playerId"),
             "salary": (prev or {}).get("salary", 0) if cpt else int(p.get("salary") or 0),
             "cpt_salary": int(p.get("salary") or 0) if cpt else (prev or {}).get("cpt_salary"),
             "dk_fppf": fppf,
             "record": st.get(-1), "weight": weight,
             "fight_no": int(at.get(ATTR_FIGHT_NUMBER) or 0) or None,
             "comp": comp.get("competitionId"), "matchup": comp.get("name"),
             "start": comp.get("startTime")}
        by_comp.setdefault(f["comp"], {})[f["name"]] = f      # dedupe slot rows
    bouts, fighters = [], []
    for comp, fs in by_comp.items():
        fs = list(fs.values())
        if len(fs) != 2:
            scratched += [f["name"] for f in fs]
            continue
        bouts.append({"comp": comp, "fighters": fs, "start": fs[0]["start"],
                      "fight_no": fs[0]["fight_no"], "matchup": fs[0]["matchup"]})
    bouts.sort(key=lambda b: (b["start"] or "", -(b["fight_no"] or 0)))
    for b in bouts:
        fighters.extend(b["fighters"])
    return bouts, fighters, scratched


def division(weight: int) -> int:
    """Weigh-in weight -> the division limit it was made for (non-title bouts
    get a one-pound allowance, and DK reports what the scale said)."""
    for lim in (115, 125, 135, 145, 155, 170, 185, 205, 265):
        if weight and weight <= lim + 1.5:
            return lim
    return 0


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
def load_book() -> tuple[dict, dict]:
    """(parsed events, info) from the committed DK Sportsbook snapshot --
    main's copy on GitHub when it is newer than the deployed one."""
    from edge import repo_files
    found = repo_files.read(SNAPSHOT)
    if found is None:
        return {}, {"error": "no MMA odds snapshot -- run scripts/mma_odds_capture.py"}
    bundle = found.json()
    gen = _parse_time(bundle.get("generated_at"))
    age_h = ((datetime.now(timezone.utc) - gen).total_seconds() / 3600.0
             if gen else None)
    return mk.parse_book(bundle), {"generated_at": bundle.get("generated_at"),
                                   "age_hours": age_h,
                                   "stale": age_h is None or age_h > STALE_HOURS,
                                   "problem": getattr(found, "problem", None)}


def match_bout(bout: dict, events: dict) -> tuple[dict | None, bool, float]:
    """(event, flipped, score) -- the sportsbook fight that IS this DK bout.

    Both names must match. A bout where only one side matches an event is a
    REPLACEMENT OPPONENT and gets no prices (see the module docstring)."""
    a, b = (f["name"] for f in bout["fighters"])
    best, best_s, flip = None, 0.0, False
    for ev in events.values():
        s1 = min(mma.name_similarity(a, ev["a"]), mma.name_similarity(b, ev["b"]))
        s2 = min(mma.name_similarity(a, ev["b"]), mma.name_similarity(b, ev["a"]))
        if max(s1, s2) > best_s:
            best, best_s, flip = ev, max(s1, s2), s2 > s1
    if best is None or best_s < BOUT_MATCH:
        return None, False, best_s
    return best, flip, best_s


def _orient(cells: dict, flip: bool) -> dict:
    if not flip:
        return dict(cells)
    return {"A_KO": cells["B_KO"], "A_SUB": cells["B_SUB"], "A_DEC": cells["B_DEC"],
            "B_KO": cells["A_KO"], "B_SUB": cells["A_SUB"], "B_DEC": cells["A_DEC"]}


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
def build_board(gid: int, n_sims: int = 20000, seed: int = 0) -> tuple:
    """(pool, points, bouts, info) for one DK MMA slate."""
    bouts, fighters, scratched = read_board(gid)
    info = {"fights": len(bouts), "fighters": len(fighters), "scratched": scratched,
            "draftables_source": dfs.LAST_DRAFTABLES_SOURCE}
    if not fighters:
        return [], None, [], {**info, "unpriced": True}
    if not any(f["salary"] for f in fighters):
        return [], None, [], {**info, "unpriced": True}

    events, book = load_book()
    info["book"] = book
    info["ufcstats"] = mma.ensure_fresh()
    today = datetime.now(ET).date()
    hist = mma.fighter_history(before=today)
    style = msim.style_indices(before=today)

    # The salary -> win-probability slope for bouts with no market, fitted on
    # THIS board's priced bouts. DraftKings spreads salaries differently by
    # game type: the same fight was a $2,400 gap on the 2026-09-26 Classic
    # board and $4,200 on the Captain board, so a Classic-fitted slope put a
    # no-market favourite at 80% on the Captain board against 71.5% on Classic.
    matched = [(b, *match_bout(b, events)) for b in bouts]
    xs, ys = [], []
    for b, ev, flip, _s in matched:
        if ev is not None and ev.get("ml"):
            pa = mk.win_prob(*ev["ml"])
            pa = 1 - pa if flip else pa
            xs.append((b["fighters"][0]["salary"] - b["fighters"][1]["salary"]) / 1000.0)
            ys.append(math.log(pa / (1 - pa)))
    k_local = (sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
               if len(xs) >= 4 and sum(x * x for x in xs) > 0 else mk.SALARY_LOGIT_PER_K)
    info["salary_logit_per_k"] = round(k_local, 3)

    pool, sim_bouts = [], []
    no_market, replaced, unmatched = [], [], []
    for b, ev, flip, score in matched:
        wt = max(f["weight"] for f in b["fighters"])
        lbs = division(wt)
        keys = []
        for f in b["fighters"]:
            names = [f["name"]]
            if ev is not None:
                i = [ev["a"], ev["b"]][(b["fighters"].index(f) + flip) % 2]
                names.append(i)
            k, s = mma.match_fighter(names, hist, lbs)
            keys.append(k)
            f["key"] = k
            f["ufc_fights"] = len(hist.get(k, [])) if k else 0
        women = any(bool(hist[k][-1]["women"]) for k in keys if k) or lbs == 115
        sched = 3
        if ev is not None and ev.get("sched_rounds"):
            sched = ev["sched_rounds"]
        elif b["fight_no"] == 1:
            sched = 5                       # every UFC main event is scheduled for five
        if ev is not None:
            tbl = mk.fight_markets_table(ev, lbs, women)
            tbl["cells"] = _orient(tbl["cells"], flip)
            source = tbl["source"]
        else:
            sal_a, sal_b = (f["salary"] for f in b["fighters"])
            tbl = mk.outcome_table(None, None, lbs, women, sched,
                                   salary_diff=(sal_a - sal_b) * k_local / mk.SALARY_LOGIT_PER_K)
            source = tbl["source"]
            names = [f["name"] for f in b["fighters"]]
            # One side matched a sportsbook fight and the other did not: the
            # book is pricing this fighter against someone else.
            one_side = any(max(mma.name_similarity(n, e["a"]), mma.name_similarity(n, e["b"])) >= 0.9
                           for n in names for e in events.values())
            (replaced if one_side else no_market).append(" vs ".join(names))
        idx = []
        for f in b["fighters"]:
            s = style.get(f["key"]) if f["key"] else None
            idx.append((s["off"], s["def"]) if s else (msim.DEBUT_OFF, msim.DEBUT_DEF))
            if not f["key"] and f.get("dk_fppf"):
                unmatched.append(f["name"])
        tim = msim.timing(tbl["cells"], sched)
        sim_bouts.append({"cells": tbl["cells"], "p_draw": tbl["p_draw"],
                          "sched": sched, "idx": idx, "timing": tim})
        cells = tbl["cells"]
        pa, pb = msim.win_probs(cells)
        for i, f in enumerate(b["fighters"]):
            side = "A_" if i == 0 else "B_"
            opp = b["fighters"][1 - i]
            fin = cells[side + "KO"] + cells[side + "SUB"]
            r1 = float(tim[side + "KO"][:msim.BINS_PER_ROUND].sum()
                       + tim[side + "SUB"][:msim.BINS_PER_ROUND].sum())
            ev_side = None
            if ev is not None:
                ev_side = (i + flip) % 2
            pool.append({
                **{k: f[k] for k in ("name", "dk_id", "salary", "cpt_salary",
                                     "dk_fppf", "record", "weight", "fight_no",
                                     "start", "key", "ufc_fights")},
                "opponent": opp["name"], "bout": b["matchup"], "sched": sched,
                "lbs": lbs, "women": women, "source": source,
                "p_win": round(pa if i == 0 else pb, 4),
                "p_finish": round(fin, 4), "p_r1": round(r1, 4),
                "p_dec_win": round(cells[side + "DEC"], 4),
                "off": round(idx[i][0], 3), "def_opp": round(idx[1 - i][1], 3),
                "book_sig": (ev["sig"].get(ev_side) if ev_side is not None else None),
                "book_td": (ev["td"].get(ev_side) if ev_side is not None else None),
                "book_name": ([ev["a"], ev["b"]][ev_side] if ev_side is not None else None),
            })
    info.update({"no_market": no_market, "replaced": replaced, "unmatched": unmatched,
                 "debuts": [d["name"] for d in pool if not d["key"]]})

    out = msim.simulate(sim_bouts, n_sims=n_sims, seed=seed)
    points = out["points"]
    for j, d in enumerate(pool):
        col = points[:, j]
        d.update({"proj": round(float(col.mean()), 2), "sd": round(float(col.std()), 2),
                  "floor": round(float(np.percentile(col, 25)), 1),
                  "median": round(float(np.percentile(col, 50)), 1),
                  "ceil": round(float(np.percentile(col, 90)), 1),
                  "value": round(1000.0 * float(col.mean()) / d["salary"], 2)
                  if d["salary"] else 0.0})
    return pool, points, sim_bouts, info


# ---------------------------------------------------------------------------
# The whole slate, in one call
# ---------------------------------------------------------------------------
def _opponents(pool: list) -> np.ndarray:
    idx = {d["name"]: i for i, d in enumerate(pool)}
    return np.array([idx.get(d["opponent"], -1) for d in pool])


def build_slate(draft_group=None, n_sims: int = 20000, seed: int = 0,
                groups: list[dict] | None = None, persist: bool = True,
                locked: list[str] | None = None,
                banned: list[str] | None = None) -> dict:
    """Board + a CASH lineup + a GPP lineup for one DK MMA Classic slate."""
    all_groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    gid, meta = resolve_slate(draft_group, all_groups)
    slates = classic_groups(all_groups)
    if gid is None:
        return {"error": meta.get("error"), "slates": slates}
    pool, points, sim_bouts, info = build_board(gid, n_sims=n_sims, seed=seed)
    if info.get("unpriced"):
        return {"unpriced": True, "gid": gid, "meta": meta, "slates": slates,
                "stats": info}
    if not pool:
        return {"error": "No fighters on this slate.", "gid": gid, "meta": meta,
                "slates": slates, "stats": info}

    sal = np.array([d["salary"] for d in pool])
    opp = _opponents(pool)
    captain = meta.get("mode") == "captain" or all(d.get("cpt_salary") for d in pool)
    name_idx = {d["name"]: i for i, d in enumerate(pool)}
    lk = [name_idx[n] for n in (locked or []) if n in name_idx]
    bn = [name_idx[n] for n in (banned or []) if n in name_idx]
    if captain:
        cpt_sal = np.array([d.get("cpt_salary") or round(1.5 * d["salary"]) for d in pool])
        everything = dfs_opt_mma.legal_captain_lineups(sal, cpt_sal)
        lineups = (dfs_opt_mma.legal_captain_lineups(sal, cpt_sal, locked=lk, banned=bn)
                   if (lk or bn) else everything)
    else:
        everything = dfs_opt_mma.legal_lineups(sal)
        lineups = (dfs_opt_mma.legal_lineups(sal, locked=lk, banned=bn)
                   if (lk or bn) else everything)
    own_gpp, fl_gpp, w_gpp = theory.field_model(pool, everything, opp,
                                                theory.FIELD_TAU_GPP, captain)
    own_cash, fl_cash, w_cash = theory.field_model(pool, everything, opp,
                                                   theory.FIELD_TAU_CASH, captain)
    cpt_own = (theory.captain_ownership(len(pool), fl_gpp, w_gpp) if captain
               else np.zeros(len(pool)))
    for d, og, oc, cp in zip(pool, own_gpp, own_cash, cpt_own):
        d["own"] = round(float(og), 1)
        d["own_cash"] = round(float(oc), 1)
        d["own_cpt"] = round(float(cp), 1)
    field_gpp = theory.sample_field(fl_gpp, w_gpp, seed=seed + 1)
    field_cash = theory.sample_field(fl_cash, w_cash, seed=seed + 2)
    lines = {"cash": theory.cash_line(points, field_cash, captain),
             "gpp": theory.gpp_line(points, field_gpp, captain)}

    cash = dfs_opt_mma.optimize(points, sal, lines, "cash", lineups=lineups,
                                captain=captain)
    gpp = dfs_opt_mma.optimize(points, sal, lines, "gpp", lineups=lineups,
                               captain=captain)
    for res in (cash, gpp):
        if "idx" in res:
            res.update(_lineup_result(pool, points, res["idx"], lines, captain))

    log_result = None
    if persist:
        try:
            log_result = log_forward_test(pool, cash, gpp, gid, meta, info)
        except Exception as exc:                            # noqa: BLE001
            log.warning("dfs_run_mma: forward-test logging failed: %s", exc)
    return {"gid": gid, "meta": meta, "slates": slates, "pool": pool,
            "points": points, "stats": info, "cash": cash, "gpp": gpp,
            "lines": {k: {"median": round(float(np.median(v)), 1)} for k, v in lines.items()},
            "_lines": lines, "captain": captain, "log": log_result}


def _lineup_result(pool, points, idx, lines, captain: bool = False) -> dict:
    """`idx` in slot order; for Captain Mode idx[0] IS the captain."""
    w = np.ones(len(idx))
    if captain:
        w[0] = dfs_opt_mma.CPT_MULT
    t = points[:, idx] @ w
    fights = {pool[i]["bout"] for i in idx}
    order = list(idx) if captain else sorted(idx, key=lambda i: -pool[i]["proj"])
    rows = []
    for k, i in enumerate(order):
        d = dict(pool[i])
        if captain and k == 0:
            d.update({"cpt": True, "salary": pool[i].get("cpt_salary") or d["salary"],
                      "proj": round(dfs_opt_mma.CPT_MULT * d["proj"], 2),
                      "floor": round(dfs_opt_mma.CPT_MULT * d["floor"], 1),
                      "ceil": round(dfs_opt_mma.CPT_MULT * d["ceil"], 1),
                      "own": d.get("own_cpt", d["own"])})
        rows.append(d)
    return {"lineup": rows, "captain": captain,
            "salary": int(sum(d["salary"] for d in rows)),
            "proj": round(float(t.mean()), 1), "sd": round(float(t.std()), 1),
            "floor": round(float(np.percentile(t, 25)), 1),
            "median": round(float(np.percentile(t, 50)), 1),
            "ceil": round(float(np.percentile(t, 90)), 1),
            "p99": round(float(np.percentile(t, 99)), 1),
            "p_cash": round(float((t > lines["cash"]).mean()), 4),
            "p_top1": round(float((t > lines["gpp"]).mean()), 4),
            "own": round(float(sum(d["own"] for d in rows)), 1),
            "exp_wins": round(float(sum(pool[i]["p_win"] for i in idx)), 2),
            "same_fight": len(fights) < len(idx)}


PROJ_LOG_COLS = ("date", "gid", "bout", "fighter", "key", "salary", "sched",
                 "source", "p_win", "p_finish", "p_r1", "proj", "sd", "floor",
                 "median", "ceil", "own", "own_cash", "dk_fppf", "off", "def_opp")


def _slate_date(meta: dict, pool: list) -> str | None:
    dt = _parse_time(meta.get("start")) or _parse_time(pool[0].get("start") if pool else None)
    return dt.astimezone(ET).date().isoformat() if dt else None


def log_forward_test(pool, cash, gpp, gid, meta, info,
                     root: Path | None = None) -> dict:
    """Persist a build, so a real contest result has something to join to.

    The rule every sport here learned the hard way (MLB 2026-09-17, the lost
    7/24-7/31 slates): an unlogged slate is unrecoverable. And a build for a
    PAST date never overwrites the log -- a review rebuild clobbered real
    forward-test data once already.
    """
    root = root or ROOT
    result = {"logged": False, "n": 0, "date": None}
    day = _slate_date(meta, pool)
    if day is None:
        return result
    result["date"] = day
    if day < datetime.now(ET).date().isoformat():
        result["skipped_past_date"] = True
        return result
    plog = root / "data" / "dfs_proj_log_mma.csv"
    plog.parent.mkdir(parents=True, exist_ok=True)
    prior = list(csv.DictReader(open(plog))) if plog.exists() else []
    mine = [r for r in prior if r.get("date") == day and str(r.get("gid")) == str(gid)]
    # AFTER LOCK, AN EXISTING LOG IS NEVER OVERWRITTEN. Once the first bout
    # starts, the sportsbook drops its market, so a rebuild prices that bout
    # from salaries and quietly replaces a clean pre-bell forward test with a
    # contaminated one -- found on the first card, 2026-09-26, twenty minutes
    # in. The date guard above cannot see it: it is still the same day.
    starts = [t for t in (_parse_time(d.get("start")) for d in pool) if t]
    if mine and starts and datetime.now(timezone.utc) >= min(starts):
        result["skipped_after_lock"] = True
        return result
    keep = [r for r in prior if not (r.get("date") == day and str(r.get("gid")) == str(gid))]
    with plog.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(PROJ_LOG_COLS)
        for r in keep:
            w.writerow([r.get(c, "") for c in PROJ_LOG_COLS])
        for d in pool:
            w.writerow([day, gid, d["bout"], d["name"], d.get("key") or "",
                        d["salary"], d["sched"], d["source"], d["p_win"],
                        d["p_finish"], d["p_r1"], d["proj"], d["sd"], d["floor"],
                        d["median"], d["ceil"], d["own"], d.get("own_cash", ""),
                        d.get("dk_fppf") or "", d["off"], d["def_opp"]])
    result.update({"logged": True, "n": len(pool)})
    lpath = root / "data" / f"dfs_lineups_mma_{day}.csv"
    # One file per DAY holds every slate that day (the main card AND the late
    # Captain slate), so another group's rows are kept, not overwritten.
    others = ([r for r in csv.DictReader(lpath.open()) if str(r.get("gid")) != str(gid)]
              if lpath.exists() else [])
    with lpath.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gid", "mode", "fighter", "salary", "proj", "p_win", "own", "cpt"])
        for r in others:
            w.writerow([r.get(c, "") for c in ("gid", "mode", "fighter", "salary",
                                               "proj", "p_win", "own", "cpt")])
        for mode, res in (("cash", cash), ("gpp", gpp)):
            for d in (res or {}).get("lineup", []):
                w.writerow([gid, mode, d["name"], d["salary"], d["proj"],
                            d["p_win"], d["own"], int(bool(d.get("cpt")))])
    result["lineup_file"] = str(lpath.relative_to(root))
    return result


def lineup_rows(result: dict) -> list[dict]:
    if not result or "lineup" not in result:
        return []
    return [{"fighter": d["name"], "cpt": bool(d.get("cpt")),
             "opponent": d["opponent"], "salary": d["salary"],
             "p_win": d["p_win"], "p_finish": d["p_finish"], "proj": d["proj"],
             "floor": d["floor"], "ceil": d["ceil"], "own": d["own"],
             "sched": d["sched"], "source": d["source"]} for d in result["lineup"]]
