#!/usr/bin/env python3
"""Does media (YouTube touts, the crowd, DFS buzz) add to the pick'em edge?

Adam's question, 2026-10-11: "backtest using NFL buzz, Jeff Mans podcast and
other media to see if I can improve my edge."

    python3 scripts/pickem_media_backtest.py collect --season 2026 --weeks 1-5
    python3 scripts/pickem_media_backtest.py collect --season 2025 --weeks 1-18 \
        --channels-from 2026
    python3 scripts/pickem_media_backtest.py evaluate

COLLECT
For each week it searches YouTube for that week's slate videos ("NFL week 3
picks against the spread 2025" and three variants), keeps those whose title
names the week and which aired in the days before it, and pulls each one's
transcript through the free get_panel route (packages/transcripts). Every
game's spread call is read with scripts/tout_picks.extract_pick -- the same
reader the per-game tout grader uses: a pick needs a pick verb ("give me",
"I'll take"...), a spread word (plus/minus/cover/points) and a team named
after the verb, and is rejected if the sentence is about a total, a prop or a
parlay. Only words spoken before the game's pick deadline count
(edge.pickem_deadline, rule "earliest"), so a live show's post-lock chatter is
never used. Writes data/media_ats_picks.csv (public videos, public picks).

EVALUATE
  * every source graded against the CLOSING line (nflverse spread_line), both
    seasons -- the bar any tout has to clear before it could help a pick;
  * the tout CONSENSUS per game (share of channels on the home side),
    followed and faded;
  * 2026 only: the same, graded against CBS's frozen line; CBS's national
    community pick % (the "public"); team-level DFS buzz (data/buzz_nfl.csv)
    as a hype signal; and each signal as the tiebreak on the shipped model's
    COIN FLIP games, where the model itself has no edge to lose.

THE BAR, set before looking (PICKEM_MODEL.md section 3): 2025 is the dev
season, 2026 weeks 1-5 the check. A signal is only interesting if it beats 50%
against the closing line in BOTH, with an interval that clears 50% somewhere.
Anything sliced into subgroups also has to clear the 5i selection-bias null.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import math
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "transcripts"))

from edge.pickem import ats_result, make_pick  # noqa: E402
from edge.pickem_deadline import EARLIEST, game_deadlines  # noqa: E402
from edge.pickem_week import EASTERN  # noqa: E402
from scripts.tout_picks import extract_pick, team_aliases  # noqa: E402

PICKS = ROOT / "data" / "media_ats_picks.csv"
CACHE = ROOT / "data" / "cache" / "buzz"
GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
FIELDS = ["season", "week", "away", "home", "video_id", "channel_id", "channel",
          "views", "aired", "title", "pick", "quote"]
QUERIES = ("NFL week {w} picks against the spread {s}", "NFL week {w} picks {s}",
           "NFL week {w} best bets {s}", "week {w} NFL predictions every game {s}")
#: tout_picks' alias table speaks edge.nfl's names; nflverse spells the Rams LA.
TO_NFLVERSE = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX"}


def nflverse_games() -> list[dict]:
    cache = ROOT / "data" / "cache" / "nflverse_games.csv"
    if cache.exists() and datetime.datetime.now().timestamp() - cache.stat().st_mtime < 86400:
        text = cache.read_text()
    else:
        text = urllib.request.urlopen(GAMES_URL, timeout=120).read().decode()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text)
    return [r for r in csv.DictReader(io.StringIO(text)) if r["game_type"] == "REG"]


def kickoff(r: dict) -> datetime.datetime:
    local = datetime.datetime.strptime(f"{r['gameday']} {r['gametime']}", "%Y-%m-%d %H:%M")
    return local.replace(tzinfo=EASTERN).astimezone(datetime.timezone.utc)


def week_games(season: int, week: int, games: list[dict]) -> list[dict]:
    rows = [r for r in games if r["season"] == str(season) and r["week"] == str(week)]
    kicks = {r["home_team"]: kickoff(r) for r in rows}
    dl = game_deadlines(kicks, EARLIEST)
    for r in rows:
        r["kick"], r["deadline"] = kicks[r["home_team"]], dl[r["home_team"]]
    return rows


def _chunks(text: str) -> str:
    """Re-punctuate run-on caption text: a 300-word "sentence" is rejected by
    extract_pick's prop/total filter as soon as anything in it says "over"."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text):
        w = s.split()
        if len(w) <= 60:
            out.append(s)
        else:
            out += [" ".join(w[i:i + 30]) + "." for i in range(0, len(w), 30)]
    return " ".join(out)


_UNIT_DAYS = {"second": 0, "minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30,
              "year": 365}


def _age_days(published: str) -> int | None:
    """YouTube's relative "3 weeks ago" / "Streamed 1 year ago", in days."""
    m = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", published or "")
    return int(m.group(1)) * _UNIT_DAYS[m.group(2)] if m else None


def _channels_from(season: int, min_picks: int) -> list[tuple[str, str]]:
    """[(channel_id, name)] of channels with >= min_picks spread picks on file
    for `season` -- the sources worth chasing back into an older season."""
    counts: dict[tuple, int] = defaultdict(int)
    with PICKS.open(newline="") as f:
        for r in csv.DictReader(f):
            if r["season"] == str(season) and r["channel_id"]:
                counts[(r["channel_id"], r["channel"])] += 1
    return [k for k, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n >= min_picks]


def collect(season: int, weeks: list[int], channels_from: int | None = None,
            min_picks: int = 8) -> None:
    """Search YouTube for each week's slate videos and read every game's call.

    Plain searches are dominated by the CURRENT season's uploads -- a 2025
    week found 80-90 titled videos of which 2-17 were from 2025 -- so for an
    older season pass `channels_from=<season>`: each channel that made at
    least `min_picks` calls in that season is searched by name, week by week.
    That also makes a season-to-season persistence test possible, since the
    same channels appear in both.
    """
    from transcripts import Cache, YouTube
    yt = YouTube(Cache(CACHE), pace=1.5)
    aliases = team_aliases()
    games = nflverse_games()
    channels = _channels_from(channels_from, min_picks) if channels_from else []
    keep = []
    if PICKS.exists():
        with PICKS.open(newline="") as f:
            keep = [r for r in csv.DictReader(f)
                    if not (r["season"] == str(season) and int(r["week"]) in weeks)]
    rows = []
    for week in weeks:
        slate = week_games(season, week, games)
        if not slate:
            continue
        first = min(g["kick"] for g in slate)
        last_deadline = max(g["deadline"] for g in slate)
        found: dict = {}
        if channels:
            for cid, name in channels:
                for v in yt.search(f"{name} NFL week {week} {season}", limit=20):
                    if v.get("channel_id") in ("", cid) and \
                            re.search(rf"\bweek\s*{week}\b", v["title"], re.I):
                        found.setdefault(v["id"], v)
        else:
            for q in QUERIES:
                for v in yt.search(q.format(w=week, s=season), limit=40):
                    if re.search(rf"\bweek\s*{week}\b", v["title"], re.I):
                        found.setdefault(v["id"], v)
        # Skip, before paying for a watch-page fetch, anything YouTube's own
        # relative date already places too recently (this season's "week 5"
        # when 2025's is wanted). Generous: "1 year ago" can mean 18 months.
        too_new = (datetime.datetime.now(datetime.timezone.utc) - last_deadline).days // 2
        found = {k: v for k, v in found.items()
                 if (_age_days(v.get("published", "")) or 10 ** 6) >= too_new}
        n_vid = n_pick = 0
        for vid, v in found.items():
            meta = yt.video(vid, v["title"])
            if channels and meta.get("channel_id") not in {c for c, _ in channels}:
                continue
            aired = YouTube.aired(meta)
            if not aired or not (first - datetime.timedelta(days=7) <= aired < last_deadline):
                continue
            segs = yt.transcript(vid, aired)
            if not segs:
                continue
            n_vid += 1
            for g in slate:
                cutoff = (g["deadline"] - aired).total_seconds()
                if cutoff <= 0:
                    continue
                text = _chunks(" ".join(t for s, t in segs if s < cutoff))
                hit = extract_pick(text, g["away_team"], g["home_team"], aliases)
                if not hit:
                    continue
                pick = TO_NFLVERSE.get(hit[0], hit[0])
                if pick not in (g["away_team"], g["home_team"]):
                    continue
                n_pick += 1
                rows.append(dict(season=season, week=week, away=g["away_team"],
                                 home=g["home_team"], video_id=vid,
                                 channel_id=meta.get("channel_id") or "",
                                 channel=meta.get("channel", ""), views=meta.get("views", 0),
                                 aired=aired.isoformat(timespec="minutes"),
                                 title=v["title"][:120], pick=pick, quote=hit[1][:300]))
        print(f"{season} wk{week}: {len(found)} titled videos, {n_vid} in window with "
              f"transcripts, {n_pick} spread picks", flush=True)
        with PICKS.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(keep + rows)


# ---------------------------------------------------------------- evaluate

def wilson(w: int, l: int, z: float = 1.96) -> tuple[float, float]:
    n = w + l
    if n == 0:
        return (0.0, 1.0)
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def rec(results) -> str:
    w = sum(r == "W" for r in results)
    l = sum(r == "L" for r in results)
    if w + l == 0:
        return "   0-0"
    lo, hi = wilson(w, l)
    return f"{w:>4}-{l:<4} {w / (w + l):6.1%}  [{lo:.0%}, {hi:.0%}]"


def evaluate(min_picks: int, share: float) -> None:
    games = {(r["season"], int(r["week"]), r["home_team"]): r for r in nflverse_games()
             if r["home_score"] and r["spread_line"]}
    with PICKS.open(newline="") as f:
        picks = list(csv.DictReader(f))

    def close_home(g):            # nflverse spread_line is the home MARGIN; flip it
        return -float(g["spread_line"])

    # CBS's frozen line and the shipped model's pick, 2026 only
    sys.path.insert(0, str(ROOT / "scripts"))
    from scripts.pickem_grade import readings
    nfl = {"LAR": "LA", "WSH": "WAS"}
    cbs, model = {}, {}
    for r in readings():
        if r["cbs"] is None or r["lock"] is None:
            continue
        key = (str(r["season"]), r["week"], nfl.get(r["home"], r["home"]))
        cbs[key] = r["cbs"]
        model[key] = make_pick(r["away"], r["home"], r["cbs"], r["lock"],
                               r["total_open"], r["total_close"])

    def grade(key, side, line=None):
        g = games[key]
        margin = float(g["home_score"]) - float(g["away_score"])
        return ats_result(margin, close_home(g) if line is None else line, side)

    # one call per (game, channel): a channel's several videos count once (its last)
    per: dict[tuple, dict] = {}
    for p in sorted(picks, key=lambda p: p["aired"]):
        key = (p["season"], int(p["week"]), p["home"])
        if key in games:
            per[(key, p["channel_id"] or p["channel"])] = p
    by_game: dict[tuple, list] = defaultdict(list)
    by_chan: dict[str, list] = defaultdict(list)
    for (key, ch), p in per.items():
        side = "home" if p["pick"] == p["home"] else "away"
        by_game[key].append(side)
        by_chan[p["channel"]].append((key, side))

    seasons = sorted({k[0] for k in by_game})
    print("=" * 78)
    print("MEDIA vs THE SPREAD")
    print("=" * 78)
    for s in seasons:
        n_g = sum(1 for k in by_game if k[0] == s)
        n_p = sum(len(v) for k, v in by_game.items() if k[0] == s)
        print(f"{s}: {n_p} channel-game picks on {n_g} games from "
              f"{len({c for (k, c) in per if k[0] == s})} channels")

    print("\n1. EVERY TOUT PICK vs the CLOSING line (the bar)")
    for s in seasons:
        res = [grade(k, side) for k, sides in by_game.items() if k[0] == s for side in sides]
        print(f"   {s}  {rec(res)}")

    print(f"\n2. TOUT CONSENSUS (games with >= {min_picks} channels; side with >= {share:.0%})")
    for s in seasons:
        follow, fade, fcbs = [], [], []
        for k, sides in by_game.items():
            if k[0] != s or len(sides) < min_picks:
                continue
            h = sides.count("home") / len(sides)
            if max(h, 1 - h) < share:
                continue
            side = "home" if h >= share else "away"
            follow.append(grade(k, side))
            fade.append(grade(k, "away" if side == "home" else "home"))
            if k in cbs:
                fcbs.append(grade(k, side, cbs[k]))
        print(f"   {s}  follow vs close {rec(follow)}   fade {rec(fade)}")
        if fcbs:
            print(f"         follow vs CBS line {rec(fcbs)}")

    print("\n2b. IS 'FADE THE TOUTS' JUST 'TAKE THE UNDERDOG'? (closing line)")
    for s in seasons:
        dogs, fade_on_fav, fade_on_dog = [], [], []
        for k, g in games.items():
            if k[0] != s or close_home(g) == 0:
                continue
            dog = "away" if close_home(g) < 0 else "home"
            dogs.append(grade(k, dog))
            sides = by_game.get(k, [])
            if len(sides) < min_picks:
                continue
            h = sides.count("home") / len(sides)
            if max(h, 1 - h) < share:
                continue
            crowd = "home" if h >= share else "away"
            faded = grade(k, "away" if crowd == "home" else "home")
            (fade_on_fav if crowd != dog else fade_on_dog).append(faded)
        print(f"   {s}  every underdog, all games  {rec(dogs)}")
        print(f"         fade when touts on the FAVOURITE (= take the dog) {rec(fade_on_fav)}")
        print(f"         fade when touts on the UNDERDOG  (= take the fav) {rec(fade_on_dog)}")

    print(f"\n3. CHANNELS with >= 20 graded picks (vs closing line), by season")
    rows = []
    for ch, items in by_chan.items():
        per_s = {s: [grade(k, side) for k, side in items if k[0] == s] for s in seasons}
        if sum(len(v) for v in per_s.values()) >= 20:
            rows.append((ch, per_s))
    rows.sort(key=lambda r: -sum(x == "W" for v in r[1].values() for x in v)
              / max(1, sum(x in "WL" for v in r[1].values() for x in v)))
    for ch, per_s in rows:
        print(f"   {ch[:30]:<30} " + "  ".join(f"{s}: {rec(v)}" for s, v in per_s.items() if v))
    if len(rows) > 1:
        tops = [r for r in rows if len(r[1].get(seasons[0], [])) >= 20]
        if len(seasons) > 1 and tops:
            print(f"\n   Persistence check: the best {min(5, len(tops))} channels of {seasons[0]} "
                  f"(>= 20 picks), in {seasons[-1]}:")
            best = sorted(tops, key=lambda r: -sum(x == "W" for x in r[1][seasons[0]])
                          / max(1, sum(x in "WL" for x in r[1][seasons[0]])))[:5]
            print(f"     {rec([x for r in best for x in r[1].get(seasons[-1], [])])}")

    if not model:
        return
    s26 = "2026"
    print("\n4. 2026 ONLY -- graded against CBS's frozen line")
    pub, pubf = [], []
    for r in _line_log_public():
        key, home_pct = r
        if key in cbs and key in games:
            side = "home" if home_pct >= 50 else "away"
            pub.append(grade(key, side, cbs[key]))
            pubf.append(grade(key, "away" if side == "home" else "home", cbs[key]))
    print(f"   CBS public majority     {rec(pub)}")
    print(f"   fade the public         {rec(pubf)}")
    hype = _buzz_hype()
    hy = [grade(k, "away" if side == "home" else "home", cbs[k])
          for k, side in hype.items() if k in cbs and k in games]
    print(f"   fade the DFS-buzz team  {rec(hy)}   (team with more player buzz)")

    print("\n5. 2026 COIN FLIPS -- each signal as the tiebreak where the model has no edge")
    cf = [k for k, p in model.items() if p.tier == "COIN FLIP" and k in games]
    base = [grade(k, model[k].side, cbs[k]) for k in cf]
    print(f"   shipped tiebreak        {rec(base)}   (n={len(cf)})")
    tc = []
    for k in cf:
        sides = by_game.get(k, [])
        if sides:
            h = sides.count("home") / len(sides)
            if h != 0.5:
                tc.append(grade(k, "home" if h > 0.5 else "away", cbs[k]))
    print(f"   tout consensus          {rec(tc)}   (games with a tout majority)")
    pubk = dict(_line_log_public())
    fp = [grade(k, "away" if pubk[k] >= 50 else "home", cbs[k]) for k in cf if k in pubk]
    print(f"   fade the public         {rec(fp)}")


def _line_log_public() -> list[tuple]:
    """[((season, week, nflverse home), comm_pct_home)] -- the last reading."""
    from edge.pickem_log import load
    out = {}
    for r in sorted(load(), key=lambda r: r.get("captured_at") or ""):
        if r.get("comm_pct_home"):
            home = {"LAR": "LA", "WSH": "WAS"}.get(r["home_team"], r["home_team"])
            out[(r["season"], int(r["week"]), home)] = float(r["comm_pct_home"])
    return list(out.items())


def _buzz_hype() -> dict:
    """{game key: side whose players got more DFS buzz}, from data/buzz_nfl.csv."""
    path = ROOT / "data" / "buzz_nfl.csv"
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows or "team" not in rows[0]:
        return {}
    games = [r for r in nflverse_games() if r["season"] == "2026"]
    by_date = defaultdict(lambda: defaultdict(float))
    for r in rows:
        by_date[r.get("slate") or r.get("date")][r["team"]] += float(r.get("mentions") or 0)
    out = {}
    for g in games:
        for d, teams in by_date.items():
            if d == g["gameday"] and (teams.get(g["home_team"]) or teams.get(g["away_team"])):
                h, a = teams.get(g["home_team"], 0), teams.get(g["away_team"], 0)
                if h != a:
                    out[("2026", int(g["week"]), g["home_team"])] = "home" if h > a else "away"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--season", type=int, required=True)
    c.add_argument("--weeks", required=True, help="'1-18' or '3'")
    c.add_argument("--channels-from", type=int,
                   help="search, by name, every channel with >= --min-picks calls in this "
                        "season (use for an older season: plain search returns this year's)")
    c.add_argument("--min-picks", type=int, default=8)
    e = sub.add_parser("evaluate")
    e.add_argument("--min-picks", type=int, default=3)
    e.add_argument("--share", type=float, default=0.6)
    args = ap.parse_args()
    if args.cmd == "collect":
        a, _, b = args.weeks.partition("-")
        collect(args.season, list(range(int(a), int(b or a) + 1)),
                args.channels_from, args.min_picks)
    else:
        evaluate(args.min_picks, args.share)


if __name__ == "__main__":
    main()
