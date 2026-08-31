"""Find Fanatics' Oddschecker league ids instead of capturing them by hand.

Adding a league to Fanatics used to cost a DevTools capture: open the league on
betfanatics.com, find the `subevent-group` request, copy the `eventId` and the
totals `bettypeId` off it. That is why three leagues had ids and twenty-odd did
not, and why HANDOFF.md carried "Fanatics has no NFL eventId" as an open item.

There is no listing endpoint -- the API rejects every parameter but `eventId`
and `subeventIds`, oddschecker.com itself is behind Cloudflare, and
betfanatics.com renders its odds client-side from ids it never puts in the HTML.
What there IS: a miss costs one 404 in ~0.2s, and the ids are small integers.
So the catalog is enumerated once and cached, rather than resolved live.

    python -m arb fanatics discover        # refresh the cache (~7 min)

Each hit carries `urlPath` -- `/us/soccer/premier-league/aston-villa-v-arsenal`
-- and its league segment is what catalog.fanatics_league() matches on. That is
a better key than the display name: Oddschecker writes both "German Bundesliga"
and "German Bundesliga Matches" for the same competition, one an outright
container and one the fixtures, and only the path is stable between them.

Cost note: 30,000 probes is not a thing to run on a schedule. Ids for a fixed
league are stable (MLB 7445 has not moved since 2026-08-26); it is the
tournament sports -- tennis and golf, a league per event -- that go stale, and
those are cheap to re-find because they cluster. Refresh weekly at most.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import http as requests
from .oddschecker_free import BASE, HEADERS

log = logging.getLogger("arb.oddschecker_discover")

# Where every id found so far has landed. Verified 2026-08-30: a dense probe of
# 1..90,000 put all 52 live leagues below 28,000, with tennis's rotating
# tournament ids the highest at ~23,700.
DEFAULT_MAX_ID = 30000
DEFAULT_WORKERS = 16
CACHE_PATH = "data/oddschecker_leagues.json"

_local = threading.local()


def _session() -> requests.Session:
    """One session per worker thread; the shim is not thread-safe to share."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def probe_event(event_id: int, session=None) -> dict | None:
    """What league this eventId names, or None if it holds nothing right now.

    Deliberately asks for the smallest useful response -- one bet type, three
    subevents -- because the sweep makes tens of thousands of these and the
    only fields read are the identity ones. A league that is out of season
    answers exactly like an id that does not exist, so "not found" here means
    "nothing to price today", not "no such league".
    """
    sess = session or _session()
    try:
        r = sess.get(BASE, params=[("eventId", str(event_id)), ("betLimit", "1"),
                                   ("subeventLimit", "3"), ("bettypeIds", "1"),
                                   ("overrideBookies", "FNP")],
                     headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        subs = (r.json() or {}).get("subevents") or []
    except Exception:                              # noqa: BLE001
        return None
    if not subs:
        return None
    first = subs[0]
    return {
        "event_id": int(event_id),
        "name": first.get("eventName"),
        "url_path": first.get("urlPath"),
        # MATCH is a fixture; OUTRIGHT is a futures container ("NBA
        # Championship"), which has no second side to arbitrage against and
        # would only add a 150-runner field to the board.
        "type": first.get("type"),
        "subevents": len(subs),
        "sample": first.get("name"),
    }


def sweep(ids=None, workers: int = DEFAULT_WORKERS, progress=None) -> list[dict]:
    """Probe a range of ids and return every one that resolved."""
    ids = list(ids if ids is not None else range(1, DEFAULT_MAX_ID))
    found: list[dict] = []
    lock = threading.Lock()
    done = [0]

    def one(eid):
        hit = probe_event(eid)
        with lock:
            done[0] += 1
            if hit:
                found.append(hit)
            if progress and done[0] % 500 == 0:
                progress(done[0], len(ids), len(found))
        return hit

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, ids))
    found.sort(key=lambda h: h["event_id"])
    return found


# How long an id stays in the cache after it last answered. A fixed league's
# id does not move; a tournament's does, and the US Open's containers go quiet
# between draws -- so a sweep that happens to run in a gap must not delete them.
STALE_AFTER_DAYS = 30


def save(rows: list[dict], path: str = CACHE_PATH, merge: bool = True) -> str:
    """Write the cache, keeping ids a sweep did not happen to catch.

    Merging rather than replacing, because "returned nothing right now" and
    "no longer exists" look identical through this API. A sweep run between
    two rounds of the US Open found neither of its draws; replacing would have
    dropped tennis from Fanatics until the next one. A dead id costs one 404
    per scan, so keeping it is the cheap side of the trade -- and anything that
    has not answered in STALE_AFTER_DAYS is dropped anyway.
    """
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    merged: dict[int, dict] = {}
    if merge:
        for old in load(path):
            merged[int(old["event_id"])] = old
    for row in rows:
        row = dict(row, last_seen=stamp)
        prev = merged.get(int(row["event_id"])) or {}
        row.setdefault("first_seen", prev.get("first_seen") or stamp)
        merged[int(row["event_id"])] = row

    keep = []
    for row in merged.values():
        seen = row.get("last_seen")
        if seen:
            try:
                age = (now - datetime.fromisoformat(seen)).days
            except ValueError:
                age = 0
            if age > STALE_AFTER_DAYS:
                continue
        keep.append(row)
    keep.sort(key=lambda r: r["event_id"])

    payload = {"generated_at": stamp, "leagues": keep}
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1)
    return path


def load(path: str = CACHE_PATH) -> list[dict]:
    """The cached sweep, or an empty list. Never raises: a missing or corrupt
    cache must cost Fanatics coverage, not the scan."""
    try:
        with open(path) as fh:
            return (json.load(fh) or {}).get("leagues") or []
    except Exception:                              # noqa: BLE001
        return []


def resolve(rows: list[dict] | None = None, path: str = CACHE_PATH) -> list[dict]:
    """Cached ids folded onto catalog leagues, ready for run.scan().

    Returns `[{name, sport_key, event_id}]`. Two rules decide which id wins
    when a league has several, and both come from what the sweep actually
    found:

    * OUTRIGHT containers are dropped. `/us/basketball/nba` resolves to both
      1554 ("NBA Championship", a futures field) and nothing else in the
      off-season -- keeping it would put a 30-runner outright on the board and
      no fixture.
    * Where two MATCH containers share a path, the one with more subevents
      wins. Oddschecker keeps a thinned duplicate of some leagues.
    """
    from .catalog import fanatics_league

    rows = rows if rows is not None else load(path)
    best: dict[str, dict] = {}
    extra: list[tuple[str, dict]] = []
    for row in rows:
        if (row.get("type") or "").upper() != "MATCH":
            continue
        league = fanatics_league(row.get("url_path") or "")
        if league is None:
            continue
        if league.tournament:
            # A tournament sport is many containers at once and they are not
            # duplicates of each other: the men's and women's US Open are
            # separate ids under one collapsed sport_key, and keeping only the
            # larger would drop a whole draw.
            extra.append((league.key, row))
            continue
        current = best.get(league.key)
        if current is None or row.get("subevents", 0) > current.get("subevents", 0):
            best[league.key] = row
    out = [{"name": (best[k].get("name") or k), "sport_key": k,
            "event_id": best[k]["event_id"]} for k in sorted(best)]
    out += [{"name": (r.get("name") or k), "sport_key": k, "event_id": r["event_id"]}
            for k, r in sorted(extra, key=lambda kr: (kr[0], kr[1]["event_id"]))]
    return out
