#!/usr/bin/env python3
"""Fit every measured constant edge/dfs_ncaaf_theory.py ships, from cfbfastR.

    python3 scripts/ncaaf_fit.py                      # everything, 2023-2025
    python3 scripts/ncaaf_fit.py --what td
    python3 scripts/ncaaf_fit.py --what sd corr --seasons 2024 2025

WHY ONE SCRIPT AND NOT FOUR
The NFL build has scripts/nfl_td_fit.py, nfl_sigma_fit.py, nfl_variance_fit.py
and nfl_correlation.py as separate files, and for the NFL that is right: they
read different sources (nflverse player-weeks, a 13,690-quote prop database,
and a stored projection log). Every fit here reads exactly ONE thing -- the
per-player-game box scores edge/ncaaf.boxscores builds -- and that source is
three 60MB CSVs that take two minutes to parse. Splitting the script would mean
parsing them four times to produce four numbers that all come from the same
table. `--what` keeps them individually runnable.

WHAT IS FITTED

  td    Touchdowns per yard, by position and by route to the end zone.
        DraftKings posts a PASSING touchdown ladder for college, so that one
        is priced and needs no rate. Rushing and receiving touchdowns have no
        two-sided market at any book -- only "Anytime TD", which is a field
        with no opposing side -- so they are imputed from yardage exactly the
        way edge/dfs_sport.py does it for the NFL.

        THE SAME METHODOLOGICAL TRAP APPLIES AND IS AVOIDED THE SAME WAY.
        Regressing touchdowns on REALIZED yards is wrong in a direction that
        flatters the low end, because a 5-yard touchdown catch IS 5 receiving
        yards and so the touchdown inflates its own predictor. The rate is
        multiplied by an EXPECTED mean in use, so it is fitted against one: a
        leave-one-out season mean per player-season.

  sd    sd(actual DK points) ~ a + b * expected points, per position. This is
        what makes the cash and GPP objectives opposites -- see
        edge/dfs_ncaaf_theory.py.

        HONEST DIFFERENCE FROM THE NFL FIT, WHICH USED REAL PROJECTIONS:
        scripts/nfl_variance_fit.py regressed against 9,979 stored leak-free
        model projections. Nothing has ever projected a college slate, so there
        is no such log and cannot be one until this app has run for a season.
        The stand-in is the same leave-one-out season mean the touchdown fit
        uses. That is a WORSE predictor than a real projection -- it knows
        nothing about the opponent -- so it leaves more variance unexplained
        and these sd values are, if anything, biased HIGH. Re-fit from
        data/dfs_proj_log_ncaaf.csv once a season of forward tests exists.

  corr  Pearson r between two players' DK totals in the same game, by the
        position pair and by whether they are team-mates. The GPP stack and
        the cash spread both fall out of these numbers.
"""
from __future__ import annotations

import argparse
import collections
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import ncaaf                     # noqa: E402

#: A player-season needs this many games before its leave-one-out mean is a
#: usable stand-in for an expected value.
MIN_GAMES = 6
#: And this much production, so the pool being fitted looks like the pool that
#: gets projected. DraftKings posts no ladder for a player nobody uses, and a
#: rate measured over players who never touch the ball is not the rate that
#: gets multiplied by a real projection.
MIN_DK = 4.0


def load(seasons: list[int]) -> list[dict]:
    """Per-player-game rows, with position inferred and season attached."""
    rows = []
    for season in seasons:
        print(f"  loading {season}…", flush=True)
        play_rows = ncaaf.fetch_season(season)
        box = ncaaf.boxscores(play_rows)
        for (gid, player), v in box.items():
            rows.append({"season": season, "game_id": gid, "player": player,
                         "key": ncaaf.norm(player), "team": v.get("team"),
                         "opponent": v.get("opponent"),
                         "pass_yds": v.get("pass_yds", 0.0),
                         "pass_td": v.get("pass_td", 0.0),
                         "pass_att": v.get("pass_att", 0.0),
                         "int": v.get("int", 0.0),
                         "rush_yds": v.get("rush_yds", 0.0),
                         "rush_td": v.get("rush_td", 0.0),
                         "rec": v.get("rec", 0.0),
                         "rec_yds": v.get("rec_yds", 0.0),
                         "rec_td": v.get("rec_td", 0.0),
                         "dk": v.get("dk", 0.0)})
    return rows


def infer_position(games: list[dict]) -> str:
    """QB / RB / WR from what a player actually did over a season.

    cfbfastR's play feed carries no position column, and DraftKings' own
    position is only available for players on a current slate -- which is
    nobody, historically. Usage is the only signal, and it is a good one:
    passing identifies a quarterback unambiguously because nobody else throws
    at volume, and after that the split is carries against catches.

    DraftKings lists college tight ends as WR, so this returns three positions
    and not four. Matching DK's own labelling is the point: a constant fitted
    under a different position taxonomy than the one it is looked up by is
    wrong in a way nothing downstream can see.
    """
    att = sum(g["pass_att"] for g in games)
    rush = sum(g["rush_yds"] for g in games)
    rec = sum(g["rec"] for g in games)
    if att >= 5 * len(games):
        return "QB"
    if rush >= 20 * len(games) and rush > 2.5 * (rec * 10):
        return "RB"
    return "WR" if rec else ("RB" if rush else "WR")


def player_seasons(rows: list[dict]) -> dict:
    """{(key, season): {games, position}} for players with enough of a sample."""
    by: dict = collections.defaultdict(list)
    for r in rows:
        by[(r["key"], r["season"])].append(r)
    out = {}
    for k, games in by.items():
        if len(games) < MIN_GAMES:
            continue
        if statistics.fmean(g["dk"] for g in games) < MIN_DK:
            continue
        out[k] = {"games": games, "position": infer_position(games)}
    return out


def loo_mean(games: list[dict], field: str, skip: dict) -> float:
    """Season mean of `field` EXCLUDING this game. The leak-free predictor."""
    vals = [g[field] for g in games if g is not skip]
    return statistics.fmean(vals) if vals else 0.0


# ---------------------------------------------------------------------------
# td
# ---------------------------------------------------------------------------
def fit_td(ps: dict) -> None:
    """Yards per touchdown, by position and route, with a bootstrap CI."""
    buckets: dict = collections.defaultdict(lambda: [0.0, 0.0])   # [yards, tds]
    samples: dict = collections.defaultdict(list)
    for (_key, _season), info in ps.items():
        pos, games = info["position"], info["games"]
        for g in games:
            exp_rush = loo_mean(games, "rush_yds", g)
            exp_rec = loo_mean(games, "rec_yds", g)
            # Only where the player is actually used that way -- the pool that
            # gets projected is the pool with a posted ladder.
            if exp_rush >= 20:
                tag = "rush_QB" if pos == "QB" else "rush_OTHER"
                buckets[tag][0] += exp_rush
                buckets[tag][1] += g["rush_td"]
                samples[tag].append((exp_rush, g["rush_td"]))
            if exp_rec >= 20:
                tag = "rec_RB" if pos == "RB" else "rec_WR"
                buckets[tag][0] += exp_rec
                buckets[tag][1] += g["rec_td"]
                samples[tag].append((exp_rec, g["rec_td"]))

    print("\nTOUCHDOWN RATES  (yards per touchdown; lower = scores more often)")
    print(f"{'route':<14}{'n':>8}{'yds/TD':>10}{'95% CI':>20}   TDs per yard")
    for tag in ("rush_QB", "rush_OTHER", "rec_WR", "rec_RB"):
        yards, tds = buckets[tag]
        if tds < 20:
            print(f"{tag:<14}{len(samples[tag]):>8}   too few touchdowns")
            continue
        rate = yards / tds
        lo, hi = _bootstrap_rate(samples[tag])
        print(f"{tag:<14}{len(samples[tag]):>8}{rate:>10.1f}"
              f"{f'({lo:.0f}-{hi:.0f})':>20}   1/{rate:.1f}")

    print("\nFLATNESS CHECK -- is the rate proportional, or is there a red-zone "
          "non-linearity?")
    print(f"{'route':<14}" + "".join(f"{f'>={f}':>10}" for f in (0, 20, 40, 60, 80)))
    for tag in ("rush_QB", "rush_OTHER", "rec_WR", "rec_RB"):
        cells = []
        for floor in (0, 20, 40, 60, 80):
            sel = [(y, t) for y, t in samples[tag] if y >= floor]
            tot_t = sum(t for _, t in sel)
            cells.append(f"{(sum(y for y, _ in sel) / tot_t):>10.0f}"
                         if tot_t >= 20 else f"{'-':>10}")
        print(f"{tag:<14}" + "".join(cells))
    print("A flat row means the proportional model is right and no intercept "
          "is defensible.")


def _bootstrap_rate(sample: list[tuple[float, float]], n: int = 400):
    import random
    rng = random.Random(0)
    out = []
    for _ in range(n):
        picks = [sample[rng.randrange(len(sample))] for _ in range(len(sample))]
        tds = sum(t for _, t in picks)
        if tds > 0:
            out.append(sum(y for y, _ in picks) / tds)
    out.sort()
    return (out[int(0.025 * len(out))], out[int(0.975 * len(out))]) if out else (0, 0)


# ---------------------------------------------------------------------------
# sd
# ---------------------------------------------------------------------------
def fit_sd(ps: dict) -> None:
    """sd(actual DK) ~ a + b * expected DK, per position, by binned regression."""
    pairs: dict = collections.defaultdict(list)
    for (_key, _season), info in ps.items():
        pos, games = info["position"], info["games"]
        for g in games:
            pairs[pos].append((loo_mean(games, "dk", g), g["dk"]))

    print("\nSPREAD  sd(actual DK points) ~ a + b * expected")
    print(f"{'pos':<6}{'n':>8}{'a':>9}{'b':>9}{'sd@10':>9}{'sd@20':>9}{'CV@20':>9}")
    for pos in ("QB", "RB", "WR"):
        rows = pairs[pos]
        if len(rows) < 200:
            print(f"{pos:<6}{len(rows):>8}   too few")
            continue
        # Bin on the predictor, take the sd of actuals in each bin, then
        # regress those sds. Regressing |residual| directly would fit the mean
        # absolute deviation, which is not the sd and is ~0.8 of it.
        bins: dict = collections.defaultdict(list)
        for exp, act in rows:
            bins[min(12, int(exp // 2.5))].append(act)
        xs, ys = [], []
        for b, acts in sorted(bins.items()):
            if len(acts) < 40:
                continue
            xs.append(statistics.fmean([e for e, a in rows
                                        if min(12, int(e // 2.5)) == b]))
            ys.append(statistics.pstdev(acts))
        if len(xs) < 4:
            print(f"{pos:<6}{len(rows):>8}   too few bins")
            continue
        b_, a_ = _ols(xs, ys)
        print(f"{pos:<6}{len(rows):>8}{a_:>9.3f}{b_:>9.4f}"
              f"{a_ + 10 * b_:>9.2f}{a_ + 20 * b_:>9.2f}{(a_ + 20 * b_) / 20:>9.3f}")
    print("CV is the coefficient of variation at a 20-point projection. The "
          "position with the\nLOWEST CV is the one a floor-maximising cash "
          "lineup should pay up at.")


def _ols(xs, ys):
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    return slope, my - slope * mx


# ---------------------------------------------------------------------------
# corr
# ---------------------------------------------------------------------------
def fit_corr(rows: list[dict], ps: dict) -> None:
    """Pearson r between DK totals of two players in the same game."""
    pos_of = {key: info["position"] for (key, _s), info in ps.items()}
    seasons_of = {(key, s) for (key, s) in ps}

    by_game: dict = collections.defaultdict(list)
    for r in rows:
        if (r["key"], r["season"]) not in seasons_of:
            continue
        by_game[r["game_id"]].append(r)

    # Each player's own mean is removed so the correlation is between
    # DEVIATIONS -- otherwise the numbers mostly measure that good players
    # score more than bad ones, in every pairing equally.
    mean_dk = {}
    for (key, season), info in ps.items():
        mean_dk[(key, season)] = statistics.fmean(g["dk"] for g in info["games"])

    pairs: dict = collections.defaultdict(list)
    for game, players in by_game.items():
        if len(players) < 2:
            continue
        for i, a in enumerate(players):
            for b in players[i + 1:]:
                pa, pb = pos_of.get(a["key"]), pos_of.get(b["key"])
                if not pa or not pb:
                    continue
                same = a["team"] == b["team"]
                tag = _pair_tag(pa, pb, same)
                if tag is None:
                    continue
                da = a["dk"] - mean_dk[(a["key"], a["season"])]
                db = b["dk"] - mean_dk[(b["key"], b["season"])]
                pairs[tag].append((da, db))

    print("\nCORRELATION  Pearson r of DK-point deviations, same game")
    print(f"{'pair':<26}{'n':>9}{'r':>9}")
    for tag in sorted(pairs, key=lambda t: -abs(_pearson(pairs[t]) or 0)):
        vals = pairs[tag]
        if len(vals) < 300:
            continue
        r = _pearson(vals)
        print(f"{tag:<26}{len(vals):>9}{r:>9.3f}")
    print("\nA positive same-team QB pairing is the stack. A negative "
          "team-mate pairing at the\nsame position is two players sharing one "
          "ball. Both should appear without being\nasked for -- if they do "
          "not, suspect the position inference before the sport.")


def _pair_tag(pa: str, pb: str, same_team: bool) -> str | None:
    a, b = sorted((pa, pb))
    where = "own" if same_team else "opp"
    return f"{a}-{b} ({where})"


def _pearson(vals: list[tuple[float, float]]) -> float | None:
    if len(vals) < 3:
        return None
    xs = [x for x, _ in vals]
    ys = [y for _, y in vals]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in vals)
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025])
    ap.add_argument("--what", nargs="+", default=["td", "sd", "corr"],
                    choices=["td", "sd", "corr"])
    args = ap.parse_args()

    print(f"cfbfastR seasons {args.seasons}")
    rows = load(args.seasons)
    ps = player_seasons(rows)
    counts = collections.Counter(v["position"] for v in ps.values())
    print(f"  {len(rows)} player-games -> {len(ps)} player-seasons with "
          f"{MIN_GAMES}+ games and {MIN_DK}+ DK avg")
    print(f"  positions: {dict(counts)}")

    if "td" in args.what:
        fit_td(ps)
    if "sd" in args.what:
        fit_sd(ps)
    if "corr" in args.what:
        fit_corr(rows, ps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
