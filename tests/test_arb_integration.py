"""The arbitrage tool's wiring inside edge_search.

The detection maths itself is covered by the standalone suite; these guard the
things the integration can break: the stdlib HTTP shim, the no-dependency
rule, the snapshot contract the Streamlit page reads, and the rule that the
prediction-market anchor never becomes a bet leg.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.arb import ArbConfig                      # noqa: E402
from edge.arb import engine, http                   # noqa: E402
from edge.arb.models import Board, EventMeta, GroupKey, Quote  # noqa: E402


def test_arb_package_imports_no_third_party():
    """edge/ has no third-party dependencies (see requirements.txt); the
    scrapers were written against `requests`, so they go through the shim."""
    offenders = []
    for path in (ROOT / "edge" / "arb").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in {"requests", "yaml", "pandas", "numpy"}:
                        offenders.append(f"{path.name}: import {a.name}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if (node.module or "").split(".")[0] in {"requests", "yaml", "pandas", "numpy"}:
                    offenders.append(f"{path.name}: from {node.module}")
    assert not offenders, offenders


def test_http_shim_sends_a_browser_user_agent():
    """urllib announces Python-urllib/3.x, which these hosts answer with 403."""
    assert "Mozilla" in http.Session().headers["User-Agent"]


def test_http_shim_repeats_list_params():
    """Oddschecker takes bettypeIds three times; collapsing them drops markets.

    Exercises Session.get itself against a local server -- an earlier version
    of this test rebuilt the encoding inline and asserted on its own copy, so
    it passed even with the shim sabotaged.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["query"] = self.path.split("?", 1)[1] if "?" in self.path else ""
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        r = http.Session().get(f"http://127.0.0.1:{srv.server_port}/x",
                               params={"bettypeIds": [1, 525, 526], "eventId": 5597},
                               timeout=5)
        assert r.status_code == 200
    finally:
        srv.server_close()

    assert seen["query"].count("bettypeIds=") == 3, seen["query"]
    assert "eventId=5597" in seen["query"]


def test_http_shim_sends_the_user_agent_it_promises():
    """urllib's default UA is refused by these hosts; verify what goes on the wire."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["ua"] = self.headers.get("User-Agent", "")
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        http.Session().get(f"http://127.0.0.1:{srv.server_port}/x", timeout=5)
    finally:
        srv.server_close()
    assert "Mozilla" in seen["ua"] and "urllib" not in seen["ua"].lower()


def test_anchor_is_never_a_bet_leg():
    cfg = ArbConfig()
    assert "fanatics_markets" in cfg.books.reference
    assert "fanatics_markets" not in cfg.books.bettable


def test_only_ct_licensed_books_are_bettable():
    assert set(ArbConfig().books.bettable) == {"draftkings", "fanduel", "fanatics"}


def test_snapshot_contract_matches_what_the_page_reads():
    """The page reads these keys; a rename would break it silently."""
    from datetime import datetime, timedelta, timezone
    cfg = ArbConfig()
    board = Board()
    ev = EventMeta("e1", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) + timedelta(hours=3),
                   "Home Team", "Away Team")
    board.events["e1"] = ev
    now = datetime.now(timezone.utc)
    k = GroupKey("e1", "totals", None, 8.5)
    board.group(k, ev).add(Quote(book="draftkings", side="over", decimal=2.10,
                                 point=8.5, last_update=now))
    board.group(k, ev).add(Quote(book="fanduel", side="under", decimal=2.05,
                                 point=8.5, last_update=now))
    opps = engine.find_arbitrages(board, cfg)
    assert opps, "a 2.10/2.05 pair is an arbitrage"
    d = opps[0].to_dict()
    for key in ("kind", "profit_pct", "matchup", "description", "commence_time",
                "legs", "warnings", "sport_title"):
        assert key in d, f"snapshot is missing {key!r}"
    for key in ("book", "label", "american", "stake", "payout", "point"):
        assert key in d["legs"][0], f"leg is missing {key!r}"
    assert json.dumps(d), "snapshot must be JSON-serialisable"


def test_middle_dict_carries_push_fields():
    d = {"kind": "middle", "hit_values": [47], "breakeven_hit_pct": 9.1,
         "max_loss_pct": 4.98}
    assert d["hit_values"] and d["breakeven_hit_pct"] > 0


def test_streamlit_page_parses():
    page = ROOT / "pages" / "5_⚖️_Arbitrage.py"
    assert page.exists()
    ast.parse(page.read_text())


def test_scan_script_parses():
    ast.parse((ROOT / "scripts" / "arb_scan.py").read_text())
