"""Is the critique loop itself improving? Loop-level scorecard, near-miss archive, exemplars.

    python3 -m harness critique-metrics              # the scorecard, by sport and overall
    python3 -m harness critique-metrics near-misses  # ideas that leaned better but did not clear the gate
    python3 -m harness critique-metrics exemplars    # findings that shipped / merged (what a win looks like)
    python3 -m harness critique-metrics --json       # all three -> data/harness/critique_metrics.json

Why (Adam, 2026-11-23, after Stanford CS329A "Self-Improving AI Agents"): the course's
long-horizon-eval lecture (METR's task-length paper, GDPval) scores the agent LOOP, not single
answers. critique_ledger_rows.py shows every cycle as a row; nothing said whether the loop is
getting better at finding money: cycles per merge, how many "passes" survive the 7-day re-gate,
dollars of model time per shipped change, how often it re-proposes the same idea. Without that
baseline, changes to the loop (more critics a night, a stricter gate) can't be judged either.

Three readers:
  - the scorecard: `harness critique-loop --status` prints its summary line;
  - near-misses (the course's ADAS / AlphaEvolve archive: build on the stepping stones, not only
    the winners): cycles whose primary held-out state was better on the point estimate but not
    beyond noise. The scout (scripts/dfs_target_scout.py) gets them, so "same idea, more slates
    now" is a target with a reason, distinct from the graveyard (tested null);
  - exemplars (the course's STaR: learn from your own verified successes): holes that shipped
    held out, so the critic sees the evidence bar a finding has to clear.

Counted from data/harness/critique_ledger.jsonl (from 2026-11-08). "usage limit" and "crashed"
cycles are the loop's own failures, not the model's: reported apart, never in the hit rate.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "harness" / "critique_ledger.jsonl"
OUT = ROOT / "data" / "harness" / "critique_metrics.json"
_LOOP_FAIL = ("usage limit", "crashed")
#: Jaccard word overlap at/above which two cycles' holes count as the same idea (the course's
#: AlphaCode clustering: extra samples only pay if they are DIFFERENT samples). Checked by eye
#: on the 59 ledger rows 2026-11-23: 0.5 pairs the NBA "no DK prop at all" duplicate and keeps
#: the NCAAB team-code / starter-bump holes apart.
SAME_IDEA = 0.5
_STOP = set("a an the of to in on and or is are was for by with at as no not its it be".split())


def load(path: Path = LEDGER) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _words(s: str | None) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", (s or "").lower()) if w not in _STOP and len(w) > 2}


def same_idea(a: str | None, b: str | None) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= SAME_IDEA


def scorecard(led: list[dict]) -> dict:
    """Per sport + 'all': attempts, loop failures, completed, pass / review / record / fail /
    no result, merged, dropped (re-gate or veto), re-gate survival, cost and minutes, repeats."""
    by: dict = defaultdict(lambda: defaultdict(float))
    holes: dict = defaultdict(list)
    for e in led:
        for s in (e.get("sport", "?"), "all"):
            b = by[s]
            ev, v = e.get("event"), e.get("verdict")
            if ev in ("cycle", "manual"):
                b["attempts"] += 1
                if v in _LOOP_FAIL:
                    b["loop_failures"] += 1
                    continue
                b["completed"] += 1
                b[f"v_{v}"] += 1
                b["cost_usd"] += e.get("cost_est") or 0
                b["minutes"] += e.get("minutes") or 0
                if v == "shipped":                    # a hand-run cycle that shipped
                    b["shipped"] += 1
                if s != "all" and e.get("hole"):
                    if any(same_idea(e["hole"], h) for h in holes[s]):
                        b["repeats"] += 1
                    holes[s].append(e["hole"])
            elif ev == "merged":
                b["shipped"] += 1
                b["merged"] += 1
            elif ev == "dropped":
                b["dropped_by_adam" if e.get("by") == "Adam" else "dropped_at_regate"] += 1
    for s, b in by.items():
        if s != "all":
            by["all"]["repeats"] += b["repeats"]
    out = {}
    for s, b in by.items():
        done = b["completed"]
        gated = b["v_pass"]                            # every auto pass faces the re-gate
        out[s] = {k: (round(x, 2) if isinstance(x, float) and x != int(x) else int(x)) for k, x in b.items()}
        out[s].update({
            "ship_rate": round(b["shipped"] / done, 3) if done else None,
            # auto passes that held at the 7-day re-gate: the gate's own false-positive check
            "regate_survival": round((gated - b["dropped_at_regate"]) / gated, 3) if gated else None,
            "cost_per_ship": round(b["cost_usd"] / b["shipped"], 2) if b["shipped"] else None,
            "repeat_rate": round(b["repeats"] / done, 3) if done else None,
            "enough_data": done >= 10,
        })
    return out


def near_misses(led: list[dict], sport: str | None = None, limit: int = 10) -> list[dict]:
    """Cycles that did not ship whose best primary state leaned better (d < 0) without clearing
    noise, ranked by how close (d / half-width). Each is a stepping stone: the same change on more
    slates, or a sharper version of it, is a target the scout can justify."""
    shipped = {e.get("hole") for e in led if e.get("event") == "merged" or e.get("verdict") == "shipped"}
    out = []
    for e in led:
        if e.get("event") != "cycle" or (sport and e.get("sport") != sport) or e.get("hole") in shipped:
            continue
        prim = [g for g in e.get("groups") or [] if g.get("split") == "primary" and g.get("d", 0) < 0]
        if not prim:
            continue
        g = min(prim, key=lambda g: g["d"] / max(1e-12, (g["hi"] - g["lo"]) / 2))
        if g["hi"] < 0 and g.get("verdict") == "better":
            continue                                   # cleared noise: failed for another reason
        out.append({"at": e["at"][:10], "sport": e["sport"], "hole": e.get("hole"), "fix": e.get("fix"),
                    "state": g["state"], "d": round(g["d"], 4), "lo": round(g["lo"], 4), "hi": round(g["hi"], 4),
                    "clusters": g["clusters"], "closeness": round(-g["d"] / max(1e-12, (g["hi"] - g["lo"]) / 2), 2),
                    "verdict": e.get("verdict")})
    return sorted(out, key=lambda r: -r["closeness"])[:limit]


def exemplars(led: list[dict], sport: str | None = None, limit: int = 8) -> list[dict]:
    """Shipped findings (hand-run 'shipped' or loop 'merged'), newest first, de-duplicated."""
    cyc = {(e.get("sport"), e.get("hole")): e for e in led if e.get("event") in ("cycle", "manual")}
    out, seen = [], []
    for e in reversed(led):
        if not (e.get("event") == "merged" or e.get("verdict") == "shipped"):
            continue
        if sport and e.get("sport") != sport and sport != "all":
            continue
        h = e.get("hole") or ""
        if any(same_idea(h, x) for x in seen):
            continue
        seen.append(h)
        c = cyc.get((e.get("sport"), h), e)
        out.append({"at": e["at"][:10], "sport": e["sport"], "hole": h, "fix": c.get("fix"),
                    "evidence": (c.get("summary") or "")[:400] or None,
                    "before": c.get("before"), "after": c.get("after"), "n": c.get("n"), "clusters": c.get("clusters")})
    return out[:limit]


def summary_line(card: dict) -> str:
    a = card.get("all") or {}
    if not a:
        return "loop: no cycles in the ledger yet"
    pct = lambda x: "-" if x is None else f"{x:.0%}"
    return (f"loop since 11/8: {a.get('completed', 0)} cycles done ({a.get('loop_failures', 0)} lost to usage limit / crash), "
            f"{a.get('shipped', 0)} shipped ({pct(a.get('ship_rate'))}), re-gate survival {pct(a.get('regate_survival'))}, "
            f"${a.get('cost_per_ship') or '-'} per ship, repeats {pct(a.get('repeat_rate'))}"
            + ("" if a.get("enough_data") else " -- not enough data"))


def table(card: dict) -> str:
    cols = ("completed", "loop_failures", "v_pass", "v_review", "v_record", "v_fail", "v_no result", "shipped",
            "dropped_at_regate", "ship_rate", "regate_survival", "cost_per_ship", "repeat_rate")
    head = ["sport", "done", "lost", "pass", "review", "record", "fail", "none", "ship", "regate-drop",
            "ship%", "survive%", "$/ship", "repeat%"]
    rows = [head]
    for s in sorted(card, key=lambda s: (s == "all", s)):
        rows.append([s] + ["-" if card[s].get(c) is None else str(card[s].get(c, 0)) for c in cols])
    w = [max(len(r[i]) for r in rows) for i in range(len(head))]
    return "\n".join("  ".join(x.rjust(w[i]) if i else x.ljust(w[i]) for i, x in enumerate(r)) for r in rows)


def build(led: list[dict] | None = None) -> dict:
    led = load() if led is None else led
    res = {"scorecard": scorecard(led), "near_misses": near_misses(led, limit=25), "exemplars": exemplars(led, limit=25)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str))
    return res


def main(argv: list[str]) -> int:
    led = load()
    sport = next((a for a in argv if not a.startswith("-") and a not in ("near-misses", "exemplars")), None)
    if "--json" in argv:
        build(led)
        print(OUT)
    elif "near-misses" in argv:
        print(json.dumps(near_misses(led, sport), indent=1))
    elif "exemplars" in argv:
        print(json.dumps(exemplars(led, sport), indent=1))
    else:
        card = scorecard(led)
        print(table(card) + "\n\n" + summary_line(card))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
