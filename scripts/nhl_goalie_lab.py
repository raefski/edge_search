#!/usr/bin/env python3
"""NHL starting goalies, held out: who the board had in the G slot (and how sure DailyFaceoff
was) at each lead before puck drop, against who started -- and what it cost our lineups.

    python3 scripts/nhl_goalie_lab.py rows [--refresh]      # build data/nhl_cache/goalie_lab/rows.json
    python3 scripts/nhl_goalie_lab.py report [--cutoff 2026-10-21]
    python3 scripts/nhl_goalie_lab.py gate OUT.jsonl        # one JSON line per held-out row
    python3 scripts/nhl_goalie_lab.py parity                # production's P(start) cell == the lab's

Why it exists (NHL critique cycle 13 (availability), 2026-11-12, queued by Adam as
"availability goalies"; transfer T01 in scripts/dfs_transfer.py). The board holds ONLY
DailyFaceoff's starting goalie per team (edge/dfs_run_nhl.build_board) and gives him the full
starter simulation whatever DFO's news strength says -- the row's `starter_status` is stored and
nothing reads it. A goalie who does not start scores 0 in the G slot. Nothing measured how often
that happens by status and lead time, nor what it cost the lineups we log and the ones Adam enters.

THE PRE-LOCK STATE (no leak). Every version of data/nhl_lines_snapshot.json ever committed --
on main 9/28-10/16 (the timer committed it there), on the `data` branch since (edge/data_branch,
every 30 min, every 15 min 16-19 h) -- read through `git log --all` + one `git cat-file --batch`.
Each version carries `generated_at` (the scrape time; the commit time when missing) and
goalies {date: {team: {goalie, status}}}. The state at time t = the newest version generated
before t that lists the team for that date. status None (DFO: no news) is "Projected".

TRUTH. NHL box scores (nhl.boxscore, cached in data/nhl_cache/box): the goalie flagged
`starter`. A board goalie's DK points: his own line if he started; his relief line if he came
on (saves / goals against, a win only with the decision); 0 if he did not play. Names are
matched as (team, first initial, norm(last name)) -- the box prints "J. Swayman".

LEADS. Minutes before THAT GAME's puck drop (late swap is allowed, so a goalie in a 10 PM game
can still be swapped at 9:55): 240 / 180 / 120 / 90 / 60 / 30 / 15. The afternoon phone build
is ~T-180 for a 7 PM game, Adam's rebuild "when goalies are confirmed" T-60 to T-90, dfs-autolog
~T-15 of the slate's FIRST game (so later games sit at longer leads in the same build).

THE SPLIT. TRAIN = dates before TEST_FROM, TEST = on/after; the night is the unit (a night's
goalie news is shared by its games). TEST_FROM 2026-10-21 is the middle of the 9/29-11/11
history, set before any test row was looked at. Anything fitted (P(start | status, lead))
is fitted on TRAIN only.

OUR LINEUPS. data/dfs_lineups_nhl_<date>.csv = the last pre-lock build's cash / gpp /
cash_floor lineups (the file is rewritten on every build); its build time is the last
`built_at` for that (date, gid) in data/dfs_proj_history_nhl.csv (10/29 on), else the slate
lock - 15 min (dfs-autolog) -- labelled `assumed`. Adam's entries: contests.db is_adam rows,
slot G, with DK's own FPTS (read-only).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import subprocess
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from edge import nhl                     # noqa: E402
from edge.names import norm              # noqa: E402

SNAP = "data/nhl_lines_snapshot.json"
OUT = ROOT / "data" / "nhl_cache" / "goalie_lab"
FIRST, TEST_FROM = "2026-09-29", "2026-10-21"
LEADS = (240, 180, 120, 90, 60, 30, 15)
#: the gate's two information states: the afternoon build and the late (pre-swap) build
GATE_LEADS = {"early": 180, "late": 60}
STATUSES = ("Confirmed", "Likely", "Projected")


def sig(name: str | None) -> str:
    """(first initial, last name) -- the box score prints 'J. Swayman', DFO 'Jeremy Swayman'."""
    p = (name or "").replace(".", " ").split()
    return f"{norm(p[0])[:1]} {norm(p[-1])}" if p else ""


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


# ──────────────────────────────────────────────────────────────────────────────
# DailyFaceoff's goalie state over time
# ──────────────────────────────────────────────────────────────────────────────
def snapshots(refresh: bool = False) -> list[tuple[datetime, dict]]:
    """[(generated_at UTC, goalies {date: {team: {goalie, status}}})], oldest first, every
    committed version of the snapshot on any ref. Cached by commit hash (git objects never
    change), so a re-run only reads new commits."""
    cache = OUT / "snapshots.json"
    have = {} if refresh or not cache.exists() else json.loads(cache.read_text())
    log = subprocess.run(["git", "log", "--all", "--format=%H %cI", "--", SNAP], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.split("\n")
    commits = dict(ln.split() for ln in log if ln.strip())
    need = [h for h in commits if h not in have]
    if need:
        p = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, capture_output=True,
                           input="".join(f"{h}:{SNAP}\n" for h in need).encode(), check=True)
        buf, pos = p.stdout, 0
        for h in need:
            nl = buf.index(b"\n", pos)
            head = buf[pos:nl].split()
            pos = nl + 1
            if len(head) < 3 or head[1] == b"missing":
                have[h] = None
                continue
            size = int(head[2])
            raw, pos = buf[pos:pos + size], pos + size + 1
            try:
                d = json.loads(raw)
            except ValueError:
                have[h] = None
                continue
            have[h] = {"at": d.get("generated_at") or commits[h], "goalies": d.get("goalies") or {}}
        OUT.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(have))
    out = []
    for h, v in have.items():
        if v and h in commits:
            out.append((_utc(v["at"]), v["goalies"]))
    out.sort(key=lambda x: x[0])
    return out


class GoalieState:
    """DFO's {goalie, status} for (date, team) as of any time, from the snapshot history."""

    def __init__(self, snaps):
        self.snaps = snaps
        self.times = [t for t, _ in snaps]

    def at(self, date: str, team: str, when: datetime) -> dict | None:
        for i in range(bisect_right(self.times, when) - 1, -1, -1):
            e = (self.snaps[i][1].get(date) or {}).get(team)
            if e:
                return {"goalie": e.get("goalie"), "status": e.get("status") or "Projected",
                        "seen": self.snaps[i][0]}
        return None

    def history(self, date: str, team: str, until: datetime) -> list[tuple[datetime, str, str]]:
        """[(time, goalie, status)] each time DFO's entry for (date, team) changed, to `until`."""
        out, last = [], None
        for t, g in self.snaps:
            if t >= until:
                break
            e = (g.get(date) or {}).get(team)
            if not e:
                continue
            cur = (e.get("goalie"), e.get("status") or "Projected")
            if cur != last:
                out.append((t, *cur))
                last = cur
        return out


# ──────────────────────────────────────────────────────────────────────────────
# rows: one per team-game
# ──────────────────────────────────────────────────────────────────────────────
def _goalie_line(g: dict) -> float:
    """DK points from a box-score goalie line (the shutout needs the whole game)."""
    ga = g["goals_against"]
    full = int((g.get("toi") or "0:00").split(":")[0]) >= 58
    return float(nhl.goalie_points(g["saves"], ga, g.get("decision") == "W", g.get("decision") == "O",
                                   ga == 0 and full and g.get("starter")))


def _schedule(date: str, cache: dict) -> list[dict]:
    if date not in cache:
        cache[date] = [g for g in nhl.schedule(date) if g.get("type") == 2]
    return cache[date]


def build_rows(refresh: bool = False, end: str | None = None) -> list[dict]:
    st = GoalieState(snapshots(refresh))
    sched_path = OUT / "schedule.json"
    sched = {} if refresh or not sched_path.exists() else json.loads(sched_path.read_text())
    stop = end or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    rows, day = [], datetime.strptime(FIRST, "%Y-%m-%d")
    while day.strftime("%Y-%m-%d") <= stop:
        date = day.strftime("%Y-%m-%d")
        day += timedelta(days=1)
        for g in _schedule(date, sched):
            try:
                box = nhl.boxscore(g["id"])
            except Exception as exc:                 # noqa: BLE001 -- not final / not fetched
                print(f"{date} {g['id']}: no box ({exc})", file=sys.stderr)
                continue
            if not box.get("goalies") or box.get("state") not in ("OFF", "FINAL"):
                continue
            t0 = _utc(g["start"])
            for team in (g["away"], g["home"]):
                mine = [x for x in box["goalies"] if x["team"] == team]
                started = [x for x in mine if x.get("starter")]
                if not started:
                    continue
                by_sig = {sig(x["name"]): x for x in mine}
                rec = {"date": date, "game": g["id"], "team": team,
                       "opp": g["home"] if team == g["away"] else g["away"],
                       "start": t0.isoformat(), "actual": started[0]["name"],
                       "actual_pts": _goalie_line(started[0]),
                       "pulled": int((started[0].get("toi") or "0:00").split(":")[0]) < 55,
                       "leads": {}}
                for lead in LEADS:
                    e = st.at(date, team, t0 - timedelta(minutes=lead))
                    if not e:
                        rec["leads"][str(lead)] = None
                        continue
                    me = by_sig.get(sig(e["goalie"]))
                    played = me is not None and (me.get("starter") or (me.get("toi") or "00:00") != "00:00")
                    rec["leads"][str(lead)] = {
                        "goalie": e["goalie"], "status": e["status"],
                        "age_min": round((t0 - timedelta(minutes=lead) - e["seen"]).total_seconds() / 60),
                        "started": sig(e["goalie"]) == sig(started[0]["name"]),
                        "pts": _goalie_line(me) if played else 0.0}
                hist = st.history(date, team, t0)
                rec["history"] = [(t.isoformat(), gname, s) for t, gname, s in hist]
                # when DFO first named the actual starter for good, and first Confirmed him
                right = [t for t, gname, _ in hist if sig(gname) == sig(started[0]["name"])]
                last_wrong = max((t for t, gname, _ in hist if sig(gname) != sig(started[0]["name"])),
                                 default=None)
                good = [t for t in right if last_wrong is None or t > last_wrong]
                rec["named_min"] = round((t0 - good[0]).total_seconds() / 60) if good else None
                conf = [t for t, gname, s in hist if s == "Confirmed" and sig(gname) == sig(started[0]["name"])]
                rec["confirmed_min"] = round((t0 - conf[0]).total_seconds() / 60) if conf else None
                rows.append(rec)
    sched_path.parent.mkdir(parents=True, exist_ok=True)
    sched_path.write_text(json.dumps(sched))
    (OUT / "rows.json").write_text(json.dumps(rows))
    return rows


def load_rows() -> list[dict]:
    p = OUT / "rows.json"
    return json.loads(p.read_text()) if p.exists() else build_rows()


# ──────────────────────────────────────────────────────────────────────────────
# P(start | status, lead) -- fitted on TRAIN only
# ──────────────────────────────────────────────────────────────────────────────
def lead_bucket(minutes: float) -> str:
    """Minutes to puck drop -> the bucket P(start) is fitted in."""
    return "<=30" if minutes <= 30 else ("31-90" if minutes <= 90 else ("91-180" if minutes <= 180 else ">180"))


def fit_p_start(rows: list[dict], prior: float = 2.0) -> dict:
    """{(status, bucket): P(DFO's goalie starts)} on the given rows, every lead pooled per
    bucket, shrunk toward the status's overall rate with `prior` pseudo-games (and the status
    rate toward 1 with the same prior, so an unseen cell is not a 100% claim)."""
    by_s, by_sb = Counter(), Counter()
    ok_s, ok_sb = Counter(), Counter()
    for r in rows:
        for lead, e in r["leads"].items():
            if not e:
                continue
            k = (e["status"], lead_bucket(int(lead)))
            by_s[e["status"]] += 1
            ok_s[e["status"]] += e["started"]
            by_sb[k] += 1
            ok_sb[k] += e["started"]
    out = {}
    for s in set(by_s) | set(STATUSES):
        ps = (ok_s[s] + prior) / (by_s[s] + prior)
        out[(s, None)] = ps
        for b in ("<=30", "31-90", "91-180", ">180"):
            out[(s, b)] = (ok_sb[(s, b)] + prior * ps) / (by_sb[(s, b)] + prior)
    return out


def p_of(model: dict, status: str, lead: int) -> float:
    return model.get((status, lead_bucket(lead)), model.get((status, None), 1.0))


# The critic's cells (cycle 14): DailyFaceoff's "Projected" (no news) default is a different
# claim depending on who it names. Its own rotation pick for the second night of a back-to-back
# (the goalie who did NOT start last night) is reliable; the goalie who started the team's last
# game, projected again on rest, is DFO assuming the #1 plays -- and the backup's starts are
# announced at warmups. Context comes from box scores before the date (no leak).
def context(rows: list[dict]) -> dict:
    """{(date, team): (started_last: bool | None per lead's goalie -> computed later, prev date,
    prev starter sig)} -- each team's previous regular-season game in the rows."""
    prev, out = {}, {}
    for r in sorted(rows, key=lambda r: r["start"]):
        out[(r["date"], r["team"])] = prev.get(r["team"])
        prev[r["team"]] = (r["date"], sig(r["actual"]))
    return out


def cell_of(status: str, goalie: str, last: tuple | None, date: str) -> str:
    """The P(start) cell for DFO's goalie; mirrors edge.dfs_run_nhl.goalie_cell."""
    if status in ("Confirmed", "Likely"):
        return status
    started_last = bool(last) and last[1] == sig(goalie)
    b2b = bool(last) and (datetime.strptime(date, "%Y-%m-%d")
                          - datetime.strptime(last[0], "%Y-%m-%d")).days == 1
    if b2b and not started_last:
        return "Projected/b2b_backup"
    if started_last:
        return "Projected/started_last"
    return "Projected/other"


CELLS = ("Confirmed", "Likely", "Projected/started_last", "Projected/b2b_backup", "Projected/other")


def cell_rows(rows: list[dict]) -> list[dict]:
    """One row per (team-game, lead) with DFO's state: cell, started, DFO goalie's points."""
    ctx = context(rows)
    out = []
    for r in rows:
        last = ctx.get((r["date"], r["team"]))
        for lead, e in r["leads"].items():
            if e:
                out.append({"date": r["date"], "team": r["team"], "lead": int(lead), "status": e["status"],
                            "cell": cell_of(e["status"], e["goalie"], last, r["date"]),
                            "y": bool(e["started"]), "pts": e["pts"]})
    return out


def fit_cells(crows: list[dict], prior: float = 2.0) -> dict:
    """{cell: P(start)} -- every lead pooled, each (team-game, lead) row weighted 1/len(LEADS)
    so n counts team-games, shrunk with `prior` team-games toward the status's pooled rate
    (Projected cells toward all Projected)."""
    w = 1.0 / len(LEADS)
    n, k, ns, ks = Counter(), Counter(), Counter(), Counter()
    for c in crows:
        n[c["cell"]] += w
        k[c["cell"]] += w * c["y"]
        ns[c["status"]] += w
        ks[c["status"]] += w * c["y"]
    out = {}
    for cell in CELLS:
        st = cell.split("/")[0]
        base = (ks[st] + prior) / (ns[st] + prior)            # the status rate, itself shrunk toward 1
        out[cell] = (k[cell] + prior * base) / (n[cell] + prior)
    out["_n"] = {c: round(n[c], 1) for c in CELLS}
    return out


def production_p(status: str, goalie: str, last: tuple | None, date: str) -> float:
    """What the checkout's production code believes: edge.dfs_run_nhl.goalie_start_prob when
    it exists (a gate on a checkout without it scores the shipped full-starter credit, 1.0)."""
    try:
        from edge import dfs_run_nhl
        fn = getattr(dfs_run_nhl, "goalie_start_prob", None)
    except Exception:                                    # noqa: BLE001
        fn = None
    if fn is None:
        return 1.0
    started_last = bool(last) and last[1] == sig(goalie)
    b2b = bool(last) and (datetime.strptime(date, "%Y-%m-%d")
                          - datetime.strptime(last[0], "%Y-%m-%d")).days == 1
    return float(fn(status, started_last, b2b))


# ──────────────────────────────────────────────────────────────────────────────
# our lineups + Adam's entries
# ──────────────────────────────────────────────────────────────────────────────
def lineup_goalies(rows: list[dict]) -> list[dict]:
    """Every G slot in a logged lineup: the build time, DFO's state for that goalie's team at
    it, whether he started, his DK points."""
    idx = {(r["date"], r["team"]): r for r in rows}
    built = defaultdict(str)
    hist = ROOT / "data" / "dfs_proj_history_nhl.csv"
    if hist.exists():
        for r in csv.DictReader(hist.open()):
            k = (r["date"], r["gid"])
            built[k] = max(built[k], r["built_at"])
    snaps = GoalieState(snapshots())
    out = []
    for f in sorted((ROOT / "data").glob("dfs_lineups_nhl_20??-??-??.csv")):
        d = f.name[-14:-4]
        slate_lock = {}
        for r in csv.DictReader(f.open()):
            if r["slot"] != "G":
                continue
            g = idx.get((d, r["team"]))
            if not g:
                continue
            r.setdefault("gid", "")
            if r["gid"] not in slate_lock:
                starts = [_utc(x["start"]) for x in rows if x["date"] == d]
                slate_lock[r["gid"]] = min(starts) if starts else _utc(g["start"])
            b = built.get((d, r["gid"]))
            when = _utc(b) if b else slate_lock[r["gid"]] - timedelta(minutes=15)
            e = snaps.at(d, r["team"], when)
            t0 = _utc(g["start"])
            lead = round((t0 - when).total_seconds() / 60)
            started = sig(r["player"]) == sig(g["actual"])
            out.append({"date": d, "gid": r["gid"], "mode": r["mode"], "player": r["player"],
                        "team": r["team"], "proj": float(r["proj"] or 0), "built": "logged" if b else "assumed",
                        "lead": lead, "status": (e or {}).get("status"),
                        "dfo_goalie": (e or {}).get("goalie"), "started": started,
                        "pts": g["actual_pts"] if started else 0.0,
                        "starter_pts": g["actual_pts"]})
    return out


def adam_goalies(rows: list[dict]) -> list[dict]:
    """Adam's entered goalies (contests.db, read-only), one per (draft group, goalie), with
    DK's FPTS and whether the box score had him starting."""
    db = ROOT / "data" / "contests.db"
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = """SELECT ct.slate_date, ct.draft_group, ct.type, COUNT(*), SUM(ct.entry_fee), ep.player_norm, pr.name,
                  pr.fpts
           FROM entry e JOIN contest ct ON ct.contest_id = e.contest_id
           JOIN entry_player ep ON ep.contest_id = e.contest_id AND ep.entry_id = e.entry_id
           LEFT JOIN player_result pr ON pr.contest_id = e.contest_id AND pr.player_norm = ep.player_norm
           WHERE ct.sport = 'NHL' AND e.is_adam = 1 AND ep.slot = 'G'
           GROUP BY ct.draft_group, ct.type, ep.player_norm"""
    starters = defaultdict(set)
    for r in rows:
        starters[r["date"]].add(norm(r["actual"].split(".")[-1].strip()))
    out = []
    for date, gid, typ, n, fees, pn, name, fpts in con.execute(q):
        last = norm((name or "").split()[-1]) if name else ""
        out.append({"date": date, "gid": gid, "type": typ, "entries": n, "fees": fees, "player": name or pn,
                    "fpts": fpts, "started": bool(last) and last in starters.get(date, set())})
    return out


# ──────────────────────────────────────────────────────────────────────────────
# report
# ──────────────────────────────────────────────────────────────────────────────
def _brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else float("nan")


def _logloss(pairs, eps=1e-3):
    return -sum(math.log(max(eps, p if y else 1 - p)) for p, y in pairs) / len(pairs) if pairs else float("nan")


def report(cutoff: str = TEST_FROM) -> str:
    rows = load_rows()
    train = [r for r in rows if r["date"] < cutoff]
    test = [r for r in rows if r["date"] >= cutoff]
    nights = lambda rs: len({r["date"] for r in rs})          # noqa: E731
    L = [f"# NHL starting goalies held out: TRAIN < {cutoff} <= TEST",
         f"team-games: train {len(train)} ({nights(train)} nights), test {len(test)} ({nights(test)} nights); "
         f"dates {rows[0]['date'] if rows else '-'} .. {rows[-1]['date'] if rows else '-'}", ""]

    L.append("## 1. DFO's goalie vs the box-score starter, by status and minutes to puck drop")
    L.append("| split | lead | Confirmed n / wrong | Likely n / wrong | Projected n / wrong | none |")
    L.append("|---|---|---|---|---|---|")
    for name, rs in (("train", train), ("test", test)):
        for lead in LEADS:
            c, w, none = Counter(), Counter(), 0
            for r in rs:
                e = r["leads"].get(str(lead))
                if not e:
                    none += 1
                    continue
                c[e["status"]] += 1
                w[e["status"]] += not e["started"]
            cells = " | ".join(f"{c[s]} / {w[s]}" + (f" ({w[s] / c[s]:.0%})" if c[s] else "") for s in STATUSES)
            other = {s: (c[s], w[s]) for s in c if s not in STATUSES}
            L.append(f"| {name} | T-{lead} | {cells} | {none}{' ' + str(other) if other else ''} |")
    L.append("")

    model = fit_p_start(train)
    L.append("## 2. P(start | status, minutes to puck drop), fitted on TRAIN (prior 2 games)")
    L.append("| status | all | <=30 | 31-90 | 91-180 | >180 |")
    L.append("|---|---|---|---|---|---|")
    for s in STATUSES:
        L.append(f"| {s} | {model[(s, None)]:.3f} | " + " | ".join(f"{model[(s, b)]:.3f}" for b in
                                                                  ("<=30", "31-90", "91-180", ">180")) + " |")
    L.append("")
    crow = cell_rows(rows)
    ctr = [c for c in crow if c["date"] < cutoff]
    cte = [c for c in crow if c["date"] >= cutoff]
    cells = fit_cells(ctr)
    L.append("## 2b. P(start | status, DFO's goalie vs the team's last game) -- the cells production uses")
    L.append("Fitted on TRAIN (leads pooled, n = team-games, 2 pseudo-games toward the status rate). "
             "TEST columns: team-games at T-180 / T-60, and how many of them started.")
    L.append("| cell | P(start) train fit | train n | train started | test T-180 n / started | test T-60 n / started |")
    L.append("|---|---|---|---|---|---|")
    w = 1.0 / len(LEADS)
    for cell in CELLS:
        tr_ = [c for c in ctr if c["cell"] == cell]
        t180 = [c for c in cte if c["cell"] == cell and c["lead"] == 180]
        t60 = [c for c in cte if c["cell"] == cell and c["lead"] == 60]
        L.append(f"| {cell} | {cells[cell]:.3f} | {cells['_n'][cell]} | "
                 f"{sum(c['y'] for c in tr_) * w:.1f} | {len(t180)} / {sum(c['y'] for c in t180)} | "
                 f"{len(t60)} / {sum(c['y'] for c in t60)} |")
    L.append("")
    smean = sum(r["actual_pts"] for r in train) / max(1, len(train))
    L.append("Held out (TEST), one row per team-game per lead. shipped = every DFO goalie a certain starter "
             "(p 1); status = section 2's status-only rate; cells = 2b. Brier and log loss (p clipped at 0.001); "
             f"pts = the board goalie's expected DK points (train starter mean {smean:.2f} x p, n {len(train)}) vs "
             "what he scored (0 if he did not play); dSE = cells minus shipped squared error per team-game, "
             "night-bootstrap 90%.")
    L.append("| lead | n | wrong | Brier ship / status / cells | logloss ship / status / cells | "
             "pts RMSE ship / status / cells | dSE cells [90%] | nights cells better / worse |")
    L.append("|---|---|---|---|---|---|---|---|")
    import numpy as np
    for lead in LEADS:
        xs = [c for c in cte if c["lead"] == lead]
        if not xs:
            continue
        P = {"ship": lambda c: 1.0, "status": lambda c: model.get((c["status"], None), 1.0),
             "cells": lambda c: cells.get(c["cell"], 1.0)}
        pr = {k: [(f(c), c["y"]) for c in xs] for k, f in P.items()}
        se = {k: np.array([(smean * f(c) - c["pts"]) ** 2 for c in xs]) for k, f in P.items()}
        d = se["cells"] - se["ship"]
        nights = np.array([c["date"] for c in xs])
        ids, inv = np.unique(nights, return_inverse=True)
        per = np.bincount(inv, weights=d)
        cnt = np.bincount(inv).astype(float)
        bi = np.random.default_rng(0).integers(0, len(ids), (2000, len(ids)))
        boot = per[bi].sum(1) / cnt[bi].sum(1)
        L.append(f"| T-{lead} | {len(xs)} | {sum(1 - c['y'] for c in xs)} | "
                 + " / ".join(f"{_brier(pr[k]):.4f}" for k in P) + " | "
                 + " / ".join(f"{_logloss(pr[k]):.3f}" for k in P) + " | "
                 + " / ".join(f"{math.sqrt(se[k].mean()):.3f}" for k in P) + " | "
                 f"{d.mean():+.2f} [{np.percentile(boot, 5):+.2f}, {np.percentile(boot, 95):+.2f}] | "
                 f"{int((per < -1e-9).sum())} / {int((per > 1e-9).sum())} of {len(ids)} |")
    L.append("")

    L.append("## 3. Lead time: when DFO had the actual starter for good / Confirmed him (minutes before puck drop)")
    for name, rs in (("train", train), ("test", test)):
        wrong_any = [r for r in rs if any(e and not e["started"] for e in r["leads"].values())]
        named = sorted(r["named_min"] for r in wrong_any if r["named_min"] is not None)
        conf = [r["confirmed_min"] for r in rs if r["confirmed_min"] is not None]
        q = lambda xs, f: xs[int(f * (len(xs) - 1))] if xs else None    # noqa: E731
        L.append(f"- {name}: {len(wrong_any)} team-games where DFO named a non-starter at some lead; the real "
                 f"starter named for good at median T-{q(named, .5)} (10th pct T-{q(named, .1)}, min T-{named[0] if named else None}); "
                 f"never named before puck drop: {sum(r['named_min'] is None for r in wrong_any)}. "
                 f"Confirmed at all: {len(conf)}/{len(rs)}, median T-{q(sorted(conf), .5)}.")
    L.append("")
    L.append("Wrong at T-60 or later (test):")
    for r in test:
        bad = [(int(k), e) for k, e in r["leads"].items() if e and not e["started"] and int(k) <= 60]
        if bad:
            k, e = max(bad, key=lambda x: x[0])
            L.append(f"- {r['date']} {r['team']}: DFO {e['goalie']} ({e['status']}) at T-{k}, started "
                     f"{r['actual']} ({r['actual_pts']:.1f} pts); named T-{r['named_min']}")
    L.append("")

    lg = lineup_goalies(rows)
    L.append("## 4. Our logged lineups' G slot (last pre-lock build per draft group)")
    L.append("| split | slots | status at build: Confirmed / Likely / Projected / none | did not start | "
             "their proj | starters' pts lost | builds timed from log |")
    L.append("|---|---|---|---|---|---|---|")
    for name, rs in (("train", [x for x in lg if x["date"] < cutoff]), ("test", [x for x in lg if x["date"] >= cutoff])):
        st = Counter(x["status"] for x in rs)
        miss = [x for x in rs if not x["started"]]
        L.append(f"| {name} | {len(rs)} | {st['Confirmed']} / {st['Likely']} / {st['Projected']} / {st[None]} | "
                 f"{len(miss)} | {sum(x['proj'] for x in miss):.1f} | {sum(x['starter_pts'] for x in miss):.1f} | "
                 f"{sum(x['built'] == 'logged' for x in rs)}/{len(rs)} |")
    for x in lg:
        if not x["started"]:
            L.append(f"- {x['date']} {x['mode']} {x['player']} ({x['team']}): {x['status']} at T-{x['lead']} "
                     f"({x['built']} build time), proj {x['proj']:.1f}")
    L.append("")
    ad = adam_goalies(rows)
    L.append("## 5. Adam's entered goalies (contests.db, one row per draft group x contest type x goalie)")
    zero = [x for x in ad if not x["started"]]
    L.append(f"{len(ad)} rows, {sum(x['entries'] for x in ad)} entries, ${sum(x['fees'] or 0 for x in ad):.2f} fees; "
             f"not the box-score starter: {len(zero)}")
    for x in zero:
        L.append(f"- {x['date']} {x['type']} {x['player']}: {x['entries']} entries, ${x['fees']:.2f}, "
                 f"DK FPTS {x['fpts']}")
    return "\n".join(L)


# ──────────────────────────────────────────────────────────────────────────────
# gate rows (shape: scripts/nascar_lab.py gate)
# ──────────────────────────────────────────────────────────────────────────────
def gate(path: str) -> int:
    """One row per TEST team-game per information state (GATE_LEADS): pred = the board goalie's
    expected DK points under PRODUCTION's P(start) (edge.dfs_run_nhl.goalie_start_prob; 1.0 when
    a checkout has none) x the train starter mean, actual = what he scored (0 if he did not
    play); cluster = the night. Squared loss on these rows is the availability part of the
    goalie error: the projection itself barely beats a constant (NHL_STATUS.md 6e)."""
    rows = load_rows()
    train = [r for r in rows if r["date"] < TEST_FROM]
    smean = sum(r["actual_pts"] for r in train) / max(1, len(train))
    ctx = context(rows)
    n = 0
    with open(path, "w") as fh:
        for r in rows:
            if r["date"] < TEST_FROM:
                continue
            for state, lead in GATE_LEADS.items():
                e = r["leads"].get(str(lead))
                if not e:
                    continue
                p = production_p(e["status"], e["goalie"], ctx.get((r["date"], r["team"])), r["date"])
                fh.write(json.dumps({"key": f"{r['date']}|{r['team']}|G", "cluster": r["date"],
                                     "split": "primary", "state": f"goalie-{state}",
                                     "pred": round(smean * p, 4), "actual": e["pts"]}) + "\n")
                n += 1
    print(f"{n} gate rows -> {path}", file=sys.stderr)
    return 0


def parity() -> str:
    """Production's cell (edge.nhl.last_starts + dfs_run_nhl.goalie_cell, live NHL schedule +
    cached box scores) == this lab's cell (the team's previous game in rows.json), for DFO's
    goalie at T-60 of every team-game."""
    from edge import dfs_run_nhl as R
    rows = load_rows()
    ctx = context(rows)
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)
    same, diff = 0, []
    for date, rs in sorted(by_date.items()):
        last = nhl.last_starts(date, {r["team"] for r in rs})
        for r in rs:
            e = r["leads"].get("60")
            if not e:
                continue
            prev = last.get(r["team"])
            sl = bool(prev) and R._initial_last(r["team"], prev["goalie"]) == R._initial_last(r["team"], e["goalie"])
            b2b = bool(prev) and (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(prev["date"], "%Y-%m-%d")).days == 1
            a, b = R.goalie_cell(e["status"], sl, b2b), cell_of(e["status"], e["goalie"], ctx.get((date, r["team"])), date)
            if a == b:
                same += 1
            else:
                diff.append(f"{date} {r['team']} {e['goalie']} ({e['status']}): production {a}, lab {b}")
    return "\n".join([f"cells equal on {same} of {same + len(diff)} team-games at T-60"] + diff)


def lineups(state: str = "lock", n: int = 20, cutoff: str = TEST_FROM) -> str:
    """The production sim + optimizers (scripts/nhl_lineup_lab.py: field-aware cash, cash_floor,
    GPP; n seeds per slate) on the skater lab's cached `state` boards, two ways: every board
    goalie a certain starter (as shipped before cycle 14) vs P(start) from GOALIE_START_P
    (context from the box scores before the date). Scored with DK's FPTS against every real
    contest field of the lineup's type; a goalie who did not start scores 0 (late swap is not
    modelled -- this is the build left as it was). Slates where every goalie is Confirmed /
    Likely build identical lineups in both arms (their difference is 0) and are not rebuilt."""
    import numpy as np
    sys.path.insert(0, str(ROOT / "scripts"))
    import nhl_lineup_lab as LL
    from edge import dfs_run_nhl as R
    LL.LAB = ROOT / "data" / "nhl_cache" / "lab" / state
    LL._BOARDS.clear()
    rows = load_rows()
    ctx = context(rows)

    def full(slate):
        for p in slate["pool"]:
            p.pop("p_start", None)
        return slate

    def pstart(slate):
        for p in slate["pool"]:
            if p.get("pos") == "G":
                last = ctx.get((slate["date"], p["team"]))
                p["p_start"] = R.goalie_start_prob(p.get("starter_status"), *(
                    (bool(last) and last[1] == sig(p["name"]),
                     bool(last) and (datetime.strptime(slate["date"], "%Y-%m-%d")
                                     - datetime.strptime(last[0], "%Y-%m-%d")).days == 1)))
            else:
                p.pop("p_start", None)
        return slate
    LL.variant("full", board=full)
    LL.variant("pstart", board=pstart)
    gids = LL.graded_gids()
    B = LL.boards()
    moved = [g for g in gids if any(p.get("pos") == "G" and p_of_row(p, B[g], ctx) < 1.0
                                    for p in B[g]["pool"])]
    # only the slates P(start) moves are built: elsewhere both arms are the same lineups (the
    # goalie draw has its own RNG), so their difference is exactly 0 and is not re-measured
    built = LL.build(["full", "pstart"], moved, n) if moved else {"full": {}, "pstart": {}}
    out_rows = []
    for v in ("full", "pstart"):
        for g in moved:
            for mode in LL.MODES:
                out_rows += LL.contest_rows(g, mode, built[v][g][mode], v)
    # G slots on goalies who did not start, per arm
    started = {(r["date"], sig(r["actual"])) for r in rows}
    L = [f"# Lineups replayed on the {state} boards, {n} seeds a slate: full starter credit vs P(start)",
         f"{len(gids)} graded slates, {len(moved)} with a goalie at p < 1 (the rest build identical lineups "
         f"in both arms and are left out of every row below); TEST = slates on/after {cutoff}.", ""]
    for mode in LL.MODES:
        L.append(f"## {mode}")
        L.append("| split | arm | contests | slates | G slots that did not start | mean pts | field pctile | "
                 "cashed | $ back per $1 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for split, keep in (("train", lambda d: d < cutoff), ("test", lambda d: d >= cutoff)):
            for v in ("full", "pstart"):
                rs = [r for r in out_rows if r["variant"] == v and r["mode"] == mode and keep(r["date"])]
                gs = sorted({r["gid"] for r in rs})
                dead = sum(1 for g in gs for lu in built[v][g][mode] if lu
                           for p in lu if p["pos"] == "G" and (B[g]["date"], sig(p["name"])) not in started)
                tot = sum(1 for g in gs for lu in built[v][g][mode] if lu)
                if rs:
                    L.append(f"| {split} | {v} | {len(rs)} | {len(gs)} | {dead} / {tot} | "
                             f"{np.mean([r['points'] for r in rs]):.2f} | {np.mean([r['pctile'] for r in rs]):.3f} | "
                             f"{np.mean([r['cashed'] for r in rs]):.1%} | {np.mean([r['ret'] for r in rs]):.3f} |")
            a = {(r["gid"], r["contest"]): r for r in out_rows if r["variant"] == "full" and r["mode"] == mode
                 and keep(r["date"])}
            b = {(r["gid"], r["contest"]): r for r in out_rows if r["variant"] == "pstart" and r["mode"] == mode
                 and keep(r["date"])}
            ks = sorted(set(a) & set(b))
            if ks:
                for met in ("points", "pctile", "ret"):
                    d = np.array([b[k][met] - a[k][met] for k in ks])
                    cl = np.array([k[0] for k in ks])
                    ids, inv = np.unique(cl, return_inverse=True)
                    per = np.bincount(inv, weights=d)
                    cnt = np.bincount(inv).astype(float)
                    bi = np.random.default_rng(0).integers(0, len(ids), (2000, len(ids)))
                    boot = per[bi].sum(1) / cnt[bi].sum(1)
                    L.append(f"  {split} pstart - full, {met}: {d.mean():+.4f} [90% by slate {np.percentile(boot, 5):+.4f}, "
                             f"{np.percentile(boot, 95):+.4f}]; slates better / worse "
                             f"{int((per > 1e-9).sum())} / {int((per < -1e-9).sum())} of {len(ids)}")
        L.append("")
    L.append("Slates where P(start) moved a goalie (train/test, date, gid: Projected goalies and p):")
    for g in moved:
        sl = B[g]
        ps = [f"{p['name']} {p.get('starter_status')} {p_of_row(p, sl, ctx):.2f}"
              + ("" if (sl["date"], sig(p["name"])) in started else " (did not start)")
              for p in sl["pool"] if p.get("pos") == "G" and p_of_row(p, sl, ctx) < 1.0]
        L.append(f"- {'test' if sl['date'] >= cutoff else 'train'} {sl['date']} {g}: " + ", ".join(ps))
    (OUT / f"lineups_{state}.json").write_text(json.dumps({"rows": out_rows, "built": {
        v: {str(g): built[v][g] for g in moved} for v in ("full", "pstart")}}))
    return "\n".join(L)


def p_of_row(p: dict, slate: dict, ctx: dict) -> float:
    from edge import dfs_run_nhl as R
    last = ctx.get((slate["date"], p["team"]))
    return R.goalie_start_prob(p.get("starter_status"), bool(last) and last[1] == sig(p["name"]),
                               bool(last) and (datetime.strptime(slate["date"], "%Y-%m-%d")
                                               - datetime.strptime(last[0], "%Y-%m-%d")).days == 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rows")
    r.add_argument("--refresh", action="store_true")
    r.add_argument("--end")
    rp = sub.add_parser("report")
    rp.add_argument("--cutoff", default=TEST_FROM)
    g = sub.add_parser("gate")
    g.add_argument("out")
    sub.add_parser("parity")
    lu = sub.add_parser("lineups")
    lu.add_argument("--state", default="lock", choices=("lock", "early"))
    lu.add_argument("--n", type=int, default=20)
    lu.add_argument("--cutoff", default=TEST_FROM)
    a = ap.parse_args()
    if a.cmd == "rows":
        rows = build_rows(a.refresh, a.end)
        print(f"{len(rows)} team-games -> {OUT / 'rows.json'}")
    elif a.cmd == "report":
        print(report(a.cutoff))
    elif a.cmd == "gate":
        return gate(a.out)
    elif a.cmd == "parity":
        print(parity())
    elif a.cmd == "lineups":
        print(lineups(a.state, a.n, a.cutoff))
    return 0


if __name__ == "__main__":
    sys.exit(main())
