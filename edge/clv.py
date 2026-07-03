"""Closing Line Value tracking — the real success metric.

Flow:
  1. log_open_bets(): when the scanner flags DK prices, snapshot them to a CSV
     with the price taken and the de-vig fair at scan time. status="open".
  2. (later, near tip-off) re-pull ONLY the flagged events on DK and call
     grade(): record DK's closing price for each bet and compute CLV.

Two CLV measures are recorded (both standard):
  * price_clv_pct = taken_decimal / close_decimal - 1
        How much better your odds were than the close. Positive = you beat the
        number. This is the "cents" view.
  * prob_clv = p_close - p_taken
        DK's own no-vig probability for the bet side at close minus at the time
        you bet. Positive = the line moved toward your side. This is the
        probability view, robust to vig.

Assumption: CLV is graded against DK's OWN closing line (same book you bet), so
it answers "did I get a better number than DK itself closed at" — the cleanest
test of whether the scan caught a real lag rather than noise.
"""
from __future__ import annotations

import csv
import datetime
from pathlib import Path

from .oddsmath import devig, decimal_to_american

FIELDS = [
    "scan_ts", "sport", "event_id", "commence_time", "event", "market", "subject",
    "side", "point", "taken_dec", "taken_american", "opp_dec", "fair_at_scan",
    "ev_at_scan", "status", "close_ts", "close_dec", "close_american",
    "p_taken_novig", "p_close_novig", "prob_clv", "price_clv_pct", "beat_close",
]


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pt(v):
    """Normalise a point value for comparison ('' / None / number)."""
    if v is None or v == "":
        return None
    return float(v)


def _dk_sides(event: dict, market: str, subject: str, point) -> dict[str, float]:
    """DK's two prices for one (market, subject, point) group, {side_name: decimal}."""
    for bm in event.get("bookmakers", []):
        if bm.get("key") != "draftkings":
            continue
        for mk in bm.get("markets", []):
            # also look in the alternate-line ladder, so a bet whose main line
            # has moved off the number still gets a closing price.
            if mk.get("key") not in (market, market + "_alternate"):
                continue
            sides = {}
            for o in mk.get("outcomes", []):
                if (o.get("description") or "") == (subject or "") and _pt(o.get("point")) == _pt(point):
                    sides[o["name"]] = o["price"]
            if sides:
                return sides
    return {}


def load(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


def _save(path, rows: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def log_open_bets(flagged: list[dict], events: list[dict], sport: str, path) -> list[dict]:
    """Append flagged DK bets as open CLV positions. De-dupes on
    (event_id, market, subject, side, point) so re-running a scan is idempotent."""
    by_id = {(e.get("id") or f'{e.get("away_team")} @ {e.get("home_team")}'): e for e in events}
    existing = load(path)
    seen = {(r["event_id"], r["market"], r["subject"], r["side"], str(r["point"])) for r in existing}
    ts = _utcnow()
    added = []
    for f in flagged:
        key = (f["event_id"], f["market"], f["subject"], f["side"], str(f["point"] if f["point"] is not None else ""))
        if key in seen:
            continue
        sides = _dk_sides(by_id.get(f["event_id"], {}), f["market"], f["subject"], f["point"])
        opp = next((d for nm, d in sides.items() if nm != f["side"]), "")
        added.append({
            "scan_ts": ts, "sport": sport, "event_id": f["event_id"],
            "commence_time": f.get("commence", ""), "event": f.get("event", ""),
            "market": f["market"], "subject": f["subject"], "side": f["side"],
            "point": "" if f["point"] is None else f["point"],
            "taken_dec": round(f["dec"], 4), "taken_american": f["american"],
            "opp_dec": opp, "fair_at_scan": round(f["fair_consensus"], 4),
            "ev_at_scan": round(f["ev"], 4), "status": "open",
        })
    _save(path, existing + added)
    return added


def grade(path, events: list[dict]) -> list[dict]:
    """Grade every open bet against DK's closing line found in `events`
    (a fresh re-pull of the flagged events). Returns the rows that changed."""
    rows = load(path)
    by_id = {(e.get("id") or f'{e.get("away_team")} @ {e.get("home_team")}'): e for e in events}
    changed = []
    ts = _utcnow()
    for r in rows:
        if r.get("status") != "open":
            continue
        ev = by_id.get(r["event_id"])
        if not ev:
            continue
        sides = _dk_sides(ev, r["market"], r["subject"], _pt(r["point"]))
        if r["side"] not in sides or len(sides) < 2:
            r["status"] = "no_close_line"  # DK moved off the number / pulled it
            r["close_ts"] = ts
            changed.append(r)
            continue
        close_dec = sides[r["side"]]
        close_opp = next(d for nm, d in sides.items() if nm != r["side"])
        taken_dec = float(r["taken_dec"])
        opp_dec = float(r["opp_dec"]) if r["opp_dec"] else None
        p_close = devig([close_dec, close_opp])[0]
        p_taken = devig([taken_dec, opp_dec])[0] if opp_dec else ""
        r.update({
            "status": "graded", "close_ts": ts,
            "close_dec": round(close_dec, 4), "close_american": decimal_to_american(close_dec),
            "p_close_novig": round(p_close, 4),
            "p_taken_novig": round(p_taken, 4) if p_taken != "" else "",
            "prob_clv": round(p_close - p_taken, 4) if p_taken != "" else "",
            "price_clv_pct": round(taken_dec / close_dec - 1, 4),
            "beat_close": taken_dec > close_dec,
        })
        changed.append(r)
    _save(path, rows)
    return changed


FLAT_BAND = 0.005  # |price CLV| <= 0.5% counts as a hold, not a beat or a loss


def clv_result(price_clv_pct: float) -> str:
    if price_clv_pct > FLAT_BAND:
        return "beat"
    if price_clv_pct < -FLAT_BAND:
        return "lost"
    return "held"


def summary(path) -> dict:
    rows = [r for r in load(path) if r.get("status") == "graded"]
    if not rows:
        return {"graded": 0}
    pcl = [float(r["price_clv_pct"]) for r in rows if r["price_clv_pct"] != ""]
    beat = sum(1 for x in pcl if clv_result(x) == "beat")
    held = sum(1 for x in pcl if clv_result(x) == "held")
    lost = sum(1 for x in pcl if clv_result(x) == "lost")
    return {
        "graded": len(rows),
        "beat": beat, "held": held, "lost": lost,
        "pct_positive": round(100 * beat / len(rows), 1),
        "avg_price_clv_pct": round(100 * sum(pcl) / len(pcl), 2) if pcl else None,
    }
