"""pages/10_🎲_Multi_Sport_Parlays.py — the best straight (no-boost) parlay across every sport on the board."""
from __future__ import annotations

import html
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Best Parlay", page_icon="🎲", layout="wide",
                   initial_sidebar_state="collapsed")

from edge.dfs_pagereload import reload_packages, source_fingerprint  # noqa: E402

_PKGS = ("edge.arb", "edge.repo_files")
_GLOBS = ("edge/arb/*.py", "edge/repo_files.py")


@st.cache_resource(show_spinner=False)
def _reload_edge(fingerprint: float) -> float:
    reload_packages(_PKGS)
    return fingerprint


_reload_edge(source_fingerprint(_GLOBS))

from edge import repo_files  # noqa: E402
from edge.arb import ArbConfig, oddsmath as om, parlay as PL  # noqa: E402
from edge.arb import scan_request as SR  # noqa: E402

ET = ZoneInfo("America/New_York")
SNAPSHOT = ROOT / "data" / "arb_snapshot.json"
STALE_MINUTES = 45
MAX_LEGS = 10

st.markdown("""
<style>
.block-container {padding-top: 2.0rem; padding-bottom: 2rem; max-width: 900px;}
h1 {font-size: 1.5rem !important; margin-bottom: .1rem;}
.sub {font-size: 13px; color: #9aa4b2; line-height: 1.5; margin: 0 0 .6rem;}
.kpis {display:flex; flex-wrap:wrap; gap:6px 18px; margin:.3rem 0 .5rem;}
.kpi {min-width: 130px;}
.kpi .v {font-size: 1.25rem; font-weight: 700;}
.kpi .l {font-size: 11px; color:#9aa4b2; text-transform: uppercase; letter-spacing:.03em;}
.good {color:#3fb950;} .bad {color:#f85149;} .muted {color:#9aa4b2;}
.tw {overflow-x:auto;}
table.pl {width:100%; border-collapse:collapse; font-size:14px;}
table.pl th {text-align:left; color:#7f8a9c; font-weight:600; font-size:11px;
             text-transform:uppercase; padding:3px 6px; border-bottom:1px solid rgba(128,128,128,.35);}
table.pl td {padding:6px 6px; border-bottom:1px solid rgba(128,128,128,.15); vertical-align:top;}
table.pl td.n {text-align:right; white-space:nowrap; font-variant-numeric: tabular-nums;}
table.pl .g {font-size:12px; color:#9aa4b2;}
table.pl tr.pick td {background: rgba(63,185,80,.10);}
</style>
""", unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape(str(x))


def kickoff(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso).astimezone(ET)
    except (TypeError, ValueError):
        return ""
    return dt.strftime("%a %-I:%M%p").replace("AM", "a").replace("PM", "p")


def et_date(iso: str) -> date | None:
    try:
        return datetime.fromisoformat(iso).astimezone(ET).date()
    except (TypeError, ValueError):
        return None


def money(x: float) -> str:
    return f"${x:,.2f}" if abs(x) < 100 else f"${x:,.0f}"


def one_in(p) -> str:
    return f"1 in {p.one_in:,.0f}" if p.one_in < 1e7 else "—"


def age_minutes(iso: str | None) -> float | None:
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


def age_text(minutes: float | None) -> str:
    if minutes is None:
        return "unknown age"
    return f"{minutes:.0f} min ago" if minutes < 90 else f"{minutes / 60:.1f} h ago"


def parse_american(text: str) -> float | None:
    t = (text or "").strip().replace("−", "-")
    if not t:
        return None
    try:
        return PL.american_to_decimal(float(t))
    except (ValueError, ZeroDivisionError):
        st.warning(f"Could not read odds {text!r} — ignoring it.")
        return None


@st.cache_data(show_spinner=False, max_entries=3)
def _mp_snapshot_json(key: tuple, _text: str) -> dict:
    return json.loads(_text)


@st.cache_data(show_spinner=False, max_entries=24)
def _mp_legs(snap_key: tuple, _cands: list, promo: PL.ParlayPromo, allow_book_only: bool,
             max_leg_dec: float | None, days: tuple, now_bucket: int) -> list:
    now = datetime.fromtimestamp(now_bucket * 60, timezone.utc)
    legs = PL.legs_from_candidates(_cands, promo, now=now, allow_book_only=allow_book_only,
                                   max_leg_decimal=max_leg_dec)
    if days:
        legs = [l for l in legs if et_date(l.commence_time) in days]
    return legs


@st.cache_data(show_spinner=False, max_entries=24)
def _mp_build(legs_key: tuple, _legs: list, promo: PL.ParlayPromo, n_legs, stake: float,
              max_one_in, bankroll: float):
    return PL.build(_legs, promo, n_legs=n_legs, stake=stake, max_one_in=max_one_in,
                    bankroll=bankroll)


found = repo_files.read(SNAPSHOT)
snap = None
if found is not None:
    try:
        snap = _mp_snapshot_json((found.source, found.updated_at.isoformat(), len(found.data)),
                                 found.data)
    except ValueError:
        snap = None

st.title("🎲 Best Parlay")
st.markdown('<div class="sub">The best straight parlay (no boost) across every sport on the '
            'board, priced at one book against the rest of the market. One leg per game.</div>',
            unsafe_allow_html=True)

cfg = ArbConfig()
sport_titles = SR.sport_choices(cfg, snap)
on_board = [k for k, _ in Counter(c.get("sport_key") for c in (snap or {}).get("candidates", [])
                                  if c.get("sport_key")).most_common()]

c1, c2 = st.columns(2)
book = c1.radio("Book", ["fanduel", "draftkings"], horizontal=True,
                format_func=lambda b: PL.BOOK_NAMES[b])
DAYS = {"Today": 0, "Today + tomorrow": 1, "All upcoming": None}
days_label = c2.radio("Games", list(DAYS), horizontal=True, index=0)

only = st.multiselect("Only these sports (blank = every sport on the board)", on_board,
                      format_func=lambda k: sport_titles.get(k, k))

c1, c2, c3 = st.columns(3)
legs_pick = c1.selectbox("Legs", ["Best"] + list(range(2, MAX_LEGS + 1)), index=0,
                         help="'Best' searches 2 to 10 legs; a number gives the best ticket of exactly that size.")
CAPS = {"1 in 5": 5, "1 in 10": 10, "1 in 25": 25, "1 in 50": 50, "1 in 100": 100,
        "1 in 1,000": 1000, "Any": None}
cap_label = c2.selectbox("Longest shot I'll take", list(CAPS), index=3)
stake = c3.number_input("Bet $", 1.0, 10000.0, 10.0, step=5.0)

with st.expander("More options"):
    c1, c2 = st.columns(2)
    min_leg_txt = c1.text_input("No leg shorter than (American)", "-500",
                                help="Keeps −50000 'locks' out: they add vig and almost no payout. Blank = none.")
    max_leg_txt = c2.text_input("No leg longer than (American)", "",
                                help="e.g. +300 keeps every leg at +300 or shorter. Blank = none.")
    c1, c2 = st.columns(2)
    bankroll = c1.number_input("Bankroll for the Kelly note $", 100.0, 1e6,
                               float(cfg.bankroll.total), step=100.0)
    allow_book_only = c2.checkbox("Include markets no other book prices", value=False,
                                  help="Their fair price is the book's own no-vig line, so they can "
                                       "never be +EV — only useful to fill a fixed leg count.")
    virtual = st.checkbox("Include virtual leagues (eBasketball, eFootball …)", value=False)

sports = only or [s for s in on_board if virtual or "h2h_gg" not in s]

today = datetime.now(ET).date()
ndays = DAYS[days_label]
days = () if ndays is None else tuple(today + timedelta(days=i) for i in range(ndays + 1))

# ------------------------------------------------------------- board status
gen_at = (snap or {}).get("generated_at")
mins = age_minutes(gen_at)
creds = repo_files.credentials()
c1, c2 = st.columns([3, 2])
if snap:
    names = [sport_titles.get(s, s) for s in on_board]
    sports_line = ", ".join(names[:5]) + (f" +{len(names) - 5} more" if len(names) > 5 else "")
    msg = f"Board: {sports_line or 'nothing'} · scanned {age_text(mins)}"
    (c1.warning if (mins or 0) > STALE_MINUTES else c1.caption)(msg)
else:
    c1.info("No board yet — ask the desktop for a scan.")
if c2.button("🔄 Rescan every sport", width="stretch", disabled=not all(creds),
             help="Asks the desktop to scrape every sport for these days and push a fresh board."
             if all(creds) else "Needs GITHUB_REPO / GITHUB_TOKEN (set on the cloud app)."):
    try:
        req = SR.ScanRequest.new(sports=[], note="best parlay (all sports)",
                                 date_from=today.isoformat(),
                                 date_to=(days[-1].isoformat() if days else None))
        SR.put_request(creds[0], creds[1], req)
        st.session_state["mp_scan_at"] = req.requested_at
        st.success("Asked the desktop for every sport. Expect a new board in about "
                   "10 minutes — tap anything after that to reload.")
    except Exception as exc:                                     # noqa: BLE001
        st.error(f"Could not file the request: {type(exc).__name__}: {exc}")
if st.session_state.get("mp_scan_at") and gen_at:
    if SR.snapshot_is_newer(gen_at, SR.ScanRequest(
            requested_at=st.session_state["mp_scan_at"], request_id="local")):
        st.success("✅ Fresh board is in.")
        st.session_state.pop("mp_scan_at", None)
    else:
        st.info("⏳ Waiting on the desktop scan — the board below is the previous one.")

if not snap:
    st.stop()
if not sports:
    st.info("Pick at least one sport.")
    st.stop()

# ------------------------------------------------------------------ build
min_leg_dec = parse_american(min_leg_txt) or 1.0
max_leg_dec = parse_american(max_leg_txt)
promo = PL.ParlayPromo(book=book, sports=list(sports), boost_by_legs={}, min_legs=2,
                       max_legs=MAX_LEGS, min_leg_decimal=min_leg_dec, max_stake=float(stake))
snap_key = (found.source, found.updated_at.isoformat(), len(found.data))
now_bucket = int(datetime.now(timezone.utc).timestamp() // 60)
legs = _mp_legs(snap_key, snap.get("candidates", []), promo, allow_book_only, max_leg_dec,
                days, now_bucket)
n_legs = None if legs_pick == "Best" else int(legs_pick)
with st.spinner("Searching…"):
    res = _mp_build((snap_key, promo, allow_book_only, max_leg_dec, days, now_bucket), legs,
                    promo, n_legs, float(stake), CAPS[cap_label], float(bankroll))

games = len({l.event_id for l in legs})
n_sports = len({l.sport_key for l in legs})
plus = sorted((l for l in legs if l.raw_ratio > 1.0), key=lambda l: -l.raw_ratio)
st.caption(f"{len(legs):,} legs from {games} games in {n_sports} sport{'s' * (n_sports != 1)} "
           f"at {PL.BOOK_NAMES[book]} · {len(plus)} of them +EV on their own")
if not legs:
    st.error(f"No {PL.BOOK_NAMES[book]} legs for these sports and days on the board. "
             "Rescan above, or widen 'Games'.")
    st.stop()
if res.note:
    st.warning(res.note)
pick = res.pick
if pick is None:
    st.stop()

BOOK_ABBR = PL.BOOK_SHORT[book]


def kpi(label: str, value: str, cls: str = "") -> str:
    return f'<div class="kpi"><div class="v {cls}">{esc(value)}</div><div class="l">{esc(label)}</div></div>'


def leg_rows(legs_, numbered=True, mark=None) -> str:
    rows = []
    for i, l in enumerate(legs_, 1):
        flags = []
        if l.suspect:
            flags.append("⚠️ far off market — likely a stale line")
        if l.book_only:
            flags.append(f"{BOOK_ABBR} only")
        src = "+".join(PL.BOOK_SHORT.get(b, b) for b in l.sources)
        edge_cls = "good" if l.raw_ratio >= 1 else ("muted" if l.raw_ratio >= .95 else "bad")
        cls = ' class="pick"' if mark and mark(l) else ""
        sport = sport_titles.get(l.sport_key, l.sport_key)
        rows.append(
            f"<tr{cls}>" + (f'<td class="n muted">{i}</td>' if numbered else "") +
            f'<td><b>{esc(l.describe())}</b><br><span class="g">{esc(sport)} · {esc(l.matchup)} · '
            f'{esc(kickoff(l.commence_time))}{" · " + esc("; ".join(flags)) if flags else ""}</span></td>'
            f'<td class="n"><b>{esc(l.american)}</b></td>'
            f'<td class="n">{l.fair_prob:.0%}<br><span class="g">{esc(src)}</span></td>'
            f'<td class="n {edge_cls}">{l.edge_pct:+.1f}%</td></tr>')
    head = ('<tr>' + ('<th></th>' if numbered else '') +
            f'<th>Leg</th><th style="text-align:right">{BOOK_ABBR}</th>'
            '<th style="text-align:right">Fair</th><th style="text-align:right">Edge</th></tr>')
    return f'<div class="tw"><table class="pl">{head}{"".join(rows)}</table></div>'


ev_cls = "good" if pick.ev > 0 else "bad"
st.subheader(f"The ticket · {pick.n} legs")
st.markdown('<div class="kpis">' +
            kpi("expected value", f"{pick.ev:+.1%}  ({money(pick.ev_dollars)})", ev_cls) +
            kpi("hits", one_in(pick)) +
            kpi("odds", om.format_american(pick.decimal)) +
            kpi(f"to win on {money(pick.stake)}", money(pick.to_win)) +
            "</div>", unsafe_allow_html=True)
if pick.ev <= 0:
    st.warning(f"No +EV {pick.n}-leg parlay on this board — this is the least-bad one. Without a "
               "boost a parlay is only +EV when its legs are, and the vig compounds with every leg"
               + (" (no leg on the board is +EV)." if not plus else
                  f" (only {len(plus)} leg{'s' * (len(plus) != 1)} on the board "
                  f"{'is' if len(plus) == 1 else 'are'} +EV)." if len(plus) < pick.n else "."))
st.markdown(leg_rows(pick.legs), unsafe_allow_html=True)
st.caption("Fair = the leg's no-vig win chance from the other books, shrunk toward this book's own "
           "line. Edge = fair × price − 1; a standard −110 leg is about −4.5%. The board is "
           f"{PL.BOOK_NAMES[book]}'s online prices from {age_text(mins)} — check each one at the "
           "counter or kiosk before you bet; retail can differ.")

with st.expander("🔁 Swaps — if a leg has moved or been pulled"):
    for i, alts in PL.bench(pick, legs).items():
        if not alts:
            continue
        st.markdown(f"**{i + 1}. {esc(pick.legs[i].describe())}** "
                    f"<span class='muted'>({esc(pick.legs[i].american)})</span> → swap for:",
                    unsafe_allow_html=True)
        st.markdown(leg_rows(alts, numbered=False), unsafe_allow_html=True)

if n_legs is None and len(res.by_legs) > 1:
    st.subheader("Best ticket at each number of legs")
    rows = []
    for n, p in sorted(res.by_legs.items()):
        rows.append(
            f'<tr class="{"pick" if p.n == pick.n else ""}"><td class="n">{n}</td>'
            f'<td class="n {"good" if p.ev > 0 else "bad"}">{p.ev:+.1%}<br>'
            f'<span class="g">{esc(money(p.ev_dollars))}</span></td>'
            f'<td class="n">{esc(one_in(p))}</td>'
            f'<td class="n">{esc(om.format_american(p.decimal))}</td>'
            f'<td class="n">{esc(money(p.to_win))}</td></tr>')
    st.markdown('<div class="tw"><table class="pl"><tr><th style="text-align:right">Legs</th>'
                '<th style="text-align:right">EV</th><th style="text-align:right">Hits</th>'
                '<th style="text-align:right">Odds</th><th style="text-align:right">To win</th></tr>'
                + "".join(rows) + "</table></div>", unsafe_allow_html=True)
    st.caption("Set 'Legs' above to see any of these in full. A longer ticket is only picked "
               "when each extra leg adds at least 1.5 points of EV — leg edges are estimates.")

with st.expander(f"Best single legs at {PL.BOOK_NAMES[book]} (build your own)"):
    top = sorted(legs, key=lambda l: -l.raw_ratio)[:40]
    st.markdown(leg_rows(top, numbered=False, mark=lambda l: l in pick.legs),
                unsafe_allow_html=True)

with st.expander("How this works"):
    st.markdown("""
Each leg returns **r = fair win chance × decimal price** per $1 (1.00 = fair; a −110 leg ≈ 0.955).
A straight parlay returns the **product** of its legs' r, so its EV is **Πr − 1**. Every leg below
1.00 drags the ticket down, and that compounds — a parlay is only +EV when it's built from +EV
legs. With no boost to cover the vig, "nothing +EV" is a normal and honest answer.

The search tries every combination of one leg per game (any sport, any day you picked) and keeps
the best EV at each hit rate. "Longest shot I'll take" stops it from picking a 1-in-thousands ticket.

**Same-game parlays are not included.** The book prices an SGP with its own correlation model, so
the payout is not the product of the legs' prices and can't be worked out from this board.
""")
