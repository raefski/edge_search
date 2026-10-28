#!/usr/bin/env python3
"""Public bettors whose every bet is on the record: do any of them actually win?

    python3 scripts/public_bettors.py collect            # TrustMyRecord + Juice Reel -> data/cache/public_bettors/
    python3 scripts/public_bettors.py collect --source jr --max-pages 300
    python3 scripts/public_bettors.py report             # per-bettor table + persistence test
    python3 scripts/public_bettors.py report --csv out.csv

WHY THESE TWO SITES. A tout's own X feed is a sales funnel: winners get a ✅
post, losers get deleted (TommyG 10/26: 23% of 9,367 archived posts gone, 129
gradable game bets 62-67, -8.4% ROI pre-game). These two sites take the bet
out of the tout's hands:

  - TrustMyRecord (trustmyrecord.com): the pick is locked on the site before
    the game, priced off a DraftKings board snapshot taken at that moment, and
    graded from ESPN finals. Small (91 users 10/26, 15 with 100+ picks).
  - Juice Reel (juicereel.com): bets are SYNCED from the bettor's own
    sportsbook account (ticket numbers, placement time, risk, payout), and
    the site stores closing-line value per bet (clvPct). Settled history is
    free JSON (/api/bets/<id>/settled); only OPEN bets are paywalled.

WHAT CAN STILL FOOL US, and what the report does about it:
  - The leaderboard is the survivors. A top-62 list ranked by profit is
    selected on luck, so its average ROI is biased up. The report's answer is
    PERSISTENCE: split each bettor's bets in time, and ask whether first-half
    ROI / CLV predicts second-half ROI / CLV across bettors. Skill persists,
    luck does not.
  - Juice Reel imports history from before a bettor goes public (Razorlines
    "joined Sep 2026" with a year of bets). That history is real money but it
    is also why they chose to go public. Bets placed after we first collect a
    bettor are the clean forward test; collect again later to grow it.
  - Win rate is the wrong yardstick at minus odds. ROI and CLV are reported;
    z is wins against each bet's own break-even (implied probability), so
    favourites and longshots are judged fairly.

Be polite: one request per ~1.1 s, one site at a time.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache" / "public_bettors"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
TMR_API = "https://trustmyrecord-api.onrender.com/api"
JR = "https://www.juicereel.com"
PAUSE = 1.1


def _get(url: str, as_json: bool = True):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html"})
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read().decode("utf-8", "replace")
            time.sleep(PAUSE)
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                time.sleep(PAUSE)
                return None
            time.sleep(5 * (attempt + 1))
        except Exception:
            time.sleep(5 * (attempt + 1))
    return None


# ---------------------------------------------------------------- collect

def collect_tmr(min_picks: int) -> None:
    d = OUT / "tmr"
    d.mkdir(parents=True, exist_ok=True)
    lb = _get(f"{TMR_API}/users/leaderboard?sortBy=rank&limit=1000&minPicks=1") or {}
    users = [u for u in lb.get("leaderboard", []) if (u.get("graded_picks") or 0) >= min_picks]
    (d / "_leaderboard.json").write_text(json.dumps(lb))
    print(f"tmr: {len(users)} users with >= {min_picks} graded picks")
    for u in users:
        picks, off = [], 0
        while True:
            page = _get(f"{TMR_API}/picks?username={u['username']}&limit=100&offset={off}") or {}
            rows = page.get("picks", [])
            picks += rows
            if len(rows) < 100:
                break
            off += 100
        (d / f"{u['username']}.json").write_text(json.dumps({"user": u, "picks": picks}))
        print(f"  {u['username']}: {len(picks)} picks", flush=True)


def jr_leaderboard_names() -> list[str]:
    html = _get(f"{JR}/best-sports-handicappers", as_json=False) or ""
    return sorted(set(re.findall(r'/users/([A-Za-z0-9_.-]+)', html)))


def collect_jr(max_pages: int) -> None:
    d = OUT / "jr"
    d.mkdir(parents=True, exist_ok=True)
    names = jr_leaderboard_names()
    print(f"jr: {len(names)} profiles linked from the leaderboard")
    seen_ids = set()
    for name in names:
        html = _get(f"{JR}/users/{name}", as_json=False) or ""
        m = re.search(r'sellerUser\\?":\{\\?"id\\?":(\d+)', html) or re.search(r'sellerUserId\\?":(\d+)', html)
        if not m:
            print(f"  {name}: no user id (not a seller page)")
            continue
        uid = int(m.group(1))
        if uid in seen_ids:          # several leaderboard slugs point at one account
            continue
        seen_ids.add(uid)
        joined = re.search(r"Joined ([A-Z][a-z]{2} \d{4})", re.sub(r"<[^>]+>", " ", html))
        path = d / f"{uid}.json"
        old = json.loads(path.read_text()) if path.exists() else {"bets": {}}
        bets = old["bets"]
        page, total = 0, None
        while page < max_pages:
            res = _get(f"{JR}/api/bets/{uid}/settled?page={page}") or {}
            blk = ((res.get("data") or {}).get("bets") or {})
            rows = ((blk.get("data") or {}).get("rows") or [])
            total = blk.get("totalPages", total)
            new = 0
            for r in rows:
                if str(r["id"]) not in bets:
                    new += 1
                bets[str(r["id"])] = _slim_jr(r)
            # settled history is newest-first: a page of all-known bets means the rest is on disk
            if not rows or (new == 0 and old["bets"]) or (total is not None and page + 1 >= total):
                break
            page += 1
        first_seen = old.get("first_seen") or datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps({"uid": uid, "name": name, "joined": joined.group(1) if joined else None,
                                    "first_seen": first_seen, "bets": bets}))
        print(f"  {name} ({uid}): {len(bets)} bets, joined {joined.group(1) if joined else '?'}", flush=True)


def _slim_jr(r: dict) -> dict:
    subs = r.get("Subbets") or []
    return {
        "placed": r.get("datePlaced"), "risk": r.get("irisk"), "win": r.get("iwin"),
        "net": r.get("netResult"), "result": r.get("result"), "vig": r.get("vig"),
        "legs": r.get("numTeam"), "clv": r.get("clvPct"), "edited": r.get("wasEdited"),
        "deleted": r.get("deletedAt"), "ticket": r.get("ticketNum"),
        "subs": [{"type": s.get("subbetType"), "desc": s.get("description"), "start": s.get("startDate"),
                  "live": s.get("wasLiveBet"), "league": s.get("leagueId"), "vig": s.get("vig")} for s in subs],
    }


# ---------------------------------------------------------------- normalise

def _implied(american: float | None) -> float | None:
    if american is None or american == 0 or abs(american) < 100:
        return None
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


def bettor_rows() -> list[dict]:
    """One row per graded bet, both sources, in a common shape:
    who, src, t (placed), risk (units or $), net, won (1/0/0.5 push), p_be, clv, live, kind."""
    out = []
    for f in sorted((OUT / "tmr").glob("*.json")):
        if f.name.startswith("_"):
            continue
        blob = json.loads(f.read_text())
        who = blob["user"]["username"]
        for p in blob["picks"]:
            if p.get("status") not in ("won", "lost", "push"):
                continue
            risk = float(p.get("risk_units") or p.get("units") or 1)
            to_win = float(p.get("to_win_units") or 0)
            # TMR's clv fields are not used: its "closing" price is usually the
            # placement price itself (BetLegend 10/26: clv 0 on the rows checked,
            # clv_novig = just the vig, -1.9..-3.1), so it measures nothing.
            out.append({"who": who, "src": "tmr", "t": p["created_at"], "risk": risk,
                        "net": float(p.get("result_units") or 0),
                        "won": {"won": 1, "lost": 0, "push": 0.5}[p["status"]],
                        "p_be": risk / (risk + to_win) if to_win > 0 else _implied(p.get("odds_snapshot")),
                        "clv": None, "live": False, "kind": p.get("market_type") or ""})
    for f in sorted((OUT / "jr").glob("*.json")):
        blob = json.loads(f.read_text())
        who = blob["name"]
        for b in blob["bets"].values():
            if b.get("deleted") or b.get("result") not in ("Won", "Lost", "Push", "Tie", "Draw"):
                continue
            risk = float(b.get("risk") or 0)
            if risk <= 0:
                continue
            win = float(b.get("win") or 0)
            net = float(b.get("net") or 0)
            # Cash-outs and partial settlements keep the ORIGINAL to-win but a
            # different net (SideHustle 10/27: "Won" to-win 204 net 4.04; "Lost"
            # net -0.0099 on 0.81 risked). Their net is real money, so it stays in
            # dollar ROI, but the to-win is not the price paid: no break-even, no z.
            settled_as_ticket = ((b["result"] == "Won" and abs(net - win) <= 0.02 * win + 0.01)
                                 or (b["result"] == "Lost" and abs(net + risk) <= 0.02 * risk + 0.01))
            p_be = risk / (risk + win) if win > 0 and settled_as_ticket else None
            out.append({"who": who, "src": "jr", "t": b["placed"], "risk": risk, "net": net,
                        "won": {"Won": 1, "Lost": 0}.get(b["result"], 0.5), "p_be": p_be,
                        "clv": b.get("clv"), "live": any(s.get("live") for s in b["subs"]),
                        "kind": ("parlay" if (b.get("legs") or 1) > 1
                                 else "cashout" if not settled_as_ticket and b["result"] in ("Won", "Lost")
                                 else (b["subs"][0]["type"] if b["subs"] else "")),
                        "first_seen": blob.get("first_seen")})
    return out


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    risk = sum(r["risk"] for r in rows)
    net = sum(r["net"] for r in rows)
    graded = [r for r in rows if r["p_be"] is not None and r["won"] != 0.5]
    w = sum(r["won"] for r in graded)
    exp = sum(r["p_be"] for r in graded)
    var = sum(r["p_be"] * (1 - r["p_be"]) for r in graded)
    clvs = [r["clv"] for r in rows if r["clv"] is not None]
    # units: TMR is already in units; Juice Reel is dollars, so 1u = the bettor's median risk
    unit = statistics.median(r["risk"] for r in rows) if rows and rows[0]["src"] == "jr" else 1.0
    return {"n": n, "wins": int(sum(1 for r in rows if r["won"] == 1)),
            "losses": int(sum(1 for r in rows if r["won"] == 0)),
            "roi": net / risk if risk else float("nan"), "units": net / unit,
            "z": (w - exp) / math.sqrt(var) if var > 0 else float("nan"),
            "clv": statistics.mean(clvs) if clvs else None, "n_clv": len(clvs),
            "live_share": sum(r["live"] for r in rows) / n if n else 0,
            "parlay_share": sum(r["kind"] == "parlay" for r in rows) / n if n else 0,
            "first": min(r["t"] for r in rows)[:10], "last": max(r["t"] for r in rows)[:10]}


def _corr(xs, ys):
    if len(xs) < 3:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else float("nan")


def report(min_n: int, csv_path: str | None) -> None:
    rows = bettor_rows()
    by = {}
    for r in rows:
        by.setdefault((r["src"], r["who"]), []).append(r)
    table = []
    for (src, who), rs in by.items():
        if len(rs) < min_n:
            continue
        rs.sort(key=lambda r: r["t"])
        s = summarise(rs)
        h1, h2 = rs[: len(rs) // 2], rs[len(rs) // 2:]
        s1, s2 = summarise(h1), summarise(h2)
        fwd = [r for r in rs if r.get("first_seen") and r["t"] > r["first_seen"]]
        table.append({"src": src, "who": who, **s, "roi_h1": s1["roi"], "roi_h2": s2["roi"],
                      "clv_h1": s1["clv"], "clv_h2": s2["clv"], "n_fwd": len(fwd),
                      "roi_fwd": summarise(fwd)["roi"] if fwd else None})
    table.sort(key=lambda t: -t["roi"])

    print(f"\n{len(table)} bettors with >= {min_n} graded bets "
          f"({sum(t['n'] for t in table):,} bets). z = wins vs each bet's own break-even.\n")
    hdr = f"{'src':4s} {'bettor':22s} {'n':>5s} {'W-L':>11s} {'ROI':>7s} {'units':>8s} {'z':>6s} {'CLV':>7s} {'live':>5s} {'parl':>5s} {'ROI 1st½':>8s} {'ROI 2nd½':>8s}  span"
    print(hdr)
    for t in table:
        clv = f"{t['clv']:+.1%}" if t["clv"] is not None else "   -"
        print(f"{t['src']:4s} {t['who'][:22]:22s} {t['n']:5d} {t['wins']:5d}-{t['losses']:<5d} {t['roi']:+7.1%} "
              f"{t['units']:+8.1f} {t['z']:+6.2f} {clv:>7s} {t['live_share']:5.0%} {t['parlay_share']:5.0%} "
              f"{t['roi_h1']:+8.1%} {t['roi_h2']:+8.1%}  {t['first']}..{t['last']}")

    # Persistence: across bettors, does the first half predict the second?
    ok = [t for t in table if t["n"] >= 2 * min_n]
    r_roi = _corr([t["roi_h1"] for t in ok], [t["roi_h2"] for t in ok])
    cl = [t for t in ok if t["clv_h1"] is not None and t["clv_h2"] is not None]
    r_clv = _corr([t["clv_h1"] for t in cl], [t["clv_h2"] for t in cl])
    r_clv_roi = _corr([t["clv_h1"] for t in cl], [t["roi_h2"] for t in cl])
    print(f"\nPersistence across {len(ok)} bettors with >= {2 * min_n} bets (each split in time):")
    print(f"  corr(ROI 1st half, ROI 2nd half) = {r_roi:+.2f}")
    print(f"  corr(CLV 1st half, CLV 2nd half) = {r_clv:+.2f}  (n={len(cl)})")
    print(f"  corr(CLV 1st half, ROI 2nd half) = {r_clv_roi:+.2f}")
    top = sorted(ok, key=lambda t: -t["roi_h1"])[: max(1, len(ok) // 4)]
    rest = [t for t in ok if t not in top]
    if top and rest:
        print(f"  top quarter by 1st-half ROI: 1st half {statistics.mean(t['roi_h1'] for t in top):+.1%} -> "
              f"2nd half {statistics.mean(t['roi_h2'] for t in top):+.1%}; "
              f"everyone else 2nd half {statistics.mean(t['roi_h2'] for t in rest):+.1%}")
    if csv_path:
        with open(csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(table[0].keys()))
            w.writeheader()
            w.writerows(table)
        print(f"\nwrote {csv_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--source", choices=["tmr", "jr", "all"], default="all")
    c.add_argument("--min-picks", type=int, default=30, help="TMR: skip members with fewer graded picks")
    c.add_argument("--max-pages", type=int, default=300, help="Juice Reel: 10 bets a page, newest first")
    r = sub.add_parser("report")
    r.add_argument("--min-n", type=int, default=100)
    r.add_argument("--csv")
    a = ap.parse_args()
    if a.cmd == "collect":
        if a.source in ("tmr", "all"):
            collect_tmr(a.min_picks)
        if a.source in ("jr", "all"):
            collect_jr(a.max_pages)
    else:
        report(a.min_n, a.csv)


if __name__ == "__main__":
    main()
