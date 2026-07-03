"""app.py — portable DK MLB DFS lineup generator (Streamlit front-end).

Same shape as the strikeouts K-prop app: push to GitHub, deploy on Streamlit
Community Cloud, open it on your phone before lock.

Two refresh modes mirror the CLI's `--from-cache` flag exactly:

  * DEFAULT (cache / 0 credits): DK salaries + confirmed batting lineups come
    from FREE public APIs and refresh live every time — these are what change as
    lock approaches. Paid pitcher props are served from data/cache/, so pressing
    "Refresh" as many times as you like costs 0 Odds-API credits.
  * "Pull fresh pitcher props" (spends credits): does the one paid live pull of
    sportsbook props, then caches them to disk so every later refresh is free.

Run locally:  streamlit run app.py
Key:  ODDS_API_KEY via .env, Streamlit secrets, or the sidebar box.
"""
from __future__ import annotations

import datetime
import io
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from edge.client import OddsAPIClient  # noqa: E402
from edge import dfs, dfs_opt, dfs_run  # noqa: E402

CACHE_DIR = ROOT / "data/cache"
LEDGER = ROOT / "data/odds_api_credits.json"


# ── API key: env / .env / Streamlit secrets, overridable in the sidebar ──────
def _bootstrap_key() -> None:
    if os.environ.get("ODDS_API_KEY"):
        return
    try:
        if "ODDS_API_KEY" in st.secrets:  # Streamlit Cloud
            os.environ["ODDS_API_KEY"] = st.secrets["ODDS_API_KEY"]
            return
    except Exception:
        pass
    for p in (ROOT / ".env", Path("/home/asr/Downloads/strikeouts/.env")):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line.startswith("ODDS_API_KEY") and "=" in line:
                    os.environ["ODDS_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return


_bootstrap_key()

st.set_page_config(page_title="DK MLB DFS Lineups", page_icon="⚾", layout="wide",
                   initial_sidebar_state="auto")

# Tight, mobile-first CSS so a full 10-man lineup fits one iPhone screen with no
# nested scroll (the whole point — DK's own app forces scrolling).
st.markdown("""
<style>
.block-container {padding-top: 2.0rem; padding-bottom: 2rem;}
h1 {font-size: 1.55rem !important; margin-bottom: .1rem;}
.summary {font-size: 13px; color: #9aa4b2; line-height: 1.55; margin: .1rem 0 .5rem;}
.ph-badge {background:#4a3a00; border:1px solid #8a6a00; color:#ffd97a; border-radius:6px;
           padding:6px 10px; font-weight:600; font-size:13px; margin:2px 0 8px;}
.lu-tot {font-size:13px; color:#c7d0dd; margin:2px 0 6px;}
.lu-wrap {overflow-x:auto;}
table.lu {width:100%; border-collapse:collapse; font-size:14px;}
table.lu th {text-align:left; color:#7f8a9c; font-weight:600; font-size:11px; text-transform:uppercase;
             padding:2px 6px; border-bottom:1px solid rgba(255,255,255,.16);}
table.lu td {padding:5px 6px; border-bottom:1px solid rgba(255,255,255,.07);}
table.lu td.pos {color:#3fb079; font-weight:700; width:32px;}
table.lu td.team {color:#9aa4b2; width:42px;}
table.lu td.nm {white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:150px;}
table.lu td.num {text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;}
</style>
""", unsafe_allow_html=True)

# --- PLACEHOLDER lineups (clearly labeled sample data; NOT today's slate) so the
# on-screen layout is visible before confirmed batting orders post. ---
PLACEHOLDER_CASH = [
    {"slot": "P", "player": "Tarik Skubal", "team": "DET", "salary": 9500, "proj": 21.8, "ceil": 27, "own": 22},
    {"slot": "P", "player": "Logan Webb", "team": "SF", "salary": 7400, "proj": 17.2, "ceil": 21, "own": 15},
    {"slot": "C", "player": "Will Smith", "team": "LAD", "salary": 3900, "proj": 8.2, "ceil": 13, "own": 9},
    {"slot": "1B", "player": "V. Pasquantino", "team": "KC", "salary": 3800, "proj": 8.0, "ceil": 12, "own": 7},
    {"slot": "2B", "player": "Jose Altuve", "team": "HOU", "salary": 4200, "proj": 8.7, "ceil": 13, "own": 11},
    {"slot": "3B", "player": "Manny Machado", "team": "SD", "salary": 4100, "proj": 8.3, "ceil": 12, "own": 8},
    {"slot": "SS", "player": "G. Henderson", "team": "BAL", "salary": 4600, "proj": 9.4, "ceil": 14, "own": 12},
    {"slot": "OF", "player": "Aaron Judge", "team": "NYY", "salary": 5000, "proj": 10.8, "ceil": 16, "own": 24},
    {"slot": "OF", "player": "Corbin Carroll", "team": "ARI", "salary": 3900, "proj": 9.0, "ceil": 13, "own": 9},
    {"slot": "OF", "player": "Riley Greene", "team": "DET", "salary": 3500, "proj": 8.1, "ceil": 12, "own": 6},
]
PLACEHOLDER_GPP = [
    {"slot": "P", "player": "Tarik Skubal", "team": "DET", "salary": 9500, "proj": 21.8, "ceil": 27, "own": 20},
    {"slot": "P", "player": "C. Sánchez", "team": "PHI", "salary": 7800, "proj": 18.5, "ceil": 23, "own": 12},
    {"slot": "C", "player": "Will Smith", "team": "LAD", "salary": 3900, "proj": 8.2, "ceil": 13, "own": 14},
    {"slot": "1B", "player": "Freddie Freeman", "team": "LAD", "salary": 4300, "proj": 8.9, "ceil": 14, "own": 16},
    {"slot": "2B", "player": "Ketel Marte", "team": "ARI", "salary": 4200, "proj": 8.6, "ceil": 13, "own": 10},
    {"slot": "3B", "player": "Max Muncy", "team": "LAD", "salary": 3600, "proj": 7.9, "ceil": 14, "own": 8},
    {"slot": "SS", "player": "Elly De La Cruz", "team": "CIN", "salary": 5200, "proj": 10.4, "ceil": 17, "own": 22},
    {"slot": "OF", "player": "T. Hernández", "team": "LAD", "salary": 4100, "proj": 8.8, "ceil": 15, "own": 11},
    {"slot": "OF", "player": "Corbin Carroll", "team": "ARI", "salary": 4000, "proj": 9.0, "ceil": 14, "own": 9},
    {"slot": "OF", "player": "Lawrence Butler", "team": "ATH", "salary": 3300, "proj": 7.4, "ceil": 12, "own": 6},
]


def make_client(live: bool) -> OddsAPIClient:
    """live=False -> dry-run + effectively infinite TTL (reads cache, spends 0).
    live=True -> real paid pull (10 min TTL), same as the CLI without --from-cache."""
    return OddsAPIClient(cache_dir=CACHE_DIR, ledger_path=LEDGER,
                         dry_run=not live, live_ttl=600 if live else 10**9)


# ── cached wrappers (Streamlit session cache; the "Refresh" button clears it) ─
@st.cache_data(ttl=300, show_spinner=False)
def cached_slate_names(_nonce: int) -> list[tuple]:
    return dfs.list_slate_names(dfs.mlb_draft_groups())


@st.cache_data(ttl=600, show_spinner="Building slate… (salaries + lineups are free; props from cache)")
def cached_build(date: str, draft_group, iters: int, live: bool, key_fingerprint: str) -> dict:
    """key_fingerprint is only in the signature so changing the API key busts the
    cache; the client reads the real key from the environment."""
    return dfs_run.build_slate(make_client(live), date, draft_group=draft_group, iters=iters)


def _lineup_rows(res: dict, mode: str) -> list[dict]:
    r = res.get(mode)
    if not r:
        return []
    rows = []
    for p, slot in sorted(r["lineup"], key=lambda x: dfs_opt.SLOTS.index(x[1])):
        rows.append({"slot": slot, "player": p["name"], "team": p["team"],
                     "salary": p["salary"], "proj": p["proj"], "ceil": p["ceiling"],
                     "own%": round(p.get("own", 0), 1), "src": p["conf"]})
    return rows


def _lineup_csv(res: dict) -> bytes:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["mode", "slot", "player", "team", "salary", "proj", "ceiling", "own"])
    for mode in ("cash", "gpp"):
        for p, slot in sorted(res[mode]["lineup"], key=lambda x: dfs_opt.SLOTS.index(x[1])) if res.get(mode) else []:
            w.writerow([mode, slot, p["name"], p["team"], p["salary"], p["proj"], p["ceiling"], p.get("own")])
    return buf.getvalue().encode()


# ── sidebar ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚾ DK MLB DFS")

    if st.button("🔄 Refresh (free)", use_container_width=True,
                 help="Re-fetch salaries + confirmed lineups (0 credits). Props stay from cache."):
        st.cache_data.clear()
        st.session_state.live = False
        st.rerun()

    if st.button("💰 Pull fresh pitcher props (spends credits)", use_container_width=True,
                 help="One paid live pull of sportsbook props for the projections, then cached."):
        st.cache_data.clear()
        st.session_state.live = True
        st.rerun()

    st.divider()
    api_key = st.text_input("ODDS_API_KEY", value=os.environ.get("ODDS_API_KEY", ""),
                            type="password", help="Stored only for this session.")
    if api_key:
        os.environ["ODDS_API_KEY"] = api_key

    slate_date = st.date_input("Slate date", value=datetime.date.today()).isoformat()

    names = []
    try:
        names = cached_slate_names(0)
    except Exception as e:
        st.caption(f"(couldn't list slates: {e})")
    labels = ["Main (auto)"] + [f"{n}  {s}Z  {gc}g" for n, i, s, gc in names]
    choice = st.selectbox("Slate", labels, index=0)
    draft_group = None if choice == "Main (auto)" else names[labels.index(choice) - 1][0]

    iters = st.slider("Optimizer restarts", 200, 3000, 800, step=200,
                      help="More restarts = closer to optimal, a bit slower.")

    preview = st.checkbox("Preview layout (placeholder)", value=False,
                          help="Show sample CASH/GPP lineups so you can see the on-screen "
                               "layout before real batting orders post.")

    rem = OddsAPIClient(cache_dir=CACHE_DIR, ledger_path=LEDGER).remaining_credits() \
        if os.environ.get("ODDS_API_KEY") else None
    if rem is not None:
        st.metric("Odds-API credits remaining", f"{rem:,}")


# ── main ─────────────────────────────────────────────────────────────────
st.title("DK MLB DFS Lineup Generator")

if not os.environ.get("ODDS_API_KEY"):
    st.warning("No ODDS_API_KEY set. Salaries + confirmed lineups (free) still work; "
               "pitcher projections need a key or a warm cache.")

live = st.session_state.get("live", False)
mode_label = "LIVE — spending credits on props" if live else "CACHE — 0 credits (props from disk)"
st.caption(f"Mode: **{mode_label}**  ·  slate date {slate_date}")

try:
    res = cached_build(slate_date, draft_group, iters, live,
                       key_fingerprint=(os.environ.get("ODDS_API_KEY", "")[-6:]))
finally:
    # only spend once; any later manual refresh falls back to the disk cache.
    st.session_state.live = False

if res.get("error"):
    st.error(f"{res['error']}. Available now: {', '.join(res.get('available', [])) or '—'}")
    st.stop()

if res.get("unpriced"):
    st.warning("This slate isn't priced yet (no salaries). Upcoming slates:")
    st.dataframe([{"slate": n, "start": s, "games": gc} for n, i, s, gc in res["upcoming"]],
                 use_container_width=True, hide_index=True)
    st.stop()

# compact one-line status (giant metric cards eat the screen on mobile)
rem_txt = f"{res['remaining']:,}" if res["remaining"] is not None else "—"
st.markdown(
    f"<div class='summary'>🧢 <b>{res['salaries_n']}</b> salaries · "
    f"⚾ <b>{len(res['pitchers'])}</b> P priced · "
    f"🧍 <b>{len(res['hitters'])}</b> hitters confirmed · "
    f"💳 spent <b>{res['spent']}</b> · left <b>{rem_txt}</b> cr</div>",
    unsafe_allow_html=True)

lineups_ready = res.get("cash") is not None or res.get("gpp") is not None
show_ph = preview or not lineups_ready
if not lineups_ready and not preview:
    st.info("Confirmed batting orders aren't posted yet (~3–4h before first pitch), so real lineups "
            "can't build. Showing a **placeholder** below so you can see the layout — near lock, tap "
            "**🔄 Refresh (free)** and real CASH + GPP lineups appear automatically.")


def _totals(rows):
    return (round(sum(r["proj"] for r in rows), 1),
            round(sum(r.get("ceil", r["proj"]) for r in rows), 1),
            sum(r["salary"] for r in rows),
            sum(r.get("own", 0) for r in rows))


def render_compact(rows, placeholder=False):
    """A tight static HTML table — whole 10-man lineup on one iPhone screen, no nested scroll."""
    if placeholder:
        st.markdown("<div class='ph-badge'>⚠️ PLACEHOLDER — sample data, NOT today's lineup</div>",
                    unsafe_allow_html=True)
    proj, ceil, salary, own = _totals(rows)
    st.markdown(f"<div class='lu-tot'>proj <b>{proj}</b> · ceil <b>{ceil}</b> · "
                f"own <b>{own:.0f}%</b> · <b>${salary:,}</b> / 50k</div>", unsafe_allow_html=True)
    body = "".join(
        f"<tr><td class='pos'>{r['slot']}</td><td class='nm'>{r['player']}</td>"
        f"<td class='team'>{r['team']}</td><td class='num'>{r['salary']:,}</td>"
        f"<td class='num'>{r['proj']}</td></tr>" for r in rows)
    st.markdown("<div class='lu-wrap'><table class='lu'>"
                "<tr><th>Pos</th><th>Player</th><th>Tm</th><th>$</th><th>Pts</th></tr>"
                f"{body}</table></div>", unsafe_allow_html=True)


def _rows_for(mode):
    r = res.get(mode)
    if not r:
        return None
    return [{"slot": slot, "player": p["name"], "team": p["team"], "salary": p["salary"],
             "proj": p["proj"], "ceil": p["ceiling"], "own": p.get("own", 0)}
            for p, slot in sorted(r["lineup"], key=lambda x: dfs_opt.SLOTS.index(x[1]))]


t_cash, t_gpp = st.tabs(["💵 CASH", "🚀 GPP"])
with t_cash:
    if show_ph:
        render_compact(PLACEHOLDER_CASH, placeholder=True)
    elif _rows_for("cash"):
        render_compact(_rows_for("cash"))
    else:
        st.caption("Cash lineup not ready.")
with t_gpp:
    if show_ph:
        st.caption("4-man stack + ceiling (sample)")
        render_compact(PLACEHOLDER_GPP, placeholder=True)
    elif _rows_for("gpp"):
        st.caption(f"4-man {res.get('stack_team')} stack + ceiling")
        render_compact(_rows_for("gpp"))
    else:
        st.caption("GPP lineup not ready.")

if lineups_ready and not preview:
    st.download_button("⬇️ Download both lineups (CSV)", data=_lineup_csv(res),
                       file_name=f"dfs_lineups_{slate_date}.csv", mime="text/csv")

# ── pitcher value board ──────────────────────────────────────────────────
with st.expander("Pitcher value board", expanded=res["cash"] is None):
    prows = sorted(
        ({"pitcher": p["name"], "team": p["team"], "salary": p["salary"], "proj": p["proj"],
          "val/1k": round(p["proj"] / (p["salary"] / 1000.0), 2) if p.get("salary") else None}
         for p in res["pitchers"]),
        key=lambda r: -(r["val/1k"] or 0))
    st.dataframe(prows, use_container_width=True, hide_index=True)

# ── full pool ────────────────────────────────────────────────────────────
with st.expander("Full player pool"):
    pool_rows = sorted(
        ({"player": p["name"], "team": p["team"], "pos": "/".join(sorted(p["pos"])),
          "salary": p["salary"], "proj": p["proj"], "ceil": p["ceiling"],
          "own%": round(p.get("own", 0), 1), "src": p["conf"]} for p in res["pool"]),
        key=lambda r: -(r["proj"] or 0))
    st.dataframe(pool_rows, use_container_width=True, hide_index=True)
