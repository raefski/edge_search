"""pages/12_🏀_NBA_DFS.py — DK NBA cash + GPP lineups, on your phone.

Everything comes from edge/dfs_run_nba.py, which the CLI also calls: DraftKings
salaries and OUT flags, NBA.com's starting fives and inactive list, each player's
recent minutes and DK points per minute (data/nba_player_history.json), and the
game lines when the books have posted them.

THE THINGS THIS PAGE SAYS OUT LOUD
  1. WHO STARTS AND WHO SITS. A bench player promoted to starter plays ~8.6 more
     minutes than his recent average while his DK price stays put -- the edge
     Adam built this for. Starters are NBA.com's guess until the team confirms
     them 30 minutes before tip; the page says which, per team.
  2. WHO CHANGED. The board's "Promoted / changed only" filter shows the players
     whose projection moved with tonight's news, green-highlighted, with the
     points they gained. A tap on the phone alert (harness/nba_scratch.py) opens
     the page with that filter on (?promoted=1).
  3. WHERE THE NUMBERS CAME FROM: live or published snapshot, and who has no NBA
     history (projected from his price, starred).
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

st.set_page_config(page_title="DK NBA DFS Lineups", page_icon="🏀",
                   layout="wide", initial_sidebar_state="auto")

from edge.dfs_pagereload import reload_packages, source_fingerprint  # noqa: E402


@st.cache_resource(show_spinner=False)
def _nba_reload_edge(fingerprint: float) -> float:
    reload_packages()
    return fingerprint


_nba_reload_edge(source_fingerprint())

from edge import dfs_run_nba as R  # noqa: E402

ET = ZoneInfo("America/New_York")

st.markdown("""
<style>
.block-container {padding-top: 2.0rem; padding-bottom: 2rem;}
h1 {font-size: 1.55rem !important; margin-bottom: .1rem;}
.summary {font-size: 13px; color: #9aa4b2; line-height: 1.55; margin: .1rem 0 .5rem;}
.lu-tot {font-size:13px; color:#c7d0dd; margin:2px 0 6px;}
.lu-wrap {overflow-x:auto;}
table.lu {width:100%; border-collapse:collapse; font-size:14px;}
table.lu th {text-align:left; color:#7f8a9c; font-weight:600; font-size:11px;
             text-transform:uppercase; padding:2px 6px;
             border-bottom:1px solid rgba(255,255,255,.16);}
table.lu td {padding:5px 6px; border-bottom:1px solid rgba(255,255,255,.07);}
table.lu td.pos {color:#3fb079; font-weight:700; width:44px;}
table.lu td.nm {white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:128px;}
table.lu td.nm small {display:block; color:#7f8a9c; font-size:11px; font-weight:400;}
table.lu td.num {text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
table.lu tr.promo td {background:rgba(31,122,77,.28);}
table.lu tr.promo td.nm {color:#8fe0b4; font-weight:700;}
.warn {background:#4a1f00; border:1px solid #a04a00; color:#ffc08a; border-radius:6px;
       padding:8px 11px; font-size:13px; margin:2px 0 10px; line-height:1.5;}
.ok   {background:#0e2c1e; border:1px solid #1f7a4d; color:#8fe0b4; border-radius:6px;
       padding:7px 10px; font-size:13px; margin:2px 0 10px; line-height:1.5;}
.out  {color:#ff8a8a; text-decoration:line-through;}
.bad  {background:#3a1010; border:1px solid #8a2a2a; color:#ffb0b0; border-radius:6px;
       padding:7px 10px; font-size:13px; margin:2px 0 10px; line-height:1.5;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def _nba_slates(_nonce: int):
    from edge import dfs
    return R.classic_groups(dfs.draft_groups(R.DK_SPORT))


@st.cache_data(ttl=180, show_spinner=False)
def _nba_build(gid, sims: int, iters: int, own_weight: float, _nonce: int):
    return R.build_slate(draft_group=gid, n_sims=sims, iters=iters, own_weight=own_weight)


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
st.session_state.setdefault("nba_nonce", 0)
with st.sidebar:
    st.header("🏀 DK NBA DFS")
    if st.button("🔄 Refresh", width="stretch",
                 help="Re-pulls DK salaries and OUT flags and NBA.com's starters and "
                      "inactives. Tap it ~30 minutes before tip, when teams confirm."):
        st.session_state.nba_nonce += 1
        st.cache_data.clear()
        st.rerun()
    try:
        slates = _nba_slates(st.session_state.nba_nonce)
    except Exception as exc:                                # noqa: BLE001
        slates = []
        st.error(f"DraftKings lobby unreachable: {exc}")
    gid = None
    if slates:
        labels = [f"{s['label']} · {s['games']} games · {_et(s['start'])}" for s in slates]
        default = next((i for i, s in enumerate(slates) if s["label"] == "Main"), 0)
        choice = st.selectbox("Slate", labels, index=default)
        gid = slates[labels.index(choice)]["gid"]
    sims = st.select_slider("Simulations", options=[1000, 3000, 6000], value=3000,
                            help="Every floor and ceiling on this page is a percentile of these.")
    iters = st.select_slider("Search effort", options=[80, 200, 400], value=200)
    own_weight = st.select_slider("GPP ownership fade", options=[0.0, 2.0, 5.0, 10.0], value=0.0,
                                  help="Points of ceiling given up per 100% of total ownership. "
                                       "The NBA ownership model is a prior until it is fitted.")

# ── main ────────────────────────────────────────────────────────────────────
st.title("DK NBA DFS Lineups")
if not slates:
    st.warning("No DK NBA Classic slates listed right now.")
    st.stop()

with st.spinner("Projecting minutes and simulating the slate…"):
    try:
        res = _nba_build(gid, sims, iters, own_weight, st.session_state.nba_nonce)
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

info, meta, pool = res["stats"], res["meta"], res["pool"]
imp = info.get("implied") or {}
src = info.get("lineups_source") or "none"
st.markdown(
    f"<div class='summary'><b>{meta.get('label')}</b> · {info.get('games')} games · "
    f"{_et(meta.get('start'))} · draft group {res['gid']}<br>{info.get('pool')} players "
    f"projected of {info.get('priced')} priced · {len(info.get('imputed') or [])} with no NBA "
    f"history (*) · history through {info.get('history_date')}<br>"
    f"salaries: {info.get('draftables_source')} · NBA.com lineups: {src} · "
    + ("implied points: " + ", ".join(f"{t} {v:.0f}" for t, v in sorted(imp.items(), key=lambda kv: -kv[1]))
       if imp else "no game lines yet (projections use season pace)")
    + "</div>", unsafe_allow_html=True)

if info.get("preseason"):
    st.markdown("<div class='warn'>🧪 <b>Preseason slate.</b> Stars often play 15–25 minutes "
                "and coaches test rotations; these projections assume regular-season roles. "
                "Treat tonight as a test of the app, not a model of the games.</div>",
                unsafe_allow_html=True)

# 1. The news: who is confirmed, who is out, who is promoted.
teams = sorted({p["team"] for p in pool})
status = {t: next((p["lineup"] for p in pool if p["team"] == t and p.get("lineup")), "") for t in teams}
pending = [t for t in teams if status[t] != "Confirmed"]
promoted = sorted((p for p in pool if p.get("promoted")), key=lambda p: -p.get("gain", 0))
outs = info.get("out") or []
if pending:
    exp = [t for t in pending if status[t] == "Expected"]
    none = [t for t in pending if not status[t]]
    st.markdown("<div class='warn'>⏳ <b>Starters not confirmed:</b> "
                + (f"expected (NBA.com's guess) {', '.join(exp)}" if exp else "")
                + ("; " if exp and none else "") + (f"not posted {', '.join(none)}" if none else "")
                + "<br>Teams confirm 30 minutes before tip — <b>Refresh</b> then. A bench player "
                "who starts plays ~8.6 more minutes than usual.</div>", unsafe_allow_html=True)
else:
    st.markdown("<div class='ok'>✅ Every starting five on the slate is confirmed.</div>",
                unsafe_allow_html=True)
if promoted:
    st.markdown("<div class='ok'>🟢 <b>Promoted:</b> " + ", ".join(
        f"{p['name']} ({p['team']}, ${p['salary']:,}, {p.get('base_min', 0):.0f}→{p['minutes']:.0f} min, "
        f"+{p.get('gain', 0):.1f} pts)" for p in promoted) + "</div>", unsafe_allow_html=True)
if outs:
    st.markdown("<div class='bad'>🔴 <b>Out:</b> " + ", ".join(f"<span class='out'>{n}</span>" for n in outs)
                + "</div>", unsafe_allow_html=True)


def _k(salary) -> str:
    return f"{salary / 1000:.1f}k"


def _who(p) -> str:
    """Name, then team / DK positions / starter on a small second line: three columns
    that pushed Own off a 390px screen."""
    tag = "🟢 " if p.get("promoted") else ""
    star = " *" if p.get("imputed") else ""
    sub = " · ".join(x for x in (p["team"], p.get("dk_pos") or "",
                                 "starts" if p.get("starter") else "") if x)
    return f"<td class='nm'>{tag}{p['name']}{star}<small>{sub}</small></td>"


def _row(slot, p, cls="") -> str:
    return (f"<tr class='{cls}'><td class='pos'>{slot}</td>{_who(p)}"
            f"<td class='num'>{_k(p['salary'])}</td>"
            f"<td class='num'>{p.get('minutes', 0):.0f}</td><td class='num'>{p['proj']:.1f}</td>"
            f"<td class='num'>{p.get('own', 0):.0f}%</td></tr>")


# Per-player floor and ceiling pushed Own off a 390px screen; the lineup's floor and
# ceiling are in the line above the table.
HEAD = ("<div class='lu-wrap'><table class='lu'><tr><th>Pos</th><th>Player</th>"
        "<th>$</th><th>Min</th><th>Pts</th><th>Own</th></tr>")


def render(result, mode: str) -> None:
    if not result:
        st.caption("No legal lineup under the cap.")
        return
    head = (f"floor <b>{result['floor']:.0f}</b>" if mode == "cash"
            else f"90th pct <b>{result['ceil']:.0f}</b>")
    st.markdown(f"<div class='lu-tot'>{head} · proj <b>{result['proj']}</b> · 1-in-100 "
                f"<b>{result['p99']}</b> · own <b>{result['own']:.0f}%</b> · "
                f"<b>${result['salary']:,}</b> / 50k</div>", unsafe_allow_html=True)
    st.markdown(HEAD + "".join(_row(slot, p, "promo" if p.get("promoted") else "")
                               for slot, p in result["slots"]) + "</table></div>",
                unsafe_allow_html=True)


t_cash, t_gpp, t_board = st.tabs(["💵 CASH", "🚀 GPP", "📋 Board"])
with t_cash:
    render(res.get("cash"), "cash")
    st.caption("Objective: the 30th percentile of the lineup's simulated total (a double-up "
               "cash line). Minutes x DK points per minute, the minutes re-shared for "
               "tonight's starters and inactives.")
with t_gpp:
    render(res.get("gpp"), "gpp")
    st.caption("Objective: the 90th percentile, minus the ownership fade if set. No stacking "
               "rule: in NBA what is left after minutes barely co-moves (teammates -0.04, "
               "opponents 0.00 over 2025-26). Ownership is a PRIOR until fitted.")
with t_board:
    params = st.query_params
    default_only = str(params.get("promoted", "")).lower() in ("1", "true", "yes")
    only = st.toggle("Promoted / changed only", value=default_only, key="nba_promo_only",
                     help="Players whose projection moved with tonight's news: promoted "
                          "starters first, then the teammates who absorb scratched minutes.")
    rows = sorted(pool, key=lambda d: -(d.get("proj") or 0))
    if only:
        rows = sorted((d for d in pool if d.get("promoted") or d.get("gain", 0) >= 2.0),
                      key=lambda d: (not d.get("promoted"), -d.get("gain", 0) * 1000 / d["salary"]))
        if not rows:
            st.caption("Nothing has changed yet: no promoted starters, nobody gaining 2+ points.")
    body = "".join(
        f"<tr class='{'promo' if d.get('promoted') else ''}'>{_who(d)}"
        f"<td class='num'>{_k(d['salary'])}</td><td class='num'>{d.get('minutes', 0):.0f}</td>"
        f"<td class='num'>{d['proj']:.1f}</td>"
        f"<td class='num'>{'+' if d.get('gain', 0) > 0 else ''}{d.get('gain', 0):.1f}</td>"
        f"<td class='num'>{d['value']:.1f}</td><td class='num'>{d.get('own', 0):.0f}%</td></tr>"
        for d in rows)
    st.markdown("<div class='lu-wrap'><table class='lu'><tr><th>Player</th><th>$</th><th>Min</th>"
                "<th>Pts</th><th>Δ</th><th>Val</th><th>Own</th></tr>"
                + body + "</table></div>", unsafe_allow_html=True)
    st.caption("“starts” = in tonight's starting five. Δ = points vs his usual role (minutes the news "
               "added or took away). Val = points per \\$1,000. * = no NBA history: projected "
               f"from his price ({R.IMPUTED_PER_K} pts per \\$1k).")


def _csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "slot", "player", "team", "salary", "minutes", "proj", "own"])
    for mode, lu in (("cash", res.get("cash")), ("gpp", res.get("gpp"))):
        for slot, p in (lu or {}).get("slots") or []:
            w.writerow([mode, slot, p["name"], p["team"], p["salary"], p.get("minutes"),
                        p["proj"], p.get("own")])
    return buf.getvalue().encode()


st.download_button("⬇️ Download lineups (CSV)", data=_csv(),
                   file_name=f"nba_lineups_{res['gid']}.csv", mime="text/csv")
