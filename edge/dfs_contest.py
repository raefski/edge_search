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
import re

from edge.names import norm


#: DraftKings' Captain / Showdown multiplier on the CPT slot's points.
CPT_MULT = 1.5


#: DK lobby code -> this repo's sport name.
SPORT = {"NHL": "NHL", "NFL": "NFL", "CFB": "NCAAF", "NAS": "NASCAR", "MMA": "MMA", "MLB": "MLB",
         "NBA": "NBA"}


def contest_id(text: str) -> str | None:
    """A DK contest ID from a pasted link or a bare number."""
    m = re.findall(r"\d{8,10}", text or "")
    return m[-1] if m else None


def lookup(cid: str) -> dict:
    """DK's own contest endpoint: type (cash = double-up / 50-50), sport, fee,
    entries, places paid, payout table and the exact draft group. Works for an
    upcoming contest as well as a finished one."""
    from edge import dfs
    d = dfs._get(f"https://api.draftkings.com/contests/v1/contests/{cid}?format=json")
    c = d.get("contestDetail", d)
    attrs = {k.replace(" ", "").lower(): str(v).lower() for k, v in (c.get("attributes") or {}).items()}
    cash = attrs.get("isdoubleup") == "true" or attrs.get("isfiftyfifty") == "true"
    payouts = [(t["minPosition"], t["maxPosition"],
                sum(float(x.get("value") or 0) for x in t.get("payoutDescriptions", [])))
               for t in c.get("payoutSummary") or []]
    pool = sum((hi - lo + 1) * amt for lo, hi, amt in payouts)
    return {"type": "cash" if cash else "gpp", "sport": SPORT.get(c.get("sport"), c.get("sport")),
            "name": c.get("name"), "field": c.get("entries"), "max_entries": c.get("maximumEntries"),
            "entry_fee": c.get("entryFee"),
            "places_paid": max((hi for _, hi, _ in payouts), default=None),
            "prize_pool": round(pool, 2) or None, "draft_group": c.get("draftGroupId"),
            "guaranteed": (attrs.get("isguranteed") or attrs.get("isguaranteed")) == "true",
            "start": c.get("contestStartTime"), "state": c.get("contestState"),
            "payouts": [p for p in payouts if p[2] > 0]}


def expected_field(info: dict) -> int:
    """The field to plan for: the cap of a capped contest (they fill, and a
    guaranteed one pays as if it did), else the current count (DK lists an
    uncapped free contest at 999,999,999)."""
    cap, now = info.get("max_entries") or 0, info.get("field") or 0
    return int(cap) if 0 < cap < 10_000_000 else int(now)


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
            # A Captain / Showdown CPT row scores 1.5x. `fpts` is the player's
            # BASE score whichever row comes first (an MMA export listed
            # Brady Hiestand's CPT row first: 132.79 for an 88.53 night), and
            # the CPT share of `pct_drafted` is kept separately as `cpt_pct`.
            cpt = (row.get("Roster Position") or "").strip().upper() == "CPT"
            base = round(fpts_val / CPT_MULT, 4) if cpt else fpts_val
            key = norm(name)
            slot = (row.get("Roster Position") or "").strip()
            if key in out:
                out[key]["pct_drafted"] = round(
                    out[key]["pct_drafted"] + pct_val, 4)
                if not cpt:
                    out[key]["fpts"] = fpts_val
                if slot and slot not in out[key]["slots"]:
                    out[key]["slots"].append(slot)
            else:
                out[key] = {"name": name, "pct_drafted": pct_val,
                            "fpts": base, "cpt_pct": 0.0,
                            "slots": [slot] if slot else []}
            if cpt:
                out[key]["cpt_pct"] = round(out[key]["cpt_pct"] + pct_val, 4)
    return out
