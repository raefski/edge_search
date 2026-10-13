#!/usr/bin/env python3
"""Which buzz / tout sources can be trusted? DFS ownership buzz per YouTube
channel, and pick'em ATS touts per channel, with intervals.

    python3 scripts/buzz_trust.py              # both halves
    python3 scripts/buzz_trust.py --dfs        # DFS ownership buzz only
    python3 scripts/buzz_trust.py --pickem     # pick'em touts only

DFS: DOES A CHANNEL'S MENTIONS ADD OWNERSHIP SIGNAL BEYOND OUR VALUE MODEL?
Baseline = the ownership our pre-lock log actually predicted for that contest
(data/contests.db; NHL's shipped model has BUZZ_BETA = 0 and the graded NFL
GPPs predate NFL buzz, so the baseline is value-only everywhere). For one
feature x (a channel's log1p mentions, or all channels together) the residual
log((actual+0.5)/(pred+0.5)) is regressed on x over the TRAINING slates, the
held-out slate's prediction is tilted by exp(b * x) and renormalised to the
same total, and the held-out MAE change is recorded. Leave-one-slate-out, so
every number is out of sample. GPP and cash are graded apart.

PICK'EM: EVERY CHANNEL AGAINST THE CLOSING LINE (nflverse), both seasons in
data/media_ats_picks.csv (scripts/pickem_media_backtest.py collects it; one call
per channel per game, its last before the deadline). Beyond the per-channel
record: is the spread of channel win rates wider than coin-flipping channels
would produce (dispersion test by simulation), what is each channel's
empirical-Bayes shrunk rate, and does 2025 skill persist into 2026?
"""
from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402
from scripts.contest_stats import wilson  # noqa: E402

DB = ROOT / "data" / "contests.db"
LEDGER = ROOT / "data" / "source_ledger.csv"


# --------------------------------------------------------------------------
# DFS
# --------------------------------------------------------------------------
def slates(conn, sport: str, ctype: str) -> dict:
    """{date: {"cid", "rows": [(player_norm, pos, pred, actual)]}} for every
    final-or-not contest of this type with a matched ownership log (ownership
    is final at lock, so an unfinished export still counts here)."""
    out = {}
    for cid, date, pg, pdate in conn.execute(
            "SELECT contest_id, slate_date, proj_draft_group, proj_date FROM contest_match "
            "WHERE sport=? AND type=? AND ok_own=1 ORDER BY slate_date", (sport, ctype)):
        board = dict(conn.execute("SELECT player_norm, pct_drafted FROM player_result WHERE contest_id=?", (cid,)))
        q = ("SELECT player_norm, pos, own_pred FROM projection WHERE source='ours' AND sport=? AND date=? AND "
             + ("draft_group=?" if pg is not None else "draft_group IS NULL"))
        rows = [(k, pos, own, board.get(k, 0.0)) for k, pos, own in
                conn.execute(q, (sport, pdate, pg) if pg is not None else (sport, pdate)) if own is not None]
        if rows and date not in out:          # one contest per slate (the first, biggest board wins ties)
            out[date] = {"cid": cid, "rows": rows}
    return out


def channel_mentions(sport: str) -> tuple[dict, dict, dict]:
    """({(date, channel_id): {player_norm: mentions}}, {channel_id: name}, {channel_id: n videos})."""
    path = ROOT / "data" / f"buzz_{sport.lower()}_videos.csv"
    per, names, vids = defaultdict(lambda: defaultdict(float)), {}, defaultdict(set)
    if not path.exists():
        return {}, {}, {}
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if not r.get("player"):
                continue
            per[(r["date"], r["channel_id"])][norm(r["player"])] += float(r.get("mentions") or 0)
            names[r["channel_id"]] = r["channel"]
            vids[r["channel_id"]].add(r["video_id"])
    return per, names, {k: len(v) for k, v in vids.items()}


def _tilt(pred: np.ndarray, x: np.ndarray, b: float) -> np.ndarray:
    new = np.maximum((pred + 0.5) * np.exp(b * x) - 0.5, 0.0)
    return new * (pred.sum() / new.sum()) if new.sum() > 0 else pred


def loo(data: dict, feature) -> dict:
    """Leave-one-slate-out MAE change for feature(date, player_norm) -> x."""
    prepared = {}
    for date, s in data.items():
        pred = np.array([r[2] for r in s["rows"]], float)
        act = np.array([r[3] for r in s["rows"]], float)
        x = np.array([feature(date, r[0]) for r in s["rows"]], float)
        prepared[date] = (pred, act, x, np.log((act + 0.5) / (pred + 0.5)))
    gains, bs = {}, {}
    for held in prepared:
        tr = [v for d, v in prepared.items() if d != held]
        if not tr:
            continue
        X = np.concatenate([v[2] for v in tr])
        Y = np.concatenate([v[3] for v in tr])
        if not len(X) or X.std() == 0:
            continue
        b = float(np.polyfit(X, Y, 1)[0])
        pred, act, x, _ = prepared[held]
        if x.std() == 0:
            gains[held] = 0.0
            bs[held] = b
            continue
        gains[held] = float(np.abs(pred - act).mean() - np.abs(_tilt(pred, x, b) - act).mean())
        bs[held] = b
    X = np.concatenate([v[2] for v in prepared.values()])
    Y = np.concatenate([v[3] for v in prepared.values()])
    b_all = float(np.polyfit(X, Y, 1)[0]) if X.std() > 0 else 0.0
    return {"gains": gains, "b": bs, "b_all": b_all,
            "base_mae": {d: float(np.abs(v[0] - v[1]).mean()) for d, v in prepared.items()}}


def loo_marginal(data: dict, rest, mine) -> dict:
    """Held-out MAE gain of adding feature `mine` to a tilt that already uses
    `rest` (all OTHER channels): is this channel worth anything on top of the
    rest of the feed? Both fitted on the training slates only."""
    prep = {}
    for date, s in data.items():
        pred = np.array([r[2] for r in s["rows"]], float)
        act = np.array([r[3] for r in s["rows"]], float)
        X = np.array([[1.0, rest(date, r[0]), mine(date, r[0])] for r in s["rows"]], float)
        prep[date] = (pred, act, X, np.log((act + 0.5) / (pred + 0.5)))
    gains = {}
    for held in prep:
        tr = [v for d, v in prep.items() if d != held]
        if not tr:
            continue
        X = np.concatenate([v[2] for v in tr])
        Y = np.concatenate([v[3] for v in tr])
        if X[:, 2].std() == 0:
            continue
        b_a = np.linalg.lstsq(X[:, :2], Y, rcond=None)[0]
        b_b = np.linalg.lstsq(X, Y, rcond=None)[0]
        pred, act, Xh, _ = prep[held]
        if Xh[:, 2].std() == 0:
            continue

        def tilt(x, b):
            new = np.maximum((pred + 0.5) * np.exp(x @ b) - 0.5, 0.0)
            return new * (pred.sum() / new.sum())
        a = np.abs(tilt(Xh[:, 1:2], b_a[1:]) - act).mean()
        bb = np.abs(tilt(Xh[:, 1:], b_b[1:]) - act).mean()
        gains[held] = float(a - bb)
    return gains


def verdict(gains: list[float]) -> str:
    if len(gains) < 2:
        return "insufficient (<2 held-out slates)"
    up = sum(g > 0.0005 for g in gains)
    down = sum(g < -0.0005 for g in gains)
    m = float(np.mean(gains))
    if m > 0 and up >= max(2, math.ceil(2 * len(gains) / 3)):
        return "KEEP"
    if m <= 0 and down >= up:
        return "DROP"
    return "downweight"


def dfs_report(conn) -> list[str]:
    out = ["## DFS ownership buzz: held-out value of each YouTube channel\n",
           "MAE change in percentage points of ownership per player when the held-out slate's prediction is "
           "tilted by the channel's mentions (positive = the channel helped). Fitted on the OTHER slates only. "
           "'alone' = the channel as the only buzz term; 'marginal' = what the channel adds to a tilt that "
           "already uses every OTHER channel (the decision-relevant number: keep/drop is judged on it).\n"]
    for sport in ("NFL", "NHL", "NCAAF"):
        per, names, vids = channel_mentions(sport)
        if not per:
            continue
        for ctype in ("gpp", "cash"):
            data = slates(conn, sport, ctype)
            dates = sorted(d for d in data if any(k[0] == d for k in per))
            data = {d: data[d] for d in dates}
            if not data:
                out.append(f"\n### {sport} {ctype}: no contest with both a matched ownership log and buzz\n")
                continue
            total = defaultdict(float)
            for (d, ch), m in per.items():
                for k, v in m.items():
                    total[(d, k)] += v
            allr = loo(data, lambda d, k: math.log1p(total.get((d, k), 0.0)))
            g = list(allr["gains"].values())
            out.append(f"\n### {sport} {ctype}: {len(data)} slates with buzz ({', '.join(dates)})\n")
            base = np.mean(list(allr["base_mae"].values()))
            out.append(f"Baseline (our logged value model) mean MAE {base:.2f}. ALL channels pooled: held-out gain "
                       + ", ".join(f"{d} {v:+.3f}" for d, v in allr["gains"].items())
                       + (f" -> mean {np.mean(g):+.3f}, b = {allr['b_all']:+.3f}" if g else "")
                       + f"  [{verdict(g)}]\n")
            rows = []
            for ch in names:
                cov = [d for d in data if (d, ch) in per]
                if len(cov) < 1 or vids.get(ch, 0) < 3:
                    continue
                mine = (lambda d, k, ch=ch: math.log1p(per.get((d, ch), {}).get(k, 0.0)))
                r = loo(data, mine)
                gains = [r["gains"][d] for d in cov if d in r["gains"]]
                rest = (lambda d, k, ch=ch: math.log1p(max(0.0, total.get((d, k), 0.0)
                                                           - per.get((d, ch), {}).get(k, 0.0))))
                mg = loo_marginal(data, rest, mine)
                marg = [mg[d] for d in cov if d in mg]
                rows.append((float(np.mean(marg)) if marg else float("nan"), names[ch], vids[ch], len(cov),
                             gains, marg, r["b_all"], verdict(marg)))
            rows.sort(key=lambda t: -(t[0] if not math.isnan(t[0]) else -9))
            out += ["| channel | videos | slates w/ mentions | alone: held-out gains | alone mean | "
                    "ON TOP OF the other channels: held-out gains | marginal mean | verdict (marginal) |",
                    "|---|---|---|---|---|---|---|---|"]
            for m, nm, nv, ns, gains, marg, b, v in rows:
                am = f"{np.mean(gains):+.3f}" if gains else "-"
                out.append(f"| {nm[:40]} | {nv} | {ns} | {' '.join(f'{x:+.3f}' for x in gains) or '-'} | {am} | "
                           f"{' '.join(f'{x:+.3f}' for x in marg) or '-'} | "
                           f"{'-' if math.isnan(m) else f'{m:+.3f}'} | {v} |")
            cnt = defaultdict(int)
            for *_, v in rows:
                cnt[v.split(" ")[0]] += 1
            out.append(f"\nVerdicts on the marginal test: {dict(cnt)}")
    out.append(ledger_note())
    return out


def ledger_note() -> str:
    """The in-sample per-channel numbers buzz_fit_nfl.py --channels wrote, for comparison."""
    if not LEDGER.exists():
        return ""
    per = defaultdict(list)
    with LEDGER.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r["domain"] == "nfl_dfs_ownership" and r["metric"] == "own_mae_gain":
                per[r["source"]].append(float(r["value"]))
    pos = sum(1 for v in per.values() if np.mean(v) > 0)
    return (f"\nFor comparison, data/source_ledger.csv (buzz_fit_nfl.py --channels, IN-SAMPLE drop-one-channel "
            f"at the shipped BUZZ_BETA): {pos} of {len(per)} NFL channels have a positive mean gain; the largest "
            f"is {max(np.mean(v) for v in per.values()):+.4f} pts of MAE.")


# --------------------------------------------------------------------------
# pick'em
# --------------------------------------------------------------------------
def nflverse() -> dict:
    """{(season, week, home): game row} from the local nflverse mirror (no network)."""
    for p in (ROOT / "data" / "cache" / "nflverse_games.csv", ROOT / "data" / "nflverse_games.csv"):
        if p.exists():
            with p.open(newline="") as fh:
                return {(r["season"], int(r["week"]), r["home_team"]): r for r in csv.DictReader(fh)
                        if r.get("home_score") and r.get("spread_line")}
    return {}


def ats(margin: float, line_home: float, side: str) -> str:
    v = margin + line_home
    if v == 0:
        return "P"
    return "W" if ((v > 0) == (side == "home")) else "L"


def pickem_report(seed: int = 0) -> list[str]:
    path = ROOT / "data" / "media_ats_picks.csv"
    games = nflverse()
    if not path.exists() or not games:
        return ["## Pick'em touts: no data/media_ats_picks.csv or nflverse mirror"]
    with path.open(newline="") as fh:
        picks = sorted(csv.DictReader(fh), key=lambda p: p["aired"])
    per = {}
    for p in picks:
        key = (p["season"], int(p["week"]), p["home"])
        if key in games:
            per[(key, p["channel_id"] or p["channel"])] = p
    res = defaultdict(lambda: defaultdict(list))         # channel -> season -> [W/L/P]
    names = {}
    by_game = defaultdict(list)
    for (key, ch), p in per.items():
        g = games[key]
        side = "home" if p["pick"] == p["home"] else "away"
        r = ats(float(g["home_score"]) - float(g["away_score"]), -float(g["spread_line"]), side)
        res[ch][key[0]].append(r)
        names[ch] = p["channel"]
        by_game[key].append(side)
    seasons = sorted({s for v in res.values() for s in v})
    out = ["## Pick'em touts against the CLOSING line (nflverse), one call per channel per game\n"]
    allw = {s: sum(x == "W" for v in res.values() for x in v.get(s, [])) for s in seasons}
    alll = {s: sum(x == "L" for v in res.values() for x in v.get(s, [])) for s in seasons}
    for s in seasons:
        lo, hi = wilson(allw[s], allw[s] + alll[s])
        out.append(f"- {s}: every tout pick {allw[s]}-{alll[s]} = {allw[s] / (allw[s] + alll[s]):.1%} "
                   f"[{lo:.1%}, {hi:.1%}] from {sum(1 for v in res.values() if v.get(s))} channels")

    # dispersion: are channels more different than coin flips would be? (2025, >= 20 picks)
    disp = []
    for s in seasons:
        rows = [(sum(x == "W" for x in v[s]), sum(x in "WL" for x in v[s])) for v in res.values()
                if sum(x in "WL" for x in v.get(s, [])) >= 20]
        if len(rows) < 5:
            continue
        w = np.array([a for a, _ in rows], float)
        n = np.array([b for _, b in rows], float)
        p = w.sum() / n.sum()
        stat = (((w - n * p) ** 2) / (n * p * (1 - p))).sum()
        rng = np.random.default_rng(seed)
        sims = rng.binomial(n.astype(int)[None, :].repeat(20000, 0), p)
        sstat = (((sims - n * p) ** 2) / (n * p * (1 - p))).sum(1)
        pval = float((sstat >= stat).mean())
        tau2 = max(0.0, float(np.var(w / n, ddof=1) - np.mean(p * (1 - p) / n)))
        disp.append((s, len(rows), p, stat, pval, tau2))
        out.append(f"- {s} dispersion ({len(rows)} channels with >= 20 picks): chi2 {stat:.1f} on {len(rows) - 1} df, "
                   f"p = {pval:.2f} vs coin-flipping channels at the pooled {p:.1%}; between-channel SD of true "
                   f"skill (method of moments) {math.sqrt(tau2):.1%}")

    # persistence
    pairs = []
    if len(seasons) >= 2:
        a, b = seasons[0], seasons[-1]
        for ch, v in res.items():
            na = sum(x in "WL" for x in v.get(a, []))
            nb = sum(x in "WL" for x in v.get(b, []))
            if na >= 20 and nb >= 8:
                pairs.append((sum(x == "W" for x in v[a]) / na, sum(x == "W" for x in v[b]) / nb, na, nb))
        if len(pairs) >= 5:
            x, y = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
            rng = np.random.default_rng(seed + 1)
            r = float(np.corrcoef(x, y)[0, 1])
            boots = []
            for _ in range(5000):
                i = rng.integers(0, len(pairs), len(pairs))
                if x[i].std() > 0 and y[i].std() > 0:
                    boots.append(np.corrcoef(x[i], y[i])[0, 1])
            out.append(f"- persistence {a} -> {b} ({len(pairs)} channels with >= 20 / >= 8 picks): correlation of "
                       f"win rates r = {r:+.2f} [{np.percentile(boots, 2.5):+.2f}, {np.percentile(boots, 97.5):+.2f}]")

    # consensus
    for s in seasons:
        follow = []
        for k, sides in by_game.items():
            if k[0] != s or len(sides) < 3:
                continue
            h = sides.count("home") / len(sides)
            if max(h, 1 - h) < 0.6:
                continue
            g = games[k]
            follow.append(ats(float(g["home_score"]) - float(g["away_score"]), -float(g["spread_line"]),
                              "home" if h >= 0.6 else "away"))
        w, l = follow.count("W"), follow.count("L")
        if w + l:
            lo, hi = wilson(w, w + l)
            out.append(f"- {s} tout CONSENSUS (>= 3 channels, >= 60% one side): follow {w}-{l} "
                       f"{w / (w + l):.1%} [{lo:.1%}, {hi:.1%}]; fade = the mirror image")

    # per channel, empirical-Bayes shrunk
    tau = {s: t for s, _, _, _, _, t in disp}
    pool = {s: p for s, _, p, _, _, _ in disp}
    out += ["\n| channel | " + " | ".join(f"{s} W-L (rate) [95%]" for s in seasons)
            + " | combined | EB-shrunk rate | verdict |",
            "|---|" + "---|" * (len(seasons) + 3)]
    rows = []
    for ch, v in res.items():
        tot_w = sum(x == "W" for s in seasons for x in v.get(s, []))
        tot_n = sum(x in "WL" for s in seasons for x in v.get(s, []))
        if tot_n < 15:
            continue
        cells = []
        for s in seasons:
            w = sum(x == "W" for x in v.get(s, []))
            n = sum(x in "WL" for x in v.get(s, []))
            if n:
                lo, hi = wilson(w, n)
                cells.append(f"{w}-{n - w} ({w / n:.0%}) [{lo:.0%}, {hi:.0%}]")
            else:
                cells.append("-")
        p0 = np.mean(list(pool.values())) if pool else 0.5
        t2 = np.mean(list(tau.values())) if tau else 0.0
        shrunk = p0 + (tot_w / tot_n - p0) * (t2 / (t2 + p0 * (1 - p0) / tot_n)) if t2 > 0 else p0
        lo, hi = wilson(tot_w, tot_n)
        if lo > 0.5:
            v_ = "KEEP (combined CI above 50%)"
        elif hi < 0.5:
            v_ = "FADE-candidate (CI below 50%) -- still not a signal to follow"
        else:
            v_ = "DROP (indistinguishable from a coin)"
        rows.append((tot_w / tot_n, names[ch], cells, f"{tot_w}-{tot_n - tot_w} ({tot_w / tot_n:.1%}) "
                     f"[{lo:.0%}, {hi:.0%}]", f"{shrunk:.1%}", v_))
    rows.sort(key=lambda r: -r[0])
    for _, nm, cells, comb, sh, v_ in rows:
        out.append(f"| {nm[:34]} | " + " | ".join(cells) + f" | {comb} | {sh} | {v_} |")

    tp = ROOT / "data" / "tout_picks.csv"
    if tp.exists():
        with tp.open(newline="") as fh:
            n = sum(1 for _ in csv.DictReader(fh))
        out.append(f"\ndata/tout_picks.csv (per-game tout grader): {n} picks on file -- far too few to grade a source.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dfs", action="store_true")
    ap.add_argument("--pickem", action="store_true")
    args = ap.parse_args()
    both = not (args.dfs or args.pickem)
    lines = []
    if both or args.dfs:
        conn = sqlite3.connect(str(DB))
        lines += dfs_report(conn)
    if both or args.pickem:
        lines += ["\n"] + pickem_report()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
