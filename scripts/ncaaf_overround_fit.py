#!/usr/bin/env python3
"""Fit the per-rung overround on DraftKings' NCAAF milestone ladders.

    python3 scripts/ncaaf_overround_fit.py              # scrape, fit, report
    python3 scripts/ncaaf_overround_fit.py --report     # re-fit stored pairs only
    python3 scripts/ncaaf_overround_fit.py --apply      # print the constants block

WHY THIS NUMBER NEEDS FITTING AT ALL
edge/dfs_ladder.py's docstring makes the argument in full; the short version is
that a one-sided rung has no partner to devig against, and a LADDER cannot
devig itself either -- its bucket masses telescope to exactly 1 by algebra
whatever the prices are, which dumps the entire overround into the one bucket
nobody priced, [0, lowest rung). Left uncorrected every yardage projection is
biased high through its head.

WHERE THE ANCHOR COMES FROM
FanDuel posts a genuine two-sided Over/Under on the same college players that
DraftKings posts ladders for -- `PLAYER_MEDIUM_RECEIVING_YARDS_CFB`, "Over"
and "Under" at one handicap. A two-sided pair devigs itself, so it yields a
clean P(X > L). Interpolating DraftKings' RAW (un-devigged) ladder at that same
L gives q_dk(L), and

    overround = q_dk(L) / p_fd(L) - 1

is a direct reading of the excess on one DraftKings rung.

THE THING THAT MAKES THIS HARDER THAN IT LOOKS, AND IT IS NOT A DETAIL
FanDuel prices nearly every college main line at -114/-114 (decimal 1.877 both
ways). Devigged that is P(over) = 0.500 for almost every player, which means
FanDuel is telling us the MEDIAN and nothing else. That is still exactly what
is needed -- it pins one point of the true survival curve at a known
probability -- but it does mean this fit has one degree of freedom per player,
not a curve per player, and that a market where FanDuel's price is genuinely
lopsided is worth more than ten where it is not. The report separates them.

WHAT THIS IS NOT
It is a CROSS-BOOK ANCHOR, not ground truth. It assumes FanDuel's devigged
median is unbiased, which is an assumption about a second book rather than
about reality. The forward test -- logged ladders joined against what actually
happened, scripts/ncaaf_calibration.py -- is the real check, and when the two
disagree the forward test wins. This exists because it can be run TODAY and the
forward test needs weeks.

COVERAGE WARNING, measured 2026-09-24: FanDuel posts college player props only
about 24-48 hours before kickoff. On a Thursday, one game on the coming
Saturday slate had them and eleven did not. Run this on a Friday or Saturday,
and run it repeatedly -- pairs accumulate in data/ncaaf_overround_pairs.json
across runs and the fit is over everything collected, not just today.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs_ladder as L          # noqa: E402
from edge.names import norm               # noqa: E402
from edge.oddsmath import devig           # noqa: E402

PAIRS_PATH = ROOT / "data" / "ncaaf_overround_pairs.json"
SPORT = "americanfootball_ncaaf"

#: DraftKings (categoryId, subcategoryId) -> canonical market.
#: Verified live 2026-09-24 with scripts/dk_categories.py; NCAAF happens to
#: share the NFL's category ids, which is worth having CHECKED rather than
#: assumed -- edge/arb/draftkings_league.PROP_CATEGORIES records what it cost
#: the NFL build to guess them.
DK_SUBS = {
    (1000, 16569): "player_pass_yds",
    (1000, 16568): "player_pass_tds",
    (1001, 16571): "player_rush_yds",
    (1342, 16570): "player_reception_yds",
    (1342, 16821): "player_receptions",
}

#: FanDuel marketType -> canonical market. The tier word (LOW/MEDIUM/HIGH) is
#: FanDuel's own grouping of players and carries no meaning here, so it is
#: matched as a wildcard. ALT markets are EXCLUDED deliberately: an alternate
#: ladder is one-sided and carries its own unknown overround, which is the very
#: quantity being measured -- using it would be circular.
FD_MARKETS = {
    "PASSING_YARDS": "player_pass_yds",
    "PASSING_TOUCHDOWNS": "player_pass_tds",
    "RUSHING_YARDS": "player_rush_yds",
    "RECEIVING_YARDS": "player_reception_yds",
    "RECEPTIONS": "player_receptions",
}
FD_TABS = ("passing-props", "rushing-props", "receiving-props")
FD_RE = re.compile(r"^PLAYER_(?:LOW|MEDIUM|HIGH)_(" +
                   "|".join(FD_MARKETS) + r")_CFB$")

#: A FanDuel pair this lopsided is either a stale quote or a market about to be
#: pulled. Both sides must also be present, which excludes a suspended side.
MAX_PAIR_SKEW = 6.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# DraftKings: the ladders
# ---------------------------------------------------------------------------
def dk_ladders(gap: float = 0.4) -> dict:
    """{market: {player: [(threshold, decimal), ...]}} from DraftKings.

    One request per subcategory covers the WHOLE league -- DraftKings serves
    these at /leagues/{id}/categories/{c}/subcategories/{s}, not per event --
    so this is five requests for every college game on the board. That is the
    reason NCAAF prop collection is cheap enough to run often, and it is worth
    knowing before anyone writes a per-event loop for it.
    """
    from edge.arb.draftkings_league import DraftKingsLeague
    dk = DraftKingsLeague()
    out: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for (cat, sub), market in DK_SUBS.items():
        try:
            payload = dk.fetch_subcategory(SPORT, cat, sub)
        except Exception as exc:                            # noqa: BLE001
            print(f"  ! {market}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        markets = {m["id"]: m for m in payload.get("markets") or []}
        for sel in payload.get("selections") or []:
            players = [p for p in (sel.get("participants") or [])
                       if p.get("type") == "Player"]
            # A "Combined" or "Either Player" market names two people and is a
            # different bet; one participant is the discriminator.
            if len(players) != 1:
                continue
            meta = markets.get(sel.get("marketId")) or {}
            if "Milestones" not in ((meta.get("marketType") or {}).get("name") or ""):
                continue
            value = sel.get("milestoneValue")
            price = (sel.get("displayOdds") or {}).get("decimal")
            if value is None or not price:
                continue
            name = re.sub(r"\s*\([^)]*\)\s*$", "", players[0].get("name") or "")
            out[market][norm(name)].append((float(value), float(price)))
        time.sleep(gap)
    return {m: {p: L._dedupe(r) for p, r in players.items()}
            for m, players in out.items()}


# ---------------------------------------------------------------------------
# FanDuel: the two-sided anchors
# ---------------------------------------------------------------------------
def fd_lines(max_events: int = 40, gap: float = 0.4) -> dict:
    """{market: {player: (line, p_over_devigged, skew)}} from FanDuel."""
    from edge.arb.fanduel import FanDuelScrape
    fd = FanDuelScrape()
    try:
        page = fd.league_page(SPORT)
        events = fd.list_events(SPORT, page)
    except Exception as exc:                                # noqa: BLE001
        print(f"  ! FanDuel league page: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {}

    out: dict = collections.defaultdict(dict)
    for eid, name, _when in events[:max_events]:
        for tab in FD_TABS:
            try:
                payload = fd.event_markets(eid, tab)
            except Exception:                               # noqa: BLE001
                continue
            markets = (payload.get("attachments") or {}).get("markets") or {}
            for meta in markets.values():
                hit = FD_RE.match(meta.get("marketType") or "")
                if not hit:
                    continue
                market = FD_MARKETS[hit.group(1)]
                sides, line = {}, None
                for runner in meta.get("runners") or []:
                    label = (runner.get("runnerName") or "")
                    odds = ((runner.get("winRunnerOdds") or {}).get("trueOdds") or {})
                    price = (odds.get("decimalOdds") or {}).get("decimalOdds")
                    if price is None:
                        price = (odds.get("decimalOdds") if isinstance(
                            odds.get("decimalOdds"), (int, float)) else None)
                    if price is None:
                        continue
                    if label.endswith(" Over"):
                        sides["over"] = float(price)
                    elif label.endswith(" Under"):
                        sides["under"] = float(price)
                    else:
                        continue
                    if runner.get("handicap") is not None:
                        line = float(runner["handicap"])
                if len(sides) != 2 or line is None or line <= 0:
                    continue
                skew = abs(sides["over"] - sides["under"])
                if skew > MAX_PAIR_SKEW:
                    continue
                player = norm((meta.get("marketName") or "").split(" - ")[0])
                if not player:
                    continue
                p_over = devig([sides["over"], sides["under"]])[0]
                out[market][player] = (line, p_over, skew)
            time.sleep(gap)
    return dict(out)


# ---------------------------------------------------------------------------
# The fit
# ---------------------------------------------------------------------------
def raw_at(rungs: list[tuple[float, float]], line: float,
           market: str | None = None) -> float | None:
    """DraftKings' RAW (un-devigged) P(X >= line), at FanDuel's own line.

    Raw on purpose: the whole point is to measure the excess that devigging
    would remove, so devigging first would measure zero by construction.

    A COUNT MARKET MUST NOT BE INTERPOLATED, AND GETTING THAT WRONG IS NOT A
    SMALL ERROR. FanDuel's passing-touchdown line is "Over 1.5", and for an
    integer stat P(X > 1.5) IS P(X >= 2) exactly -- DraftKings' own "2+" rung,
    no interpolation and no ambiguity. Interpolating linearly between the "1+"
    and "2+" rungs instead answers with a value about halfway between them,
    which is far too high, and the ratio it produces is then attributed to
    vig. Measured on the 2026-09-24 board that mistake reported a 50%
    overround on passing touchdowns and 23% on receptions, against 7-10% on
    the three yardage markets -- numbers that are not a plausible hold and
    were the signal that this was an arithmetic bug rather than a discovery
    about how DraftKings prices counts.

    Refuses to EXTRAPOLATE -- a line outside the ladder's range would be
    answered by the shape of the fitted curve rather than by posted prices,
    which is the thing this script exists to avoid assuming.
    """
    pts = sorted((t, 1.0 / d) for t, d in rungs if d > 1.0)
    if not pts:
        return None

    if market in L.COUNT_MARKETS:
        target = math.ceil(line - 1e-9)
        for t, q in pts:
            if abs(t - target) < 1e-9:
                return q
        return None

    if len(pts) < 2 or not (pts[0][0] <= line <= pts[-1][0]):
        return None
    for (t0, q0), (t1, q1) in zip(pts, pts[1:]):
        if t0 <= line <= t1:
            if t1 == t0:
                return q0
            w = (line - t0) / (t1 - t0)
            return q0 + w * (q1 - q0)
    return None


def collect_pairs() -> list[dict]:
    print("DraftKings ladders…", flush=True)
    dk = dk_ladders()
    for market, players in sorted(dk.items()):
        print(f"  {market:<24} {len(players)} players")
    print("FanDuel two-sided lines…", flush=True)
    fd = fd_lines()
    for market, players in sorted(fd.items()):
        print(f"  {market:<24} {len(players)} players")

    pairs = []
    for market, fd_players in fd.items():
        for player, (line, p_over, skew) in fd_players.items():
            rungs = (dk.get(market) or {}).get(player)
            if not rungs:
                continue
            q = raw_at(rungs, line, market)
            if q is None or not (0.02 < p_over < 0.98):
                continue
            pairs.append({"captured_at": _now(), "market": market,
                          "player": player, "line": line, "p_fd": p_over,
                          "q_dk": q, "skew": skew, "rungs": len(rungs),
                          "overround": q / p_over - 1.0})
    return pairs


def load_pairs() -> list[dict]:
    if not PAIRS_PATH.exists():
        return []
    try:
        return json.loads(PAIRS_PATH.read_text())
    except (ValueError, OSError):
        return []


def save_pairs(pairs: list[dict]) -> int:
    """Append, de-duplicating on (captured_at, market, player).

    Accumulating matters more than it looks: FanDuel posts college props only
    a day or two out, so any single run sees a fraction of the season. The fit
    is meant to be over everything ever collected.
    """
    PAIRS_PATH.parent.mkdir(parents=True, exist_ok=True)
    have = load_pairs()
    seen = {(p["captured_at"], p["market"], p["player"]) for p in have}
    added = [p for p in pairs
             if (p["captured_at"], p["market"], p["player"]) not in seen]
    PAIRS_PATH.write_text(json.dumps(have + added, indent=1))
    return len(added)


def report(pairs: list[dict]) -> dict:
    """Per-market overround, and enough context to judge whether to believe it."""
    by_market: dict = collections.defaultdict(list)
    for p in pairs:
        by_market[p["market"]].append(p)

    print()
    print(f"{'market':<24}{'n':>4}{'median':>9}{'mean':>8}{'p25':>8}{'p75':>8}"
          f"{'lopsided':>10}")
    print("-" * 71)
    fitted = {}
    for market, rows in sorted(by_market.items()):
        vals = sorted(r["overround"] for r in rows)
        if not vals:
            continue
        lop = sum(1 for r in rows if r["skew"] > 0.15)
        med = statistics.median(vals)
        q1 = vals[len(vals) // 4]
        q3 = vals[(3 * len(vals)) // 4]
        print(f"{market:<24}{len(vals):>4}{med:>9.4f}{statistics.fmean(vals):>8.4f}"
              f"{q1:>8.4f}{q3:>8.4f}{lop:>10}")
        fitted[market] = med
    print()
    print("median, not mean: one stale rung produces a huge ratio and the mean "
          "follows it.")
    print("'lopsided' counts pairs where FanDuel's two sides differ by more "
          "than 0.15 in\ndecimal odds -- those carry real information about "
          "the curve; the rest pin only\nthe median. A fit made entirely of "
          "even-money pairs is thinner than its n suggests.")
    return fitted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true",
                    help="skip scraping; re-fit whatever is already stored")
    ap.add_argument("--apply", action="store_true",
                    help="print a LADDER_OVERROUND block to paste into "
                         "edge/dfs_ladder.py (never edits it automatically -- "
                         "a fitted constant should be reviewed with its n)")
    ap.add_argument("--min-n", type=int, default=25,
                    help="refuse to suggest a constant fitted on fewer pairs "
                         "than this (default: %(default)s)")
    args = ap.parse_args()

    if args.report:
        pairs = load_pairs()
        print(f"{len(pairs)} stored pairs")
    else:
        fresh = collect_pairs()
        added = save_pairs(fresh)
        print(f"\n{len(fresh)} matched pairs this run, {added} new "
              f"-> {PAIRS_PATH.relative_to(ROOT)}")
        pairs = load_pairs()

    if not pairs:
        print("\nNo pairs. FanDuel posts college player props about 24-48h "
              "before kickoff —\nrun this on a Friday or Saturday.",
              file=sys.stderr)
        return 1

    fitted = report(pairs)

    if args.apply:
        counts = collections.Counter(p["market"] for p in pairs)
        print("\nLADDER_OVERROUND = {")
        for market, value in sorted(fitted.items()):
            n = counts[market]
            flag = "" if n >= args.min_n else f"   # THIN: n={n}, do not ship"
            print(f'    "{market}": {value:.3f},{flag or f"   # n={n}"}')
        print("}")
        print(f"\nCurrent values in edge/dfs_ladder.py: "
              f"{dict(sorted(L.LADDER_OVERROUND.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
