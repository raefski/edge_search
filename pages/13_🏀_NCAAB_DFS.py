"""pages/13_🏀_NCAAB_DFS.py — DK College Basketball cash + GPP lineups.

edge/dfs_run_ncaab.py builds the board (roster G G G F F F UTIL UTIL). Most
college players have no prop, so most projections are DK's FPPG scaled to
tonight's implied team points -- the board marks every player's source.

THE THINGS THIS PAGE SAYS OUT LOUD
  1. WHO IS OUT AND WHO IS QUESTIONABLE, at the top. A lineup with a
     scratched player in it is a zero.
  2. WHERE EVERY PROJECTION CAME FROM: props, DK's FPPG, or salary. A fallback
     projection does not move when a teammate is ruled out; a prop does once
     the books re-post. The summary counts each.
  3. WHICH GAMES HAVE NO LINE (simulated at the league average) and how old
     the props snapshot is.
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

st.set_page_config(page_title="DK College Basketball DFS", page_icon="🏀",
                   layout="wide", initial_sidebar_state="auto")

from edge.pagereload import fresh  # noqa: E402

fresh()   # a Cloud deploy reloads every repo module (edge/pagereload.py)

from edge import dfs_opt_ncaab, dfs_run_ncaab as R  # noqa: E402


@st.cache_data(ttl=300, show_spinner=False)
def _cbb_slates(_nonce: int):
    from edge import dfs, ncaab
    return R.classic_groups(dfs.draft_groups(ncaab.DK_SPORT))


@st.cache_data(ttl=300, show_spinner=False)
def _cbb_build(gid, sims: int, iters: int, _nonce: int):
    return R.build_slate(draft_group=gid, n_sims=sims, iters=iters)


@st.cache_data(ttl=600, show_spinner=False)
def _cbb_leverage(gid, sims: int, iters: int, _nonce: int):
    from edge import dfs_leverage_sports as LS
    return LS.hoops(_cbb_build(gid, sims, iters, _nonce), None, iters=max(20, iters // 3))


@st.cache_data(ttl=600, show_spinner=False)
def _cbb_leverage_for(gid, sims: int, iters: int, _nonce: int, contest: dict):
    from edge import dfs_leverage as lev
    return lev.with_contest(_cbb_leverage(gid, sims, iters, _nonce),
                            _cbb_build(gid, sims, iters, _nonce)["sim"], contest)


def _portfolio(res, n, iters):
    return dfs_opt_ncaab.portfolio(res["pool"], res["sim"], n, iters=iters)



BOARD_NOTE = ("Source marks: none = props; ·f = DK's FPPG scaled to tonight's implied "
                  "team points; ·$ = no FPPG yet (early season, or a new rotation player), "
                  "projected off DK's own salary at 4.6 points per $1K — a prior. ⚠️ = DK "
                  "lists him questionable. A min-salary player with no FPPG is left off as "
                  "a probable DNP.")

ET = ZoneInfo("America/New_York")

CSS = """
<style>
.block-container {padding-top: 2.0rem; padding-bottom: 2rem;}
h1 {font-size: 1.55rem !important; margin-bottom: .1rem;}
.summary {font-size: 13px; color: #9aa4b2; line-height: 1.55; margin: .1rem 0 .5rem;}
.lu-tot {font-size:13px; color:#c7d0dd; margin:2px 0 6px;}
.lu-note {font-size:12px; color:#9aa4b2; margin:0 0 6px;}
.lu-wrap {overflow-x:auto;}
table.lu {width:100%; border-collapse:collapse; font-size:13px;}
table.lu th {text-align:left; color:#7f8a9c; font-weight:600; font-size:11px;
             text-transform:uppercase; padding:2px 3px;
             border-bottom:1px solid rgba(255,255,255,.16);}
table.lu td {padding:5px 3px; border-bottom:1px solid rgba(255,255,255,.07);}
table.lu td.pos {color:#3fb079; font-weight:700; width:44px;}
table.lu td.nm div {white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
                    max-width:92px;}
table.lu td.tip {white-space:nowrap; color:#9aa4b2;}
table.lu td.num {text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
.warn {background:#4a1f00; border:1px solid #a04a00; color:#ffc08a; border-radius:6px;
       padding:8px 11px; font-size:13px; margin:2px 0 10px; line-height:1.5;}
.ok   {background:#0e2c1e; border:1px solid #1f7a4d; color:#8fe0b4; border-radius:6px;
       padding:7px 10px; font-size:13px; margin:2px 0 10px;}
</style>
"""

SOURCE_MARK = {"props": "", "fppg": " ·f", "salary": " ·$"}


def et(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        s = str(iso).replace("Z", "+00:00").split(".")[0]
        dt = datetime.fromisoformat(s)
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        return dt.astimezone(ET).strftime("%a %-I:%M %p ET")
    except Exception:                                       # noqa: BLE001
        return ""


def _tip(iso) -> str:
    """'7p' / '9:30p': a full '7:00 PM' wrapped onto two lines at 390px and
    doubled every row's height (Playwright, iPhone width, 2026-10-13)."""
    s = et(iso)
    if not s:
        return ""
    hm, ampm = s.split(" ")[1], s.split(" ")[2]
    return hm.replace(":00", "") + ampm[0].lower()


def _name(p) -> str:
    flag = " ⚠️" if p.get("status") else ""
    return f"{p['name']}{SOURCE_MARK.get(p.get('source'), '')}{flag}"


def table(result) -> str:
    body = "".join(
        f"<tr><td class='pos'>{slot}</td><td class='nm'><div>{_name(p)}</div></td>"
        f"<td>{p['team']}</td><td class='tip'>{_tip(p.get('start'))}</td>"
        f"<td class='num'>{p['salary']:,}</td><td class='num'>{p['proj']:.1f}</td>"
        f"<td class='num'>{p['floor']:.0f}</td><td class='num'>{p['ceil']:.0f}</td>"
        f"<td class='num'>{p.get('own', 0):.0f}%</td></tr>" for slot, p in result["slots"])
    return ("<div class='lu-wrap'><table class='lu'><tr><th>Pos</th><th>Player</th>"
            "<th>Tm</th><th>Tip</th><th>$</th><th>Pts</th><th>Flr</th><th>Ceil</th>"
            "<th>Own</th></tr>" + body + "</table></div>")


def render(result, mode: str, cap: int = 50000) -> None:
    if not result:
        st.caption("No legal lineup under the cap.")
        return
    head = (f"floor <b>{result['floor']:.0f}</b>" if mode == "cash"
            else f"90th pct <b>{result['ceil']:.0f}</b>")
    stacks = ", ".join(f"{g}×{n}" for g, n in result.get("game_stacks", {}).items())
    st.markdown(f"<div class='lu-tot'>{head} · proj <b>{result['proj']}</b> · 1-in-100 "
                f"<b>{result['p99']}</b> · own <b>{result['own']:.0f}%</b> · "
                f"<b>${result['salary']:,}</b> / {cap // 1000}k</div>"
                f"<div class='lu-note'>Same game: {stacks or 'none'}</div>",
                unsafe_allow_html=True)
    st.markdown(table(result), unsafe_allow_html=True)


def summary(res: dict, sport_label: str) -> None:
    info, meta = res["stats"], res["meta"]
    imp = info.get("implied") or {}
    src = info.get("sources") or {}
    top = sorted(imp.items(), key=lambda kv: -kv[1])[:6]
    odds = (f"props: published snapshot, {info['odds_age_min']} min old"
            if info.get("odds_age_min") is not None else "props: live local store")
    books = ", ".join(f"{b} {n}" for b, n in (info.get("books") or {}).items()) or "none"
    st.markdown(
        f"<div class='summary'><b>{meta.get('label')}</b> · {info.get('games')} games · "
        f"{et(meta.get('start'))} · draft group {res['gid']}<br>{info.get('pool')} "
        f"{sport_label} players projected of {info.get('priced')} priced — "
        f"<b>{src.get('props', 0)}</b> from props, <b>{src.get('fppg', 0)}</b> from DK FPPG"
        + (f", <b>{src.get('salary', 0)}</b> from salary" if src.get("salary") else "")
        + f"<br>implied points: " + ", ".join(f"{t} {g:.1f}" for t, g in top)
        + (" …" if len(imp) > len(top) else "")
        + f"<br>{odds} · books: {books} · salaries: {info.get('draftables_source', 'live')}"
        + "</div>", unsafe_allow_html=True)
    if info.get("out") or info.get("doubtful"):
        msg = []
        if info.get("out"):
            msg.append("<b>Out (dropped):</b> " + ", ".join(info["out"]))
        if info.get("doubtful"):
            msg.append("<b>Questionable (kept, ⚠️ in lineups):</b> " + ", ".join(info["doubtful"]))
        st.markdown("<div class='warn'>🩹 " + "<br>".join(msg) + "<br>A teammate's prop moves "
                    "once the books re-post after a scratch; an FPPG projection never does. "
                    "<b>Refresh</b> after news, and use late swap.</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown("<div class='ok'>✅ DraftKings lists nobody on this slate as out or "
                    "questionable.</div>", unsafe_allow_html=True)
    if info.get("missing_lines"):
        st.markdown("<div class='warn'>No betting line matched for: "
                    + ", ".join(info["missing_lines"]) + " — simulated at the league "
                    "average, and FPPG players there are not scaled to tonight.</div>",
                    unsafe_allow_html=True)


def board(res: dict, note: str) -> None:
    pool = sorted(res["pool"], key=lambda d: -(d.get("proj") or 0))
    body = "".join(
        f"<tr><td class='pos'>{d['dk_pos']}</td><td class='nm'><div>{_name(d)}</div></td>"
        f"<td>{d['team']}</td><td class='num'>{d['salary']:,}</td>"
        f"<td class='num'>{d['proj']:.1f}</td><td class='num'>{d['floor']:.0f}</td>"
        f"<td class='num'>{d['ceil']:.0f}</td>"
        f"<td class='num'>{1000.0 * d['proj'] / d['salary']:.2f}</td>"
        f"<td class='num'>{d.get('own', 0):.0f}%</td></tr>" for d in pool)
    st.markdown("<div class='lu-wrap'><table class='lu'><tr><th>Pos</th><th>Player</th>"
                "<th>Tm</th><th>$</th><th>Pts</th><th>Flr</th><th>Ceil</th><th>Val</th>"
                "<th>Own</th></tr>" + body + "</table></div>", unsafe_allow_html=True)
    st.caption(note)


def lineups_csv(res: dict, gpp_pick) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "slot", "player", "team", "salary", "proj", "own", "source"])
    for mode, lu in (("cash", res.get("cash")), ("gpp", gpp_pick)):
        for slot, p in (lu or {}).get("slots", []):
            w.writerow([mode, slot, p["name"], p["team"], p["salary"], p["proj"],
                        p.get("own"), p.get("source")])
    return buf.getvalue().encode()


st.markdown(CSS, unsafe_allow_html=True)
nonce_key = "cbb_nonce"
st.session_state.setdefault(nonce_key, 0)
with st.sidebar:
    st.header("🏀 NCAAB DFS")
    if st.button("🔄 Refresh", width="stretch", key="cbb_refresh",
                 help="Re-pulls DK salaries + injury status and the latest props "
                      "snapshot. Tap it after injury news."):
        st.session_state[nonce_key] += 1
        st.cache_data.clear()
        st.rerun()
    try:
        slates = _cbb_slates(st.session_state[nonce_key])
    except Exception as exc:                            # noqa: BLE001
        slates = []
        st.error(f"DraftKings lobby unreachable: {exc}")
    gid = None
    if slates:
        labels = [f"{s['label']} · {s['games']} games · {et(s['start'])}" for s in slates]
        choice = st.selectbox("Slate", labels, index=R.default_slate(slates), key="cbb_slate")
        gid = slates[labels.index(choice)]["gid"]
    sims = st.select_slider("Simulated games", options=[1000, 2000, 4000], value=2000,
                            key="cbb_sims",
                            help="Every number on this page is a percentile of these.")
    iters = st.select_slider("Search effort", options=[40, 100, 200], value=100,
                             key="cbb_iters")
    n_gpp = st.select_slider("GPP lineups", options=[1, 3, 5, 10], value=1,
                             key="cbb_ngpp")

st.title("DK NCAAB DFS Lineups")
if not slates:
    st.warning("No DK college basketball Classic slates listed right now.")
    st.stop()
if any(s.get("unverified_type") for s in slates):
    st.caption("DraftKings' lobby named no Classic game type, so every multi-game "
               "slate is listed — check the label before entering.")
nonce = st.session_state[nonce_key]
with st.spinner("Simulating every game on the slate…"):
    try:
        res = _cbb_build(gid, sims, iters, nonce)
    except Exception as exc:                            # noqa: BLE001
        st.error(f"Build failed: {exc}")
        st.exception(exc)
        st.stop()
if res.get("unpriced"):
    st.warning("DraftKings lists this slate but has not priced it yet.")
    st.stop()
if res.get("error"):
    st.error(res["error"])
    st.stop()

summary(res, "college")
t_cash, t_gpp, t_board = st.tabs(["💵 CASH", "🚀 GPP", "📋 Board"])
with t_cash:
    render(res.get("cash"), "cash")
    st.caption("Objective: the 25th percentile of the lineup's simulated total — the "
               "highest floor. No stacking rule: teammates share one team's points, so "
               "the simulation already pulls cash away from piling into one team.")
gpp_pick = res.get("gpp")
with t_gpp:
    from edge import dfs_leverage_ui as LU
    contest = LU.contest_picker("NCAAB", res["gid"], key="cbb")
    lev_on = contest is not None or st.toggle(
        "Chalk ↔ contrarian map", key="cbb_lev_on",
        help="Every GPP lineup from the chalkiest to the most contrarian, on a chart of "
             "ownership against points, beside a simulated field.")
    if lev_on:
        with st.spinner("Building lineups from chalk to contrarian…"):
            an = (_cbb_leverage_for(gid, sims, iters, nonce, contest) if contest
                  else _cbb_leverage(gid, sims, iters, nonce))
        chosen = LU.leverage_view(an, key="cbb")
        if chosen:
            gpp_pick = chosen["result"]
            render(gpp_pick, "gpp")
    else:
        lineups = [res.get("gpp")]
        if n_gpp > 1:
            with st.spinner(f"Building {n_gpp} different GPP lineups…"):
                lineups = _portfolio(res, n_gpp, max(20, iters // 3))
        for k, lu in enumerate(lineups, 1):
            if n_gpp > 1:
                st.markdown(f"**Lineup {k}**")
            render(lu, "gpp")
    st.caption("Objective: the 90th percentile of the simulated total. Pace is shared by "
               "both teams in a game, so the search walks into high totals by itself; a "
               "blowout costs both sides' starters minutes. Ownership is a PRIOR until "
               "the first contest export is fitted.")
with t_board:
    board(res, BOARD_NOTE)
st.download_button("⬇️ Download lineups (CSV)", data=lineups_csv(res, gpp_pick),
                   file_name=f"cbb_lineups_{res['gid']}.csv", mime="text/csv",
                   key="cbb_dl")
