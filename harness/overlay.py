"""Overlay alerts: guaranteed contests that will not fill, pushed before lock.

    python3 -m harness overlay                 # print what would be pushed
    python3 -m harness overlay --push          # send it (ntfy, same channel as the monitor)
    python3 -m harness overlay --min-roi 0.25  # only bigger edges
    python3 -m harness overlay --all-types     # also Showdown / Captain / Madden contests

A guaranteed contest pays its prize table whether or not it fills, so when
the field stays thin one more entry cashes far more often and returns more
than it costs (edge/dfs_entry_edge.py). The alert is about the entry you would
add, not the contest in general:

  * "at lock": cash chance and expected return for the field to EXPECT at lock --
    the usual late fill measured on the fill log (edge/dfs_entry_edge.py
    likely_at_lock), never less than a straight line through this contest's own
    snapshots. This is what decides whether to alert;
  * "if no one else enters": the same with n + 1 entries -- the best case, not the
    forecast. (Until 2026-10-15 the straight line WAS the forecast; it showed a
    CFB contest at 6/78 as +495% at lock, and it locked at 52/78, +28%.)

After lock, unfilled contests are looked up once on DK's contest endpoint and
their entries at lock appended to data/contest_finals.csv (at most
FINALS_PER_RUN, spaced FINALS_PACE_S apart), which fits the last minutes the
fill log cannot see.

Only contests that are not resizable are alerted (DK may shrink a resizable
one that does not fill, which cancels the overlay); pass --include-resizable
to see them anyway. Each contest alerts once, and again as a final call when
it is 20 minutes from lock. Every run appends a fill snapshot to
data/contest_fill_log.csv, which is what makes the projection better over time.

Runs on the desktop: payout tables come from api.draftkings.com (datacenter
IPs are refused, DRAFTKINGS_ACCESS.md). The lobby is one request per sport
that has a slate locking in the window, every 15 minutes -- see the timer.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from harness import monitor
from harness.registry import ROOT
from harness.runner import STATE

sys.path.insert(0, str(ROOT))
from edge.dfs_entry_edge import (entry_edge, fill_basis, fit_late_fill,  # noqa: E402
                                  likely_at_lock, projected_fill, weekday)
from scripts import dfs_overlay as O  # noqa: E402

STATE_FILE = STATE / "overlay_state.json"
#: Autolog's cache of each sport's upcoming Classic slates (refreshed hourly).
SLATES = ROOT / "data" / "cache" / "dfs_autolog_slates.json"
AUTOLOG_TO_LOBBY = {"nhl": "NHL", "nfl": "NFL", "ncaaf": "CFB", "nascar": "NAS", "mma": "MMA",
                    "mlb": "MLB"}
#: Prizes that are a seat in another contest, not cash: their face value overstates the return.
TICKET = re.compile(r"satellite|qualifier|ticket", re.I)
#: Final call inside this many minutes of lock.
FINAL_MIN = 20
#: At most this many pushes per run, biggest edge first.
MAX_PUSH = 6
#: Entries at lock, for fitting the minutes after the fill log's last snapshot.
FINALS = ROOT / "data" / "contest_finals.csv"
FINALS_COLS = ("contest_id", "sport", "name", "cap", "lock", "last_seen_min", "last_seen_entries",
               "final", "checked_at")
#: DK lookups per run for finals, and the gap between them (DRAFTKINGS_ACCESS.md:
#: >= 1 s between ad-hoc lookups; the alert job already prices up to 40 per run).
FINALS_PER_RUN = 8
FINALS_PACE_S = 1.5
#: Only contests this empty at their last snapshot carry information about the
#: late fill an overlay depends on, and only ones that locked inside this window.
FINALS_MAX_FILL = 0.5
FINALS_WINDOW = (timedelta(minutes=10), timedelta(hours=6))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts) -> datetime | None:
    """ISO text (with or without fractions / 'Z') -> aware datetime, or None."""
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        if "." in s and "+" in s.split(".", 1)[1]:
            head, tail = s.split(".", 1)
            s = head + "+" + tail.split("+", 1)[1]
        elif "." in s:
            s = s.split(".", 1)[0] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def due(st: dict, now: datetime, hours: float) -> bool:
    """Poll a sport now? Every run while one of its contests locks inside the
    window (plus 30 min of slack); otherwise at most once an hour. DK's lobby
    is public, but a 15-minute poll of every sport all day is needless traffic."""
    polled = _iso(st.get("polled"))
    if polled is None or now - polled >= timedelta(minutes=60):
        return True
    nl = _iso(st.get("next_lock"))
    return nl is not None and now - timedelta(minutes=5) <= nl <= now + timedelta(hours=hours, minutes=30)


def load_log() -> tuple[dict, dict]:
    """The fill log, read once: ({contest_id: [(datetime, entries), ...]} oldest
    first, {contest_id: {"cap", "points": {minutes_to_lock: entries}, "lock",
    "sport", "name"}}) -- the second is what the late-fill model is fitted on."""
    hist: dict = {}
    contests: dict = {}
    if not O.FILL_LOG.exists():
        return hist, contests
    with O.FILL_LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                t = datetime.fromisoformat(r["seen_at"])
                e = int(r["entries"])
            except (KeyError, ValueError, TypeError):
                continue
            cid = r["contest_id"]
            hist.setdefault(cid, {})[t] = e
            lock = _iso(r.get("lock"))
            if lock is None:
                continue
            mins = (lock - t).total_seconds() / 60.0
            if mins < 0:
                continue
            try:
                cap = int(r["max_entries"])
            except (KeyError, ValueError, TypeError):
                continue
            c = contests.setdefault(cid, {"cap": cap, "points": {}, "lock": lock,
                                          "sport": r.get("sport"), "name": r.get("name")})
            c["points"][mins] = e
    return {cid: sorted(pts.items()) for cid, pts in hist.items()}, contests


def load_history() -> dict:
    """{contest_id: [(datetime, entries), ...]} oldest first, one point per snapshot."""
    return load_log()[0]


def load_finals() -> dict:
    """{contest_id: entries at lock} from data/contest_finals.csv."""
    if not FINALS.exists():
        return {}
    out = {}
    with FINALS.open(newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                out[r["contest_id"]] = int(r["final"])
            except (KeyError, ValueError, TypeError):
                continue
    return out


def collect_finals(contests: dict, finals: dict, now: datetime, lookup=None) -> list[dict]:
    """Look up entries at lock for recently locked contests that were still under
    FINALS_MAX_FILL full at their last snapshot; append them to FINALS."""
    if lookup is None:
        from edge.dfs_contest import lookup
    due_now = []
    for cid, c in contests.items():
        if cid in finals or not c["points"] or c["cap"] < 10:
            continue
        if not (FINALS_WINDOW[0] <= now - c["lock"] <= FINALS_WINDOW[1]):
            continue
        m_last = min(c["points"])
        if c["points"][m_last] / c["cap"] >= FINALS_MAX_FILL:
            continue
        due_now.append((c["lock"], cid, c, m_last))
    rows = []
    for i, (_, cid, c, m_last) in enumerate(sorted(due_now, reverse=True)[:FINALS_PER_RUN]):
        if i:
            time.sleep(FINALS_PACE_S)
        try:
            info = lookup(cid)
        except Exception:                                   # noqa: BLE001
            break                                           # DK refused: try next run
        if info.get("field") is None:
            continue
        rows.append({"contest_id": cid, "sport": c["sport"], "name": c["name"], "cap": c["cap"],
                     "lock": c["lock"].isoformat(), "last_seen_min": round(m_last, 1),
                     "last_seen_entries": c["points"][m_last], "final": int(info["field"]),
                     "checked_at": now.isoformat()})
    if rows:
        new = not FINALS.exists()
        FINALS.parent.mkdir(parents=True, exist_ok=True)
        with FINALS.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FINALS_COLS)
            if new:
                w.writeheader()
            w.writerows(rows)
    return rows


def price(found: list[dict]) -> dict:
    """Payout tables for the best candidates (cached). {contest_id: {tiers, resizable}}."""
    cache = json.loads(O.PAYOUTS.read_text()) if O.PAYOUTS.exists() else {}
    got: dict = {}
    for r in sorted(found, key=lambda r: -r["bound"])[:O.MAX_DETAILS]:
        try:
            got[str(r["id"])] = O.payout_table(r["id"], cache)
        except Exception:                                   # noqa: BLE001
            break                                           # datacenter IP, or DK refused
    O.PAYOUTS.parent.mkdir(parents=True, exist_ok=True)
    O.PAYOUTS.write_text(json.dumps(cache))
    return got


def assess(found: list[dict], tables: dict, history: dict, now: datetime, hours: float,
           max_fee: float, min_roi: float, include_resizable: bool,
           model: dict | None = None) -> list[dict]:
    """One row per contest with its marginal entry value now and at lock.

    The at-lock field is the late-fill model's expectation (`model`, from
    fit_late_fill) or, if higher, the straight line through this contest's own
    snapshots; without a model it is the straight line alone."""
    out = []
    for r in found:
        if not r["fee_ok"] or r["fee"] > max_fee:
            continue
        cid = str(r["id"])
        mins = (r["lock"] - now).total_seconds() / 60.0
        pt = tables.get(cid)
        if mins <= 0 or mins > hours * 60 or not pt or not pt["tiers"]:
            continue
        proj = projected_fill(history.get(cid, []), r["m"], mins)
        n_floor = int(proj["at_lock"]) if proj["at_lock"] is not None else r["nt"]
        day = weekday(r["lock"])
        n_likely = (likely_at_lock(model, r["nt"], r["m"], mins, r["sport"], day)
                    if model else n_floor)
        basis = fill_basis(model, r["nt"], r["m"], r["sport"], day)
        n_lock = max(n_floor, n_likely)
        now_e = entry_edge(pt["tiers"], r["nt"], r["fee"])
        lock_e = entry_edge(pt["tiers"], n_lock, r["fee"])
        full = n_lock >= r["m"]
        ticket = bool(TICKET.search(r["name"] or ""))      # prize is a seat, not cash
        ok = (not full and not ticket and lock_e["roi"] >= min_roi
              and (include_resizable or pt["resizable"] is not True))
        out.append({"id": cid, "sport": r["sport"], "name": r["name"], "fee": r["fee"],
                    "entries": r["nt"], "cap": r["m"], "lock": r["lock"], "minutes": round(mins),
                    "resizable": pt["resizable"], "places": lock_e["places"], "now": now_e,
                    "ticket": ticket,
                    "at_lock": lock_e, "projected": proj, "n_lock": n_lock, "n_floor": n_floor,
                    "modelled": bool(model), "fill_basis": basis, "alert": ok})
    return out


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True))


def alerts_for(rows: list[dict], state: dict, now: datetime) -> list[dict]:
    """Pushes not yet sent: a contest's first qualifying look, then its final call."""
    out = []
    for r in sorted((r for r in rows if r["alert"]), key=lambda r: -r["at_lock"]["roi"]):
        cid = r["id"]
        prev = state.get(cid)
        final = r["minutes"] <= FINAL_MIN
        if prev and (prev.get("final") or not final):
            continue
        n_now, e_now = r["entries"], r["now"]
        e_lock, p = r["at_lock"], r["projected"]
        rz = {True: "yes", False: "no", None: "not priced"}[r["resizable"]]
        head = ("FINAL CALL — " if final else "") + f"{r['sport']} · {r['name'][:46]}"
        if r.get("modelled"):
            expect = (f"Expect ~{r['n_lock']}/{r['cap']} at lock (how {r['fill_basis']} contests "
                      f"this empty fill late): cash {e_lock['cash']:.0%}, returns ${e_lock['return']:.2f} per $1 "
                      f"({e_lock['roi']:+.0%}), {e_lock['places']} paid places. "
                      f"Best case, if no one else enters: returns ${e_now['return']:.2f} "
                      f"({e_now['roi']:+.0%}).")
        else:
            expect = ((f"Projected {r['n_lock']} at lock. " if p["rate_per_min"] > 0 else "")
                      + f"If you enter now: cash {e_now['cash']:.0%}, returns ${e_now['return']:.2f} "
                      f"per $1 ({e_now['roi']:+.0%}). At lock: cash {e_lock['cash']:.0%}, returns "
                      f"${e_lock['return']:.2f} per $1 ({e_lock['roi']:+.0%}), "
                      f"{e_lock['places']} paid places.")
        body = (f"{r['minutes']} min to lock, {n_now}/{r['cap']} entered. {expect} "
                f"${r['fee']:g} entry. Resizable: {rz}. "
                f"https://www.draftkings.com/draft/contest/{cid}")
        out.append({"kind": "overlay", "priority": "high" if final or e_lock["roi"] >= 0.25 else "default",
                    "title": head, "body": body, "contest": cid, "final": final, "at": now.isoformat()})
    return out[:MAX_PUSH]


def run(do_push: bool = False, hours: float = 2.5, min_roi: float = 0.10, max_fee: float = 25.0,
        include_resizable: bool = False, all_types: bool = False) -> list[dict]:
    now = _now()
    state = _load_state()
    sports_state = state.setdefault("sports", {})
    found, polled = [], []
    for code in O.SPORTS:
        if not due(sports_state.get(code, {}), now, hours):
            continue
        try:
            cs = O.contests(code)
        except Exception as exc:                            # noqa: BLE001
            print(f"{code}: lobby unavailable ({exc})")
            continue
        polled.append(code)
        locks = [t for t in (O.lock_time(c) for c in cs) if t and t > now]
        sports_state[code] = {"polled": now.isoformat(),
                              "next_lock": min(locks).isoformat() if locks else None}
        found += O.rows_from(cs, code, now, max_fee, hours, classic_only=not all_types)
    O.log(found, now)                                       # fill snapshot for the projection
    tables = price(found)
    history, logged = load_log()
    finals = load_finals()
    try:
        got = collect_finals(logged, finals, now)
    except Exception as exc:                                # noqa: BLE001
        got = []
        print(f"finals: lookup unavailable ({exc})")
    finals.update({g["contest_id"]: g["final"] for g in got})
    model = fit_late_fill(logged, finals) if logged else None
    if model:
        levels = sorted({f"{k[0]} {k[1]}" if k[1] else k[0] for (k, _), n in model["cells"].items()
                         if k[0] and k[2] is not None and n >= 10})
        print(f"late-fill model: {model['n']} contests ({', '.join(levels)}), "
              f"last-minutes share {model['tail']:.2f} "
              f"({'fitted on ' + str(model['tail_n']) + ' finals' if model['tail_n'] >= 5 else 'default, n=1'})"
              + (f" · {len(got)} new finals" if got else ""))
    rows = assess(found, tables, history, now, hours, max_fee, min_roi, include_resizable, model)
    new = alerts_for(rows, state, now)
    print(f"polled {', '.join(polled) or 'nothing (no sport due)'} · {len(found)} guaranteed "
          f"contests in window · {sum(1 for r in rows if r['alert'])} qualify · {len(new)} to push")
    for r in sorted(rows, key=lambda r: -r["at_lock"]["roi"])[:12]:
        mark = "●" if r["alert"] else (" " if not r["ticket"] else "t")
        print(f"  {mark} {r['sport']:5} {r['minutes']:4}m {r['fee']:6.2f} "
              f"{r['entries']:>5}/{r['cap']:<6} expect {r['n_lock']:>6}  "
              f"enter-now cash {r['now']['cash']:4.0%} ${r['now']['return']:.2f}  "
              f"at-lock cash {r['at_lock']['cash']:4.0%} ${r['at_lock']['return']:.2f} "
              f"({r['at_lock']['roi']:+.0%})  rz={r['resizable']}  {r['name'][:40]}")
    sent_now = []
    for a in new:
        sent = do_push and monitor.push(a)
        print(f"{'📲 ' if sent else '   '}{a['title']}\n      {a['body']}")
        if sent:
            sent_now.append(a)
    # Only what reached the phone counts as alerted: a dry run, or a failed
    # push, must leave the contest eligible for the next run.
    if sent_now:
        monitor.log(sent_now, path=monitor.ALERTS)
    for a in sent_now:
        state[a["contest"]] = {"final": a["final"], "at": a["at"]}
    cutoff = (now - timedelta(days=2)).isoformat()
    state = {k: v for k, v in state.items() if k == "sports" or v.get("at", "") >= cutoff}
    _save_state(state)
    return new
