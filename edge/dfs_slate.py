"""A DK slate as DraftKings describes it: lobby rows, tip times, games. Once.

Every sport's builder needs the same three things before it projects anyone:
which Classic draft groups the lobby lists, when each starts, and which games
a draft group's players are in. Until 2026-10-16 each dfs_run_<sport>.py had
its own copy (six `_parse_time`s, two `slate_games`, seven `classic_groups`),
and NHL, NBA and college basketball imported the NFL module's PRIVATE
`_parse_time` to get theirs -- so a basketball fix could break football.

THE SLATE IS DEFINED BY DRAFTKINGS, never by the odds feed: a scan holds every
event a book has posted, two nights of them at once, and inferring the slate
from it is how the NFL builder once gave 21 of 28 teams the wrong opponent
(DFS_STATUS.md). slate_games() reads the games off DK's own draftables.

WHAT STAYS IN EACH SPORT: which slate is the default. That is a real
difference, not drift -- NFL, NHL and NBA take the next Main to lock, because
DK lists tomorrow's (or next week's) Main beside tonight's (NHL, 2026-10-07;
NFL, 2026-10-16), NFL skipping a 0-game Main; NASCAR's is the Cup race.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def parse_time(value) -> datetime | None:
    """DK's '2026-10-10T23:00:00.0000000Z' (seven fractional digits, which
    fromisoformat refuses) or any ISO time -> an aware UTC datetime, or None."""
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def slate_games(salaries: dict) -> dict:
    """{DK game id: {home, away, matchup, start}} from a draft group's
    draftables (edge/dfs.fetch_draftables), one entry per game. DK writes a
    matchup as "AWAY @ HOME"."""
    games: dict = {}
    for info in salaries.values():
        gid, matchup = info.get("game"), info.get("matchup")
        if not gid or not matchup or "@" in str(gid) or gid in games:
            continue
        parts = [p.strip().upper() for p in str(matchup).split("@")]
        if len(parts) != 2:
            continue
        away, home = parts
        games[gid] = {"home": home, "away": away, "matchup": matchup,
                      "start": parse_time(info.get("start"))}
    return games


# ---------------------------------------------------------------------------
# Which odds event is which slate game -- the guard on every prop join
# ---------------------------------------------------------------------------
#: How far a book's start time may sit from DraftKings' for the same game.
#: Same game = same scheduled start, give or take a book's rounding; a
#: back-to-back's second night is 24h away, which is what this must refuse.
EVENT_TOLERANCE = timedelta(hours=3)


def event_games(event_subjects: dict, resolve, kickoffs: dict | None = None,
                games: dict | None = None,
                tolerance: timedelta = EVENT_TOLERANCE) -> dict:
    """{event id: DK game id} for the odds events that ARE a game on this slate.

    A sportsbook prop names a player and nothing else, and a scan holds every
    event a book has posted -- other games, tomorrow's games, next week's. A
    join by name alone therefore takes ANY namesake's line, and also the
    player's own line for a DIFFERENT game:

      * NCAAF 2026-10-16: Missouri QB "Austin Simmons (MIZZ)", not on the
        slate, became a $3,000 Washington WR projected 16.3. Both model
        lineups played him; he scored 0, and the cash entry missed the line.
      * Every flat {name: markets} walk (NFL, NHL, NCAAB, MLB pitchers)
        merged a player's lines across events with .update(), so a team on a
        back-to-back could carry TOMORROW's line into tonight's projection.

    An event belongs to the game MOST of its own subjects resolve to, and only
    when at least half of them do: a slate game's priced players are nearly
    all on DK's board, an off-slate game matches only by coincidence (1 of
    ~10). When both start times are known they must also agree within
    `tolerance`, which is what refuses the same two teams on another night.

    `event_subjects` = {event id: every subject the book named in it};
    `resolve(subject)` = the DK salary entry (with "game") or None;
    `kickoffs` = {event id: commence time}; `games` = slate_games().
    """
    out: dict = {}
    for ev, subjects in event_subjects.items():
        subjects = set(subjects)
        votes: dict = {}
        for s in subjects:
            info = resolve(s)
            if info and info.get("game"):
                votes[info["game"]] = votes.get(info["game"], 0) + 1
        if not votes:
            continue
        gid, n = max(votes.items(), key=lambda kv: (kv[1], str(kv[0])))
        if n < 0.5 * len(subjects):
            continue
        start = (games or {}).get(gid, {}).get("start")
        kick = parse_time((kickoffs or {}).get(ev))
        if start and kick and abs(kick - start) > tolerance:
            continue
        out[ev] = gid
    return out


def subject_events(event_subjects: dict, resolve, kickoffs: dict | None = None,
                   games: dict | None = None,
                   tolerance: timedelta = EVENT_TOLERANCE) -> tuple[dict, list]:
    """({subject: the ONE event whose lines are his}, [names refused]).

    A subject is kept from the event that is his own DK game (event_games)
    and from no other; a name with no such event is refused and listed, so a
    builder can show it rather than lose it silently. Two events that are both
    his game (a doubleheader) keep the earlier, deterministically.
    """
    ev_game = event_games(event_subjects, resolve, kickoffs, games, tolerance)
    owner: dict = {}
    seen: dict = {}
    for ev in sorted(event_subjects, key=lambda e: (str(parse_time((kickoffs or {}).get(e)) or ""), str(e))):
        for s in event_subjects[ev]:
            info = resolve(s)
            if not info:
                continue
            seen[s] = info
            if s not in owner and ev_game.get(ev) is not None and ev_game[ev] == info.get("game"):
                owner[s] = ev
    refused = sorted({info.get("name") or s for s, info in seen.items() if s not in owner})
    return owner, refused


def player_markets_by_event(client, sport_key: str, markets: list[str],
                            book: str = "draftkings") -> tuple[dict, dict]:
    """({event id: {subject: {market: {side: price, point}}}}, {event id:
    commence time}) for one book -- the per-event shape subject_events needs.
    An event whose odds call fails is skipped (logged by the caller's client),
    as the flat walks it replaces did implicitly."""
    from edge import dfs
    by_event: dict = {}
    kickoffs: dict = {}
    for ev in client.get_events(sport_key):
        kickoffs[ev["id"]] = ev.get("commence_time")
        try:
            payload = client.get_event_odds(sport_key, ev["id"], markets, "us")
        except Exception:                                   # noqa: BLE001
            continue
        for bk in payload.get("bookmakers", []):
            if bk.get("key") != book:
                continue
            names = {o["description"] for m in bk.get("markets", [])
                     for o in m.get("outcomes", []) if o.get("description")}
            players = {}
            for name in names:
                players[name] = dfs.player_markets(bk, name) or {}
            if players:
                by_event[ev["id"]] = players
    return by_event, kickoffs


def own_game_markets(by_event: dict, kickoffs: dict, resolve, games: dict,
                     tolerance: timedelta = EVENT_TOLERANCE) -> tuple[dict, list]:
    """({subject: markets from HIS game's event only}, [names refused]).

    The guarded replacement for `{name: markets}` built by .update() across
    every event in a scan. Subjects that resolve to no DK player are kept as
    before (the caller counts them not_on_slate)."""
    owner, refused = subject_events({ev: set(p) for ev, p in by_event.items()},
                                    resolve, kickoffs, games, tolerance)
    out: dict = {}
    for ev, players in by_event.items():
        for name, pm in players.items():
            if not pm:
                continue
            if name in owner:
                if owner[name] == ev:
                    out[name] = pm
            elif resolve(name) is None:
                out.setdefault(name, pm)
    return out, refused


def group_row(g: dict, **extra) -> dict:
    """One lobby DraftGroup as every page and CLI shows it."""
    suffix = (g.get("ContestStartTimeSuffix") or "").strip()
    return {"gid": g.get("DraftGroupId"), "games": g.get("GameCount") or 0,
            "label": suffix.strip("()") or "Main", "start": g.get("StartDate"),
            "start_est": g.get("StartDateEst"),
            "featured": g.get("DraftGroupTag") == "Featured", **extra}


def classic_rows(groups: list[dict], game_types) -> list[dict]:
    """The lobby's groups of these GameTypeIds, earliest first, then biggest."""
    ids = {game_types} if isinstance(game_types, int) else set(game_types)
    out = [group_row(g) for g in groups if g.get("GameTypeId") in ids]
    out.sort(key=lambda r: (r["start"] or "", -r["games"]))
    return out


def next_main(rows: list[dict]) -> int:
    """Index of the NEXT Main slate to lock in start-sorted `rows` (not the
    biggest: tomorrow's Main is listed beside tonight's), else 0."""
    return next((i for i, r in enumerate(rows) if r["label"] == "Main"), 0)


def explicit(rows: list[dict], draft_group, **defaults) -> tuple[int, dict]:
    """(gid, meta) for a draft group named on purpose: its lobby row when the
    lobby lists it, else a stub -- a slate that has left the lobby (locked,
    or a replay) is still buildable from its draftables."""
    gid = int(draft_group)
    match = next((r for r in rows if r["gid"] == gid), None)
    return gid, (match or {"gid": gid, "label": "(explicit)", "games": 0, **defaults})
