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

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Salaries", res["salaries_n"])
c2.metric("Pitchers priced", len(res["pitchers"]))
c3.metric("Hitters (confirmed)", len(res["hitters"]))
c4.metric("Credits spent", res["spent"])
c5.metric("Credits left", f"{res['remaining']:,}" if res["remaining"] is not None else "—")

if res["cash"] is None and res["gpp"] is None:
    st.info("Confirmed lineups aren't posted yet (they land ~3–4h before first pitch), so the "
            "hitter pool is too thin to build full lineups. Pitcher projections below are ready — "
            "come back near lock and hit **Refresh (free)** to pull the batting orders.")

# ── lineups ─────────────────────────────────────────────────────────────
def render_lineup(title: str, res: dict, mode: str):
    r = res.get(mode)
    if not r:
        return
    own = sum(p.get("own", 0) for p, _ in r["lineup"])
    st.subheader(title)
    st.caption(f"proj **{r['proj']}**  ·  ceiling **{r['ceil']}**  ·  total own **{own:.0f}%**  ·  "
               f"salary **${r['salary']:,}** / $50,000")
    st.dataframe(_lineup_rows(res, mode), use_container_width=True, hide_index=True)


lc, gc = st.columns(2)
with lc:
    render_lineup("💵 CASH (mean / floor)", res, "cash")
with gc:
    st_team = res["stack_team"]
    render_lineup(f"🚀 GPP (4-man {st_team} stack + ceiling)", res, "gpp")

if res.get("cash") or res.get("gpp"):
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
