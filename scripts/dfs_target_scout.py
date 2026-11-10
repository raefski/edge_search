#!/usr/bin/env python3
"""DFS target scout, part 1 (no tokens): the facts a critique cycle's target is chosen from.

    python3 scripts/dfs_target_scout.py                 # every sport -> data/harness/dfs_targets.json + table
    python3 scripts/dfs_target_scout.py nba             # one sport, printed
    python3 scripts/dfs_target_scout.py --rebuild       # rebuild data/dfs_scorecard.json first (~5 s)

Why it exists (Adam, 2026-11-08). NBA's critic aimed at minutes for 25 cycles and improved
them while NBA cash lost -67%: the win (cycle 27, cash lineups 24% -> 65% cashed, held out)
came from three questions asked BEFORE a critic -- where is the money going, does a harness
score what we enter, what information is collected and unused (the DK props). This script
answers the mechanical parts for every sport; the dfs-target-scout agent (part 2,
.claude/agents/dfs-target-scout.md) weighs them and names the cycle's target.

Per sport:
  money     model-era ROI by cash / GPP with 90% ranges, entries, slates, enough_data
            (data/dfs_scorecard.json; never pooled)
  harness   which held-out labs exist and what they score (player metric vs what we ENTER)
  data      contests exported (cash / GPP) and logged boards
  unused    odds markets the sport's collector keeps (edge/odds/profiles) that no model
            file of the sport reads; "no odds collected" when there is no profile
  names     prop subjects the books priced that production's SalaryLookup does not resolve
            but that share a surname with a board player and look like the same man
            (same initial, or first names within two edits): each a possible dropped player
  coverage  board players in the top 20% of salary with no prop subject (props sports)
  history   the critique ledger: cycles, results, the last holes tried
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

OUT = ROOT / "data" / "harness" / "dfs_targets.json"
SCORECARD = ROOT / "data" / "dfs_scorecard.json"
LEDGER = ROOT / "data" / "harness" / "critique_ledger.jsonl"
SPORTS = ("nba", "ncaab", "nhl", "ncaaf", "nascar", "nfl", "mma", "mlb")

#: model files whose text decides whether a collected market is "used" (the collectors in
#: edge/odds and edge/arb never count)
MODEL_FILES = {
    "nba": ("dfs_run_nba.py", "dfs_nba_theory.py", "nba_project.py", "nba_minutes.py"),
    "ncaab": ("dfs_run_ncaab.py", "ncaab_sim.py"),
    "nhl": ("dfs_run_nhl.py", "nhl_sim.py"),
    "ncaaf": ("dfs_run_ncaaf.py", "dfs_ladder.py"),
    "nfl": ("dfs_run_nfl.py", "dfs_sport.py", "dfs_project.py"),
    "mlb": ("dfs.py",),
    "mma": ("dfs_run_mma.py", "mma_sim.py"),
    "nascar": ("dfs_run_nascar.py", "nascar_sim.py"),
}
#: held-out labs and what they score. "entered" = the lineups / contest result we enter;
#: "player" = a per-player proxy. Kept by hand: update when a lab is added.
HARNESS = {
    "nba": [("scripts/nba_minutes_lab.py", "player", "minutes per player"),
            ("scripts/nba_lineup_lab.py", "entered", "lineup field percentile / cash line; field ranking (2 s)"),
            ("scripts/nba_salary_shrink_lab.py", "player", "DK points per player vs salary")],
    "ncaab": [("scripts/ncaab_lab.py", "player", "DK points per player (LOSO)")],
    "nhl": [("scripts/nhl_skater_lab.py", "player", "skater DK points")],
    "ncaaf": [("scripts/ncaaf_lab.py", "player", "DK points per player")],
    "nascar": [("scripts/nascar_lab.py", "player", "DK points per driver")],
    "nfl": [("scripts/nfl_lineup_lab.py", "entered", "cash / GPP lineup field percentile, cash line, $"),
            ("scripts/nfl_rb_lab.py", "player", "RB DK points vs pre-lock props projection")],
    "mma": [], "mlb": [("scripts/dfs_hitter_backtest.py", "player", "hitter DK points")],
}
PROFILE = {s: f"dfs_{s}" for s in SPORTS}
DISPLAY_ONLY = {"spreads", "totals", "h2h"}       # game lines: read for implied totals, if at all


def money(card: dict, sport: str) -> list[dict]:
    out = []
    for r in card.get("roi", []):
        if r["period"] == "model" and r["sport"] == sport.upper() and r["type"] in ("cash", "gpp"):
            out.append({k: r.get(k) for k in ("type", "roi", "roi_lo", "roi_hi", "net", "entries", "slates",
                                               "cash_rate", "beat", "enough_data")})
    return out


def data_volume(sport: str) -> dict:
    c = sqlite3.connect(ROOT / "data" / "contests.db")
    by = dict(c.execute("select type, count(*) from contest where sport=? and slate_date >= '2026-06-28' "
                        "group by type", (sport.upper(),)).fetchall())
    log = ROOT / "data" / f"dfs_proj_log_{sport}.csv"
    slates = 0
    last = None
    if log.exists():
        with log.open() as fh:
            ds = {(r.get("date"), r.get("gid")) for r in csv.DictReader(fh)}
        slates, last = len(ds), max((d for d, _ in ds if d), default=None)
    return {"contests_cash": by.get("cash", 0), "contests_gpp": by.get("gpp", 0),
            "boards_logged": slates, "last_board": last}


def unused_markets(sport: str) -> dict:
    from edge.odds import profiles
    prof = profiles.PROFILES.get(PROFILE[sport])
    if sport == "mma":                 # its own capture, not an edge/odds profile
        return {"collected": True, "markets": ["h2h", "method"], "unused": [],
                "note": "fight odds via scripts/mma_odds_capture.py -> data/odds_snapshot_dfs_mma.json (used)"}
    if prof is None:
        return {"collected": False, "note": "no odds collected for this sport (no edge/odds profile)"}
    keep = set(prof.publish_markets or ()) | set(prof.required_markets or ())
    text = "".join((ROOT / "edge" / f).read_text() for f in MODEL_FILES[sport] if (ROOT / "edge" / f).exists())
    unused = sorted(m for m in keep - DISPLAY_ONLY if f'"{m}"' not in text and f"'{m}'" not in text)
    return {"collected": True, "markets": sorted(keep), "unused": unused}


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def names_and_coverage(sport: str, dates: int = 14) -> dict:
    """Possible dropped players (book subject unresolved, same surname, same-looking first
    name) and top-salary board players with no prop subject, over the last `dates` boards."""
    from edge.dfs import SalaryLookup
    from edge.dfs_run_nba import _sig
    from edge.names import norm
    from edge.odds.store import OddsStore
    log = ROOT / "data" / f"dfs_proj_log_{sport}.csv"
    if not log.exists():
        return {}
    by_date = defaultdict(dict)
    with log.open() as fh:
        for r in csv.DictReader(fh):
            try:
                by_date[r["date"]][(r["player"], r.get("team") or "", r.get("dk_pos") or "")] = float(r.get("salary") or 0)
            except ValueError:
                continue
    store = OddsStore(ROOT / "data" / "odds.db")
    suspects, uncovered, n_subj, n_dates = defaultdict(set), Counter(), 0, 0
    for d in sorted(by_date)[-dates:]:
        d0 = datetime.fromisoformat(d + "T12:00:00+00:00")
        sc = [r["id"] for r in store.conn.execute(
            "select id from scan where profile=? and ok=1 and started_at between ? and ? order by id",
            (PROFILE[sport], d0.isoformat(), (d0 + timedelta(hours=12)).isoformat()))]
        if not sc:
            continue
        n_dates += 1
        board = by_date[d]
        # namesakes keyed "name|TEAM" with team + position, as production's salaries are, so a
        # book's "Sebastian Aho (CAR)" resolves to the right one
        cnt = Counter(norm(n) for n, _, _ in board)
        sl = SalaryLookup({(norm(n) if cnt[norm(n)] == 1 else f"{norm(n)}|{t}"):
                           {"name": n, "team": t, "position": pos} for n, t, pos in board})
        bylast = defaultdict(list)
        for n, _, _ in board:
            bylast[_sig(n)[0]].append(n)
        subjects = {r["subject"] for r in store.quotes(sc[len(sc) // 2]) if r["subject"]}
        n_subj += len(subjects)
        hit = set()
        for x in subjects:
            got = sl.get(x)
            if got:
                hit.add((got["name"], got["team"]))
                continue
            fx = norm(x.split()[0]) if x.split() else ""
            for b in bylast.get(_sig(x)[0], []):
                fb = norm(b.split()[0])
                if fx[:1] == fb[:1] or _lev(fx, fb) <= 2:
                    suspects[(x, b)].add(d)
        if subjects:
            cut = sorted(board.values(), reverse=True)[max(0, len(board) // 5 - 1)] if board else 0
            for (n, t, _), sal in board.items():
                if sal >= cut and (n, t) not in hit:
                    uncovered[n] += 1
    return {"dates": n_dates, "prop_subjects": n_subj,
            "name_suspects": [{"book": x, "board": b, "dates": len(ds)} for (x, b), ds in sorted(suspects.items())],
            "top_salary_without_props": [{"player": p, "boards": n} for p, n in uncovered.most_common(12)]}


def history(sport: str) -> dict:
    if not LEDGER.exists():
        return {"cycles": 0}
    rows = [json.loads(x) for x in LEDGER.read_text().splitlines() if x.strip()]
    mine = [r for r in rows if r.get("sport") == sport and r.get("event") in ("cycle", "manual")]
    return {"cycles": len(mine), "results": dict(Counter(r.get("verdict") for r in mine)),
            "recent": [{"at": r["at"][:10], "result": r.get("verdict"), "target": r.get("target"),
                        "hole": (r.get("hole") or "")[:200]} for r in mine[-5:]]}


def scout(sport: str, card: dict, names: bool = True) -> dict:
    h = HARNESS.get(sport, [])
    out = {"sport": sport, "money": money(card, sport), "data": data_volume(sport),
           "harness": [{"lab": a, "scores": b, "what": c} for a, b, c in h],
           "harness_scores_entered": any(b == "entered" for _, b, _ in h),
           "unused_odds": unused_markets(sport), "history": history(sport)}
    if names and out["unused_odds"].get("collected") and sport != "mma":
        try:
            out["names"] = names_and_coverage(sport)
        except Exception as exc:                      # noqa: BLE001 -- the rest still stands
            out["names"] = {"error": str(exc)}
    return out


def table(rs: list[dict]) -> str:
    lines = ["| sport | cash ROI [90%] (n) | GPP ROI [90%] (n) | contests c/g | lab scores what we enter | "
             "unused odds | name suspects | cycles (results) |", "|---|---|---|---|---|---|---|---|"]

    def m(r, t):
        x = next((y for y in r["money"] if y["type"] == t), None)
        if not x or x["roi"] is None:
            return "-"
        flag = "" if x.get("enough_data") else " thin"
        return f"{x['roi']:+.0%} [{x['roi_lo']:+.0%}, {x['roi_hi']:+.0%}] ({x['entries']}e/{x['slates']}s{flag})"
    for r in rs:
        u = r["unused_odds"]
        un = "NOT COLLECTED" if not u.get("collected") else (", ".join(u["unused"]) or "none")
        ns = len((r.get("names") or {}).get("name_suspects", []))
        h = r["history"]
        lines.append(f"| {r['sport'].upper()} | {m(r, 'cash')} | {m(r, 'gpp')} | "
                     f"{r['data']['contests_cash']}/{r['data']['contests_gpp']} | "
                     f"{'yes' if r['harness_scores_entered'] else 'no'} | {un} | {ns} | "
                     f"{h['cycles']} {h.get('results', {})} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sport", nargs="?", choices=SPORTS)
    ap.add_argument("--rebuild", action="store_true", help="rebuild data/dfs_scorecard.json first")
    ap.add_argument("--no-names", action="store_true", help="skip the odds-store name audit (fast)")
    a = ap.parse_args(argv)
    if a.rebuild or not SCORECARD.exists():
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "dfs_scorecard.py")], cwd=ROOT,
                       capture_output=True, timeout=600)
    card = json.loads(SCORECARD.read_text())
    rs = [scout(s, card, not a.no_names) for s in ([a.sport] if a.sport else SPORTS)]
    doc = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "scorecard_built": card.get("generated_at"), "sports": rs}
    if not a.sport:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(doc, indent=1))
    print(table(rs))
    if a.sport:
        print("\n" + json.dumps(rs[0], indent=1))
    else:
        print(f"\n-> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
