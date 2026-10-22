"""Held-out harness for the overlay app's at-lock field forecast (Classic only).

The overlay alert's money question is "how many entries will this contest have at
lock?" -- it decides whether the overlay is still there when the entry counts. The
alert's final call is made 4.5-9.5 min before lock (feedback: final call lead), so
this scores the forecast exactly as live makes it then:

  n_lock = max(straight line through the contest's own snapshots,   # projected_fill
               likely_at_lock(late-fill model fitted on TRAIN locks only))

against the field DK reported after lock (data/contest_finals.csv, collected by
harness/overlay.py collect_finals). Train/test is split by lock DATE: contests that
lock together share one realisation (a slow NFL Sunday is slow for every contest),
so a day never straddles the split. Classic only: Showdown/Captain alerts are off
(feedback: overlay Classic only).

    python3 scripts/overlay_lab.py report [--cutoff 2026-10-18] [--last 4.5] [--first 9.5]

Metrics (test contests not full at the call, n given with every number):
  err/cap    (forecast - final) / cap -- MAE, RMSE, bias (+ = forecast too full)
  overlay    the call "prize pool > fee x entries at lock": missed (said no, was),
             false (said yes, wasn't) -- false calls cost entries, misses cost EV
Slices by sport and fill band at the call. Used by the dfs-critique skill (overlay).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness import overlay as H  # noqa: E402
from edge import dfs_entry_edge as E  # noqa: E402
from scripts import dfs_overlay as O  # noqa: E402


def classic_ids() -> set:
    """Contest ids logged as Classic (load_log drops game_type)."""
    out, other = set(), set()
    with O.FILL_LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            (out if r.get("game_type", "Classic") == "Classic" else other).add(r["contest_id"])
    return out - other


def lock_day(c) -> date:
    return c["lock"].astimezone(E.ET).date()


def call_point(c, first: float, last: float):
    """The snapshot the final call would use: the latest one in [last, first] min."""
    ms = [m for m in c["points"] if last <= m <= first]
    return min(ms) if ms else None


PACE_LO, PACE_HI = 12.0, 25.0   # earlier snapshot this far before the call sets the pace


def pace_v(c, m_call):
    """Straight-line share of the empty room the recent pace would fill by lock."""
    e, cap = c["points"][m_call], c["cap"]
    older = [m for m in c["points"] if m_call + PACE_LO <= m <= m_call + PACE_HI]
    if not older or cap <= e:
        return None
    m0 = min(older)
    rate = max(0.0, (e - c["points"][m0]) / (m0 - m_call))
    return rate * m_call / (cap - e)


def fit_pace(train, finals, first, last, k=5):
    """Quintile edges of pace v and the median realised call->final share in each."""
    xs, every = [], []
    for cid, c in train.items():
        if cid not in finals or c["cap"] < 10:
            continue
        m = call_point(c, first, last)
        if m is None or c["points"][m] >= c["cap"]:
            continue
        e = c["points"][m]
        share = max(0.0, (min(finals[cid], c["cap"]) - e) / (c["cap"] - e))
        every.append(share)
        v = pace_v(c, m)
        if v is not None:
            xs.append((v, share))
    xs.sort()
    n = len(xs)
    cuts = [xs[n * i // k][0] for i in range(1, k)]
    bins = [[s for v, s in xs if (i == 0 or v >= cuts[i - 1]) and (i == k - 1 or v < cuts[i])]
            for i in range(k)]
    # The whole spread per bin is kept for the EV call; contests with no pace
    # snapshot draw from every train share.
    return {"cuts": cuts, "share": [E._median(b) for b in bins], "spread": bins,
            "all": every, "n": n}


def tables() -> dict:
    """Payout tiers per contest id (data/cache/contest_payouts.json: the live alert's
    pricing cache plus the tables collect_finals keeps from its finals lookups)."""
    return json.loads(O.PAYOUTS.read_text()) if O.PAYOUTS.exists() else {}


def entry_value(c, field: float, tiers=None) -> float:
    """$ one more entry is worth when `field` others are in: the contest's real
    payout tiers as the live alert prices it (entry_edge), else the flat
    approximation pool/(field+1) - fee."""
    if tiers:
        return E.entry_edge(tiers, round(field), c["fee"])["roi"] * c["fee"]
    return c["prize_pool"] / (field + 1) - c["fee"]


def entry_ev(c, m_call, pace, tiers=None) -> tuple[float, float]:
    """(expected value of one entry, P(overlay at lock)) over the train spread of
    call->final shares in this contest's pace bin."""
    e, cap, pp, fee = c["points"][m_call], c["cap"], c["prize_pool"], c["fee"]
    v = pace_v(c, m_call)
    ss = pace["all"] if v is None else pace["spread"][sum(v >= x for x in pace["cuts"])]
    fields = [e + min(1.0, x) * (cap - e) for x in ss]
    return (sum(entry_value(c, f, tiers) for f in fields) / len(fields),
            sum(pp > fee * f for f in fields) / len(fields))


def forecast(c, cid, hist, model, m_call, pace=None):
    """n_lock as harness/overlay.py assess() computes it, from data up to the call."""
    e = c["points"][m_call]
    if pace:
        v = pace_v(c, m_call)
        if v is not None:
            i = sum(v >= x for x in pace["cuts"])
            return min(c["cap"], round(e + min(1.0, pace["share"][i]) * (c["cap"] - e)))
    t_call = c["lock"] - timedelta(minutes=m_call)
    h = [(t, x) for t, x in hist.get(cid, []) if t <= t_call]
    proj = E.projected_fill(h, c["cap"], m_call)
    floor = int(proj["at_lock"]) if proj["at_lock"] is not None else e
    likely = E.likely_at_lock(model, e, c["cap"], m_call, c.get("sport"), E.weekday(c["lock"]))
    return max(floor, likely)


def evaluate(cutoff: date, first: float = 9.5, last: float = 4.5, model_fn=None, pace=False, ev=False):
    hist, contests = H.load_log()
    finals = H.load_finals()
    keep = classic_ids()
    contests = {k: v for k, v in contests.items() if k in keep}
    train = {k: v for k, v in contests.items() if lock_day(v) < cutoff}
    model = (model_fn or E.fit_late_fill)(train, {k: v for k, v in finals.items() if k in train})
    pace = fit_pace(train, finals, first, last) if (pace or ev) else None
    tabs = tables()
    rows = []
    for cid, c in contests.items():
        if lock_day(c) < cutoff or cid not in finals or c["cap"] < 10:
            continue
        m = call_point(c, first, last)
        if m is None or c["points"][m] >= c["cap"]:
            continue
        final = min(finals[cid], c["cap"])
        f = forecast(c, cid, hist, model, m, pace)
        pp, fee = c.get("prize_pool"), c.get("fee")
        pred = None if pp is None or fee is None else pp > fee * f
        tiers = (tabs.get(cid) or {}).get("tiers")
        if ev and pred is not None:
            pred = entry_ev(c, m, pace, tiers)[0] > 0
        rows.append({"cid": cid, "sport": c.get("sport"), "cap": c["cap"],
                     "band": E._band(c["points"][m] / c["cap"]), "err": (f - final) / c["cap"],
                     "ov_true": None if pp is None or fee is None else pp > fee * final,
                     "ov_pred": pred,
                     # one extra entry's value at the real final field, priced with the
                     # contest's payout tiers when cached (else flat; "priced" counts them)
                     "priced": bool(tiers),
                     "ev": None if pp is None or fee is None else entry_value(c, final, tiers)})
    return rows, model


def summarize(rows) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    errs = [r["err"] for r in rows]
    ov = [r for r in rows if r["ov_true"] is not None]
    return {"n": n, "mae": sum(map(abs, errs)) / n, "rmse": math.sqrt(sum(e * e for e in errs) / n),
            "bias": sum(errs) / n,
            "ov_n": sum(r["ov_true"] for r in ov),
            "missed": sum(r["ov_true"] and not r["ov_pred"] for r in ov),
            "false": sum(r["ov_pred"] and not r["ov_true"] for r in ov),
            "ev": sum(r["ev"] for r in ov if r["ov_pred"]),
            "priced": sum(r["priced"] for r in rows)}


def fmt(label, s) -> str:
    if not s["n"]:
        return f"{label:<14} n=0"
    flag = "  (not enough data)" if s["n"] < 30 else ""
    return (f"{label:<14} n={s['n']:>5}  MAE {s['mae']:.3f}  RMSE {s['rmse']:.3f}  bias {s['bias']:+.3f}"
            f"  overlays {s['ov_n']:>4}  missed {s['missed']:>3}  false {s['false']:>3}  $/1-entry-each {s['ev']:+8.0f}  tiered {s['priced']}/{s['n']}{flag}")


def report(cutoff: date, first: float, last: float, pace=False, ev=False) -> list[str]:
    rows, model = evaluate(cutoff, first, last, pace=pace, ev=ev)
    days = sorted({r["cid"] for r in rows})
    out = [f"overlay lab: Classic, call {last}-{first} min out, fit locks < {cutoff} "
           f"(model n={model['n']}, tail {model['tail']:.2f} n={model['tail_n']}), test >= {cutoff}",
           fmt("ALL", summarize(rows))]
    for sp in sorted({r["sport"] for r in rows if r["sport"]}):
        out.append(fmt(f"  {sp}", summarize([r for r in rows if r["sport"] == sp])))
    for b, (lo, hi) in enumerate(E.FILL_BANDS):
        out.append(fmt(f"  fill {lo:.2f}-{hi:.2f}", summarize([r for r in rows if r["band"] == b])))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report"])
    ap.add_argument("--cutoff", default="2026-10-18")
    ap.add_argument("--first", type=float, default=9.5)
    ap.add_argument("--last", type=float, default=4.5)
    ap.add_argument("--pace", action="store_true", help="cycle 1 candidate: pace-binned share")
    ap.add_argument("--ev", action="store_true",
                    help="cycle 2 candidate: call on expected entry value over the pace bin's spread")
    a = ap.parse_args(argv)
    print("\n".join(report(date.fromisoformat(a.cutoff), a.first, a.last, a.pace, a.ev)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
