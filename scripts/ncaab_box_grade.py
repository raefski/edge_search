#!/usr/bin/env python3
"""Grade a college build on EVERY player on the board, from ESPN's box scores.

    python3 scripts/ncaab_box_grade.py <draft group>    # grade one logged slate
    python3 scripts/ncaab_box_grade.py --fit            # refit SALARY_TIER_LINE on saved slates

Why the contest export is not enough (scripts/nba_calibration.py --sport ncaab reads it):
it lists only the players somebody drafted. On 2026-11-02 (draft group 155565) it had 43 of
the 148 bench rows and said the bench ran 5.13 points LOW; on all 148, zeros included, the
miss is 1.75. It also has no team column: the two Trent Perrys came back as one name, UCLA's
35.25 next to Kansas's 7.00. Box scores have the undrafted, the teams, minutes and who started.

Names go through edge/ncaab_names.Rosters inside the player's own team, and the box points
are checked against DK's FPTS wherever an export has them (172 of 173 agreed that night; the
one miss was Perry). Boxes are kept in data/ncaab_box/<gid>.json once every game is final.
One night is a reading, not a fit: CLAUDE.md wants ~5 slates before anything is called proven.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.dfs_contest import parse_contest_file  # noqa: E402
from edge.names import norm  # noqa: E402
from edge.ncaab import match_team  # noqa: E402
from edge.ncaab_espn import ESPN, _num, box_rows, get as _get  # noqa: E402
from edge.ncaab_names import Rosters  # noqa: E402

BOX_DIR = ROOT / "data" / "ncaab_box"
LOG = ROOT / "data" / "dfs_proj_log_ncaab.csv"
_SLOT = re.compile(r"(?:^|\s)(?:G|F|UTIL)\s+")


def logged(gid: int) -> list[dict]:
    with LOG.open(newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if str(r["gid"]) == str(gid)]
    for r in rows:
        for k in ("salary", "proj", "own"):
            r[k] = float(r[k] or 0)
    return rows


def fetch(rows: list[dict]) -> list[dict] | None:
    """Every game on the slate from ESPN, or None while any is missing or unfinished."""
    games = {tuple(sorted((r["team"], r["opp"]))) for r in rows}
    day = rows[0]["date"].replace("-", "")
    board = _get(f"{ESPN}/scoreboard?dates={day}&groups=50&limit=400").get("events", [])
    box, bad = [], []
    for pair in sorted(games):
        best, best_score = None, 0.0
        for ev in board:
            names = [c["team"].get("displayName", "") for c in ev["competitions"][0]["competitors"]]
            if len(names) != 2:
                continue
            score = max(match_team(pair[0], names[0]) + match_team(pair[1], names[1]),
                        match_team(pair[0], names[1]) + match_team(pair[1], names[0]))
            if score > best_score:
                best, best_score = ev, score
        if best is None or best_score < 1.5 or not best["status"]["type"].get("completed"):
            bad.append("/".join(pair))
            continue
        box += box_rows(_get(f"{ESPN}/summary?event={best['id']}"), pair)
        time.sleep(1)
    if bad:
        print(f"Not final or not found on ESPN: {', '.join(bad)}. Nothing saved.")
        return None
    return box


def boxes(gid: int, rows: list[dict]) -> list[dict] | None:
    path = BOX_DIR / f"{gid}.json"
    if path.exists():
        return json.loads(path.read_text())
    box = fetch(rows)
    if box is not None:
        BOX_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(box))
    return box


def join(rows: list[dict], box: list[dict]) -> tuple[list[dict], list[dict]]:
    """(logged rows with truth, box rows nobody claimed). A logged player with no box row
    never dressed: he scored 0. Unclaimed rows that logged minutes are a name problem."""
    rosters = Rosters({f"{norm(r['player'])}|{r['team']}": {"name": r["player"], "team": r["team"], "row": r}
                       for r in rows})
    for r in rows:
        r.update(act=0.0, min=0.0, started=False)
    unclaimed = []
    for b in box:
        info, _how = rosters.resolve(b["name"], [b["team"]])
        if info is None:
            unclaimed.append(b)
        else:
            info["row"].update(act=b["fp"], min=b["min"], started=b["started"])
    return rows, unclaimed


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def line(label: str, grp: list[dict]) -> str:
    return (f"  {label:<8} n={len(grp):>3}  proj {_mean(r['proj'] for r in grp):5.1f}  "
            f"actual {_mean(r['act'] for r in grp):5.1f}  bias {_mean(r['proj'] - r['act'] for r in grp):+6.2f}  "
            f"MAE {_mean(abs(r['proj'] - r['act']) for r in grp):5.2f}  "
            f"played {_mean(r['min'] > 0 for r in grp):4.0%}  min {_mean(r['min'] for r in grp):4.1f}")


def largest_gpp(gid: int) -> str | None:
    meta = json.loads((ROOT / "data" / "contest_meta.json").read_text())
    gpps = [(m.get("field") or 0, cid) for cid, m in meta.items()
            if m.get("draft_group") == gid and m.get("type") == "gpp"]
    return max(gpps)[1] if gpps else None


def lineup_owns(path: Path, own: dict) -> list[tuple[float, float]]:
    """(points, summed field ownership) for each entry in an export."""
    out = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            names = [n for n in _SLOT.split(row.get("Lineup") or "") if n]
            if row.get("EntryId") and len(names) == 8:
                out.append((_num(row.get("Points")), sum(own.get(norm(n), 0.0) for n in names)))
    return out


def ownership(rows: list[dict], gid: int) -> None:
    cid = largest_gpp(gid)
    if not cid:
        print("\nNo GPP export for this draft group: ownership not graded.")
        return
    path = ROOT / "data" / f"contest-standings-{cid}.csv"
    board = parse_contest_file(path)
    for r in rows:
        r["field"] = (board.get(norm(r["player"])) or {}).get("pct_drafted", 0.0)
    top = sorted(rows, key=lambda r: -r["field"])
    print(f"\nOwnership vs GPP {cid} (one contest, GPP only, never pooled with cash):")
    print(f"  all {len(rows)}: MAE {_mean(abs(r['own'] - r['field']) for r in rows):.2f}. Field's top 10 average "
          f"{_mean(r['field'] for r in top[:10]):.1f}%, ours on those {_mean(r['own'] for r in top[:10]):.1f}%")
    for r in top[:10]:
        print(f"    {r['player']:<26} {r['team']:<6} ${r['salary']:>6,.0f}  field {r['field']:4.1f}%  "
              f"ours {r['own']:4.1f}%  scored {r['act']:5.2f}")
    entries = sorted(lineup_owns(path, {norm(r["player"]): r["field"] for r in rows}), key=lambda e: -e[0])
    cut = max(1, len(entries) // 100)
    print(f"  lineup ownership: top 1% ({cut}) {_mean(e[1] for e in entries[:cut]):.0f}%, "
          f"field ({len(entries)}) {_mean(e[1] for e in entries):.0f}%. Same projection not held fixed; one contest.")


def grade(gid: int) -> int:
    rows = logged(gid)
    if not rows:
        print(f"No college build logged for draft group {gid}.")
        return 1
    box = boxes(gid, rows)
    if box is None:
        return 1
    rows, unclaimed = join(rows, box)
    print(f"Draft group {gid}, {rows[0]['date']}: {len(rows)} logged players against ESPN box scores. "
          "ONE slate: a reading, not a fit.")
    print(line("all", rows))
    for src in ("props", "blend", "salary", "bench"):
        print(line(src, [r for r in rows if r["proj_source"] == src]))
    played = [b["name"] for b in unclaimed if b["min"] > 0]
    print(f"Box rows not on the board: {len(unclaimed)}" + (f"; PLAYED, CHECK NAMES: {played}" if played else ", all 0 minutes"))
    listed = [r for r in rows if r["starter"] in ("1", "True")]
    print(f"Starters: {sum(r['started'] for r in rows)} started, we listed {len(listed)}, "
          f"{sum(r['started'] for r in listed)} of those right")
    q = [r for r in rows if r["status"]]
    print(f"Tagged: {len(q)}, played {sum(r['min'] > 0 for r in q)}")
    print("Projected 10+, did not play:")
    for r in sorted(rows, key=lambda r: -r["proj"]):
        if r["proj"] >= 10 and not r["min"]:
            print(f"    {r['player']:<26} {r['team']:<6} {r['proj']:5.1f} {r['proj_source']}")
    for src in ("salary",):
        print(f"{src} tier:")
        for r in sorted((r for r in rows if r["proj_source"] == src), key=lambda r: -r["salary"]):
            print(f"    {r['player']:<26} {r['team']:<6} ${r['salary']:>6,.0f}  proj {r['proj']:5.1f}  "
                  f"actual {r['act']:5.2f}  {r['min']:3.0f} min")
    ownership(rows, gid)
    return 0


def fit_line(xs: list[dict]) -> tuple[float, float]:
    """Least squares DK points on salary in $K."""
    mx, my = _mean(r["salary"] / 1000 for r in xs), _mean(r["act"] for r in xs)
    den = sum((r["salary"] / 1000 - mx) ** 2 for r in xs)
    slope = sum((r["salary"] / 1000 - mx) * (r["act"] - my) for r in xs) / den if den else 0.0
    return my - slope * mx, slope


def fit_salary_tier() -> int:
    """Fit on all but one unit, score the unit left out. The unit is the slate once there
    are two; with one, the game, which is weaker: games in a night share nothing but say so."""
    tier = []
    for path in sorted(BOX_DIR.glob("*.json")):
        gid = int(path.stem)
        rows, _ = join(logged(gid), json.loads(path.read_text()))
        tier += [{**r, "game": "/".join(sorted((r["team"], r["opp"])))} for r in rows if r["proj_source"] == "salary"]
    slates = {r["gid"] for r in tier}
    unit = "gid" if len(slates) > 1 else "game"
    errs = []
    for u in {r[unit] for r in tier}:
        a, b = fit_line([r for r in tier if r[unit] != u])
        errs += [a + b * r["salary"] / 1000 - r["act"] for r in tier if r[unit] == u]
    a, b = fit_line(tier)
    print(f"Salary tier, n={len(tier)}, {len(slates)} slate(s), {len({r['game'] for r in tier})} games")
    print(f"  logged  MAE {_mean(abs(r['proj'] - r['act']) for r in tier):.2f}  "
          f"bias {_mean(r['proj'] - r['act'] for r in tier):+.2f}")
    print(f"  line ({a:.2f} + {b:.2f} per $1K)  held out by {unit}: MAE {_mean(abs(e) for e in errs):.2f}  "
          f"bias {_mean(errs):+.2f}" + ("  NOT ENOUGH DATA below ~5 slates" if len(slates) < 5 else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("gid", nargs="?", type=int)
    ap.add_argument("--fit", action="store_true", help="refit the salary tier on data/ncaab_box/")
    args = ap.parse_args()
    if args.fit:
        return fit_salary_tier()
    if not args.gid:
        ap.error("give a draft group, or --fit")
    return grade(args.gid)


if __name__ == "__main__":
    sys.exit(main())
