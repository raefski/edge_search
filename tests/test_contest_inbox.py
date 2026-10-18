"""scripts/dfs_import_contests.py's drop folders (data/contest_inbox/<sport>/<cash|gpp>/)
and edge.dfs_contest's draft-group slate match, on synthetic exports with a fake DK
lookup -- no network, no dependence on which real exports exist."""
from __future__ import annotations

import json
import zipfile

import pytest

from edge.dfs_contest import export_tag, sport_exports, tagged_slate
from scripts import dfs_import_contests as imp

CSV = b"\xef\xbb\xbfRank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS\n" \
      b"1,11,gorillabiscuit,0,150.5,C Aa One,,Aa One,C,75.00%,30\n"


def info(sport="NHL", ctype="gpp", gid=154755):
    return {"type": ctype, "sport": sport, "field": 549, "max_entries": 549, "entry_fee": 1,
            "places_paid": 136, "prize_pool": 500.0, "draft_group": gid, "name": f"{sport} test",
            "guaranteed": True, "start": "2026-10-16T23:00:00.0000000Z", "state": "Completed"}


@pytest.fixture
def box(tmp_path):
    inbox, data = tmp_path / "contest_inbox", tmp_path / "data"
    for s in imp.SPORTS:
        for t in imp.TYPES:
            (inbox / s / t).mkdir(parents=True)
            (inbox / s / t / ".gitkeep").touch()
    data.mkdir()
    return inbox, data


def fake(answers: dict, calls: list):
    def look(cid):
        calls.append(cid)
        if isinstance(answers.get(cid), Exception):
            raise answers[cid]
        return answers[cid]
    return imp.Lookups(look, gap=0)


def zipped(path, body=CSV, inner="contest-standings-1.csv"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(inner, body)


# ------------------------------------------------------------------ names + labels
def test_export_id_reads_dk_names_and_ignores_zone_streams():
    assert imp.export_id("contest-standings-196766174.zip") == "196766174"
    assert imp.export_id("contest-standings-196164828 (1).zip") == "196164828"
    assert imp.export_id("contest-standings-186465288_2025-12-25.csv") == "186465288"
    assert imp.export_id("contest-standings-196766174.zip:Zone.Identifier") is None
    assert imp.export_id("nhl gpp.csv") is None


def test_drop_label_from_folders(box):
    inbox, _ = box
    assert imp.drop_label(inbox / "nhl" / "gpp" / "x.zip", inbox) == ("NHL", "gpp")
    assert imp.drop_label(inbox / "CFB" / "Cash" / "x.zip", inbox) == ("NCAAF", "cash")
    assert imp.drop_label(inbox / "gpp" / "nba" / "x.zip", inbox) == ("NBA", "gpp")
    assert imp.drop_label(inbox / "nhl" / "x.zip", inbox) == ("NHL", None)
    assert imp.drop_label(inbox / "x.zip", inbox) == (None, None)


def test_export_bytes_zip_csv_folder_and_junk(tmp_path):
    z = tmp_path / "contest-standings-1.zip"
    zipped(z)
    c = tmp_path / "contest-standings-2.csv"
    c.write_bytes(CSV)
    d = tmp_path / "contest-standings-3"
    d.mkdir()
    (d / "contest-standings-3.csv").write_bytes(CSV)
    junk = tmp_path / "contest-standings-4.csv"
    junk.write_bytes(b"<html>login</html>")
    broken = tmp_path / "contest-standings-5.zip"
    broken.write_bytes(b"PK\x03\x04 truncated")
    assert imp.export_bytes(z) == imp.export_bytes(c) == imp.export_bytes(d) == CSV
    assert imp.export_bytes(junk) is None
    assert imp.export_bytes(broken) is None


# ------------------------------------------------------------------ the import
def test_inbox_files_tags_and_empties(box):
    inbox, data = box
    zipped(inbox / "nhl" / "gpp" / "contest-standings-196766174.zip")
    (inbox / "nhl" / "gpp" / "contest-standings-196766174.zip:Zone.Identifier").write_text("[ZoneTransfer]")
    meta, calls = {}, []
    out = imp.import_inbox(inbox, data, meta, fake({"196766174": info()}, calls))
    assert [r["status"] for r in out] == ["filed"] and not out[0]["note"]
    assert (data / "contest-standings-196766174.csv").read_bytes() == CSV
    assert meta["196766174"]["draft_group"] == 154755
    assert meta["196766174"]["slate"] == "2026-10-16 (gid 154755)"
    assert "dropped_as" not in meta["196766174"]
    assert imp.inbox_files(inbox) == []
    assert not list(inbox.rglob("*Zone.Identifier"))
    assert (inbox / "nhl" / "gpp" / ".gitkeep").exists()          # the folders stay


def test_dk_beats_the_folder_and_says_so(box):
    """2026-10-14: two NFL exports handed over as NHL were graded against NHL nights."""
    inbox, data = box
    (inbox / "nhl" / "gpp" / "contest-standings-196420207.csv").write_bytes(CSV)
    meta, calls = {}, []
    out = imp.import_inbox(inbox, data, meta, fake({"196420207": info("NFL", "cash", 154468)}, calls))
    assert out[0]["status"] == "filed"
    assert "NFL, not NHL" in out[0]["note"] and "cash, not gpp" in out[0]["note"]
    assert meta["196420207"]["sport"] == "NFL" and meta["196420207"]["type"] == "cash"
    assert meta["196420207"]["dropped_as"] == "nhl/gpp"


def test_failed_lookup_leaves_the_file_and_stops_asking(box):
    inbox, data = box
    (inbox / "nhl" / "cash" / "contest-standings-196766189.csv").write_bytes(CSV)
    (inbox / "nhl" / "gpp" / "contest-standings-196766174.csv").write_bytes(CSV)
    meta, calls = {}, []
    out = imp.import_inbox(inbox, data, meta, fake({"196766189": RuntimeError("403")}, calls))
    assert [r["status"] for r in out] == ["left", "left"]
    assert calls == ["196766189"]                                  # a 403 means stop
    assert not list(data.glob("*.csv")) and meta == {}
    assert len(imp.inbox_files(inbox)) == 2


def test_unusable_files_stay_with_a_reason(box):
    inbox, data = box
    (inbox / "nfl" / "gpp" / "my nfl gpp.csv").write_bytes(CSV)
    (inbox / "nfl" / "gpp" / "contest-standings-196000001.csv").write_bytes(b"not an export")
    out = imp.import_inbox(inbox, data, {}, fake({}, []))
    assert {r["status"] for r in out} == {"left"}
    assert any("no contest id" in r["note"] for r in out)
    assert any("Rank header" in r["note"] for r in out)


def test_redrop_identical_needs_no_lookup_and_a_changed_file_replaces(box):
    inbox, data = box
    dest = data / "contest-standings-196766174.csv"
    dest.write_bytes(CSV)
    meta = {"196766174": {**{k: v for k, v in info().items() if k != "start"}, "slate": "x"}}
    (inbox / "nhl" / "gpp" / "contest-standings-196766174 (1).csv").write_bytes(CSV)
    calls = []
    out = imp.import_inbox(inbox, data, meta, fake({}, calls))
    assert out[0]["status"] == "already filed" and calls == []
    assert imp.inbox_files(inbox) == []
    newer = CSV + b"2,12,someone,0,140.0,C Bb Two,,,,,\n"
    (inbox / "nhl" / "gpp" / "contest-standings-196766174.csv").write_bytes(newer)
    out = imp.import_inbox(inbox, data, meta, fake({"196766174": info()}, calls))
    assert out[0]["status"] == "replaced" and dest.read_bytes() == newer


def test_extract_all_folder_is_pruned(box):
    inbox, data = box
    folder = inbox / "mma" / "gpp" / "contest-standings-196164828"
    folder.mkdir()
    (folder / "contest-standings-196164828.csv").write_bytes(CSV)
    out = imp.import_inbox(inbox, data, {}, fake({"196164828": info("MMA", "gpp", 154125)}, []))
    assert out[0]["status"] == "filed"
    assert not folder.exists() and (inbox / "mma" / "gpp").exists()


def test_lookups_are_spaced_and_cached():
    waits, calls = [], []
    look = imp.Lookups(lambda c: calls.append(c) or {"cid": c}, gap=5.0, sleep=waits.append)
    look("1"), look("2"), look("1")
    assert calls == ["1", "2"]                     # each id asked once
    assert len(waits) == 1 and 4.0 < waits[0] <= 5.0


def test_pending_lists_inbox_and_untagged(box, tmp_path):
    inbox, data = box
    (inbox / "nba" / "gpp" / "contest-standings-197000001.zip").write_bytes(b"x")
    (data / "contest-standings-197000002.csv").write_bytes(CSV)
    (data / "contest-standings-197000003.csv").write_bytes(CSV)
    mp = tmp_path / "meta.json"
    mp.write_text(json.dumps({"197000003": {"sport": "NBA", "draft_group": 1}}))
    got = imp.pending(inbox, data, mp)
    assert got == ["nba/gpp/contest-standings-197000001.zip",
                   "data/contest-standings-197000002.csv (untagged)"]


def test_logged_by_draft_group(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "dfs_proj_log_nhl.csv").write_text("date,gid,player\n2026-10-16,154755,Aa One\n")
    (tmp_path / "data" / "dfs_proj_log_2026-10-03_g154448.csv").write_text("player\nX\n")
    assert imp.logged("NHL", 154755, tmp_path) is True
    assert imp.logged("NHL", 154733, tmp_path) is False
    assert imp.logged("MLB", 154448, tmp_path) is True
    assert imp.logged("MLB", 1, tmp_path) is False
    assert imp.logged("NFL", 1, tmp_path) is None                 # no log for the sport
    assert imp.logged("GOLF", 1, tmp_path) is None


# ------------------------------------------------------------------ edge.dfs_contest
def test_tagged_slate_matches_by_draft_group_never_overlap(tmp_path):
    mp = tmp_path / "contest_meta.json"
    mp.write_text(json.dumps({"1001": {"sport": "NASCAR", "draft_group": 154817, "name": "NAS"},
                              "1002": {"sport": "NFL", "draft_group": 154468, "name": "NFL GPP"},
                              "1003": {"sport": "NASCAR", "draft_group": 999, "name": "NAS"}}))
    keys = [("2026-10-11", "154568"), ("2026-10-11", "154817")]    # two races, one date
    assert tagged_slate("contest-standings-1001.csv", "NASCAR", keys, mp) == (keys[1], None)
    key, why = tagged_slate("contest-standings-1002.csv", "NASCAR", keys, mp)
    assert key is None and "NFL contest" in why
    key, why = tagged_slate("contest-standings-1003.csv", "NASCAR", keys, mp)
    assert key is None and "no pre-lock NASCAR log" in why
    assert tagged_slate("contest-standings-1004.csv", "NASCAR", keys, mp) == (None, None)
    assert export_tag("contest-standings-1001.csv", mp)["draft_group"] == 154817
    assert export_tag("contest-standings-1001.csv", tmp_path / "missing.json") == {}


def test_sport_exports_keeps_the_sport_and_untagged(tmp_path):
    mp = tmp_path / "contest_meta.json"
    mp.write_text(json.dumps({"1001": {"sport": "MMA"}, "1002": {"sport": "NHL"}, "1004": "cash"}))
    for cid in ("1001", "1002", "1003", "1004"):
        (tmp_path / f"contest-standings-{cid}.csv").write_bytes(CSV)
    got = [p.rsplit("-", 1)[-1] for p in sport_exports("MMA", mp)]
    assert got == ["1001.csv", "1003.csv", "1004.csv"]
