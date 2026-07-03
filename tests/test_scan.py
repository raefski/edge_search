from edge.scanner import scan


def _prop_event():
    """One WNBA-style player_points 15.5 line. Books A & B are tight and
    agree; book C is soft on the Over (prices it at 2.50 / +150)."""
    def book(key, over_price, under_price):
        return {
            "key": key,
            "markets": [{
                "key": "player_points",
                "outcomes": [
                    {"name": "Over", "description": "Role Player", "price": over_price, "point": 15.5},
                    {"name": "Under", "description": "Role Player", "price": under_price, "point": 15.5},
                ],
            }],
        }

    return {
        "home_team": "Aces", "away_team": "Sky",
        "commence_time": "2026-06-27T18:00:00Z",
        "bookmakers": [
            book("draftkings", 2.00, 1.80),
            book("fanduel", 2.00, 1.80),
            book("betmgm", 2.50, 1.55),  # soft Over
        ],
    }


def test_flags_soft_over():
    flagged = scan([_prop_event()], ev_threshold=0.02, min_books=2)
    soft = [f for f in flagged if f["book"] == "betmgm" and f["side"] == "Over"]
    assert soft, "expected the soft betmgm Over to be flagged"
    # consensus fair (DK+FD) = 0.47368; EV = 0.47368*2.50 - 1 = 0.18421
    assert abs(soft[0]["ev"] - 0.18421) < 1e-3
    assert soft[0]["n_books"] == 2
    assert soft[0]["american"] == 150


def test_no_flag_below_threshold():
    # The tight DK/FD Over prices should never clear a +2% bar.
    flagged = scan([_prop_event()], ev_threshold=0.02, min_books=2)
    assert all(not (f["book"] in ("draftkings", "fanduel") and f["side"] == "Over") for f in flagged)


def test_no_cross_game_pooling():
    # Same team plays twice on a slate: heavy favourite in g1, heavy dog in g2.
    # Each game is internally efficient (all books agree) so there is no real
    # edge. The bug would pool "Team X" across both games and invent one.
    def h2h_event(eid, x_dec, y_dec):
        def bk(k):
            return {"key": k, "markets": [{"key": "h2h", "outcomes": [
                {"name": "Team X", "price": x_dec},
                {"name": "Team Y", "price": y_dec}]}]}
        return {"id": eid, "home_team": "Team X", "away_team": "Team Y",
                "bookmakers": [bk("draftkings"), bk("fanduel"), bk("betmgm")]}

    g1 = h2h_event("g1", 1.30, 3.60)  # X ~75%
    g2 = h2h_event("g2", 3.60, 1.30)  # X ~25%
    assert scan([g1, g2], ev_threshold=0.02, min_books=2) == []


def test_target_book_filter():
    ev = _prop_event()  # betmgm is the soft one, DK/FD are tight
    # Against a clean sharp reference (fanduel), DK matches the market -> no edge.
    assert scan([ev], target_books={"draftkings"}, ref_books={"fanduel"}, min_books=1) == []
    # The soft line is still findable if betmgm itself were bettable.
    mg = scan([ev], target_books={"betmgm"})
    assert any(f["book"] == "betmgm" and f["side"] == "Over" for f in mg)


def test_soft_book_pollutes_unfiltered_consensus():
    # Demonstrates WHY ref_books matters: with betmgm's lopsided line left in
    # the consensus, DK's Under is falsely flagged as +EV. Restricting the
    # consensus to sharp books removes the phantom edge.
    ev = _prop_event()
    polluted = scan([ev], target_books={"draftkings"})
    assert any(f["book"] == "draftkings" and f["side"] == "Under" for f in polluted)
    clean = scan([ev], target_books={"draftkings"}, ref_books={"fanduel"}, min_books=1)
    assert clean == []


def test_ref_books_restricts_consensus():
    def bk(key, o, u):
        return {"key": key, "markets": [{"key": "player_points", "outcomes": [
            {"name": "Over", "description": "Role Player", "price": o, "point": 15.5},
            {"name": "Under", "description": "Role Player", "price": u, "point": 15.5}]}]}
    ev = {"id": "g", "home_team": "Aces", "away_team": "Sky",
          "bookmakers": [bk("draftkings", 2.60, 1.50),   # DK soft Over
                         bk("fanduel", 2.00, 1.80),
                         bk("betmgm", 2.00, 1.80),
                         bk("bovada", 5.00, 1.10)]}       # junk line to ignore
    f = scan([ev], target_books={"draftkings"}, ref_books={"fanduel", "betmgm"})
    soft = [x for x in f if x["book"] == "draftkings" and x["side"] == "Over"]
    assert soft and soft[0]["n_books"] == 2  # consensus used only the 2 ref books


def test_min_books_guard():
    # With only two books total, excluding one leaves a single-book consensus
    # which is below min_books -> nothing should flag.
    ev = _prop_event()
    ev["bookmakers"] = ev["bookmakers"][:2]
    assert scan([ev], ev_threshold=0.0, min_books=2) == []
