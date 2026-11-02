"""RotoWire's college lineups page: projected starters and who is hurt, by game.

The only free source of college starters found (2026-11-02). The page DISPLAYS a
shortened name in the link text and the full one only in its title attribute:

    <a title="Denzel Aberdeen" href="/cbasketball/player/d-aberdeen-20573">D. Aberdeen</a>

Everything here reads the title, never the text (Adam: "No using first initial as a
first name"). Team names are full ("Illinois State", "St. Mary's (CAL)",
"Fairleigh-Dickinson"), matched to DK codes by edge/ncaab.match_team in edge/dfs_run_ncaab.

Coverage is partial: on the first slate 6 of 20 teams showed no starters (a small
school before opening night), so "not listed" means not known, and never "benched".
"""
from __future__ import annotations

import re
import urllib.request

URL = "https://www.rotowire.com/daily/ncaab/lineups.php"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
#: RotoWire tag -> DK-style status. OFS = out for the season.
OUT_TAGS = {"Out", "OFS", "O", "Susp", "SUSP"}
_LOGO = re.compile(r'lineup__logo"[^>]*\balt="([^"]*)"')
_PLAYER = re.compile(r'lineup__pos[^>]*>([^<]*)</div>\s*<a title="([^"]*)"[^>]*>[^<]*</a>'
                     r'\s*<span[^>]*>([^<]*)</span>')


def fetch(timeout: float = 20) -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _players(block: str) -> tuple[list, list]:
    """(starters, injuries) of one team's list: the page puts an "Injuries" title
    between them, and a starter can carry a tag too (GTD)."""
    head, _, tail = block.partition("lineup__title is-middle")
    rows = lambda s: [{"pos": p.strip(), "name": n.strip(), "tag": t.strip()}
                      for p, n, t in _PLAYER.findall(s)]
    return rows(head), rows(tail)


def parse(html: str) -> list[dict]:
    """[{"team", "starters": [{pos, name, tag}], "injuries": [...]}], one per team."""
    out = []
    for box in html.split('class="lineup__box"')[1:]:
        names = _LOGO.findall(box)
        if len(names) != 2 or "lineup__list is-home" not in box:
            continue
        visit, _, home = box.partition("lineup__list is-home")
        for team, block in zip(names, (visit, home)):
            starters, injuries = _players(block)
            out.append({"team": team, "starters": starters, "injuries": injuries})
    return out
