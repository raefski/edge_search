"""Overlay entry value (edge/dfs_entry_edge.py) and the overlay alert job
(harness/overlay.py): what gets pushed, once, and when it is a final call."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edge.dfs_entry_edge import entry_edge, paid_out, places_paid, projected_fill
from harness import overlay

T0 = datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)


# ── the arithmetic ─────────────────────────────────────────────────────────
def test_the_five_of_eleven_example():
    """Pays the top 5 of an 11-entry field, $1 entry, double-up style ($2 each).
    Five entered: everyone cashes. A sixth entry: 5 of 6 cash, and the
    expected return is 5*2/6 = 1.67 per $1."""
    tiers = [[1, 5, 2.0]]
    now = entry_edge(tiers, 5, 1.0)
    assert now["entries_after"] == 6 and now["places"] == 5
    assert now["cash"] == pytest.approx(5 / 6)
    assert now["return"] == pytest.approx(10 / 6)
    assert now["roi"] == pytest.approx(10 / 6 - 1)
    # the field as it stands (5 entries) is the 100% the user quoted
    assert entry_edge(tiers, 4, 1.0)["cash"] == pytest.approx(1.0)


def test_a_full_field_cashes_only_its_paid_places():
    tiers = [[1, 5, 2.0]]
    assert entry_edge(tiers, 10, 1.0)["cash"] == pytest.approx(5 / 11)


def test_entries_below_the_paid_places_all_cash():
    tiers = [[1, 3, 10.0]]
    e = entry_edge(tiers, 2, 1.0)
    assert e["cash"] == 1.0
    assert e["return"] == pytest.approx(paid_out(tiers, 3) / 3)


def test_paid_places_and_totals():
    assert places_paid([[1, 1, 20.0], [2, 10, 0.0]]) == 1
    assert paid_out([[1, 1, 20.0], [2, 4, 5.0]], 3) == pytest.approx(20 + 5 + 5)


def test_projection_extrapolates_the_last_two_snapshots_and_caps_at_the_field():
    hist = [(T0, 5), (T0 + timedelta(minutes=30), 20)]
    p = projected_fill(hist, cap=100, minutes_to_lock=30)
    assert p["rate_per_min"] == pytest.approx(0.5)
    assert p["at_lock"] == 35
    assert projected_fill(hist, cap=18, minutes_to_lock=30)["at_lock"] == 18
    assert projected_fill([], cap=18, minutes_to_lock=30)["at_lock"] is None


def test_short_baselines_claim_no_rate():
    """Eight seconds apart projected 90 entries to 103; ten minutes apart, one
    entry, projected +14 over 137 minutes. Neither is a trend."""
    seconds = [(T0, 5), (T0 + timedelta(seconds=8), 90)]
    minutes = [(T0 - timedelta(minutes=10), 89), (T0, 90)]
    for hist in (seconds, minutes):
        p = projected_fill(hist, cap=237, minutes_to_lock=137)
        assert p["rate_per_min"] == 0.0 and p["at_lock"] == hist[-1][1]


def test_a_shrinking_field_projects_flat():
    hist = [(T0, 9), (T0 + timedelta(minutes=10), 7)]
    assert projected_fill(hist, cap=20, minutes_to_lock=30)["at_lock"] == 7


# ── the alert job ──────────────────────────────────────────────────────────
def _found(cid, nt, m, fee, mins, name="NFL $1 Double Up (CHI @ DAL)"):
    return {"id": cid, "sport": "NFL", "name": name, "fee": fee, "fee_ok": True,
            "nt": nt, "m": m, "lock": T0 + timedelta(minutes=mins), "bound": 1.0}


DOUBLE = {"tiers": [[1, 10, 2.0]], "resizable": False}


def test_a_qualifying_contest_alerts_with_both_numbers():
    rows = overlay.assess([_found("1", 11, 23, 1.0, 12)], {"1": DOUBLE}, {}, T0, 2.5, 25,
                          0.10, False)
    assert rows[0]["alert"] and rows[0]["now"]["cash"] == pytest.approx(10 / 12)
    out = overlay.alerts_for(rows, {}, T0)
    assert len(out) == 1 and "11/23" in out[0]["body"] and "contest/1" in out[0]["body"]
    assert out[0]["priority"] == "high"        # +67% at lock: an edge this size is worth a loud push
    assert out[0]["final"] is True


def test_a_full_contest_resizable_or_ticket_never_alerts():
    cases = [
        _found("full", 23, 23, 1.0, 28),                                  # at the cap
        _found("rz", 11, 23, 1.0, 28),                                    # resizable below
        _found("tix", 11, 23, 1.0, 28, name="Madden Stream Satellite to $20 NFL"),
    ]
    tables = {"full": DOUBLE, "rz": {**DOUBLE, "resizable": True}, "tix": DOUBLE}
    rows = overlay.assess(cases, tables, {}, T0, 2.5, 25, 0.10, False)
    assert not any(r["alert"] for r in rows)
    assert any(r["ticket"] for r in rows)
    rows = overlay.assess(cases[1:2], tables, {}, T0, 2.5, 25, 0.10, True)
    assert rows[0]["alert"]                                               # --include-resizable


def test_outside_the_window_or_below_threshold_is_quiet():
    later = overlay.assess([_found("1", 11, 23, 1.0, 200)], {"1": DOUBLE}, {}, T0, 2.5, 25,
                           0.10, False)
    # 20 of 23 entered, 10 places: one more entry returns 20/21 -- a loss
    weak = overlay.assess([_found("2", 20, 23, 1.0, 28)], {"2": DOUBLE}, {}, T0, 2.5, 25,
                          0.10, False)
    assert not later and not weak[0]["alert"]


def test_nothing_pushes_before_15_minutes_and_each_contest_pushes_once():
    early = overlay.assess([_found("1", 11, 23, 1.0, 40)], {"1": DOUBLE}, {}, T0, 2.5, 25,
                           0.10, False)
    state = {}
    assert overlay.alerts_for(early, state, T0) == []                     # 40 min out: quiet
    late = overlay.assess([_found("1", 11, 23, 1.0, 15)], {"1": DOUBLE}, {}, T0, 2.5, 25,
                          0.10, False)
    out = overlay.alerts_for(late, state, T0)
    assert len(out) == 1 and out[0]["final"] and out[0]["priority"] == "high"
    state["1"] = {"final": True}
    assert overlay.alerts_for(late, state, T0) == []


def test_history_moves_the_at_lock_numbers():
    hist = {"1": [(T0 - timedelta(minutes=30), 9), (T0, 11)]}
    rows = overlay.assess([_found("1", 11, 23, 1.0, 28)], {"1": DOUBLE}, hist, T0, 2.5, 25,
                          0.10, False)
    assert rows[0]["projected"]["basis"].startswith("2 snapshots")
    assert rows[0]["n_lock"] > 11                                         # filling
    assert rows[0]["at_lock"]["cash"] < rows[0]["now"]["cash"] + 1e-9


# ── polling schedule ───────────────────────────────────────────────────────
def test_a_sport_is_polled_each_run_only_near_its_locks():
    now = T0
    assert overlay.due({}, now, 2.5)                                       # never polled
    recent_far = {"polled": (now - timedelta(minutes=15)).isoformat(),
                  "next_lock": (now + timedelta(hours=8)).isoformat()}
    assert not overlay.due(recent_far, now, 2.5)
    near = {"polled": (now - timedelta(minutes=15)).isoformat(),
            "next_lock": (now + timedelta(hours=1)).isoformat()}
    assert overlay.due(near, now, 2.5)
    stale = {"polled": (now - timedelta(minutes=61)).isoformat(),
             "next_lock": (now + timedelta(hours=8)).isoformat()}
    assert overlay.due(stale, now, 2.5)


def test_timestamps_from_dk_and_from_python_parse():
    assert overlay._iso("2026-10-13T23:00:00.0000000Z") == datetime(2026, 10, 13, 23, tzinfo=timezone.utc)
    assert overlay._iso("2026-10-13T23:00:00+00:00") == datetime(2026, 10, 13, 23, tzinfo=timezone.utc)
    assert overlay._iso(None) is None and overlay._iso("nonsense") is None
