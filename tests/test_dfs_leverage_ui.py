"""The contest picker (edge/dfs_leverage_ui.py) must still offer a contest
after it fills: DK drops a full contest from its lobby, and the small
single-entry ones (NHL $500 Daily Dollar) fill before lock -- 2026-10-15."""
from __future__ import annotations

from edge import dfs_leverage_ui as ui


def _row(cid, name, dg=154800, m=594, nt=100):
    return {"id": cid, "n": name, "dg": dg, "a": 1, "m": m, "nt": nt, "po": 500, "attr": {}}


def test_a_contest_that_leaves_the_lobby_stays_listed(monkeypatch):
    ui._seen().clear()
    lobby = [_row(1, "NHL $500 Daily Dollar [Single Entry]"), _row(2, "NHL $20 Quarter Jukebox", m=95)]
    monkeypatch.setattr(ui, "_lobby", lambda code: lobby)
    assert [c["id"] for c in ui.slate_gpps("NHL", 154800)] == [1, 2]
    lobby.pop(0)                                            # it filled
    out = ui.slate_gpps("NHL", 154800)
    assert [c["id"] for c in out] == [1, 2] and out[0]["_full"] and "_full" not in out[1]
    assert ui.slate_gpps("NHL", 999) == []                  # other draft groups untouched


def test_past_gpps_offer_each_named_contest_once_with_its_own_field():
    past = {c["n"]: c for c in ui.past_gpps("NHL")}
    dd = past["NHL $500 Daily Dollar [Single Entry]"]
    # its own field from contest_meta.json (the latest one played), not a pinned
    # number: the 10/15 one drew 594, the 10/16 one 549
    import json
    meta = json.loads(ui._META.read_text())
    fields = {v["field"] for v in meta.values() if isinstance(v, dict)
              and v.get("name") == "NHL $500 Daily Dollar [Single Entry]"}
    assert dd["m"] in fields and dd["_past"] >= "2026-10-09"
    assert ui._from_lobby(dd)["payouts"] is None             # no DK lookup for a past row
    assert "NHL $500 Daily Dollar [Single Entry]" not in {
        c["n"] for c in ui.past_gpps("NHL", {"nhl $500 daily dollar [single entry]"})}
    assert all(c["n"].startswith("NHL") for c in past.values())
