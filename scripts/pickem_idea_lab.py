#!/usr/bin/env python3
"""Rapid hypothesis harness for the 10-round idea search -- DEV SPLIT ONLY.

Companion to scripts/pickem_feature_lab.py (rounds 1-3, the EPA/coach/market
work). This file is the reproducible record for the situational-data rounds
that follow, and it exists separately because it is LEAN: it loads two CSVs and
nothing else, so a round of ideas can be tested in seconds rather than after a
45-second EPA rebuild.

HARD RULE, same as feature_lab: refuses to load HOLDOUT seasons (2023-2024).
Train 2014-2019, validate 2020-2022. The holdout is spent by
scripts/pickem_backtest.py only, on a finished model, once.

THE TWO THINGS THIS HARNESS ENFORCES THAT A NAIVE TEST WOULD MISS
-----------------------------------------------------------------
1. A rule is judged ONLY on the games it FLIPS. In pick'em you must pick every
   game, so a rule that agrees with the shipped model 95% of the time cannot be
   judged by its overall win rate -- that number is dominated by picks it did
   not make. Its entire effect lives in the games where it disagrees, so those
   are measured directly (and that is also the most statistically powerful
   test). See PICKEM_MODEL.md 5e.
2. MULTIPLICITY. This harness keeps a persistent ledger of every hypothesis
   ever tested (data/pickem_idea_ledger.json) and reports the Sidak-adjusted
   significance bar for the CUMULATIVE number of tests, not for one test in
   isolation. Testing 30 ideas guarantees roughly 1.5 of them clear p<0.05 by
   luck; the ledger is what stops a lucky draw being written up as a finding.

Run: python3 scripts/pickem_idea_lab.py
"""
from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.pickem import ats_result, make_pick  # noqa: E402

ODDS = ROOT / "data" / "pickem_odds_history.csv"
SITU = ROOT / "data" / "pickem_situational.csv"
LEDGER = ROOT / "data" / "pickem_idea_ledger.json"

HOLDOUT = {2023, 2024}
TRAIN = set(range(2014, 2020))
VAL = set(range(2020, 2023))
DEV_SEASONS = sorted(TRAIN | VAL)


# --------------------------------------------------------------- loading ----

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_dev() -> list[dict]:
    """Odds history joined to situational context, holdout excluded at load."""
    situ = {}
    with SITU.open() as f:
        for r in csv.DictReader(f):
            situ[(int(r["season"]), int(r["week"]), r["home_team"], r["away_team"])] = r

    rows = []
    with ODDS.open() as f:
        for r in csv.DictReader(f):
            s = int(r["season"])
            if s in HOLDOUT:
                continue                    # guard rail, not a filter to relax
            key = (s, int(r["week"]), r["home_team"], r["away_team"])
            sr = situ.get(key, {})
            pool = float(r["home_line_open"])
            live = float(r["home_line_close"])
            margin = int(r["home_score"]) - int(r["away_score"])
            v = margin + pool
            g = {
                "season": s, "week": int(r["week"]),
                "home": r["home_team"], "away": r["away_team"],
                "pool_line": pool, "live_line": live, "margin": margin,
                "home_covers": None if v == 0 else v > 0,
                "total_open": _f(r["total_open"]), "total_close": _f(r["total_close"]),
                "home_ml_close": _f(r["home_ml_close"]),
                "away_ml_close": _f(r["away_ml_close"]),
                # situational
                "home_rest": _f(sr.get("home_rest")), "away_rest": _f(sr.get("away_rest")),
                "div_game": sr.get("div_game") == "1",
                "roof": sr.get("roof", ""), "surface": sr.get("surface", ""),
                "temp": _f(sr.get("temp")), "wind": _f(sr.get("wind")),
                "weekday": sr.get("weekday", ""), "gametime": sr.get("gametime", ""),
                "location": sr.get("location", ""),
                "home_qb": sr.get("home_qb_name", ""), "away_qb": sr.get("away_qb_name", ""),
                "referee": sr.get("referee", ""),
            }
            g["outdoor"] = g["roof"] in ("outdoors", "open")
            g["rest_edge"] = (g["home_rest"] - g["away_rest"]
                              if g["home_rest"] is not None and g["away_rest"] is not None
                              else None)
            g["move"] = live - pool
            g["abs_line"] = abs(pool)
            rows.append(g)
    rows.sort(key=lambda g: (g["season"], g["week"]))
    return rows


def shipped_side(g) -> str:
    return make_pick(g["away"], g["home"], g["pool_line"], g["live_line"],
                     g["total_open"], g["total_close"]).side


def shipped_tier(g) -> str:
    return make_pick(g["away"], g["home"], g["pool_line"], g["live_line"],
                     g["total_open"], g["total_close"]).tier


def won(g, side) -> str:
    return ats_result(g["margin"], g["pool_line"], side)


# ----------------------------------------------------------------- stats ----

def _z(w, l):
    n = w + l
    return 0.0 if n == 0 else ((w / n) - 0.5) / math.sqrt(0.25 / n)


def _p_two_sided(z):
    return math.erfc(abs(z) / math.sqrt(2))


def sidak_bar(n_tests: int, alpha: float = 0.05) -> float:
    """Two-sided z needed for family-wise alpha across n_tests independent tests."""
    per = 1 - (1 - alpha) ** (1 / max(n_tests, 1))
    lo, hi = 0.0, 8.0
    for _ in range(200):                       # invert the normal tail
        mid = (lo + hi) / 2
        if math.erfc(mid / math.sqrt(2)) > per:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------- ledger ----

def load_ledger() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"tests": []}


def save_ledger(led: dict) -> None:
    LEDGER.write_text(json.dumps(led, indent=2))


# ------------------------------------------------------------ evaluation ----

@dataclass
class Result:
    name: str
    round_no: int
    n_flipped: int = 0
    tr_gain: int = 0          # net wins gained on flipped games, train
    tr_n: int = 0
    va_gain: int = 0
    va_n: int = 0
    seasons_better: int = 0
    seasons_n: int = 0
    full_train: float = 0.0
    full_val: float = 0.0
    base_train: float = 0.0
    base_val: float = 0.0
    notes: str = ""

    @property
    def tr_w(self):
        return (self.tr_n + self.tr_gain) // 2 if self.tr_n else 0

    def summary(self) -> str:
        def part(gain, n, lab):
            if n == 0:
                return f"{lab} n=0"
            w = (n + gain) // 2
            return f"{lab} {w}-{n-w} ({w/n:.1%}, z={_z(w, n-w):+.2f}, n={n})"
        return (f"{self.name}\n"
                f"    flips {self.n_flipped} games | "
                f"{part(self.tr_gain, self.tr_n, 'TRAIN')} | "
                f"{part(self.va_gain, self.va_n, 'VAL')}\n"
                f"    full slate: train {self.base_train:.1%}->{self.full_train:.1%}  "
                f"validate {self.base_val:.1%}->{self.full_val:.1%}  | "
                f"beats shipped in {self.seasons_better}/{self.seasons_n} dev seasons")


def evaluate(rows, name, side_fn, round_no=0, notes="") -> Result:
    """side_fn(g) -> 'home' / 'away' / None.  None means 'defer to shipped'.

    Only the games where side_fn DISAGREES with the shipped model matter --
    everywhere else the two are identical by construction, so including those
    games would just dilute the signal with shared picks.
    """
    res = Result(name=name, round_no=round_no, notes=notes)
    per_season = {}
    full = {"tr": [0, 0], "va": [0, 0]}
    base = {"tr": [0, 0], "va": [0, 0]}

    for g in rows:
        if g["home_covers"] is None:
            continue
        base_side = shipped_side(g)
        prop = side_fn(g)
        new_side = base_side if prop is None else prop

        bucket = "tr" if g["season"] in TRAIN else "va"
        for tag, side in (("base", base_side), ("full", new_side)):
            d = base if tag == "base" else full
            if won(g, side) == "W":
                d[bucket][0] += 1
            else:
                d[bucket][1] += 1

        if new_side == base_side:
            continue
        res.n_flipped += 1
        ps = per_season.setdefault(g["season"], [0, 0])
        new_w = won(g, new_side) == "W"
        if bucket == "tr":
            res.tr_n += 1
            res.tr_gain += 1 if new_w else -1
        else:
            res.va_n += 1
            res.va_gain += 1 if new_w else -1
        ps[0 if new_w else 1] += 1

    def rate(d):
        return d[0] / (d[0] + d[1]) if (d[0] + d[1]) else 0.0
    res.full_train, res.full_val = rate(full["tr"]), rate(full["va"])
    res.base_train, res.base_val = rate(base["tr"]), rate(base["va"])
    res.seasons_n = len(per_season)
    res.seasons_better = sum(1 for w, l in per_season.values() if w > l)
    return res


def report(res: Result, ledger: dict, register=True) -> Result:
    """Print a verdict scored against the CUMULATIVE multiplicity bar."""
    n_prior = len(ledger["tests"])
    n_now = n_prior + (1 if register else 0)
    bar = sidak_bar(max(n_now, 1))
    tot_n = res.tr_n + res.va_n
    tot_w = (tot_n + res.tr_gain + res.va_gain) // 2
    z = _z(tot_w, tot_n - tot_w)

    tr_pos = res.tr_gain > 0
    va_pos = res.va_gain > 0
    consistent = tr_pos and va_pos

    print("  " + res.summary())
    if tot_n == 0:
        verdict = "NO EFFECT -- rule never fires"
    elif not consistent:
        verdict = ("SIGN FLIP -- dead (train and validate disagree)"
                   if (tr_pos != va_pos) else "NEGATIVE in both eras -- dead")
    elif abs(z) < bar:
        verdict = (f"consistent but UNDERPOWERED: z={z:+.2f} vs "
                   f"multiplicity bar {bar:.2f} ({n_now} tests) -- not a finding")
    else:
        verdict = (f"*** SURVIVES: z={z:+.2f} clears multiplicity bar {bar:.2f} "
                   f"({n_now} tests) -- CANDIDATE ***")
    print(f"    -> {verdict}\n")

    if register:
        ledger["tests"].append({
            "name": res.name, "round": res.round_no, "n_flipped": res.n_flipped,
            "train_gain": res.tr_gain, "val_gain": res.va_gain,
            "z": round(z, 3), "verdict": verdict.split(" --")[0], "notes": res.notes,
        })
    return res


def header(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    rows = load_dev()
    assert not ({r["season"] for r in rows} & HOLDOUT), "holdout leaked into dev"
    ledger = load_ledger()
    print(f"DEV {len(rows)} games | train {sum(1 for r in rows if r['season'] in TRAIN)} "
          f"(2014-19), validate {sum(1 for r in rows if r['season'] in VAL)} (2020-22)")
    print(f"holdout {sorted(HOLDOUT)} excluded at load")
    print(f"ledger: {len(ledger['tests'])} hypotheses tested so far; "
          f"multiplicity bar for the next one is z={sidak_bar(len(ledger['tests'])+1):.2f}")

    n_situ = sum(1 for r in rows if r["home_rest"] is not None)
    print(f"situational context present on {n_situ}/{len(rows)} dev games\n")

    # Rounds are appended below by scripts/pickem_idea_rounds.py imports.
    from pickem_idea_rounds import run_rounds  # noqa: E402
    run_rounds(rows, ledger, evaluate, report, header)
    save_ledger(ledger)
    print(f"\nledger now holds {len(ledger['tests'])} tested hypotheses -> {LEDGER}")


if __name__ == "__main__":
    main()
