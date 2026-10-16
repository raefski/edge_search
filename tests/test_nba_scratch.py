"""NBA late scratches (harness/nba_scratch.py) and the minutes model behind them
(edge/nba_minutes.py): what fires, once, how loud, and who it names."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from edge import nba_minutes as NM
from harness import nba_scratch as S


def _hist(minutes, started=False, pts_per_min=1.0, n=10):
    return [(float(minutes), started, minutes * pts_per_min) for _ in range(n)]


def _player(pid, name, team="NYK", starter=False, inactive=False, confirmed=True):
    return {"id": pid, "name": name, "team": team, "starter": starter, "position": "",
            "confirmed": confirmed, "inactive": inactive, "at": None}


# ── the minutes model ──────────────────────────────────────────────────────
def test_a_scratched_regulars_minutes_go_to_the_teammates_and_the_team_stays_at_240():
    team = [{"id": str(i), "position": "G", "inactive": i == 0, "starter": None,
             "history": [(30.0 if i < 5 else 18.0, i < 5)] * 10} for i in range(10)]
    proj = NM.project(team)
    assert "0" not in proj                                          # he does not play
    assert sum(p["minutes"] for p in proj.values()) == pytest.approx(240, abs=0.5)
    assert all(p["minutes"] > p["base"] for p in proj.values())     # 30 minutes shared out


def test_a_starting_five_alone_is_not_pushed_to_48_minutes_each():
    """Preseason files list only the starters; 240 over five is 48 apiece."""
    team = [{"id": str(i), "position": "G", "inactive": False, "starter": True,
             "history": [(32.0, True)] * 10} for i in range(5)]
    assert all(p["minutes"] == pytest.approx(32.0) for p in NM.project(team).values())


def test_a_promoted_starter_is_flagged_and_lifted_toward_starter_minutes():
    team = [{"id": "bench", "position": "G", "inactive": False, "starter": True,
             "history": [(16.0, False)] * 10}]
    p = NM.project(team, {"PROMO_LIFT": 0.5})["bench"]
    assert p["promoted"] and p["minutes"] == pytest.approx(16 + 0.5 * (31 - 16))


# ── the alert ──────────────────────────────────────────────────────────────
def test_tip_times_and_surnames():
    assert S.tip_time("7:30 pm ET", date(2026, 10, 15)) == datetime(2026, 10, 15, 23, 30,
                                                                     tzinfo=timezone.utc)
    assert S.tip_time("Final", date(2026, 10, 15)) is None
    assert S._surname("Jabari Smith Jr.") == "Smith" and S._surname("Jalen Brunson") == "Brunson"


def _night(game_id="0022600001", out_star=False, promoted=False):
    star = _player("1", "Jalen Brunson", starter=not out_star, inactive=out_star)
    bench = _player("2", "Miles McBride", starter=promoted)
    others = [_player(str(i), f"Player {i}", starter=(i < 7 and not promoted) or i < 6)
              for i in range(3, 11)]
    return [{"game_id": game_id, "status": "7:30 pm ET", "home": "NYK", "away": "PHI",
             "players": [star, bench] + others}]


HIST = {"1": _hist(36, True, 1.2), "2": _hist(20, False, 1.0),
        **{str(i): _hist(26 if i < 7 else 12, i < 7, 0.9) for i in range(3, 11)}}
DK = {("milesmcbride", "NYK"): {"salary": 3900, "status": None},
      ("jalenbrunson", "NYK"): {"salary": 9800, "status": None}}


def test_a_star_ruled_out_after_the_first_look_fires_and_names_the_cheap_beneficiary():
    _, seen = S.assess(_night(), HIST, {}, DK, {}, prime=True)            # morning: all in
    alerts, _ = S.assess(_night(out_star=True), HIST, {}, DK, seen)
    a = alerts[0]
    assert "🔴 Brunson OUT (inactive list)" in a["title"] and a["team"] == "NYK"
    assert "McBride [NYK] $3,900" in a["body"] and a["priority"] in ("high", "urgent")
    assert "red_circle" in a["tags"] and a["players"] == ["1"]


def test_who_is_already_out_at_the_first_look_is_old_news():
    alerts, _ = S.assess(_night(out_star=True), HIST, {}, DK, {}, prime=True)
    assert not any("OUT" in a["title"] for a in alerts)


def test_a_bench_player_starting_is_news_and_a_cheap_value_jump_is_urgent():
    alerts, _ = S.assess(_night(out_star=True, promoted=True), HIST, {}, DK, {}, prime=True)
    a = next(a for a in alerts if "McBride" in a["body"])
    assert "🟢 McBride STARTS (confirmed)" in a["title"]
    assert a["priority"] == "urgent" and "rotating_light" in a["tags"]   # $3.9k at 6x+


def test_preseason_is_labelled_a_test_and_never_urgent():
    alerts, _ = S.assess(_night(game_id="0012600055", out_star=True, promoted=True), HIST, {},
                         DK, {}, prime=True)
    assert alerts and all(a["title"].startswith("PRESEASON TEST") and a["priority"] == "low"
                          for a in alerts)
