#!/usr/bin/env python3
"""How accurate are our DFS models? Points AND ownership, per sport, position,
slate and contest type, read from data/contests.db (scripts/contest_db.py).

    python3 scripts/model_accuracy.py              # every table
    python3 scripts/model_accuracy.py --sport NHL  # one sport
    python3 scripts/model_accuracy.py --per-slate  # also one row per slate / contest

POINTS are graded per SLATE (draft group): a player's DK score is the same in
every contest on it, so the actual comes from the slate's final boards and the
projection from the PRE-LOCK log matched to that draft group (contest_match --
never the nearest logged night). Only players someone drafted are on a board,
so a projected player nobody rostered has no actual and is left out; the share
of the log that is graded is printed.

OWNERSHIP is graded per CONTEST and never pooled across cash and GPP: small
cash fields concentrate far harder. The universe is the logged pool for that
slate; a logged player missing from the board was rostered by nobody (0%).
"relevant" MAE is over players the model or the field put at >= 5%, where the
decisions are.

DAN: where Dan's Projections sheet for the date is in the repo root (NFL 9/13,
9/20, 9/27; NCAAF 10/03), both models are graded on exactly the same players.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.contest_stats import summarize  # noqa: E402

DB = ROOT / "data" / "contests.db"


def pos_group(sport: str, pos: str | None) -> str:
    p = (pos or "?").split("/")[0].upper()
    if sport == "MLB":
        return "P" if p in ("P", "SP", "RP") else p
    if sport == "NHL":
        return "W" if p in ("LW", "RW", "W") else p
    return p


def proj_rows(conn, key) -> list[tuple]:
    sport, gid, date = key
    q = ("SELECT player_norm, name, pos, proj, own_pred, own_cash_pred, salary, variant, extra "
         "FROM projection WHERE source='ours' AND sport=? AND date=? AND " +
         ("draft_group=?" if gid is not None else "draft_group IS NULL"))
    return conn.execute(q, (sport, date, gid) if gid is not None else (sport, date)).fetchall()


def matched(conn, sport=None):
    q = ("SELECT m.contest_id, m.sport, m.type, m.draft_group, m.slate_date, m.proj_draft_group, "
         "m.proj_date, m.proj_variant, m.ok_points, m.ok_own, c.final, c.live_entries, c.name "
         "FROM contest_match m JOIN contest c USING(contest_id) WHERE m.ok_points=1")
    rows = conn.execute(q).fetchall()
    return [r for r in rows if sport is None or r[1] == sport]


# --------------------------------------------------------------------------
def points_tables(conn, sport_filter=None, per_slate=False) -> dict:
    """{sport: {"slates": [...], "by_pos": {pos: summary}, "all": summary}}"""
    slates = {}
    for (cid, sport, ctype, gid, date, pg, pdate, var, okp, oko, final, live, name) in matched(conn, sport_filter):
        if not final:
            continue
        key = (sport, gid)
        n_board = conn.execute("SELECT COUNT(*) FROM player_result WHERE contest_id=?", (cid,)).fetchone()[0]
        if key not in slates or n_board > slates[key]["n_board"]:
            slates[key] = {"cid": cid, "sport": sport, "gid": gid, "date": date, "n_board": n_board,
                           "proj_key": (sport, pg, pdate), "variant": var}
    out = defaultdict(lambda: {"slates": [], "rows": []})
    for (sport, gid), s in sorted(slates.items(), key=lambda kv: (kv[0][0], kv[1]["date"])):
        board = {k: f for k, f in conn.execute(
            "SELECT player_norm, fpts FROM player_result WHERE contest_id=?", (s["cid"],))}
        logged = proj_rows(conn, s["proj_key"])
        rows = []
        for k, name, pos, proj, own, ownc, sal, var, extra in logged:
            if proj is None or k not in board or board[k] is None:
                continue
            ex = json.loads(extra or "{}")
            fppg = ex.get("dk_fppg") or ex.get("dk_fppf")
            try:
                fppg = float(fppg) if fppg not in (None, "") else None
            except ValueError:
                fppg = None
            rows.append({"pos": pos_group(sport, pos), "proj": proj, "act": board[k], "fppg": fppg,
                         "variant": var, "date": s["date"], "gid": gid})
        summ = summarize([r["proj"] for r in rows], [r["act"] for r in rows])
        summ.update(date=s["date"], gid=gid, graded=len(rows), logged=len(logged), variant=s["variant"])
        out[sport]["slates"].append(summ)
        out[sport]["rows"] += rows
    res = {}
    for sport, d in out.items():
        rows = d["rows"]
        by_pos = {}
        for pos in sorted({r["pos"] for r in rows}):
            rs = [r for r in rows if r["pos"] == pos]
            by_pos[pos] = summarize([r["proj"] for r in rs], [r["act"] for r in rs])
            fp = [r for r in rs if r["fppg"] is not None]
            if len(fp) >= 10:
                by_pos[pos]["fppg_mae"] = summarize([r["fppg"] for r in fp], [r["act"] for r in fp])["mae"]
                by_pos[pos]["ours_mae_same"] = summarize([r["proj"] for r in fp], [r["act"] for r in fp])["mae"]
        variants = {}
        for v in sorted({r["variant"] for r in rows}):
            rs = [r for r in rows if r["variant"] == v]
            variants[v] = summarize([r["proj"] for r in rs], [r["act"] for r in rs])
        res[sport] = {"slates": d["slates"], "by_pos": by_pos, "variants": variants,
                      "all": summarize([r["proj"] for r in rows], [r["act"] for r in rows])}
    return res


def own_tables(conn, sport_filter=None) -> dict:
    """{(sport, type): {"contests": [...], "by_pos": {...}, "all": ..., "relevant": ...}}"""
    out = defaultdict(lambda: {"contests": [], "rows": []})
    for (cid, sport, ctype, gid, date, pg, pdate, var, okp, oko, final, live, name) in matched(conn, sport_filter):
        if not oko:
            continue
        board = {k: pct for k, pct in conn.execute(
            "SELECT player_norm, pct_drafted FROM player_result WHERE contest_id=?", (cid,))}
        rows = []
        for k, nm, pos, proj, own, ownc, sal, v, extra in proj_rows(conn, (sport, pg, pdate)):
            if own is None:
                continue
            pred = ownc if (ctype == "cash" and ownc is not None) else own
            rows.append({"pos": pos_group(sport, pos), "pred": pred, "pred_gpp": own,
                         "act": board.get(k, 0.0), "cid": cid})
        s = summarize([r["pred"] for r in rows], [r["act"] for r in rows])
        if not s["n"]:
            print(f"  (ownership: contest {cid} {sport} {ctype} {date}: the matched log has no own column)")
            continue
        rel = [r for r in rows if max(r["pred"], r["act"]) >= 5]
        s.update(cid=cid, date=date, gid=gid, field=live, variant=var,
                 rel_mae=summarize([r["pred"] for r in rel], [r["act"] for r in rel])["mae"],
                 sum_pred=sum(r["pred"] for r in rows), sum_act=sum(r["act"] for r in rows),
                 board_total=sum(board.values()),
                 top_act=max((r["act"] for r in rows), default=0),
                 top_pred=max((r["pred"] for r in rows), default=0))
        out[(sport, ctype)]["contests"].append(s)
        out[(sport, ctype)]["rows"] += rows
    res = {}
    for key, d in out.items():
        rows = d["rows"]
        by_pos = {p: summarize([r["pred"] for r in rows if r["pos"] == p],
                               [r["act"] for r in rows if r["pos"] == p])
                  for p in sorted({r["pos"] for r in rows})}
        rel = [r for r in rows if max(r["pred"], r["act"]) >= 5]
        res[key] = {"contests": d["contests"], "by_pos": by_pos,
                    "all": summarize([r["pred"] for r in rows], [r["act"] for r in rows]),
                    "relevant": summarize([r["pred"] for r in rel], [r["act"] for r in rel]),
                    "mean_contest_mae": float(np.mean([c["mae"] for c in d["contests"]]))}
    return res


def dan_benchmark(conn) -> list[dict]:
    """Same players, both models, against the contest boards on Dan's dates."""
    out = []
    for sport, date in conn.execute("SELECT DISTINCT sport, date FROM projection WHERE source='dan'"):
        cons = conn.execute(
            "SELECT m.contest_id, m.type, m.proj_draft_group, m.proj_date, m.ok_own, c.final "
            "FROM contest_match m JOIN contest c USING(contest_id) WHERE m.sport=? AND m.slate_date=? "
            "AND m.ok_points=1 AND m.method='draft_group'", (sport, date)).fetchall()
        if not cons:
            out.append({"sport": sport, "date": date, "note": "no contest export with a matched log"})
            continue
        dan = {k: (p, o, pos) for k, p, o, pos in conn.execute(
            "SELECT player_norm, proj, own_pred, pos FROM projection WHERE source='dan' AND sport=? AND date=?",
            (sport, date))}
        for cid, ctype, pg, pdate, oko, final in cons:
            ours = {r[0]: r for r in proj_rows(conn, (sport, pg, pdate))}
            board = {k: (pct, f) for k, pct, f in conn.execute(
                "SELECT player_norm, pct_drafted, fpts FROM player_result WHERE contest_id=?", (cid,))}
            both = [k for k in ours if k in dan and ours[k][3] is not None and dan[k][0] is not None]
            rec = {"sport": sport, "date": date, "cid": cid, "type": ctype, "gid": pg,
                   "n_ours": len(ours), "n_dan": len(dan), "n_both": len(both)}
            if ctype == "gpp" and final:
                pts = [k for k in both if k in board]
                rec["pts_ours"] = summarize([ours[k][3] for k in pts], [board[k][1] for k in pts])
                rec["pts_dan"] = summarize([dan[k][0] for k in pts], [board[k][1] for k in pts])
                rec["pts_by_pos"] = {}
                for p in sorted({pos_group(sport, ours[k][2]) for k in pts}):
                    ks = [k for k in pts if pos_group(sport, ours[k][2]) == p]
                    rec["pts_by_pos"][p] = (
                        summarize([ours[k][3] for k in ks], [board[k][1] for k in ks]),
                        summarize([dan[k][0] for k in ks], [board[k][1] for k in ks]))
            ow = [k for k in both if ours[k][4] is not None and dan[k][1] is not None]
            act = [board.get(k, (0.0, None))[0] for k in ow]
            rec["own_ours"] = summarize([ours[k][4] for k in ow], act)
            rec["own_dan"] = summarize([dan[k][1] for k in ow], act)
            rel = [i for i, k in enumerate(ow) if max(ours[k][4], dan[k][1], act[i]) >= 5]
            rec["own_rel_ours"] = summarize([ours[ow[i]][4] for i in rel], [act[i] for i in rel])
            rec["own_rel_dan"] = summarize([dan[ow[i]][1] for i in rel], [act[i] for i in rel])
            out.append(rec)
    return out


def mlb_norm_bug(conn) -> dict:
    """How many MLB board joins the old edge.dfs.norm keying of the log drops
    (it keeps 'Jr.'/'II'; the board is keyed suffix-free). Fixed in 8336706a;
    counted here so the size of what it hid is on record."""
    from edge.dfs import norm as dfs_norm
    from edge.names import norm as bare
    lost = rows = 0
    own_lost = own_tot = 0.0
    names = set()
    for (cid, sport, ctype, gid, date, pg, pdate, *_rest) in matched(conn, "MLB"):
        board = {k: pct for k, pct in conn.execute(
            "SELECT player_norm, pct_drafted FROM player_result WHERE contest_id=?", (cid,))}
        for k, name, *_ in proj_rows(conn, ("MLB", pg, pdate)):
            if k in board:
                rows += 1
                own_tot += board[k]
                if dfs_norm(name) != bare(name):
                    lost += 1
                    own_lost += board[k]
                    names.add(name)
    return {"joined_rows": rows, "dropped_rows": lost, "own_share_dropped": own_lost / own_tot if own_tot else 0,
            "players": sorted(names)[:12], "n_players": len(names)}


# --------------------------------------------------------------------------
def _row(label, s, extra=""):
    if not s or not s.get("n"):
        return f"| {label} | 0 | - | - | - |{extra}"
    rho = "-" if s["rho"] is None else f"{s['rho']:+.2f}"
    return f"| {label} | {s['n']} | {s['bias']:+.2f} | {s['mae']:.2f} | {rho} |{extra}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport")
    ap.add_argument("--per-slate", action="store_true")
    ap.add_argument("--json", help="also write every number to this file")
    args = ap.parse_args()
    conn = sqlite3.connect(str(DB))

    pts = points_tables(conn, args.sport)
    print("## Points: our pre-lock projection vs actual DK points (per slate, final boards)\n")
    print("| sport | slates | graded / logged | n | bias | MAE | rho | r |")
    print("|---|---|---|---|---|---|---|---|")
    for sport, d in sorted(pts.items()):
        a = d["all"]
        gl = f"{sum(s['graded'] for s in d['slates'])} / {sum(s['logged'] for s in d['slates'])}"
        print(f"| {sport} | {len(d['slates'])} | {gl} | {a['n']} | {a['bias']:+.2f} | {a['mae']:.2f} | "
              f"{a['rho']:+.2f} | {a['r']:+.2f} |")
    print("\nBy position (DK FPPG baseline on the same players where the log carries it):\n")
    print("| sport | pos | n | bias | MAE | rho | FPPG MAE (ours, same players) |")
    print("|---|---|---|---|---|---|---|")
    for sport, d in sorted(pts.items()):
        for pos, s in d["by_pos"].items():
            fb = (f" {s['fppg_mae']:.2f} ({s['ours_mae_same']:.2f}) |" if s.get("fppg_mae") is not None
                  else " - |")
            print(_row(f"{sport} | {pos}", s, fb))
        if len(d["variants"]) > 1:
            for v, s in d["variants"].items():
                print(_row(f"{sport} | log={v}", s, " - |"))
    if args.per_slate:
        print("\nPer slate:\n")
        print("| sport | date | gid | variant | graded/logged | bias | MAE | rho |")
        print("|---|---|---|---|---|---|---|---|")
        for sport, d in sorted(pts.items()):
            for s in d["slates"]:
                rho = "-" if s["rho"] is None else f"{s['rho']:+.2f}"
                print(f"| {sport} | {s['date']} | {s['gid']} | {s['variant']} | {s['graded']}/{s['logged']} | "
                      f"{s['bias']:+.2f} | {s['mae']:.2f} | {rho} |")

    own = own_tables(conn, args.sport)
    print("\n## Ownership: our pre-lock prediction vs real %Drafted (per contest, cash and GPP apart)\n")
    print("| sport | type | contests | n players | bias | MAE | rho | MAE where >=5% (n) | mean contest MAE | "
          "field top owned vs model top (median) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for (sport, ctype), d in sorted(own.items()):
        a, r = d["all"], d["relevant"]
        tops = f"{np.median([c['top_act'] for c in d['contests']]):.0f}% vs " \
               f"{np.median([c['top_pred'] for c in d['contests']]):.0f}%"
        print(f"| {sport} | {ctype} | {len(d['contests'])} | {a['n']} | {a['bias']:+.2f} | {a['mae']:.2f} | "
              f"{a['rho']:+.2f} | {r['mae']:.2f} ({r['n']}) | {d['mean_contest_mae']:.2f} | {tops} |")
    print("\nOwnership by position:\n")
    print("| sport | type | pos | n | bias | MAE | rho |")
    print("|---|---|---|---|---|---|---|")
    for (sport, ctype), d in sorted(own.items()):
        for pos, s in d["by_pos"].items():
            print(_row(f"{sport} | {ctype} | {pos}", s))
    if args.per_slate:
        print("\nPer contest:\n")
        print("| sport | type | contest | date | field | n | bias | MAE | rel MAE | rho | sum pred / sum act |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for (sport, ctype), d in sorted(own.items()):
            for c in d["contests"]:
                rho = "-" if c["rho"] is None else f"{c['rho']:+.2f}"
                rel = "-" if c["rel_mae"] is None else f"{c['rel_mae']:.2f}"
                print(f"| {sport} | {ctype} | {c['cid']} | {c['date']} | {c['field']} | {c['n']} | "
                      f"{c['bias']:+.2f} | {c['mae']:.2f} | {rel} | {rho} | {c['sum_pred']:.0f} / {c['sum_act']:.0f} |")

    print("\n## Dan's Projections vs ours, same players\n")
    dan = dan_benchmark(conn)
    print("| sport | date | contest | type | n same players | points MAE ours / Dan | points rho ours / Dan | "
          "own MAE ours / Dan | own MAE >=5% ours / Dan (n) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in dan:
        if "note" in r:
            print(f"| {r['sport']} | {r['date']} | - | - | - | {r['note']} | | | |")
            continue
        p = (f"{r['pts_ours']['mae']:.2f} / {r['pts_dan']['mae']:.2f} (n={r['pts_ours']['n']})"
             if r.get("pts_ours") and r["pts_ours"]["n"] else "-")
        pr = (f"{r['pts_ours']['rho']:+.2f} / {r['pts_dan']['rho']:+.2f}"
              if r.get("pts_ours") and r["pts_ours"]["n"] else "-")
        o = f"{r['own_ours']['mae']:.2f} / {r['own_dan']['mae']:.2f}" if r["own_ours"]["n"] else "-"
        orl = (f"{r['own_rel_ours']['mae']:.2f} / {r['own_rel_dan']['mae']:.2f} ({r['own_rel_ours']['n']})"
               if r["own_rel_ours"]["n"] else "-")
        print(f"| {r['sport']} | {r['date']} | {r['cid']} | {r['type']} | {r['n_both']} | {p} | {pr} | {o} | {orl} |")
    print("\nDan vs ours, points by position (GPP boards, same players):\n")
    print("| sport | date | pos | n | MAE ours | MAE Dan | bias ours | bias Dan |")
    print("|---|---|---|---|---|---|---|---|")
    for r in dan:
        for pos, (o, d) in (r.get("pts_by_pos") or {}).items():
            if o["n"]:
                print(f"| {r['sport']} | {r['date']} | {pos} | {o['n']} | {o['mae']:.2f} | {d['mae']:.2f} | "
                      f"{o['bias']:+.2f} | {d['bias']:+.2f} |")

    if args.sport in (None, "MLB"):
        b = mlb_norm_bug(conn)
        print(f"\nMLB edge.dfs.norm keying (fixed in 8336706a): would drop {b['dropped_rows']} of "
              f"{b['joined_rows']} board joins ({b['n_players']} players, {b['own_share_dropped']:.1%} of "
              f"joined ownership), e.g. {', '.join(b['players'][:6])}")
    if args.json:
        Path(args.json).write_text(json.dumps({"points": pts, "own": {f"{k[0]}:{k[1]}": v for k, v in own.items()},
                                               "dan": dan}, default=str, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
