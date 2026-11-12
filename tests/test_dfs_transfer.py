"""scripts/dfs_transfer.py: the cross-sport lesson ledger the target scout reads (Adam 2026-11-11)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import dfs_transfer as T  # noqa: E402

STATUSES = {"open", "measure", "waiting", "handled", "null", "audit"}
KINDS = {"projection", "availability", "ownership", "eval", "market"}


def test_every_transfer_names_a_lesson_a_sport_and_where_to_look():
    lessons = {L["id"] for L in T.LESSONS}
    ids = [t["id"] for t in T.TRANSFERS]
    assert len(ids) == len(set(ids)) and len(lessons) == len(T.LESSONS)
    for t in T.TRANSFERS:
        assert t["lesson"] in lessons, t["id"]
        assert t["sport"] in (*T.SPORTS, "all"), t["id"]
        assert t["status"] in STATUSES and t["kind"] in KINDS, t["id"]
        assert t["idea"] and t["data"], t["id"]
        m = t.get("measured")
        if m:                               # a number carries its date and how it was got
            assert m["at"] and m["note"] and isinstance(m["pts_lineup"], (int, float)), t["id"]
        if t["status"] == "open":           # open = in our code, with the data on hand
            assert t.get("where") or m, t["id"]
    for L in T.LESSONS:
        assert L["mechanism"] and L["ref"] and L["evidence"]
        for s, d in L["covers"]:
            assert s in (*T.SPORTS, "overlay") and len(d) == 10


def test_a_shipped_fix_no_lesson_covers_is_shown_to_the_other_sports(monkeypatch):
    cycles = [{"sport": "nba", "date": "2026-11-08", "hole": "props ignored"},      # L03 covers it
              {"sport": "nba", "date": "2026-11-09", "hole": "props again"},        # within a day: covered
              {"sport": "nhl", "date": "2026-12-01", "hole": "a new hole"}]
    assert [c["hole"] for c in T.unmapped(cycles)] == ["a new hole"]
    monkeypatch.setattr(T, "shipped_cycles", lambda: cycles)
    assert [c["hole"] for c in T.for_sport("nba")["unmapped_lessons"]] == ["a new hole"]
    assert T.for_sport("nhl")["unmapped_lessons"] == []                             # not its own


def test_for_sport_gives_the_scout_open_rows_and_the_graveyard():
    nhl = T.for_sport("nhl")
    assert "T01" not in [r["id"] for r in nhl["open"]]                               # NHL cycle 14 (11/12)
    assert any(x.startswith("T01 handled") for x in nhl["settled"])
    assert all(r["status"] in ("open", "measure", "audit") for r in nhl["open"])
    assert any("goalie DK points" in g["idea"] for g in nhl["graveyard"])           # NHL cycle 7: null
    assert all(any(s in g["sports"] for s in ("nhl", "all")) for g in nhl["graveyard"])


def test_pareto_prices_only_measured_open_holes_and_ranks_them():
    doc = T.pareto(live_money=False, probes=False)
    rows = doc["ideas"]
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    priced = [r for r in rows if r["usd_week"] is not None]
    assert priced and rows[:len(priced)] == priced                                   # priced first
    assert [r["usd_week"] for r in priced] == sorted((r["usd_week"] for r in priced), reverse=True)
    assert priced[-1]["cum_share"] == 1.0
    assert all(r["status"] == "open" for r in priced)
    nba = next(r for r in rows if r["id"] == "T02")
    assert nba["usd_week"] == round(nba["pts_lineup"] * T.DPPW_2026_11_11["NBA"], 2) and nba["upper_bound"]
    assert next(r for r in rows if r["id"] == "T10")["usd_week"] is None            # null is not priced
