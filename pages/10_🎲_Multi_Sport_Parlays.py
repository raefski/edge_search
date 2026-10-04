"""pages/10_🎲_Multi_Sport_Parlays.py — casino cash-line parlays across all sports.

No boost required. Just +EV parlays on a single book (default FanDuel)
across any sports in a time window. Use this at the casino sportsbook.
"""
from __future__ import annotations

import html
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Multi-Sport Parlays", page_icon="🎲", layout="wide",
                   initial_sidebar_state="collapsed")

from edge.dfs_pagereload import reload_packages, source_fingerprint

_PKGS = ("edge.arb", "edge.repo_files")
_GLOBS = ("edge/arb/*.py", "edge/repo_files.py")

@st.cache_resource(show_spinner=False)
def _reload_edge(fingerprint: float) -> float:
    reload_packages(_PKGS)
    return fingerprint

_reload_edge(source_fingerprint(_GLOBS))

from edge import repo_files
from edge.arb import ArbConfig, oddsmath as om, parlay as PL
from edge.arb import scan_request as SR

ET = ZoneInfo("America/New_York")
SNAPSHOT = ROOT / "data" / "arb_snapshot.json"

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

def money(x: float) -> str:
    return f"${x:,.2f}" if abs(x) < 100 else f"${x:,.0f}"

def signed_pct(x: float) -> str:
    return f"{x:+.1%}"

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

def american_or_blank(dec: float) -> str:
    return "" if dec <= 1.0 + 1e-9 else om.format_american(dec)

@st.cache_data(show_spinner=False)
def _snapshot_json() -> dict:
    if SNAPSHOT.exists():
        return json.loads(SNAPSHOT.read_text())
    # Try GitHub
    try:
        return json.loads(repo_files.get_text("data/arb_snapshot.json"))
    except Exception:
        return {"candidates": [], "timestamp": None}

@st.cache_data(show_spinner=False)
def _multi_sport_legs(snapshot: dict, book: str, days: int, max_leg_decimal: float) -> list[PL.ParlayLeg]:
    candidates = snapshot.get("candidates", [])
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)
    
    legs = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        
        start = cand.get("commence_time")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(start)
        except (ValueError, TypeError):
            continue
        
        if not (now <= dt <= cutoff):
            continue
        
        for leg in cand.get("legs", []):
            if not isinstance(leg, dict) or leg.get("book") != book:
                continue
            
            dec = leg.get("decimal", 1.0)
            if dec > max_leg_decimal or dec <= 1.0:
                continue
            
            # Price this leg against the rest of the market
            fair_p = 0.5
            sources = ()
            try:
                result = PL.fair_probs(cand, book)
                if result:
                    fair_dict, sources = result
                    fair_p = fair_dict.get(leg["side"], 0.5)
            except Exception:
                pass

            r = fair_p * dec

            legs.append(PL.ParlayLeg(
                event_id=cand.get("event_id", ""),
                sport_key=cand.get("sport_key", ""),
                matchup=cand.get("matchup", ""),
                commence_time=cand.get("commence_time", ""),
                market=cand.get("market", ""),
                subject=cand.get("subject"),
                point=cand.get("point"),
                side=leg.get("side", ""),
                label=f"{leg.get('label', '')}",
                decimal=dec,
                fair_prob=fair_p,
                sources=sources,
                own_hold=cand.get("own_hold")
            ))
    
    return legs

@st.cache_data(show_spinner=False)
def _multi_sport_build(legs: tuple, n_legs: int | None = None, leg_band: float = 0.12,
                       max_one_in: int = 1000, bankroll: int = 1000,
                       per_event: int = 8) -> PL.BuildResult | None:
    if not legs:
        return None
    promo = PL.ParlayPromo(
        book="fanduel", sports=[], boost_by_legs={},
        min_legs=2, max_legs=15, max_stake=1000.0
    )
    try:
        return PL.build(list(legs), promo, n_legs=n_legs, stake=None,
                       max_one_in=max_one_in, bankroll=bankroll,
                       per_event=per_event, leg_band=leg_band)
    except Exception:
        return None

# --- Page ---
st.title("🎲 Multi-Sport Parlays")
st.markdown("**Casino cash-line parlays** — find +EV tickets across all sports (or same-game) with no boost required.",
            unsafe_allow_html=True)

snapshot_data = _snapshot_json()
ts = snapshot_data.get("timestamp")
age_m = age_minutes(ts) if ts else None

col1, col2 = st.columns(2)
with col1:
    book = st.radio("Book", ["fanduel", "draftkings", "fan_duel", "dk"], 
                    index=0, horizontal=True)
    book = "fanduel" if book.startswith("fan") else "draftkings"

with col2:
    days = st.slider("Days ahead", 1, 7, 1)
    max_odds = st.select_slider("Max leg odds", 
                                 options=[1.20, 1.30, 1.40, 1.50, 2.00, 3.00],
                                 value=2.00)

col1, col2, col3, col4 = st.columns(4)
with col1:
    fix_legs = st.number_input("Legs (0 = all)", 0, 15, value=0, step=1,
                               help="0 to search all leg counts, or 2-15 for fixed")
    fix_legs = fix_legs if fix_legs > 0 else None
with col2:
    max_one_in = st.number_input("Hit rate cap", 10, 10000, 1000, step=100,
                                  help="Skip tickets less likely than this")
with col3:
    bankroll = st.number_input("Bankroll", 100, 10000, 1000, step=100)
with col4:
    stake = st.number_input("Stake per ticket", 1.0, 1000.0, 10.0, step=1.0)

if st.button("🔄 Rescan", key="rescan_multi"):
    st.cache_data.clear()

col1, col2 = st.columns([2, 1])
with col1:
    st.markdown(f"**Board:** {age_minutes(ts) or '?':.0f} min ago" if age_m else "**Board:** unknown age")
with col2:
    if st.button("Scan Live"):
        try:
            SR.put_request("all", [])
            st.info("Scan requested. Refresh in ~30s.")
        except Exception as e:
            st.warning(f"Scan request failed: {e}")

# Build
legs_tuple = tuple(_multi_sport_legs(snapshot_data, book, days, max_odds))
if not legs_tuple:
    st.warning(f"No {book} legs found in the next {days} day(s) with odds ≤ {max_odds:.2f}.")
    st.stop()

st.markdown(f"**{len(legs_tuple):,} legs** from {len(set(l.event_id for l in legs_tuple))} games")

result = _multi_sport_build(legs_tuple, n_legs=fix_legs, max_one_in=max_one_in, bankroll=bankroll, per_event=8)

if not result or not result.pick:
    st.warning("No +EV parlay found.")
    st.stop()

pick = result.pick
st.markdown("---")

# KPIs
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown(f"""
    <div class="kpi">
        <div class="v good">{pick.n} legs</div>
        <div class="l">The Ticket</div>
    </div>
    """, unsafe_allow_html=True)
with col2:
    st.markdown(f"""
    <div class="kpi">
        <div class="v good">{signed_pct(pick.ev / stake)}</div>
        <div class="l">EV %</div>
    </div>
    """, unsafe_allow_html=True)
with col3:
    st.markdown(f"""
    <div class="kpi">
        <div class="v good">{money(pick.ev)}</div>
        <div class="l">EV $</div>
    </div>
    """, unsafe_allow_html=True)
with col4:
    st.markdown(f"""
    <div class="kpi">
        <div class="v">{one_in(pick)}</div>
        <div class="l">Hit Rate</div>
    </div>
    """, unsafe_allow_html=True)

col1, col2, col3 = st.columns(3)
with col1:
    st.markdown(f"""
    <div class="kpi">
        <div class="v">{american_or_blank(pick.decimal)}</div>
        <div class="l">Odds</div>
    </div>
    """, unsafe_allow_html=True)
with col2:
    st.markdown(f"""
    <div class="kpi">
        <div class="v">{money(stake * (pick.decimal - 1))}</div>
        <div class="l">To Win (${stake})</div>
    </div>
    """, unsafe_allow_html=True)
with col3:
    st.markdown(f"""
    <div class="kpi">
        <div class="v">{signed_pct(pick.growth())}</div>
        <div class="l">Kelly Growth</div>
    </div>
    """, unsafe_allow_html=True)

# Ticket
st.markdown("### Legs")
rows = []
for i, leg in enumerate(pick.legs, 1):
    rows.append(f"""
    <tr>
        <td class="n">{i}</td>
        <td>{esc(leg.label[:50])}</td>
        <td class="n">{american_or_blank(leg.decimal)}</td>
        <td class="n">{leg.fair_prob:.1%}</td>
        <td class="n good" style="color:#3fb950">{leg.edge_pct:+.1%}</td>
        <td class="g">{kickoff(leg.commence_time)}</td>
    </tr>
    """)

st.markdown(f"""
<div class="tw">
<table class="pl">
    <thead>
        <tr><th>#</th><th>Leg</th><th>Odds</th><th>Fair</th><th>Edge</th><th>Time</th></tr>
    </thead>
    <tbody>
        {"".join(rows)}
    </tbody>
</table>
</div>
""", unsafe_allow_html=True)

# By leg count (skip if legs are fixed)
if not fix_legs and result.by_legs:
    st.markdown("### Best EV at Each Leg Count")
    by_leg_rows = []
    for n in sorted(result.by_legs.keys()):
        p = result.by_legs[n]
        by_leg_rows.append(f"""
        <tr>
            <td class="n">{n} legs</td>
            <td class="n good">{signed_pct(p.ev / stake)}</td>
            <td class="n">{money(p.ev)}</td>
            <td>{one_in(p)}</td>
            <td class="n">{american_or_blank(p.decimal)}</td>
            <td class="n">{money(stake * (p.decimal - 1))}</td>
        </tr>
        """)
    
    st.markdown(f"""
    <div class="tw">
    <table class="pl">
        <thead>
            <tr><th>Legs</th><th>EV %</th><th>EV $</th><th>Hit Rate</th><th>Odds</th><th>To Win</th></tr>
        </thead>
        <tbody>
            {"".join(by_leg_rows)}
        </tbody>
    </table>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")
with st.expander("ℹ️ How this works"):
    st.markdown("""
    **EV parlays.** Straight parlays (no boost), any sport or same-game (SGP).

    - **Multi-sport or SGP:** search across all sports, or stack multiple legs from the same game.
    - **Fix leg count:** set "Legs" to find the best parlay of exactly that length (e.g., best 5-leg).
    - **Fair price:** best price from any book at the given book's line.
    - **Capped at 1.06:** legs with extreme edges (> 6%) are flagged but capped.
    - **Hit rate:** win chance of the whole ticket (product of all leg probabilities).
    - **Kelly:** the leg count that maximizes bankroll growth at your stake size.
    """)
