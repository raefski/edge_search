#!/usr/bin/env python3
"""Turn a saved pool page into data/pickem_current_week.csv -- no typing.

The pool's frozen spreads live behind a CBS login. An anonymous fetch of
the pool URL redirects to /join and returns settings only (verified with a
real browser), and CBS's PUBLIC odds page is not a substitute: on 2026
Week 1 it matched the pool line on only 12 of 16 games, disagreeing on
exactly the four the market had moved -- which is the signal, not noise.

So the fetch needs your own logged-in session. This script takes it from
there, which removes the part that actually costs you time:

    1. Open the pool's Picks page while logged in.
    2. Select all (Ctrl+A), copy (Ctrl+C), paste into a file.
    3. python3 scripts/pickem_pool_import.py picks.txt --week 3

No credentials are stored, requested, or transmitted by anything in this
repo -- it parses a page you already opened yourself.

KICKOFFS COME FROM THE SCRAPED BOARD, not from the paste. The pool page
prints "Thu @ 8:35 PM"; pages/4_🎯_Pickem.py needs a real instant, because it
groups the slate into the PER-DAY deadlines the pool actually runs on. The
board (edge/odds, profile pickem_nfl, already collected twice daily) carries
`commence_time` for every game, keyed the same CBS way the page joins on.

THE BUG THIS REPLACES, found 2026-09-10. Every row was written with

    "kickoff_utc": "",  "tv": "",   ... "note": <the kickoff TEXT>

so the next --write would have blanked all sixteen kickoffs, collapsed the
whole slate under one `TBD` day header, and dropped the kickoff correction
that was sitting uncommitted at the time. PICKEM_WEEKLY.md section 2 calls an
empty day "the one truly fatal mistake" -- missed-week scoring is zero.

AND IT NO LONGER TRUNCATES. `OUT.open("w")` rewrote the file with exactly the
rows the paste covered, so a clipped copy silently deleted the games it missed
and an import of week 3 deleted weeks 1 and 2. Rows the paste does not cover
are carried through untouched; rows it does cover are replaced, except `tv`,
which no odds feed carries and which therefore survives from the old row.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem_cbs import parse_pool_text  # noqa: E402

OUT = ROOT / "data" / "pickem_current_week.csv"
FIELDS = ["week", "away_abbr", "home_abbr", "away_name", "home_name",
          "cbs_line_home", "kickoff_utc", "tv", "comm_pct_away", "comm_pct_home", "note"]

#: What a row with no kickoff renders as on the page. Named so the warning and
#: the renderer cannot drift apart.
NO_KICKOFF = "TBD"


def board_kickoffs(week: int) -> dict[tuple[str, str], str]:
    """{(away_abbr, home_abbr): ISO kickoff} for one pool week, CBS-keyed.

    Goes through edge.pickem_week.slate_for_week -- the SAME join the page and
    the capture script use -- rather than a private lookup, so the Rams (LA on
    the board, LAR in the pool) and the Commanders (WAS/WSH) alias here exactly
    as they do there. edge/pickem_week.py's docstring records what an unaliased
    join costs: a silently dropped game, which on the page became a fabricated
    zero edge.

    Reads ONLY `commence_time`. Nothing here may touch cbs_line_home --
    PICKEM_MODEL.md 5h -- because the model IS the gap between CBS's frozen
    number and the market's, and a market number in the CBS column sets that
    gap to zero on every game, undetectably.
    """
    from edge.odds.cli import scraped_client
    from edge.pickem_live import fetch_week
    from edge.pickem_week import slate_for_week

    client = scraped_client("americanfootball_nfl", "pickem")
    slate = slate_for_week(fetch_week(client), week)
    return {k: g.kickoff for k, g in slate.items() if g.kickoff}


def _iso_z(value: str) -> str:
    """Normalise a board timestamp to the `...Z` form the CSV already uses."""
    try:
        ts = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return str(value or "")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return ts.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def carry_note(old_note: str) -> str:
    """Keep a hand-written caveat across a re-import; drop `provisional`.

    A fresh paste IS the verification, so the `provisional` marker stops being
    true and pages/4_🎯_Pickem.py's banner (which counts exactly that word)
    should clear. Everything else in the cell is Adam's -- "CBS froze Texans
    favored -- watch for a flip" is the note that called last week's flip --
    and blanking it is the same shape of loss as truncating the file.
    """
    parts = [seg.strip() for seg in re.split(r"[;\n]", old_note or "")]
    return "; ".join(seg for seg in parts
                     if seg and "provisional" not in seg.lower())


def load_existing(path: Path = None) -> list[dict]:
    """Whatever is already in the CSV, so an import can merge instead of
    truncate. Missing or unreadable is simply "nothing yet"."""
    path = OUT if path is None else path
    if not path.exists():
        return []
    try:
        with path.open() as f:
            return [dict(r) for r in csv.DictReader(f)]
    except OSError:
        return []


def _sort_key(row: dict) -> tuple:
    """Week, then kickoff, then paste order.

    The page groups cards into day headers by walking the file IN ORDER and
    never sorts, so a row landing out of sequence splits one day into two
    headers. Rows with no parseable kickoff sort LAST within their week --
    visible, not silently folded into the previous day.
    """
    try:
        week = int(row.get("week") or 0)
    except (TypeError, ValueError):
        week = 0
    kick = str(row.get("kickoff_utc") or "")
    try:
        ts = datetime.datetime.fromisoformat(kick.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        return (week, 0, ts.timestamp(), row.get("_order", 0))
    except ValueError:
        return (week, 1, 0.0, row.get("_order", 0))


def run(text: str, week: int, no_enrich: bool = False, write: bool = False) -> None:
    """Turn already-fetched pool-page text into `pickem_current_week.csv` rows.

    The one implementation of "parse + merge + write" this repo has, shared
    by `main()` below (a manual paste) and `scripts/pickem_pool_fetch.py`
    (the automated, session-based fetch) -- so the truncation/carry-note/
    kickoff-preservation fixes recorded in this module's docstring cannot
    drift between the two entry points, only their source of `text` differs.
    """
    games = parse_pool_text(text)
    if not games:
        print("Parsed 0 games. Copy the PICKS page (the one listing every matchup "
              "with the spreads), not Standings or Settings — and paste the text, "
              "not a screenshot.")
        return

    # Whatever is already on disk, so this import MERGES. Keyed the same way
    # the page and the capture script key their joins: the (away, home) PAIR.
    prior = load_existing()
    prior_by_key = {}
    for i, r in enumerate(prior):
        r["_order"] = i
        try:
            prior_by_key[(int(r.get("week") or 0), r.get("away_abbr", ""),
                          r.get("home_abbr", ""))] = r
        except (TypeError, ValueError):
            continue

    kick: dict[tuple[str, str], str] = {}
    if no_enrich:
        print("--no-enrich: NO kickoff times will be looked up. Rows without a "
              f"kickoff already on file render under a single {NO_KICKOFF} day "
              "header on the Pick'em page, which is the pool's per-day deadline "
              "made invisible. Drop --no-enrich once the board is reachable.\n")
    else:
        try:
            kick = board_kickoffs(week)
            print(f"kickoffs from the scraped board: {len(kick)} game(s)\n")
        except Exception as e:
            print(f"(kickoff lookup skipped: {type(e).__name__}: {e})")
            print("  The CBS lines below are the irreplaceable half -- they are "
                  "behind a login and exist only in this paste -- so the import "
                  "continues. Any kickoff already on file is kept. Fix with "
                  "`python3 scripts/odds_collect.py --profile pickem_nfl` and "
                  "re-run.\n")

    print(f"parsed {len(games)} games for week {week}\n")
    print(f"{'matchup':<16}{'CBS line':>10}{'community':>14}  kickoff")
    rows = []
    for i, g in enumerate(games):
        key = (g["away_abbr"], g["home_abbr"])
        old = prior_by_key.get((week,) + key, {})
        # board first, then whatever the file already knew. NEVER blank an
        # existing kickoff just because this board is thin.
        kickoff = _iso_z(kick[key]) if key in kick else (old.get("kickoff_utc") or "")
        rows.append({
            "week": week, "away_abbr": g["away_abbr"], "home_abbr": g["home_abbr"],
            "away_name": g["away_name"], "home_name": g["home_name"],
            "cbs_line_home": g["cbs_line_home"], "kickoff_utc": kickoff,
            # No odds feed carries a broadcaster, so `tv` can only ever come
            # from the row it is replacing. Blanking it was pure loss.
            "tv": old.get("tv", "") or "",
            "comm_pct_away": g["comm_pct_away"] if g["comm_pct_away"] is not None else "",
            "comm_pct_home": g["comm_pct_home"] if g["comm_pct_home"] is not None else "",
            # `note` is for genuine caveats. A kickoff time is not one -- it
            # used to be written here, which made every row look annotated. A
            # freshly pasted row IS the verification, so the `provisional`
            # marker clears; any other annotation on the row it replaces is
            # kept. See carry_note.
            "note": carry_note(old.get("note", "")),
            "_order": i,
        })
        comm = (f'{g["comm_pct_away"]}/{g["comm_pct_home"]}'
                if g["comm_pct_away"] is not None else "-")
        print(f'{g["away_abbr"]+" @ "+g["home_abbr"]:<16}{g["cbs_line_home"]:>10}'
              f'{comm:>14}  {kickoff or NO_KICKOFF}')

    dateless = [r for r in rows if not r["kickoff_utc"]]
    if dateless:
        names = ", ".join(f'{r["away_abbr"]}@{r["home_abbr"]}' for r in dateless)
        print(f"\n!! {len(dateless)} of {len(rows)} game(s) have NO kickoff and will "
              f"render under a single {NO_KICKOFF} header: {names}")
        print("   The pool's deadline is PER DAY and missed-week scoring is zero, "
              "so a slate with no days is the one truly fatal mistake "
              "(PICKEM_WEEKLY.md section 2). Collect the board "
              "(`python3 scripts/odds_collect.py --profile pickem_nfl`) and re-run, "
              f"or fill kickoff_utc by hand in {OUT.name}.")

    missing = [r for r in rows if r["comm_pct_home"] == ""]
    if missing:
        print(f"\n{len(missing)} game(s) came through without community percentages — "
              "those are what unblock the public-pick experiment, so check the copy "
              "included them.")

    # Everything the paste did NOT cover survives verbatim: other weeks, and
    # games a clipped copy missed.
    imported_keys = {(week, r["away_abbr"], r["home_abbr"]) for r in rows}
    carried = [r for k, r in prior_by_key.items() if k not in imported_keys]
    if carried:
        weeks = sorted({str(r.get("week", "?")) for r in carried})
        print(f"\ncarrying through {len(carried)} row(s) this paste did not cover "
              f"(week(s) {', '.join(weeks)}) — {OUT.name} is not truncated to "
              "the imported week.")

    if not write:
        print(f"\nDRY RUN — nothing written. Re-run with --write to update {OUT.name}.")
        return

    out_rows = sorted(rows + carried, key=_sort_key)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)
    print(f"\nwrote {len(rows)} imported + {len(carried)} carried = "
          f"{len(out_rows)} rows -> {OUT.name}")
    print("next: python3 scripts/pickem_capture.py --snapshot post "
          f"--week {week} --confirm")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="text (or HTML) saved from the pool's Picks page")
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip the board lookup for kickoff times. AN IMPORTED "
                         "ROW THEN HAS NO KICKOFF, and the page groups its "
                         "per-day deadlines by kickoff -- so the whole slate "
                         "lands under one TBD header. An escape hatch for an "
                         "offline import, not a default.")
    ap.add_argument("--write", action="store_true",
                    help="actually write the CSV (default is a dry run)")
    args = ap.parse_args()

    text = Path(args.file).read_text(errors="ignore")
    run(text, args.week, no_enrich=args.no_enrich, write=args.write)


if __name__ == "__main__":
    main()
