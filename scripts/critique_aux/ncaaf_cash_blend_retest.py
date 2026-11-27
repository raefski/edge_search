#!/usr/bin/env python3
"""NCAAF critique cycle 10 (2026-11-27): the cash salary blend, held out.

    python3 scripts/critique_aux/ncaaf_cash_blend_retest.py [W[,W..]]   # default 0.5

Repaired boards (ncaaf_lineup_lab.REPAIR), the production cash optimizer, the real
Double Up fields. k_pos (DK points per $1k by position, rows with proj >= 3) is fitted
on EARLIER dates only for each slate; w is not refit (cycle 9 pre-registered 0.5).
The baseline is the pre-cycle-10 build (w = 0). Prints all slates, the slates after
cycle 9 (11/14, 11/20) on their own, the earlier ones, and per contest.

`report --repair` uses production's fixed k (fitted on every date), so its gain is
in-sample in k; THIS script is the held-out number.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT)]
import ncaaf_lineup_lab as L                                    # noqa: E402

L.REPAIR = True
B, F, T = L.boards(), L.fields(), L.dk_points()
data = L.player_rows(B, T)                     # (date, pos, proj, salary_k, actual)
W = [float(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["0.5"])]
NEW = ("2026-11-14", "2026-11-20")


def ppk(train):
    out = {}
    for pos in ("QB", "RB", "WR"):
        rs = [r for r in train if r[1] == pos and r[2] >= 3]
        if rs:
            out[pos] = sum(r[4] for r in rs) / sum(r[3] for r in rs)
    return out


def at(w, k, Bd):
    L.CASH_W, L.CASH_K = w, k
    return L.rows(L.theory.Z_CASH, ("cash",), Bd, F, T)


base = at(0.0, None, B)
bmap = {r["contest"]: r for r in base}
dates = sorted({r["date"] for r in base})
res, K = {}, {}
for w in W:
    for d in dates:
        K[d] = k = ppk([r for r in data if r[0] < d])
        Bd = {g: ps for g, ps in B.items() if ps[0]["date"] == d}
        # no earlier data (the first slate): k is empty and every row keeps its ladder
        res[(w, d)] = at(w, k or {"none": 0}, Bd)
print(L.HEAD)
for lab, sel in (("all", dates), ("new 11/14+11/20", NEW),
                 ("pre-cycle-9", [d for d in dates if d not in NEW])):
    print(L.summary([r for r in base if r["date"] in sel], f"w=0 (pre-cycle-10) {lab}"))
    for w in W:
        print(L.summary(sum((res[(w, d)] for d in sel), []), f"w={w} fwd k {lab}", bmap))
for w in W:
    print(f"\nper contest w={w}: date contest field line | w=0 pts/pctile/cashed | blend pts/pctile/cashed | k")
    for d in dates:
        for r in res[(w, d)]:
            b = bmap[r["contest"]]
            print(d, r["contest"], r["field_n"], round(r["line"], 1), "|", round(b["points"], 1),
                  round(b["pctile"], 2), b["cashed"], "|", round(r["points"], 1), round(r["pctile"], 2),
                  r["cashed"], "|", {p: round(v, 2) for p, v in K[d].items()})
