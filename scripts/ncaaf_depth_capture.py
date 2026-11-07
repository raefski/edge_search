#!/usr/bin/env python3
"""Capture Ourlads' FBS depth charts, keeping every change as it happens.

    python3 scripts/ncaaf_depth_capture.py              # crawl, log teams that changed
    python3 scripts/ncaaf_depth_capture.py --push       # ...and publish to the data branch
    python3 scripts/ncaaf_depth_capture.py --teams alabama,texas   # a few teams (debugging)

WHY THIS RUNS NOW, BEFORE ANY MODEL USES IT
NCAAF_EXPERT_SOURCES.md #3: unladdered drafted players are projected at MAE
4.07 against 4.21 for predicting zero, and the lead is depth position x recent
usage share. A held-out test needs the depth chart AS IT STOOD before each
lock, and Ourlads shows only today's: nobody archives it (2026-11-07). So the
history starts the day this runs.

THE SOURCE
ourlads.com/ncaa-football-depth-charts/depth-chart.aspx?s=<slug>&id=<id>, plain
HTML, one ~128 KB page per team; robots.txt does not exclude it. Each page has
a "Updated: 11/06/2026 9:09PM ET" stamp. The stamps vary by team (7/29 to
10/26 when the write-up was done), so freshness is per team and is logged.
Rows are position slots (QB, RB, WR-X, ..., PT, PK, KR, RES) with up to five
players in depth order; gold = 2026 transfer, purple = 2026 true freshman
(the page's own legend).

The crawl is polite by construction: one page per team, GAP seconds apart,
about 135 pages and 5 minutes a run.

WHAT IS WRITTEN (data/ncaaf_depth/, published to the data branch)
  <ISO week>.csv   one row per player per depth slot, for each team whose chart
                   CHANGED since the last capture (a stamp change with the same
                   chart is not a change). Week by capture date, ET.
  seen.json        each team's last logged version, stamp and capture time
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402

OUT = ROOT / "data" / "ncaaf_depth"
BASE = "https://www.ourlads.com/ncaa-football-depth-charts/"
ET = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
GAP = 1.5
#: the four tables on a team page, in page order
SECTIONS = {"": "offense", "2": "defense", "3": "special", "4": "reserve"}

FIELDS = ("captured_at", "team_id", "team", "slug", "conference", "updated", "version",
          "section", "slot", "pos", "depth", "jersey", "player", "player_key",
          "player_raw", "class", "transfer", "true_freshman", "ourlads_id")

INDEX_TEAM_RE = re.compile(r"depth-chart\.aspx\?s=([a-z0-9-]+)&(?:amp;)?id=(\d+)")
PICKER_RE = re.compile(
    r"<a class='tp-team' href='/ncaa-football-depth-charts/depth-chart\.aspx\?s=([a-z0-9-]+)&amp;id=(\d+)'"
    r" data-conf='([^']*)'[^>]*>.*?<span class='tp-team-name'>([^<]+)</span>", re.S)
STAMP_RE = re.compile(r"Updated:\s*([0-9/]+\s+[0-9:]+\s*[AP]M\s*ET)")
TBODY_RE = re.compile(r'<tbody id="ctl00_phContent_dcTBody(\d*)">(.*?)</tbody>', re.S)
TR_RE = re.compile(r"<tr class='row-dc-[a-z]+'>(.*?)</tr>", re.S)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
PLAYER_RE = re.compile(r"<a href='[^']*/player/([^/']*)/(\d+)' class='([^']*)'>([^<]*)</a>\s*"
                       r"<span class='dc-key'>([^<]*)</span>", re.S)


def _get(url: str, session) -> str:
    r = session.get(url, headers={"user-agent": UA}, timeout=40)
    r.raise_for_status()
    return r.text


def teams_from(index_html: str, team_html: str = "") -> dict:
    """{team id: {slug, name, conference}} from the index page's links and the
    team picker (on every team page; it alone carries the conference)."""
    # the index also links a "fcs-and-small-college-nfl-prospects" page: not a team
    out = {tid: {"slug": slug, "name": "", "conference": ""}
           for slug, tid in INDEX_TEAM_RE.findall(index_html) if "prospects" not in slug}
    for slug, tid, conf, name in PICKER_RE.findall(team_html):
        out[tid] = {"slug": slug, "name": html.unescape(name).strip(), "conference": conf}
    return out


def first_last(raw: str) -> str:
    """Ourlads writes "Rogers, Noah"; everything we join against writes Noah Rogers."""
    raw = html.unescape(raw or "").strip()
    last, sep, first = raw.partition(", ")
    return f"{first.strip()} {last.strip()}".strip() if sep else raw


def parse_chart(page: str) -> tuple[str, list[dict]]:
    """(the page's Updated stamp, one dict per filled depth slot)."""
    m = STAMP_RE.search(page)
    stamp = m.group(1) if m else ""
    rows = []
    for suffix, body in TBODY_RE.findall(page):
        section = SECTIONS.get(suffix, f"table{suffix}")
        for slot, tr in enumerate(TR_RE.findall(body)):
            tds = TD_RE.findall(tr)
            if not tds:
                continue
            pos = re.sub(r"<[^>]+>", "", tds[0]).strip()
            # then (jersey, player cell) pairs, depth 1..5
            for depth, i in enumerate(range(1, len(tds) - 1, 2), start=1):
                jersey = re.sub(r"<[^>]+>", "", tds[i]).strip()
                pm = PLAYER_RE.search(tds[i + 1])
                if not pm or pm.group(2) == "0" or not pm.group(4).strip():
                    continue
                name = first_last(pm.group(4))
                rows.append({"section": section, "slot": slot, "pos": pos, "depth": depth,
                             "jersey": jersey, "player": name, "player_key": norm(name),
                             "player_raw": html.unescape(pm.group(4)).strip(),
                             "class": pm.group(5).strip(),
                             "transfer": int("lc_gold" in pm.group(3)),
                             "true_freshman": int("lc_purple" in pm.group(3)),
                             "ourlads_id": pm.group(2)})
    return stamp, rows


def version_of(rows: list[dict]) -> str:
    body = [(r["section"], r["slot"], r["pos"], r["depth"], r["jersey"], r["ourlads_id"],
             r["class"]) for r in rows]
    return hashlib.sha1(json.dumps(body).encode()).hexdigest()[:12]


def _load_seen() -> dict:
    try:
        return json.loads((OUT / "seen.json").read_text())
    except (OSError, ValueError):
        return {}


def capture(only: set | None = None, gap: float = GAP) -> tuple[list[str], list[str], bool]:
    """Crawl every team: (lines to print, teams that failed, wrote anything)."""
    import requests
    session = requests.Session()
    now = datetime.now(timezone.utc)
    captured_at = now.isoformat(timespec="seconds")
    teams = teams_from(_get(BASE, session))
    seen = _load_seen()
    order = sorted(teams, key=lambda t: teams[t]["slug"])
    if only:
        order = [t for t in order if teams[t]["slug"] in only]
    rows, failed, msgs, changed_teams, empty = [], [], [], [], []
    pages = {}
    for tid in order:
        time.sleep(gap)
        try:
            pages[tid] = _get(f"{BASE}depth-chart.aspx?s={teams[tid]['slug']}&id={tid}", session)
        except Exception as exc:                            # noqa: BLE001
            failed.append(teams[tid]["slug"])
            msgs.append(f"  ! {teams[tid]['slug']}: {type(exc).__name__}: {exc}")
            continue
        # The picker names every team and its conference EXCEPT the page's own
        # team, so names are filled from all the pages before any row is built.
        for k, v in teams_from("", pages[tid]).items():
            teams.setdefault(k, {}).update(v)
    for tid, page in pages.items():
        t = teams[tid]
        stamp, chart = parse_chart(page)
        if not chart:
            empty.append(t["slug"])
            continue
        ver = version_of(chart)
        if (seen.get(tid) or {}).get("version") == ver:
            continue
        for r in chart:
            rows.append({"captured_at": captured_at, "team_id": tid, "team": t["name"] or t["slug"],
                         "slug": t["slug"], "conference": t["conference"], "updated": stamp,
                         "version": ver, **r})
        seen[tid] = {"slug": t["slug"], "version": ver, "updated": stamp, "captured_at": captured_at}
        changed_teams.append(t["slug"])
    if rows:
        y, w, _ = now.astimezone(ET).isocalendar()
        path = OUT / f"{y}-W{w:02d}.csv"
        OUT.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                wr.writeheader()
            wr.writerows(rows)
        (OUT / "seen.json").write_text(json.dumps(seen, indent=0, sort_keys=True))
    msgs.append(f"{len(order)} teams crawled, {len(changed_teams)} changed ({len(rows)} rows), "
                f"{len(empty)} with no chart, {len(failed)} failed")
    if empty:
        msgs.append(f"  no chart: {', '.join(empty[:20])}")
    return msgs, failed, bool(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--push", action="store_true", help="publish data/ncaaf_depth to the data branch")
    ap.add_argument("--teams", help="comma-separated Ourlads slugs (alabama,texas)")
    args = ap.parse_args()
    only = set(args.teams.split(",")) if args.teams else None
    msgs, failed, changed = capture(only)
    print("\n".join(msgs))
    if changed and args.push:
        from edge.data_branch import publish_one
        if not publish_one(str(OUT.relative_to(ROOT)), "ncaaf depth charts"):
            return 1
    # A few pages failing is a site hiccup the next run repairs (only changes are
    # logged, so nothing is lost but timing); most of them failing is a block.
    return 1 if len(failed) > 10 else 0


if __name__ == "__main__":
    sys.exit(main())
