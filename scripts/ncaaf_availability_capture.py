#!/usr/bin/env python3
"""Capture the Power-4 football availability reports as they are posted.

    python3 scripts/ncaaf_availability_capture.py           # poll, log new versions
    python3 scripts/ncaaf_availability_capture.py --push    # ...and publish to the data branch

WHY THIS RUNS NOW, BEFORE ANY MODEL USES IT
NCAAF_EXPERT_SOURCES.md ranks the conference reports first (availability and
the promoted backup), and a fit needs >= 5 slates of reports captured BEFORE
lock next to what happened. The vendor keeps only the LATEST version of each
game's report: Wednesday's initial report is replaced by Thursday's, which is
replaced by the game-day report 90-120 minutes before kickoff. A version
nobody saved is gone, so this polls and keeps each one with its posted time.

WHERE THE REPORTS COME FROM
SEC, Big Ten, ACC and Big 12 all embed one vendor's app on their report pages
(app.hdintelligence.com, ?source=SEC|B10|ACC|B12). The page asks two public
endpoints, found with `harness navigate` on big12sports.com 2026-11-07 and
replayed here with a plain POST (no cookie, no referrer needed):

  get-publish-public   the current report for every game, both teams' WHOLE
                       travel party: "QB #16 Arch Manning" Available / Out /
                       Game Time Decision / Questionable / Exempt ...
                       (SEC 10.8 MB, mostly base64 logos, stripped here)
  get-archive-public   the season so far: every player listed as anything but
                       Available, with his status in each report of the week
                       (Initial, Update 1, Update 2, Game Day). SEC had 787
                       player-games back to week 2. No posted times and no
                       positions, but it is a backfill nothing else gives us.

Conference games only; G5 and non-conference games have no report.

WHAT IS WRITTEN (data/ncaaf_availability/, published to the data branch)
  <ISO week>.csv     one row per player per report VERSION (a version is the
                     report's type, posted time and rows; an identical re-poll
                     writes nothing). Week by capture date, ET: a Saturday
                     game's Wednesday-to-Saturday reports share one file.
  archive_<CONF>.json  the season archive as last fetched (rewritten only when
                     it changes; the data branch's history keeps the old ones)
  seen.json          versions already logged, so a re-poll is a no-op
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402

OUT = ROOT / "data" / "ncaaf_availability"
API = "https://app.hdintelligence.com/api"
CONFERENCES = ("SEC", "B10", "ACC", "B12")
ET = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
#: seen.json forgets a report this long after its game; the vendor drops a
#: game's report within days of it, so nothing older can come back.
SEEN_DAYS = 21

FIELDS = ("captured_at", "conference", "report_id", "version", "report_type",
          "publish_date", "posted_time", "conf_tz", "game_date", "game_time",
          "location", "neutral", "team", "opponent", "side", "pos", "jersey",
          "player", "player_key", "status", "exempt")

# "QB #16 Arch Manning", "QB #9 Karle \"KJ\" Lacey Jr.", "CB #1 Vernon Glover, Jr."
ROW_RE = re.compile(r"^(?P<pos>[A-Z/]+)\s+#(?P<num>\d+)\s+(?P<name>.+)$")
# 2 of 6,626 rows on 2026-11-07 had no number ("S CJ Christian"). Without the
# "#" the first word is a position only if it is one: "CJ Christian" is a name.
POSITIONS = {"QB", "RB", "FB", "WR", "TE", "OL", "OT", "IOL", "C", "DL", "DE", "DT", "NG",
             "EDG", "LB", "ILB", "OLB", "JCK", "NCK", "STR", "DB", "CB", "S", "DS", "N/S",
             "K", "P", "P/K", "LS", "ATH"}


def _post(endpoint: str, conf: str, session=None) -> dict:
    import requests
    s = session or requests
    r = s.post(f"{API}/{endpoint}", timeout=90,
               headers={"user-agent": UA, "content-type": "application/json",
                        "accept": "application/json, text/plain, */*"},
               data=json.dumps({"sport": "Football", "organization": conf,
                                "conference": conf}))
    r.raise_for_status()
    return r.json() if r.content else {}


def split_name(raw: str) -> tuple[str, str, str]:
    """(position, jersey, player) from one report row's name."""
    raw = (raw or "").strip()
    m = ROW_RE.match(raw)
    if m:
        return m.group("pos"), m.group("num"), m.group("name").strip()
    first, _, rest = raw.partition(" ")
    if first in POSITIONS and rest:
        return first, "", rest.strip()
    return "", "", raw


def version_of(report: dict) -> str:
    """A report version: its type, posting stamp and every row. Logos and colours
    are left out, so a restyled page is not a new version."""
    body = {"type": report.get("ReportType"), "date": report.get("publishDate"),
            "posted": report.get("postedTime"),
            "games": [[g.get("teamName"), sorted((r.get("name"), r.get("status"), r.get("exemptStatus"))
                                                 for r in g.get("rows") or [])]
                      for g in report.get("games") or []]}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


def report_rows(conf: str, report_id: str, report: dict, captured_at: str) -> list[dict]:
    """One row per player per team. The first team listed is the away side
    (TCU at Arizona, footer location Tucson); `side` records the order only."""
    footer = report.get("footer") or {}
    games = report.get("games") or []
    teams = [g.get("teamName") or g.get("teamDisplayName") or "" for g in games]
    ver = version_of(report)
    out = []
    for i, g in enumerate(games):
        opp = teams[1 - i] if len(teams) == 2 else ""
        for r in g.get("rows") or []:
            pos, num, name = split_name(r.get("name"))
            out.append({
                "captured_at": captured_at, "conference": conf, "report_id": report_id,
                "version": ver, "report_type": report.get("ReportType") or "",
                "publish_date": report.get("publishDate") or "",
                "posted_time": report.get("postedTime") or "",
                "conf_tz": report.get("conferenceTimeZone") or "",
                "game_date": footer.get("date") or "", "game_time": footer.get("time") or "",
                "location": footer.get("location") or "",
                "neutral": int(bool(report.get("gameNeutral"))),
                "team": teams[i], "opponent": opp, "side": "first" if i == 0 else "second",
                "pos": pos, "jersey": num, "player": name, "player_key": norm(name),
                "status": r.get("status") or "", "exempt": r.get("exemptStatus") or "",
            })
    return out


def strip_archive(payload: dict) -> dict:
    """The archive without its base64 logo: data rows and the report-day names."""
    return {"report_days": payload.get("report_days") or [],
            "data": payload.get("data") or []}


def _load_seen() -> dict:
    try:
        return json.loads((OUT / "seen.json").read_text())
    except (OSError, ValueError):
        return {}


def _prune(seen: dict, today: date) -> dict:
    keep = {}
    for key, v in seen.items():
        try:
            gd = date.fromisoformat(v.get("game_date") or "")
        except ValueError:
            gd = today
        if (today - gd).days <= SEEN_DAYS:
            keep[key] = v
    return keep


def week_file(now_et: datetime) -> Path:
    y, w, _ = now_et.isocalendar()
    return OUT / f"{y}-W{w:02d}.csv"


def append_rows(path: Path, rows: list[dict]) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def capture(confs=CONFERENCES, gap: float = 1.0) -> tuple[list[str], list[str], bool]:
    """Poll every conference: (lines to print, conferences that failed, wrote anything)."""
    import requests
    session = requests.Session()
    now = datetime.now(timezone.utc)
    captured_at = now.isoformat(timespec="seconds")
    seen = _prune(_load_seen(), now.astimezone(ET).date())
    rows, msgs, failed, changed = [], [], [], False
    for conf in confs:
        try:
            current = _post("get-publish-public", conf, session)
            time.sleep(gap)
            archive = _post("get-archive-public", conf, session)
            time.sleep(gap)
        except Exception as exc:                            # noqa: BLE001
            failed.append(conf)
            msgs.append(f"  ! {conf}: {type(exc).__name__}: {exc}")
            continue
        n_new = 0
        for rid, rep in (current or {}).items():
            if not isinstance(rep, dict):
                continue
            ver = version_of(rep)
            key = f"{conf}:{rid}:{ver}"
            if key in seen:
                continue
            got = report_rows(conf, str(rid), rep, captured_at)
            rows += got
            seen[key] = {"game_date": (rep.get("footer") or {}).get("date"),
                         "type": rep.get("ReportType"), "first_seen": captured_at}
            n_new += 1
        arch = strip_archive(archive or {})
        apath = OUT / f"archive_{conf}.json"
        try:
            old = json.loads(apath.read_text())
        except (OSError, ValueError):
            old = None
        if arch["data"] and (old is None or {k: old.get(k) for k in arch} != arch):
            OUT.mkdir(parents=True, exist_ok=True)
            apath.write_text(json.dumps({**arch, "fetched_at": captured_at}, indent=0))
            changed = True
        msgs.append(f"  {conf}: {len(current or {})} current reports, {n_new} new version(s); "
                    f"archive {len(arch['data'])} player-games")
    if rows:
        append_rows(week_file(now.astimezone(ET)), rows)
        changed = True
    if changed:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "seen.json").write_text(json.dumps(seen, indent=0, sort_keys=True))
    msgs.append(f"{len(rows)} player rows logged" + ("" if changed else " -- nothing changed"))
    return msgs, failed, changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--push", action="store_true", help="publish data/ncaaf_availability to the data branch")
    args = ap.parse_args()
    msgs, failed, changed = capture()
    print("\n".join(msgs))
    if changed and args.push:
        from edge.data_branch import publish_one
        if not publish_one(str(OUT.relative_to(ROOT)), "ncaaf availability reports"):
            return 1
    # A conference that failed is a red timer in `harness status`, even when
    # the others were logged: a quiet gap in this log cannot be refilled.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
