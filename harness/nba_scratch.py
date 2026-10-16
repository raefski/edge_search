"""NBA late scratches and promoted starters, pushed to the phone before lock.

    python3 -m harness nba-scratch                       # check now, print, no push
    python3 -m harness nba-scratch --push                # ...and send (the timer does this)
    python3 -m harness nba-scratch --date 2026-04-10     # replay a past night (no DK, no push)

Adam, 2026-10-15: "a crucial piece of information are late scratches, which could
mean a cheap bench player promoted to starter, and the name of the game is
capitalizing on under priced players." DK sets salaries the night before; when a
regular is ruled out, his minutes go to teammates whose prices did not move.

Sources (both free, both polled from this desktop):
  * NBA.com's daily lineups (edge/nba_lineups.py) -- every run: the starting five
    ("Expected" = NBA.com's guess, "Confirmed" = the team's submission, due 30 min
    before tip) and the inactive list;
  * DraftKings' own player status (OUT / Q) on the night's Classic slate -- at most
    every DK_EVERY, since DK blocks heavy polling (DRAFTKINGS_ACCESS.md).

Each change is pushed once: a regular newly OUT (DK or the inactive list), or a
player newly in the starting five who has not been starting. The push names the
teammates who gain the most, from edge/nba_minutes.py (minutes when the rotation
changes) times each one's DK points per minute, against his usual role:

    🔴 Brunson OUT · NYK (7:00 pm)
    🟢 McBride $3,900 · 24→33 min · 17→24 pts · 6.2x

ntfy cannot colour text; the emoji, the tags shown before the title and the
priority do that job -- "urgent" (its own sound, stays on screen) when a player at
or under URGENT_MAX_SALARY reaches URGENT_VALUE points per $1k.

Every change the feeds show is appended to data/nba_lineup_log.csv (gitignored),
which is how we learn when starters and inactives actually post.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from harness import monitor
from harness.registry import ROOT
from harness.runner import STATE

sys.path.insert(0, str(ROOT))
from edge import nba_minutes as NM          # noqa: E402
from edge.nba_lineups import fetch, parse   # noqa: E402
from edge.names import norm                 # noqa: E402

ET = ZoneInfo("America/New_York")
STATE_FILE = STATE / "nba_scratch_state.json"
LOG = ROOT / "data" / "nba_lineup_log.csv"
LOG_COLS = ("seen_at", "game_id", "team", "player_id", "player", "starter", "confirmed",
            "inactive", "dk_status")
POSITIONS = ROOT / "data" / "nba_ground_truth" / "positions.json"
#: Poll from this long before the first tip until shortly after the last.
LEAD = timedelta(hours=4)
TAIL = timedelta(minutes=10)
#: DK draftables (statuses) at most this often; the lobby (slate list) hourly.
DK_EVERY = timedelta(minutes=6)
DK_LOBBY_EVERY = timedelta(hours=1)
#: A teammate is named when his projection gains at least this many DK points.
MIN_GAIN_PTS = 3.0
#: Teammates listed per push.
MAX_LISTED = 3
#: Urgent: a player this cheap reaching this many DK points per $1,000.
URGENT_MAX_SALARY = 5000
URGENT_VALUE = 6.0
#: DK -> NBA.com team codes where they differ.
DK_TEAM = {"PHO": "PHX", "NO": "NOP", "NY": "NYK", "SA": "SAS", "GS": "GSW", "UTAH": "UTA",
           "WSH": "WAS", "BRK": "BKN", "CHO": "CHA"}
CLASSIC = 70
#: The phone app's NBA page, opened on tap with the "Promoted / changed only" filter on.
APP_URL = "https://edgesearch-h2dkcwvywzteys8e7tjk6v.streamlit.app/NBA_DFS?promoted=1"
#: Preseason (NBA game ids 001...) has no inactive list and no DK contests: pushes are
#: labelled tests, low priority, a few per run.
PRESEASON_MAX_PUSH = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True))


def tip_time(status: str | None, day: date) -> datetime | None:
    """'7:00 pm ET' on `day` -> aware UTC datetime (None once a game has started)."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*([ap])m", status or "", re.I)
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    local = datetime(day.year, day.month, day.day, h, int(m.group(2)), tzinfo=ET)
    return local.astimezone(timezone.utc)


def dk_players(state: dict, now: datetime, day: date) -> dict:
    """{(norm name, NBA team): {"salary", "status"}} for the night's Classic slates,
    refreshed at most every DK_EVERY (lobby hourly). Cached in `state`."""
    from edge import dfs
    dk = state.setdefault("dk", {})
    if dk.get("day") != day.isoformat():
        dk.clear()
        dk["day"] = day.isoformat()
    last = dk.get("fetched")
    if last and now - datetime.fromisoformat(last) < DK_EVERY:
        return {tuple(k.split("|")): v for k, v in dk.get("players", {}).items()}
    lobby = dk.get("lobby_at")
    if not lobby or now - datetime.fromisoformat(lobby) >= DK_LOBBY_EVERY:
        groups = [g for g in dfs.draft_groups("NBA") if g.get("GameTypeId") == CLASSIC
                  and (g.get("StartDateEst") or "")[:10] == day.isoformat()]
        dk["groups"] = [g["DraftGroupId"] for g in sorted(groups, key=lambda g: -(g.get("GameCount") or 0))]
        dk["lobby_at"] = now.isoformat()
    players: dict = {}
    for gid in dk.get("groups", []):
        for info in dfs.fetch_draftables(gid).values():
            key = f"{norm(info['name'])}|{DK_TEAM.get(info['team'], info['team'])}"
            players.setdefault(key, {"salary": info["salary"], "status": info.get("dk_status")})
        break                     # the biggest slate covers its games; statuses are shared
    dk["players"], dk["fetched"] = players, now.isoformat()
    return {tuple(k.split("|")): v for k, v in players.items()}


def assess(games: list[dict], hist: dict, pos: dict, dk: dict, seen: dict,
           prime: bool = False, windows: dict | None = None) -> tuple[list, dict]:
    """(alerts, new `seen`) for tonight's games. `seen` = {player_id: {"out", "starter"}}
    from the previous run; an event fires on a change into OUT or into the starting five.
    `prime` = the day's first look: who is already out is old news (priced into DK's
    salaries) and does not fire; a promoted starter still does."""
    alerts, now_seen = [], {}
    for g in games:
        teams = {}
        for p in g["players"]:
            teams.setdefault(p["team"], []).append(p)
        for team, ps in teams.items():
            rows, events = [], []
            for p in ps:
                pid = str(p["id"])
                d = dk.get((norm(p["name"]), team), {})
                h = hist.get(pid, [])
                win = (windows or {}).get(pid)
                r = NM.role([(m, s) for m, s, _ in h], win)
                out = p["inactive"] or d.get("status") == "OUT"
                now_seen[pid] = {"out": out, "starter": p["starter"]}
                before = {"out": out, "starter": False} if prime else seen.get(pid, {})
                if out and not before.get("out") and r["base"] >= NM.REGULAR_MIN:
                    events.append(("out", p, d, r))
                if (p["starter"] and not before.get("starter") and not out
                        and r["games"] >= 5 and r["start_rate"] < 0.5):
                    events.append(("promoted", p, d, r))
                rows.append({"id": pid, "team": team, "position": pos.get(pid), "inactive": out, "window": win,
                             "starter": p["starter"] if any(q["starter"] for q in ps) else None,
                             "history": [(m, s) for m, s, _ in h], "name": p["name"],
                             "dk": d, "ppm": NM.points_per_minute(h), "base": r["base"]})
            if not events:
                continue
            proj = NM.project(rows)
            gains = []
            for row in rows:
                pj = proj.get(row["id"])
                if not pj or not row["ppm"]:
                    continue
                usual, tonight = row["base"] * row["ppm"], pj["minutes"] * row["ppm"]
                if tonight - usual < MIN_GAIN_PTS and not pj["promoted"]:
                    continue
                sal = row["dk"].get("salary")
                gains.append({"name": row["name"], "team": row["team"], "salary": sal, "min0": row["base"],
                              "min1": pj["minutes"], "pts0": usual, "pts1": tonight,
                              "value": tonight / sal * 1000 if sal else None,
                              "promoted": pj["promoted"]})
            # Cheap is the point: rank by points gained per $1k of salary (a missing
            # salary counts as an average $6k), a promoted starter always first.
            gains.sort(key=lambda x: (not x["promoted"],
                                      -(x["pts1"] - x["pts0"]) * 1000 / (x["salary"] or 6000)))
            if gains:                     # an absence nobody absorbs is not worth a push
                alerts.append(_alert(g, team, events, gains[:MAX_LISTED]))
    return alerts, now_seen


def _surname(name: str) -> str:
    """'Jabari Smith Jr.' -> 'Smith' (a suffix is not a name)."""
    parts = [w for w in (name or "").split() if w.rstrip(".").lower() not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return parts[-1] if parts else name


def _alert(game: dict, team: str, events: list, gains: list) -> dict:
    heads = []
    for kind, p, d, _ in events:
        last = _surname(p["name"])
        if kind == "out":
            why = "inactive list" if p["inactive"] else "DK: OUT"
            heads.append(f"🔴 {last} OUT ({why})")
        else:
            heads.append(f"🟢 {last} STARTS ({'confirmed' if p['confirmed'] else 'expected'})")
    lines = []
    for x in gains:
        sal = f"${x['salary']:,}" if x["salary"] else "no DK salary"
        val = f" · {x['value']:.1f}x" if x["value"] else ""
        mark = "🟢 " if x["promoted"] or (x["value"] or 0) >= URGENT_VALUE else "   "
        lines.append(f"{mark}{x['name']} [{x['team']}] {sal} · {x['min0']:.0f}→{x['min1']:.0f} min · "
                     f"{x['pts0']:.0f}→{x['pts1']:.0f} pts{val}")
    urgent = any(x["salary"] and x["salary"] <= URGENT_MAX_SALARY and (x["value"] or 0) >= URGENT_VALUE
                 for x in gains)
    opp = game["home"] if team == game["away"] else game["away"]
    title = f"{' · '.join(heads)} · {team} vs {opp}"
    key = "|".join(sorted(f"{k}:{p['id']}" for k, p, _, _ in events))
    kinds = {k for k, *_ in events}
    tags = ",".join((["rotating_light"] if urgent else []) + (["red_circle"] if "out" in kinds else [])
                    + (["green_circle"] if "promoted" in kinds else []) + ["basketball"])
    preseason = str(game["game_id"]).startswith("001")
    if preseason:
        title, urgent, tags = "PRESEASON TEST · " + title, False, tags.replace("rotating_light,", "")
    return {"kind": "nba_scratch", "priority": "low" if preseason else ("urgent" if urgent else "high"),
            "title": title, "body": "\n".join(lines) or "No teammate gains enough to name.",
            "key": key, "tags": tags, "game": game["game_id"], "team": team,
            "players": [str(p["id"]) for _, p, _, _ in events], "preseason": preseason,
            "click": APP_URL}


def _log_changes(games: list[dict], dk: dict, prev: dict, now: datetime) -> dict:
    """Append rows whose (starter, confirmed, inactive, dk status) changed; return the new map."""
    cur, rows = {}, []
    for g in games:
        for p in g["players"]:
            st = dk.get((norm(p["name"]), p["team"]), {}).get("status")
            sig = [p["starter"], p["confirmed"], p["inactive"], st]
            pid = str(p["id"])
            cur[pid] = sig
            if prev.get(pid) != sig:
                rows.append({"seen_at": now.isoformat(), "game_id": g["game_id"], "team": p["team"],
                             "player_id": pid, "player": p["name"], "starter": p["starter"],
                             "confirmed": p["confirmed"], "inactive": p["inactive"], "dk_status": st})
    if rows:
        new = not LOG.exists()
        with LOG.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=LOG_COLS)
            if new:
                w.writeheader()
            w.writerows(rows)
    return cur


def run(do_push: bool = False, day: str | None = None) -> list[dict]:
    now = _now()
    replay = day is not None
    d = date.fromisoformat(day) if replay else now.astimezone(ET).date()
    raw = fetch(d, use_cache=not (do_push and not replay))
    if do_push and not replay:
        from edge.nba_lineups import SNAPSHOT, write_snapshot
        if write_snapshot(d, raw, now.isoformat()):
            from scripts.odds_collect import push_snapshot
            push_snapshot(SNAPSHOT, "nba lineups")        # the cloud app reads it
    games = parse(raw)
    if not games:
        print(f"{d}: no NBA games")
        return []
    tips = [t for t in (tip_time(g["status"], d) for g in games) if t]
    if not replay:
        if not tips or not (min(tips) - LEAD <= now <= max(tips) + TAIL):
            print(f"{d}: outside the polling window ({len(games)} games)")
            return []
    state = {} if replay else _load_state()
    if state.get("day") != d.isoformat():
        state = {"day": d.isoformat(), "seen": {}, "sent": [], "log": {}}
    if replay:
        hist = NM.load_history(before=d.isoformat())
        pos = json.loads(POSITIONS.read_text()) if POSITIONS.exists() else {}
        windows: dict = {}
    else:
        # The same history the app projects from (data/nba_player_history.json), so
        # an alert and the page it opens cannot disagree about a player's role.
        from edge.dfs_run_nba import load_history as app_history
        snap = app_history()
        hist = {pid: [tuple(x) for x in p["hist"]] for pid, p in snap["players"].items()}
        pos = {pid: p.get("pos") for pid, p in snap["players"].items()}
        windows = {pid: NM.SEASON_START_WINDOW for pid, p in snap["players"].items()
                   if p.get("season_games", 0) < 5}
    dk = {}
    if not replay:
        try:
            dk = dk_players(state, now, d)
        except Exception as exc:                            # noqa: BLE001
            print(f"DK unavailable ({exc}); using NBA.com only")
        if do_push:                                         # the timer's runs are the record
            state["log"] = _log_changes(games, dk, state.get("log", {}), now)
    alerts, seen = assess(games, hist, pos, dk, state.get("seen", {}),
                          prime=not replay and not state.get("seen"), windows=windows)
    new = [a for a in alerts if a["key"] not in state["sent"]]
    if any(a["preseason"] for a in new):
        new = sorted(new, key=lambda a: a["preseason"])[:PRESEASON_MAX_PUSH]
    print(f"{d}: {len(games)} games · {sum(len(g['players']) for g in games)} players · "
          f"{len(alerts)} change(s), {len(new)} new")
    sent, failed = [], []
    for a in new:
        ok = do_push and not replay and monitor.push(a)
        print(f"{'📲 ' if ok else '   '}[{a['priority']}] {a['title']}\n      "
              + a["body"].replace("\n", "\n      "))
        (sent if ok else failed).append(a)
    # Only the pushing timer keeps state: a dry run (or a replay) must not use up an
    # alert, and a push that failed must fire again on the next run.
    if do_push and not replay:
        if sent:
            monitor.log(sent, path=monitor.ALERTS)
        state["sent"] += [a["key"] for a in sent]
        for a in failed:
            for pid in a["players"]:
                if pid in state.get("seen", {}):
                    seen[pid] = state["seen"][pid]
                else:
                    seen.pop(pid, None)
        state["seen"] = seen
        _save_state(state)
    return new
