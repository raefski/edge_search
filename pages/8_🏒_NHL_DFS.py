"""pages/8_🏒_NHL_DFS.py — DK NHL cash + GPP lineups, on your phone.

Everything comes from edge/dfs_run_nhl.py, which the CLI also calls: DraftKings
salaries, DraftKings player props (shots, points, assists, saves), the game
lines, DailyFaceoff's line combinations and starting goalies, and a simulation
of every game on the slate (edge/nhl_sim.py) that scores lineups on percentiles.

THE THINGS THIS PAGE SAYS OUT LOUD
  1. WHICH GOALIES ARE CONFIRMED. The starting goalie is hockey's late news, and
     a lineup built on a projected starter who sits is a zero in the G slot.
  2. THE STACKS. A goal pays a scorer and two linemates together, so every
     lineup shows each skater's line and power-play unit.
  3. WHO IS PRICED AND WHO IS ESTIMATED. Props cover the top of each lineup;
     depth players fall back to last season's rates, flagged in the board.
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

st.set_page_config(page_title="DK NHL DFS Lineups", page_icon="🏒",
                   layout="wide", initial_sidebar_state="auto")

from edge.dfs_pagereload import reload_packages, source_fingerprint  # noqa: E402


@st.cache_resource(show_spinner=False)
def _reload_edge(fingerprint: float) -> float:
    reload_packages()
    return fingerprint


_reload_edge(source_fingerprint())

from edge import dfs_opt_nhl, dfs_run_nhl as R  # noqa: E402

ET = ZoneInfo("America/New_York")

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
table.lu td.pos {color:#3fb079; font-weight:700; width:44px;}
table.lu td.nm {white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:160px;}
table.lu td.num {text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
.warn {background:#4a1f00; border:1px solid #a04a00; color:#ffc08a; border-radius:6px;
       padding:8px 11px; font-size:13px; margin:2px 0 10px; line-height:1.5;}
.ok   {background:#0e2c1e; border:1px solid #1f7a4d; color:#8fe0b4; border-radius:6px;
       padding:7px 10px; font-size:13px; margin:2px 0 10px;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def _slates(_nonce: int):
    from edge import dfs, nhl
    return R.classic_groups(dfs.draft_groups(nhl.DK_SPORT))


@st.cache_data(ttl=300, show_spinner=False)
def _build(gid, sims: int, iters: int, _nonce: int):
    return R.build_slate(draft_group=gid, n_sims=sims, iters=iters)


def _et(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        s = iso.replace("Z", "+00:00").split(".")[0]
        dt = datetime.fromisoformat(s)
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        return dt.astimezone(ET).strftime("%a %-I:%M %p ET")
    except Exception:                                       # noqa: BLE001
        return ""


# ── sidebar ─────────────────────────────────────────────────────────────────
st.session_state.setdefault("nhl_nonce", 0)
with st.sidebar:
    st.header("🏒 DK NHL DFS")
    if st.button("🔄 Refresh", width="stretch",
                 help="Re-pulls DK salaries, DailyFaceoff lines and starting goalies, "
                      "and the latest props snapshot. Tap it after goalies are confirmed."):
        st.session_state.nhl_nonce += 1
        st.cache_data.clear()
        st.rerun()
    try:
        slates = _slates(st.session_state.nhl_nonce)
    except Exception as exc:                                # noqa: BLE001
        slates = []
        st.error(f"DraftKings lobby unreachable: {exc}")
    gid = None
    if slates:
        labels = [f"{s['label']} · {s['games']} games · {_et(s['start'])}" for s in slates]
        default = max(range(len(slates)), key=lambda i: (slates[i]["label"] == "Main",
                                                         slates[i]["games"]))
        choice = st.selectbox("Slate", labels, index=default)
        gid = slates[labels.index(choice)]["gid"]
    sims = st.select_slider("Simulated games", options=[1000, 2000, 4000], value=2000,
                            help="Every number on this page is a percentile of these.")
    iters = st.select_slider("Search effort", options=[60, 150, 300], value=150)
    n_gpp = st.select_slider("GPP lineups", options=[1, 3, 5, 10], value=1)

# ── main ────────────────────────────────────────────────────────────────────
st.title("DK NHL DFS Lineups")
if not slates:
    st.warning("No DK NHL Classic slates listed right now.")
    st.stop()

with st.spinner("Simulating every game on the slate…"):
    try:
        res = _build(gid, sims, iters, st.session_state.nhl_nonce)
    except Exception as exc:                                # noqa: BLE001
        st.error(f"Build failed: {exc}")
        st.exception(exc)
        st.stop()
if res.get("unpriced"):
    st.warning("DraftKings lists this slate but has not priced it yet.")
    st.stop()
if res.get("error"):
    st.error(res["error"])
    st.stop()

info, meta = res["stats"], res["meta"]
imp = info.get("implied") or {}
st.markdown(
    f"<div class='summary'><b>{meta.get('label')}</b> · {info.get('games')} games · "
    f"{_et(meta.get('start'))} · draft group {res['gid']}<br>{info.get('pool')} players "
    f"dressing ({info.get('imputed')} estimated from last season) of {info.get('priced')} "
    f"priced · implied goals: "
    + ", ".join(f"{t} {g:.2f}" for t, g in sorted(imp.items(), key=lambda kv: -kv[1]))
    + "</div>", unsafe_allow_html=True)

# 1. Goalies: the late news that decides a slate.
starters = info.get("starters") or {}
unconfirmed = [f"{t} {v.get('goalie')}" for t, v in sorted(starters.items())
               if t in imp and (v.get("status") or "").lower() != "confirmed"]
if unconfirmed:
    st.markdown("<div class='warn'>⚠️ <b>Unconfirmed goalies:</b> " + ", ".join(unconfirmed)
                + "<br>These are DailyFaceoff's projected starters. Goalies are usually "
                "confirmed 1–2 hours before puck drop — <b>Refresh</b> then, and use late "
                "swap if one changes (DK NHL allows it).</div>", unsafe_allow_html=True)
else:
    st.markdown("<div class='ok'>✅ Every starting goalie on the slate is confirmed.</div>",
                unsafe_allow_html=True)
if info.get("missing_lines"):
    st.markdown("<div class='warn'>No betting line yet for: "
                + ", ".join(info["missing_lines"]) + " — those teams are left out.</div>",
                unsafe_allow_html=True)


def _table(result) -> str:
    body = "".join(
        f"<tr><td class='pos'>{slot}</td><td class='nm'>{p['name']}</td>"
        f"<td>{p['team']}</td><td>{p.get('line') or ''} {p.get('pp') or ''}</td>"
        f"<td class='num'>{p['salary']:,}</td><td class='num'>{p['proj']:.1f}</td>"
        f"<td class='num'>{p['floor']:.0f}</td><td class='num'>{p['ceil']:.0f}</td>"
        f"<td class='num'>{p.get('own', 0):.0f}%</td></tr>" for slot, p in result["slots"])
    return ("<div class='lu-wrap'><table class='lu'><tr><th>Pos</th><th>Player</th>"
            "<th>Tm</th><th>Line</th><th>$</th><th>Pts</th><th>Flr</th><th>Ceil</th>"
            "<th>Own</th></tr>" + body + "</table></div>")


def render(result, mode: str) -> None:
    if not result:
        st.caption("No legal lineup under the cap.")
        return
    head = (f"floor <b>{result['floor']:.0f}</b>" if mode == "cash"
            else f"95th pct <b>{result['ceil']:.0f}</b>")
    stacks = ", ".join(f"{t}×{n}" for t, n in result["stacks"].items() if n >= 2)
    lines = ", ".join(f"{k}×{n}" for k, n in result["line_stacks"].items())
    st.markdown(f"<div class='lu-tot'>{head} · proj <b>{result['proj']}</b> · 1-in-100 "
                f"<b>{result['p99']}</b> · own <b>{result['own']:.0f}%</b> · "
                f"<b>${result['salary']:,}</b> / 50k</div>"
                f"<div class='lu-note'>Stacks: {stacks or 'none'}"
                + (f" · same line: {lines}" if lines else "") + "</div>",
                unsafe_allow_html=True)
    st.markdown(_table(result), unsafe_allow_html=True)


t_cash, t_gpp, t_board = st.tabs(["💵 CASH", "🚀 GPP", "📋 Board"])
with t_cash:
    render(res.get("cash"), "cash")
    st.caption("Objective: the 25th percentile of the lineup's own simulated total — "
               "clear the cash line, don't chase the win. No stacking rule.")
with t_gpp:
    lineups = [res.get("gpp")]
    if n_gpp > 1:
        with st.spinner(f"Building {n_gpp} different GPP lineups…"):
            lineups = dfs_opt_nhl.portfolio(res["pool"], res["sim"], n_gpp, iters=max(40, iters // 3))
    for k, lu in enumerate(lineups, 1):
        if n_gpp > 1:
            st.markdown(f"**Lineup {k}**")
        render(lu, "gpp")
    st.caption("Objective: the 95th percentile of the lineup's simulated total, with two "
               "team stacks of 3+ skaters (the 3-3-2 / 4-3-1 builds) and no skater facing "
               "your goalie. Which lines to stack is the simulation's call: a goal pays "
               "linemates together. Ownership is a PRIOR until the first NHL contest export.")
with t_board:
    pool = sorted(res["pool"], key=lambda d: -(d.get("proj") or 0))
    body = "".join(
        f"<tr><td class='pos'>{d['pos']}</td><td class='nm'>{d['name']}"
        f"{' *' if d.get('imputed') else ''}</td><td>{d['team']}</td>"
        f"<td>{d.get('line') or ''} {d.get('pp') or ''}</td>"
        f"<td class='num'>{d['salary']:,}</td><td class='num'>{d['proj']:.1f}</td>"
        f"<td class='num'>{d['floor']:.0f}</td><td class='num'>{d['ceil']:.0f}</td>"
        f"<td class='num'>{1000.0 * d['proj'] / d['salary']:.2f}</td>"
        f"<td class='num'>{d.get('own', 0):.0f}%</td>"
        f"<td class='num'>{'' if d.get('buzz') is None else d['buzz']}</td></tr>" for d in pool)
    st.markdown("<div class='lu-wrap'><table class='lu'><tr><th>Pos</th><th>Player</th>"
                "<th>Tm</th><th>Line</th><th>$</th><th>Pts</th><th>Flr</th><th>Ceil</th>"
                "<th>Val</th><th>Own</th><th>Buzz</th></tr>" + body + "</table></div>",
                unsafe_allow_html=True)
    st.caption("* = no DraftKings prop for this player; shots/points from last season, "
               "scaled to tonight's implied goals. Blocks have no market anywhere and "
               "always come from last season. **Buzz** = mentions in tonight's public NHL "
               "DFS YouTube videos (scripts/buzz_nhl.py) — the field plays who it is told "
               "to; not yet weighted into Own until NHL contest exports can fit it.")


def _csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "slot", "player", "team", "line", "pp", "salary", "proj", "own"])
    for mode in ("cash", "gpp"):
        for slot, p in (res.get(mode) or {}).get("slots", []):
            w.writerow([mode, slot, p["name"], p["team"], p.get("line"), p.get("pp"),
                        p["salary"], p["proj"], p.get("own")])
    return buf.getvalue().encode()


st.download_button("⬇️ Download lineups (CSV)", data=_csv(),
                   file_name=f"nhl_lineups_{res['gid']}.csv", mime="text/csv")
