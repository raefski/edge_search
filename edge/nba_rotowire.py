"""RotoWire's NBA lineups page: who each team lists as Out / Doubtful / GTD tonight.

Adam, 2026-10-29: "find out who is the fastest to reporting who's out". Our sources
until then were NBA.com's lineups (inactive list posts ~30 min before tip), DK's own
player status and the NBA's official injury report (every 15 min). RotoWire's page is
updated by hand from beat writers and team news, so it may beat all three. This
module only READS it; harness/nba_scratch.py logs every status change it shows to
data/nba_news_lead_log.csv beside the official report's, and scripts/nba_news_lead.py
measures who was first. Nothing is projected from it until that log says it leads.

The page is server-rendered HTML; each team's block opens with buttons carrying
data-team="SAC", followed by its players:

    <li class="lineup__player is-pct-play-0 has-injury-status" title="Very Unlikely To Play">
        <a title="Keegan Murray" href="...">K. Murray</a>
        <span class="lineup__inj">Out</span>
"""
from __future__ import annotations

import re
import urllib.request

URL = "https://www.rotowire.com/basketball/nba-lineups.php"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
#: the page's abbreviations -> ours (edge.nba.team does the rest)
STATUS = {"Out": "Out", "OFS": "Out", "Doubt": "Doubtful", "GTD": "GTD", "Ques": "Questionable",
          "Prob": "Probable"}

_TOKEN = re.compile(
    r'data-team="(?P<team>[A-Z]{2,4})"'
    r'|<li class="lineup__player(?P<cls>[^"]*)" title="(?P<likely>[^"]*)">.*?'
    r'<a title="(?P<name>[^"]+)".*?</a>\s*(?:<span class="lineup__inj">(?P<inj>[^<]*)</span>)?',
    re.S)


def fetch(timeout: float = 20) -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse(html: str) -> list[dict]:
    """[{"team", "name", "status", "likely"}] for every player with an injury tag.
    `status` is Out / Doubtful / GTD / Questionable / Probable (OFS = out for the
    season counts as Out); `likely` is RotoWire's own "Very Unlikely To Play" label."""
    from edge import nba
    team, out, seen = None, [], set()
    for m in _TOKEN.finditer(html):
        if m.group("team"):
            team = nba.team(m.group("team"))
            continue
        inj = (m.group("inj") or "").strip()
        if not inj or team is None:
            continue
        key = (team, m.group("name"))
        if key in seen:                                 # a player shows in two views of the page
            continue
        seen.add(key)
        out.append({"team": team, "name": m.group("name"), "status": STATUS.get(inj, inj),
                    "likely": m.group("likely")})
    return out
