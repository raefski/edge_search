#!/usr/bin/env python3
"""Build DK NFL Classic lineups from the free scraped props.

    python3 scripts/dfs_lineups_nfl.py                    # one cash lineup
    python3 scripts/dfs_lineups_nfl.py --mode gpp -n 3    # three stacked ones
    python3 scripts/dfs_lineups_nfl.py --mode gpp --stack-n 3 --bring-back 1

`scripts/dfs_board.py --sport NFL` prints the projected board; this turns that
board into rosters. The correlation model and the reason it is a separate
optimizer from MLB's are documented in edge/dfs_opt_nfl.py, and every number in
it comes from scripts/nfl_correlation.py.

TWO THINGS THIS HAS TO DO THAT THE BOARD DOES NOT

1. Project the DEFENCES. A DST has no prop market at all, so it comes from the
   game markets instead -- the opponent's implied team total, which is exactly
   the input DK's points-allowed tiers take. Without this there is no DST slot
   to fill and no lineup at all.

2. Carry the OPPONENT on every player. The optimizer's one hard correlation
   rule is that a defence never faces its own lineup, and that rule is only
   enforceable if each player knows who they are playing. A missing opponent
   would silently disable the check rather than raise -- so players whose game
   could not be resolved are dropped and counted, not passed through.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs, dfs_opt_nfl, dfs_project, dfs_sport  # noqa: E402
from edge.nfl import TEAM_NAME_TO_ABBR  # noqa: E402
from edge.odds.cli import add_source_args, client_from_args, describe  # noqa: E402
from scripts.dfs_board import collect_player_markets  # noqa: E402

SPORT_KEY = "americanfootball_nfl"


#: nflverse spelling -> DraftKings spelling, where they disagree.
#:
#: Exactly one team, and it is worth the dictionary rather than a special case.
#: edge/nfl.py's TEAM_NAME_TO_ABBR must stay on nflverse's spelling because the
#: ground-truth joins key on it, and DraftKings writes the Rams LAR where
#: nflverse writes LA. Unaliased, every Rams player found no game line and was
#: dropped -- which on the live 2026-09-06 slate silently removed Puka Nacua,
#: the highest-projected receiver on the board, from the pool. That is the
#: failure shape HANDOFF.md names at the top: a lookup matching nothing is
#: indistinguishable from a book offering nothing. Hence also the by-name
#: reporting in build_pool rather than a bare count.
DK_ALIAS = {"LA": "LAR"}


def _abbr(name: str) -> str | None:
    """Full team name -> the code DraftKings uses on the slate."""
    if not name:
        return None
    code = TEAM_NAME_TO_ABBR.get(name)
    if code is None:
        tail = name.rsplit(" ", 1)[-1]
        for full, c in TEAM_NAME_TO_ABBR.items():
            if full.rsplit(" ", 1)[-1] == tail:
                code = c
                break
    return DK_ALIAS.get(code, code)


def game_lines(client) -> dict:
    """{team abbr: {"opp": abbr, "total": x, "spread": y, "game": id}}.

    Read off the same scan as the props. `spread` is that team's own number,
    negative for a favourite -- the sign convention dfs_project.
    implied_team_points expects, and the one that silently inverts every
    defence on the slate if it is taken backwards.
    """
    out: dict[str, dict] = {}
    for ev in client.get_featured_odds(SPORT_KEY, ["totals", "spreads"], "us"):
        home, away = _abbr(ev.get("home_team")), _abbr(ev.get("away_team"))
        if not home or not away:
            continue
        total = spread_home = None
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                for o in m.get("outcomes", []):
                    if m["key"] == "totals" and total is None and o.get("point") is not None:
                        total = float(o["point"])
                    if (m["key"] == "spreads" and spread_home is None
                            and o.get("point") is not None
                            and _abbr(o.get("name")) == home):
                        spread_home = float(o["point"])
        if total is None or spread_home is None:
            continue
        gid = ev.get("id")
        out[home] = {"opp": away, "total": total, "spread": spread_home, "game": gid}
        out[away] = {"opp": home, "total": total, "spread": -spread_home, "game": gid}
    return out


def build_pool(client, args) -> tuple[list, dict]:
    sport = dfs_sport.get(SPORT_KEY)
    gid, salaries = (args.draft_group, dfs.fetch_draftables(args.draft_group)) \
        if args.draft_group else dfs.pick_priced_group("NFL")
    if gid is None:
        raise SystemExit("no PRICED NFL draft group found -- DK lists a slate "
                         "before it prices it, so this is normal a few days out.")
    lines = game_lines(client)
    props = collect_player_markets(client, SPORT_KEY, sport.market_keys()).get(args.book, {})
    if not props:
        raise SystemExit(f"no props from {args.book!r}")

    pool, stats = [], {"no_game": 0, "no_proj": 0, "dst": 0, "offense": 0}
    missing_teams: set = set()
    for name, markets in props.items():
        info = salaries.get(dfs.norm(name))
        if not info or not info.get("salary"):
            continue
        team = info.get("team")
        line = lines.get(team)
        if not line:
            stats["no_game"] += 1        # counted BY TEAM, never passed through:
            missing_teams.add(team)      # a missing opponent disables the DST
            continue                     # rule, and a whole team going missing
                                         # looks exactly like a thin slate
        res = dfs_project.project(markets, sport, position=info.get("position"))
        if res["proj"] is None:
            stats["no_proj"] += 1
            continue
        pool.append({"name": info["name"], "pos": dfs_opt_nfl.eligible_slots(info.get("position")),
                     "salary": info["salary"], "proj": res["proj"], "team": team,
                     "opp_team": line["opp"], "game": line["game"],
                     "dk_pos": info.get("position") or ""})
        stats["offense"] += 1

    # Defences: no prop market exists, so they come from the game markets.
    for key, info in salaries.items():
        if "DST" not in (info.get("position") or "").upper():
            continue
        team = info.get("team")
        line = lines.get(team)
        if not line or not info.get("salary"):
            continue
        opp_pts = dfs_project.implied_team_points(line["total"], -line["spread"])
        res = dfs_project.project_dst(opp_pts)
        pool.append({"name": info["name"], "pos": {"DST"}, "salary": info["salary"],
                     "proj": res["proj"], "team": team, "opp_team": line["opp"],
                     "game": line["game"], "dk_pos": "DST"})
        stats["dst"] += 1
    stats["missing_teams"] = sorted(t for t in missing_teams if t)
    return pool, stats


def show(res, idx=None):
    if res is None:
        print("  no legal lineup"); return
    head = f"lineup {idx}" if idx else "lineup"
    print(f"\n{head}: proj {res['proj']}  ceil {res['ceil']}  salary ${res['salary']:,}")
    if res["stack"]:
        s = res["stack"]
        print(f"  stack: {s['qb']} ({s['team']}) + {', '.join(s['with']) or 'none'}"
              + (f"  bring-back: {', '.join(s['bring_back'])}" if s["bring_back"] else ""))
    order = {s: i for i, s in enumerate(dfs_opt_nfl.SLOTS)}
    for p, slot in sorted(res["lineup"], key=lambda t: order[t[1]]):
        print(f"    {slot:5s} {p['name'][:24]:26s} {p['team']:4s} vs {p['opp_team']:4s} "
              f"${p['salary']:>6,} {p['proj']:6.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="cash", choices=("cash", "gpp"))
    ap.add_argument("-n", "--lineups", type=int, default=1)
    ap.add_argument("--stack-n", type=int, default=2,
                    help="own pass-catchers with the QB (default: %(default)s -- "
                         "teammates are slightly ANTI-correlated, see dfs_opt_nfl)")
    ap.add_argument("--bring-back", type=int, default=1)
    ap.add_argument("--stack-qb", default=None)
    ap.add_argument("--book", default="draftkings")
    ap.add_argument("--draft-group", type=int, default=None)
    ap.add_argument("--iters", type=int, default=2000)
    ap.add_argument("--max-overlap", type=int, default=6,
                    help="most players two lineups in a portfolio may share")
    add_source_args(ap)
    args = ap.parse_args()

    client = client_from_args(args, SPORT_KEY, consumer="dfs")
    print(describe(client))
    pool, stats = build_pool(client, args)
    print(f"pool: {stats['offense']} offensive players + {stats['dst']} defences"
          f"  ({stats['no_proj']} unprojectable, {stats['no_game']} with no game)")
    if stats["missing_teams"]:
        print(f"  WARNING: no game line for {', '.join(stats['missing_teams'])} -- "
              f"every player on those teams was dropped. A whole team missing is "
              f"an abbreviation mismatch (see DK_ALIAS), not a thin slate.",
              file=sys.stderr)
    if stats["dst"] == 0:
        print("  WARNING: no defences priced -- every lineup needs one, so this "
              "will return nothing", file=sys.stderr)

    if args.lineups == 1:
        show(dfs_opt_nfl.optimize(pool, mode=args.mode, stack_qb=args.stack_qb,
                                  stack_n=args.stack_n, bring_back=args.bring_back,
                                  iters=args.iters, seed=0))
        return 0
    # More than one lineup means a PORTFOLIO, not the same lineup n times --
    # varying only the seed converges to the same optimum. See dfs_opt_nfl.portfolio.
    res = dfs_opt_nfl.portfolio(pool, args.lineups, max_overlap=args.max_overlap,
                                mode=args.mode, stack_n=args.stack_n,
                                bring_back=args.bring_back, iters=args.iters)
    for i, r in enumerate(res, 1):
        show(r, i)
    if len(res) < args.lineups:
        print(f"\n  only {len(res)} of {args.lineups} lineups were distinct enough "
              f"(max_overlap={args.max_overlap}); the pool cannot support more.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
