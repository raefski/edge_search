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
    rows = overlay.assess([_found("1", 11, 23, 1.0, 28)], {"1": DOUBLE}, {}, T0, 2.5, 25,
                          0.10, False)
    assert rows[0]["alert"] and rows[0]["now"]["cash"] == pytest.approx(10 / 12)
    out = overlay.alerts_for(rows, {}, T0)
    assert len(out) == 1 and "11/23" in out[0]["body"] and "contest/1" in out[0]["body"]
    assert out[0]["priority"] == "high"        # +67% at lock: an edge this size is worth a loud push
    assert out[0]["final"] is False


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


def test_each_contest_pushes_once_then_a_final_call_inside_20_minutes():
    first = overlay.assess([_found("1", 11, 23, 1.0, 40)], {"1": DOUBLE}, {}, T0, 2.5, 25,
                           0.10, False)
    state = {}
    assert len(overlay.alerts_for(first, state, T0)) == 1
    state["1"] = {"final": False}
    assert overlay.alerts_for(first, state, T0) == []                     # not final yet
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


# ── the late fill (2026-10-15) ─────────────────────────────────────────────
from edge.dfs_entry_edge import (TAIL_DEFAULT, fit_late_fill, late_fill_share,  # noqa: E402
                                 likely_at_lock)

#: "CFB $1K Wednesday Night Kickoff", 10/14: DK's real table, $15, 78 cap.
CFB_TIERS = [[1, 1, 175.0], [2, 2, 125.0], [3, 3, 100.0], [4, 4, 75.0], [5, 5, 60.0],
             [6, 7, 45.0], [8, 10, 40.0], [11, 13, 35.0], [14, 18, 30.0]]
#: A model shaped like the 10/11-10/15 fit: emptier contests fill more of their room.
MODEL = {"curves": {(None, None, 0): [(6.0, 0.42), (15.0, 0.45), (30.0, 0.47), (62.5, 0.6),
                                      (97.5, 0.75)],
                    (None, None, 1): [(6.0, 0.2), (15.0, 0.23), (30.0, 0.31), (62.5, 0.48),
                                      (97.5, 0.54)],
                    (None, None, 2): [(6.0, 0.2), (15.0, 0.25), (30.0, 0.38), (62.5, 0.5),
                                      (97.5, 0.6)]},
         "tail": TAIL_DEFAULT, "tails": {}, "tail_n": 0, "n": 900}


def _contest(cap, pts):
    return {"cap": cap, "points": pts}


def test_the_fit_reads_the_share_of_empty_room_and_never_shrinks_with_more_time():
    # 20 contests, each 10/100 at 20 min, 40/100 at 10 min, 70/100 at 3 min.
    cs = {str(i): _contest(100, {20.0: 10, 10.0: 40, 3.0: 70}) for i in range(20)}
    m = fit_late_fill(cs)
    assert m["n"] == 20 and m["tail"] == TAIL_DEFAULT and m["tail_n"] == 0
    low = dict(m["curves"][(None, None, 0)])          # 10% full -> band 0
    assert low[21.5] == pytest.approx(60 / 90)                    # (70-10)/(100-10)
    shares = [s for _, s in m["curves"][(None, None, 0)]]
    assert shares == sorted(shares)                               # monotone in minutes


def test_a_contest_not_seen_near_lock_says_nothing_about_how_it_finished():
    cs = {str(i): _contest(100, {30.0: 10, 15.0: 20}) for i in range(20)}  # last seen 15 min out
    assert fit_late_fill(cs)["n"] == 0


def test_the_last_minutes_share_comes_from_finals_once_there_are_five():
    cs = {str(i): _contest(100, {3.0: 50}) for i in range(6)}
    finals = {str(i): 75 for i in range(4)}
    assert fit_late_fill(cs, finals)["tail"] == TAIL_DEFAULT                # 4: not enough
    finals = {str(i): 75 for i in range(6)}
    m = fit_late_fill(cs, finals)
    assert m["tail_n"] == 6 and m["tail"] == pytest.approx(0.5)             # 25 of 50 empty


def test_the_cfb_contest_is_no_longer_sold_as_plus_495():
    """6/78 at 16 min, flat. The straight line said 6 at lock (+495%); it locked at 52."""
    n = likely_at_lock(MODEL, 6, 78, 16)
    assert 40 <= n <= 65
    # inside the last snapshot's minutes only part of the tail is left
    assert likely_at_lock(MODEL, 30, 78, 2.8) == pytest.approx(30 + 0.46 * 48 * 2.8 / 3, abs=1)
    found = [{"id": "196751376", "sport": "CFB", "name": "CFB $1K Wednesday Night Kickoff",
              "fee": 15.0, "fee_ok": True, "nt": 6, "m": 78, "lock": T0 + timedelta(minutes=16),
              "bound": 1.0}]
    hist = {"196751376": [(T0 - timedelta(minutes=30), 6), (T0, 6)]}
    tables = {"196751376": {"tiers": CFB_TIERS, "resizable": False}}
    old = overlay.assess(found, tables, hist, T0, 2.5, 25, 0.10, False)[0]
    new = overlay.assess(found, tables, hist, T0, 2.5, 25, 0.10, False, MODEL)[0]
    assert old["n_lock"] == 6 and old["at_lock"]["roi"] > 4.0               # the old +495%
    assert new["n_lock"] == n and 0.0 < new["at_lock"]["roi"] < 0.5           # real lock: +28%
    body = overlay.alerts_for([new], {}, T0)[0]["body"]
    assert f"Expect ~{n}/78" in body and "Best case, if no one else enters" in body
    assert "how all sports contests" in body                 # MODEL has only the broad level


def test_an_edge_that_only_exists_if_nobody_else_enters_does_not_alert():
    """11 of 23 in a $1 double-up 40 min out: +67% if it stayed there, but a field
    that fills like the usual one leaves nothing."""
    rows = overlay.assess([_found("1", 11, 23, 1.0, 40)], {"1": DOUBLE}, {}, T0, 2.5, 25,
                          0.10, False, MODEL)
    assert rows[0]["now"]["roi"] > 0.5 and rows[0]["n_lock"] > 15 and not rows[0]["alert"]


def test_the_model_never_projects_below_the_contests_own_straight_line():
    hist = {"1": [(T0 - timedelta(minutes=30), 2), (T0, 11)]}               # fast: 0.3/min
    flat = {"curves": {(None, None, b): [(6.0, 0.0), (240.0, 0.0)] for b in range(3)},
            "tail": 0.0}
    row = overlay.assess([_found("1", 11, 40, 1.0, 28)], {"1": DOUBLE}, hist, T0, 2.5, 25,
                         0.10, False, flat)[0]
    assert row["n_lock"] == row["n_floor"] > 11


def test_finals_are_looked_up_for_every_unfilled_contest_overlay_candidates_first(tmp_path,
                                                                                  monkeypatch):
    """Adam, 10/15: track the overlay whether or not he entered."""
    monkeypatch.setattr(overlay, "FINALS", tmp_path / "contest_finals.csv")
    monkeypatch.setattr(overlay, "FINALS_PACE_S", 0)
    now = T0
    def c(lock_ago_min, last_entries, cap=78, fee=15.0, pool=1000.0):
        return {"cap": cap, "points": {3.0: last_entries}, "lock": now - timedelta(minutes=lock_ago_min),
                "sport": "CFB", "name": "x", "fee": fee, "prize_pool": pool}
    logged = {"rake": c(30, 70),                 # 70 x $15 = $1,050 in: no overlay pending
              "overlay": c(40, 30),              # $450 in for a $1,000 pool
              "full": c(30, 78), "early": c(5, 10), "old": c(60 * 13, 10), "known": c(30, 10)}
    asked = []
    def fake(cid):
        asked.append(cid)
        return {"field": 52}
    rows = overlay.collect_finals(logged, {"known": 40}, now, lookup=fake)
    assert asked == ["overlay", "rake"]                   # candidates first, full/early/old/known never
    got = {r["contest_id"]: r for r in rows}
    assert got["overlay"]["overlay"] == pytest.approx(1000 - 52 * 15)    # $220, the CFB contest
    assert got["overlay"]["weekday"] == "Tue"
    assert overlay.load_finals() == {"overlay": 52, "rake": 52}
    # DK refusing is not fatal: nothing is written and the next run tries again
    def refuse(cid):
        raise RuntimeError("403")
    assert overlay.collect_finals({"thin2": c(30, 20)}, {}, now, lookup=refuse) == []
    # the report counts every locked contest, entered or not
    lines = overlay.report(logged, now)
    cfb = next(l for l in lines if l.startswith("CFB"))
    assert cfb.split()[2:6] == ["6", "1", "2", "2"]       # locked, full, final known, overlay


def test_the_log_loader_keeps_minutes_to_lock_and_the_cap(tmp_path, monkeypatch):
    log = tmp_path / "fill.csv"
    cols = ("seen_at", "sport", "contest_id", "template", "draft_group", "name", "fee",
            "max_entries", "entries", "prize_pool", "lock", "double_up", "game_type")
    lines = [",".join(cols),
             "2026-10-14T23:14:38+00:00,CFB,1,t,d,x,15.0,78,7,1000.0,2026-10-14T23:30:00+00:00,0,Classic",
             "2026-10-14T23:27:14+00:00,CFB,1,t,d,x,15.0,78,30,1000.0,2026-10-14T23:30:00+00:00,0,Classic"]
    log.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(overlay.O, "FILL_LOG", log)
    hist, logged = overlay.load_log()
    assert [e for _, e in hist["1"]] == [7, 30]
    assert logged["1"]["cap"] == 78
    assert sorted(round(m, 1) for m in logged["1"]["points"]) == [2.8, 15.4]


def test_forecasts_use_the_pooled_curve_until_stratification_is_switched_on():
    tue = datetime(2026, 10, 13, 23, 0, tzinfo=timezone.utc)
    cs = {f"t{i}": {"cap": 100, "points": {20.0: 10, 3.0: 82}, "sport": "NHL", "lock": tue}
          for i in range(40)}
    cs.update({f"n{i}": {"cap": 100, "points": {20.0: 10, 3.0: 28}, "sport": "NFL",
                         "lock": tue - timedelta(days=2)} for i in range(40)})
    m = fit_late_fill(cs)
    from edge.dfs_entry_edge import fill_basis
    assert late_fill_share(m, 0.1, 21.5, "NHL", "Tue") == late_fill_share(m, 0.1, 21.5)
    assert fill_basis(m, 10, 100, "NHL", "Tue") == "all sports"


def test_sport_and_weekday_get_their_own_fill_when_they_have_the_contests(monkeypatch):
    from edge import dfs_entry_edge
    monkeypatch.setattr(dfs_entry_edge, "STRATIFY", True)
    """Adam, 10/15: NFL on a Sunday is not NHL on a Tuesday. A (sport, day) cell with
    enough contests is used; a thin one falls back to the sport, then to everyone."""
    tue = datetime(2026, 10, 13, 23, 0, tzinfo=timezone.utc)            # 7 pm ET Tuesday
    wed = tue + timedelta(days=1)
    cs = {}
    for i in range(12):                                                  # NHL Tue: fills a lot
        cs[f"t{i}"] = {"cap": 100, "points": {20.0: 10, 3.0: 82}, "sport": "NHL", "lock": tue}
    for i in range(3):                                                   # NHL Wed: too few
        cs[f"w{i}"] = {"cap": 100, "points": {20.0: 10, 3.0: 19}, "sport": "NHL", "lock": wed}
    for i in range(12):                                                  # NFL Sun: fills little
        cs[f"n{i}"] = {"cap": 100, "points": {20.0: 10, 3.0: 28}, "sport": "NFL",
                       "lock": tue - timedelta(days=2)}
    m = fit_late_fill(cs)
    tue_share = late_fill_share(m, 0.1, 21.5, "NHL", "Tue")
    nfl_share = late_fill_share(m, 0.1, 21.5, "NFL", "Sun")
    everyone = late_fill_share(m, 0.1, 21.5)
    # 12 contests each: pulled toward everyone's share (SHRINK_K), but on their own side
    assert nfl_share <= everyone < tue_share
    assert 0.2 <= nfl_share and tue_share < 0.8
    # 600 NFL Sundays mostly speak for themselves
    for i in range(600):
        cs[f"N{i}"] = {"cap": 100, "points": {20.0: 10, 3.0: 28}, "sport": "NFL",
                       "lock": tue - timedelta(days=2)}
    big = fit_late_fill(cs)
    assert late_fill_share(big, 0.1, 21.5, "NFL", "Sun") == pytest.approx(0.2, abs=0.02)
    # Wednesday has 3 contests of its own: it borrows NHL's (pooled Tue + Wed) median
    assert ("NHL", "Wed", 0) in m["curves"]
    from edge.dfs_entry_edge import fill_basis
    assert fill_basis(m, 10, 100, "NHL", "Tue") == "NHL Tue"
    assert late_fill_share(m, 0.1, 21.5, "NHL", "Wed") == pytest.approx(
        late_fill_share(m, 0.1, 21.5, "NHL", None))
    assert fill_basis(m, 10, 100, "MMA", "Sat") == "all sports"
    assert likely_at_lock(m, 10, 100, 21.5, "NHL", "Tue") > likely_at_lock(m, 10, 100, 21.5,
                                                                             "NFL", "Sun")


def test_a_contest_that_leaves_the_lobby_nearly_full_counts_as_filled(tmp_path, monkeypatch):
    """DK stops listing a full contest. One last seen 30 min out at 95% while its
    siblings were seen 3 min out filled; one last seen at 20% is unknown, not full."""
    lock = T0
    def c(points, cap=100):
        return {"cap": cap, "points": dict(points), "lock": lock, "sport": "NHL", "name": "x",
                "fee": 1.0, "prize_pool": 100.0}
    cs = {"sib": c({30.0: 40, 3.0: 60}), "full": c({60.0: 70, 30.0: 95}),
          "thin": c({60.0: 10, 30.0: 20})}
    overlay.mark_vanished(cs)
    assert cs["full"]["filled"] and cs["full"]["points"][0.0] == 100
    assert cs["thin"]["vanished"] and not cs["thin"].get("filled") and 0.0 not in cs["thin"]["points"]
    assert not cs["sib"]["vanished"]
    # the inferred-full one is only spot-checked; the thin one is looked up
    monkeypatch.setattr(overlay, "FINALS", tmp_path / "f.csv")
    monkeypatch.setattr(overlay, "FINALS_PACE_S", 0)
    asked = []
    overlay.collect_finals(cs, {}, lock + timedelta(minutes=30),
                           lookup=lambda cid: asked.append(cid) or {"field": 100})
    assert set(asked) == {"sib", "thin", "full"} and asked[-1] == "full"
    monkeypatch.setattr(overlay, "VANISH_SPOT_CHECKS", 0)
    asked.clear()
    overlay.collect_finals(cs, {}, lock + timedelta(minutes=30),
                           lookup=lambda cid: asked.append(cid) or {"field": 100})
    assert "full" not in asked
