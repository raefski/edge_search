"""The systemd units are part of the model, not packaging.

WHY THIS FILE EXISTS (2026-09-09, iteration 2)
The pick'em model's input is `live_line - pool_line`, and `live_line` comes
from whatever the scraped board held at the instant a capture ran. So WHEN the
capture runs is a model parameter, and it was wrong in a way nothing could
detect:

  * odds-collect-pickem-nfl.timer collects at 12:20 and 23:20.
  * pickem-capture-lock-sun.timer fired at 12:00 -- twenty minutes BEFORE the
    day's collection -- so the week's most important reading was Saturday
    23:20 prices, 12h40m stale, stamped `captured_at = utcnow()`.
  * -midweek fired Friday 12:00 against Thursday 23:20 (12h40m).
  * -post fired Tuesday 13:15 against the 12:20 board, which is also ~40
    minutes BEFORE CBS's 1pm freeze it is supposed to be contemporaneous with.

The profile's max_age_seconds is a full day (edge/odds/profiles.py), by design
-- the pool line is frozen all week -- so the freshness contract never caught
any of it. These assertions pin the fix: collect immediately before capturing,
and never schedule a capture ahead of the day's collection.

Unit files are also the one part of this repo with no other test at all: no
pickem-capture timer had ever executed when this was written.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
CAPTURE_TIMERS = sorted(DEPLOY.glob("pickem-capture-*.timer"))

#: The daily collection times from odds-collect-pickem-nfl.timer, as minutes
#: past midnight LOCAL time (the collect timer carries no timezone, so it runs
#: in the machine's own -- America/New_York here).
COLLECT_MINUTES = (12 * 60 + 20, 23 * 60 + 20)


def _service() -> str:
    return (DEPLOY / "pickem-capture@.service").read_text()


def test_every_capture_collects_the_board_first():
    """The reading is only "the market at this deadline" if the board is new.

    The leading `-` is load-bearing: if the collection itself fails (DK 403s,
    network down), the capture must still bank the older board rather than
    being cancelled. A stale reading recorded honestly beats no reading, which
    is gone forever.
    """
    src = _service()
    pre = [l for l in src.splitlines() if l.startswith("ExecStartPre=")]
    assert pre, "no ExecStartPre: the capture reads whatever the board last had"
    board_pre = [l for l in pre if "odds_collect.py" in l]
    assert len(board_pre) == 1
    assert board_pre[0].startswith("ExecStartPre=-"), (
        "must be prefixed with '-' so a failed collection does not cancel the "
        "capture -- an unbanked reading cannot be recovered")
    assert "pickem_nfl" in board_pre[0]


def test_the_cbs_half_is_fetched_automatically_but_never_blocks_the_market_reading():
    """2026-09-10: the CBS half used to need a human to paste the pool page
    (scripts/pickem_pool_import.py). Automated via a stored login session
    (scripts/pickem_session_bootstrap.py, scripts/pickem_pool_fetch.py), but
    it must NEVER be allowed to cost the market reading -- that is the one
    that can never be retaken, and a CBS session WILL eventually expire with
    no way to renew it unattended.
    """
    src = _service()
    lines = src.splitlines()

    pre = [l for l in lines if l.startswith("ExecStartPre=")]
    fetch_pre = [l for l in pre if "pickem_pool_fetch.py" in l]
    assert len(fetch_pre) == 1
    assert fetch_pre[0].startswith("ExecStartPre=-"), (
        "an expired/failed CBS fetch must not cancel the capture below")

    exec_lines = [l for l in lines if l.startswith("ExecStart=")]
    assert len(exec_lines) == 1
    exec_i = next(i for i, l in enumerate(lines) if l.startswith("ExecStart="))
    post_i = next(i for i, l in enumerate(lines) if l.startswith("ExecStartPost="))
    exec_block = "\n".join(lines[exec_i:post_i])
    assert "--market-only" in exec_block, (
        "the primary capture must bank the market half unconditionally, "
        "whether or not the CBS fetch above got anything")

    post = [lines[post_i]]
    assert post[0].startswith("ExecStartPost=-"), (
        "the CBS merge pass must never fail the unit -- it is strictly "
        "additive to the row ExecStart already committed, and an empty CBS "
        "half here is 'wrote 0 new', not an error")
    post_block = "\n".join(lines[post_i:])
    assert "pickem_capture.py" in post_block
    assert "--market-only" not in post_block, (
        "the post-pass must run WITHOUT --market-only, or it can never "
        "merge in the CBS half it exists to add")


def test_no_capture_is_scheduled_before_the_days_collection():
    """A capture that beats the collector reads YESTERDAY's prices."""
    bad = []
    for t in CAPTURE_TIMERS:
        for line in t.read_text().splitlines():
            m = re.match(r"OnCalendar=\S+ \S+ (\d+):(\d+):\d+", line.strip())
            if not m:
                continue
            minutes = int(m.group(1)) * 60 + int(m.group(2))
            if not any(minutes >= c for c in COLLECT_MINUTES):
                bad.append(f"{t.name}: {minutes // 60:02d}:{minutes % 60:02d}")
                continue
            newest = max(c for c in COLLECT_MINUTES if c <= minutes)
            if minutes - newest > 12 * 60:
                bad.append(f"{t.name}: {minutes - newest} min after a collection")
    assert not bad, f"captures scheduled against a stale board: {bad}"


@pytest.mark.parametrize("name", ["pickem-capture-lock-sun.timer",
                                  "pickem-capture-midweek.timer"])
def test_the_two_noon_timers_moved_behind_the_collection(name):
    """These two fired at 12:00 against a 12:20 collection -- 20 minutes short.

    -midweek is the only source of the TIME PROFILE of post-freeze drift (5j
    round 6d); the 2014-2024 archive holds two snapshots per game and can never
    supply it. A midweek reading that is really Thursday night measures nothing.
    """
    assert "12:35:00" in (DEPLOY / name).read_text()


def test_a_failed_capture_is_recorded_where_a_human_will_see_it():
    """Restart=no plus `raise SystemExit(1)` plus no OnFailure = silence.

    A StaleOdds at Sunday noon loses the week's single most valuable reading
    and nothing anywhere says so: no capture timer had ever run, and there was
    no OnFailure= line in deploy/ or ~/.config/systemd/user/.
    """
    src = _service()
    assert "OnFailure=pickem-capture-failed@%i.service" in src
    assert "Restart=on-failure" in src
    assert "RestartSec=" in src
    assert "StartLimitBurst=" in src

    failed = (DEPLOY / "pickem-capture-failed@.service").read_text()
    assert "pickem_capture_failures.log" in failed
    page = (DEPLOY.parent / "pages" / "4_🎯_Pickem.py").read_text()
    assert "pickem_capture_failures.log" in page, (
        "the failure has to reach the person who reads the page")


def test_only_the_final_state_pass_pushes():
    """Auto-push, authorized 2026-09-16 -- but not from every line that writes.

    Left deliberately off through 2026-09-15: a push from a timer commits to
    a PUBLIC repo unattended, judged a decision the owner had to make once,
    explicitly, not a side effect of installing a unit. That decision
    surfaced its own cost before it got made -- three real deadline captures
    (Sep 13-15) wrote good local data that sat uncommitted for days, and the
    deployed Streamlit app silently served a six-day-stale snapshot, because
    nothing was watching for "captured but not pushed." Adam authorized
    auto-push explicitly once that cost was visible.

    Still not every Exec* line: --push belongs on whichever line produces a
    ROW'S FINAL STATE for this label, not on one that will be immediately
    superseded seconds later by the next line in the same unit. That is
    pickem_pool_fetch.py's own write (the CBS half, its only write) and the
    capture script's ExecStartPost merge pass (market + whatever CBS half
    exists) -- NOT the bare ExecStart market-only pass, which
    ExecStartPost's merge immediately supersedes.
    """
    lines = _service().splitlines()

    def find(prefix: str) -> str:
        # Exec directives here are two physical lines (a trailing `\` splits
        # the command onto the next), so join them for one substring check.
        for i, l in enumerate(lines):
            if l.startswith(prefix):
                nxt = lines[i + 1] if l.rstrip().endswith("\\") else ""
                return l + " " + nxt.strip()
        raise AssertionError(f"no {prefix!r} line found")

    cbs_fetch = find("ExecStartPre=-/usr/bin/env python3 scripts/pickem_pool_fetch.py")
    market_only = find("ExecStart=/usr/bin/env python3 scripts/pickem_capture.py")
    final_merge = find("ExecStartPost=-/usr/bin/env python3 scripts/pickem_capture.py")

    assert "--push" in cbs_fetch, cbs_fetch
    assert "--push" not in market_only, market_only
    assert "--push" in final_merge, final_merge

    # And the push, when it's there, must be soft-failing (leading `-`) on
    # BOTH lines that carry it: a lost push is recoverable next capture
    # (push_snapshot commits the file's whole current diff), unlike a lost
    # market reading, which is gone forever. It must never hold the same
    # unit-failing severity as ExecStart's market fetch.
    assert cbs_fetch.startswith("ExecStartPre=-"), cbs_fetch
    assert final_merge.startswith("ExecStartPost=-"), final_merge
