"""Barttorvik's player table: last season's real box scores for every D1 player.

    https://barttorvik.com/getadvstats.php?year=2026&csv=1    (no header row)

Why it is here. On opening night DK's FPPG is LAST season's (it agrees with this table
to within the double-double bonus: Thomas Haugh 32.3 / 31.9), wherever the player was
then: Dennis Parker Jr. carries 31.5 from Radford at a $3,500 Kansas salary. This table
says where he earned it, in what minutes, and with what real shape of stats.

THE JOIN IS CHECKED BY NUMBERS. Names alone are not safe across ~5,000 players: DK's
$9,700 Duke freshman Cameron Williams is not Portland's Cameron Williams, and Jake Davis
(Illinois / Cal Poly) and Isaiah Brown (Florida / Howard) each have two rows. A row is
accepted when it is the same school, or when its computed DK points match DK's own FPPG
within FP_MATCH; a transfer with no FPPG to check against stays unjoined.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from edge import ncaab
from edge.ncaab_names import _same_person
from edge.names import norm

ROOT = Path(__file__).resolve().parents[1]
URL = "https://barttorvik.com/getadvstats.php?year={year}&csv=1"
PATH = ROOT / "data" / "ncaab_torvik_{year}.json"
#: Season whose rows are the prior for 2026-27 (Torvik names a season by its spring).
PRIOR_YEAR = 2026
#: DK FPPG vs points computed here. DK's figure adds double-double bonuses and sees
#: turnovers exactly; on board 155565 the same-school joins differed by 0.0-0.9.
FP_MATCH = 1.5
#: CSV columns read (the file has no header; layout checked against rows 2026-11-02).
COL = {"name": 0, "team": 1, "conf": 2, "gp": 3, "tov_pct": 12, "fta": 14, "fg2a": 17,
       "fg3m": 19, "fg3a": 20, "cls": 25, "ht": 26, "mpg": 54, "oreb": 57, "dreb": 58,
       "reb": 59, "ast": 60, "stl": 61, "blk": 62, "pts": 63, "role": 64}


def parse_csv(text: str) -> list[dict]:
    out = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 65:
            continue
        try:
            gp = float(r[COL["gp"]])
            if gp <= 0:
                continue
            fga = (float(r[COL["fg2a"]]) + float(r[COL["fg3a"]])) / gp
            fta = float(r[COL["fta"]]) / gp
            tor = min(float(r[COL["tov_pct"]]) / 100.0, 0.9)
            row = {"name": r[COL["name"]], "team": r[COL["team"]], "conf": r[COL["conf"]],
                   "gp": gp, "mpg": float(r[COL["mpg"]]), "pts": float(r[COL["pts"]]),
                   "reb": float(r[COL["reb"]]), "ast": float(r[COL["ast"]]),
                   "stl": float(r[COL["stl"]]), "blk": float(r[COL["blk"]]),
                   "fg3": float(r[COL["fg3m"]]) / gp,
                   # usage identity: TOV% = TOV / (FGA + .44 FTA + TOV)
                   "tov": tor * (fga + 0.44 * fta) / (1.0 - tor),
                   "cls": r[COL["cls"]], "role": r[COL["role"]]}
        except (TypeError, ValueError):
            continue
        out.append({k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()})
    return out


def dk_points(row: dict) -> float:
    return (row["pts"] + 0.5 * row["fg3"] + 1.25 * row["reb"] + 1.5 * row["ast"]
            + 2.0 * row["stl"] + 2.0 * row["blk"] - 0.5 * row["tov"])


def load(year: int = PRIOR_YEAR) -> list[dict]:
    p = Path(str(PATH).format(year=year))
    return json.loads(p.read_text()) if p.exists() else []


def join(salaries: dict, rows: list[dict] | None = None) -> tuple[dict, dict]:
    """({salary key: Torvik row + same_school}, audit counts). A DK player absent from
    the result has no verifiable history: a freshman, or a transfer DK has no FPPG for."""
    rows = load() if rows is None else rows
    by_flat: dict = {}
    for r in rows:
        by_flat.setdefault(norm(r["name"]), []).append(r)
    out: dict = {}
    audit = {"same_school": 0, "fingerprint": 0, "refused_mismatch": [], "no_row": 0}
    for key, info in salaries.items():
        cands = by_flat.get(norm(info.get("name") or ""), [])
        team = str(info.get("team") or "")
        same = [r for r in cands if ncaab.match_team(team, r["team"]) >= 0.8
                and _same_person(r["name"], info["name"])]
        fp = info.get("dk_fppg")
        if len(same) == 1:
            out[key] = {**same[0], "same_school": True}
            audit["same_school"] += 1
            continue
        near = [r for r in cands if fp and abs(dk_points(r) - fp) <= FP_MATCH]
        if len(near) == 1:
            out[key] = {**near[0], "same_school": False}
            audit["fingerprint"] += 1
        elif cands:
            audit["refused_mismatch"].append(info["name"])
        else:
            audit["no_row"] += 1
    return out, audit
