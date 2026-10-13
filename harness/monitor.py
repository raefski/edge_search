"""Watch what changes before lock: starting goalies and game lines.

    python3 -m harness monitor            # check, log alerts, print them
    python3 -m harness monitor --push     # ...and send new ones to the phone (ntfy)

Two checks, each against the monitor's own memory of what it last saw
(data/harness/monitor_state.json), so an alert fires once per change:

  * NHL STARTING GOALIES (DailyFaceoff, live): a team's projected starter
    changing is the late news that zeroes a G slot or flips a stack -- the
    NHL page's warning box says "use late swap if one changes". Pushed at
    high priority with the late-swap command to run.
  * GAME LINES (the odds store the collect timers fill): the consensus
    (median across books) total, spread and de-vigged moneyline for every
    event starting in the next HOURS, against the value last alerted on.
    A move past the sport's threshold (MOVES) means the projections built on
    the old number are stale: rebuild the slate.

Pushes go through ntfy (free app, no account) to the topic in
~/.config/edge_search/ntfy_topic, falling back to the investing project's
~/.config/investing/ntfy_topic so the phone already subscribed gets them.
Every alert is also appended to data/harness/alerts.jsonl, which
`harness status` shows.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from harness.registry import ROOT
from harness.runner import STATE

ET = ZoneInfo("America/New_York")
STATE_FILE = STATE / "monitor_state.json"
ALERTS = STATE / "alerts.jsonl"
TOPICS = (Path.home() / ".config" / "edge_search" / "ntfy_topic",
          Path.home() / ".config" / "investing" / "ntfy_topic")

#: Consensus move that triggers an alert, per sport: (total, spread, ML prob).
MOVES = {"icehockey_nhl": (0.5, None, 0.06), "americanfootball_nfl": (1.5, 1.5, 0.06),
         "americanfootball_ncaaf": (3.0, 3.0, 0.08), "baseball_mlb": (0.5, None, 0.06)}
PROFILES = {"dfs_nhl": "icehockey_nhl", "dfs_nfl": "americanfootball_nfl",
            "dfs_ncaaf": "americanfootball_ncaaf", "dfs_mlb": "baseball_mlb"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_state(path: Path = STATE_FILE) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"goalies": {}, "lines": {}}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True))


# ── goalies ─────────────────────────────────────────────────────────────────
def goalie_changes(today: dict, seen: dict) -> list[dict]:
    """Alerts for {team: {goalie, status}} against what was last seen.
    A first sighting is a baseline, not an alert; a new NAME is high
    priority; a status change (Likely -> Confirmed) is logged only."""
    out = []
    for team, g in sorted(today.items()):
        name, status = (g or {}).get("goalie"), (g or {}).get("status")
        prev = seen.get(team)
        if prev is None or not name:
            continue
        if name != prev.get("goalie"):
            out.append({"kind": "goalie", "priority": "high", "team": team,
                        "title": f"{team} goalie change: {prev.get('goalie')} → {name}",
                        "body": f"{name} ({status or 'projected'}) replaces {prev.get('goalie')}. "
                                f"If you entered {prev.get('goalie')}: python3 -m harness late-swap nhl"})
        elif (status or "") != (prev.get("status") or ""):
            out.append({"kind": "goalie_status", "priority": "low", "team": team,
                        "title": f"{team} {name}: {prev.get('status') or 'projected'} → {status}",
                        "body": "status only"})
    return out


def fetch_goalies(date: str) -> dict:
    sys.path.insert(0, str(ROOT))
    from edge import nhl
    return nhl.starting_goalies(date, live_only=True)


# ── lines ───────────────────────────────────────────────────────────────────
def consensus(rows: list[tuple]) -> dict:
    """{(event_id, market): value} from (event_id, market, point, side, book,
    decimal) rows: median over books of the total (over line), the home
    spread, and the de-vigged home win probability."""
    by: dict = {}
    for ev, market, point, side, book, dec in rows:
        by.setdefault((ev, market, book), {})[side] = (point, dec)
    vals: dict = {}
    for (ev, market, _book), sides in by.items():
        v = None
        if market == "totals" and "over" in sides and sides["over"][0] is not None:
            v = sides["over"][0]
        elif market == "spreads" and "home" in sides and sides["home"][0] is not None:
            v = sides["home"][0]
        elif market == "h2h" and "home" in sides and "away" in sides:
            ph, pa = 1 / sides["home"][1], 1 / sides["away"][1]
            v = ph / (ph + pa)
        if v is not None:
            vals.setdefault((ev, market), []).append(v)
    return {k: statistics.median(v) for k, v in vals.items()}


def line_moves(db: Path, hours: float, seen: dict) -> list[dict]:
    """Alerts for consensus moves past MOVES on events starting within `hours`;
    `seen` ({"event|market": value}) is updated in place."""
    out = []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return out
    soon = (_now() + timedelta(hours=hours)).isoformat()
    now = _now().isoformat()
    for prof, sport in PROFILES.items():
        row = con.execute("SELECT MAX(id) FROM scan WHERE profile = ? AND ok = 1", (prof,)).fetchone()
        if not row or row[0] is None:
            continue
        events = {e: (h, a, t) for e, h, a, t in con.execute(
            "SELECT event_id, home_team, away_team, commence_time FROM event "
            "WHERE sport_key = ? AND commence_time BETWEEN ? AND ?", (sport, now, soon))}
        if not events:
            continue
        rows = con.execute(
            "SELECT event_id, market, point, side, book, decimal FROM quote WHERE scan_id = ? "
            "AND market IN ('totals', 'spreads', 'h2h') AND subject IS NULL", (row[0],)).fetchall()
        cons = consensus([r for r in rows if r[0] in events])
        t_tot, t_spr, t_ml = MOVES[sport]
        for (ev, market), v in cons.items():
            key = f"{ev}|{market}"
            prev = seen.get(key)
            seen.setdefault(key, v)
            if prev is None:
                continue
            thr = {"totals": t_tot, "spreads": t_spr, "h2h": t_ml}[market]
            if thr is None or abs(v - prev) < thr - 1e-9:
                continue
            seen[key] = v
            home, away, start = events[ev]
            what = {"totals": "total", "spreads": f"{home} spread", "h2h": f"{home} win prob"}[market]
            fmt = (lambda x: f"{100 * x:.0f}%") if market == "h2h" else (lambda x: f"{x:g}")
            out.append({"kind": "line", "priority": "default", "event": ev,
                        "title": f"{away} @ {home}: {what} {fmt(prev)} → {fmt(v)}",
                        "body": f"{sport.split('_')[-1].upper()} game at "
                                f"{datetime.fromisoformat(start.replace('Z', '+00:00')).astimezone(ET):%-I:%M %p} ET. "
                                "Projections built on the old line are stale -- Refresh the slate."})
    con.close()
    return out


# ── delivery ────────────────────────────────────────────────────────────────
def topic() -> str | None:
    for p in TOPICS:
        try:
            t = p.read_text().strip()
        except OSError:
            continue
        if t:
            return t
    return None


def push(alert: dict) -> bool:
    t = topic()
    if not t:
        return False
    pr = {"high": "high", "low": "low"}.get(alert.get("priority"), "default")
    req = urllib.request.Request(f"https://ntfy.sh/{t}", data=alert["body"].encode(),
                                 headers={"Title": ("edge: " + alert["title"]).encode("utf-8"),
                                          "Priority": pr, "Tags": "hockey" if alert["kind"].startswith(
                                              "goalie") else "chart_with_upwards_trend"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except OSError:
        return False


def log(alerts: list[dict], path: Path = ALERTS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for a in alerts:
            fh.write(json.dumps({"at": _now().isoformat(), **a}) + "\n")


def run(do_push: bool = False, hours: float = 12.0, goalies: bool = True,
        lines: bool = True) -> list[dict]:
    state = load_state()
    alerts: list[dict] = []
    if goalies:
        today = datetime.now(ET).date().isoformat()
        try:
            cur = fetch_goalies(today)
        except Exception as exc:                            # noqa: BLE001
            cur = {}
            print(f"goalies unavailable: {exc}")
        seen = state.setdefault("goalies", {}).get(today, {})
        alerts += goalie_changes(cur, seen)
        if cur:
            state["goalies"] = {today: {t: {"goalie": g.get("goalie"), "status": g.get("status")}
                                        for t, g in cur.items()}}
    if lines:
        alerts += line_moves(ROOT / "data" / "odds.db", hours, state.setdefault("lines", {}))
    save_state(state)
    if alerts:
        log(alerts)
    for a in alerts:
        sent = do_push and a.get("priority") != "low" and push(a)
        print(f"{'📲 ' if sent else '   '}{a['title']} — {a['body']}")
    if not alerts:
        print("no changes since the last check")
    return alerts
