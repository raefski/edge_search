"""As-of-week team efficiency ratings (DVOA-analog) + coach history features.

THE ANTI-LEAKAGE CONTRACT, which is the whole point of this module:
every function here takes an (season, week) "as-of" point and may only use
games that finished STRICTLY BEFORE it. A game in season S week W sees
season S weeks 1..W-1 plus earlier seasons -- never week W itself, never a
season-end total. `build_ratings_timeline` enforces this structurally by
computing each week's ratings from an accumulator that has not yet been
shown that week's games, so a leak would require rewriting the loop rather
than merely passing a wrong argument.

WHY AN ANALOG AND NOT REAL DVOA: DVOA is proprietary (FTN, formerly
Football Outsiders); its historical archive is subscriber-only, with no
free API, so it cannot be used in a reproducible free pipeline. What's
here shares DVOA's two core ideas -- efficiency measured against a
league-average baseline, then adjusted for opponent quality -- but is
computed from nflverse EPA (data/pbp_team_game.csv). It is NOT DVOA, does
not reproduce DVOA's numbers, and should never be described as DVOA.
Differences that matter: no situational weighting for down/distance beyond
what EPA already embeds, no special-teams component, no explicit
strength-of-schedule reweighting across the season, and garbage-time
handling is a crude win-probability filter rather than DVOA's own scheme.

Stdlib only, matching this repo's zero-dep core.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PBP_TEAM_GAME = ROOT / "data" / "pbp_team_game.csv"
GAMES_CSV = ROOT / "data" / "nflverse_games.csv"

# Tuned on the DEV split only (2014-2022); see PICKEM_MODEL.md.
PRIOR_WEIGHT = 0.35     # weight on last season's games vs. this season's
SHRINK_PLAYS = 400      # ~6 games; ratings shrink toward league mean below this
ADJ_ITERS = 2           # opponent-adjustment passes


def load_team_games(path: Path | str = PBP_TEAM_GAME) -> list[dict]:
    out = []
    with Path(path).open() as f:
        for r in csv.DictReader(f):
            out.append({
                "season": int(r["season"]), "week": int(r["week"]),
                "game_id": r["game_id"], "team": r["team"], "opp": r["opp"],
                "is_home": int(r["is_home"]),
                "plays": int(r["off_plays"]), "epa": float(r["off_epa"]),
                "pass_plays": int(r["off_pass_plays"]), "pass_epa": float(r["off_pass_epa"]),
                "rush_plays": int(r["off_rush_plays"]), "rush_epa": float(r["off_rush_epa"]),
                "success": int(r["off_success"]),
                "plays_gt": int(r["off_plays_gt"]), "epa_gt": float(r["off_epa_gt"]),
            })
    return out


def _weighted(cur, prior, w):
    """Combine current-season and prior-season (sum, n) pairs."""
    s = cur[0] + w * prior[0]
    n = cur[1] + w * prior[1]
    return s, n


def build_ratings_timeline(team_games: list[dict], prior_weight: float = PRIOR_WEIGHT,
                           shrink_plays: int = SHRINK_PLAYS, iters: int = ADJ_ITERS,
                           garbage_time: bool = False) -> dict:
    """dict[(season, week)] -> {team: {'off','def','n'}}, each entry being
    what was knowable BEFORE that week kicked off.

    off/def are opponent-adjusted EPA per play relative to league average.
    off > 0 is a good offense; def < 0 is a good defense (fewer EPA allowed).
    """
    epa_key, play_key = ("epa_gt", "plays_gt") if garbage_time else ("epa", "plays")

    by_season = defaultdict(list)
    for g in team_games:
        by_season[g["season"]].append(g)
    seasons = sorted(by_season)

    timeline: dict = {}
    # prior[season] holds the PREVIOUS season's completed accumulator
    prior_off: dict = {}
    prior_def: dict = {}

    for s in seasons:
        weeks = sorted({g["week"] for g in by_season[s]})
        cur_off: dict = defaultdict(lambda: [0.0, 0.0])   # team -> [epa, plays]
        cur_def: dict = defaultdict(lambda: [0.0, 0.0])
        cur_opps: dict = defaultdict(list)                # team -> [(opp, epa, plays, side)]

        for w in weeks:
            # --- snapshot BEFORE adding week w's games -------------------
            timeline[(s, w)] = _compute_ratings(
                cur_off, cur_def, cur_opps, prior_off, prior_def,
                prior_weight, shrink_plays, iters)

            # --- now fold week w in, for the benefit of later weeks ------
            for g in by_season[s]:
                if g["week"] != w:
                    continue
                e, p = g[epa_key], g[play_key]
                if p <= 0:
                    continue
                cur_off[g["team"]][0] += e
                cur_off[g["team"]][1] += p
                cur_def[g["opp"]][0] += e
                cur_def[g["opp"]][1] += p
                cur_opps[g["team"]].append((g["opp"], e, p, "off"))
                cur_opps[g["opp"]].append((g["team"], e, p, "def"))

        # season over -> becomes next season's prior
        prior_off = {t: list(v) for t, v in cur_off.items()}
        prior_def = {t: list(v) for t, v in cur_def.items()}

    return timeline


def _compute_ratings(cur_off, cur_def, cur_opps, prior_off, prior_def,
                     prior_weight, shrink_plays, iters) -> dict:
    teams = set(cur_off) | set(cur_def) | set(prior_off) | set(prior_def)
    if not teams:
        return {}

    tot_epa = tot_plays = 0.0
    combined_off, combined_def = {}, {}
    for t in teams:
        co = _weighted(cur_off.get(t, [0.0, 0.0]), prior_off.get(t, [0.0, 0.0]), prior_weight)
        cd = _weighted(cur_def.get(t, [0.0, 0.0]), prior_def.get(t, [0.0, 0.0]), prior_weight)
        combined_off[t], combined_def[t] = co, cd
        tot_epa += co[0]
        tot_plays += co[1]
    league = (tot_epa / tot_plays) if tot_plays else 0.0

    def raw(pair):
        s, n = pair
        if n <= 0:
            return 0.0, 0.0
        rate = s / n - league
        return rate * (n / (n + shrink_plays)), n

    off = {t: raw(combined_off[t])[0] for t in teams}
    dfn = {t: raw(combined_def[t])[0] for t in teams}
    nplays = {t: combined_off[t][1] for t in teams}

    # Opponent adjustment: a team's offence is credited for facing good
    # defences and debited for facing bad ones (and vice versa). Iterated a
    # couple of times so the adjustment itself accounts for opponent quality.
    for _ in range(iters):
        new_off, new_def = {}, {}
        for t in teams:
            o_num = o_den = d_num = d_den = 0.0
            for opp, e, p, side in cur_opps.get(t, []):
                if p <= 0:
                    continue
                rate = e / p - league
                if side == "off":
                    o_num += (rate - dfn.get(opp, 0.0)) * p
                    o_den += p
                else:
                    d_num += (rate - off.get(opp, 0.0)) * p
                    d_den += p
            # fall back to the unadjusted (prior-informed) value when a team
            # has no current-season opponents yet -- i.e. week 1.
            new_off[t] = (o_num / o_den) * (o_den / (o_den + shrink_plays)) if o_den else off[t]
            new_def[t] = (d_num / d_den) * (d_den / (d_den + shrink_plays)) if d_den else dfn[t]
        off, dfn = new_off, new_def

    return {t: {"off": off[t], "def": dfn[t], "n": nplays[t]} for t in teams}


def net_rating(ratings: dict, home: str, away: str) -> float | None:
    """Home-minus-away net efficiency, EPA/play. None if either side unrated."""
    h, a = ratings.get(home), ratings.get(away)
    if not h or not a:
        return None
    return (h["off"] - h["def"]) - (a["off"] - a["def"])


# ---------------------------------------------------------------------------
# Coach features
# ---------------------------------------------------------------------------

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"


def load_games(path: Path | str = GAMES_CSV) -> list[dict]:
    """Schedule + coach history. Auto-downloads on first use: the file is a
    2MB raw mirror of a live nflverse source, so it is gitignored rather
    than committed (regenerable for free, same call as the other nflverse
    ground-truth files in .gitignore)."""
    path = Path(path)
    if not path.exists():
        import urllib.request
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(urllib.request.urlopen(GAMES_URL, timeout=120).read())
    out = []
    with path.open() as f:
        for r in csv.DictReader(f):
            if r.get("game_type") != "REG":
                continue
            if not r.get("home_score") or not r.get("away_score"):
                continue
            out.append(r)
    return out


def build_coach_timeline(games: list[dict], spread_of) -> dict:
    """dict[(season, week)] -> {coach: {...as-of career ATS record...}}.

    `spread_of(game_row)` returns that game's home-team spread, or None to
    skip it (games with no line contribute experience but not ATS record).
    Same strictly-before contract as the ratings timeline: a week's entry is
    snapshotted before that week's results are folded in.
    """
    rows = sorted(games, key=lambda r: (int(r["season"]), int(r["week"])))
    acc: dict = defaultdict(lambda: {
        "g": 0, "ats_w": 0, "ats_l": 0,
        "dog_w": 0, "dog_l": 0, "fav_w": 0, "fav_l": 0,
        "first_season": None,
    })
    timeline: dict = {}
    seen = set()

    for r in rows:
        s, w = int(r["season"]), int(r["week"])
        if (s, w) not in seen:
            seen.add((s, w))
            timeline[(s, w)] = {c: dict(v) for c, v in acc.items()}

        line = spread_of(r)
        margin = int(r["home_score"]) - int(r["away_score"])
        for coach, is_home in ((r.get("home_coach"), True), (r.get("away_coach"), False)):
            if not coach:
                continue
            a = acc[coach]
            a["g"] += 1
            if a["first_season"] is None:
                a["first_season"] = s
            if line is None:
                continue
            # cover from this coach's perspective
            v = margin + line
            if v == 0:
                continue
            covered = (v > 0) if is_home else (v < 0)
            # favourite/underdog from this coach's perspective
            is_dog = (line > 0) if is_home else (line < 0)
            a["ats_w" if covered else "ats_l"] += 1
            if is_dog:
                a["dog_w" if covered else "dog_l"] += 1
            else:
                a["fav_w" if covered else "fav_l"] += 1

    return timeline


def coach_ats_rate(entry: dict | None, split: str = "all", shrink: int = 50) -> float:
    """Shrunk ATS cover rate for a coach, centred on 0 (i.e. rate - 0.5).

    Heavy default shrinkage (50 games) because coach ATS records are mostly
    noise -- see PICKEM_MODEL.md's killed-signals section.
    """
    if not entry:
        return 0.0
    w, l = {
        "all": (entry["ats_w"], entry["ats_l"]),
        "dog": (entry["dog_w"], entry["dog_l"]),
        "fav": (entry["fav_w"], entry["fav_l"]),
    }[split]
    n = w + l
    if n == 0:
        return 0.0
    return (w / n - 0.5) * (n / (n + shrink))


# nflverse play-by-play normalises relocated franchises to their CURRENT
# codes, while the schedule/odds history preserves the code in use at the
# time. Without this the three relocated teams silently go unrated.
RELOCATED = {"OAK": "LV", "SD": "LAC", "STL": "LA"}


def canon(team: str) -> str:
    return RELOCATED.get(team, team)
