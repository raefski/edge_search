#!/usr/bin/env python3
"""What is still blocked on data, and how close are we to unblocking it?

Adam asked to be reminded of these rather than have them buried in a
document. Run it any time -- it reads data/pickem_line_log.csv and reports
progress toward each experiment that cannot run yet:

    python3 scripts/pickem_blocked.py

Every one of these is blocked purely on data nobody recorded, NOT on a
failed test. They become runnable by running scripts/pickem_capture.py
twice a week. A missed week is a permanently missing row.

Keep this in sync with PICKEM_MODEL.md section 5f -- that is the prose
version; this is the one that tells you where you stand.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.pickem_log import LINE_LOG, load  # noqa: E402

# Rough sample sizes at which each test stops being noise. Deliberately
# conservative: these mirror the discipline in PICKEM_MODEL.md section 3 --
# a signal has to hold across seasons, not just reach a p-value once.
NEED_GAMES = 250          # ~1 season of games for a first read
NEED_WEEKS_VELOCITY = 20  # weeks carrying 3+ snapshots


def main() -> None:
    rows = load()
    posts = [r for r in rows if r.get("snapshot") == "post"]
    with_cbs_and_mkt = [r for r in posts if r.get("cbs_line_home") and r.get("market_line_home")]
    with_totals = [r for r in posts if r.get("market_total")]
    with_comm = [r for r in rows if r.get("comm_pct_home")]
    multi_book = [r for r in rows if r.get("book_lines_json") and r["book_lines_json"] not in ("", "{}")]

    snaps_per_week = defaultdict(set)
    for r in rows:
        snaps_per_week[(r.get("season"), r.get("week"))].add(r.get("snapshot"))
    velocity_weeks = sum(1 for v in snaps_per_week.values() if len(v) >= 3)

    def bar(have: int, need: int) -> str:
        pct = min(1.0, have / need) if need else 1.0
        filled = int(round(pct * 24))
        state = "READY" if have >= need else f"{have}/{need}"
        return f"[{'#' * filled}{'.' * (24 - filled)}] {state}"

    print("PICK'EM -- EXPERIMENTS BLOCKED ON DATA")
    print(f"log: {LINE_LOG.name} ({len(rows)} rows, "
          f"{len(snaps_per_week)} week(s) captured)\n")

    items = [
        ("CBS post-offset isolation", len(with_cbs_and_mkt), NEED_GAMES,
         "cbs_bias = CBS's line minus the market at the same instant. Separates "
         "CBS's own house shading from real post-Tuesday drift. THE highest-value "
         "one: nobody else in the pool can exploit it.",
         "run --snapshot post every Tuesday, right after transcribing the screenshot"),
        ("Line-movement velocity", velocity_weeks, NEED_WEEKS_VELOCITY,
         "Does a late, fast move carry more signal than slow early drift? Needs "
         "3+ readings per week, not just post and lock.",
         "add a mid-week capture, e.g. --snapshot thu-am"),
        ("Sharp-book disagreement", len(multi_book), NEED_GAMES,
         "When books disagree, is the sharp side the one to follow? The historical "
         "file is single-book, so this has never been measurable.",
         "captured automatically now -- every snapshot stores per-book numbers"),
        ("Public-pick fading", len(with_comm), NEED_GAMES,
         "Does fading the crowd pay? Also the input that would upgrade the "
         "standings module from CBS's national percentages to your actual pool.",
         "fill comm_pct_away/comm_pct_home in data/pickem_current_week.csv"),
        ("Totals at CBS-post time", len(with_totals), NEED_GAMES,
         "Feeds the shipped no-movement tiebreak. Until this exists the model "
         "silently falls back to the old market-favourite rule.",
         "captured automatically by --snapshot post"),
    ]

    for i, (name, have, need, why, how) in enumerate(items, 1):
        ready = have >= need
        print(f"{i}. {name}  {'** RUNNABLE **' if ready else ''}")
        print(f"   {bar(have, need)}")
        print(f"   why: {why}")
        if not ready:
            print(f"   how: {how}")
        print()

    if not rows:
        print("Nothing captured yet. The first Tuesday of the season is the start of all")
        print("of it -- scripts/pickem_capture.py --snapshot post --week 1 --confirm")
    else:
        weeks = len(snaps_per_week)
        print(f"{weeks} week(s) logged. At ~16 games a week, a first read on the "
              f"{NEED_GAMES}-game items lands around week {max(1, -(-NEED_GAMES // 16))}"
              " of consistent capturing.")


if __name__ == "__main__":
    main()
