"""College basketball late scratches read off the prop board (NCAAB_CRITIQUE_LOG.md cycle 11).

College basketball has no injury report. When a player is ruled out late, the books take
his props down while his game-mates stay quoted -- the one public signal that reaches us
before tip. Before this module the board read a pulled player as "no props" and fell
back to a full blend / salary projection: Boogie Fland (UF, 11/19) lost his props at 19:17
UTC, the autolog built at 19:18, projected him 22.6 on the blend path, put him in both
lineups, and he played 0 minutes.

One function, `isolated_pull`, decides; the board (edge/dfs_run_ncaab.build_board) and the
lab (scripts/ncaab_scratch_lab.py, scripts/ncaab_lab.py) both call it, so production and
the replay cannot drift apart.

A pull is ISOLATED when, in the scan where the player vanished, no other player of the
game vanished and at least PULL_MIN_OTHERS stayed quoted. Without that test the flag fired
on books closing a whole game's props 7-12 minutes before tip (24 flags, 4 DNPs, 11/13-11/26;
isolated: 13 flags, the same 4 DNPs -- critic, cycle 11).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Game-mates still quoted for a missing player to count as pulled. Books thin a board to
#: 1-2 names as they close it at tip; 3 keeps that from reading as a scratch.
PULL_MIN_OTHERS = 3

#: Projection multiplier for a player whose props were pulled in isolation before the
#: build. Fitted 2026-11-27 (cycle 11) on the TRAIN slates only (11/13-11/18, 6 isolated
#: pulls, 2 of them DNPs): realised DK points / projection = 0.52. Held out (11/19-11/26,
#: 7 flags, 2 DNPs): 0.435, so the train value is, if anything, generous. Unflagged
#: players projected 15+ sat ~1% of the time. Refit (`scripts/ncaab_scratch_lab.py
#: report`) above 20 flags; drop the rule if precision (DNP share) falls under 20%.
PULL_MULT = 0.52


def isolated_pull(timeline, subjects, until: datetime) -> datetime | None:
    """When the player was pulled in isolation, if he is still off at the last scan
    before `until`; else None.

    `timeline`: [(scan time, frozenset of player subjects quoted for his game)] in time
    order (any book, any player market). `subjects`: the spellings books use for him. A
    one-scan blip that comes back is not a pull; vanishing together with a game-mate is
    the books closing the game, not news about him."""
    subjects = set(subjects or ())
    if not subjects:
        return None
    out, prev = None, None
    for t, subs in timeline or ():
        if t >= until:
            break
        hit = any(s in subs for s in subjects)
        if hit:
            out = None
        elif out is None and prev is not None and any(s in prev for s in subjects):
            others_gone = {s for s in prev - subs if s not in subjects}
            if not others_gone and len(subs) >= PULL_MIN_OTHERS:
                out = t
        prev = subs
    return out


def store_timelines(store, profile: str, sport_key: str, now: datetime | None = None,
                    hours: float = 18.0) -> dict:
    """{event_id: [(scan time, frozenset player subjects)]} for one profile's ok scans in the
    last `hours` before `now`, from the odds store (hot or archived, read-only)."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).isoformat()
    rows = store.conn.execute(
        "SELECT id, started_at FROM scan WHERE profile=? AND ok=1 AND started_at>=? AND started_at<=? "
        "ORDER BY id", (profile, since, now.isoformat())).fetchall()
    when = {int(r[0]): _ts(r[1]) for r in rows}
    groups = store.scan_groups(list(when), sport_key) if when else {}
    out: dict = {}
    # By start time, not scan id: two collectors overlap and ids come out of time order
    # (11/19 17:26 got a lower id than 17:25), and isolated_pull reads the order.
    for sid in sorted(when, key=lambda i: (when[i], i)):
        per: dict = {}
        for gk in groups.get(sid, ()):
            parts = gk.split("|")
            if len(parts) >= 3 and parts[1].startswith("player_") and parts[2]:
                per.setdefault(parts[0], set()).add(parts[2])
        for ev, subs in per.items():
            out.setdefault(ev, []).append((when[sid], frozenset(subs)))
    return out


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def pulled_players(timelines: dict, event_pairs: dict, rosters, now: datetime) -> dict:
    """{salary key: pulled-at time} for slate players pulled in isolation before `now`.

    `event_pairs`: {event_id: (team, team)} -- the slate game each prop event was assigned
    to (player_props' own assignment). Every subject the event ever quoted is resolved
    only among that game's two teams (edge/ncaab_names.Rosters), never by name alone."""
    out: dict = {}
    for ev, pair in event_pairs.items():
        tl = timelines.get(ev)
        if not tl:
            continue
        names: dict = {}
        for _t, subs in tl:
            for s in subs:
                info, _how = rosters.resolve(s, pair)
                if info is not None:
                    names.setdefault(rosters.key(info), set()).add(s)
        for key, subs in names.items():
            t = isolated_pull(tl, subs, now)
            if t is not None:
                out[key] = t
    return out
