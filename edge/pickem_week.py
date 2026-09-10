"""The pool's calendar and team vocabulary -- ONE implementation, shared.

Extracted from scripts/pickem_capture.py on 2026-09-09 because there were two
consumers and only one of them had the logic.

WHAT WENT WRONG WITHOUT IT
scripts/pickem_capture.py was fixed on 2026-09-08 to trim the odds board to a
single pool week and to alias the two team names the market and CBS spell
differently. pages/4_🎯_Pickem.py -- the artefact Adam actually reads -- was
not. So on Week 1 the page joined a TWO-WEEK board by home team alone: five
home teams appeared twice, BUF@HOU resolved to Week 2's CIN@HOU, and the page
printed a Texans pick where the Week 1 market says Bills. A side flip on the
showcase game of the week, from a bug that had already been found and fixed
eighteen hours earlier in a different file.

That is the exact failure the 2026-08-21 fallback-drift incident produced, and
the rule adopted afterwards was: only one implementation left to drift from.
Hence this module. Both the capture script and the Streamlit page import from
here; neither keeps a private copy.
"""
from __future__ import annotations

import datetime
import zoneinfo

#: The pool's clock. CBS posts on ET Tuesdays and deadlines are ET kickoffs.
EASTERN = zoneinfo.ZoneInfo("America/New_York")

#: Tuesday of week 1. A pool week runs Tuesday->Tuesday, which cleanly
#: brackets a Thu-Mon slate, and it is the same anchor the capture workflow
#: uses to derive the week number.
SEASON_START = datetime.date(2026, 9, 8)

#: The market side spells teams the way nflverse does (edge/nfl.py, via
#: pickem_live._parse_events); the CBS side spells them the way the pool does
#: (edge/pickem_cbs.NICK_TO_ABBR). Two teams differ, and an unaliased join
#: silently drops them -- the same failure shape as scripts/dfs_lineups_nfl.py's
#: DK_ALIAS, where the Rams going unmatched removed Puka Nacua from the pool.
#: In the capture script that logs a CBS line with no market reading beside it;
#: on the page it falls back to live_line = cbs_line, i.e. a fabricated ZERO
#: edge that is indistinguishable from a game the market agrees with.
MARKET_TO_CBS_ABBR = {"LA": "LAR", "WAS": "WSH"}


def _cbs_abbr(abbr: str) -> str:
    return MARKET_TO_CBS_ABBR.get(abbr, abbr)


#: Public alias. `_cbs_abbr` keeps its underscore for the call sites and tests
#: that already import it by that name.
cbs_abbr = _cbs_abbr


def current_week(today: datetime.date | None = None) -> int | None:
    """Today's pool week in ET, or None outside the regular season.

    Lets a timer say `--week auto` instead of hard-coding a number that would
    be wrong every week but one.
    """
    today = today or datetime.datetime.now(EASTERN).date()
    week = (today - SEASON_START).days // 7 + 1
    return week if 1 <= week <= 18 else None


def week_window(week: int) -> tuple[datetime.datetime, datetime.datetime]:
    """[start, end) of one pool week, as ET instants.

    Tuesday->Tuesday, which brackets a Thu-Mon slate with room either side.
    Computed in ET rather than UTC because a Monday night kickoff is already
    Tuesday in UTC, and a UTC-bucketed week drops it into the NEXT one -- the
    symptom being a week-1 slate of 15 games with DEN@KC missing.

    This exists to give edge.pickem_free.filter_to_slate an explicit window.
    The live feed carries more than one week -- on 2026-09-08 it held weeks 1
    AND 2, with five teams hosting in both -- and every consumer here keys by
    team, so an unfiltered board silently resolves to the wrong game.
    """
    start_date = SEASON_START + datetime.timedelta(days=7 * (week - 1))
    start = datetime.datetime.combine(start_date, datetime.time(0, 0),
                                      tzinfo=EASTERN)
    return start, start + datetime.timedelta(days=7)


def slate_for_week(board, week: int):
    """Trim a raw odds board to exactly one pool week, keyed the CBS way.

    Returns `{(away_abbr, home_abbr): LiveGame}` in CBS's vocabulary. The pair
    -- not the home team alone -- is the key on purpose: filter_to_slate has
    already guaranteed one game per home team, and keying on both makes a
    mis-join impossible to express rather than merely unlikely.

    Raises ValueError (from filter_to_slate) if two games for one home team
    survive the window. Callers must surface that, never swallow it: a team
    plays once a week, so a duplicate means the window is wrong and any answer
    given would be a guess.
    """
    from edge.pickem_free import filter_to_slate

    start, end = week_window(week)
    games = filter_to_slate(board, window_start=start, window_end=end)
    return {(_cbs_abbr(g.away_abbr), _cbs_abbr(g.home_abbr)): g for g in games}
