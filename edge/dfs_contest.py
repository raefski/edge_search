"""Reading a DraftKings contest-standings export. The one piece of the
calibration loop that carries no sport in it at all.

EXTRACTED 2026-09-24, and the trigger is the usual one in this repo: a fourth
consumer. scripts/dfs_calibration.py (MLB), dfs_calibration_nfl.py,
dfs_sim_validate.py and friends all reached into the MLB script for this
function, which was fine while every caller lived beside it. The college
calibration script is the first consumer that has to run in a repo where the
MLB pipeline is not present at all -- scripts/mirror_app.py generates a
standalone NCAAF app -- and `from scripts.dfs_calibration import ...` drags in
scripts.dfs_grade, statsapi box scores and the whole MLB actuals cache behind
it.

So the sport-free half moves here and scripts/dfs_calibration.py re-exports it.
Nothing about the MLB path changes, and there is still exactly one
implementation.
"""
from __future__ import annotations

import csv

from edge.names import norm


def parse_contest_file(path) -> dict:
    """{norm_name: {"name", "pct_drafted", "fpts"}} from one DK export.

    Each DK contest-standings CSV interleaves two unrelated tables in the same
    rows: the per-entry leaderboard (Rank, EntryId, EntryName, ..., Points,
    Lineup) and, in the trailing columns, a field ownership board (Player,
    Roster Position, %Drafted, FPTS) whose rows have nothing to do with the
    entry beside them. Only the second is wanted, and the discriminator is
    that a real ownership row has a Player and a percentage.

    OWNERSHIP IS SUMMED ACROSS ROSTER SLOTS, NOT OVERWRITTEN. DraftKings lists
    a player once per slot he was used in, and taking the last row silently
    discards most of the board: it dropped 2/3 of a 2026-09-13 NFL GPP board's
    ownership (300% of 900%) and 5-10% of every MLB board's.

    That effect is LARGEST in college football, which is worth knowing before
    reading a college number against an NFL one. A DK CFB roster is
    QB/RB/RB/WR/WR/WR/FLEX/S-FLEX: a receiver can be drafted as WR, FLEX or
    S-FLEX and a quarterback as QB or S-FLEX, so most of the board is
    multi-slot rather than a minority of it.
    """
    out: dict = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("Player") or "").strip()
            pct = (row.get("%Drafted") or "").strip()
            fpts = (row.get("FPTS") or "").strip()
            if not name or not pct.endswith("%"):
                continue
            try:
                pct_val = float(pct.rstrip("%"))
                fpts_val = float(fpts)
            except ValueError:
                continue
            key = norm(name)
            if key in out:
                out[key]["pct_drafted"] = round(
                    out[key]["pct_drafted"] + pct_val, 4)
            else:
                out[key] = {"name": name, "pct_drafted": pct_val,
                            "fpts": fpts_val}
    return out
