"""pages/11_💸_Overlay.py -- guaranteed DK contests that are not filling."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pagereload import fresh  # noqa: E402

fresh()   # a Cloud deploy reloads every repo module (edge/pagereload.py)
from scripts import dfs_overlay as O  # noqa: E402

st.set_page_config(page_title="Overlay finder", page_icon="💸", layout="centered")
st.title("💸 Overlay finder")
st.caption("Guaranteed contests pay their prize table even if they don't fill — but only to "
           "places that have an entrant. **$ back** is what an average entry gets back per $1 "
           "if the contest locked right now; above 1.00, DraftKings is adding money. Most "
           "contests fill in the last hour, so check close to lock.")

c1, c2, c3 = st.columns(3)
sports = c1.multiselect("Sports", O.SPORTS, default=["NHL", "NFL", "CFB", "NAS", "MMA", "MLB"])
# A range, not just a ceiling: the lobby is mostly 10c-$1 contests, so a
# floor is what makes the list short enough to scroll on a phone.
min_fee, max_fee = c2.select_slider("Entry fee ($)", options=[0.1, 0.25, 1, 2, 3, 5, 10, 15, 25, 50],
                                    value=(0.1, 5))
hours = c3.select_slider("Locks within (h)", options=[0.5, 1, 2, 3, 6, 12, 24], value=3)
only_dbl = st.toggle("Double-ups only", value=False)
min_ret = st.select_slider("Min $ back per $1", options=[0.0, 1.0, 1.2, 1.5, 2.0], value=1.0)


@st.cache_data(ttl=120, show_spinner=False)
def _scan(sports: tuple, max_fee: float, hours: float):
    now = datetime.now(timezone.utc)
    found, errs = [], []
    for s in sports:
        try:
            found += O.rows(s, now, max_fee, hours, classic_only=True)
        except Exception as exc:                            # noqa: BLE001
            errs.append(f"{s}: {exc}")
    O.price(found)
    return now, found, errs


if st.button("🔄 Refresh", width="stretch"):
    _scan.clear()
with st.spinner("Reading the DraftKings lobby…"):
    now, found, errs = _scan(tuple(sports), float(max_fee), float(hours))
for e in errs:
    st.warning(e)
rows = [r for r in found if r["fee_ok"] and r["fee"] >= min_fee and r["return"] is not None and r["return"] >= min_ret
        and (r["dbl"] or not only_dbl)]
rows.sort(key=lambda r: -r["return"])
unpriced = sum(1 for r in found if r["fee_ok"] and r["fee"] >= min_fee and r["return"] is None)
et = ZoneInfo("America/New_York")
st.markdown(f"**{len(rows)}** contests · lobby read {now.astimezone(et):%-I:%M %p} ET")
if unpriced:
    st.caption(f"{unpriced} non-double-up contests have no payout table here (DraftKings "
               "blocks that lookup from the cloud); the desktop scanner prices them.")
for r in rows[:60]:
    mins = (r["lock"] - now).total_seconds() / 60
    cr = f" · cashes **{r['cash_rate']:.0%}**" if r["cash_rate"] is not None else ""
    rz = " · ⚠️ resizable" if r["resizable"] else ""
    st.markdown(f"**{r['return']:.2f}×** · {r['sport']} · ${r['fee']:g} · "
                f"{r['nt']}/{r['m']} entries{cr} · locks in {mins:.0f} min{rz}  \n"
                f"<span style='color:#999;font-size:13px'>{r['name']} · #{r['id']}</span>",
                unsafe_allow_html=True)
