#!/usr/bin/env python3
"""Were the winners' players in our pool? (Adam, 2026-10-29: "if we don't have the
right guys in the pool we're DOA.")

For every contest with a pre-lock projection matched BY DRAFT GROUP (contests.db
contest_match.ok_points), take the winning lineups -- GPP: the top 1% of the field
(at least the winner); cash: every lineup that cashed -- and for each of their
player-slots ask:

  on board    was he on our board at all (a projection row for that draft group)?
  value rank  his rank by our projected points per $1k among board players at his
              primary DK position (1 = our best value there)
  proj rank   the same by projected points
  in Adam's   was he in any of Adam's entries in that contest?

Then the players the winners leaned on most that Adam did not have, with our
projection against what they scored. Cash and GPP are never pooled; the CONTEST is
the unit (n contests / slates printed with every number).

    python3 scripts/pool_coverage.py                 # NBA and NHL
    python3 scripts/pool_coverage.py --sport NHL --top 25
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "contests.db"
#: rank cut-offs reported: "in our top K at his position" by value / by projection
KS = (3, 5, 10)


def board(conn, sport: str, gid, date: str) -> dict:
    """{player_norm: (proj, salary, primary pos)} -- the latest logged row per player
    (the last build before lock)."""
    q = ("SELECT player_norm, proj, salary, pos, logged_at FROM projection WHERE source='ours' AND sport=? "
         "AND date=? AND " + ("draft_group=?" if gid is not None else "draft_group IS NULL"))
    rows = conn.execute(q, (sport, date, gid) if gid is not None else (sport, date)).fetchall()
    out = {}
    for k, p, sal, pos, at in sorted(rows, key=lambda r: r[4] or ""):
        if p is not None and sal:
            out[k] = (float(p), float(sal), (pos or "?").split("/")[0])
    return out


def ranks(b: dict) -> tuple[dict, dict]:
    by_pos = defaultdict(list)
    for k, (p, sal, pos) in b.items():
        by_pos[pos].append(k)
    vr, pr = {}, {}
    for ks in by_pos.values():
        for i, k in enumerate(sorted(ks, key=lambda k: -b[k][0] / b[k][1])):
            vr[k] = i + 1
        for i, k in enumerate(sorted(ks, key=lambda k: -b[k][0])):
            pr[k] = i + 1
    return vr, pr


def contests(conn, sport: str, ctype: str):
    return conn.execute(
        "SELECT c.contest_id, c.slate_date, c.field, c.places_paid, m.proj_draft_group, m.proj_date, c.name "
        "FROM contest c JOIN contest_match m USING (contest_id) "
        "WHERE c.sport=? AND c.type=? AND m.ok_points=1 ORDER BY c.slate_date", (sport, ctype)).fetchall()


def run(conn, sport: str, ctype: str, top: int) -> None:
    cs = contests(conn, sport, ctype)
    if not cs:
        print(f"\n{sport} {ctype}: no contests with a matched projection")
        return
    slots, missed = [], Counter()
    info = {}
    per_contest = []
    for cid, date, field, paid, gid, pdate, name in cs:
        b = board(conn, sport, gid, pdate)
        vr, pr = ranks(b)
        fp = {k: (f, nm) for k, f, nm in conn.execute(
            "SELECT player_norm, fpts, name FROM player_result WHERE contest_id=?", (cid,))}
        own = {k: o for k, o in conn.execute(
            "SELECT player_norm, pct_drafted FROM player_result WHERE contest_id=?", (cid,))}
        live = conn.execute("SELECT COUNT(*) FROM entry WHERE contest_id=? AND n_players>0", (cid,)).fetchone()[0]
        n = max(live, field or 0)
        cut = max(1, math.ceil(0.01 * n)) if ctype == "gpp" else (paid or max(1, int(0.45 * n)))
        win = [e for (e,) in conn.execute(
            "SELECT entry_id FROM entry WHERE contest_id=? AND rank<=? AND n_players>0", (cid, cut))]
        adam = {k for (k,) in conn.execute(
            "SELECT DISTINCT p.player_norm FROM entry_player p JOIN entry e USING (contest_id, entry_id) "
            "WHERE p.contest_id=? AND e.is_adam=1", (cid,))}
        has_adam = conn.execute("SELECT COUNT(*) FROM entry WHERE contest_id=? AND is_adam=1",
                                (cid,)).fetchone()[0] > 0
        cnt = Counter(k for (k,) in conn.execute(
            "SELECT player_norm FROM entry_player WHERE contest_id=? AND entry_id IN (%s)"
            % ",".join("?" * len(win)), (cid, *win))) if win else Counter()
        tot = sum(cnt.values())
        c_on = 0
        for k, c in cnt.items():
            on = k in b
            c_on += c * on
            slots.append({"w": c, "on": on, "vr": vr.get(k), "pr": pr.get(k), "adam": k in adam,
                          "has_adam": has_adam})
            if has_adam and k not in adam:
                share = c / len(win)
                missed[(date, k)] = max(missed[(date, k)], share)
                f, nm = fp.get(k, (None, k))
                info[(date, k)] = (nm, b.get(k), vr.get(k), pr.get(k), f, own.get(k))
        per_contest.append((date, name[:38], len(win), tot and c_on / tot))
    W = sum(s["w"] for s in slots)
    nslate = len({c[1] for c in cs})
    print(f"\n=== {sport} {ctype.upper()}: {len(cs)} contests over {nslate} slates; winning lineups = "
          f"{'top 1%' if ctype == 'gpp' else 'every cashing entry'}; {W:,} player-slots ===")
    on = sum(s["w"] for s in slots if s["on"]) / W
    print(f"  on our board at all:                    {on:6.1%}")
    for K in KS:
        v = sum(s["w"] for s in slots if s["vr"] and s["vr"] <= K) / W
        p = sum(s["w"] for s in slots if s["pr"] and s["pr"] <= K) / W
        print(f"  in our top {K:>2} at his position: by value {v:6.1%}   by projection {p:6.1%}")
    vrs = [s["vr"] for s in slots for _ in range(s["w"]) if s["vr"]]
    if vrs:
        print(f"  median value rank of a winner's player at his position: {st.median(vrs):.0f}")
    ad = [s for s in slots if s["has_adam"]]
    if ad:
        aw = sum(s["w"] for s in ad)
        print(f"  in contests you entered, winners' player-slots you also had: "
              f"{sum(s['w'] for s in ad if s['adam']) / aw:6.1%} (of {aw:,})")
    print("  per contest: date, contest, winning lineups, share of their slots on our board")
    for d, nm, nw, c in per_contest:
        print(f"    {d}  {nm:38s} {nw:5d}  {c:6.1%}")
    if missed:
        print(f"\n  players in the most winning lineups that you did NOT have (top {top}):")
        print(f"    {'date':10s} {'player':24s} {'in wins':>7s} {'$':>6s} {'proj':>5s} {'act':>5s} "
              f"{'own':>5s} {'valRk':>5s} {'prjRk':>5s} pos")
        for (d, k), share in missed.most_common(top):
            nm, bb, v, p, f, o = info[(d, k)]
            proj, sal, pos = bb if bb else (None, None, "-")
            print(f"    {d:10s} {nm[:24]:24s} {share:7.0%} {sal or 0:6.0f} "
                  f"{(f'{proj:5.1f}' if proj is not None else '  off')} {f or 0:5.1f} {o or 0:4.0f}% "
                  f"{v or '-':>5} {p or '-':>5} {pos}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", action="append", help="NBA / NHL / NFL ... (default NBA and NHL)")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    conn = sqlite3.connect(DB)
    for sport in a.sport or ["NBA", "NHL"]:
        for ctype in ("gpp", "cash"):
            run(conn, sport.upper(), ctype, a.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
