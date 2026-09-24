"""pages/3_🏈_NCAAF_DFS.py — DK College Football cash + GPP lineups, on your phone.

The college half of the DFS app. `app.py` is MLB, `2_🏈_NFL_DFS.py` is the NFL,
and this is a third salary-cap game rather than a variant of the second: DK's
college roster has no defence and no tight end, it has an S-FLEX that accepts a
quarterback, and DraftKings prices college players with one-sided milestone
LADDERS instead of two-sided Over/Under lines.

EVERYTHING COMES FROM edge/dfs_run_ncaaf.py, WHICH THE CLI ALSO CALLS
No pool-building, no slate resolution and no optimizer settings live in this
file, so a lineup here and a lineup from scripts/dfs_lineups_ncaaf.py are the
same lineup. Two copies of a shared core drifting is a failure this repo has
already had once (ODDS_LAYER.md).

WHAT THE PAGE HAS TO EXPLAIN THAT THE NFL PAGE DOES NOT
Three things about a college lineup look like bugs and are not, so the page
says so where they appear rather than in a document nobody opens:

  * a CASH lineup usually starts TWO quarterbacks, from different teams;
  * a GPP lineup usually starts a quarterback, THREE of his own receivers, and
    the opposing quarterback;
  * every projection carries a `band` -- how many DK points of it came from the
    unpriced head of a ladder rather than from a posted price.

PRICES ARE FREE AND THE PAGE SAYS SO
Ladders come from the scraped store on the desktop and from the committed
snapshot (`data/odds_snapshot_dfs_ncaaf.json`) on Streamlit Cloud, which cannot
scrape. DK salaries come from DraftKings' own draftables endpoint.
"""
from __future__ import annotations

import csv
import importlib
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="DK NCAAF DFS Lineups", page_icon="🏈",
                   layout="wide", initial_sidebar_state="auto")


# Streamlit Community Cloud pulls new commits and RERUNS this script without
# restarting the Python process, so sys.modules keeps whatever module objects
# an earlier run imported. A deploy that changes an existing function's BODY --
# the ordinary bugfix -- then goes on running the pre-fix code with no error at
# all. Same guard, same reason, as pages/2_🏈_NFL_DFS.py.
def _dfs_fingerprint() -> float:
    try:
        return max(p.stat().st_mtime for p in (ROOT / "edge").glob("dfs*.py"))
    except ValueError:
        return 0.0


@st.cache_resource(show_spinner=False)
def _reload_dfs(fingerprint: float) -> float:
    for _pass in range(2):
        for name in sorted(k for k in sys.modules
                           if k.startswith("edge.dfs") or k == "edge.ncaaf"):
            try:
                importlib.reload(sys.modules[name])
            except Exception:                               # noqa: BLE001
                pass
    return fingerprint


_reload_dfs(_dfs_fingerprint())

from edge import dfs_run_ncaaf as cfb          # noqa: E402
from edge.ncaaf import base_position           # noqa: E402
from edge.odds.cli import scraped_client       # noqa: E402

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
table.lu td.pos {color:#3fb079; font-weight:700; width:56px;}
table.lu td.team {color:#9aa4b2; width:74px; font-size:12px;}
table.lu td.nm {white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:150px;}
table.lu td.num {text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
.stk {background:#0e2c1e; border:1px solid #1f7a4d; color:#8fe0b4; border-radius:6px;
      padding:5px 9px; font-size:12.5px; margin:2px 0 8px;}
.qb2 {background:#12243d; border:1px solid #2b5c99; color:#9ecbff; border-radius:6px;
      padding:5px 9px; font-size:12.5px; margin:2px 0 8px;}
.warn {background:#4a3a00; border:1px solid #8a6a00; color:#ffd97a; border-radius:6px;
       padding:6px 10px; font-size:13px; margin:2px 0 8px;}
</style>
""", unsafe_allow_html=True)


# ── data ────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _slates(_nonce: int):
    from edge import dfs
    return cfb.classic_groups(dfs.draft_groups(cfb.DK_SPORT))


@st.cache_data(ttl=300, show_spinner=False)
def _build(gid, iters: int, stack_n: int, _nonce: int):
    client = scraped_client(cfb.SPORT, "dfs")
    return cfb.build_slate(client, draft_group=gid, iters=iters, stack_n=stack_n)


def _source_badge():
    """Say where the prices actually came from, every time."""
    try:
        client = scraped_client(cfb.SPORT, "dfs")
    except Exception as exc:                                # noqa: BLE001
        st.sidebar.error(f"No free price source: {exc}")
        return
    kind = type(client).__name__
    if kind == "ScrapedOddsClient":
        st.sidebar.success("🟢 Free scraped ladders (local store)")
    elif kind == "SnapshotOddsClient":
        mins = client.age_seconds / 60.0
        st.sidebar.success(f"🟢 Free scraped ladders · {mins:.0f} min old")
        if mins > 180:
            st.sidebar.caption(
                "Getting stale. On the desktop: `python3 scripts/odds_collect.py "
                "--profile dfs_ncaaf --push`.")
    else:
        st.sidebar.warning("🟡 Falling back to the paid Odds API — and it cannot "
                           "serve ladders, so the board will be empty.")


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
st.session_state.setdefault("cfb_nonce", 0)

with st.sidebar:
    st.header("🏈 DK NCAAF DFS")
    _source_badge()
    if st.button("🔄 Refresh (free)", width="stretch",
                 help="Re-pulls DK salaries and the latest scraped ladders. "
                      "0 Odds-API credits."):
        st.session_state.cfb_nonce += 1
        st.cache_data.clear()
        st.rerun()

    try:
        slates = _slates(st.session_state.cfb_nonce)
    except Exception as exc:                                # noqa: BLE001
        slates = []
        st.error(f"DraftKings lobby unreachable: {exc}")

    gid = None
    if slates:
        labels = [f"{s['label']} · {s['games']}g · {_et(s['start'])}" for s in slates]
        default = max(range(len(slates)),
                      key=lambda i: (slates[i]["label"] == "Main",
                                     slates[i]["games"]))
        choice = st.selectbox("Slate", labels, index=default,
                              help="DK Classic slates only. 'Main' is the one "
                                   "the big tournaments and the deepest cash "
                                   "games run on.")
        gid = slates[labels.index(choice)]["gid"]

    iters = st.select_slider(
        "Search effort", options=[150, 350, 700], value=350,
        help="Higher finds slightly better lineups and takes longer.")
    stack_n = st.select_slider(
        "GPP stack size", options=[2, 3, 4], value=3,
        help="Pass-catchers rostered with the GPP quarterback. 3 is the "
             "measured default: college team-mate receivers are POSITIVELY "
             "correlated (+0.048) where NFL ones are negative (−0.029), so a "
             "college stack compounds.")
    st.caption("Prices are free. This page never spends a credit.")


# ── main ────────────────────────────────────────────────────────────────────
st.title("DK NCAAF DFS Lineups")

if not slates:
    st.warning("No DK College Football Classic slates listed right now.")
    st.stop()

with st.spinner("Building cash + GPP lineups…"):
    try:
        res = _build(gid, iters, stack_n, st.session_state.cfb_nonce)
    except Exception as exc:                                # noqa: BLE001
        st.error(f"Build failed: {exc}")
        st.exception(exc)
        st.stop()

if res.get("error"):
    st.error(res["error"])
    st.stop()
if res.get("unpriced"):
    st.warning("DraftKings lists this slate but has not PRICED it yet — that is "
               "normal a few days out. Try again closer to kickoff.")
    st.stop()

meta, stats = res["meta"], res["stats"]
st.markdown(
    f"<div class='summary'><b>{meta['label']}</b> slate · {meta['games']} games · "
    f"{_et(meta.get('start'))} · draft group {res['gid']}<br>"
    f"pool: <b>{stats['projected']}</b> players carry a DraftKings ladder, of "
    f"{stats['slate_players']} on the slate</div>", unsafe_allow_html=True)

# THE POOL IS A SMALL SLICE OF THE SLATE, AND THAT IS STRUCTURAL.
# DraftKings prices ~110 of a 12-game slate's ~860 players with any prop at
# all. Everyone else is unprojectable here -- not badly projected, absent. A
# cheap high-volume backup can be a real play and this model cannot see him.
if stats["slate_players"] and stats["projected"] < 0.25 * stats["slate_players"]:
    st.markdown(
        f"<div class='lu-note'>Only <b>{stats['projected']}</b> of "
        f"{stats['slate_players']} slate players carry a posted ladder, so the "
        f"rest are <b>absent from the pool</b> rather than badly projected. "
        f"College props cover the players books take action on; a cheap backup "
        f"getting a full workload is a real play this model cannot see.</div>",
        unsafe_allow_html=True)

if stats.get("one_rung"):
    st.markdown(
        f"<div class='warn'>{stats['one_rung']} players came back with a single "
        "rung per market. That is the signature of an odds client built with "
        "<code>main_line_only</code> left ON — the ladders have been collapsed "
        "and these projections are meaningless. See edge/dfs_run_ncaaf.py.</div>",
        unsafe_allow_html=True)

_age = None
_is_snapshot = False
try:
    _c = scraped_client(cfb.SPORT, "dfs")
    _age = getattr(_c, "age_seconds", 0.0) / 60.0
    _is_snapshot = type(_c).__name__ == "SnapshotOddsClient"
except Exception:                                           # noqa: BLE001
    pass
if _age is not None and _age > 180:
    # College's staleness failure is the OPPOSITE of the NFL's. There are no
    # 90-minute inactive reports to miss; what happens instead is that
    # DraftKings keeps ADDING ladders through Friday night, so an old scan is
    # a thin pool rather than a wrong one. Say which it is.
    msg = ("⏱️ These ladders are <b>{:.0f} minutes old</b>. DraftKings keeps "
           "adding college props through the night before kickoff, so an old "
           "scan means a <b>thinner pool</b>, not wrong numbers.").format(_age)
    if _is_snapshot:
        msg += (" This page reads a snapshot pushed from the desktop — 🔄 "
                "Refresh only helps once a newer one has landed.")
    st.markdown(f"<div class='warn'>{msg}</div>", unsafe_allow_html=True)


def render(result, mode: str) -> None:
    if not result:
        st.caption("No legal lineup under the cap for this slate.")
        return
    rows = cfb.lineup_rows(result)
    headline = ("floor <b>{:.0f}</b>".format(result["floor"]) if mode == "cash"
                else "ceiling <b>{:.0f}</b>".format(result["ceil"]))
    st.markdown(
        f"<div class='lu-tot'>{headline} · proj <b>{result['proj']}</b> · "
        f"sd <b>{result['sd']}</b> · own <b>{result['own']:.0f}%</b> · "
        f"<b>${result['salary']:,}</b> / 50k</div>", unsafe_allow_html=True)

    if mode == "gpp" and result.get("stack"):
        s = result["stack"]
        mates = ", ".join(s["with"]) or "none"
        back = (" · bring-back " + ", ".join(s["bring_back"])) if s["bring_back"] else ""
        st.markdown(f"<div class='stk'>Stack: <b>{s['qb']}</b> ({s['team']}) "
                    f"+ {mates}{back}</div>", unsafe_allow_html=True)
    elif mode == "cash":
        st.markdown("<div class='lu-note'>No stack by design — a cash lineup "
                    "maximises its floor, and correlation is what raises a "
                    "lineup's spread.</div>", unsafe_allow_html=True)

    # Two quarterbacks looks like a bug the first time. Say what it is, where
    # it appears, and why it is never two from the same team.
    if len(result.get("qbs") or []) > 1:
        st.markdown(
            "<div class='qb2'>Two quarterbacks — <b>"
            + " + ".join(result["qbs"]) +
            "</b>. The S-FLEX takes a QB, and college quarterbacks average "
            "<b>17.2</b> DK points against 8.9 for receivers. Never two from "
            "the same team: that pairing is <b>−0.098</b> correlated, because "
            "they compete for snaps.</div>", unsafe_allow_html=True)

    body = "".join(
        f"<tr><td class='pos'>{r['slot']}</td>"
        f"<td class='nm'>{r['player']}</td>"
        f"<td class='team'>{r['team']} v {r['opp']}</td>"
        f"<td class='num'>{r['salary']:,}</td>"
        f"<td class='num'>{r['proj']}</td>"
        f"<td class='num'>{r['own']:.0f}%</td></tr>" for r in rows)
    st.markdown("<div class='lu-wrap'><table class='lu'>"
                "<tr><th>Slot</th><th>Player</th><th>Match</th><th>$</th>"
                "<th>Pts</th><th>Own</th></tr>"
                f"{body}</table></div>", unsafe_allow_html=True)
    st.caption(
        f"Objective: mean {'−' if mode == 'cash' else '+'} "
        f"{0.75 if mode == 'cash' else 1.25}×sd, through a correlation matrix "
        f"measured on cfbfastR 2024+2025. Ownership is a PRIOR — no college "
        f"contest exports have been fitted yet — so read it as a tilt.")


def _csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "slot", "player", "team", "opp", "salary", "proj",
                "own", "leverage", "band"])
    for mode in ("cash", "gpp"):
        for r in cfb.lineup_rows(res.get(mode)):
            w.writerow([mode, r["slot"], r["player"], r["team"], r["opp"],
                        r["salary"], r["proj"], r["own"], r["leverage"],
                        r["band"]])
    return buf.getvalue().encode()


t_cash, t_gpp, t_board = st.tabs(["💵 CASH", "🚀 GPP", "📋 Board"])
with t_cash:
    render(res.get("cash"), "cash")
with t_gpp:
    render(res.get("gpp"), "gpp")
with t_board:
    pool = sorted(res["pool"], key=lambda p: -(p.get("proj") or 0))
    pos_filter = st.multiselect("Position", ["QB", "RB", "WR"], default=[])
    shown = [p for p in pool
             if not pos_filter or base_position(p.get("dk_pos")) in pos_filter][:60]
    body = "".join(
        f"<tr><td class='pos'>{base_position(p.get('dk_pos'))}</td>"
        f"<td class='nm'>{p['name']}</td>"
        f"<td class='team'>{p['team']} v {p.get('opp_team') or '?'}</td>"
        f"<td class='num'>{p['salary']:,}</td>"
        f"<td class='num'>{p['proj']}</td>"
        f"<td class='num'>{p.get('own', 0):.0f}%</td>"
        f"<td class='num'>{p.get('leverage', 0):+.0f}</td>"
        f"<td class='num'>{p.get('band_points', 0):.1f}</td></tr>" for p in shown)
    st.markdown("<div class='lu-wrap'><table class='lu'>"
                "<tr><th>Pos</th><th>Player</th><th>Match</th><th>$</th>"
                "<th>Pts</th><th>Own</th><th>Lev</th><th>Band</th></tr>"
                f"{body}</table></div>", unsafe_allow_html=True)
    st.caption(
        "Top 60 by projection. **Lev** is projection percentile minus ownership "
        "percentile within position — GPP signal only. **Band** is how many DK "
        "points of the projection came from the *unpriced head* of a ladder "
        "rather than from a posted price: a player whose ladder starts at "
        "\"50+\" has everything below 50 yards modelled. Higher band, less "
        "trustworthy number.")

st.download_button("⬇️ Download both lineups (CSV)", data=_csv(),
                   file_name=f"ncaaf_lineups_{res['gid']}.csv", mime="text/csv")
