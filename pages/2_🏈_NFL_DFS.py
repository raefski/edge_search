"""pages/2_🏈_NFL_DFS.py — DK NFL Classic cash + GPP lineups, on your phone.

The NFL half of the DFS app. `app.py` is MLB; this is the sport picker's second
entry, which is the shape DFS_MULTISPORT_PLAN.md called for (it reserved `2_`
for NFL). Named `NFL_DFS` rather than plain `NFL` because `4_🎯_Pickem.py` is
also an NFL page and is a completely different contest -- a spread pool, not a
salary-cap lineup game -- and two sidebar entries reading "NFL" would be a
genuine ambiguity on a small screen.

EVERYTHING COMES FROM edge/dfs_run_nfl.py, WHICH THE CLI ALSO CALLS
No pool-building, no slate resolution and no optimizer settings live in this
file. `scripts/dfs_lineups_nfl.py` and this page call the same `build_slate`,
so a lineup on the phone and a lineup on the desktop are the same lineup. That
is not tidiness: the two copies of the shared core drifting is the failure this
repo has already had once (ODDS_LAYER.md), and DFS is where it would cost money.

WHAT THE TWO TABS ACTUALLY ARE
Not "the same lineup, stacked and unstacked". CASH maximises `mean - z*sd` of
the lineup total and GPP maximises `mean + z*sd`, through a measured
correlation matrix -- so cash spreads across games and refuses a stack, and GPP
concentrates into one, as consequences of the objective rather than as rules.
The reasoning and every measured constant are in edge/dfs_nfl_theory.py, and
the head-to-head that tests the claim is scripts/nfl_lineup_backtest.py.

PRICES ARE FREE AND THE PAGE SAYS SO
Props come from the scraped store on the desktop and from the committed
snapshot (`data/odds_snapshot_dfs_nfl.json`) on Streamlit Cloud, which cannot
scrape. DK salaries come from DraftKings' own draftables endpoint, which does
answer a datacenter IP. The source badge is not decoration -- a silent fallback
to the paid Odds API is invisible in the output and costs credits.
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

st.set_page_config(page_title="DK NFL DFS Lineups", page_icon="🏈",
                   layout="wide", initial_sidebar_state="auto")


# Streamlit Community Cloud pulls new commits and RERUNS this script without
# restarting the Python process, so sys.modules keeps whatever module objects
# an earlier run imported. A deploy that changes an existing function's BODY
# -- the ordinary bugfix -- then goes on running the pre-fix code with no
# error at all. pages/5_⚖️_Arbitrage.py records this happening for real and
# carries the same guard; the DFS modules need it for the same reason and the
# stakes here are a wrong lineup rather than a wrong display.
def _dfs_fingerprint() -> float:
    try:
        return max(p.stat().st_mtime for p in (ROOT / "edge").glob("dfs*.py"))
    except ValueError:
        return 0.0


@st.cache_resource(show_spinner=False)
def _reload_dfs(fingerprint: float) -> float:
    for _pass in range(2):
        for name in sorted(k for k in sys.modules
                           if k.startswith("edge.dfs") or k == "edge.nfl"):
            try:
                importlib.reload(sys.modules[name])
            except Exception:                               # noqa: BLE001
                pass
    return fingerprint


_reload_dfs(_dfs_fingerprint())

from edge import dfs_nfl_theory as theory     # noqa: E402
from edge import dfs_run_nfl as nfl           # noqa: E402
from edge.odds.cli import scraped_client      # noqa: E402

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
table.lu td.team {color:#9aa4b2; width:74px; font-size:12px;}
table.lu td.nm {white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:150px;}
table.lu td.num {text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
.stk {background:#0e2c1e; border:1px solid #1f7a4d; color:#8fe0b4; border-radius:6px;
      padding:5px 9px; font-size:12.5px; margin:2px 0 8px;}
.warn {background:#4a3a00; border:1px solid #8a6a00; color:#ffd97a; border-radius:6px;
       padding:6px 10px; font-size:13px; margin:2px 0 8px;}
</style>
""", unsafe_allow_html=True)


# ── data ────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _slates(_nonce: int):
    from edge import dfs
    return nfl.classic_groups(dfs.draft_groups("NFL"))


@st.cache_data(ttl=300, show_spinner=False)
def _build(gid, iters: int, _nonce: int):
    client = scraped_client(nfl.SPORT, "dfs")
    return nfl.build_slate(client, draft_group=gid, iters=iters)


def _source_badge():
    """Say where the prices actually came from, every time.

    The free scrape, the committed snapshot and the paid Odds API produce
    identical-looking lineups, so a silent fallback to the paid client is
    indistinguishable from the free path working -- except on the bill.
    """
    try:
        client = scraped_client(nfl.SPORT, "dfs")
    except Exception as exc:                                # noqa: BLE001
        st.sidebar.error(f"No free price source: {exc}")
        return
    kind = type(client).__name__
    if kind == "ScrapedOddsClient":
        st.sidebar.success("🟢 Free scraped prices (local store)")
    elif kind == "SnapshotOddsClient":
        mins = client.age_seconds / 60.0
        st.sidebar.success(f"🟢 Free scraped prices · {mins:.0f} min old")
        if mins > 120:
            st.sidebar.caption(
                "Getting stale. Tap **📡 Request a desktop scan** on the "
                "Arbitrage page — it refreshes this too. Or on the desktop: "
                "`python3 scripts/odds_collect.py --profile dfs_nfl --push`.")
    else:
        st.sidebar.warning("🟡 Falling back to the paid Odds API — the free "
                           "snapshot is missing or stale.")


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
st.session_state.setdefault("nfl_nonce", 0)

with st.sidebar:
    st.header("🏈 DK NFL DFS")
    _source_badge()
    if st.button("🔄 Refresh (free)", width="stretch",
                 help="Re-pulls DK salaries and the latest scraped props. "
                      "0 Odds-API credits."):
        st.session_state.nfl_nonce += 1
        st.cache_data.clear()
        st.rerun()

    try:
        slates = _slates(st.session_state.nfl_nonce)
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
        help="Higher finds slightly better lineups and takes longer. 350 is "
             "~15s on a full slate.")
    st.caption("Prices are free. This page never spends a credit.")


# ── main ────────────────────────────────────────────────────────────────────
st.title("DK NFL DFS Lineups")

if not slates:
    st.warning("No DK NFL Classic slates listed right now.")
    st.stop()

with st.spinner("Building cash + GPP lineups…"):
    try:
        res = _build(gid, iters, st.session_state.nfl_nonce)
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
    f"pool: <b>{stats['offense']}</b> offensive players + <b>{stats['dst']}</b> "
    f"defences</div>", unsafe_allow_html=True)

# INACTIVES. NFL teams declare them 90 minutes before kickoff, and this page
# has no inactive feed -- MLB gets confirmed batting orders, NFL's equivalent
# does not exist for free at this cadence. What DOES track it is the props
# themselves: books pull a ruled-out player's markets, and a player with no
# markets is absent from the pool entirely (dfs_project returns proj=None).
# So the snapshot's AGE is the inactive check, which is why it is stated in
# the sidebar and why this line is here rather than in a doc nobody opens.
_age = None
_is_snapshot = False
try:
    _c = scraped_client(nfl.SPORT, "dfs")
    _age = getattr(_c, "age_seconds", 0.0) / 60.0
    _is_snapshot = type(_c).__name__ == "SnapshotOddsClient"
except Exception:                                           # noqa: BLE001
    pass
if _age is not None and _age > 90:
    if _is_snapshot:
        # On Streamlit Cloud, 🔄 Refresh re-reads whatever snapshot is
        # CURRENTLY COMMITTED — it does not trigger a new scrape. Telling a
        # phone user to "just tap Refresh" here would be actively wrong: found
        # live 2026-09-12 when the app was checked Saturday evening and showed
        # 333-minute-old props with nothing scheduled to push a newer one
        # until Sunday 8 AM. Refresh only helps once the desktop's publish
        # timer (deploy/odds-publish-dfs-nfl.timer) has actually landed a new
        # commit, so say that instead of implying a tap fixes it.
        st.markdown(
            f"<div class='warn'>⏱️ These props are <b>{_age:.0f} minutes "
            "old</b>. This page reads a snapshot pushed from the desktop — "
            "🔄 Refresh only helps once a NEWER one has landed, it doesn't "
            "scrape on its own. A push runs automatically every 2 hours, "
            "more often near a lock. For one right now, tap 📡 <b>Request a "
            "desktop scan</b> on the Arbitrage page.</div>",
            unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div class='warn'>⏱️ These props are <b>{_age:.0f} minutes "
            "old</b>. NFL inactives drop 90 minutes before kickoff and a "
            "ruled-out player keeps a stale projection until the books pull "
            "his markets — tap <b>🔄 Refresh</b> inside the last hour before "
            "lock.</div>", unsafe_allow_html=True)

if stats.get("missing_games"):
    st.markdown(
        "<div class='warn'>No game total posted for: "
        + ", ".join(stats["missing_games"])
        + " — those defences are missing from the pool. Offensive players are "
          "unaffected (they are projected from their own props).</div>",
        unsafe_allow_html=True)
if stats.get("conflicts"):
    st.markdown("<div class='warn'>Two books' events matched the same game: "
                + ", ".join(stats["conflicts"]) + "</div>", unsafe_allow_html=True)


def render(result, mode: str) -> None:
    if not result:
        st.caption("No legal lineup under the cap for this slate.")
        return
    rows = nfl.lineup_rows(result)
    left = theory.Z_CASH if mode == "cash" else theory.Z_GPP
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
    st.caption(f"Objective: mean {'−' if mode == 'cash' else '+'} {left}×sd, "
               f"through the measured correlation matrix. Ownership is a PRIOR "
               f"(no NFL contest exports) — read it as a tilt, not a number.")


def _csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "slot", "player", "team", "opp", "salary", "proj",
                "own", "leverage"])
    for mode in ("cash", "gpp"):
        for r in nfl.lineup_rows(res.get(mode)):
            w.writerow([mode, r["slot"], r["player"], r["team"], r["opp"],
                        r["salary"], r["proj"], r["own"], r["leverage"]])
    return buf.getvalue().encode()


t_cash, t_gpp, t_board = st.tabs(["💵 CASH", "🚀 GPP", "📋 Board"])
with t_cash:
    render(res.get("cash"), "cash")
with t_gpp:
    render(res.get("gpp"), "gpp")
with t_board:
    pool = sorted(res["pool"], key=lambda p: -(p.get("proj") or 0))
    pos_filter = st.multiselect("Position", ["QB", "RB", "WR", "TE", "DST"],
                                default=[])
    shown = [p for p in pool
             if not pos_filter or theory.base_position(p) in pos_filter][:60]
    body = "".join(
        f"<tr><td class='pos'>{theory.base_position(p)}</td>"
        f"<td class='nm'>{p['name']}</td>"
        f"<td class='team'>{p['team']} v {p.get('opp_team') or '?'}</td>"
        f"<td class='num'>{p['salary']:,}</td>"
        f"<td class='num'>{p['proj']}</td>"
        f"<td class='num'>{p.get('own', 0):.0f}%</td>"
        f"<td class='num'>{p.get('leverage', 0):+.0f}</td></tr>" for p in shown)
    st.markdown("<div class='lu-wrap'><table class='lu'>"
                "<tr><th>Pos</th><th>Player</th><th>Match</th><th>$</th>"
                "<th>Pts</th><th>Own</th><th>Lev</th></tr>"
                f"{body}</table></div>", unsafe_allow_html=True)
    st.caption("Top 60 by projection. 'Lev' is projection percentile minus "
               "ownership percentile within position — positive means the "
               "field is underweighting him. GPP signal only.")

st.download_button("⬇️ Download both lineups (CSV)", data=_csv(),
                   file_name=f"nfl_lineups_{res['gid']}.csv", mime="text/csv")
