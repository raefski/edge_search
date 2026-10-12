"""When a pick locks -- ONE implementation, shared by the capture scheduler,
the grader, the transferability measurement and the page.

THE RULE IS NOT SETTLED, AND THIS MODULE IS BUILT AROUND THAT
Two sources disagree (2026-10-11):

  * Adam: "picks lock 2 hours before kickoff."
  * The pool's own Settings page, fetched that day with the stored session:
    "Picks Deadline: Before the start of the first game of the day."

The page data could not break the tie: the only observation available (every
game but Monday's locked at 18:50 on a Sunday) is predicted by both rules.

So:
  * the SCHEDULER captures before every deadline either rule could produce
    (`candidate_deadlines`) -- a reading not taken is gone forever, and an
    extra one costs ten seconds;
  * the GRADER and the PAGE use `RULE`, which defaults to "earliest" -- the
    earlier of the two. That never credits the model with a reading Adam could
    not have used, and never tells him a game is open after it has locked;
  * scripts/pickem_pool_picks.py records, just after each candidate deadline,
    which games' picks have become visible to other entries (CBS shows them
    only once a game locks). `infer_rule` scores both rules against those
    observations. Once it is decisive, set RULE by hand -- it is a decision
    about money, so nothing flips it automatically.
"""
from __future__ import annotations

import datetime
from typing import Hashable, Iterable, Mapping

from edge.pickem_week import EASTERN

LOCK_LEAD = datetime.timedelta(hours=2)

KICKOFF_MINUS_2H = "kickoff_minus_2h"
FIRST_GAME_OF_DAY = "first_game_of_day"
EARLIEST = "earliest"
RULES = (KICKOFF_MINUS_2H, FIRST_GAME_OF_DAY, EARLIEST)

#: See the module docstring before changing this.
RULE = EARLIEST


def parse_utc(value) -> datetime.datetime | None:
    """An aware datetime from an ISO string (Z or offset), else None."""
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
    if not value:
        return None
    try:
        d = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _et_day(kick: datetime.datetime) -> datetime.date:
    return kick.astimezone(EASTERN).date()


def game_deadlines(kickoffs: Mapping[Hashable, datetime.datetime],
                   rule: str = RULE) -> dict[Hashable, datetime.datetime]:
    """{game: lock instant}. `kickoffs` must hold the WHOLE slate, because the
    first-game-of-the-day rule depends on the other games on that ET day."""
    if rule not in RULES:
        raise ValueError(f"unknown deadline rule {rule!r}; one of {RULES}")
    first: dict[datetime.date, datetime.datetime] = {}
    for k in kickoffs.values():
        d = _et_day(k)
        first[d] = min(first.get(d, k), k)
    out = {}
    for g, k in kickoffs.items():
        per_game = k - LOCK_LEAD
        per_day = first[_et_day(k)]
        out[g] = {KICKOFF_MINUS_2H: per_game, FIRST_GAME_OF_DAY: per_day,
                  EARLIEST: min(per_game, per_day)}[rule]
    return out


def candidate_deadlines(kickoffs: Mapping[Hashable, datetime.datetime]
                        ) -> list[datetime.datetime]:
    """Every distinct instant either rule could lock a game at, sorted."""
    s = set(game_deadlines(kickoffs, KICKOFF_MINUS_2H).values())
    s |= set(game_deadlines(kickoffs, FIRST_GAME_OF_DAY).values())
    return sorted(s)


def label_for(deadline: datetime.datetime) -> str:
    """Snapshot label for a capture taken ahead of `deadline`, e.g. lock-sun1100.

    Starts with `lock` so every existing consumer of the lock* family accepts
    it, and is unique within a pool week (day + ET clock time), which is what
    edge.pickem_log.append's (season, week, snapshot, home_team) de-dupe needs.
    """
    et = deadline.astimezone(EASTERN)
    return f"lock-{et:%a}".lower() + f"{et:%H%M}"


def infer_rule(observations: Iterable[Mapping]) -> dict[str, dict[str, int]]:
    """Score each rule against observed lock states.

    Each observation: `observed_at` and `kickoff` (aware datetimes), `day_first`
    (the first kickoff of that game's ET day), and `locked` (bool -- other
    entries' picks were visible). Returns {rule: {"agree": n, "disagree": n}}.
    Observations that both rules predict identically carry no information and
    are skipped.
    """
    score = {r: {"agree": 0, "disagree": 0} for r in (KICKOFF_MINUS_2H, FIRST_GAME_OF_DAY)}
    for o in observations:
        t, k, f = o["observed_at"], o["kickoff"], o["day_first"]
        pred = {KICKOFF_MINUS_2H: t >= k - LOCK_LEAD, FIRST_GAME_OF_DAY: t >= f}
        if pred[KICKOFF_MINUS_2H] == pred[FIRST_GAME_OF_DAY]:
            continue
        for r, p in pred.items():
            score[r]["agree" if p == bool(o["locked"]) else "disagree"] += 1
    return score


def deadlines_from_rows(rows: Iterable[Mapping], rule: str = RULE
                        ) -> dict[tuple, datetime.datetime]:
    """{(season, week, home_team): lock instant} from line-log rows.

    Each game's kickoff is the latest non-blank `kickoff_utc` seen for it (a
    flexed game's newer time wins over Tuesday's). Slates are grouped by
    (season, week) so the first-game-of-the-day rule sees its neighbours.
    """
    kicks: dict[tuple, datetime.datetime] = {}
    for r in rows:
        k = parse_utc(r.get("kickoff_utc"))
        if k is None:
            continue
        kicks[(int(r["season"]), int(r["week"]), r["home_team"])] = k
    out: dict[tuple, datetime.datetime] = {}
    for sw in {key[:2] for key in kicks}:
        slate = {key: k for key, k in kicks.items() if key[:2] == sw}
        out.update(game_deadlines(slate, rule))
    return out


def before_deadline(captured_at, deadline: datetime.datetime | None) -> bool:
    """Was a reading taken before the game locked?

    Unknown timestamps count as in time, as the old kickoff guard did: the
    guard rejects a demonstrably late reading, it does not discard rows whose
    provenance cannot be checked.
    """
    c = parse_utc(captured_at)
    if c is None or deadline is None:
        return True
    return c < deadline
