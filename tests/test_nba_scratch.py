"""NBA late scratches (harness/nba_scratch.py) and the minutes model behind them
(edge/nba_minutes.py): what fires, once, how loud, and who it names."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from edge import nba_minutes as NM
from harness import nba_scratch as S


@pytest.fixture(autouse=True)
def _hand_minutes_model(monkeypatch):
    """These tests pin the hand-built minutes model's mechanics on toy teams; the
    gradient-boosted correction (edge/nba_minutes_ml.py) has its own tests."""
    from edge import nba_minutes_ml
    monkeypatch.setattr(nba_minutes_ml, "ENABLED", False)



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


# ── a player in a logged lineup (11/1: Poeltl OUT 8:46 PM went out "high", was missed) ──
_MINE = {("milesmcbride", "NYK"): [(156049, "cash")], ("jalenbrunson", "NYK"): [(156043, "gpp"), (156049, "cash")]}
_DAY = date(2026, 10, 15)
_NOW = datetime(2026, 10, 15, 23, 0, tzinfo=timezone.utc)      # 7:00 pm ET; the game tips 7:30


def _swap_alert(night=None, lineups=_MINE, **kw):
    _, seen = S.assess(_night(), HIST, {}, DK, {}, prime=True)
    alerts, seen = S.assess(night or _night(out_star=True), HIST, {}, DK, seen, lineups=lineups, day=_DAY,
                            labels={"156049": "Night 2g", "156043": "Main 4g"}, now=_NOW, **kw)
    return alerts, seen


def test_an_out_player_in_a_logged_lineup_is_urgent_with_the_deadline_and_a_swap_link():
    a = _swap_alert()[0][0]
    assert a["priority"] == "urgent" and "rotating_light" in a["tags"]
    assert a["title"].startswith("🚨 SWAP BY 7:30 PM") and "Brunson OUT" in a["title"]
    assert "cash + GPP lineup" in a["title"]
    assert "Night 2g cash" in a["body"] and "Main 4g GPP" in a["body"]
    assert "gid=156049" in a["click"] and "swap=1" in a["click"] and "mode=cash" in a["click"]
    assert f"at={int(_NOW.timestamp())}" in a["click"]
    assert a["swap"][0]["id"] == "1" and a["swap"][0]["tip"] == "2026-10-15T23:30:00+00:00"


def test_a_player_not_in_a_logged_lineup_keeps_the_old_alert_and_namesakes_on_other_teams_do_not_match():
    for lineups in (None, {}, {("jalenbrunson", "BOS"): [(156049, "cash")]}):
        a = _swap_alert(lineups=lineups)[0][0]
        assert "SWAP" not in a["title"] and "swap=1" not in a["click"] and not a["swap"]


def test_an_out_player_in_a_lineup_fires_even_if_he_is_no_regular_and_nobody_gains():
    deep = _player("13", "Player 13", inactive=True)
    night = _night()
    night[0]["players"] = [p for p in night[0]["players"] if p["id"] != "13"] + [deep]
    hist = dict(HIST, **{"13": _hist(3, False, 0.5)})
    _, seen = S.assess(_night(), hist, {}, DK, {}, prime=True)
    quiet, _ = S.assess(night, hist, {}, DK, seen)
    assert not quiet                                            # no lineup: not worth a push, as before
    alerts, _ = S.assess(night, hist, {}, DK, seen, lineups={("player13", "NYK"): [(156049, "cash")]},
                         day=_DAY, now=_NOW)
    assert alerts and alerts[0]["priority"] == "urgent" and "Player 13 OUT" not in alerts[0]["title"]
    assert "13 OUT" in alerts[0]["title"]


def test_after_the_tip_there_is_nothing_to_swap_so_no_swap_alert():
    night = _night(out_star=True)
    night[0]["status"] = "Q1 11:02"
    a = _swap_alert(night=night)[0][0]
    assert "SWAP" not in a["title"] and not a["swap"]


def test_a_doubtful_player_in_a_lineup_is_a_swap_alert_too():
    a = _swap_alert(night=_night(), official_doubtful={"jalenbrunson"})[0][0]
    assert a["priority"] == "urgent" and "SWAP BY" in a["title"] and "DOUBTFUL" in a["title"]


def test_preseason_never_gets_a_swap_alert():
    a = _swap_alert(night=_night(game_id="0012600001", out_star=True))[0][0]
    assert a["priority"] == "low" and "SWAP" not in a["title"] and not a["swap"]


def _watch(tip="2026-10-15T23:30:00+00:00", **kw):
    return {"id": "1", "name": "Brunson", "tip": tip, "what": "OUT", "modes": "cash", "gid": 156049,
            "mode": "cash", "team": "NYK", "opp": "PHI", "reminded": False, **kw}


def test_the_reminder_fires_once_in_the_last_five_minutes_while_he_is_still_out():
    seen = {"1": {"out": True}}
    at = lambda h, m: datetime(2026, 10, 15, h, m, tzinfo=timezone.utc)    # noqa: E731
    assert S.swap_reminders([_watch()], seen, at(23, 24)) == []            # too early
    a = S.swap_reminders([_watch()], seen, at(23, 26))[0]
    assert a["priority"] == "urgent" and "LAST CALL" in a["title"] and "7:30 PM" in a["title"]
    assert "gid=156049" in a["click"] and a["watch_id"] == "1"
    assert S.swap_reminders([_watch(reminded=True)], seen, at(23, 26)) == []
    assert S.swap_reminders([_watch()], seen, at(23, 30)) == []            # tipped: too late
    assert S.swap_reminders([_watch()], {"1": {"out": False}}, at(23, 26)) == []    # he is back


def test_an_alert_inside_the_last_five_minutes_is_its_own_reminder():
    a = _swap_alert()[0][0]
    far = S.watch_from(a, datetime(2026, 10, 15, 23, 0, tzinfo=timezone.utc))
    near = S.watch_from(a, datetime(2026, 10, 15, 23, 27, tzinfo=timezone.utc))
    assert far and not far[0]["reminded"] and near and near[0]["reminded"]


def test_the_timer_run_pushes_the_swap_alert_then_one_last_call(monkeypatch):
    # run() end to end, as the timer calls it: it is the one path that only ever runs live.
    from edge import data_branch, dfs_run_nba, nba_availability, nba_injury_report, nba_lineups, nba_rotowire
    state, pushed = {}, []
    night = {"games": _night()}
    clock = {"now": datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc)}
    snap = {"players": {pid: {"hist": rows, "name": f"p{pid}", "team": "NYK", "season_games": 10}
                        for pid, rows in HIST.items()}}
    monkeypatch.setattr(S, "_now", lambda: clock["now"])
    monkeypatch.setattr(S, "fetch", lambda d, use_cache=True: {})
    monkeypatch.setattr(S, "parse", lambda raw: night["games"])
    monkeypatch.setattr(S, "_load_state", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(S, "_save_state", lambda s: (state.clear(), state.update(json.loads(json.dumps(s)))))
    monkeypatch.setattr(S, "dk_players", lambda st, now, day: DK)
    monkeypatch.setattr(S, "_log_changes", lambda *a: {})
    monkeypatch.setattr(S, "_log_news", lambda *a: {})
    monkeypatch.setattr(S, "lineup_index", lambda day: {("jalenbrunson", "NYK"): [(156049, "cash")]})
    monkeypatch.setattr(S.monitor, "push", lambda a: pushed.append(a) or True)
    monkeypatch.setattr(S.monitor, "log", lambda a, path=None: None)
    monkeypatch.setattr(nba_lineups, "write_snapshot", lambda *a: False)
    monkeypatch.setattr(dfs_run_nba, "load_history", lambda: snap)
    monkeypatch.setattr(nba_availability, "tonight_out", lambda: [])
    monkeypatch.setattr(nba_injury_report, "latest", lambda: None)
    monkeypatch.setattr(nba_rotowire, "fetch", lambda: "")
    monkeypatch.setattr(nba_rotowire, "parse", lambda raw: [])

    S.run(do_push=True)                                         # 6:00: the first look, all in
    assert not pushed
    night["games"] = _night(out_star=True)
    clock["now"] = datetime(2026, 10, 15, 23, 10, tzinfo=timezone.utc)
    S.run(do_push=True)                                         # 7:10: ruled out, 20 min to tip
    assert len(pushed) == 1 and pushed[0]["title"].startswith("🚨 SWAP BY 7:30 PM")
    assert pushed[0]["priority"] == "urgent" and "gid=156049" in pushed[0]["click"]
    clock["now"] = datetime(2026, 10, 15, 23, 15, tzinfo=timezone.utc)
    S.run(do_push=True)                                         # 7:15: nothing new
    assert len(pushed) == 1
    clock["now"] = datetime(2026, 10, 15, 23, 26, tzinfo=timezone.utc)
    S.run(do_push=True)                                         # 7:26: the last call
    assert len(pushed) == 2 and "LAST CALL" in pushed[1]["title"]
    clock["now"] = datetime(2026, 10, 15, 23, 28, tzinfo=timezone.utc)
    S.run(do_push=True)                                         # once only
    assert len(pushed) == 2


def test_a_failed_last_call_goes_again_on_the_next_run():
    w = _watch()
    at = datetime(2026, 10, 15, 23, 26, tzinfo=timezone.utc)
    assert S.swap_reminders([w], {"1": {"out": True}}, at)
    assert not w["reminded"]                                    # only run() marks it, once sent
