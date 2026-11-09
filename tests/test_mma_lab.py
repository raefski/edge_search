"""scripts/mma_lab.py: the parts that could fake a held-out result -- reading a
line captured after the lock, the prop -> count conversion, fitting on the
held-out card, the gate row shape."""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mma_lab as L  # noqa: E402


def test_e_sig_centres_on_the_line_at_even_odds_and_moves_with_p_over():
    assert abs(L.e_sig({"line": 40.5, "p_over": 0.5}) - 40.5) < 1e-9
    assert L.e_sig({"line": 40.5, "p_over": 0.6}) > 40.5 > L.e_sig({"line": 40.5, "p_over": 0.4})
    assert L.e_sig(None) is None


def test_e_td_is_the_poisson_mean_matching_p_over():
    import math
    lam = L.e_td({"line": 0.5, "p_over": 0.5})
    assert abs((1 - math.exp(-lam)) - 0.5) < 1e-4          # P(X >= 1) = 0.5
    assert L.e_td({"line": 1.5, "p_over": 0.5}) > lam


def test_book_at_lock_drops_captures_after_the_first_bell(monkeypatch):
    ev = lambda line, start: {"start": start, "ml": None, "method": [None] * 6,  # noqa: E731
                              "sig": {"0": {"line": line, "p_over": 0.5}}, "td": {}}
    start = "2026-10-24T22:00:00Z"
    caps = {"e1": [("2026-10-24T20:00:00+00:00", ev(30.5, start)),
                   ("2026-10-24T23:00:00+00:00", ev(99.5, start))]}
    monkeypatch.setattr(L, "_scan_captures", lambda: caps)
    out = L.book_at_lock()
    (card, evs), = out.items()
    assert card == dt.date(2026, 10, 24)
    assert evs[0]["sig"][0]["line"] == 30.5


def test_loco_never_fits_on_the_held_out_card(monkeypatch):
    seen = []

    def spy(train):
        seen.append({r["card"] for r in train})
        return lambda r: r["proj"]
    monkeypatch.setitem(L.VARIANTS, "spy", spy)
    rows = [{"card": c, "key": k, "proj": 1.0, "proj_bonus": 0.0}
            for c in ("a", "b", "c") for k in ("x", "y")]
    out = L.loco(rows, "spy")
    assert len(out) == 6
    assert seen == [{"b", "c"}, {"a", "c"}, {"a", "b"}]


def test_gate_rows_have_the_gate_shape(monkeypatch, tmp_path):
    row = {"card": "2026-10-24", "key": "x", "proj": 50.0, "proj_stats": 30.0,
           "proj_bonus": 20.0, "sig": None, "td": None, "actual": 41.0}
    monkeypatch.setattr(L, "load_rows", lambda: [row])
    p = tmp_path / "g.jsonl"
    L.gate(str(p))
    (r,) = [json.loads(x) for x in p.read_text().splitlines()]
    assert set(r) == {"key", "cluster", "split", "state", "pred", "actual"}
    assert r["cluster"] == "2026-10-24" and r["split"] == "primary"
    assert r["state"] == "no_props" and r["pred"] == 50.0 and r["actual"] == 41.0
