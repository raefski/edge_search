#!/usr/bin/env python3
"""Held-out harness for the MMA projection -- the dfs-critique loop's measuring
stick (MMA_CRITIQUE_LOG.md). Built 2026-11-09, MMA critique cycle 1.

    python3 scripts/mma_lab.py report            # every variant, leave-one-card-out
    python3 scripts/mma_lab.py report --variant props
    python3 scripts/mma_lab.py parity            # lab "shipped" vs the logged board
    python3 scripts/mma_lab.py gate OUT          # one JSON line per graded fighter-fight

THE METRIC: DK points per fighter-fight against the pre-lock projection (MAE,
bias), and its STAT half -- DK points minus the win bonus, the half the
Significant Strikes / Takedowns O/U props price -- sliced into fighters who had
a pre-lock prop line and those who did not.

LIVE-FAITHFUL, FROM THE ARCHIVED BOOK, NOT THE BOARD LOG. The board log
(data/dfs_proj_log_mma.csv) misses 10/03 and 10/10 and stores only the total, so
each card is rebuilt here from DK Sportsbook's own captures
(data/mma_odds_history/<date>/*.json.gz, scripts/mma_odds_capture.py), using
production's pieces exactly as edge/dfs_run_mma.build_board does:
    mk.fight_markets_table -> msim.timing -> msim.expected_points
with msim.style_indices(before=card date). expected_points is the simulator's
mean without the sampling noise (the gamma noise has mean 1). Differences from a
live build, stated: (1) the fighter key and weight class come from UFCStats, not
from DK's draftables (no draftables are archived for 10/03 and 10/10), so a DK
name that live failed to match is matched here; (2) only bouts that had a
moneyline+method price are scored -- a salary-priced bout has no book to rebuild
from. `parity` measures the gap to the logged board on the cards that have one.

INFORMATION STATE = the card's lock. For each card (ET date of the first bell)
the book is the LAST capture taken before the card's first scheduled start; a
prop line is the last one captured before that lock (DK pulls a fighter's props
early sometimes, so a line seen earlier the same day is kept). Nothing after the
lock is read.

TRUTH = UFCStats rescored with DK's rules (edge/mma.py, which reproduces DK's own
FPPF), so the lab needs no contest export. UFCStats lags a card by ~a day; the
lab never refreshes the mirror (it is a measuring stick, not a collector).

THE SPLIT: leave one CARD out (a card is one night, one set of judges and
referees, one market). Every fitted constant of a variant is fitted on the other
cards and scored on the held-out one; per-card deltas are bootstrapped by card
for a 90% range. Below 5 cards with a prop line the verdict is "not enough data".

Variants are VARIANTS entries: fit(train_rows) -> predict(row) -> DK points.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import gzip
import json
import math
import sys
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np                                     # noqa: E402

from edge import mma, mma_market as mk, mma_sim as msim  # noqa: E402

HIST = ROOT / "data" / "mma_odds_history"
CACHE = ROOT / "data" / "cache" / "mma_lab_book.json"
ET = dt.timezone(dt.timedelta(hours=-4))   # card dates; every UFC card starts >= 17:00 ET
MATCH = 0.72                                 # = edge/dfs_run_mma.BOUT_MATCH
MIN_CARDS = 5


def _utc(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, tail = s.split(".", 1)
        tz = tail[tail.find("+"):] if "+" in tail else "+00:00"
        s = head + tz
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# The book, per card, at the lock
# ---------------------------------------------------------------------------
def _scan_captures() -> dict:
    """{event_id: [(generated_at, ev), ...]} over every archived capture, cached
    in data/cache (parsing 1,700 bundles takes a minute)."""
    files = sorted(glob.glob(str(HIST / "*" / "*.json.gz")))
    if CACHE.exists():
        c = json.loads(CACHE.read_text())
        if c.get("files") == len(files):
            return c["events"]
    out: dict = collections.defaultdict(list)
    for f in files:
        try:
            b = json.load(gzip.open(f))
        except (OSError, ValueError):
            continue
        gen = b.get("generated_at")
        for eid, ev in mk.parse_book(b).items():
            ev = dict(ev)
            ev["sig"] = {str(k): v for k, v in ev["sig"].items()}
            ev["td"] = {str(k): v for k, v in ev["td"].items()}
            ev["rounds"] = {}                          # unused here; tuple keys
            out[eid].append((gen, ev))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"files": len(files), "events": out}))
    return out


def book_at_lock() -> dict:
    """{card date: [ev at lock, ...]} -- each event as of the last capture
    before its card's first scheduled start, with every prop line kept from
    the last capture before the lock that had it."""
    caps = _scan_captures()
    start: dict = {}
    for eid, snaps in caps.items():
        s = _utc(snaps[-1][1].get("start"))
        if s is not None:
            start[eid] = s
    cards: dict = collections.defaultdict(list)
    for eid, s in start.items():
        cards[s.astimezone(ET).date()].append(eid)
    out: dict = {}
    for d, eids in cards.items():
        lock = min(start[e] for e in eids)
        evs = []
        for eid in eids:
            pre = [(g, ev) for g, ev in caps[eid] if (_utc(g) or lock) < lock]
            if not pre:
                continue
            ev = dict(pre[-1][1])
            for key in ("sig", "td"):
                ev[key] = {}
                for _g, e in pre:                    # later captures overwrite
                    for side, v in (e.get(key) or {}).items():
                        if v:
                            ev[key][int(side)] = v
            evs.append(ev)
        if evs:
            out[d] = evs
    return out


# ---------------------------------------------------------------------------
# Rows: one per graded fighter-fight
# ---------------------------------------------------------------------------
def _e_bonus(cells: dict, tim: dict, sched: int, side: int) -> float:
    """E[win bonus] for one corner -- the same bins expected_points pays on."""
    nb = sched * msim.BINS_PER_ROUND
    rnd = np.arange(nb) // msim.BINS_PER_ROUND + 1
    sec = (np.arange(nb) % msim.BINS_PER_ROUND + 0.5) * msim.BIN_SEC
    bonus = np.array([mma.WIN_BONUS.get(int(r), 40.0) for r in rnd])
    bonus = bonus + np.where((rnd == 1) & (sec <= mma.QUICK_WIN_SECONDS), mma.QUICK_WIN_BONUS, 0.0)
    p = "A_" if side == 0 else "B_"
    return (float((tim[p + "KO"] * bonus).sum() + (tim[p + "SUB"] * bonus).sum())
            + cells[p + "DEC"] * mma.DECISION_BONUS)


def load_rows() -> list[dict]:
    fights = mma.fights()
    by_date: dict = collections.defaultdict(list)
    for f in fights:
        by_date[f["date"]].append(f)
    style_cache: dict = {}
    rows = []
    for card, evs in sorted(book_at_lock().items()):
        cand = by_date.get(card, []) + by_date.get(card + dt.timedelta(days=1), [])
        if not cand:
            continue                                  # UFCStats has not got it yet
        if card not in style_cache:
            style_cache[card] = msim.style_indices(before=card, fights=fights)
        style = style_cache[card]
        for ev in evs:
            if not (ev.get("ml") and all(ev.get("method") or [None])):
                continue
            best, best_s, flip = None, 0.0, False
            for f in cand:
                a, b = f["f"][0]["name"], f["f"][1]["name"]
                s1 = min(mma.name_similarity(ev["a"], a), mma.name_similarity(ev["b"], b))
                s2 = min(mma.name_similarity(ev["a"], b), mma.name_similarity(ev["b"], a))
                if max(s1, s2) > best_s:
                    best, best_s, flip = f, max(s1, s2), s2 > s1
            if best is None or best_s < MATCH or best["nc"]:
                continue
            f = best
            sched = ev.get("sched_rounds") or f["sched_rounds"]
            tbl = mk.fight_markets_table(ev, f["lbs"], f["women"])
            # orient the book's (a, b) onto UFCStats' corner order
            cells = tbl["cells"] if not flip else {
                "A_KO": tbl["cells"]["B_KO"], "A_SUB": tbl["cells"]["B_SUB"],
                "A_DEC": tbl["cells"]["B_DEC"], "B_KO": tbl["cells"]["A_KO"],
                "B_SUB": tbl["cells"]["A_SUB"], "B_DEC": tbl["cells"]["A_DEC"]}
            tim = msim.timing(cells, sched)
            idx = []
            for me in f["f"]:
                s = style.get(me["key"])
                idx.append((s["off"], s["def"]) if s else (msim.DEBUT_OFF, msim.DEBUT_DEF))
            ea, eb = msim.expected_points(cells, tbl["p_draw"], sched, tim, idx[0], idx[1])
            pa, _pb = msim.win_probs(cells)
            for i, (me, e_tot) in enumerate(zip(f["f"], (ea, eb))):
                book_side = (i + flip) % 2
                e_bon = _e_bonus(cells, tim, sched, i)
                rows.append({
                    "card": card.isoformat(), "fighter": me["name"], "key": me["key"],
                    "sched": sched, "p_win": pa if i == 0 else 1 - pa,
                    "proj": e_tot, "proj_bonus": e_bon, "proj_stats": e_tot - e_bon,
                    "sig": ev["sig"].get(book_side), "td": ev["td"].get(book_side),
                    "actual": me["dk"], "actual_stats": me["dk_stats"],
                    "actual_sig": me["sig"], "actual_td": me["td"],
                })
    return rows


# ---------------------------------------------------------------------------
# Props -> expected counts
# ---------------------------------------------------------------------------
#: Spread of a fighter's significant strikes around the book's centre, as a
#: coefficient of variation, for turning (line, P(over)) into a mean. A JUDGMENT
#: CALL, not fitted (the lines sit within a few points of 50/50, so it moves the
#: mean by a few per cent at most); recorded in MMA_CRITIQUE_LOG.md cycle 1.
SIG_CV = 0.5


def e_sig(o: dict | None) -> float | None:
    """E[significant strikes landed] from the O/U: normal, sd = SIG_CV * mean,
    solved so P(X > line) = the devigged P(over)."""
    if not o or not o.get("line"):
        return None
    p = min(max(o["p_over"], 0.03), 0.97)
    z = NormalDist().inv_cdf(p)
    return o["line"] / max(0.2, 1.0 - SIG_CV * z)


def e_td(o: dict | None) -> float | None:
    """E[takedowns landed] from the O/U: Poisson whose P(X > line) = P(over)."""
    if not o or o.get("line") is None:
        return None
    p = min(max(o["p_over"], 0.02), 0.98)
    k = int(math.floor(o["line"]))               # over 0.5 = at least 1, etc.
    lo, hi = 1e-4, 30.0
    for _ in range(60):
        lam = 0.5 * (lo + hi)
        cdf = sum(math.exp(-lam) * lam ** j / math.factorial(j) for j in range(k + 1))
        if 1 - cdf < p:
            lo = lam
        else:
            hi = lam
    return 0.5 * (lo + hi)


def _stat_map_from_history(before: dt.date) -> tuple:
    """(c0, c_sig, c_td): DK stat points ~ c0 + c_sig*sig + c_td*td over UFC
    fights 2020 -> `before` -- the count -> points map (non-significant strikes,
    control time, knockdowns ride on the two counts). History only, no props, so
    fitting it on every fight before the held-out card leaks nothing."""
    X, y = [], []
    for f in mma.fights():
        if f["date"] is None or f["date"] < dt.date(2020, 1, 1) or f["date"] >= before or f["nc"]:
            continue
        for me in f["f"]:
            X.append((1.0, me["sig"], me["td"]))
            y.append(me["dk_stats"])
    c, *_ = np.linalg.lstsq(np.array(X), np.array(y), rcond=None)
    return tuple(float(x) for x in c)


_MAP: dict = {}


def prop_stats(r: dict) -> float | None:
    """The props' own E[DK stat points] for a row with both lines, else None."""
    s, t = e_sig(r["sig"]), e_td(r["td"])
    if s is None or t is None:
        return None
    first = min(dt.date.fromisoformat(r["card"]), dt.date(2026, 9, 26))
    if first not in _MAP:
        _MAP[first] = _stat_map_from_history(first)
    c0, cs, ct = _MAP[first]
    return c0 + cs * s + ct * t


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------
def v_shipped(train):
    return lambda r: r["proj"]


def _fit_w(train) -> float:
    """Blend weight on the props' stat points, least squares on train rows with
    props, clipped to [0, 1]."""
    xs = [(prop_stats(r) - r["proj_stats"], r["actual_stats"] - r["proj_stats"])
          for r in train if prop_stats(r) is not None]
    if len(xs) < 10:
        return 0.0
    d = np.array(xs)
    w = float((d[:, 0] * d[:, 1]).sum() / max(1e-9, (d[:, 0] ** 2).sum()))
    return min(1.0, max(0.0, w))


def v_props(train):
    """stats' = stats + w * (prop stats - stats); w fitted on the train cards."""
    w = _fit_w(train)

    def pred(r):
        ps = prop_stats(r)
        return r["proj"] if ps is None else r["proj"] + w * (ps - r["proj_stats"])
    pred.w = w
    return pred


def v_props_full(train):
    """The props' stat points replace the model's outright (w = 1, nothing fitted)."""
    def pred(r):
        ps = prop_stats(r)
        return r["proj"] if ps is None else r["proj_bonus"] + ps
    return pred


def v_props_ols(train):
    """Recalibrated blend: stat residual ~ a + b * (prop stats - model stats),
    both fitted on the train cards (the lines run ~19% above actual sig strikes,
    so a bare weight cannot use them without an intercept)."""
    xs = [(prop_stats(r) - r["proj_stats"], r["actual_stats"] - r["proj_stats"])
          for r in train if prop_stats(r) is not None]
    a = b = 0.0
    if len(xs) >= 10:
        d = np.array(xs)
        b, a = np.polyfit(d[:, 0], d[:, 1], 1)

    def pred(r):
        ps = prop_stats(r)
        return r["proj"] if ps is None else r["proj"] + a + b * (ps - r["proj_stats"])
    pred.w = float(b)
    return pred


VARIANTS = {"shipped": v_shipped, "props": v_props, "props_full": v_props_full,
            "props_ols": v_props_ols}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def loco(rows, variant) -> list[dict]:
    """Leave-one-card-out predictions: [{**row, "pred", "pred_stats"}]."""
    cards = sorted({r["card"] for r in rows})
    out = []
    for c in cards:
        train = [r for r in rows if r["card"] != c]
        pred = VARIANTS[variant](train)
        for r in rows:
            if r["card"] == c:
                p = pred(r)
                out.append({**r, "pred": p, "pred_stats": p - r["proj_bonus"],
                            "w": getattr(pred, "w", None)})
    return out


def _stats(rs, col_p, col_a):
    if not rs:
        return float("nan"), float("nan"), 0
    e = np.array([r[col_p] - r[col_a] for r in rs])
    return float(np.mean(np.abs(e))), float(np.mean(e)), len(rs)


def _boot(deltas_by_card: dict, n=4000, seed=0):
    rng = np.random.default_rng(seed)
    keys = list(deltas_by_card)
    arr = [np.array(deltas_by_card[k]) for k in keys]
    tot = float(np.mean(np.concatenate(arr)))
    sims = []
    for _ in range(n):
        pick = rng.integers(0, len(keys), len(keys))
        sims.append(float(np.mean(np.concatenate([arr[i] for i in pick]))))
    return tot, float(np.percentile(sims, 5)), float(np.percentile(sims, 95))


def report(variant: str | None) -> None:
    rows = load_rows()
    cards = sorted({r["card"] for r in rows})
    has = lambda r: prop_stats(r) is not None          # noqa: E731
    print(f"{len(rows)} graded fighter-fights on {len(cards)} cards "
          f"({', '.join(cards)}); {sum(map(has, rows))} with sig+td lines at lock "
          f"on {len({r['card'] for r in rows if has(r)})} cards")
    base = {(r["card"], r["key"]): r for r in loco(rows, "shipped")}
    for v in ([variant] if variant else list(VARIANTS)):
        res = loco(rows, v)
        pr = [r for r in res if has(r)]
        print(f"\n== {v} ==")
        print(f"  {'slice':<26}{'n':>5}{'stat MAE':>10}{'stat bias':>10}{'DK MAE':>9}{'DK bias':>9}")
        for name, rs in (("fighters with props", pr), ("fighters without", [r for r in res if not has(r)]),
                         ("all", res)):
            sm, sb, n = _stats(rs, "pred_stats", "actual_stats")
            tm, tb, _ = _stats(rs, "pred", "actual")
            print(f"  {name:<26}{n:5d}{sm:10.2f}{sb:+10.2f}{tm:9.2f}{tb:+9.2f}")
        if v == "shipped":
            continue
        dby: dict = collections.defaultdict(list)
        for r in pr:
            b = base[(r["card"], r["key"])]
            dby[r["card"]].append(abs(r["pred"] - r["actual"]) - abs(b["pred"] - b["actual"]))
        if not dby:
            continue
        tot, lo, hi = _boot(dby)
        better = sum(1 for d in dby.values() if np.mean(d) < 0)
        print(f"  DK MAE change vs shipped on prop fighters: {tot:+.2f} [90% {lo:+.2f}, {hi:+.2f}] "
              f"by card; better on {better}/{len(dby)} cards")
        for c in sorted(dby):
            ws = {r["w"] for r in res if r["card"] == c}
            print(f"    {c}: n={len(dby[c]):2d} delta {np.mean(dby[c]):+6.2f}"
                  + (f"  w={next(iter(ws)):.2f}" if ws and None not in ws else ""))
        if len(dby) < MIN_CARDS:
            print(f"  NOT ENOUGH DATA: {len(dby)} cards with props (< {MIN_CARDS})")


def parity() -> None:
    """Lab 'shipped' vs the logged live board, where a board was logged."""
    import csv
    log = collections.defaultdict(dict)
    p = ROOT / "data" / "dfs_proj_log_mma.csv"
    for r in csv.DictReader(p.open(newline="")):
        log[r["date"]].setdefault(mma.fighter_key(r["fighter"]), float(r["proj"]))
    rows = load_rows()
    d = [(r["proj"], log[r["card"]][r["key"]]) for r in rows if r["key"] in log.get(r["card"], {})]
    if not d:
        print("no overlap with the board log")
        return
    a = np.array(d)
    print(f"n={len(a)} fighters on {len({r['card'] for r in rows if r['card'] in log})} logged cards: "
          f"lab - live mean {np.mean(a[:, 0] - a[:, 1]):+.2f}, MAE {np.mean(np.abs(a[:, 0] - a[:, 1])):.2f}, "
          f"corr {np.corrcoef(a[:, 0], a[:, 1])[0, 1]:.3f}")


def gate(path: str) -> None:
    """Rows for harness/critique_gate.py: the SHIPPED projection (production
    pieces, as in build_board) per graded fighter-fight, clustered by card.
    state = whether the fighter had sig+td prop lines at lock."""
    rows = load_rows()
    n = 0
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps({"key": f"{r['card']}|{r['key']}", "cluster": r["card"],
                                 "split": "primary",
                                 "state": "props" if prop_stats(r) is not None else "no_props",
                                 "pred": float(_production_pred(r)),
                                 "actual": float(r["actual"])}) + "\n")
            n += 1
    print(f"{n:,} gate rows -> {path}")


def _production_pred(r: dict) -> float:
    """What production projects for this row: the analytic mean, plus
    production's own prop adjustment if it has one (msim.prop_adjust)."""
    adj = getattr(msim, "prop_adjust", None)
    if adj is None:
        return r["proj"]
    return r["proj"] + adj(r["proj_stats"], r["sig"], r["td"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["report", "parity", "gate"])
    ap.add_argument("out", nargs="?")
    ap.add_argument("--variant", choices=sorted(VARIANTS))
    a = ap.parse_args()
    if a.cmd == "gate":
        if not a.out:
            ap.error("gate needs an output path")
        gate(a.out)
    elif a.cmd == "parity":
        parity()
    else:
        report(a.variant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
