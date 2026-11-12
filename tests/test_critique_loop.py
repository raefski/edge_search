"""The unattended critique loop's gate and bookkeeping (harness/critique_gate.py,
harness/critique_loop.py). The gate is the only thing standing between an agent's
claim and main, so its refusals are tested one by one."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from harness import critique_gate as G
from harness import critique_loop as C


def _rows(pred_shift=0.0, nights=8, per=40, split="primary", state="confirmed", seed=1, better=None):
    rng = np.random.default_rng(seed)
    out = []
    for n in range(nights):
        for i in range(per):
            act = float(rng.uniform(0, 40))
            noise = float(rng.normal(0, 5))
            p = act + noise
            if better is not None:
                p = act + noise * better
            out.append({"key": f"{split}|{state}|{n}|{i}", "cluster": f"2025-11-{n + 1:02d}", "split": split,
                        "state": state, "pred": p + pred_shift, "actual": act})
    return out


def test_identical_candidate_is_unchanged_not_a_pass():
    b = _rows()
    r = G.compare(b, [dict(x) for x in b])
    assert not r["ok"] and r["groups"][0]["verdict"] == "unchanged"


def test_noise_is_not_a_pass():
    # half the nights a little better, half a little worse: no clear winner
    b = _rows()
    c = [dict(x, pred=x["actual"] + (x["pred"] - x["actual"]) * (0.95 if int(x["cluster"][-2:]) % 2 else 1.05))
         for x in b]
    r = G.compare(b, c)
    assert not r["ok"] and r["groups"][0]["verdict"] == "noise"


def test_consistently_better_candidate_passes():
    b = _rows()
    c = _rows(better=0.8)          # same draws, every error shrunk 20%
    r = G.compare(b, c)
    assert r["ok"], r["reasons"]
    assert r["groups"][0]["verdict"] == "better" and r["groups"][0]["hi"] < 0


def test_worse_candidate_fails():
    r = G.compare(_rows(), _rows(better=1.3))
    assert not r["ok"] and r["groups"][0]["verdict"] == "worse"


def test_changed_truth_or_dropped_rows_fail():
    b, c = _rows(), _rows(better=0.8)
    c[0] = {**c[0], "actual": c[0]["actual"] + 1}
    assert any("truth" in x for x in G.compare(b, c)["reasons"])
    c = _rows(better=0.8)[: int(len(b) * 0.9)]
    assert any("scored" in x for x in G.compare(b, c)["reasons"])


def test_too_few_clusters_is_not_enough_data():
    b = _rows(nights=3)
    r = G.compare(b, _rows(nights=3, better=0.5), min_clusters=5)
    assert not r["ok"] and r["groups"][0]["verdict"] == "not enough data"


def test_guard_period_worse_blocks_a_primary_win():
    b = _rows() + _rows(split="guard", seed=2)
    c = _rows(better=0.8) + _rows(split="guard", seed=2, better=1.3)
    r = G.compare(b, c)
    assert not r["ok"] and any(x.startswith("guard") for x in r["reasons"])
    c = _rows(better=0.8) + _rows(split="guard", seed=2)
    assert G.compare(b, c)["ok"]


def test_a_change_aimed_at_one_state_passes_when_the_others_are_untouched():
    # cycle 25: the Expected-five hedge moved only the live state
    b = _rows() + _rows(state="inactives", seed=3)
    c = _rows(better=0.8) + _rows(state="inactives", seed=3)
    assert G.compare(b, c)["ok"]


def test_another_state_worse_in_its_point_estimate_blocks_the_pass():
    b = _rows() + _rows(state="inactives", seed=3)
    c = _rows(better=0.8) + _rows(state="inactives", seed=3, better=1.0 + 1e-4)
    r = G.compare(b, c)
    assert not r["ok"] and any("inactives" in x for x in r["reasons"])


def test_absolute_loss_and_table():
    b, c = _rows(), _rows(better=0.8)
    r = G.compare(b, c, loss="abs")
    assert r["ok"]
    assert "MAE" in G.table(r, "abs") and "RMSE" in G.table(r, "sq")


def _checkout(tmp_path, name, gate_body, extra=None):
    d = tmp_path / name
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "nba_minutes_ml_fit.py").write_text(f"def gate(out):\n    {gate_body}\n\ndef other():\n    return {extra!r}\n")
    return d


def test_judge_change_is_detected_by_gate_function_only(tmp_path):
    base = _checkout(tmp_path, "base", "return 0", extra=1)
    same = _checkout(tmp_path, "same", "return 0", extra=2)        # other code changed: fine
    edited = _checkout(tmp_path, "edited", "return 1")
    assert not G.judge_changed("nba", base, same)
    assert G.judge_changed("nba", base, edited)


def test_every_rotation_sport_has_a_log_and_gated_sports_have_specs():
    assert set(C.ROTATION) == set(C.LOGS)
    assert {"nba", "ncaab", "nhl", "ncaaf", "nascar"} <= set(G.SPECS)


def test_classify_protects_the_loops_own_judge(tmp_path):
    base = _checkout(tmp_path, "base", "return 0")
    cand = _checkout(tmp_path, "cand", "return 0")
    assert C.classify(["NBA_MINUTES_CRITIQUE_LOG.md", "scripts/nba_minutes_lab.py"], "nba", cand, base) == "record"
    assert C.classify(["edge/nba_minutes.py", "NBA_MINUTES_CRITIQUE_LOG.md"], "nba", cand, base) == "model"
    assert C.classify([".claude/skills/dfs-critique/SKILL.md"], "nba", cand, base) == "protected"
    assert C.classify(["harness/critique_gate.py"], "nba", cand, base) == "protected"
    edited = _checkout(tmp_path, "edited", "return 1")
    assert C.classify(["scripts/nba_minutes_ml_fit.py"], "nba", edited, base) == "protected"


def test_judge_a_record_or_protected_candidate_runs_no_gate(tmp_path, monkeypatch):
    """11/8: a log + lab + tests branch (record) crashed the night on gate["verdict"]."""
    monkeypatch.setattr(C, "worktree", lambda *a, **k: tmp_path)
    monkeypatch.setattr(C, "drop_worktree", lambda *a, **k: None)
    monkeypatch.setattr(C, "link_data", lambda *a, **k: None)
    monkeypatch.setattr(C, "verify", lambda wt, paths: (True, "ok"))
    monkeypatch.setattr(G, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gate ran")))
    for kind, want in (("record", "record"), ("protected", "review")):
        monkeypatch.setattr(C, "classify", lambda *a, _k=kind, **k: _k)
        assert C.judge("nhl", tmp_path, ["NHL_CRITIQUE_LOG.md"])[0] == want


def test_a_candidate_that_breaks_its_own_gate_rows_fails(tmp_path, monkeypatch):
    cand = tmp_path / "cand"
    monkeypatch.setattr(C, "worktree", lambda *a, **k: tmp_path / "main")
    monkeypatch.setattr(C, "drop_worktree", lambda *a, **k: None)
    monkeypatch.setattr(C, "link_data", lambda *a, **k: None)
    monkeypatch.setattr(C, "classify", lambda *a, **k: "model")
    monkeypatch.setattr(G, "run", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError(f"nhl gate rows failed in {cand} (scripts/x.py, rc 1): TypeError")))
    v, gate = C.judge("nhl", cand, ["edge/nhl_sim.py"])
    assert v == "fail" and "TypeError" in gate["reasons"][0]
    monkeypatch.setattr(G, "run", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError(f"nhl gate rows failed in {tmp_path / 'main'} (scripts/x.py, rc 1)")))
    try:
        C.judge("nhl", cand, ["edge/nhl_sim.py"])
        raise AssertionError("main's failure must raise")
    except RuntimeError:
        pass


def test_a_model_change_that_also_fixes_its_lab_goes_to_adam_not_fail(tmp_path, monkeypatch):
    """11/8 NCAAB: a lab fix + model change failed on the lab it corrected."""
    monkeypatch.setattr(C, "worktree", lambda *a, **k: tmp_path)
    monkeypatch.setattr(C, "drop_worktree", lambda *a, **k: None)
    monkeypatch.setattr(C, "link_data", lambda *a, **k: None)
    monkeypatch.setattr(C, "classify", lambda *a, **k: "model")
    monkeypatch.setattr(C, "verify", lambda wt, paths: (True, "ok"))
    for gv, want in (("fail", "review"), ("unchanged", "review"), ("pass", "pass")):
        monkeypatch.setattr(G, "run", lambda *a, _v=gv, **k: {"verdict": _v, "table": "", "reasons": []})
        assert C.judge("ncaab", tmp_path, ["edge/dfs_run_ncaab.py", "scripts/ncaab_lab.py"])[0] == want
    monkeypatch.setattr(G, "run", lambda *a, **k: {"verdict": "fail", "table": "", "reasons": []})
    assert C.judge("ncaab", tmp_path, ["edge/dfs_run_ncaab.py"])[0] == "fail"
    assert "scripts/nhl_skater_lab.py" in G.judge_files("nhl") and G.judge_files("nfl") == set()


def test_a_model_change_aimed_at_a_target_the_gate_does_not_measure_goes_to_adam(tmp_path, monkeypatch):
    """11/8: the scout can aim NBA at cash lineups; the NBA gate measures minutes."""
    monkeypatch.setattr(C, "worktree", lambda *a, **k: tmp_path)
    monkeypatch.setattr(C, "drop_worktree", lambda *a, **k: None)
    monkeypatch.setattr(C, "link_data", lambda *a, **k: None)
    monkeypatch.setattr(C, "classify", lambda *a, **k: "model")
    monkeypatch.setattr(C, "verify", lambda wt, paths: (True, "ok"))
    monkeypatch.setattr(G, "run", lambda *a, **k: {"verdict": "unchanged", "table": "", "reasons": []})
    assert C.judge("nba", tmp_path, ["edge/dfs_run_nba.py"], "cash lineups")[0] == "review"
    assert C.judge("nba", tmp_path, ["edge/dfs_run_nba.py"], "minutes at tip-90")[0] == "unchanged"
    assert C.judge("nba", tmp_path, ["edge/dfs_run_nba.py"])[0] == "unchanged"
    monkeypatch.setattr(G, "run", lambda *a, **k: {"verdict": "pass", "table": "", "reasons": []})
    assert C.judge("nba", tmp_path, ["edge/dfs_run_nba.py"], "cash lineups")[0] == "pass"
    assert all(G.SPECS[s].get("measures") for s in G.SPECS)


def test_rotation_skips_paused_pending_out_of_season_and_done():
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    st = {"next": 0, "sports": {}}
    season = lambda s: s != "mlb"                                    # noqa: E731
    C._sport(st, "nba")["paused_until"] = (now + timedelta(days=3)).isoformat()
    C._sport(st, "ncaab")["pending"] = {"branch": "x"}
    assert C.pick(st, now, season=season) == "nhl"
    night = []
    st["next"] = 0
    while (s := C.pick(st, now, set(night), season=season)):
        night.append(s)
    assert night == ["nhl", "ncaaf", "nascar", "mma"]                # each once, no MLB, no NFL (11/10)
    C._sport(st, "nba")["paused_until"] = (now - timedelta(days=1)).isoformat()
    assert C.pick(st, now, season=season) == "nba"


def test_season_comes_from_the_projection_log(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "dfs_proj_log_nfl.csv").write_text("date,gid,name\n2026-11-05,1,a\n2026-11-02,0,b\n")
    (tmp_path / "data" / "dfs_proj_log_2026-10-15_g155140.csv").write_text("x\n")
    monkeypatch.setattr(C, "ROOT", tmp_path)
    assert C.last_slate("nfl") == "2026-11-05" and C.last_slate("mlb") == "2026-10-15"
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    assert C.in_season("nfl", now) and not C.in_season("mlb", now) and not C.in_season("mma", now)


def test_claude_binary_override(monkeypatch):
    monkeypatch.setenv("EDGE_CLAUDE", "/opt/claude")
    assert C.claude_bin() == "/opt/claude"


def test_link_data_copies_small_and_lab_caches_links_big(tmp_path, monkeypatch):
    root, wt = tmp_path / "root", tmp_path / "wt"
    (root / "data" / "cache" / "nba_minutes_lab").mkdir(parents=True)
    (root / "data" / "cache" / "nba_minutes_lab" / "k.json").write_text("x" * 50)
    (root / "data" / "small.csv").write_text("a")
    (root / "data" / "big.db").write_text("y" * 200)
    wt.mkdir()
    monkeypatch.setattr(C, "ROOT", root)
    monkeypatch.setattr(C, "LINK_ABOVE", 100)
    monkeypatch.setattr(C, "ALWAYS_COPY", ("data/cache/nba_minutes_lab/",))
    monkeypatch.setattr(C, "git", lambda *a, **k: "data/cache/nba_minutes_lab/\ndata/small.csv\ndata/big.db")
    C.link_data(wt)
    assert (wt / "data" / "big.db").is_symlink()
    assert not (wt / "data" / "small.csv").is_symlink() and (wt / "data" / "small.csv").read_text() == "a"
    assert not (wt / "data" / "cache" / "nba_minutes_lab").is_symlink()
    assert (wt / "data" / "cache" / "nba_minutes_lab" / "k.json").exists()


def test_refresh_replaces_a_stale_copy_with_the_current_one(tmp_path, monkeypatch):
    root, wt = tmp_path / "root", tmp_path / "wt"
    (root / "data" / "nba_injury_reports").mkdir(parents=True)
    (root / "data" / "nba_injury_reports" / "a.pdf").write_text("a")
    wt.mkdir()
    monkeypatch.setattr(C, "ROOT", root)
    monkeypatch.setattr(C, "git", lambda *a, **k: "data/nba_injury_reports/")
    C.link_data(wt)
    (root / "data" / "nba_injury_reports" / "b.pdf").write_text("b")     # a report lands later
    C.link_data(wt)
    assert not (wt / "data" / "nba_injury_reports" / "b.pdf").exists()   # frozen copy
    C.link_data(wt, refresh=True)
    assert (wt / "data" / "nba_injury_reports" / "b.pdf").exists()


def test_ledger_rows_keep_every_cycle_and_a_merge_updates_its_cycle(tmp_path):
    import sys
    sys.path.insert(0, str(C.ROOT / "scripts"))
    import critique_ledger_rows as R
    g = [{"split": "primary", "state": "tip-90 expected", "n": 100, "clusters": 14, "base": 36.0, "cand": 25.0,
          "d": -11.0, "lo": -15.0, "hi": -5.0, "verdict": "better"},
         {"split": "primary", "state": "confirmed", "n": 900, "clusters": 160, "base": 36.0, "cand": 36.0,
          "d": 0.0, "lo": 0.0, "hi": 0.0, "verdict": "unchanged"}]
    led = [{"at": "2026-11-08T08:00:00+00:00", "event": "cycle", "sport": "nba", "verdict": "pass",
            "hole": "h1", "loss": "sq", "groups": g},
           {"at": "2026-11-08T09:00:00+00:00", "event": "cycle", "sport": "nhl", "verdict": "fail",
            "hole": "h2", "loss": "sq", "groups": []},
           {"at": "2026-11-15T08:00:00+00:00", "event": "merged", "sport": "nba", "hole": "h1"}]
    rows = R.rows(led)
    assert [r["sport"] for r in rows] == ["NBA", "NHL"]                 # nothing hidden
    nba, nhl = rows
    assert nba["result"] == "Shipped" and nba["state"] == "tip-90 expected"
    assert nba["before"] == 6.0 and nba["after"] == 5.0 and nba["beyond_noise"]
    assert nhl["result"] == "Not shipped" and nhl["before"] is None


def test_the_0705_run_skips_sports_already_run_tonight():
    now = datetime(2026, 11, 10, 12, 5, tzinfo=timezone.utc)
    st = {"next": 0, "sports": {}}
    C._sport(st, "nba")["history"].append({"at": "20261110-0700", "outcome": "fail"})
    C._sport(st, "nhl")["history"].append({"at": "20261110-0900", "outcome": "regate pass"})
    C._sport(st, "ncaaf")["history"].append({"at": "20261109-0700", "outcome": "fail"})   # last night
    assert C.ran_tonight(st, now) == {"nba"}
    season = lambda s: s != "mlb"                                    # noqa: E731
    assert C.pick(st, now, C.ran_tonight(st, now), season=season) == "ncaab"


def test_the_5_hour_limit_message_is_a_usage_stop_with_its_reset_time():
    """11/9 06:31: this exact text was logged as two failed cycles instead of a stop."""
    msg = "You've hit your 5-hour limit · resets 7:50am (America/New_York)"
    assert C.re.search(r"hit your .{0,20}limit|\d-hour limit", msg, C.re.I)
    t = C.reset_time(msg, datetime(2026, 11, 9, 6, 31))
    assert (t.hour, t.minute, t.day) == (7, 50, 9)
    assert C.reset_time("resets 12:15am", datetime(2026, 11, 9, 23, 0)).day == 10
    assert C.reset_time("no time here") is None


def test_one_sport_a_night_and_nfl_skipped(monkeypatch):
    now = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)
    st = {"next": C.ROTATION.index("nfl"), "sports": {}}
    assert C.pick(st, now, season=lambda s: True) == "mma"          # NFL left out (11/10)
    assert C.MAX_PER_NIGHT == 1


def test_a_queued_target_runs_first_through_pause_and_skip_but_not_past_pending():
    """Adam 2026-11-11: "NHL goalie question should be tonight's NHL critique target"."""
    now = datetime(2026, 11, 12, 7, 0, tzinfo=timezone.utc)
    season = lambda s: s != "mlb"                                    # noqa: E731
    st = {"next": 0, "sports": {}}
    C._sport(st, "nhl")["paused_until"] = (now + timedelta(days=3)).isoformat()
    C.enqueue(st, "nhl", "  availability   goalies ")
    assert st["queue"]["nhl"]["target"] == "availability goalies"
    assert C.pick(st, now, season=season) == "nhl" and st["next"] == 0     # rotation untouched
    assert C.pick(st, now, {"nhl"}, season=season) == "nba"                # then the rotation
    C.enqueue(st, "nfl", "rb")                                             # NFL is in SKIP
    assert C.pick(st, now, {"nhl"}, season=season) == "nfl"
    C._sport(st, "nhl")["pending"] = {"branch": "x"}
    st["next"] = 0
    assert C.pick(st, now, {"nfl"}, season=season) == "nba"                # pending blocks it
    assert "queued: nhl -> 'availability goalies'" in C.status(st)
    for bad in (("xfl", "t"), ("nhl", "   ")):
        try:
            C.enqueue(st, *bad)
            raise AssertionError("accepted a bad queue entry")
        except ValueError:
            pass


def test_a_queued_target_replaces_auto_and_is_used_once(tmp_path, monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, '{"result": "ok"}', ""
    monkeypatch.setattr(C, "RUNS", tmp_path)
    monkeypatch.setattr(C, "claude_bin", lambda: "claude")
    monkeypatch.setattr(C.subprocess, "run", lambda cmd, **k: calls.append(cmd) or R())
    C.run_agent("nhl", tmp_path, "s1", "availability goalies")
    C.run_agent("nba", tmp_path, "s2")
    assert calls[0][2] == "/dfs-critique nhl availability goalies unattended"
    assert calls[1][2] == "/dfs-critique nba auto unattended"
    # cycle(): the queue entry is consumed when the cycle ran, kept on a usage-limit stop
    st = {"next": 0, "sports": {}}
    C.enqueue(st, "nhl", "availability goalies")
    monkeypatch.setattr(C, "worktree", lambda *a: tmp_path)
    monkeypatch.setattr(C, "drop_worktree", lambda *a: None)
    monkeypatch.setattr(C, "changed", lambda wt: [])
    monkeypatch.setattr(C, "ledger", lambda e: None)
    monkeypatch.setattr(C, "notify", lambda *a: None)
    seen = []
    monkeypatch.setattr(C, "run_agent", lambda s, wt, stamp, target=None: seen.append(target) or
                        {"rc": 1, "claim": {}, "limited": True, "resets": None, "cost": None, "minutes": 1})
    assert C.cycle("nhl", st) == "usage limit" and "nhl" in st["queue"]
    monkeypatch.setattr(C, "run_agent", lambda s, wt, stamp, target=None: seen.append(target) or
                        {"rc": 0, "claim": {}, "limited": False, "resets": None, "cost": None, "minutes": 1})
    assert C.cycle("nhl", st) == "no commits" and "nhl" not in st["queue"]
    assert seen == ["availability goalies", "availability goalies"]
