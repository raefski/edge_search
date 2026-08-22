"""pages/4_🎯_Pickem.py — NFL pick'em spread-edge picks (TOO-GOODE pool).

Streamlit's native multi-page mechanism (this file living in pages/) is what
adds "Pickem" to the sidebar next to the MLB DFS home page -- no routing code
needed, and app.py's working MLB flow is untouched. Numbered 4_ rather than
2_/3_ deliberately: DFS_MULTISPORT_PLAN.md already reserves those slots for
real NFL/NBA DFS lineup pages, a different game (salary-cap rosters) from
this one (spread picks) despite the shared "NFL" word.

Two data sources, matching app.py's own free-vs-manual split:
  * Live market line: edge.pickem_live.fetch_week, via edge.client's Odds
    API (dry-run/cache by default, ~1 credit for the WHOLE week's slate in
    one call when you explicitly tap "Pull fresh lines"). Originally free
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
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.client import CreditFloorError, DryRunBlocked, NoApiKey, OddsAPIClient  # noqa: E402
from edge.pickem import make_pick  # noqa: E402
from edge.pickem_live import fetch_week  # noqa: E402
from edge.pickem_log import load as load_line_log  # noqa: E402

CURRENT_WEEK_CSV = ROOT / "data" / "pickem_current_week.csv"
CACHE_DIR = ROOT / "data" / "cache"
LEDGER = ROOT / "data" / "odds_api_credits.json"


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
    week = st.number_input("Week", min_value=1, max_value=18, value=1, step=1)

    api_key = st.text_input("ODDS_API_KEY", value=os.environ.get("ODDS_API_KEY", ""),
                            type="password", help="Stored only for this session.")
    if api_key:
        os.environ["ODDS_API_KEY"] = api_key

    _remaining = OddsAPIClient(cache_dir=CACHE_DIR, ledger_path=LEDGER).remaining_credits()
    if _remaining is not None:
        st.caption(f"Odds API: {_remaining} credits remaining this cycle")

    pull_fresh = st.button(
        "💰 Pull fresh lines (~2 credits)", use_container_width=True,
        help="One call covers the whole week's slate: spreads + totals across every "
             "available book (markets × regions = 2), then free for 10 minutes. "
             "Nothing spends unless you tap this.")

if not CURRENT_WEEK_CSV.exists():
    st.warning(f"No {CURRENT_WEEK_CSV.name} committed yet for this week.")
    st.stop()

with CURRENT_WEEK_CSV.open() as f:
    cbs_rows = [r for r in csv.DictReader(f) if int(r["week"]) == week]

if not cbs_rows:
    st.info(f"No Week {week} lines captured yet.")
    st.stop()

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

client = OddsAPIClient(cache_dir=CACHE_DIR, ledger_path=LEDGER, dry_run=not pull_fresh)
live_by_abbr = {}
try:
    live_games = fetch_week(client)
    live_by_abbr = {g.home_abbr: g for g in live_games}
except NoApiKey:
    st.warning("No ODDS_API_KEY set — paste your key in the sidebar, then tap "
               "**Pull fresh lines**. Showing CBS lines only until then.")
except DryRunBlocked:
    st.info("No live lines cached yet this week — tap **💰 Pull fresh lines** "
            "in the sidebar (~1 credit for the whole slate). Showing CBS lines only.")
except CreditFloorError as e:
    st.error(f"Skipped the live pull to protect your credit floor: {e}")
except Exception as e:
    st.error(f"Couldn't reach the Odds API ({e}) — showing CBS lines only, no edge computed.")

TIER_CLASS = {"STRONG": "strong", "SOLID": "solid", "LEAN": "lean", "COIN FLIP": ""}

rows = []
for r in cbs_rows:
    cbs_line = float(r["cbs_line_home"])
    live = live_by_abbr.get(r["home_abbr"])
    live_line = live.live_line if live and live.live_line is not None else cbs_line
    pk = make_pick(r["away_name"], r["home_name"], cbs_line, live_line,
                   post_totals.get(r["home_abbr"]),
                   live.total if live else None)
    rows.append((r, pk, live is None or live.live_line is None))

exp_wins = sum(p.prob if p.tier != "COIN FLIP" else 0.5 for _, p, _ in rows)
c1, c2, c3 = st.columns(3)
c1.metric("Expected wins", f"{exp_wins:.1f} / {len(rows)}")
c2.metric("Strong + Solid + Lean", sum(1 for _, p, _ in rows if p.tier != "COIN FLIP"))
c3.metric("Coin flips", sum(1 for _, p, _ in rows if p.tier == "COIN FLIP"))

if any(no_live for *_, no_live in rows):
    st.caption("⚠️ Some games had no live line available yet — shown at CBS's number, no edge.")

last_day = None
for r, pk, no_live in rows:
    day_label = r.get("kickoff_utc", "")[:10] or "TBD"
    if day_label != last_day:
        st.markdown(f'<div class="pk-day">{day_label}</div>', unsafe_allow_html=True)
        last_day = day_label

    card_cls = "pk-card strong" if pk.tier == "STRONG" else "pk-card"
    pill_cls = f"pk-pill {TIER_CLASS.get(pk.tier, '')}"
    away_pct = int(r.get("comm_pct_away") or 0)
    home_pct = int(r.get("comm_pct_home") or 0)
    comm_pct = home_pct if pk.side == "home" else away_pct
    note_html = f'<div class="pk-note">{r["note"]}</div>' if r.get("note") and "provisional" not in r["note"] else ""
    live_g = live_by_abbr.get(r["home_abbr"])
    books_html = ""
    if live_g and live_g.n_books:
        # book disagreement is a confidence caveat: a consensus built from
        # books that are a full point apart deserves less trust
        dis = live_g.book_spread
        books_html = (f' &nbsp; {live_g.n_books} BOOKS'
                      + (f' (spread {dis:.1f})' if dis else ''))

    st.markdown(f'''
<div class="{card_cls}">
  <div class="pk-row1">
    <span class="pk-matchup">{r["away_name"]} <span style="color:#7f8a9c;font-weight:500;">@</span> {r["home_name"]}</span>
    <span class="pk-tv">{r.get("tv", "")}</span>
  </div>
  <div class="pk-pickrow">
    <span class="pk-pickbox">{pk.matchup.split(" @ ")[0] if pk.side == "away" else pk.matchup.split(" @ ")[1]} {pk.side_line:+.1f}</span>
    <span class="pk-prob">{pk.prob:.0%}</span>
    <span class="{pill_cls}">{pk.tier}</span>
  </div>
  <div class="pk-nums">CBS <b>{pk.pool_line:+.1f}</b> &nbsp; MARKET <b>{pk.live_line:+.1f}</b>
    &nbsp; EDGE <b>{abs(pk.edge_pts):.1f}</b> &nbsp; COMMUNITY <b>{comm_pct}%</b>{books_html}</div>
  {note_html}
</div>
''', unsafe_allow_html=True)

st.caption(f"CBS lines from {CURRENT_WEEK_CSV.name}, last committed capture. "
           "Provisional rows haven't been re-verified against the live pool page yet.")
