#!/usr/bin/env python3
"""Every DFS result number in one machine-readable file: data/dfs_scorecard.json.

The "DFS ROI by Sport" dashboard (claude.ai artifact N4rKiCCFV2h9LHcTTq3Xc5)
shows these numbers to a person; this file gives the same numbers to a script
or an LLM, so neither has to scrape a page or recompute anything. It reads the
same rows the dashboard reads (scripts/dfs_roi_rows.py: build, updates,
proj_rows) and computes the same statistics the page does, so a number here
matches the number on the page.

Every table is a flat list of records (long format), one per group, and every
record carries its own sample size (entries, contests, slates; players) and an
enough_data flag (>= 5 slates; for accuracy also >= 50 players), so a reader
can never quote a number without its n. Tables:

  roi               period x sport x type: ROI, 90% range (slate-clustered),
                    cash rate, field beaten, and the nightly-ROI box
  contest_groups    period x sport x type x dim x bucket (field size, contest
                    size, slate size, overlay at lock, overlay alert)
  slates            period x sport x type x slate date: that night, the last 5
                    slates, and to date (ROI, field beaten, running profit)
  code_updates      commits that changed one sport's model code
  projection_accuracy  sport x position (and all sports x sport): MAE, bias,
                    R^2, the fitted line
  players           sport x player: slates, mean projected / actual, bias,
                    MAE, field GPP ownership vs our guess
  player_games      one row per player per slate (projected, actual, miss,
                    ownership): the rows every accuracy number comes from

sport "all" is every sport together; type "all" is cash + GPP together (the
roi and slates tables only: contest groups never pool cash and GPP).

Usage (DFS_COMMANDS.md has the same, for people):
  python3 scripts/dfs_scorecard.py                    rebuild the file
  python3 scripts/dfs_scorecard.py show               every sport (from the file)
  python3 scripts/dfs_scorecard.py show NCAAF         one sport
  python3 scripts/dfs_scorecard.py show NCAAF "Love"  one player in that sport
"""
import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dfs_roi_rows  # noqa: E402

sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402

ENOUGH_SLATES = 5     # CLAUDE.md: "not enough data" below ~5 slates
ENOUGH_PLAYERS = 50
OUT = ROOT / "data/dfs_scorecard.json"
Z90 = 1.645
PERIODS = {"model": dfs_roi_rows.MODEL_ERA, "all": None}
DEFINITIONS = {
    "roi": "won / fee - 1; won = DK cash winnings + tickets at face value",
    "roi_lo, roi_hi": "90% range of ROI; each slate date is one draw (ratio estimator, slate-clustered "
                      "standard error x 1.645); roi_lo is floored at -100%",
    "cash_rate": "share of entries that won anything",
    "beat": "share of the field an entry finished ahead of, averaged: 1 = won, 0.5 = median entry",
    "est_share": "share of entries whose payout is estimated from a stretched payout table rather than DK's "
                 "own winnings (0 when the DK entry-history export covers every contest)",
    "slate_roi": "distribution of nightly ROI, one value per slate date (each night counts the same, whatever "
                 "was entered); outliers are beyond 1.5 x IQR",
    "sd": "swing per entry: standard deviation of won / fee per entry",
    "dim.field": "entries in the contest at lock",
    "dim.cap": "contest size (max entries): DK's figure from the overlay monitor's lobby logs since 2026-10-11, "
               "otherwise prize pool / (fee x 0.845-0.880), right to about 5%",
    "dim.games": "games on the slate (imported contests only; not NASCAR/MMA)",
    "dim.overlay": "prize pool > fee x entries at lock",
    "dim.alert": "the overlay monitor pushed an alert for the contest (alerts began 2026-10-13); "
                 "contests that filled anyway still count",
    "roll5_roi": "ROI of this slate and the 4 before it, dollars pooled",
    "projection_accuracy": "our last pre-lock projection vs DK points, matched by DK draft group; only players "
                           "someone in the exported contest drafted; scratches count as 0; NHL after-the-fact "
                           "rebuilds excluded. bias = mean(actual - projected): > 0 means we project too low. "
                           "r2 = squared correlation; slope/intercept = least-squares actual ~ projected",
    "own_gpp": "% of the field that drafted the player in the largest GPP we exported on that draft group "
               "(cash contests never: small cash fields pile onto the top play)",
    "own_pred": "our own pre-lock GPP ownership guess, in percent",
    "miss": "actual - projected for one player on one slate",
    "players.enough_data": "the player appeared on >= 5 graded slates",
    "position": "DK's first listed position (PG/SG -> PG); NHL LW/RW -> W; MLB SP/RP -> P",
    "period.model": f"slates on or after {dfs_roi_rows.MODEL_ERA}, when the DFS models started",
    "period.all": "every paid entry in the DK history (back to 2022)",
}


def r4(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, 4)


def roi_stats(rows):
    """Same arithmetic as the dashboard's roiStats()."""
    fee = sum(e["fee"] for e in rows)
    won = sum(e["won"] for e in rows)
    slates = defaultdict(lambda: [0.0, 0.0])
    for e in rows:
        slates[e["date"]][0] += e["won"]
        slates[e["date"]][1] += e["fee"]
    r, k = won / fee, len(slates)
    se = (math.sqrt(sum((w - r * f) ** 2 for w, f in slates.values()) * k / (k - 1)) / fee) if k > 1 else None
    beats = [e["beat"] for e in rows if e["beat"] is not None]
    return dict(
        entries=len(rows), contests=len({e["contest_id"] for e in rows}), slates=k,
        fee=round(fee, 2), won=round(won, 2), net=round(won - fee, 2), roi=r4(r - 1),
        roi_lo=r4(max(-1.0, r - 1 - Z90 * se)) if se is not None else None,
        roi_hi=r4(r - 1 + Z90 * se) if se is not None else None,
        cash_rate=r4(sum(e["cashed"] for e in rows) / len(rows)),
        beat=r4(statistics.fmean(beats)) if beats else None,
        est_share=r4(sum(e["source"] == "estimated" for e in rows) / len(rows)),
        enough_data=k >= ENOUGH_SLATES)


def quantile(v, q):
    """d3.quantileSorted (R-7), as the page's boxes use."""
    i = (len(v) - 1) * q
    lo = math.floor(i)
    return v[lo] if lo + 1 >= len(v) else v[lo] + (v[lo + 1] - v[lo]) * (i - lo)


def box(vals):
    v = sorted(vals)
    q1, med, q3 = quantile(v, .25), quantile(v, .5), quantile(v, .75)
    iqr = q3 - q1
    inside = [x for x in v if q1 - 1.5 * iqr <= x <= q3 + 1.5 * iqr]
    return dict(n=len(v), min=r4(v[0]), q1=r4(q1), median=r4(med), q3=r4(q3), max=r4(v[-1]),
                whisker_lo=r4(min(inside)), whisker_hi=r4(max(inside)),
                outliers=sum(x < min(inside) or x > max(inside) for x in v))


def per_slate(rows):
    m = defaultdict(lambda: [0.0, 0.0])
    for e in rows:
        m[e["date"]][0] += e["won"]
        m[e["date"]][1] += e["fee"]
    return [w / f - 1 for w, f in m.values()]


def size_bucket(n):
    if n is None:
        return None
    return ((1, "25 or fewer") if n <= 25 else (2, "26-100") if n <= 100 else (3, "101-500") if n <= 500
            else (4, "501-5,000") if n <= 5000 else (5, "over 5,000"))


DIMS = {
    "field": lambda e: size_bucket(e["field"]),
    "cap": lambda e: size_bucket(e["cap"]),
    "games": lambda e: None if e["games"] is None else ((1, "1-2 games") if e["games"] <= 2 else
                                                        (2, "3-4 games") if e["games"] <= 4 else
                                                        (3, "5-7 games") if e["games"] <= 7 else (4, "8+ games")),
    "overlay": lambda e: None if e["overlay"] is None else ((1, "ended in overlay") if e["overlay"] > 0
                                                            else (2, "filled, no overlay")),
    "alert": lambda e: None if e["alerted"] is None else ((1, "alert sent") if e["alerted"] else (2, "no alert")),
}


def scorecard(entries, updates, proj):
    roi, groups, slates = [], [], []
    for period, start in PERIODS.items():
        inp = [e for e in entries if start is None or e["date"] >= start]
        sports = sorted({e["sport"] for e in inp})
        for sport in ["all", *sports]:
            sp = [e for e in inp if sport == "all" or e["sport"] == sport]
            for typ in ("all", "cash", "gpp"):
                rows = [e for e in sp if typ == "all" or e["type"] == typ]
                if not rows:
                    continue
                roi.append(dict(period=period, sport=sport, type=typ, **roi_stats(rows),
                                slate_roi=box(per_slate(rows))))
                # one row per slate date, with the last 5 and to-date figures
                by_date = defaultdict(list)
                for e in rows:
                    by_date[e["date"]].append(e)
                dates = sorted(by_date)
                cum_net = 0.0
                for i, d in enumerate(dates):
                    night = by_date[d]
                    last5 = [e for x in dates[max(0, i - 4):i + 1] for e in by_date[x]]
                    to_date = [e for x in dates[:i + 1] for e in by_date[x]]
                    cum_net += sum(e["net"] for e in night)
                    b = [e["beat"] for e in night if e["beat"] is not None]
                    b5 = [e["beat"] for e in last5 if e["beat"] is not None]
                    slates.append(dict(
                        period=period, sport=sport, type=typ, date=d, entries=len(night),
                        fee=round(sum(e["fee"] for e in night), 2), won=round(sum(e["won"] for e in night), 2),
                        net=round(sum(e["net"] for e in night), 2),
                        roi=r4(sum(e["won"] for e in night) / sum(e["fee"] for e in night) - 1),
                        beat=r4(statistics.fmean(b)) if b else None,
                        roll5_roi=r4(sum(e["won"] for e in last5) / sum(e["fee"] for e in last5) - 1),
                        roll5_beat=r4(statistics.fmean(b5)) if b5 else None,
                        cum_roi=r4(sum(e["won"] for e in to_date) / sum(e["fee"] for e in to_date) - 1),
                        cum_net=round(cum_net, 2)))
                if typ == "all":
                    continue
                for dim, pick in DIMS.items():
                    bucketed = defaultdict(list)
                    for e in rows:
                        b = pick(e)
                        if b:
                            bucketed[b].append(e)
                    for (order, label), list_ in sorted(bucketed.items()):
                        ret = [e["won"] / e["fee"] for e in list_]
                        groups.append(dict(period=period, sport=sport, type=typ, dim=dim, order=order,
                                           bucket=label, **roi_stats(list_),
                                           sd=r4(statistics.stdev(ret)) if len(ret) > 1 else None))
    acc = []

    def fit(sport, group_by, group, rows):
        p = [r["proj"] for r in rows]
        a = [r["actual"] for r in rows]
        mp, ma = statistics.fmean(p), statistics.fmean(a)
        sxy = sum((x - mp) * (y - ma) for x, y in zip(p, a))
        sxx = sum((x - mp) ** 2 for x in p)
        syy = sum((y - ma) ** 2 for y in a)
        slope = sxy / sxx if sxx else None
        n_slates = len({(r["sport"], r["slate"]) for r in rows})
        acc.append(dict(sport=sport, group_by=group_by, group=group, players=len(rows), slates=n_slates,
                        first=min(r["date"] for r in rows), last=max(r["date"] for r in rows),
                        mean_proj=r4(mp), mean_actual=r4(ma), bias=r4(ma - mp),
                        mae=r4(statistics.fmean(abs(y - x) for x, y in zip(p, a))),
                        r2=r4(sxy * sxy / (sxx * syy)) if sxx and syy else None,
                        slope=r4(slope), intercept=r4(ma - slope * mp) if slope is not None else None,
                        enough_data=n_slates >= ENOUGH_SLATES and len(rows) >= ENOUGH_PLAYERS))

    fit("all", "all", "all", proj)
    by_sport = defaultdict(list)
    for r in proj:
        by_sport[r["sport"]].append(r)
    for sport, rows in sorted(by_sport.items()):
        fit("all", "sport", sport, rows)
        fit(sport, "all", "all", rows)
        by_pos = defaultdict(list)
        for r in rows:
            by_pos[r["pos"]].append(r)
        for pos, list_ in sorted(by_pos.items()):
            fit(sport, "position", pos, list_)

    games = [dict(sport=r["sport"], date=r["date"], slate=r["slate"], name=r["name"], team=r.get("team"),
                  pos=r["pos"], salary=r.get("salary"), proj=r["proj"], actual=r["actual"],
                  miss=r4(r["actual"] - r["proj"]), own_gpp=r.get("own_gpp"), own_pred=r.get("own_pred"))
             for r in proj]
    by_player = defaultdict(list)
    for g in games:
        by_player[(g["sport"], norm(g["name"]))].append(g)
    players = []
    for (sport, _), gs in sorted(by_player.items()):
        og = [g["own_gpp"] for g in gs if g["own_gpp"] is not None]
        op = [g["own_pred"] for g in gs if g["own_pred"] is not None]
        sal = [g["salary"] for g in gs if g["salary"]]
        n_slates = len({g["slate"] for g in gs})
        players.append(dict(
            sport=sport, name=gs[-1]["name"], team=gs[-1]["team"], pos=gs[-1]["pos"], slates=n_slates,
            first=gs[0]["date"], last=gs[-1]["date"], salary=round(statistics.fmean(sal)) if sal else None,
            mean_proj=r4(statistics.fmean(g["proj"] for g in gs)),
            mean_actual=r4(statistics.fmean(g["actual"] for g in gs)),
            bias=r4(statistics.fmean(g["miss"] for g in gs)),
            mae=r4(statistics.fmean(abs(g["miss"]) for g in gs)),
            own_gpp=r4(statistics.fmean(og)) if og else None, own_pred=r4(statistics.fmean(op)) if op else None,
            enough_data=n_slates >= ENOUGH_SLATES))

    hist = [e for e in entries if e["source"] == "DK history"]
    return dict(
        schema="dfs_scorecard/1",
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        made_by="python3 -m harness run dfs-scorecard (scripts/dfs_scorecard.py)",
        human_view="https://claude.ai/artifact/N4rKiCCFV2h9LHcTTq3Xc5",
        sources=dict(
            entries=len(entries), dk_history_through=max(e["date"] for e in hist) if hist else None,
            estimated_payouts=sum(e["source"] == "estimated" for e in entries),
            projection_rows=len(proj), code_updates=len(updates),
            files=["DK entry-history exports (data/ and Windows Downloads)", "data/contests.db",
                   "data/contest_fill_log.csv", "data/contest_finals.csv", "data/harness/alerts.jsonl",
                   "git log of edge/"]),
        thresholds=dict(enough_slates=ENOUGH_SLATES, enough_players=ENOUGH_PLAYERS),
        definitions=DEFINITIONS,
        roi=roi, contest_groups=groups, slates=slates,
        code_updates=[{k: u[k] for k in ("sport", "date", "hash", "subject", "n_sports")} for u in updates],
        projection_accuracy=acc, players=players, player_games=games,
    )


# ---- reading it: three zoom levels, printed for a person or an LLM --------

def pct(v, sign=True):
    return "  -  " if v is None else f"{v * 100:+.0f}%" if sign else f"{v * 100:.0f}%"


def roi_cell(r):
    if r is None:
        return "-"
    rng = f" [{pct(r['roi_lo'])},{pct(r['roi_hi'])}]" if r["roi_lo"] is not None else ""
    return f"{pct(r['roi'])}{rng} {r['entries']}e/{r['slates']}s" + ("" if r["enough_data"] else " thin")


def show_all(card):
    roi = {(r["period"], r["sport"], r["type"]): r for r in card["roi"]}
    acc = {(r["sport"], r["group_by"], r["group"]): r for r in card["projection_accuracy"]}
    sports = sorted({r["sport"] for r in card["roi"] if r["sport"] != "all"})
    print("ROI since the models started (" + dfs_roi_rows.MODEL_ERA + "): ROI [90% range] entries/slates;"
          " 'thin' = under 5 slates")
    print(f"  {'sport':7} {'cash':34} {'GPP':34} last 5 slates")
    for sp in ["all", *sports]:
        last = [x for x in card["slates"] if (x["period"], x["sport"], x["type"]) == ("model", sp, "all")]
        print(f"  {sp:7} {roi_cell(roi.get(('model', sp, 'cash'))):34} {roi_cell(roi.get(('model', sp, 'gpp'))):34} "
              f"{pct(last[-1]['roll5_roi']) if last else '-'}")
    print("\nProjection accuracy (our pre-lock points vs DK points, drafted players): "
          "bias > 0 = we project too low")
    print(f"  {'sport':7} {'R2':>5} {'MAE':>5} {'bias':>6} {'players':>7} {'slates':>6}  weakest position (R2)")
    for sp in sports + sorted({r["sport"] for r in card["projection_accuracy"]} - set(sports) - {"all"}):
        a = acc.get((sp, "all", "all"))
        if not a:
            continue
        pos = [r for r in card["projection_accuracy"] if r["sport"] == sp and r["group_by"] == "position"
               and r["players"] >= 30 and r["r2"] is not None]
        weak = min(pos, key=lambda r: r["r2"]) if pos else None
        print(f"  {sp:7} {a['r2'] or 0:5.2f} {a['mae']:5.1f} {a['bias']:+6.1f} {a['players']:7} {a['slates']:6}  "
              + (f"{weak['group']} ({weak['r2']:.2f}, bias {weak['bias']:+.1f})" if weak else "-")
              + ("" if a["enough_data"] else "  thin"))
    print("\nContest groups (all sports, model era, groups with 5+ slates): best and worst per dimension")
    for typ in ("cash", "gpp"):
        for dim in DIMS:
            gs = [g for g in card["contest_groups"] if (g["period"], g["sport"], g["type"], g["dim"])
                  == ("model", "all", typ, dim) and g["enough_data"]]
            if len(gs) < 2:
                continue
            hi, lo = max(gs, key=lambda g: g["roi"]), min(gs, key=lambda g: g["roi"])
            print(f"  {typ:4} {dim:8} best {hi['bucket']} {roi_cell(hi)}  |  worst {lo['bucket']} {roi_cell(lo)}")
    print("\nZoom in: python3 scripts/dfs_scorecard.py show <SPORT>")


def show_sport(card, sport):
    sport = sport.upper()
    rows = {(r["period"], r["type"]): r for r in card["roi"] if r["sport"] == sport}
    if not rows and not any(r["sport"] == sport for r in card["players"]):
        sys.exit(f"no {sport} in the scorecard; sports: "
                 + ", ".join(sorted({r['sport'] for r in card['players']} | {r['sport'] for r in card['roi']})))
    print(f"{sport} ROI: ROI [90% range] entries/slates, cash rate, field beaten")
    for period in ("model", "all"):
        for typ in ("cash", "gpp", "all"):
            r = rows.get((period, typ))
            if r:
                print(f"  {period:5} {typ:4} {roi_cell(r):40} cashed {pct(r['cash_rate'], False):>4}  "
                      f"beat {pct(r['beat'], False):>4}  net ${r['net']:,.0f}")
    print("\nLast 10 slates (cash + GPP): ROI that night, last 5, running profit")
    for x in [x for x in card["slates"] if (x["period"], x["sport"], x["type"]) == ("model", sport, "all")][-10:]:
        print(f"  {x['date']}  {x['entries']:2}e  {pct(x['roi']):>6}  last5 {pct(x['roll5_roi']):>6}  "
              f"to date ${x['cum_net']:,.2f}")
    print("\nContest groups since the models started (cash and GPP never pooled)")
    for typ in ("cash", "gpp"):
        for g in [g for g in card["contest_groups"] if (g["period"], g["sport"], g["type"]) == ("model", sport, typ)]:
            print(f"  {typ:4} {g['dim']:8} {g['bucket']:20} {roi_cell(g)}")
    print("\nProjection accuracy by position (bias > 0 = we project too low)")
    for a in [a for a in card["projection_accuracy"] if a["sport"] == sport]:
        print(f"  {a['group']:5} R2 {a['r2'] or 0:4.2f}  MAE {a['mae']:4.1f}  bias {a['bias']:+5.1f}  "
              f"{a['players']} players / {a['slates']} slates" + ("" if a["enough_data"] else "  thin"))
    ps = [p for p in card["players"] if p["sport"] == sport and p["slates"] >= 3]
    for label, key in (("most under-projected (we were low)", lambda p: -p["bias"]),
                       ("most over-projected (we were high)", lambda p: p["bias"])):
        print(f"\nPlayers {label}, 3+ slates: projected -> actual, field GPP own vs ours")
        for p in sorted(ps, key=key)[:8]:
            print(f"  {p['name']:24} {p['pos']:4} {p['team'] or '':5} {p['slates']:2}s  {p['mean_proj']:5.1f} -> "
                  f"{p['mean_actual']:5.1f} ({p['bias']:+.1f})  own {p['own_gpp'] or 0:4.1f}% vs {p['own_pred'] or 0:4.1f}%")
    ups = [u for u in card["code_updates"] if u["sport"] == sport][-5:]
    if ups:
        print("\nLast code updates")
        for u in ups:
            print(f"  {u['date']} {u['hash'][:8]} {u['subject'][:100]}")
    print(f'\nZoom in: python3 scripts/dfs_scorecard.py show {sport} "<player name>"')


def show_player(card, sport, name):
    sport, want = sport.upper(), norm(name)
    hits = [p for p in card["players"] if p["sport"] == sport and want in norm(p["name"])]
    exact = [p for p in hits if norm(p["name"]) == want]
    hits = exact or hits
    if len(hits) != 1:
        sys.exit(f"{len(hits)} {sport} players match {name!r}"
                 + (": " + ", ".join(p["name"] for p in hits[:20]) if hits else ""))
    p = hits[0]
    print(f"{p['name']} ({sport} {p['pos']}, {p['team']}): {p['slates']} slates {p['first']} to {p['last']}, "
          f"avg salary ${p['salary'] or 0:,}")
    print(f"  projected {p['mean_proj']:.1f}  actual {p['mean_actual']:.1f}  bias {p['bias']:+.1f}  MAE {p['mae']:.1f}"
          f"  field GPP own {p['own_gpp'] or 0:.1f}% vs our guess {p['own_pred'] or 0:.1f}%"
          + ("" if p["enough_data"] else "  (thin: under 5 slates)"))
    print(f"\n  {'date':10} {'salary':>7} {'proj':>5} {'actual':>6} {'miss':>6} {'own':>5} {'ours':>5}")
    for g in [g for g in card["player_games"] if g["sport"] == sport and norm(g["name"]) == norm(p["name"])]:
        fmt = lambda v: "  -  " if v is None else f"{v:5.1f}"
        print(f"  {g['date']:10} {int(g['salary'] or 0):7,} {g['proj']:5.1f} {g['actual']:6.1f} {g['miss']:+6.1f} "
              f"{fmt(g['own_gpp'])} {fmt(g['own_pred'])}")


def main():
    ap = argparse.ArgumentParser(description="DFS scorecard: rebuild it, or show it at three zoom levels")
    ap.add_argument("view", nargs="*", help='nothing = rebuild; show; show NCAAF; show NCAAF "player"')
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    if a.view:
        if a.view[0] != "show" or len(a.view) > 3:
            ap.error('use: show | show <SPORT> | show <SPORT> "<player>"')
        if not Path(a.out).exists():
            sys.exit(f"{a.out} missing: python3 -m harness run dfs-scorecard")
        card = json.loads(Path(a.out).read_text())
        print(f"DFS scorecard built {card['generated_at']}; DK entry history through "
              f"{card['sources']['dk_history_through']}; {card['sources']['estimated_payouts']} payouts estimated\n")
        if len(a.view) == 1:
            show_all(card)
        elif len(a.view) == 2:
            show_sport(card, a.view[1])
        else:
            show_player(card, a.view[1], a.view[2])
        return
    card = scorecard(dfs_roi_rows.build(), dfs_roi_rows.updates(), dfs_roi_rows.proj_rows())
    # one record per line: a reader (or grep) sees a whole group at once
    head = {k: v for k, v in card.items() if not isinstance(v, list) or k == "sources"}
    parts = [json.dumps(head, indent=1)[:-2]]
    for k, v in card.items():
        if isinstance(v, list):
            parts.append(f',\n "{k}": [\n  ' + ",\n  ".join(json.dumps(r, separators=(",", ":")) for r in v) + "\n ]")
    Path(a.out).write_text("".join(parts) + "\n}\n")
    print(f"{a.out}: {len(card['roi'])} roi rows, {len(card['contest_groups'])} contest groups, "
          f"{len(card['slates'])} slate rows, {len(card['projection_accuracy'])} accuracy rows, "
          f"{len(card['players'])} players", file=sys.stderr)


if __name__ == "__main__":
    main()
