"""The NBA's official injury report: who is Out / Doubtful / Questionable / Probable.

Critique cycle 7 (NBA_MINUTES_CRITIQUE_LOG.md). Before tip there is no inactive list
(NBA.com's lineups feed stopped carrying one in 2026-27) and ESPN's injuries feed --
the Out list cycles 3 and 6 used -- lists injuries only: of the 47 role-10+ players
who did not dress on 2026-10-20/21 it had 18 Out, 16 Day-To-Day and 11 not at all
(two-way / G-League players). The league's own report covers every team's injured
AND two-way / G-League players, with a status, every 15 minutes (hourly before
2026), and its PDFs are archived at a predictable URL:

    https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-22_05_00PM.pdf
    https://ak-static.cms.nba.com/referee/injury/Injury-Report_2025-01-15_06PM.pdf

`report(date, hour, minute)` fetches one (cached in data/nba_injury_reports/, a
missing report cached as such) and `parse` turns its text into rows. Names are
"Last, First"; teams are left to the caller (a team cell spans its players'
rows and its label sits mid-block), which joins on edge.names.norm.
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "nba_injury_reports"
URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{name}.pdf"
ET = ZoneInfo("America/New_York")
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
#: "Last, First   Status" in pypdf's layout text; a suffix may sit on either side
#: ("Jackson Jr., Jaren", "Porter, Otto Jr."). Two or more spaces end the name.
ROW = re.compile(r"([A-Z][A-Za-z'\.\- ]*?(?: (?:Jr|Sr|II|III|IV)\.?)?)\s*,\s*([A-Za-z'\.\-]+(?: [A-Za-z'\.\-]+)*?)\s{2,}"
                 r"(" + "|".join(STATUSES) + r")\b[ \t]*([^\n]*)")
NOT_SUBMITTED = re.compile(r"NOT\s+YET\s+SUBMITTED")
#: What report() last served, for the page: "official <time>", "none" or "failed: ...".
LAST_SOURCE = "none"


def names_for(day: str, hour: int, minute: int = 0) -> list[str]:
    """URL names to try for the report at hour:minute ET on day 'YYYY-MM-DD' -- the
    15-minute format (2026 on) first, then the hourly one (before)."""
    h12 = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    out = [f"{day}_{h12:02d}_{minute:02d}{ampm}"]
    if minute == 0:
        out.append(f"{day}_{h12:02d}{ampm}")
    return out


def _fetch(name: str) -> bytes | None:
    req = urllib.request.Request(URL.format(name=name), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):              # S3-style: a report that was never published
            return None
        raise


def report(day: str, hour: int, minute: int = 0, fetch: bool = True, pace: float = 0.0) -> tuple[str, bytes] | None:
    """(name, PDF bytes) of the report at hour:minute ET, or None if there is none.
    Cached on disk, a missing one too (data/nba_injury_reports/<name>.missing), so an
    archive backfill never asks twice. Past reports never change."""
    CACHE.mkdir(parents=True, exist_ok=True)
    for name in names_for(day, hour, minute):
        pdf, miss = CACHE / f"{name}.pdf", CACHE / f"{name}.missing"
        if pdf.exists():
            return name, pdf.read_bytes()
        if miss.exists() or not fetch:
            continue
        if pace:
            time.sleep(pace)
        body = _fetch(name)
        if body and body[:4] == b"%PDF":
            pdf.write_bytes(body)
            return name, body
        miss.write_text("")
    return None


def text(pdf: bytes) -> str:
    """Layout text of every page (pypdf)."""
    import pypdf
    r = pypdf.PdfReader(io.BytesIO(pdf))
    return "\n".join(p.extract_text(extraction_mode="layout") for p in r.pages)


def parse(pdf: bytes) -> dict:
    """{"rows": [(last, first, status, reason)], "not_submitted": n, "status_words": n} --
    the last is a check: every listed player carries exactly one status word. `reason`
    is the text on the status line ("Injury/Illness- Left Ankle", "G League - Two-Way");
    a reason wrapped onto other lines comes back partial or empty."""
    t = text(pdf)
    # a team / matchup label can sit on the player's line, columns apart: the name is
    # what follows the last run of 2+ spaces
    rows = [(re.split(r"\s{2,}", m.group(1).strip())[-1], m.group(2).strip(), m.group(3),
             re.sub(r"\s+", " ", m.group(4)).strip()) for m in ROW.finditer(t)]
    words = sum(len(re.findall(r"\b" + s + r"\b", t)) for s in STATUSES)
    return {"rows": rows, "not_submitted": len(NOT_SUBMITTED.findall(t)), "status_words": words}


def full_name(last: str, first: str) -> str:
    """'Jackson Jr.', 'Jaren' -> 'Jaren Jackson Jr.' (edge.names.norm drops the suffix anyway)."""
    return f"{first} {last}".strip()


_OUT_CACHE: dict = {}


def out_at_tip(game_id: str, lead_min: int = 60) -> set[str] | None:
    """edge.names.norm names the official report listed Out `lead_min` before the
    game's tip (floored to the hour, falling back 1-2 hours; cache only), or None when
    there is no report or no tip time. Critique cycle 8: through 2025-26 NBA.com
    flagged ~1,100 a season of these injured players active with 0 minutes; as history
    they decayed the role while hurt and hid the return ramp's 5-game gap, so role
    history skips them (lab and edge.nba_minutes.load_history alike)."""
    from edge.names import norm
    if "tips" not in _OUT_CACHE:
        f = Path(__file__).resolve().parents[1] / "data" / "nba_ground_truth" / "schedule_tips.json"
        _OUT_CACHE["tips"] = json.loads(f.read_text()) if f.exists() else {}
    t = _OUT_CACHE["tips"].get(game_id)
    if not t:
        return None
    at = datetime.fromisoformat(t.replace("Z", "")) - timedelta(minutes=lead_min)
    for back in (0, 1, 2):
        got = report(at.date().isoformat(), at.hour - back, 0, fetch=False)
        if got:
            if got[0] not in _OUT_CACHE:
                _OUT_CACHE[got[0]] = {norm(full_name(a, b)) for a, b, st, _ in parse(got[1])["rows"] if st == "Out"}
            return _OUT_CACHE[got[0]]
    return None


def latest(now: datetime | None = None, back_min: int = 120, fetch: bool = True) -> tuple[str, dict] | None:
    """The newest report published at or before `now` (ET), looking back `back_min`
    minutes in 15-minute steps. (name, parse()) or None."""
    global LAST_SOURCE
    now = (now or datetime.now(ET)).astimezone(ET)
    t = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    try:
        for _ in range(back_min // 15 + 1):
            got = report(t.date().isoformat(), t.hour, t.minute, fetch=fetch and t.date() == now.date()
                         and (now - t) < timedelta(hours=3))
            if got:
                LAST_SOURCE = f"official {got[0]}"
                return got[0], parse(got[1])
            t -= timedelta(minutes=15)
    except Exception as exc:                                # noqa: BLE001
        LAST_SOURCE = f"failed: {exc}"
        return None
    LAST_SOURCE = "none"
    return None
