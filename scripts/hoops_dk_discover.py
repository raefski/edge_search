#!/usr/bin/env python3
"""Read off DraftKings everything the NBA / college basketball build ASSUMED.

    python3 -m harness run hoops-verify          # both sports
    python3 scripts/hoops_dk_discover.py --sport NBA

The basketball build shipped 2026-10-13, before either season opened, from a
container that could not reach DraftKings. Every contest fact below was
therefore written down rather than read, which is the provenance that made
the NFL prop ids wrong (edge/arb/draftkings_league.py). This prints the live
value beside the assumed one, and exits non-zero on any disagreement:

  lobby      the GameTypes the lobby names "Classic" (NBA assumed 70; CBB
             resolved by name, no id assumed)
  rules      api.draftkings.com/lineups/v1/gametypes/<id>/rules: the roster
             slots, salary cap and team/game minimums (edge/nba.py, ncaab.py)
  draftables one priced Classic group: the position strings DK uses (do they
             all map to a slot?), the injury statuses seen, and which
             draftStatAttributes id behaved like FPPG (dfs.detect_fppg_id)
  sportsbook which DK prop categories the basketball NAME rule selected
             (draftkings_league.PROP_CATEGORY_NAME), so their ids can be pinned

Cost: per sport one lobby call, one rules call, one draftables call, one
sportsbook league call -- inside DRAFTKINGS_ACCESS.md's budget, with a 1.5 s
gap between calls.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs, nba, ncaab  # noqa: E402

ASSUMED = {
    "NBA": {"classic": {nba.CLASSIC_GAME_TYPE}, "slots": list(nba.SLOTS), "cap": nba.SALARY_CAP,
            "slots_for": nba.slots_for, "odds": "basketball_nba"},
    "CBB": {"classic": None, "slots": list(ncaab.SLOTS), "cap": ncaab.SALARY_CAP,
            "slots_for": ncaab.slots_for, "odds": "basketball_ncaab"},
}
GAP = 1.5


def check(sport: str) -> int:
    want, bad = ASSUMED[sport], 0
    print(f"\n=== {sport} ===")
    groups = dfs.draft_groups(sport)
    classic = dfs.classic_game_types(sport)
    print(f"lobby: {len(groups)} draft groups; GameTypes named Classic: {sorted(classic) or 'none'}")
    for g in dfs.LOBBY_GAME_TYPES.get(sport) or []:
        print(f"   {g.get('GameTypeId'):>5}  {g.get('Name')}  ({g.get('DraftType')})")
    if want["classic"] and classic and classic != want["classic"]:
        print(f"  !! Classic id is {sorted(classic)}, the code assumes {sorted(want['classic'])}")
        bad += 1
    cg = [g for g in groups if g.get("GameTypeId") in (classic or want["classic"] or set())]
    if not cg:
        print("  (no Classic group listed -- out of season?)")
        return bad
    gt = cg[0]["GameTypeId"]
    time.sleep(GAP)
    try:
        rules = dfs._get(f"https://api.draftkings.com/lineups/v1/gametypes/{gt}/rules")
        # The template's exact shape has not been read for basketball; take a
        # slot name from whichever field carries one, and show the raw keys
        # if none does, rather than guessing quietly.
        slots = []
        for x in rules.get("lineupTemplate") or []:
            slot = x.get("rosterSlot") if isinstance(x, dict) else None
            name = (slot or {}).get("name") if isinstance(slot, dict) else None
            slots.append(name or (x.get("name") if isinstance(x, dict) else x))
        if not any(slots):
            print(f"  rules keys: {sorted(rules)[:20]}")
        cap = (rules.get("salaryCap") or {}).get("maxValue")
        print(f"rules {gt}: slots {slots}  cap {cap}  minTeams {rules.get('teamCount')} "
              f"minGames {rules.get('gameCount')}  lateSwap {rules.get('allowLateSwap')}")
        if slots and [str(s).upper() for s in slots] != want["slots"]:
            print(f"  !! roster differs from the code's {want['slots']}")
            bad += 1
        if cap and int(cap) != want["cap"]:
            print(f"  !! cap {cap} != {want['cap']}")
            bad += 1
    except Exception as exc:                                # noqa: BLE001
        print(f"  rules unreadable: {exc}")
    gid = cg[0]["DraftGroupId"]
    time.sleep(GAP)
    try:
        sal = dfs.fetch_draftables(gid)
    except Exception as exc:                                # noqa: BLE001
        print(f"  draftables {gid}: {exc}")
        return bad
    positions = sorted({s.get("position") or "" for s in sal.values()})
    unmapped = [p for p in positions if not want["slots_for"](p)]
    statuses = sorted({s.get("dk_status") or "-" for s in sal.values()})
    with_fppg = sum(s.get("dk_fppg") is not None for s in sal.values())
    print(f"draftables {gid}: {len(sal)} players, positions {positions}")
    print(f"   statuses {statuses};  FPPG on {with_fppg}/{len(sal)} via "
          f"{'a known id' if dfs.LAST_DETECTED_FPPG_ID is None else f'DETECTED id {dfs.LAST_DETECTED_FPPG_ID} -- pin it in dfs.FPPG_STAT_IDS'}")
    if unmapped:
        print(f"  !! positions no slot accepts: {unmapped}")
        bad += 1
    time.sleep(GAP)
    try:
        from edge.arb.draftkings_league import DraftKingsLeague
        dk = DraftKingsLeague()
        payload = dk.fetch(want["odds"])
        cats = {int(c["id"]): c.get("name") for c in payload.get("categories") or [] if c.get("id")}
        subs = dk.prop_subcategories(payload, sport_key=want["odds"])
        chosen = sorted({c for c, _, _ in subs})
        print(f"sportsbook: {len(subs)} prop tabs selected from categories "
              + ", ".join(f"{c} {cats.get(c)!r}" for c in chosen))
        if not subs:
            print("  !! no basketball prop tab selected -- the name rule matched nothing")
            bad += 1
    except Exception as exc:                                # noqa: BLE001
        print(f"  sportsbook unreadable: {exc}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", choices=("NBA", "CBB"), action="append")
    args = ap.parse_args()
    bad = 0
    for k, sport in enumerate(args.sport or ("NBA", "CBB")):
        if k:
            time.sleep(GAP)
        bad += check(sport)
    print(f"\n{bad} disagreement(s)." if bad else "\nEverything checked agrees with the code.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
