"""pages/14_🔀_NBA_CourtIQ.py -- who takes a missing NBA player's start and minutes.

Pick a team, mark who is OUT, and see what the team did the nights they all sat: who
started at each slot (did the backup C start, or did the PF slide over?) and every
teammate's minutes and DK points with vs. without. Built from NBA.com's starting fives
and the box scores (edge/nba_courtiq.py); the table comes off GitHub's data branch,
published by the desktop's 06:00 / 14:00 NBA history job.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pagereload import fresh  # noqa: E402

fresh()   # a Cloud deploy reloads every repo module (edge/pagereload.py)
from edge import nba_courtiq as C  # noqa: E402

st.set_page_config(page_title="NBA CourtIQ", page_icon="🔀", layout="centered")
st.title("🔀 NBA CourtIQ")
st.caption("Mark players OUT: what the team did in the games they all missed. Game level "
           "(he sat the whole night), not on-court minutes. Watch **n**: under "
           f"{C.THIN} games without is an anecdote.")


@st.cache_data(ttl=1800, show_spinner=False)
def _courtiq_table():
    from edge import repo_files
    got = repo_files.read(C.OUT)
    if not got:
        return None, "no table published yet"
    src = f"built {got.updated_at.astimezone(ZoneInfo('America/New_York')):%b %-d %-I:%M %p} ET ({got.source})"
    return got.json(), (f"{src} -- {got.problem}" if got.problem else src)


table, src = _courtiq_table()
if not table:
    st.error(f"CourtIQ table unavailable: {src}")
    st.stop()

teams = sorted(table["teams"])
c1, c2 = st.columns([1, 2])
team = c1.selectbox("Team", teams, index=teams.index(st.query_params.get("team", "HOU"))
                    if st.query_params.get("team", "HOU") in teams else 0)
span = c2.radio("Games", ["This season", "Last 2 seasons"], index=1, horizontal=True)
since = "2026-07-01" if span == "This season" else None
# the OUT list: anyone who played for the team this season (or last, if none yet)
cur = C.roster(table, team, since="2026-07-01") or C.roster(table, team)
label = {pid: f"{name} ({m:.0f} min)" for pid, name, _, m in cur}
# ?out=<nba id>,<nba id> opens with them marked (a link from an alert)
pre = [p for p in st.query_params.get("out", "").split(",") if p in label]
out = st.multiselect("OUT", list(label), default=pre, format_func=label.get, max_selections=3)
st.caption(src)
if not out:
    st.info("Pick one or more players who are out tonight.")
    st.stop()

s = C.split(table, team, out, since=since)
nw, nwo = len(s["with"]), len(s["without"])
st.markdown(f"**{nwo}** games without vs **{nw}** with")
if not nwo:
    st.warning("They have never all missed a game in this span.")
    st.stop()
names = table["names"]


def _short(pid):
    parts = [w for w in names.get(pid, pid).split() if w.rstrip(".") not in ("Jr", "Sr", "II", "III", "IV")]
    return parts[-1] if parts else pid


def _top(shares, k=2):
    return ", ".join(f"{_short(p)} {x:.0%}" for p, x in shares[:k]) or "—"


st.subheader("Who starts where")
st.dataframe([{"Slot": sl, "With": _top(s["slots"]["with"].get(sl, []), 1),
               "Without": _top(s["slots"]["without"].get(sl, []))} for sl in C.SLOTS],
             hide_index=True, width="stretch")

st.subheader("Teammates without them")
rows = []
# real splits first; a 1-2 game "without" sample sorts below them
for m in sorted(s["mates"], key=lambda r: r["without"]["n"] < C.THIN):
    a, b = m["with"], m["without"]
    rows.append({"Player": m["name"] + (" ⚠️" if b["n"] < C.THIN else ""), "n": b["n"],
                 "Min": f"{a['min']:.0f}→{b['min']:.0f}" if a else f"–→{b['min']:.0f}",
                 "ΔMin": round(m["dmin"], 1), "ΔDK": round(m["ddk"], 1),
                 "Slot": b["slot"] or "bench", "Start": f"{b['start']:.0%}", "DK/min": f"{b['dkpm']:.2f}"})
st.dataframe(rows, hide_index=True, width="stretch",
             column_config={"ΔMin": st.column_config.NumberColumn(format="%+.1f"),
                            "ΔDK": st.column_config.NumberColumn(format="%+.1f")})
st.caption("With = who started there most with them · Min with→without · ΔDK = DK points without minus with · Start = start rate "
           "without · Slot = where he started most without. ⚠️ fewer than "
           f"{C.THIN} games.")
