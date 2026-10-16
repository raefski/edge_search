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
difference, not drift -- NFL's week is its biggest Main slate; the NHL's and
the NBA's is the next Main to lock, because DK lists tomorrow's Main beside
tonight's (NHL, 2026-10-07); NASCAR's is the Cup race.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


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
