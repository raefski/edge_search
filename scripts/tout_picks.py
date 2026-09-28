#!/usr/bin/env python3
"""What YouTube's NFL touts pick against the spread, graded over time.

    python3 scripts/tout_picks.py record --date 2026-09-28 --away PHI --home CHI --line 3.5
    python3 scripts/tout_picks.py grade  --date 2026-09-28 --away PHI --home CHI --line 3.5
    python3 scripts/tout_picks.py report

`record` searches YouTube for the game, keeps videos that aired before kickoff,
and takes each one's FINAL spread call ("give me the Bears plus three and a
half") into data/tout_picks.csv with the quote it came from, so a wrong read
can be checked by eye. `grade` scores every recorded pick against the final
score into data/source_ledger.csv (domain nfl_ats, metric ats_correct: 1 cover,
0 miss, 0.5 push); `report` ranks channels on it.

`--line` is the HOME team's spread in the pool (CHI +3.5 -> 3.5). Grading
against one line for every tout is deliberate: the question is whether a
source helps pick THIS number, not whether it beat whatever number it quoted.

Expect little: the research this started from found most betting touts lose.
The point is to find the few that do not, and stop reading the rest.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import urllib.request
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "transcripts"))
from edge.nfl import TEAM_NAME_TO_ABBR  # noqa: E402
from transcripts import Cache, Ledger, YouTube  # noqa: E402

PICKS = ROOT / "data" / "tout_picks.csv"
LEDGER = ROOT / "data" / "source_ledger.csv"
CACHE = ROOT / "data" / "cache" / "buzz"
GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
FIELDS = ["date", "game", "video_id", "channel_id", "channel", "views", "aired", "pick", "quote"]

_VERB = re.compile(r"\b(give me|i like|i'?m taking|i'?ll take|we'?ll take|i'?m on|i'?m going with|"
                   r"i'?ll go with|my pick|our pick|i'?m backing|i'?d take|i would take|i lean|"
                   r"lean(?:ing)? (?:to|towards)|the play is|hammer|bet on|side is|i'?m playing|"
                   r"i'?m rolling with|give us)\b", re.I)
_SPREAD = re.compile(r"\b(plus|minus|points|cover|covers|covering|spread|dog|underdog|"
                     r"field goal|three and a half|four and a half)\b|(?<![\w-])[+-]\s?\d", re.I)
_NOT_SIDE = re.compile(r"\b(over|under|total|yards|receptions?|touchdowns?|props?|first half|"
                       r"1st half|quarter|alt(?:ernate)?|parlay|anytime|team total|captain|"
                       r"showdown|lineups?|builds?|spread (?:is|was|leaning|has))\b", re.I)


def team_aliases() -> dict:
    """{alias: abbr}: nickname, city ("Philly" too), full name."""
    out = {"philly": "PHI", "niners": "SF", "bucs": "TB", "pats": "NE", "jags": "JAX",
           "commies": "WAS", "vegas": "LV", "la rams": "LAR", "la chargers": "LAC"}
    for full, abbr in TEAM_NAME_TO_ABBR.items():
        words = full.split()
        out[full.lower()] = abbr
        out[words[-1].lower()] = abbr
        city = " ".join(words[:-1]).lower()
        if city not in ("los angeles", "new york"):          # two teams each
            out[city] = abbr
    return out


def names_for(abbr: str) -> tuple[str, str]:
    full = next(k for k, v in TEAM_NAME_TO_ABBR.items() if v == abbr)
    return full, full.split()[-1]


def extract_pick(text: str, away: str, home: str, aliases: dict) -> tuple[str, str] | None:
    """(abbr, quote) for the LAST spread call in a transcript on this game --
    videos argue both sides and give the verdict at the end."""
    team_re = re.compile(r"\b(" + "|".join(sorted((re.escape(a) for a, t in aliases.items()
                                                   if t in (away, home)), key=len, reverse=True))
                         + r")\b", re.I)
    pick = None
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        v = _VERB.search(sentence)
        if not v or not _SPREAD.search(sentence) or _NOT_SIDE.search(sentence):
            continue
        after = team_re.search(sentence, v.end())
        if after:
            pick = (aliases[after.group(1).lower()], sentence.strip()[:300])
    return pick


def kickoff(date: str, away: str, home: str) -> datetime:
    """From the pick'em odds snapshot if it has the game, else 8:15 PM ET."""
    import json
    snap = ROOT / "data" / "odds_snapshot_pickem_nfl.json"
    if snap.exists():
        home_full, _ = names_for(home)
        for e in json.loads(snap.read_text()).get("events", []):
            if e.get("home_team") == home_full and e.get("commence_time", "").startswith(
                    (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat()) \
                    or (e.get("home_team") == home_full and e.get("commence_time", "").startswith(date)):
                return datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
    d = datetime.fromisoformat(date).date()
    return datetime.combine(d, dtime(20, 15), ZoneInfo("America/New_York")).astimezone(timezone.utc)


def record(args) -> None:
    aliases = team_aliases()
    (_, away_nick), (_, home_nick) = names_for(args.away), names_for(args.home)
    ko = kickoff(args.date, args.away, args.home)
    since = ko - timedelta(days=args.days)
    yt = YouTube(Cache(CACHE), pace=2.0)
    found: dict = {}
    for q in (f"{away_nick} vs {home_nick} picks", f"{away_nick} {home_nick} prediction",
              f"{away_nick} at {home_nick} pick against the spread",
              f"{away_nick} {home_nick} best bets"):
        for sort in ("relevance", "date"):
            for v in yt.search(q, sort=sort, limit=25):
                t = v["title"].lower()
                if (away_nick.lower() in t or args.away.lower() in t) and \
                        (home_nick.lower() in t or args.home.lower() in t):
                    found.setdefault(v["id"], v)
    game = f"{args.away}@{args.home}"
    rows = []
    for vid, v in sorted(found.items(), key=lambda kv: -kv[1]["views"]):
        meta = yt.video(vid, v["title"])
        aired = YouTube.aired(meta)
        if not aired or not since <= aired < ko:
            continue
        segs = yt.transcript(vid, aired)
        if not segs:
            continue
        cutoff = (ko - aired).total_seconds()
        hit = extract_pick(" ".join(t for s, t in segs if s < cutoff), args.away, args.home, aliases)
        if hit:
            rows.append({"date": args.date, "game": game, "video_id": vid,
                         "channel_id": meta.get("channel_id") or "", "channel": meta.get("channel", ""),
                         "views": meta.get("views", 0), "aired": aired.isoformat(timespec="minutes"),
                         "pick": hit[0], "quote": hit[1]})
    keep = []
    if PICKS.exists():
        with PICKS.open(newline="") as fh:
            keep = [r for r in csv.DictReader(fh) if (r["date"], r["game"]) != (args.date, game)]
    with PICKS.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(keep + rows)
    tally = {t: sum(r["pick"] == t for r in rows) for t in (args.away, args.home)}
    print(f"{game}: {len(rows)} spread picks from {len(found)} videos -- {tally}")
    for r in rows:
        print(f"  {r['pick']:3} {int(r['views']):>7,} {r['channel'][:30]:30} | {r['quote'][:150]}")


def grade(args) -> None:
    game = f"{args.away}@{args.home}"
    score = None
    for r in csv.DictReader(urllib.request.urlopen(GAMES_URL, timeout=120).read()
                            .decode().splitlines()):
        if r["gameday"] == args.date and r["away_team"] == args.away and r["home_team"] == args.home:
            if r["home_score"] and r["away_score"]:
                score = (int(r["away_score"]), int(r["home_score"]))
    if score is None:
        raise SystemExit(f"{game} on {args.date} has no final score in nflverse yet")
    margin = score[1] - score[0] + args.line                  # home, with the points
    covered = args.home if margin > 0 else args.away if margin < 0 else None
    ledger = Ledger(LEDGER)
    with PICKS.open(newline="") as fh:
        picks = [r for r in csv.DictReader(fh) if (r["date"], r["game"]) == (args.date, game)]
    for r in picks:
        value = 0.5 if covered is None else float(r["pick"] == covered)
        ledger.record("nfl_ats", r["channel_id"], r["channel"], f"{args.date} {game}",
                      "ats_correct", value)
    print(f"{game} {score[0]}-{score[1]}, home line {args.line:+}: "
          f"{'push' if covered is None else covered + ' covered'}; "
          f"{sum(r['pick'] == covered for r in picks)}/{len(picks)} touts right")


def report(_args) -> None:
    rows = Ledger(LEDGER).scores("nfl_ats", "ats_correct")
    print(f"{'channel':40} {'picks':>5} {'ATS':>6}")
    for s in rows:
        print(f"{s['source'][:40]:40} {s['n']:5} {s['mean']:6.1%}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("record", "grade"):
        p = sub.add_parser(name)
        p.add_argument("--date", required=True, help="game day (ET), YYYY-MM-DD")
        p.add_argument("--away", required=True)
        p.add_argument("--home", required=True)
        p.add_argument("--line", type=float, required=True, help="home team's spread, e.g. 3.5")
        p.add_argument("--days", type=float, default=5)
    sub.add_parser("report")
    args = ap.parse_args()
    {"record": record, "grade": grade, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
