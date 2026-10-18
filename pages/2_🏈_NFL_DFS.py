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


from edge.pagereload import fresh  # noqa: E402

fresh()   # a Cloud deploy reloads every repo module (edge/pagereload.py)

from edge import dfs_leverage_ui as LU        # noqa: E402
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
.tag {font-size:10px; font-weight:700; border-radius:3px; padding:0 3px; margin-left:4px;
       background:#5a2a00; color:#ffb070; vertical-align:1px;}
.mu-pos {color:#3fb079;} .mu-neg {color:#e07070;}
.warn {background:#4a3a00; border:1px solid #8a6a00; color:#ffd97a; border-radius:6px;
       padding:6px 10px; font-size:13px; margin:2px 0 8px;}
</style>
""", unsafe_allow_html=True)


# ── data ────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _nfl_slates(_nonce: int):
    from edge import dfs
    return nfl.classic_groups(dfs.draft_groups("NFL"))


@st.cache_data(ttl=300, show_spinner=False)
def _nfl_build(gid, iters: int, _nonce: int):
    client = scraped_client(nfl.SPORT, "dfs")
    return nfl.build_slate(client, draft_group=gid, iters=iters)


@st.cache_data(ttl=300, show_spinner=False)
def _nfl_stack(gid, iters: int, _nonce: int, qb: str):
    """The best GPP lineup stacked around one chosen quarterback."""
    from edge import dfs_opt_nfl
    pool = _nfl_build(gid, iters, _nonce)["pool"]
    return dfs_opt_nfl.optimize(pool, mode="gpp", stack_qb=qb, iters=iters, seed=0)


@st.cache_data(ttl=600, show_spinner=False)
def _nfl_leverage(gid, iters: int, _nonce: int, qbs: tuple):
    """GPP lineups from chalk to contrarian (edge/dfs_leverage.py) plus the
    named quarterbacks' stacks, with a simulated field."""
    from edge import dfs_leverage_sports as LS
    extra = [_nfl_stack(gid, iters, _nonce, qb) for qb in qbs]
    return LS.nfl(_nfl_build(gid, iters, _nonce), None, iters=max(60, iters // 3), extra=extra)


@st.cache_data(ttl=600, show_spinner=False)
def _nfl_leverage_for(gid, iters: int, _nonce: int, qbs: tuple, contest: dict):
    """The same lineups ranked by expected payout in one contest -- the same
    simulated worlds edge/dfs_leverage_sports.nfl drew (3,000, seed 1)."""
    from edge import dfs_leverage as lev, dfs_nfl_contest as nc
    pool = _nfl_build(gid, iters, _nonce)["pool"]
    return lev.with_contest(_nfl_leverage(gid, iters, _nonce, qbs),
                            nc.scores(pool, n_sims=3000, seed=1), contest)


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
        if client.problem:
            st.sidebar.caption(f"⚠️ Could not read GitHub ({client.problem}), "
                               "so these are the prices deployed with the app.")
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
        slates = _nfl_slates(st.session_state.nfl_nonce)
    except Exception as exc:                                # noqa: BLE001
        slates = []
        st.error(f"DraftKings lobby unreachable: {exc}")

    gid = None
    if slates:
        labels = [f"{s['label']} · {s['games']}g · {_et(s['start'])}" for s in slates]
        # The next Main to lock, not the biggest: DK lists future weeks'
        # Mains early (dfs_run_nfl.default_slate).
        default = nfl.default_slate(slates)
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
        res = _nfl_build(gid, iters, st.session_state.nfl_nonce)
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
    if res.get("unpriced_reason"):
        st.caption(res["unpriced_reason"])
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


# Injury tag next to a name: Q / D (ESPN's live report, edge/nfl_intel.py).
# Ruled-out players never reach the board -- build_slate removes them.
_TAG = {"questionable": "Q", "doubtful": "D", "day-to-day": "DTD"}


def _tag(status: str | None) -> str:
    t = _TAG.get((status or "").strip().lower())
    return f"<span class='tag'>{t}</span>" if t else ""


_intel = stats.get("intel") or {}
if stats.get("ruled_out"):
    st.markdown("<div class='warn'>Ruled out on the injury report, removed from the "
                "pool: " + ", ".join(stats["ruled_out"]) + "</div>", unsafe_allow_html=True)
if not _intel.get("ok") or _intel.get("injury_source") in ("unavailable", "none"):
    st.markdown("<div class='warn'>Injury report unavailable — ruled-out players are "
                "NOT being removed and ownership runs without matchup / last-game "
                f"terms. {_intel.get('reason', '')}</div>", unsafe_allow_html=True)


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
        f"<td class='nm'>{r['player']}{_tag(r.get('inj'))}</td>"
        f"<td class='team'>{r['team']} v {r['opp']}</td>"
        f"<td class='num'>{r['salary']:,}</td>"
        f"<td class='num'>{r['proj']}</td>"
        f"<td class='num'>{r['own']:.0f}%</td></tr>" for r in rows)
    st.markdown("<div class='lu-wrap'><table class='lu'>"
                "<tr><th>Slot</th><th>Player</th><th>Match</th><th>$</th>"
                "<th>Pts</th><th>Own</th></tr>"
                f"{body}</table></div>", unsafe_allow_html=True)
    st.caption(f"Objective: mean {'−' if mode == 'cash' else '+'} {left}×sd, "
               f"through the measured correlation matrix. Ownership is fitted on "
               f"five 2026 main-slate GPPs (~2.2 pts/player held out) — a tilt, "
               f"not gospel.")


def _csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "slot", "player", "team", "opp", "salary", "proj",
                "own", "leverage"])
    for mode, lu in (("cash", res.get("cash")), ("gpp", gpp_pick)):
        for r in nfl.lineup_rows(lu):
            w.writerow([mode, r["slot"], r["player"], r["team"], r["opp"],
                        r["salary"], r["proj"], r["own"], r["leverage"]])
    return buf.getvalue().encode()


t_cash, t_gpp, t_board, t_mu = st.tabs(["💵 CASH", "🚀 GPP", "📋 Board", "🎯 Matchups"])
with t_cash:
    render(res.get("cash"), "cash")
gpp_pick = res.get("gpp")
with t_gpp:
    # The optimiser's best stack is one opinion; when its game is the field's
    # chalk, a single-entry GPP may want a different one (2026-10-11: the
    # top stack was the slate's most-owned game from both sides). Which one is
    # right depends on the contest, so with one given every candidate -- the top
    # QBs' stacks and the chalk -> contrarian frontier (edge/dfs_leverage.py) --
    # is ranked by expected payout in it, in ONE table with ONE star.
    _qbs = sorted((p for p in res["pool"] if "QB" in p["pos"]), key=lambda p: -(p.get("proj") or 0))
    _contest = LU.contest_picker("NFL", res["gid"], key="nfl")
    _nonce = st.session_state.get("nfl_nonce", 0)
    _auto_qb = ((res.get("gpp") or {}).get("stack") or {}).get("qb")
    _cands = [q["name"] for q in _qbs[:8]]
    if _auto_qb and _auto_qb not in _cands:
        _cands.append(_auto_qb)
    _lev_on = _contest is not None or st.toggle(
        "Chalk ↔ contrarian map", key="nfl_lev_on",
        help="Every GPP lineup from the chalkiest to the most contrarian (the top 8 QBs' "
             "stacks included), on a chart of ownership against points, beside a "
             "simulated field.")
    if _lev_on:
        with st.spinner("Building lineups from chalk to contrarian (~1 min the first time)…"):
            _an = (_nfl_leverage_for(gid, iters, _nonce, tuple(_cands), _contest) if _contest
                   else _nfl_leverage(gid, iters, _nonce, tuple(_cands)))
        if _contest and _an.get("cands"):
            # ONE ranking (2026-10-18): this table, the map's dropdown and the lineup
            # below share the same candidates, simulation and star. A separate table
            # of the top-8 QBs' stacks used to star a different lineup (Shough) from
            # the one the map picked and rendered (a 5%-owned Jones stack).
            _rk = sorted(range(len(_an["cands"])), key=lambda i: -_an["cands"][i].get("ev", 0))
            _body = "".join(
                f"<tr><td class='nm'>{'⭐ ' if i == _an.get('best') else ''}"
                f"{_an['cands'][i].get('tag') or 'no stack'}</td>"
                f"<td class='num'>{_an['cands'][i]['own']:.0f}%</td>"
                f"<td class='num'>{_an['cands'][i]['ceil']:.0f}</td>"
                f"<td class='num'>{_an['cands'][i].get('p_cash', 0):.0%}</td>"
                f"<td class='num'>{_an['cands'][i].get('p_top1', 0):.1%}</td>"
                f"<td class='num'>{_an['cands'][i].get('rel', 0):.0f}</td></tr>"
                for i in _rk[:10])
            st.markdown("<div class='lu-wrap'><table class='lu'><tr><th>Lineup ranked for "
                        "this contest</th><th>Own</th><th>Ceil</th><th>Cash</th><th>Top 1%</th>"
                        "<th>EV</th></tr>" + _body + "</table></div>", unsafe_allow_html=True)
            st.caption("Every candidate lineup (each top QB's best stack, plus lineups from "
                       "chalk to contrarian), played against a simulated field of this "
                       "contest's size. ⭐ = best expected payout; it is the lineup shown "
                       "below. Own = the lineup's summed ownership. EV is relative: 100 = best.")
        _chosen = LU.leverage_view(_an, key="nfl")
        if _chosen:
            gpp_pick = _chosen["result"]
            render(gpp_pick, "gpp")
    else:
        _opts = ["Auto (best)"] + [f"{q['name']} ({q['team']}) · {q['proj']} pts · "
                                   f"{q.get('own', 0):.0f}% own" for q in _qbs]
        _names = [q["name"] for q in _qbs]
        _pick = st.selectbox("Stack", _opts,
                             index=0,
                             help="Build the GPP lineup around a quarterback you choose.")
        if _pick != "Auto (best)":
            _qb = _qbs[_opts.index(_pick) - 1]["name"]
            with st.spinner(f"Building a {_qb} stack…"):
                gpp_pick = _nfl_stack(gid, iters, _nonce, _qb)
        render(gpp_pick, "gpp")
with t_board:
    pool = sorted(res["pool"], key=lambda p: -(p.get("proj") or 0))
    pos_filter = st.multiselect("Position", ["QB", "RB", "WR", "TE", "DST"],
                                default=[])
    shown = [p for p in pool
             if not pos_filter or theory.base_position(p) in pos_filter][:60]
    body = "".join(
        f"<tr><td class='pos'>{theory.base_position(p)}</td>"
        f"<td class='nm'>{p['name']}{_tag(p.get('inj_status'))}</td>"
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

with t_mu:
    # Our SMASH matchups (edge/nfl_intel.matchup_table): DK points the
    # opponent's defence allows each position per game ABOVE what the same
    # offences score against everyone else. They move OWNERSHIP (the field
    # chases them), not the projection -- props already price them
    # (scripts/nfl_intel_backtest.py).
    _imp: dict = {}
    for p in res["pool"]:
        if p.get("implied") is not None:
            _imp.setdefault(p["team"], p["implied"])
        elif p.get("implied_opp_points") is not None and p.get("opp_team"):
            _imp.setdefault(p["opp_team"], p["implied_opp_points"])   # a DST's opponent
    _mu = {t: dict(r, imp=_imp.get(t)) for t, r in (_intel.get("matchups") or {}).items()}
    if not _mu:
        st.caption("No matchup data for this slate (nflverse unavailable?).")
    else:
        def _cell(v):
            if v is None:
                return "<td class='num'>·</td>"
            cls = "mu-pos" if v >= 1.5 else "mu-neg" if v <= -1.5 else ""
            return f"<td class='num {cls}'>{v:+.1f}</td>"
        order = sorted(_mu.items(), key=lambda kv: -(kv[1].get("imp") or 0))
        body = "".join(
            f"<tr><td class='pos'>{t}</td><td class='team'>v {r['opp'] or '?'}</td>"
            f"<td class='num'>{(r.get('imp') or 0):.1f}</td>"
            + "".join(_cell(r.get(pos)) for pos in ("QB", "RB", "WR", "TE")) + "</tr>"
            for t, r in order)
        st.markdown("<div class='lu-wrap'><table class='lu'>"
                    "<tr><th>Off</th><th>Opp</th><th>Imp</th><th>QB</th><th>RB</th>"
                    "<th>WR</th><th>TE</th></tr>" f"{body}</table></div>",
                    unsafe_allow_html=True)
        st.caption("DK pts/game the opponent allows each position above what those "
                   "offences score elsewhere (this season, recent weeks weighted; "
                   "green = soft). Imp = Vegas implied team total. The field chases "
                   "both — they feed ownership, not projections (books price them).")
    _vac = (_intel.get("vacated") or {})
    _slate_teams = set(res.get("teams") or [])
    _vac_rows = [(t, v) for t, v in sorted(_vac.items()) if t in _slate_teams]
    if _vac_rows:
        st.markdown("**Out this week — volume to absorb**")
        st.markdown("".join(
            f"<div class='lu-note'><b>{t}</b>: {', '.join(v['who'])} — "
            f"{100 * v['pass']:.0f}% of targets, {100 * v['rush']:.0f}% of carries</div>"
            for t, v in _vac_rows), unsafe_allow_html=True)
    if _intel.get("ok"):
        st.caption(f"Injuries: {_intel.get('injury_source')} · NFL week {_intel.get('week')}")

st.download_button("⬇️ Download both lineups (CSV)", data=_csv(),
                   file_name=f"nfl_lineups_{res['gid']}.csv", mime="text/csv")
