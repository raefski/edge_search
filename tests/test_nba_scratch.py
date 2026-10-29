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
    roles = [36, 32, 30, 30, 28, 24, 20, 14, 10, 8, 4, 3, 1]            # 240 with everyone in
    team = [{"id": str(i), "position": "G", "inactive": i == 1, "starter": None,
             "history": [(float(m), i < 5)] * 10} for i, m in enumerate(roles)]
    proj = NM.project(team)
    assert "1" not in proj                                          # he does not play
    assert sum(p["minutes"] for p in proj.values()) == pytest.approx(240, abs=0.5)
    assert sum(p["minutes"] - p["base"] for p in proj.values()) == pytest.approx(32, abs=0.6)
    assert proj["0"]["minutes"] - proj["0"]["base"] < proj["8"]["minutes"] - proj["8"]["base"]  # the bench absorbs


def test_a_starting_five_alone_is_not_pushed_to_48_minutes_each():
    """Preseason files list only the starters; 240 over five is 48 apiece."""
    team = [{"id": str(i), "position": "G", "inactive": False, "starter": True,
             "history": [(32.0, True)] * 10} for i in range(5)]
    assert all(p["minutes"] == pytest.approx(32.0) for p in NM.project(team).values())


def test_a_promoted_starter_is_flagged_and_gets_the_starting_role():
    """A bench player who starts tonight takes his STARTING role: with no starts of
    his own, his bench role + SPLIT_GAP (the 2026-10-21 model's start/bench split)."""
    team = [{"id": "bench", "position": "G", "inactive": False, "starter": True,
             "history": [(16.0, False)] * 10}]
    p = NM.project(team)["bench"]
    assert p["promoted"] and p["minutes"] == pytest.approx(16 + NM.SPLIT_GAP, abs=0.1)
    assert NM.project_v1(team, {"PROMO_LIFT": 0.5})["bench"]["minutes"] == pytest.approx(16 + 0.5 * (31 - 16))


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
              for i in range(3, 14)]
    return [{"game_id": game_id, "status": "7:30 pm ET", "home": "NYK", "away": "PHI",
             "players": [star, bench] + others}]


# A real game-night roster: 13 dressed, roles summing to ~233 (the rank-gap allocation
# of 2026-10-21 is fitted on such teams; a 10-man team summing to 208 is not one).
_BENCH = {7: 14, 8: 12, 9: 10, 10: 8, 11: 6, 12: 4, 13: 3}
HIST = {"1": _hist(36, True, 1.2), "2": _hist(20, False, 1.0),
        **{str(i): _hist(30 if i < 7 else _BENCH[i], i < 7, 0.9) for i in range(3, 14)}}
DK = {("milesmcbride", "NYK"): {"salary": 3900, "status": None, "name": "Miles McBride"},
      ("jalenbrunson", "NYK"): {"salary": 9800, "status": None},
      **{(f"player{i}", "NYK"): {"salary": 3000, "status": None} for i in range(7, 14)}}


def test_a_star_ruled_out_after_the_first_look_fires_and_names_the_cheap_beneficiary():
    _, seen = S.assess(_night(), HIST, {}, DK, {}, prime=True)            # morning: all in
    alerts, _ = S.assess(_night(out_star=True), HIST, {}, DK, seen)
    a = alerts[0]
    assert "🔴 Brunson OUT (inactive list)" in a["title"] and a["team"] == "NYK"
    # his minutes go down the rotation: the cheap end of the bench gains most (rank-gap, 10/21)
    assert "[NYK] $3,000" in a["body"] and a["priority"] in ("high", "urgent")
    assert "red_circle" in a["tags"] and a["players"] == ["1"]


def test_who_is_already_out_at_the_first_look_is_old_news():
    alerts, _ = S.assess(_night(out_star=True), HIST, {}, DK, {}, prime=True)
    assert not any("OUT" in a["title"] for a in alerts)


def test_a_bench_player_starting_is_news_and_a_cheap_value_jump_is_urgent():
    alerts, _ = S.assess(_night(out_star=True, promoted=True), HIST, {}, DK, {}, prime=True)
    a = next(a for a in alerts if "McBride" in a["body"])
    assert "🟢 McBride STARTS (confirmed)" in a["title"]
    assert a["priority"] == "urgent" and "rotating_light" in a["tags"]   # $3.9k at 6x+


def _starters_only():
    """NBA.com before rosters post: only the fives; the bench comes from DK (fillers)."""
    g = _night()
    g[0]["players"] = [x for x in g[0]["players"] if x["starter"]]
    return g


# Poole 10/29: off NBA.com's list, Out on the 6:15 PM official report, pushed only at
# 7:51 when the inactive list posted. A DK-filled bench regular now fires on the report.
def test_a_bench_regular_not_on_nba_coms_list_fires_on_the_official_report():
    ids = {"milesmcbride": "2"}
    _, seen = S.assess(_starters_only(), HIST, {}, DK, {}, prime=True, name_ids=ids)
    alerts, _ = S.assess(_starters_only(), HIST, {}, DK, seen, name_ids=ids,
                         official_out={"milesmcbride"})
    assert any("🔴 McBride OUT (official report)" in a["title"] for a in alerts)


def test_doubtful_on_the_official_report_fires_once_then_out_fires_again():
    _, seen = S.assess(_night(), HIST, {}, DK, {}, prime=True)
    alerts, seen = S.assess(_night(), HIST, {}, DK, seen, official_doubtful={"jalenbrunson"})
    assert "🟠 Brunson DOUBTFUL" in alerts[0]["title"] and "orange_circle" in alerts[0]["tags"]
    again, seen = S.assess(_night(), HIST, {}, DK, seen, official_doubtful={"jalenbrunson"})
    assert not again
    out, _ = S.assess(_night(), HIST, {}, DK, seen, official_out={"jalenbrunson"})
    assert "🔴 Brunson OUT (official report)" in out[0]["title"]


def test_preseason_is_labelled_a_test_and_never_urgent():
    alerts, _ = S.assess(_night(game_id="0012600055", out_star=True, promoted=True), HIST, {},
                         DK, {}, prime=True)
    assert alerts and all(a["title"].startswith("PRESEASON TEST") and a["priority"] == "low"
                          for a in alerts)


# ── who reports a scratch first (10/29) ───────────────────────────────────
def test_rotowire_page_parses_team_name_and_status():
    from edge import nba_rotowire as R
    html = ('<button data-team="NOP">x</button>'
            '<li class="lineup__player is-pct-play-0 has-injury-status" title="Very Unlikely To Play">'
            '<div class="lineup__pos">G</div><a title="Dejounte Murray" href="/x">D. Murray</a>'
            '<span class="lineup__inj">Out</span></li>'
            '<li class="lineup__player is-pct-play-100" title="Very Likely To Play">'
            '<a title="Zion Williamson" href="/y">Z. Williamson</a></li>'
            '<button data-team="POR">x</button>'
            '<li class="lineup__player is-pct-play-25 has-injury-status" title="Unlikely To Play">'
            '<a title="Robert Williams" href="/z">R. Williams</a><span class="lineup__inj">Doubt</span></li>')
    assert R.parse(html) == [
        {"team": "NOP", "name": "Dejounte Murray", "status": "Out", "likely": "Very Unlikely To Play"},
        {"team": "POR", "name": "Robert Williams", "status": "Doubtful", "likely": "Unlikely To Play"}]


def test_news_log_records_changes_and_a_failed_source_keeps_its_state(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "NEWS_LOG", tmp_path / "news.csv")
    t = datetime(2026, 10, 29, 22, 0, tzinfo=timezone.utc)
    st = S._log_news({}, t, {"rotowire": [("NOP", "Jordan Poole", "Doubtful")], "official": []})
    st = S._log_news(st, t, {"rotowire": None, "official": []})          # RotoWire down: no rows
    st = S._log_news(st, t, {"rotowire": [("NOP", "Jordan Poole", "Out")], "official": []})
    rows = (tmp_path / "news.csv").read_text().splitlines()
    assert [r.split(",")[-1] for r in rows[1:]] == ["Doubtful", "Out"]
