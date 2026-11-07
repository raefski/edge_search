#!/usr/bin/env python3
"""One row per paid DK entry of Adam's, every sport, for the ROI dashboard
(claude.ai artifact "DFS ROI by Sport": its one attached file is this output).

Two sources, merged by contest:
  * DK's entry-history exports (draftkings-contest-entry-history*.csv in
    data/ or Windows Downloads, deduped by Entry_Key): DK's own winnings,
    cash + ticket, back to 2022. The authority wherever it covers a contest.
  * contests.db (is_adam entries) for contests the newest history export
    predates. Its payouts are DK's table where cached ("dk"), pool/places for
    flat cash tables ("cash_flat", exact for Double Ups), otherwise a stretched
    template ("scaled"/"template"/"name_flat"): those rows say "estimated".
    A contest exported before it finished has no outcome and is left out.

Free contests (fee $0) are left out: no stake, so no ROI.

Contest-picking fields, per entry:
  * pool -- the prize pool DK lists; overlay = pool - fee x field (> 0: the
    field paid in less than DK pays out).
  * cap -- the contest's size (max entries): DK's own figure from the overlay
    app's lobby logs (contest_fill_log.csv / contest_finals.csv, since 10/11)
    where it saw the contest ("lobby"), otherwise pool / (fee x POOL_SHARE)
    ("from pool"). fill = field / cap.
  * games -- games on the slate from contests.db's board for the contest's draft
    group (imported contests only; None elsewhere).
  * alerted -- the overlay monitor pushed an alert for this contest
    (data/harness/alerts.jsonl); None before alerts began (10/13).
Type: contests.db's DK-tagged type where the contest is there, else the name
(Double Up / 50/50 / head-to-head = cash; everything else, multipliers and
satellites included, = gpp).

Code updates (--updates-out): every commit on main touching a sport's own
model files under edge/ (theory, sim, run, opt, the sport's ingest; MLB = the
original dfs_run/opt/sim/project/swap/validate/field/roster/optimal), one row
per sport per commit, so the dashboard can mark when each sport's code changed.
Shared code (dfs.py, dfs_opt_climb, dfs_leverage*, dfs_slate) marks no sport.
n_sports > 1 flags a commit that touched several sports at once (refactors).

Usage: python3 scripts/dfs_roi_rows.py --out <path.json> [--updates-out <path.json>]
"""
import argparse
import csv
import glob
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HIST_GLOBS = [str(ROOT / "data/draftkings-contest-entry-history*.csv"),
              "/mnt/c/Users/ASR/Downloads/draftkings-contest-entry-history*.csv"]
SPORT = {"CFB": "NCAAF", "NAS": "NASCAR", "CBB": "NCAAB", "GOLF": "PGA"}
# prize pool / (fee x max entries), the median over 22,853 lobby contests logged
# 10/11-11/6 (contest_fill_log.csv): Double Ups .871, <= $1 .845, <= $5 .857,
# above .880; p10-p90 within about +-5%, so a capacity from the pool is good for
# size buckets, not for the exact cap
POOL_SHARE = [(1.0, 0.845), (5.0, 0.857), (float("inf"), 0.880)]
POOL_SHARE_CASH = 0.871
ALERTS_START = "2026-10-13"
MODEL_ERA = "2026-06-28"   # the DFS project's start (dfs_entry_history.PROJECT_START)


def dollars(s):
    try:
        return float(str(s).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def name_type(name):
    n = name.lower()
    return "cash" if any(k in n for k in ("double up", "double-up", "50/50", "head-to-head", "h2h")) else "gpp"


def build():
    conn = sqlite3.connect(ROOT / "data/contests.db")
    db_type = dict(conn.execute("SELECT contest_id, type FROM contest"))
    hist = {}
    for g in HIST_GLOBS:
        for f in glob.glob(g):
            with open(f, newline="", encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    hist[r["Entry_Key"]] = r
    rows, hist_cids = [], set()
    for r in hist.values():
        cid = int(r["Contest_Key"])
        hist_cids.add(cid)
        fee = dollars(r["Entry_Fee"])
        if fee <= 0:
            continue
        field = int(r["Contest_Entries"] or 0)
        place = int(r["Place"] or 0)
        rows.append(dict(
            entry=r["Entry_Key"], date=r["Contest_Date_EST"][:10],
            sport=SPORT.get(r["Sport"], r["Sport"]),
            type=db_type.get(cid) or name_type(r["Entry"]),
            contest_id=cid, contest=r["Entry"], fee=fee,
            won=round(dollars(r["Winnings_Non_Ticket"]) + dollars(r["Winnings_Ticket"]), 2),
            place=place, field=field, pool=dollars(r["Prize_Pool"]), source="DK history"))
    hist_end = max(r["date"] for r in rows)
    q = """SELECT c.contest_id, e.entry_id, c.slate_date, c.sport, c.type, c.name, c.entry_fee,
                  f.payout, e.rank, c.field, c.final, c.prize_pool,
                  (SELECT group_concat(DISTINCT source) FROM payout p WHERE p.contest_id = c.contest_id)
           FROM entry e JOIN contest c USING (contest_id) JOIN entry_feature f USING (contest_id, entry_id)
           WHERE e.is_adam = 1"""
    pending = 0
    for cid, eid, date, sport, ctype, name, fee, payout, rank, field, final, pool, src in conn.execute(q):
        if cid in hist_cids or not fee or fee <= 0:
            continue
        if not final:
            pending += 1
            continue
        exact = (src or "").split(":")[0] in ("dk", "cash_flat")
        rows.append(dict(
            entry=f"db{cid}-{eid}", date=date, sport=sport, type=ctype, contest_id=cid,
            contest=name, fee=fee, won=round(payout or 0.0, 2), place=rank or 0, field=field or 0,
            pool=pool or 0.0, source="contest table" if exact else "estimated"))
    cids = {r["contest_id"] for r in rows}
    cap = {}
    for f, col in (("contest_fill_log.csv", "max_entries"), ("contest_finals.csv", "cap")):
        path = ROOT / "data" / f
        if path.exists():
            with open(path, newline="") as fh:
                for x in csv.DictReader(fh):
                    if x.get(col) and int(x["contest_id"]) in cids:
                        cap[int(x["contest_id"])] = int(x[col])
    dg = dict(conn.execute("SELECT contest_id, draft_group FROM contest"))
    games = dict(conn.execute("SELECT draft_group, COUNT(DISTINCT game) FROM slate_player "
                              "WHERE game IS NOT NULL AND game != '' GROUP BY draft_group"))
    alerted = set()
    alerts = ROOT / "data/harness/alerts.jsonl"
    if alerts.exists():
        for line in alerts.read_text().splitlines():
            a = json.loads(line)
            if a.get("kind") == "overlay" and a.get("contest"):
                alerted.add(int(a["contest"]))
    for r in rows:
        cid, fee = r["contest_id"], r["fee"]
        r["overlay"] = round(r["pool"] - fee * r["field"], 2) if r["pool"] else None
        if cid in cap:
            r["cap"], r["cap_from"] = cap[cid], "lobby"
        elif r["pool"]:
            share = POOL_SHARE_CASH if r["type"] == "cash" else next(s for top, s in POOL_SHARE if fee <= top)
            r["cap"], r["cap_from"] = max(r["field"], round(r["pool"] / (fee * share))), "from pool"
        else:
            r["cap"], r["cap_from"] = None, None
        r["fill"] = round(r["field"] / r["cap"], 3) if r["cap"] else None
        r["games"] = games.get(dg.get(cid)) if r["sport"] not in ("NASCAR", "MMA") else None
        r["alerted"] = int(cid in alerted) if r["date"] >= ALERTS_START else None
    for r in rows:
        r["net"] = round(r["won"] - r["fee"], 2)
        r["cashed"] = int(r["won"] > 0)
        # share of the field this entry beat: 1 = won it, .5 = field median
        r["beat"] = round(1 - (r["place"] - 1) / (r["field"] - 1), 4) if r["field"] > 1 and r["place"] else None
        r["era"] = "model" if r["date"] >= MODEL_ERA else "before"
    rows.sort(key=lambda r: (r["date"], r["contest_id"], r["entry"]))
    print(f"{len(rows)} paid entries: DK history to {hist_end} ({len(hist)} rows read), "
          f"{sum(r['source'] != 'DK history' for r in rows)} from contests.db after it "
          f"({sum(r['source'] == 'estimated' for r in rows)} estimated payouts), "
          f"{pending} left out as unfinished", file=sys.stderr)
    return rows


SPORT_CODE = {
    "NBA": r"(dfs_\w*nba\w*|nba\w*)\.(py|npz)",
    "NCAAB": r"(dfs_\w*ncaab\w*|ncaab\w*)\.py",
    "NFL": r"(dfs_\w*nfl\w*|nfl\w*)\.py",
    "NCAAF": r"(dfs_\w*ncaaf\w*|ncaaf\w*)\.py",
    "NHL": r"(dfs_\w*nhl\w*|nhl\w*)\.py",
    "NASCAR": r"(dfs_\w*nascar\w*|nascar\w*)\.py",
    "MMA": r"(dfs_\w*mma\w*|mma\w*)\.py",
    "MLB": r"(dfs_run|dfs_opt|dfs_sim|dfs_project|dfs_swap|dfs_validate|dfs_field|dfs_roster|dfs_optimal)\.py",
}


def updates():
    log = subprocess.run(["git", "-C", str(ROOT), "log", "main", "--name-only", "--date=short",
                          "--format=@@%h|%ad|%s", "--", "edge/"],
                         capture_output=True, text=True, check=True).stdout
    commits, cur = [], None
    for line in log.splitlines():
        if line.startswith("@@"):
            h, d, subj = line[2:].split("|", 2)
            cur = dict(hash=h, date=d, subject=subj, sports=set())
            commits.append(cur)
        elif line.strip() and cur:
            name = line.strip()
            for sport, pat in SPORT_CODE.items():
                if re.fullmatch("edge/" + pat, name):
                    cur["sports"].add(sport)
    rows = []
    for c in commits:
        subj = c["subject"] if len(c["subject"]) <= 220 else c["subject"][:217].rstrip() + "..."
        for sport in sorted(c["sports"]):
            rows.append(dict(key=f"{sport}|{c['hash']}", sport=sport, date=c["date"], hash=c["hash"],
                             subject=subj, n_sports=len(c["sports"])))
    rows.sort(key=lambda r: (r["date"], r["key"]))
    print(f"{len(rows)} sport code updates from {len(commits)} commits", file=sys.stderr)
    return rows


def primary_pos(sport, pos, alt):
    """One position per player for the accuracy breakdown: DK's first listed
    position (PG/SG -> PG), NHL wings as W (DK's own slot), MLB SP/RP as P. MLB's
    main log writes a generic "H" for hitters, so those take the position the
    same player carries in another log or on DK's board (alt), else stay H."""
    p = (pos or "").split("/")[0].upper()
    if sport == "MLB" and p == "H" and alt:
        p = alt.split("/")[0].upper()
    if sport == "NHL" and p in ("LW", "RW"):
        return "W"
    if sport == "MLB" and p in ("SP", "RP"):
        return "P"
    return p or "?"


def proj_rows():
    """Projected vs actual DK points: one row per player per slate, our own
    pre-lock projection log against the DK points in the contest export of the
    same draft group (contest_match, ok_points=1: the grader's own match, so no
    "nearest slate"). MLB logs carry no draft group and match by date, as the
    grader does. NHL's "rebuild" variant is left out: it was rebuilt after the
    games, not logged before them. Actuals exist only for players someone in the
    exported contest drafted, so unrostered bench players are missing.

    own_gpp is the field's % drafted in the largest GPP we exported on that
    draft group (never a cash contest: small cash fields pile 80-95% onto the
    top play), own_pred our own pre-lock GPP ownership guess; both in percent,
    None where there was no GPP export or no guess."""
    conn = sqlite3.connect(ROOT / "data/contests.db")
    alt = {}
    for sport, pn, pos in conn.execute("SELECT sport, player_norm, pos FROM projection "
                                       "WHERE pos IS NOT NULL AND pos NOT IN ('H', '')"):
        alt[(sport, pn)] = pos
    for pn, pos in conn.execute("SELECT player_norm, position FROM slate_player sp JOIN contest c "
                                "ON c.draft_group = sp.draft_group WHERE c.sport = 'MLB'"):
        alt.setdefault(("MLB", pn), pos)
    matches = conn.execute("""SELECT m.sport, m.contest_id, m.proj_draft_group, m.proj_date, m.proj_variant,
                                     c.type, c.field
                              FROM contest_match m JOIN contest c ON c.contest_id = m.contest_id
                              WHERE m.ok_points = 1 AND m.proj_variant != 'rebuild'""").fetchall()
    out, own = {}, {}
    for sport, cid, pdg, pdate, variant, ctype, field in matches:
        if pdg:
            proj = conn.execute("""SELECT player_norm, name, pos, salary, proj, date, team, own_pred FROM projection
                                   WHERE sport = ? AND draft_group = ? AND variant = ? ORDER BY logged_at""",
                                (sport, pdg, variant)).fetchall()
        else:
            proj = conn.execute("""SELECT player_norm, name, pos, salary, proj, date, team, own_pred FROM projection
                                   WHERE sport = ? AND date = ? AND variant = ? AND draft_group IS NULL
                                   ORDER BY logged_at""", (sport, pdate, variant)).fetchall()
        proj = {p[0]: p for p in proj}   # the last log before lock wins
        slate = str(pdg or pdate)
        for pn, fpts, pct in conn.execute("SELECT player_norm, fpts, pct_drafted FROM player_result "
                                          "WHERE contest_id = ?", (cid,)):
            p = proj.get(pn)
            if p is None or fpts is None or p[4] is None:
                continue
            k = (sport, slate, pn)
            if ctype == "gpp" and pct is not None and (field or 0) > own.get(k, (-1, None))[0]:
                own[k] = (field or 0, pct)
            out[k] = dict(
                key=f"{sport}|{slate}|{pn}", sport=sport, date=p[5], slate=slate, name=p[1], team=p[6],
                pos=primary_pos(sport, p[2], alt.get((sport, pn))),
                salary=p[3], proj=round(p[4], 2), actual=round(fpts, 2), own_pred=p[7])
    for k, r in out.items():
        r["own_gpp"] = own[k][1] if k in own else None
    rows = sorted(out.values(), key=lambda r: (r["date"], r["key"]))
    print(f"{len(rows)} projected-vs-actual player rows from {len({(r['sport'], r['slate']) for r in rows})} slates",
          file=sys.stderr)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--updates-out")
    ap.add_argument("--proj-out")
    a = ap.parse_args()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(build(), separators=(",", ":")))
    if a.updates_out:
        Path(a.updates_out).write_text(json.dumps(updates(), separators=(",", ":")))
    if a.proj_out:
        Path(a.proj_out).write_text(json.dumps(proj_rows(), separators=(",", ":")))


if __name__ == "__main__":
    main()
