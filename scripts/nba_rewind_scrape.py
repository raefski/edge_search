"""Historical NBA DraftKings GPP ownership + winning scores from free public
"lineup rewind" articles.

WHAT EXISTS (surveyed 2026-10-31, so the next person doesn't redo it):
  * Awesemo (renamed Stokastic in 2022) ran "Lineup Rewind: NBA DraftKings
    Main Slate GPP Review" posts in the 2020-21 season only (12/24/20 ..
    3/22/21). stokastic.com no longer serves them (old URLs return the
    Next.js shell; the live articles sitemap has 0 rewinds), so they come
    from the Wayback Machine. They are PROSE: the flagship GPP's name,
    entries, fee, the winner's score + cumulative ownership, and the
    ownership % of the handful of players the writer mentions (chalk, the
    boom, the bust). The lineup itself and the full ownership table are
    screenshots -- not recoverable as text.
  * Awesemo/Stokastic "Yesterday's NBA DFS winning lineups" (2021-22): the
    lineups are images only; no text data. Discovered but skipped.
  * establishtherun.com "Historical Winning Lineups" (?p=1097048) is NFL
    showdown; ETR's NBA content is subscriber-only.
  * FantasyCruncher lineup-rewind: full per-slate ownership, but the table is
    served only to logged-in premium users (page is a shell without login).
  * RotoGrinders ResultsDB / FantasyLabs contest dashboards: paid.
So the free dataset is small: ~30 slates of partial ownership. Nothing here
fits an ownership model on hundreds of slates.

Usage: python3 scripts/nba_rewind_scrape.py [--refresh-index]
Raw HTML cached in data/nba_rewind/raw/ (resumable; cached pages are not
re-fetched). Writes data/nba_rewind/{ownership,winners,failures}.csv.
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
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402

OUT = ROOT / "data" / "nba_rewind"
RAW = OUT / "raw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
DELAY = 1.5  # seconds between network requests (polite; Wayback rate-limits)

_last = [0.0]


def fetch(url: str, timeout: int = 90, tries: int = 4) -> str:
    for i in range(tries):
        wait = DELAY - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # Wayback 503s under load: back off
            if i == tries - 1:
                raise
            time.sleep(10 * (i + 1))
    raise RuntimeError("unreachable")


# ------------------------------------------------------------------ discovery
CDX = ("https://web.archive.org/cdx/search/cdx?url={d}&matchType=domain&fl=original"
       "&collapse=urlkey&filter=original:.*nba.*(rewind|gpp-review).*&limit=20000")


def discover(refresh: bool) -> list[str]:
    idx = OUT / "index.json"
    if idx.exists() and not refresh:
        return json.loads(idx.read_text())
    urls = set()
    for d in ("awesemo.com", "stokastic.com"):
        try:
            txt = fetch(CDX.format(d=d), timeout=250)
        except Exception as e:
            print(f"cdx {d}: {e}", file=sys.stderr)
            continue
        for u in txt.split():
            u = u.strip().lower()
            if not u.startswith("http") or any(x in u for x in (
                    "login", "/amp", "/feed", "%", ")", "bit.ly", "/page/", "?")):
                continue
            m = re.match(r"https?://(?:www\.)?(awesemo|stokastic)\.com/([a-z]+)/([a-z0-9-]+)/", u)
            # article slugs only: the rewind posts, not the category page
            if m and "rewind" in m.group(3) or (m and "gpp-review" in m.group(3)):
                urls.add(f"https://www.{m.group(1)}.com/{m.group(2)}/{m.group(3)}/")
    out = sorted(urls)
    OUT.mkdir(parents=True, exist_ok=True)
    idx.write_text(json.dumps(out, indent=1))
    return out


def get_page(url: str) -> tuple[str, str]:
    """(html, fetched_from). Cached by URL hash."""
    RAW.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    p = RAW / f"{key}.html"
    meta = RAW / f"{key}.src"
    if p.exists():
        return p.read_text(), meta.read_text() if meta.exists() else ""
    wb = f"https://web.archive.org/web/2022id_/{url}"
    h = fetch(wb)
    p.write_text(h)
    meta.write_text(wb)
    return h, wb


# ------------------------------------------------------------------ parsing
def text_of(h: str) -> str:
    h = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    return re.sub(r"\s+", " ", t)


MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}


def slate_date(url: str, t: str) -> str | None:
    """Slate date = the night reviewed (the post runs the next morning)."""
    m = re.search(r"\|\s*(\d{1,2})/(\d{1,2})/(\d{2})\b", t[:200000])
    s = url.rstrip("/").rsplit("/", 1)[-1]
    m2 = re.search(r"(\d{1,2})-(\d{1,2})-(20\d\d|\d\d)(?:-|$)", s)
    for mm in (m2, m):
        if mm:
            mo, d, y = int(mm.group(1)), int(mm.group(2)), int(mm.group(3))
            y = y + 2000 if y < 100 else y
            return date(y, mo, d).isoformat()
    if "christmas-day" in s:
        return "2020-12-25"
    if "new-years-eve" in s:
        return "2020-12-31"
    return None


NAME = r"([A-Z][A-Za-z.'’-]+(?: [A-Z][A-Za-z.'’-]+){1,3})"
# "Christian Wood (61.5%)"  /  "Jerami Grant , who put up 52.5 fantasy points for $5,200 (10.1x) at 5.2% ownership"
PAT_PAREN = re.compile(NAME + r"\s*\((\d{1,2}(?:\.\d+)?)%\)")
PAT_LONG = re.compile(NAME + r"\s*,? who (?:put up|scored|posted|had|finished with) ([\d.]+) (?:DK |fantasy )?points?"
                      r"(?: for \$([\d,]+))?[^.%]{0,40}? at ([\d.]+)% owner")
STOP = {"The", "Cumulative", "Points", "Only", "Here", "There", "Salary", "DraftKings", "NBA"}


def body(t: str) -> str:
    i = t.find("Lineup Rewind:")
    j = t.find("Related NBA DFS Content", i)
    for k in ("Related Topics", "Continue Reading"):
        jj = t.find(k, i)
        if jj > 0 and (j < 0 or jj < j):
            j = jj
    return t[i:j if j > 0 else i + 20000] if i >= 0 else ""


def parse(url: str, h: str):
    t = text_of(h)
    b = body(t)
    if not b:
        raise ValueError("no 'Lineup Rewind:' article body (not archived / different template)")
    d = slate_date(url, b)
    if not d:
        raise ValueError("no slate date")
    games = re.search(r"(\w+)-game NBA DFS slate", b)
    cm = re.search(r"main DraftKings GPP was the (.+?) \(\$([\d,]+) to first\)", b)
    em = re.search(r"([\d,]+) entries at \$([\d.]+) each", b)
    wm = re.search(r"(?:posted|scored|put up) (?:a |an )?([\d]{3}(?:\.\d+)?)(?: fantasy points| DK points)?", b)
    om = re.search(r"[Cc]umulative team ownership (?:checked in at|was|of|came in at) (?:just )?([\d.]+)%", b)
    contest = cm.group(1).strip() if cm else ""
    win = dict(date=d, slate=(f"{games.group(1)}-game main" if games else "main"),
               contest=contest, first_prize=(cm.group(2).replace(",", "") if cm else ""),
               entries=(em.group(1).replace(",", "") if em else ""),
               entry_fee=(em.group(2) if em else ""),
               winning_score=(wm.group(1) if wm else ""), cash_line="",
               lineup="", total_ownership=(om.group(1) if om else ""), source_url=url)
    rows, seen = [], set()
    for m in PAT_LONG.finditer(b):
        nm = m.group(1).strip()
        rows.append(dict(player=nm, salary=(m.group(3) or "").replace(",", ""),
                         own=m.group(4), dk_pts=m.group(2)))
        seen.add(norm(nm))
    for m in PAT_PAREN.finditer(b):
        nm = m.group(1).strip()
        first = nm.split()[0]
        if first in STOP or norm(nm) in seen:
            continue
        # strip leading sentence words glued to the name ("Chalk Sterling Brown")
        rows.append(dict(player=nm, salary="", own=m.group(2), dk_pts=""))
        seen.add(norm(nm))
    own = [dict(date=d, slate=win["slate"], contest=contest, contest_size=win["entries"],
                player=r["player"], player_norm=norm(r["player"]), team="", salary=r["salary"],
                ownership_pct=r["own"], dk_pts=r["dk_pts"], source_url=url) for r in rows]
    return win, own


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-index", action="store_true")
    a = ap.parse_args()
    urls = discover(a.refresh_index)
    print(f"{len(urls)} candidate rewind URLs")
    wins, owns, fails = [], [], []
    for u in urls:
        try:
            h, _ = get_page(u)
        except Exception as e:
            fails.append(dict(url=u, stage="fetch", error=str(e)[:200]))
            continue
        try:
            w, o = parse(u, h)
        except Exception as e:
            fails.append(dict(url=u, stage="parse", error=str(e)[:200]))
            continue
        if not o and not w["winning_score"]:
            fails.append(dict(url=u, stage="parse", error="article found but nothing parsed"))
        wins.append(w)
        owns.extend(o)
    # one row per slate date (duplicate URLs for one night: keep the richest)
    by = {}
    for w in wins:
        k = w["date"]
        score = sum(bool(v) for v in w.values())
        if k not in by or score > by[k][0]:
            by[k] = (score, w)
    keep_urls = {w["source_url"] for _, w in by.values()}
    wins = sorted((w for _, w in by.values()), key=lambda w: w["date"])
    owns = [o for o in owns if o["source_url"] in keep_urls]
    for name, rows in (("winners", wins), ("ownership", owns), ("failures", fails)):
        p = OUT / f"{name}.csv"
        cols = list(rows[0]) if rows else ["url", "stage", "error"]
        with p.open("w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=cols)
            wr.writeheader()
            wr.writerows(rows)
    print(f"slates {len(wins)}  ownership rows {len(owns)}  failures {len(fails)}")
    if wins:
        print(f"dates {wins[0]['date']} .. {wins[-1]['date']}")


if __name__ == "__main__":
    main()
