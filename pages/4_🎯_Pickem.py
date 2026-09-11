"""pages/4_🎯_Pickem.py — NFL pick'em spread-edge picks (TOO-GOODE pool).

Streamlit's native multi-page mechanism (this file living in pages/) is what
adds "Pickem" to the sidebar next to the MLB DFS home page -- no routing code
needed, and app.py's working MLB flow is untouched. Numbered 4_ rather than
2_/3_ deliberately: DFS_MULTISPORT_PLAN.md already reserves those slots for
real NFL/NBA DFS lineup pages, a different game (salary-cap rosters) from
this one (spread picks) despite the shared "NFL" word.

Two data sources, matching app.py's own free-vs-manual split:
  * Live market line: edge.pickem_live.fetch_week, via edge.client's Odds
    API (dry-run/cache by default, ~2 credits -- spreads + totals -- for the
    WHOLE week's slate in one call when you explicitly tap "Pull fresh
    lines"). Originally free
    via ESPN's public scoreboard -- that got blocked in production (403,
    Akamai) and DraftKings' own sportsbook endpoint hits the identical wall
    (same Akamai infrastructure sits in front of both), so this is the
    legitimate path, not a workaround. See edge/pickem_live.py's docstring.
  * CBS's frozen pool line: CANNOT be fetched here -- it's behind CBS's
    login, so no server can pull it. Comes from data/pickem_current_week.csv,
    committed each time the pool's numbers are captured (by screenshot) and
    pushed. This is the one piece of the whole app that needs a human.

Deliberately does NOT read data/pickem/ (tracker.csv, standings.csv) -- that
directory holds the TOO-GOODE pool's real opponents, standings, and $ splits
and is gitignored on purpose (see .gitignore's comment). This page only ever
shows this week's matchups and the model's picks, nothing personal.
"""
from __future__ import annotations

import csv
import datetime
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.client import CreditFloorError, DryRunBlocked, NoApiKey, OddsAPIClient  # noqa: E402
from edge.odds.source import StaleOdds  # noqa: E402
from edge.pickem import make_pick  # noqa: E402
from edge.pickem_live import fetch_week  # noqa: E402
from edge.pickem_log import load as load_line_log  # noqa: E402
# The pool calendar and the CBS/market alias, shared with
# scripts/pickem_capture.py rather than reimplemented here. Only one
# implementation left to drift from -- the rule adopted after 2026-08-21.
from edge.pickem_week import EASTERN, current_week, slate_for_week  # noqa: E402

CURRENT_WEEK_CSV = ROOT / "data" / "pickem_current_week.csv"
CACHE_DIR = ROOT / "data" / "cache"
LEDGER = ROOT / "data" / "odds_api_credits.json"
# Written by deploy/pickem-capture-failed@.service, one line per failed timer
# run. Surfaced HERE because a journal entry is not a notification: nobody
# reads `journalctl` on a Sunday morning, and a capture missed at its deadline
# is a permanently missing reading, not a retryable outage.
FAILURES_LOG = ROOT / "data" / "pickem_capture_failures.log"


def _bootstrap_key() -> None:
    """Same precedence as app.py's own bootstrap: env, then Streamlit Cloud
    secrets, then a local .env -- duplicated rather than imported, since
    app.py's version only runs when app.py itself is the active page, and a
    session that lands directly on Pickem would otherwise never see it."""
    if os.environ.get("ODDS_API_KEY"):
        return
    try:
        if "ODDS_API_KEY" in st.secrets:
            os.environ["ODDS_API_KEY"] = st.secrets["ODDS_API_KEY"]
            return
    except Exception:
        pass
    for p in (ROOT / ".env",):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line.startswith("ODDS_API_KEY") and "=" in line:
                    os.environ["ODDS_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return


_bootstrap_key()

st.set_page_config(page_title="Pick'em — TOO-GOODE", page_icon="🎯", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 2.0rem; padding-bottom: 2rem;}
h1 {font-size: 1.55rem !important; margin-bottom: .1rem;}
.pk-sub {font-size: 13px; color: #9aa4b2; line-height: 1.55; margin: .1rem 0 1rem;}
.pk-day {font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
         color: #7f8a9c; margin: 18px 0 6px; padding-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,.12);}
.pk-card {background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.10);
          border-radius: 8px; padding: 10px 12px; margin-bottom: 8px;}
.pk-card.strong {border-left: 3px solid #e0a03c;}
.pk-row1 {display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap;}
.pk-matchup {font-weight: 700; font-size: 16px;}
.pk-tv {font-size: 10px; letter-spacing: .05em; text-transform: uppercase; color: #7f8a9c;}
.pk-pickrow {display: flex; align-items: center; gap: 10px; margin: 6px 0 4px; flex-wrap: wrap;}
.pk-pickbox {font-weight: 700; font-size: 15px; background: #262a33; padding: 2px 10px; border-radius: 5px;}
.pk-card.strong .pk-pickbox {background: #4a3a00; color: #ffd97a;}
.pk-prob {font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums;}
.pk-pill {font-size: 10px; letter-spacing: .05em; text-transform: uppercase; padding: 2px 7px;
          border-radius: 4px; background: rgba(255,255,255,.10); color: #c7d0dd;}
.pk-pill.strong {background: #4a3a00; color: #ffd97a;}
.pk-pill.solid, .pk-pill.lean {background: #2a3a4a; color: #8fc4e8;}
.pk-nums {font-size: 11px; color: #9aa4b2; font-variant-numeric: tabular-nums; margin-top: 4px;}
.pk-nums b {color: #c7d0dd; font-weight: 500;}
.pk-note {font-size: 11px; color: #e0a03c; margin-top: 4px; font-style: italic;}
.pk-nolive {font-size: 10px; letter-spacing: .05em; text-transform: uppercase; padding: 2px 7px;
            border-radius: 4px; background: #4a2020; color: #ffb3b3;}
/* A card with no market reading. It must not look like a pick at any glance
   distance: the box is empty and recessed, and the label sits at the same
   15px weight the team name used to, so the eye lands on the absence. */
.pk-card.dark {opacity: .72; border-style: dashed;}
.pk-pickbox.pk-nopick {background: transparent; border: 1px dashed rgba(255,255,255,.22);
                       color: #7f8a9c; font-weight: 500;}
.pk-noreading {font-size: 15px; font-weight: 700; letter-spacing: .04em; color: #9aa4b2;}
.pk-played {font-size: 10px; letter-spacing: .05em; text-transform: uppercase; padding: 2px 7px;
            border-radius: 4px; background: rgba(255,255,255,.08); color: #9aa4b2;}
</style>
""", unsafe_allow_html=True)

st.title("🎯 NFL Pick'em")
st.markdown(
    '<p class="pk-sub">TOO-GOODE FOOTBALL POOL, CBS Sportsline. Model: follow the market\'s '
    'move off CBS\'s frozen line — backtested 55.9% ATS out-of-sample (2023–24 held-out '
    'seasons, 298-235-10). See PICKEM_MODEL.md for the full method and honest caveats.</p>',
    unsafe_allow_html=True)


with st.sidebar:
    st.header("🎯 Pick'em")
    # Defaulting to 1 was fine for exactly one week. scripts/pickem_pool_import.py
    # TRUNCATES data/pickem_current_week.csv to the week it imports, so from
    # Week 2 on this page would open on Week 1, find no rows, and print "No
    # Week 1 lines captured yet" with that week's slate sitting in the file it
    # had just read. current_week() is the same calendar the timers' --week
    # auto uses; out of season it falls back to 1 rather than crashing.
    week = st.number_input("Week", min_value=1, max_value=18,
                           value=int(current_week() or 1), step=1)

    # Where the market line actually comes from. Since edge/odds landed, the
    # free scrape and the paid feed produce identical-looking output, so a
    # silent fallback to the paid client was invisible -- and here it spends
    # real credits. State the source rather than leaving it to be inferred.
    try:
        from edge.odds.cli import scraped_client as _scraped
        _free = _scraped("americanfootball_nfl", "pickem")
        if type(_free).__name__ == "SnapshotOddsClient":
            _mins = _free.age_seconds / 60.0
            st.success(f"🟢 Free scraped lines · {_mins:.0f} min old")
            if _mins > 24 * 60:
                st.caption("Over a day old. Tap **Request a desktop scan** on "
                           "the Arbitrage page — it refreshes these too.")
        else:
            st.success("🟢 Free scraped lines (local store)")
        st.caption("DraftKings · FanDuel · Fanatics")
    except Exception as _exc:                               # noqa: BLE001
        st.warning(f"🟡 Free lines unavailable ({_exc}). Using the Odds API.")

    # Paid path folded away: it is the fallback now, not how this page works.
    # Kept because the pool line is frozen all week and a missed collection
    # should not leave the model with no market line at all.
    with st.expander("💰 Paid fallback (Odds API)"):
        st.caption("Only needed if the free lines above are missing or stale.")
        api_key = st.text_input("ODDS_API_KEY",
                                value=os.environ.get("ODDS_API_KEY", ""),
                                type="password",
                                help="Stored only for this session.")
        if api_key:
            os.environ["ODDS_API_KEY"] = api_key

        _remaining = OddsAPIClient(cache_dir=CACHE_DIR,
                                   ledger_path=LEDGER).remaining_credits()
        if _remaining is not None:
            st.caption(f"Odds API: {_remaining} credits remaining this cycle")

        pull_fresh = st.button(
            "Pull fresh lines (~2 credits)", width="stretch",
            help="One call covers the whole week's slate: spreads + totals across "
                 "every available book (markets × regions = 2), then free for 10 "
                 "minutes. Nothing spends unless you tap this.")

# A capture that never ran is invisible everywhere else in this project.
try:
    _failures = [l for l in FAILURES_LOG.read_text().splitlines() if l.strip()]
except OSError:
    _failures = []
if _failures:
    st.error(
        f"⚠️ {len(_failures)} pick'em capture(s) FAILED. Each one is a market "
        f"reading that no longer exists — the deadline it belonged to has "
        f"passed and data/odds.db can only replay scans the collector took.\n\n"
        + "\n".join(f"- `{l}`" for l in _failures[-5:]))
    st.caption(
        "Recover what is recoverable: `python3 scripts/pickem_capture.py "
        "--snapshot <label> --week <n> --at <ISO instant> --market-only "
        "--confirm` pins the scan that ran closest to that deadline. Then "
        f"clear `{FAILURES_LOG.name}`. Check the run with "
        "`journalctl --user -u 'pickem-capture@*'`.")

if not CURRENT_WEEK_CSV.exists():
    st.warning(f"No {CURRENT_WEEK_CSV.name} committed yet for this week.")
    st.stop()

with CURRENT_WEEK_CSV.open() as f:
    cbs_rows = [r for r in csv.DictReader(f) if int(r["week"]) == week]

if not cbs_rows:
    st.info(f"No Week {week} lines captured yet.")
    st.stop()

# THE POOL LINE IS THE MODEL'S INPUT, so an unverified one is not a cosmetic
# caveat -- every edge on this page is measured against it. The rows tagged
# `provisional` were transcribed before CBS posted and were never re-verified,
# and the note renderer used to filter out the single word "provisional",
# which made a stale CSV indistinguishable from a fresh one.
_provisional = [r for r in cbs_rows if "provisional" in (r.get("note") or "").lower()]
if _provisional:
    st.error(
        f"⚠️ {len(_provisional)} of {len(cbs_rows)} Week {week} CBS lines are "
        f"**provisional** — transcribed before CBS posted and never re-verified. "
        f"Every edge below is measured against a number that may not be the "
        f"one you are graded on. Fix by pasting the real CBS Picks page and "
        f"running `python3 scripts/pickem_pool_import.py picks.txt "
        f"--week {week} --write`.")
    st.caption("Do NOT substitute CBS's public odds page for these — it tracks "
               "the live market, so it would delete the very gap this model "
               "measures while still looking like it worked (PICKEM_MODEL.md §5h).")

# The totals tiebreak needs the total as it stood when CBS froze its line.
# That only exists once scripts/pickem_capture.py has run a 'post' snapshot
# for this week; without it every game simply falls back to the old
# market-favourite rule, which is exactly what shipped before.
post_totals = {
    r["home_team"]: float(r["market_total"])
    for r in load_line_log()
    if r.get("snapshot") == "post" and str(r.get("week")) == str(week)
    and r.get("market_total")
}

# Free prices first. On the desktop that is data/odds.db; on Streamlit Cloud,
# which cannot scrape and has no store, it is the committed snapshot published
# by edge/odds/publish.py. Only if neither exists does this fall back to the
# paid client -- so the "Pull fresh lines" button stays as the deliberate
# escape hatch rather than the default path.
client = None
try:
    from edge.odds.cli import scraped_client
    client = scraped_client("americanfootball_nfl", "pickem")
except Exception as _exc:  # noqa: BLE001
    st.caption(f"Free scraped lines unavailable ({_exc}); using the Odds API.")
if client is None or pull_fresh:
    client = OddsAPIClient(cache_dir=CACHE_DIR, ledger_path=LEDGER, dry_run=not pull_fresh)
# THE JOIN, and why it is not keyed on the home team.
#
# The books' board is NOT one week. On 2026-09-08 it held weeks 1 AND 2 with
# five teams -- LA, KC, TEN, HOU, LAC -- hosting in both. Keyed by home team
# alone, BUF@HOU silently resolved to Week 2's CIN@HOU and this page printed
# a Texans LEAN where the Week 1 market says BUF +1.5 STRONG: a side flip on
# the showcase game of the week. And SF@LAR matched nothing at all, because
# the market spells the Rams "LA" and CBS spells them "LAR", so the page fell
# back to live_line = cbs_line -- a fabricated zero edge that looks exactly
# like a game the market agrees with.
#
# slate_for_week() trims to the selected pool week and keys on the
# CBS-aliased (away, home) PAIR. It RAISES on a board that still has a home
# team twice after filtering, and that refusal is surfaced below rather than
# swallowed: a team plays once a week, so a duplicate means the window is
# wrong and any pick shown would be a guess.
live_by_pair: dict[tuple[str, str], object] = {}
board_ok = False
try:
    live_by_pair = slate_for_week(fetch_week(client), int(week))
    board_ok = True
except ValueError as e:
    st.error(f"🚫 Refusing to show picks for Week {week}: {e}")
    st.caption("Nothing is displayed rather than the wrong game. Re-collect the "
               "board with `python3 scripts/odds_collect.py --profile pickem_nfl`.")
    st.stop()
except NoApiKey:
    st.warning("No ODDS_API_KEY set — paste your key in the sidebar, then tap "
               "**Pull fresh lines**. Showing CBS lines only until then.")
except DryRunBlocked:
    st.info("No live lines cached yet this week — tap **💰 Pull fresh lines** "
            "in the sidebar (~2 credits for the whole slate). Showing CBS lines only.")
except CreditFloorError as e:
    st.error(f"Skipped the live pull to protect your credit floor: {e}")
except StaleOdds as e:
    # NOT the Odds API's fault, and saying so sent the reader to the wrong
    # remedy. This is the locally SCRAPED board being too old or absent.
    st.error(f"The scraped market board is stale or missing: {e}")
    st.caption("Fix: `python3 scripts/odds_collect.py --profile pickem_nfl`. "
               "Until then every game below shows CBS's number with no edge.")
except Exception as e:
    st.error(f"Couldn't reach the Odds API ({e}) — showing CBS lines only, no edge computed.")

TIER_CLASS = {"STRONG": "strong", "SOLID": "solid", "LEAN": "lean", "COIN FLIP": ""}

NOW_UTC = datetime.datetime.now(datetime.timezone.utc)


def kick_dt(kickoff_utc) -> datetime.datetime | None:
    """The kickoff as an aware UTC instant, or None if it will not parse.

    One parser, used by BOTH the day header and the has-it-started test, so
    the two can never disagree about when a game is.
    """
    try:
        kick = datetime.datetime.fromisoformat(
            str(kickoff_utc).replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    return kick if kick.tzinfo is not None else kick.replace(
        tzinfo=datetime.timezone.utc)


# THE BUG THIS REPLACES, found 2026-09-10 and live at the time.
#
#     no_live   = live is None or live.live_line is None
#     live_line = cbs_line if no_live else live.live_line
#     pk        = make_pick(..., cbs_line, live_line, ...)
#
# A game with no market reading was handed (cbs_line, cbs_line). That is an
# edge of exactly 0, so edge.pickem._coinflip_side takes over and returns the
# MARKET FAVOURITE -- and with no market, "the favourite" is whichever side
# CBS froze as favourite, i.e. the OPPOSITE of the side the market had moved
# to. Measured on the real Week 1 board:
#
#     BUF@HOU: with board -> BUF STRONG 59% | board MISSING -> HOU COIN FLIP 50%
#     ARI@LAC: with board -> ARI LEAN  53% | board MISSING -> LAC COIN FLIP 50%
#     NYJ@TEN: with board -> NYJ LEAN  53% | board MISSING -> TEN COIN FLIP 50%
#
# Not hypothetical: books pull a game from the board at kickoff, so NE@SEA
# went dark the moment it started and by Sunday 4:30pm ten of sixteen cards
# are in this state. The pre-kickoff case -- a book taking a game down on QB
# news -- produces exactly ONE quietly-flipped card among fifteen normal ones.
# The badge was a 10px pill; the flipped team sat in the 15px bold pick box.
#
# So the model is no longer ASKED about a game it has no reading for. There is
# no pick to show, and a card that shows none cannot show the wrong one.
rows = []
for r in cbs_rows:
    cbs_line = float(r["cbs_line_home"])
    live = live_by_pair.get((r["away_abbr"], r["home_abbr"]))
    no_live = live is None or live.live_line is None
    kick = kick_dt(r.get("kickoff_utc", ""))
    # A finished game and a missing game look identical in the data and need
    # OPPOSITE responses: "kicked off" means nothing is wrong, "not on the
    # board" means go re-collect the odds. Telling them apart needs the
    # kickoff, which is why scripts/pickem_pool_import.py must keep writing it.
    played = no_live and kick is not None and kick <= NOW_UTC
    pk = None if no_live else make_pick(
        r["away_name"], r["home_name"], cbs_line, live.live_line,
        post_totals.get(r["home_abbr"]), live.total)
    rows.append((r, pk, no_live, live, played))

# A game with no live line is NOT a game the market agrees with -- it is a
# game we have no market reading for at all. Booking its 50% as if the model
# had looked at it inflates the headline number with an outage. Counted as a
# flat 0.5 and named, so the metric and the caption cannot disagree.
n_dark = sum(1 for *_, no_live, _, _ in rows if no_live)
n_played = sum(1 for *_, played in rows if played)
n_offboard = n_dark - n_played
exp_wins = sum(0.5 if (p is None or p.tier == "COIN FLIP") else p.prob
               for _, p, _no_live, _, _ in rows)
c1, c2, c3 = st.columns(3)
c1.metric("Expected wins", f"{exp_wins:.1f} / {len(rows)}",
          help=(f"{n_dark} game(s) with no market reading counted as a flat "
                "0.5 — an outage, not a modelled edge. No pick is shown for "
                "them." if n_dark else None))
c2.metric("Strong + Solid + Lean",
          sum(1 for _, p, _n, _, _ in rows if p is not None and p.tier != "COIN FLIP"))
# COIN FLIP means the model looked and found no edge. A game with no reading
# was never looked at. Lumping them together made the one number that says
# "how much of this slate is guesswork" unable to tell the two apart.
c3.metric("Coin flips",
          sum(1 for _, p, _n, _, _ in rows if p is not None and p.tier == "COIN FLIP"),
          help=(f"{n_dark} game(s) with no market reading are NOT counted here "
                "— they have no pick at all." if n_dark else None))

if n_dark:
    if not board_ok:
        why = "the board above is unavailable"
    elif n_played and not n_offboard:
        why = "they have already kicked off, so the books have pulled them"
    elif n_played:
        why = (f"{n_played} already kicked off; {n_offboard} "
               f"{'is' if n_offboard == 1 else 'are'} not on the scraped board")
    else:
        why = "not on the scraped board"
    st.warning(f"⚠️ {n_dark} of {len(rows)} games have NO market reading "
               f"({why}) — those cards show **NO READING** instead of a pick. "
               "A pick computed from CBS's own number is not a neutral 50%: it "
               "is the side CBS froze as favourite, which is the opposite of "
               "the side the market moved to. Each is counted as a flat 0.5.")
    if n_offboard and board_ok:
        st.caption("Re-collect the board with `python3 scripts/odds_collect.py "
                   "--profile pickem_nfl` if a game that has not kicked off is "
                   "missing.")

def day_header(kickoff_utc: str) -> str:
    """The kickoff's EASTERN day, e.g. "Sun Sep 13".

    THE BUG THIS REPLACES, found 2026-09-09: `kickoff_utc[:10]`, the first ten
    characters of a UTC timestamp. Every NFL night game kicks off after 00:00
    UTC, so it rendered a day late -- four of the sixteen Week 1 games, and
    they are exactly the four that carry their own separate pool deadline:

        NE@SEA  2026-09-10T00:20:00Z -> "2026-09-10", really Wed 09-09 8:20pm ET
        SF@LAR  2026-09-11T00:35:00Z -> "2026-09-11", really Thu 09-10 8:35pm ET
        DAL@NYG 2026-09-14T00:20:00Z -> "2026-09-14", really Sun 09-13 8:20pm ET
        DEN@KC  2026-09-15T00:15:00Z -> "2026-09-15", really Mon 09-14 8:15pm ET

    The TOO-GOODE pool's deadline is PER DAY, and PICKEM_WEEKLY.md section 2
    calls an empty day "the one truly fatal mistake". Filing Sunday night's
    game under Monday tells the reader they have a day longer than they do.

    An unparseable value falls back to the raw string rather than to "": a
    missing header would fold the row silently into the previous day's group,
    which is the same failure in a quieter form.
    """
    kick = kick_dt(kickoff_utc)
    if kick is None:
        return kickoff_utc or "TBD"
    return kick.astimezone(EASTERN).strftime("%a %b %-d")


last_day = None
for r, pk, no_live, live_g, played in rows:
    day_label = day_header(r.get("kickoff_utc", ""))
    if day_label != last_day:
        st.markdown(f'<div class="pk-day">{day_label}</div>', unsafe_allow_html=True)
        last_day = day_label

    cbs_line = float(r["cbs_line_home"])
    away_pct = int(r.get("comm_pct_away") or 0)
    home_pct = int(r.get("comm_pct_home") or 0)
    # ALWAYS render the note. The old condition was
    #   `... and "provisional" not in r["note"]`
    # which suppressed the one word worth showing: every row of
    # data/pickem_current_week.csv was tagged provisional, so the file's
    # staleness was invisible on the page that depends on it.
    note_html = f'<div class="pk-note">{r["note"]}</div>' if r.get("note") else ""
    books_html = ""
    if live_g and live_g.n_books:
        # book disagreement is a confidence caveat: a consensus built from
        # books that are a full point apart deserves less trust
        dis = live_g.book_spread
        books_html = (f' &nbsp; {live_g.n_books} BOOKS'
                      + (f' (spread {dis:.1f})' if dis else ''))

    if no_live:
        # NO PICK. Not a 50% pick, not a greyed-out pick -- none. The pick box
        # is the only thing on this card a phone reads as the answer, so it has
        # to say the model has no answer. CBS's own number stays (it is what
        # the pool grades against and it is real), the market number and the
        # edge become em-dashes because they do not exist, and the badge says
        # WHY the reading is missing: a played game needs no remedy, a game
        # missing from the board needs `odds_collect.py`.
        card_cls = "pk-card dark"
        pick_cell = ('<span class="pk-pickbox pk-nopick">—</span>'
                     '<span class="pk-noreading">NO READING</span>')
        badge = ('<span class="pk-played">kicked off — books pull a game '
                 'at kickoff</span>' if played else
                 '<span class="pk-nolive">not on the board</span>')
        nums = (f'CBS <b>{cbs_line:+.1f}</b> &nbsp; MARKET <b>—</b>'
                f' &nbsp; EDGE <b>—</b> &nbsp; COMMUNITY <b>{away_pct}/{home_pct}</b>')
    else:
        card_cls = "pk-card strong" if pk.tier == "STRONG" else "pk-card"
        side_name = (pk.matchup.split(" @ ")[0] if pk.side == "away"
                     else pk.matchup.split(" @ ")[1])
        pick_cell = (
            f'<span class="pk-pickbox">{side_name} {pk.side_line:+.1f}</span>'
            f'<span class="pk-prob">{pk.prob:.0%}</span>'
            f'<span class="pk-pill {TIER_CLASS.get(pk.tier, "")}">{pk.tier}</span>')
        badge = ""
        comm_pct = home_pct if pk.side == "home" else away_pct
        nums = (f'CBS <b>{pk.pool_line:+.1f}</b> &nbsp; MARKET <b>{pk.live_line:+.1f}</b>'
                f' &nbsp; EDGE <b>{abs(pk.edge_pts):.1f}</b>'
                f' &nbsp; COMMUNITY <b>{comm_pct}%</b>')

    st.markdown(f'''
<div class="{card_cls}">
  <div class="pk-row1">
    <span class="pk-matchup">{r["away_name"]} <span style="color:#7f8a9c;font-weight:500;">@</span> {r["home_name"]}</span>
    <span class="pk-tv">{r.get("tv", "")}</span>
  </div>
  <div class="pk-pickrow">
    {pick_cell}
    {badge}
  </div>
  <div class="pk-nums">{nums}{books_html}</div>
  {note_html}
</div>
''', unsafe_allow_html=True)

st.caption(f"CBS lines from {CURRENT_WEEK_CSV.name}, last committed capture. "
           "Rows tagged `provisional` have NOT been re-verified against the "
           "live pool page — see the banner at the top of this page.")
