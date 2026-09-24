"""pages/6_🏁_NASCAR_DFS.py — DK NASCAR cash + GPP lineups, on your phone.

The fourth salary-cap game in this app, and the one least like the other three.
Six drivers, $50,000, no positions at all. No sportsbook is involved: three of
the four things DraftKings scores -- place differential, laps led, fastest laps
-- have no betting market anywhere, so the whole model is built on NASCAR's own
public timing feeds and the page never touches an odds snapshot.

THE THREE THINGS THIS PAGE EXISTS TO SAY OUT LOUD
  1. WHETHER THE GRID IS SET. Before qualifying there is no starting position,
     so there is no place differential -- and place differential is most of the
     scoring for most of a lineup. A pre-qualifying build is real but half of
     it is an estimate, and the banner says so in the first thing you see.
  2. THAT THERE IS NO LATE SWAP. DraftKings does not allow it on NASCAR. What
     is entered at the green flag is what runs, so "check again later" is not
     a strategy here the way it is for an NFL inactive.
  3. WHAT KIND OF TRACK IT IS. A superspeedway and a short track are different
     games: at Daytona, starting position explains 3% of finishing position and
     the front of the grid is the MOST dangerous place to be; at Martinsville
     one car leads half the race. The lineups differ accordingly and the page
     explains why rather than leaving it looking arbitrary.

EVERYTHING COMES FROM edge/dfs_run_nascar.py, WHICH THE CLI ALSO CALLS.
"""
from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="DK NASCAR DFS Lineups", page_icon="🏁",
                   layout="wide", initial_sidebar_state="auto")


# Streamlit Community Cloud pulls new commits and RERUNS this script without
# restarting the Python process, so sys.modules keeps whatever module objects
# an earlier run imported. A deploy that changes an existing function's BODY --
# the ordinary bugfix -- then goes on running the pre-fix code with no error at
# all. The mechanism, the package list and the fingerprint all live in
# edge/dfs_pagereload.py; this is only the st.cache_resource gate, which cannot
# live in edge/ because nothing in edge/ imports streamlit.
#
# THIS USED TO BE THREE DIVERGENT COPIES AND THE DIVERGENCE WAS AN OUTAGE: none
# of them reloaded edge.odds, so the deployed NCAAF page died on
# "unknown profile 'dfs_ncaaf'" while serving the very commit that added it.
from edge.dfs_pagereload import reload_packages, source_fingerprint  # noqa: E402


@st.cache_resource(show_spinner=False)
def _reload_edge(fingerprint: float) -> float:
    reload_packages()
    return fingerprint


_reload_edge(source_fingerprint())

from edge import dfs_run_nascar as R          # noqa: E402

ET = ZoneInfo("America/New_York")

#: One line per track type, shown where the lineups are, because the shape of a
#: correct lineup changes completely between them and every number on the page
#: is downstream of it. All four are measured -- see edge/nascar_sim.py.
TRACK_NOTE = {
    "superspeedway": (
        "Pack racing. Starting position explains just <b>3%</b> of finishing "
        "position here (against 29% at a short track), the leader takes only "
        "27% of the laps, and fastest laps are <b>uncorrelated</b> with "
        "leading (r=0.01). There is no such thing as a dominator at Daytona — "
        "and the front of the grid is the most dangerous place to be, "
        "<b>28%</b> DNF from the first ten rows against 23% from the back."),
    "short": (
        "Track position is everything. One car leads <b>48%</b> of the laps on "
        "average, starting position is the strongest predictor of finish on "
        "the calendar, and DNFs are rare from the front (<b>6.6%</b>). "
        "Dominator points are most of a winning lineup."),
    "road": (
        "Skill and attrition separate the field. The leader takes <b>48%</b> "
        "of the laps and <b>24%</b> of the fastest laps — the highest "
        "fastest-lap concentration of any track type."),
    "intermediate": (
        "The default shape: the leader takes <b>42%</b> of the laps, fastest "
        "laps track leading closely (r=0.77), and starting position matters "
        "but does not dominate."),
}

st.markdown("""
<style>
.block-container {padding-top: 2.0rem; padding-bottom: 2rem;}
h1 {font-size: 1.55rem !important; margin-bottom: .1rem;}
.summary {font-size: 13px; color: #9aa4b2; line-height: 1.55; margin: .1rem 0 .5rem;}
.lu-tot {font-size:13px; color:#c7d0dd; margin:2px 0 6px;}
.lu-note {font-size:12px; color:#9aa4b2; margin:0 0 6px;}
.lu-wrap {overflow-x:auto;}
table.lu {width:100%; border-collapse:collapse; font-size:14px;}
table.lu th {text-align:left; color:#7f8a9c; font-weight:600; font-size:11px;
             text-transform:uppercase; padding:2px 6px;
             border-bottom:1px solid rgba(255,255,255,.16);}
table.lu td {padding:5px 6px; border-bottom:1px solid rgba(255,255,255,.07);}
table.lu td.pos {color:#3fb079; font-weight:700; width:52px;}
table.lu td.nm {white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:170px;}
table.lu td.num {text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
.prov {background:#4a1f00; border:1px solid #a04a00; color:#ffc08a; border-radius:6px;
       padding:8px 11px; font-size:13px; margin:2px 0 10px; line-height:1.5;}
.set  {background:#0e2c1e; border:1px solid #1f7a4d; color:#8fe0b4; border-radius:6px;
       padding:7px 10px; font-size:13px; margin:2px 0 10px;}
.trk  {background:#151b28; border:1px solid #2b3a52; color:#aebdd4; border-radius:6px;
       padding:7px 10px; font-size:12.5px; margin:2px 0 10px; line-height:1.5;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def _slates(_nonce: int):
    from edge import dfs
    return R.classic_groups(dfs.draft_groups(R.DK_SPORT))


@st.cache_data(ttl=300, show_spinner=False)
def _build(gid, sims: int, iters: int, _nonce: int):
    return R.build_slate(draft_group=gid, n_sims=sims, iters=iters)


def _et(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        s = iso.replace("Z", "+00:00")
        if "." in s:
            head, _, tail = s.partition(".")
            s = head + "+00:00" if "+" not in tail else head + "+" + tail.split("+", 1)[1]
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).strftime("%a %-I:%M %p ET")
    except Exception:                                       # noqa: BLE001
        return ""


# ── sidebar ─────────────────────────────────────────────────────────────────
st.session_state.setdefault("nas_nonce", 0)

with st.sidebar:
    st.header("🏁 DK NASCAR DFS")
    st.success("🟢 Free — NASCAR's own timing feeds. No odds API, ever.")
    if st.button("🔄 Refresh (free)", width="stretch",
                 help="Re-pulls DK salaries and NASCAR's entry list, practice "
                      "and qualifying. Tap this after qualifying."):
        st.session_state.nas_nonce += 1
        st.cache_data.clear()
        st.rerun()

    try:
        slates = _slates(st.session_state.nas_nonce)
    except Exception as exc:                                # noqa: BLE001
        slates = []
        st.error(f"DraftKings lobby unreachable: {exc}")

    gid = None
    if slates:
        labels = [f"{s['label']} · {_et(s['start'])}" for s in slates]
        default = max(range(len(slates)),
                      key=lambda i: (slates[i]["label"] == "Cup",))
        choice = st.selectbox("Race", labels, index=default,
                              help="DK Classic slates. Each one is a single "
                                   "race; the label is which series.")
        gid = slates[labels.index(choice)]["gid"]

    sims = st.select_slider(
        "Simulated races", options=[800, 1500, 3000], value=1500,
        help="More simulations make the floor and ceiling steadier and take "
             "longer. Every number on this page is a percentile of these.")
    iters = st.select_slider("Search effort", options=[120, 250, 500], value=250)
    st.caption("No credits, no snapshot, no staleness. This sport has no "
               "betting market to read.")


# ── main ────────────────────────────────────────────────────────────────────
st.title("DK NASCAR DFS Lineups")

if not slates:
    st.warning("No DK NASCAR Classic slates listed right now.")
    st.stop()

with st.spinner("Simulating the race…"):
    try:
        res = _build(gid, sims, iters, st.session_state.nas_nonce)
    except Exception as exc:                                # noqa: BLE001
        st.error(f"Build failed: {exc}")
        st.exception(exc)
        st.stop()

if res.get("unpriced"):
    st.warning("DraftKings lists this slate but has not PRICED it yet — that is "
               "normal a few days out.")
    st.stop()
if res.get("error"):
    st.error(res["error"])
    st.stop()

st_, meta = res["stats"], res["meta"]
st.markdown(
    f"<div class='summary'><b>{st_.get('race')}</b> · {st_.get('track')} · "
    f"{st_.get('laps')} laps · {_et(meta.get('start'))} · draft group {res['gid']}"
    f"<br>{st_.get('entered')} drivers entered, {st_.get('priced')} priced by "
    f"DraftKings</div>", unsafe_allow_html=True)

# 1. Is the grid set? This is the most important thing on the page.
if st_.get("provisional"):
    src = ("their <b>practice rank</b>" if st_.get("practice")
           else "their <b>recent average finish</b>")
    st.markdown(
        "<div class='prov'>⚠️ <b>Provisional grid — qualifying has not run.</b>"
        f"<br>Starting positions below are estimated from {src}. Place "
        "differential is <b>start minus finish</b>, so until the grid is real "
        "so is most of the scoring for most of this lineup."
        "<br><b>Rebuild after qualifying</b> — and note that DraftKings does "
        "<b>not allow late swap</b> on NASCAR, so the lineup you enter is the "
        "lineup that runs.</div>", unsafe_allow_html=True)
else:
    st.markdown(
        "<div class='set'>✅ <b>Grid is set</b> — these are the real qualifying "
        "positions. No late swap on NASCAR: what you enter is what runs.</div>",
        unsafe_allow_html=True)

# 2. What kind of track, and what that changes.
note = TRACK_NOTE.get(st_.get("track_type"))
if note:
    st.markdown(f"<div class='trk'><b>{st_.get('track_type', '').title()}</b> — "
                f"{note}</div>", unsafe_allow_html=True)

if st_.get("not_entered"):
    st.markdown(
        "<div class='lu-note'>Priced by DraftKings but not on NASCAR's entry "
        "list, so dropped: " + ", ".join(st_["not_entered"]) + "</div>",
        unsafe_allow_html=True)


def render(result, mode: str) -> None:
    if not result:
        st.caption("No legal lineup under the cap.")
        return
    headline = ("floor <b>{:.0f}</b>".format(result["floor"]) if mode == "cash"
                else "ceiling <b>{:.0f}</b>".format(result["ceil"]))
    st.markdown(
        f"<div class='lu-tot'>{headline} · proj <b>{result['proj']}</b> · "
        f"sd <b>{result['sd']}</b> · 1-in-100 <b>{result['p99']}</b> · "
        f"worst sim <b>{result['worst']}</b> · own "
        f"<b>{result['own']:.0f}%</b> · <b>${result['salary']:,}</b> / 50k</div>",
        unsafe_allow_html=True)

    body = "".join(
        f"<tr><td class='pos'>P{r['start']}</td>"
        f"<td class='nm'>{r['driver']}</td>"
        f"<td class='num'>{r['salary']:,}</td>"
        f"<td class='num'>{r['proj']}</td>"
        f"<td class='num'>{r['floor']}</td>"
        f"<td class='num'>{r['ceil']}</td>"
        f"<td class='num'>{r['own']:.0f}%</td></tr>" for r in R.lineup_rows(result))
    st.markdown("<div class='lu-wrap'><table class='lu'>"
                "<tr><th>Start</th><th>Driver</th><th>$</th><th>Pts</th>"
                "<th>Floor</th><th>Ceil</th><th>Own</th></tr>"
                f"{body}</table></div>", unsafe_allow_html=True)
    st.caption(
        f"Objective: the {'25th' if mode == 'cash' else '90th'} percentile of "
        f"this lineup's own simulated total, over {sims:,} simulated races. "
        f"Percentiles rather than mean ± k·sd because a NASCAR score is not "
        f"symmetric — a wreck is in the left tail and a dominator in the "
        f"right. Ownership is a PRIOR — never fitted against a real NASCAR "
        f"contest — so read it as a tilt, not a number.")


def _csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "driver", "start", "salary", "proj", "floor", "ceil",
                "own", "leverage"])
    for mode in ("cash", "gpp"):
        for r in R.lineup_rows(res.get(mode)):
            w.writerow([mode, r["driver"], r["start"], r["salary"], r["proj"],
                        r["floor"], r["ceil"], r["own"], r["leverage"]])
    return buf.getvalue().encode()


t_cash, t_gpp, t_board = st.tabs(["💵 CASH", "🚀 GPP", "📋 Board"])
with t_cash:
    render(res.get("cash"), "cash")
with t_gpp:
    render(res.get("gpp"), "gpp")
with t_board:
    pool = sorted(res["pool"], key=lambda d: -(d.get("proj") or 0))
    body = "".join(
        f"<tr><td class='pos'>P{d.get('start', 0)}</td>"
        f"<td class='nm'>{d['name']}</td>"
        f"<td class='num'>{d.get('salary', 0):,}</td>"
        f"<td class='num'>{d['proj']:.1f}</td>"
        f"<td class='num'>{d['floor']:.1f}</td>"
        f"<td class='num'>{d['ceil']:.1f}</td>"
        f"<td class='num'>{(1000.0 * d['proj'] / d['salary']) if d.get('salary') else 0:.2f}</td>"
        f"<td class='num'>{d.get('own', 0):.0f}%</td>"
        f"<td class='num'>{d.get('leverage', 0):+.0f}</td></tr>" for d in pool)
    st.markdown("<div class='lu-wrap'><table class='lu'>"
                "<tr><th>Start</th><th>Driver</th><th>$</th><th>Pts</th>"
                "<th>Floor</th><th>Ceil</th><th>Val</th><th>Own</th>"
                "<th>Lev</th></tr>"
                f"{body}</table></div>", unsafe_allow_html=True)
    st.caption(
        "**Floor** and **Ceil** are that driver's own 25th and 90th simulated "
        "percentiles. **Lev** is projection percentile minus ownership "
        "percentile — GPP signal only. Six drivers cannot all gain twenty "
        "positions and cannot all lead laps, which is why the optimiser scores "
        "whole simulated races rather than adding up six independent "
        "projections.")

st.download_button("⬇️ Download both lineups (CSV)", data=_csv(),
                   file_name=f"nascar_lineups_{res['gid']}.csv", mime="text/csv")
