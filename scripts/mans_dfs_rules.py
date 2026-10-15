#!/usr/bin/env python3
"""Jeff Mans's NFL DFS construction rules (cash and GPP), tested on the real field.

Adam, 2026-10-14: "his philosophy to put together a DFS lineup for football, both GPP
and cash." The rules come from One MANS Opinion transcripts (MANS_METHODOLOGY.md lists
each with its episode). Each rule becomes a lineup feature; the test is whether real
lineups with that feature finished better, HOLDING OUR PRE-LOCK PROJECTION FIXED --
otherwise "lineups with X won" just means "lineups with better players won".

The slate is the unit (every entry in a contest shares one realisation), so the result
is reported per slate and summarised as a sign count, never as a pooled p-value over
entries. 5 main slates: anything below 5-of-5 or 4-of-5 is noise until more arrive.

    effect = coefficient on the feature in  outcome ~ feature + proj_sum  (within contest,
             linear probability, outcome in {top 1%, cashed}); reported in percentage
             points per unit of the feature (per lineup for 0/1 features).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from edge.names import norm   # noqa: E402

DB = ROOT / "data" / "contests.db"
# (contest_id, label). The 5 Sunday main-slate single-entry GPPs and their Double Ups.
GPP = ((193028246, "9/13"), (195661382, "9/20"), (195921991, "9/27"),
       (196169741, "10/4"), (196420207, "10/11"))
CASH = ((195614469, "9/13"), (195659767, "9/20"), (195920333, "9/27"),
        (196170291, "10/4"), (196601669, "10/11"))
REBUILT = ROOT / "data" / "cache" / "mans_rebuilt_pool_{gid}.json"   # scripts/mans_claims.py
CHALK, LOW = 20.0, 5.0       # % drafted: "chalk" and "low-owned" cut-offs
WEEK = {193028246: 1, 195661382: 2, 195921991: 3, 196169741: 4, 196420207: 5,
        195614469: 1, 195659767: 2, 195920333: 3, 196170291: 4, 196601669: 5}
DK_TO_NFLVERSE = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}


def game_totals(week: int) -> dict:
    """{frozenset(teams): closing total} for a 2026 week (data/nflverse_games.csv)."""
    import csv
    out = {}
    with (ROOT / "data" / "nflverse_games.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r["season"] == "2026" and r["game_type"] == "REG" and int(r["week"]) == week \
                    and r["total_line"]:
                out[frozenset((r["away_team"], r["home_team"]))] = float(r["total_line"])
    return out


def slate(con, cid: int):
    """Entries with their players, each player carrying pos/team/opp/salary/proj/own/fpts."""
    dg = con.execute("select draft_group from contest where contest_id=?", (cid,)).fetchone()[0]
    sp = {n: dict(team=t, opp=o, pos=p, salary=s) for n, t, o, p, s in con.execute(
        "select player_norm, team, opp, position, salary from slate_player where draft_group=?",
        (dg,))}
    proj = {n: p for n, p in con.execute(
        "select player_norm, proj from projection where draft_group=? and proj is not null "
        "order by logged_at", (dg,))}
    if not proj and Path(str(REBUILT).format(gid=dg)).exists():
        proj = {norm(p["name"]): p["proj"] for p in json.loads(
            Path(str(REBUILT).format(gid=dg)).read_text())}
    res = {n: (o, f) for n, o, f in con.execute(
        "select player_norm, pct_drafted, fpts from player_result where contest_id=?", (cid,))}
    ent = defaultdict(list)
    for eid, slot, n in con.execute(
            "select entry_id, slot, player_norm from entry_player where contest_id=?", (cid,)):
        info = sp.get(n)
        if info is None:
            continue
        o, f = res.get(n, (0.0, 0.0))
        ent[eid].append(dict(slot=slot, n=n, own=o or 0.0, fpts=f or 0.0, proj=proj.get(n),
                             **info))
    totals = game_totals(WEEK[cid])
    slate_totals = sorted({totals.get(frozenset(DK_TO_NFLVERSE.get(t, t) for t in (i["team"], i["opp"])), 0)
                           for i in sp.values()}, reverse=True)
    top3_total = slate_totals[2] if len(slate_totals) > 2 else 99
    for pl in ent.values():
        for p in pl:
            key = frozenset(DK_TO_NFLVERSE.get(t, t) for t in (p["team"], p["opp"]))
            p["shootout"] = totals.get(key, 0) >= top3_total
    top = sorted(res, key=lambda n: -(res[n][0] or 0))
    for pl in ent.values():
        for p in pl:
            p["top_owned"] = p["n"] == top[0]
            p["top5_owned"] = p["n"] in top[:5]
    out = {eid: dict(players=pl) for eid, pl in ent.items() if len(pl) == 9}
    for eid, rank, cash, top1, payout, pts in con.execute(
            "select entry_id, rank, cash, top1, payout, points from entry_feature "
            "where contest_id=? and live=1", (cid,)):
        if eid in out:
            out[eid].update(rank=rank, cash=cash or 0, top1=top1 or 0, payout=payout or 0.0,
                            points=pts)
    out = {k: v for k, v in out.items() if "rank" in v}
    for v in out.values():       # finished in the top 45%: a double-up's cash line
        v["top45"] = int(v["rank"] <= 0.45 * len(out))
    return out


# --- features: one function per rule, each returning a number per lineup -------------

def _qb(pl):
    return next(p for p in pl if p["slot"] == "QB")


def qb_mates(pl):                 # pass-catchers on the QB's team
    q = _qb(pl)
    return sum(p["team"] == q["team"] and p["pos"] in ("WR", "TE", "RB") for p in pl
               if p is not q and p["slot"] != "DST")


def bring_back(pl):               # skill players from the QB's opponent
    q = _qb(pl)
    return sum(p["team"] == q["opp"] and p["slot"] != "DST" for p in pl)


def game_stack(pl):               # most players (ex-DST) from one game
    c = defaultdict(int)
    for p in pl:
        if p["slot"] != "DST":
            c[tuple(sorted((p["team"], p["opp"])))] += 1
    return max(c.values())


def own_sum(pl):
    return sum(p["own"] for p in pl)


def n_chalk(pl):
    return sum(p["own"] >= CHALK for p in pl)


def n_low(pl):
    return sum(p["own"] < LOW for p in pl)


def salary_left(pl):
    return (50000 - sum(p["salary"] for p in pl)) / 1000      # per $1k


def flex_pos(pos):
    return lambda pl: float(next(p for p in pl if p["slot"] == "FLEX")["pos"] == pos)


def dst_vs_own(pl):               # DST facing one of your own players
    d = next(p for p in pl if p["slot"] == "DST")
    return float(any(p["team"] == d["opp"] for p in pl if p["slot"] != "DST"))


def rb_dst_same(pl):              # RB and DST from the same team (game-script pairing)
    d = next(p for p in pl if p["slot"] == "DST")
    return float(any(p["pos"] == "RB" and p["team"] == d["team"] for p in pl))


def qb_salary(pl):
    return _qb(pl)["salary"] / 1000


def stud_rb(pl):                  # an RB priced $8k+
    return float(any(p["pos"] == "RB" and p["salary"] >= 8000 for p in pl if p["slot"] != "DST"))


def te_salary(pl):
    return next(p for p in pl if p["slot"] == "TE")["salary"] / 1000


def n_punts(pl):                  # "zero fat": skill players at $4,000 or less
    return sum(p["salary"] <= 4000 for p in pl if p["slot"] != "DST")


def core4_share(pl):              # salary share of the 4 priciest players, per 10 pp
    s = sorted((p["salary"] for p in pl), reverse=True)
    return 10 * sum(s[:4]) / sum(s)


def has_top_owned(pl):            # the slate's most-owned player ("free square" / fade)
    return float(any(p.get("top_owned") for p in pl))


def n_top5_owned(pl):             # "drift with the chalk"
    return sum(p.get("top5_owned", False) for p in pl)


def split_games(pl):              # cash diversification: no two non-QB skill players
    c = defaultdict(int)          # from the same game outside the QB stack
    q = _qb(pl)
    for p in pl:
        if p["slot"] in ("DST", "QB") or {p["team"], p["opp"]} == {q["team"], q["opp"]}:
            continue
        c[tuple(sorted((p["team"], p["opp"])))] += 1
    return float(all(v <= 1 for v in c.values()))


def qb_low_own_shootout(pl):      # GPP: a sub-10%-owned QB in a top-3 total game
    q = _qb(pl)
    return float(q["own"] < 10 and q["shootout"])


def qb_low_own(pl):
    return float(_qb(pl)["own"] < 10)


def shootout_stack(pl):           # GPP: QB + 2 pass-catchers + a bring-back
    return float(qb_mates(pl) >= 2 and bring_back(pl) >= 1)


FEATURES = {
    "qb_mates (per mate)": qb_mates,
    "bring_back (per player)": bring_back,
    "game_stack size": game_stack,
    "own_sum (per 10%)": lambda pl: own_sum(pl) / 10,
    "n_chalk >=20% (per player)": n_chalk,
    "n_low <5% (per player)": n_low,
    "salary_left (per $1k)": salary_left,
    "FLEX is RB": flex_pos("RB"),
    "FLEX is WR": flex_pos("WR"),
    "FLEX is TE": flex_pos("TE"),
    "DST vs own player": dst_vs_own,
    "RB+DST same team": rb_dst_same,
    "QB salary (per $1k)": qb_salary,
    "stud RB $8k+": stud_rb,
    "TE salary (per $1k)": te_salary,
    "punts <=$4k (per player)": n_punts,
    "core-4 salary share (per 10pp)": core4_share,
    "has slate's most-owned": has_top_owned,
    "top-5-owned players (per)": n_top5_owned,
    "no same-game pair off-stack": split_games,
    "QB+2 + bring-back": shootout_stack,
    "QB <10% owned": qb_low_own,
    "QB <10% owned in top-3 total game": qb_low_own_shootout,
}


def effect(rows, fn, outcome):
    """pp change in P(outcome) per unit of the feature, with our projection held fixed."""
    X = np.array([[1.0, fn(r["players"]), r["proj"]] for r in rows])
    y = np.array([r[outcome] for r in rows], dtype=float)
    if np.std(X[:, 1]) == 0:
        return None
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    return 100 * beta[1]


def run(contests, outcomes) -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    table = defaultdict(dict)
    sizes = {}
    for cid, label in contests:
        rows = []
        for r in slate(con, cid).values():
            pj = [p["proj"] for p in r["players"]]
            if any(x is None for x in pj):
                continue
            r["proj"] = sum(pj)
            rows.append(r)
        sizes[label] = len(rows)
        for name, fn in FEATURES.items():
            for oc in outcomes:
                table[(name, oc)][label] = effect(rows, fn, oc)
    labels = [lab for _, lab in contests]
    print("lineups with a projection: " + ", ".join(f"{k} {v:,}" for k, v in sizes.items()))
    for oc in outcomes:
        print(f"\n{oc}: pp per unit, projection held fixed  " + "  ".join(f"{x:>6}" for x in labels)
              + "   signs")
        for name in FEATURES:
            vals = [table[(name, oc)].get(lab) for lab in labels]
            ok = [v for v in vals if v is not None]
            sign = f"{sum(v > 0 for v in ok)}+/{sum(v < 0 for v in ok)}-"
            print(f"  {name:28s}" + "".join(f"{v:+7.2f}" if v is not None else "      -"
                                            for v in vals) + f"   {sign}")


if __name__ == "__main__":
    print("== GPP (17,835 / 11,890-entry single-entry Daily Dollar) ==")
    run(GPP, ("top1", "top45"))
    print("\n== CASH (Double Ups, 23-124 entries: little power) ==")
    run(CASH, ("cash",))
