"""pages/7_🥊_MMA_DFS.py — DK MMA cash + GPP lineups, on your phone.

Six fighters, $50,000, no positions. Every number comes from
edge/dfs_run_mma.py, which the CLI (scripts/dfs_lineups_mma.py) also calls.

THE FOUR THINGS THIS PAGE EXISTS TO SAY OUT LOUD
  1. HOW OLD THE PRICES ARE. Win, method and round all come from a DraftKings
     Sportsbook capture pushed by the desktop (Streamlit Cloud cannot reach the
     sportsbook). A fight line moves on weigh-in news; the capture time is the
     first line under the title.
  2. THAT THERE IS NO LATE SWAP, and that LOCK IS THE FIRST FIGHT, not the
     main event -- three-plus hours earlier.
  3. WHICH BOUTS HAVE NO MARKET. A replacement opponent is the common case:
     the sportsbook is still pricing the old fight. Those bouts run on
     DraftKings' own salaries instead, and are labelled.
  4. WHAT THE OBJECTIVES ARE. P(beat the double-up line) and P(top 1%),
     against a simulated field on the same simulated fights -- not "floor"
     and "ceiling" of the lineup in isolation. The field is a PRIOR until a
     contest export has been fitted.
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

st.set_page_config(page_title="DK MMA DFS Lineups", page_icon="🥊",
                   layout="wide", initial_sidebar_state="auto")

# See pages/6_🏁_NASCAR_DFS.py and edge/dfs_pagereload.py: Streamlit Cloud
# reruns without restarting, so a deploy only takes effect through this gate.
from edge.dfs_pagereload import reload_packages, source_fingerprint  # noqa: E402


@st.cache_resource(show_spinner=False)
def _reload_edge(fingerprint: float) -> float:
    reload_packages()
    return fingerprint


_reload_edge(source_fingerprint())

from edge import dfs_run_mma as R          # noqa: E402

ET = ZoneInfo("America/New_York")

st.markdown("""
<style>
.block-container {padding-top: 2.0rem; padding-bottom: 2rem;}
h1 {font-size: 1.55rem !important; margin-bottom: .1rem;}
.summary {font-size: 13px; color: #9aa4b2; line-height: 1.55; margin: .1rem 0 .5rem;}
.lu-tot {font-size:13px; color:#c7d0dd; margin:2px 0 6px; line-height:1.5;}
.lu-wrap {overflow-x:auto;}
table.lu {width:100%; border-collapse:collapse; font-size:14px;}
table.lu th {text-align:left; color:#7f8a9c; font-weight:600; font-size:11px;
             text-transform:uppercase; padding:2px 5px;
             border-bottom:1px solid rgba(255,255,255,.16);}
table.lu td {padding:5px 5px; border-bottom:1px solid rgba(255,255,255,.07);
             vertical-align:top;}
table.lu td.nm {white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:150px;}
table.lu td.nm small {display:block; color:#7f8a9c; font-size:11px;}
table.lu td.num {text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
.warn {background:#4a1f00; border:1px solid #a04a00; color:#ffc08a; border-radius:6px;
       padding:8px 11px; font-size:13px; margin:2px 0 10px; line-height:1.5;}
.ok   {background:#0e2c1e; border:1px solid #1f7a4d; color:#8fe0b4; border-radius:6px;
       padding:7px 10px; font-size:13px; margin:2px 0 10px;}
.note {background:#151b28; border:1px solid #2b3a52; color:#aebdd4; border-radius:6px;
       padding:7px 10px; font-size:12.5px; margin:2px 0 10px; line-height:1.5;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def _slates(_nonce: int):
    from edge import dfs
    return R.classic_groups(dfs.draft_groups(R.DK_SPORT))


@st.cache_data(ttl=300, show_spinner=False)
def _build(gid, sims: int, _nonce: int):
    res = R.build_slate(draft_group=gid, n_sims=sims)
    # The per-sim matrices are for the CLI's portfolio; the page does not
    # need them and they are the bulk of what st.cache_data would pickle.
    res.pop("points", None)
    res.pop("_lines", None)
    return res


def _et(iso: str | None, fmt: str = "%a %-I:%M %p ET") -> str:
    if not iso:
        return ""
    dt = R._parse_time(iso)
    return dt.astimezone(ET).strftime(fmt) if dt else ""


def _pct(x) -> str:
    return f"{100 * float(x or 0):.0f}%"


# ── sidebar ─────────────────────────────────────────────────────────────────
st.session_state.setdefault("mma_nonce", 0)

with st.sidebar:
    st.header("🥊 DK MMA DFS")
    if st.button("🔄 Refresh", width="stretch",
                 help="Re-reads DK salaries and the latest sportsbook capture "
                      "the desktop has pushed."):
        st.session_state.mma_nonce += 1
        st.cache_data.clear()
        st.rerun()
    try:
        slates = _slates(st.session_state.mma_nonce)
    except Exception as exc:                                # noqa: BLE001
        slates = []
        st.error(f"DraftKings lobby unreachable: {exc}")
    gid = None
    if slates:
        labels = [f"{s['label']} · {s['fights']} fights · {_et(s['start'])}" for s in slates]
        choice = st.selectbox("Slate", labels, index=0,
                              help="DK MMA Classic slates. 'Main' is the full card.")
        gid = slates[labels.index(choice)]["gid"]
    sims = st.select_slider("Simulated cards", options=[5000, 10000, 20000],
                            value=10000,
                            help="Every number on the page is read off these. "
                                 "More is steadier and slower.")
    st.caption("Win / method / round: DraftKings Sportsbook, de-biased. "
               "Stats: UFCStats. Nothing here costs credits.")


# ── main ────────────────────────────────────────────────────────────────────
st.title("DK MMA DFS Lineups")

if not slates:
    st.warning("No DK MMA Classic slates listed right now.")
    st.stop()

with st.spinner("Simulating the card…"):
    try:
        res = _build(gid, sims, st.session_state.mma_nonce)
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

stt, book = res["stats"], res["stats"].get("book") or {}
first = min((d.get("start") or "" for d in res["pool"]), default="")
st.markdown(
    f"<div class='summary'><b>{stt['fights']} fights</b> · lock "
    f"<b>{_et(first)}</b> (first bell) · draft group {res['gid']}<br>"
    f"Odds captured <b>{_et(book.get('generated_at'), '%a %-I:%M %p ET')}</b>"
    + (f" ({book['age_hours']:.1f}h ago)" if book.get("age_hours") is not None else "")
    + f" · salaries {stt.get('draftables_source')}</div>", unsafe_allow_html=True)

if book.get("error"):
    st.markdown(f"<div class='warn'>⚠️ <b>No sportsbook capture.</b> {book['error']}. "
                "Every bout is running on DK's salaries alone.</div>",
                unsafe_allow_html=True)
elif book.get("stale"):
    st.markdown(
        f"<div class='warn'>⚠️ <b>Odds are {book.get('age_hours', 0):.0f} hours old.</b> "
        "Lines move on weigh-in and late-replacement news. Run "
        "<code>scripts/mma_odds_capture.py --push</code> on the desktop, then "
        "Refresh.</div>", unsafe_allow_html=True)

st.markdown("<div class='note'>🔒 <b>No late swap.</b> DraftKings locks MMA at the "
            "first fight's opening bell — the main event is hours later, but "
            "nothing can change after lock.</div>", unsafe_allow_html=True)

flags = []
if stt.get("scratched"):
    flags.append("Scratched (removed): " + ", ".join(stt["scratched"]))
if stt.get("replaced"):
    flags.append("<b>Replacement opponent</b> — the sportsbook still prices the "
                 "original fight, so these run on DK's salaries: "
                 + ", ".join(stt["replaced"]))
if stt.get("no_market"):
    flags.append("No sportsbook market (DK salaries used): " + ", ".join(stt["no_market"]))
if stt.get("unmatched"):
    flags.append("Could not match to UFCStats (treated as a debut): "
                 + ", ".join(stt["unmatched"]))
if flags:
    st.markdown("<div class='warn'>" + "<br>".join(flags) + "</div>",
                unsafe_allow_html=True)


def render(result: dict, mode: str) -> None:
    if not result or "lineup" not in result:
        st.caption((result or {}).get("error", "No legal lineup under the cap."))
        return
    head = (f"P(cash) <b>{_pct(result['p_cash'])}</b>" if mode == "cash"
            else f"P(top 1%) <b>{100 * result['p_top1']:.1f}%</b>")
    st.markdown(
        f"<div class='lu-tot'>{head} · proj <b>{result['proj']}</b> · "
        f"p25 <b>{result['floor']:.0f}</b> · p90 <b>{result['ceil']:.0f}</b> · "
        f"exp. wins <b>{result['exp_wins']}</b> · <b>${result['salary']:,}</b></div>",
        unsafe_allow_html=True)
    body = "".join(
        f"<tr><td class='nm'>{r['fighter']}<small>vs {r['opponent']}"
        f"{' · 5 rds' if r['sched'] == 5 else ''}"
        f"{' · no mkt' if r['source'] in ('salary', 'none') else ''}</small></td>"
        f"<td class='num'>{r['salary']:,}</td>"
        f"<td class='num'>{_pct(r['p_win'])}</td>"
        f"<td class='num'>{r['proj']:.0f}</td>"
        f"<td class='num'>{r['own']:.0f}%</td></tr>" for r in R.lineup_rows(result))
    st.markdown("<div class='lu-wrap'><table class='lu'>"
                "<tr><th>Fighter</th><th>$</th><th>Win</th><th>Pts</th><th>Own</th></tr>"
                f"{body}</table></div>", unsafe_allow_html=True)
    if result.get("same_fight"):
        st.caption("⚠️ Holds **both corners of one fight** — one guaranteed "
                   "winner, one guaranteed loser. The objective chose it; in a "
                   "five-round main event both corners bank 25 minutes of stats.")
    if mode == "cash":
        st.caption("Chosen to maximise the chance of clearing a double-up line "
                   "(top 44%) against a simulated field, on the same simulated "
                   "fights — when the chalk loses, the line drops too.")
    else:
        st.caption("Chosen to maximise the chance of a top-1% finish against a "
                   "simulated field. Leverage comes from the simulation, not a "
                   "penalty: a fighter the field ignored lifts you past it on "
                   "the nights he wins. Ownership is a PRIOR until a contest "
                   "export has been fitted — read it as a tilt, not a number.")


def _csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "fighter", "opponent", "salary", "p_win", "p_finish",
                "proj", "floor", "ceil", "own"])
    for mode in ("cash", "gpp"):
        for r in R.lineup_rows(res.get(mode)):
            w.writerow([mode, r["fighter"], r["opponent"], r["salary"], r["p_win"],
                        r["p_finish"], r["proj"], r["floor"], r["ceil"], r["own"]])
    return buf.getvalue().encode()


t_cash, t_gpp, t_board = st.tabs(["💵 CASH", "🚀 GPP", "📋 Board"])
with t_cash:
    render(res.get("cash"), "cash")
with t_gpp:
    render(res.get("gpp"), "gpp")
with t_board:
    pool = sorted(res["pool"], key=lambda d: -d["proj"])
    body = "".join(
        f"<tr><td class='nm'>{d['name']}<small>vs {d['opponent']}"
        f"{' · 5 rds' if d['sched'] == 5 else ''}"
        f"{' · debut' if not d.get('key') else ''}"
        f"{' · no mkt' if d['source'] in ('salary', 'none') else ''}</small></td>"
        f"<td class='num'>{d['salary']:,}</td>"
        f"<td class='num'>{_pct(d['p_win'])}</td>"
        f"<td class='num'>{_pct(d['p_finish'])}</td>"
        f"<td class='num'>{d['proj']:.0f}</td>"
        f"<td class='num'>{d['ceil']:.0f}</td>"
        f"<td class='num'>{d['own']:.0f}%</td></tr>" for d in pool)
    st.markdown("<div class='lu-wrap'><table class='lu'>"
                "<tr><th>Fighter</th><th>$</th><th>Win</th><th>Fin</th>"
                "<th>Pts</th><th>p90</th><th>Own</th></tr>"
                f"{body}</table></div>", unsafe_allow_html=True)
    st.caption(
        "**Win** and **Fin** (win inside the distance) are de-biased sportsbook "
        "probabilities: the method market underprices decisions by 3–4 points "
        "in every era since 2013, and a finish pays 45–90 to a decision's 30. "
        "**Pts** is the simulated mean; on 3,454 held-out fighter-fights it "
        "beat DK's own FPPF average by 6.5 points of MAE (30.0 vs 36.5). "
        "**Own** is a prior.")

st.download_button("⬇️ Download both lineups (CSV)", data=_csv(),
                   file_name=f"mma_lineups_{res['gid']}.csv", mime="text/csv")
