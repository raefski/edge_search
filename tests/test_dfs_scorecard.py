"""scripts/dfs_scorecard.py: the machine-readable DFS scorecard (same numbers as the ROI dashboard)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dfs_scorecard as sc  # noqa: E402


def entry(date, sport, typ, fee, won, cid, field=100, cap=100, overlay=-5.0, beat=0.5):
    return dict(entry=f"{cid}-{date}", date=date, sport=sport, type=typ, contest_id=cid, fee=fee, won=won,
                net=won - fee, cashed=int(won > 0), beat=beat, source="DK history", field=field, cap=cap,
                overlay=overlay, games=4, alerted=None, era="model" if date >= "2026-06-28" else "before")


def test_roi_range_counts_each_slate_once():
    # two entries on one night are one draw: k=2 slates, not 3 entries
    rows = [entry("2026-07-01", "NHL", "cash", 5, 10, 1), entry("2026-07-01", "NHL", "cash", 5, 0, 2),
            entry("2026-07-02", "NHL", "cash", 5, 0, 3)]
    s = sc.roi_stats(rows)
    assert (s["slates"], s["entries"], s["fee"], s["won"]) == (2, 3, 15, 10)
    assert abs(s["roi"] - (10 / 15 - 1)) < 1e-4
    assert s["roi_lo"] < s["roi"] < s["roi_hi"] and s["roi_lo"] >= -1
    assert s["enough_data"] is False


def test_box_matches_d3_quantiles():
    b = sc.box([-1, -1, -1, 0.8, 9.0])
    assert (b["q1"], b["median"], b["q3"]) == (-1, -1, 0.8)
    assert b["outliers"] == 1 and b["whisker_hi"] == 0.8


def test_contest_groups_never_pool_cash_and_gpp():
    entries = [entry(f"2026-07-0{d}", "NFL", t, 5, 9 if d % 2 else 0, d * 10 + i)
               for d in range(1, 7) for i, t in enumerate(("cash", "gpp"))]
    card = sc.scorecard(entries, [], [dict(sport="NFL", slate="1", date="2026-07-01", name=f"QB {p}", pos="QB", proj=p, actual=p + 1)
                                      for p in (10.0, 20.0, 30.0)])
    assert {g["type"] for g in card["contest_groups"]} == {"cash", "gpp"}
    top = next(r for r in card["roi"] if (r["period"], r["sport"], r["type"]) == ("model", "all", "all"))
    assert top["slates"] == 6 and top["enough_data"] is True
    qb = next(r for r in card["projection_accuracy"] if r["sport"] == "NFL" and r["group"] == "QB")
    assert qb["bias"] == 1.0 and qb["mae"] == 1.0 and qb["r2"] == 1.0 and qb["enough_data"] is False


def test_file_is_one_parseable_record_per_line(tmp_path, monkeypatch):
    rows = [entry("2026-07-01", "MLB", "gpp", 1, 0, 1)]
    monkeypatch.setattr(sc.dfs_roi_rows, "build", lambda: rows)
    monkeypatch.setattr(sc.dfs_roi_rows, "updates", lambda: [])
    monkeypatch.setattr(sc.dfs_roi_rows, "proj_rows",
                        lambda: [dict(sport="MLB", slate="d", date="2026-07-01", name="Ace", pos="P", proj=10.0, actual=12.0)])
    out = tmp_path / "card.json"
    monkeypatch.setattr(sys, "argv", ["dfs_scorecard.py", "--out", str(out)])
    sc.main()
    card = json.loads(out.read_text())
    assert card["schema"] == "dfs_scorecard/1" and card["roi"][0]["sport"] == "all"
    assert all(line.startswith('  {"') for line in out.read_text().splitlines() if '"period":' in line)


def test_players_and_three_zoom_levels(capsys):
    entries = [entry(f"2026-07-0{d}", "NCAAF", "gpp", 5, 9 if d % 2 else 0, d) for d in range(1, 7)]
    proj = [dict(sport="NCAAF", slate=str(d), date=f"2026-07-0{d}", name="Jeremiyah Love Jr.", team="ND",
                 pos="RB", salary=8000.0, proj=20.0, actual=20.0 + d, own_gpp=30.0, own_pred=20.0)
            for d in range(1, 7)]
    card = json.loads(json.dumps(sc.scorecard(entries, [], proj)))
    (p,) = card["players"]
    assert (p["slates"], p["bias"], p["own_gpp"], p["enough_data"]) == (6, 3.5, 30.0, True)
    assert len(card["player_games"]) == 6 and card["player_games"][0]["miss"] == 1.0
    sc.show_all(card)
    sc.show_sport(card, "ncaaf")
    sc.show_player(card, "NCAAF", "jeremiyah love")   # suffix and case do not matter
    out = capsys.readouterr().out
    assert "NCAAF" in out and "Jeremiyah Love Jr. (NCAAF RB, ND): 6 slates" in out
