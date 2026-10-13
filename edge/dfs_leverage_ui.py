"""The GPP leverage view every DFS page shares (Streamlit).

    contest = contest_picker("NHL", gid, key="nhl")      # None, or field/fee/payouts
    pick = leverage_view(analysis, key="nhl")            # the candidate to render

`analysis` is edge/dfs_leverage.analyze()'s dict (edge/dfs_leverage_sports.py
builds it per sport). The view is a chart -- every candidate lineup and a
sample of the simulated field, summed ownership across, points up -- and a
chooser from the chalkiest lineup to the most contrarian one, starting on the
best expected payout for the contest when one is given.
"""
from __future__ import annotations

import streamlit as st

from edge import dfs_contest

#: DK lobby code per sport name used by the pages.
LOBBY = {"NHL": "NHL", "NFL": "NFL", "NCAAF": "CFB", "NASCAR": "NAS", "MMA": "MMA", "MLB": "MLB"}

#: Chart colours -- reference palette, dark steps (the app's theme is dark):
#: field = recessive neutral, ours = categorical slot 1, best = slot 2.
C_FIELD, C_OURS, C_BEST, C_RING = "#8a8f98", "#3987e5", "#d95926", "#0e1117"


@st.cache_data(ttl=300, show_spinner=False)
def _lobby(code: str) -> list[dict]:
    """DK's public lobby (www.draftkings.com -- reachable from Streamlit Cloud,
    unlike api.draftkings.com)."""
    from edge import dfs
    return dfs._get(f"https://www.draftkings.com/lobby/getcontests?sport={code}").get("Contests", [])


@st.cache_data(ttl=3600, show_spinner=False)
def _lookup(cid: str) -> dict | None:
    """DK's own payout table. Blocked from datacenter IPs (DRAFTKINGS_ACCESS.md),
    so on the cloud this is None and the curve is pinned to the prize pool."""
    try:
        return dfs_contest.lookup(cid)
    except Exception:                                       # noqa: BLE001
        return None


def _is_cash(c: dict) -> bool:
    attr = c.get("attr") or {}
    name = (c.get("n") or "").lower()
    return (attr.get("IsDoubleUp") == "true" or attr.get("IsFiftyfifty") == "true"
            or "double up" in name or "50/50" in name)


def slate_gpps(sport: str, gid) -> list[dict]:
    """The GPPs on this draft group, biggest first."""
    try:
        rows = _lobby(LOBBY.get(sport, sport))
    except Exception:                                       # noqa: BLE001
        return []
    out = [c for c in rows if str(c.get("dg")) == str(gid) and not _is_cash(c)]
    return sorted(out, key=lambda c: -int(c.get("m") or c.get("nt") or 0))


def _from_lobby(c: dict) -> dict:
    cap, now = int(c.get("m") or 0), int(c.get("nt") or 0)
    field = cap if 0 < cap < 10_000_000 else now
    info = _lookup(str(c["id"]))
    return {"id": str(c["id"]), "name": c.get("n"), "field": max(field, 2),
            "fee": float(c.get("a") or 0), "prize_pool": float(c.get("po") or 0) or None,
            "payouts": tuple(tuple(p) for p in info["payouts"]) if info and info.get("payouts")
            else None, "max_entries": c.get("mec")}


def contest_picker(sport: str, gid, key: str) -> dict | None:
    """The GPP this lineup is for: one of the slate's contests from DK's lobby,
    a pasted link / ID, or a typed field size. None if none is given."""
    with st.expander("🎯 Your contest", expanded=True):
        gpps = slate_gpps(sport, gid)
        labels = ["— pick the contest you entered —"] + [
            f"{c.get('n')} · ${float(c.get('a') or 0):g} · "
            f"{int(c.get('nt') or 0):,}/{int(c.get('m') or 0):,}" for c in gpps]
        choice = st.selectbox("Contest on this slate", labels, key=f"{key}_lev_contest",
                              help="DK's lobby for this draft group, biggest first. The field "
                                   "size and prize pool decide how much ownership is worth.")
        if choice != labels[0]:
            out = _from_lobby(gpps[labels.index(choice) - 1])
            st.caption(f"{out['field']:,} entries planned for · ${out['fee']:g} · "
                       + ("DK payout table" if out["payouts"] else
                          f"payout curve fitted to the ${out['prize_pool'] or 0:,.0f} pool"))
            return out
        c1, c2 = st.columns([3, 2])
        text = c1.text_input("…or paste a DK contest link / ID", key=f"{key}_lev_link")
        field = c2.number_input("…or field size", min_value=0, value=0, step=100,
                                key=f"{key}_lev_field")
        cid = dfs_contest.contest_id(text)
        if cid:
            info = _lookup(cid)
            hit = next((c for c in _safe_lobby(sport) if str(c.get("id")) == cid), None)
            if hit and _is_cash(hit):
                st.warning("That is a double-up / 50-50 — play the 💵 CASH lineup.")
                return None
            if hit and str(hit.get("dg")) != str(gid):
                st.warning(f"That contest is on draft group {hit.get('dg')}, not this slate "
                           f"({gid}). Pick its slate in the sidebar.")
                return None
            if hit:
                return _from_lobby(hit)
            if info:
                if info.get("sport") and info["sport"] != sport:
                    st.warning(f"That is a {info['sport']} contest, not {sport}.")
                    return None
                if info.get("type") == "cash":
                    st.warning("That is a double-up / 50-50 — play the 💵 CASH lineup.")
                    return None
                if info.get("draft_group") and str(info["draft_group"]) != str(gid):
                    st.warning(f"That contest is on draft group {info['draft_group']}, not this "
                               f"slate ({gid}). Pick its slate in the sidebar.")
                    return None
                return {"id": cid, "name": info.get("name"),
                        "field": max(dfs_contest.expected_field(info), 2),
                        "fee": float(info.get("entry_fee") or 0),
                        "prize_pool": info.get("prize_pool"),
                        "payouts": tuple(tuple(p) for p in info["payouts"]) or None}
            st.warning(f"Contest {cid} isn't in DK's lobby for this sport and its details "
                       "can't be fetched from here -- type the field size instead.")
        if field >= 2:
            return {"id": None, "name": None, "field": int(field), "fee": 1.0,
                    "prize_pool": None, "payouts": None}
    return None


def _safe_lobby(sport: str) -> list[dict]:
    try:
        return _lobby(LOBBY.get(sport, sport))
    except Exception:                                       # noqa: BLE001
        return []


def _label(c: dict, best: bool, contest) -> str:
    s = f"{'⭐ ' if best else ''}{c['own']:.0f}% own · ceil {c['ceil']:.0f} · proj {c['proj']:.0f}"
    if contest and c.get("rel") is not None:
        s += f" · EV {c['rel']:.0f}"
    if c.get("tag"):
        s += f" · {c['tag']}"
    if not c.get("frontier"):
        s += " · off frontier"
    return s


def chart(analysis: dict, y: str, selected: int | None) -> None:
    import altair as alt
    import pandas as pd
    best = analysis.get("best")
    ytitle = (f"Ceiling ({analysis.get('ceil_pct', 95):.0f}th pct)" if y == "ceil"
              else "Projected points")
    field = pd.DataFrame(analysis.get("field") or [])
    rows = []
    for i, c in enumerate(analysis["cands"]):
        rows.append({"own": c["own"], "y": c[y], "proj": c["proj"], "ceil": c["ceil"],
                     "kind": "Best EV" if (i == best and analysis.get("contest"))
                     else ("Top ceiling" if i == best else "Our lineups"),
                     "frontier": bool(c.get("frontier")), "sel": i == selected,
                     "ev": c.get("rel"), "label": c.get("tag") or ""})
    ours = pd.DataFrame(rows)
    kinds = ["Sim field", "Our lineups", "Best EV" if analysis.get("contest") else "Top ceiling"]
    color = alt.Color("kind:N", title=None,
                      legend=alt.Legend(orient="bottom", symbolOpacity=1, labelLimit=120),
                      scale=alt.Scale(domain=kinds, range=[C_FIELD, C_OURS, C_BEST]))
    x = alt.X("own:Q", title="Summed ownership % (lower = more contrarian)",
              scale=alt.Scale(zero=False, nice=True))
    yy = alt.Y("y:Q", title=ytitle, scale=alt.Scale(zero=False, nice=True))
    layers = []
    if not field.empty:
        field = field.assign(y=field[y], kind="Sim field")
        layers.append(alt.Chart(field).mark_circle(size=16, opacity=0.35).encode(
            x=x, y=yy, color=color,
            tooltip=[alt.Tooltip("own:Q", title="Own %"), alt.Tooltip("proj:Q", title="Proj"),
                     alt.Tooltip("ceil:Q", title="Ceil")]))
    front = ours[ours["frontier"]].sort_values("own")
    if len(front) > 1:
        layers.append(alt.Chart(front).mark_line(strokeWidth=2, color=C_OURS, opacity=0.8)
                      .encode(x=x, y=yy))
    tips = [alt.Tooltip("own:Q", title="Own %"), alt.Tooltip("proj:Q", title="Proj"),
            alt.Tooltip("ceil:Q", title="Ceil")]
    if analysis.get("contest"):
        tips.append(alt.Tooltip("ev:Q", title="EV (100 = best)", format=".0f"))
    layers.append(alt.Chart(ours).mark_circle(size=110, opacity=1, stroke=C_RING, strokeWidth=2)
                  .encode(x=x, y=yy, color=color, tooltip=tips))
    sel = ours[ours["sel"]]
    if not sel.empty:
        layers.append(alt.Chart(sel).mark_circle(size=380, filled=False, stroke="#f0efec",
                                                 strokeWidth=2).encode(x=x, y=yy))
    st.altair_chart(alt.layer(*layers).properties(height=290), width="stretch")


def leverage_view(analysis: dict, key: str) -> dict | None:
    """Chart + chooser. Returns the chosen candidate (its `result` is the
    sport's lineup to render), or None when there is nothing to show."""
    cands = analysis.get("cands") or []
    if not cands:
        st.caption("No GPP lineups to compare on this slate.")
        return None
    best = analysis.get("best") or 0
    order = sorted(range(len(cands)), key=lambda i: -cands[i]["own"])     # chalk first
    labels = [_label(cands[i], i == best, analysis.get("contest")) for i in order]
    y = st.radio("Points axis", ["Ceiling", "Projection"], horizontal=True,
                 key=f"{key}_lev_y", label_visibility="collapsed")
    pick = st.selectbox("Chalk ↔ contrarian", labels, index=order.index(best),
                        key=f"{key}_lev_pick_{(analysis.get('contest') or {}).get('field', 0)}",
                        help="Top = the chalkiest lineup (the optimiser's own), bottom = the "
                             "most contrarian. ⭐ = best expected payout in your contest.")
    chosen = order[labels.index(pick)]
    chart(analysis, "ceil" if y == "Ceiling" else "proj", chosen)
    c = analysis.get("contest")
    if c:
        st.caption(f"⭐ = best expected payout in a {c['field']:,}-entry field. Each lineup faces "
                   "the same simulated games and the same simulated field (drawn from projected "
                   "ownership, part of it optimised). EV is relative, 100 = best here; the "
                   "absolute dollars flatter every lineup alike because the sim trusts our "
                   "projections, so the ORDER is the signal. Real results so far (GPP_META.md, "
                   "108 contests): at equal projection, lower ownership has not raised ROI in "
                   "MLB or NHL — take a contrarian lineup when it costs little or nothing in "
                   "points, not on principle.")
    else:
        st.caption("Pick your contest above to rank these by expected payout — small fields "
                   "favour the chalk, big ones pay for being different. Gray dots are a "
                   "simulated field: a lineup up and to the left of them is contrarian without "
                   "giving up points.")
    return cands[chosen]
