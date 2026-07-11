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
from edge import dfs, dfs_opt, dfs_run, dfs_swap  # noqa: E402

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


@st.cache_data(ttl=120, show_spinner=False)
def cached_started(date: str, _nonce: int) -> dict:
    """Which games have locked (first pitch passed) — for late-swap eligibility."""
    return {str(k): v for k, v in dfs_swap.game_started_map(date).items()}


@st.cache_data(ttl=600, show_spinner="Building slate… (salaries + lineups are free; props from cache)")
def cached_build(date: str, draft_group, iters: int, live: bool, key_fingerprint: str,
                 exclude_teams: tuple = ()) -> dict:
    """key_fingerprint is only in the signature so changing the API key busts the
    cache; the client reads the real key from the environment. exclude_teams is
    part of the cache key (a tuple, not a set, so it hashes) -- excluding a team
    is a manual override for when DK voids/doesn't count specific games (caught
    live 2026-07-09: BAL@CHC didn't count for a contest and the generator had no
    way to know), since that's a DK contest-scoring rule no free API exposes."""
    return dfs_run.build_slate(make_client(live), date, draft_group=draft_group, iters=iters,
                               exclude_teams=set(exclude_teams))


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


# ── pinned DK entry: saved to DISK via edge.dfs_swap (not session_state), the
# SAME file scripts/dfs_swap.py reads/writes with --pin — so pinning from the
# phone and pinning from the computer are visible to each other.
# CAVEAT: Streamlit Community Cloud's disk is ephemeral — if the app sleeps
# from inactivity and wakes back up, it restarts from the last git commit and
# this file is gone. That's fine within one sitting (build -> swap through
# lock); for guaranteed next-day grading, also tap the download button, or
# run scripts/dfs_swap.py --pin on your computer as a second copy.
def _entry_csv_bytes(rows: list[dict]) -> bytes:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(list(dfs_swap.ENTRY_COLS))
    for r in rows:
        w.writerow([r.get(c, "") for c in dfs_swap.ENTRY_COLS])
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

    # populated after each successful build (see below) -- empty on the very
    # first load of a session, since we haven't built anything yet to learn
    # the team list from.
    _known_teams = st.session_state.get("all_teams", [])
    _team_status = st.session_state.get("team_status", {})
    exclude_teams = st.multiselect(
        "Exclude teams (voided/postponed games)", options=_known_teams,
        format_func=lambda t: f"{t}  ⚠ {_team_status[t]}" if _team_status.get(t) else t,
        help="DK sometimes doesn't count specific games for a contest (postponement, or a "
             "contest-scoring rule) -- there's no free API that exposes DK's own rule, so this "
             "is a manual override. A ⚠ next to a team means its actual MLB game looked "
             "non-normal (e.g. Postponed) the last time this slate was built.")

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
                       key_fingerprint=(os.environ.get("ODDS_API_KEY", "")[-6:]),
                       exclude_teams=tuple(sorted(exclude_teams)))
finally:
    # only spend once; any later manual refresh falls back to the disk cache.
    st.session_state.live = False

if res.get("all_teams"):
    # feeds the sidebar multiselect's options on the NEXT rerun -- empty on
    # the very first load, since nothing's been built yet to learn this from.
    st.session_state["all_teams"] = res["all_teams"]
    st.session_state["team_status"] = res["team_status"]

if res.get("error"):
    st.error(f"{res['error']}. Available now: {', '.join(res.get('available', [])) or '—'}")
    st.stop()

if exclude_teams:
    st.caption(f"🚫 Excluding: {', '.join(exclude_teams)}")
flagged = {t: s for t, s in res.get("team_status", {}).items() if s and t not in exclude_teams}
if flagged:
    st.warning("⚠ Non-normal game status detected — consider excluding: " +
              ", ".join(f"{t} ({s})" for t, s in flagged.items()))

if res.get("unpriced"):
    st.warning("This slate isn't priced yet (no salaries). Upcoming slates:")
    st.dataframe([{"slate": n, "start": s, "games": gc} for n, i, s, gc in res["upcoming"]],
                 use_container_width=True, hide_index=True)
    st.stop()

# log to disk so scripts/dfs_grade.py always has something to grade, whether
# this build came from the CLI or the app. Fingerprint-guarded so a rerun that
# reuses the same cached_build result doesn't rewrite the file every widget click.
_fp = (res["gid"], res["salaries_n"], len(res["hitters"]), res["spent"])
if st.session_state.get("_logged_fp") != _fp:
    dfs_run.log_forward_test(ROOT, slate_date, res["is_main"], res["gid"], res["pool"], res.get("cash"), res.get("gpp"),
                             games=res.get("games"))
    st.session_state["_logged_fp"] = _fp

# compact one-line status (giant metric cards eat the screen on mobile)
rem_txt = f"{res['remaining']:,}" if res["remaining"] is not None else "—"
n_proj = sum(1 for h in res["hitters"] if not h.get("confirmed", True))
n_conf = len(res["hitters"]) - n_proj
hit_txt = f"<b>{n_conf}</b> conf" + (f" · <b>{n_proj}</b> proj*" if n_proj else "")
st.markdown(
    f"<div class='summary'>🧢 <b>{res['salaries_n']}</b> salaries · "
    f"⚾ <b>{len(res['pitchers'])}</b> P priced · "
    f"🧍 {hit_txt} hitters · "
    f"💳 spent <b>{res['spent']}</b> · left <b>{rem_txt}</b> cr</div>",
    unsafe_allow_html=True)
if n_proj:
    st.markdown("<div class='summary'>* <b>proj</b> = projected batting order (team's lineup not "
                "posted yet). Lineups can build now; tap 🔄 Refresh as orders drop, then use "
                "<b>Late-swap</b> below.</div>", unsafe_allow_html=True)

lineups_ready = res.get("cash") is not None or res.get("gpp") is not None
show_ph = preview or not lineups_ready
if not lineups_ready and not preview:
    # build_slate needs >=2 pitchers AND >=8 hitters to build at all -- name
    # whichever side is actually short, instead of always blaming "batting
    # orders." Found live 2026-07-11: a morning build showed the placeholder
    # because DraftKings hadn't posted the FULL pitcher prop set (outs/ER/
    # hits/BB/win -- not just strikeouts) for most of that day's starters yet,
    # not because of hitter lineups at all; the old message pointed at the
    # wrong cause every time pitchers were the actual blocker.
    n_p, n_h = len(res["pitchers"]), len(res["hitters"])
    reasons = []
    if n_p < 2:
        reasons.append(f"only **{n_p} pitcher(s)** have a full prop board posted by DraftKings so far "
                       "(needs pitcher_outs + pitcher_strikeouts both live, not just one) — sportsbooks "
                       "stagger which starters get props posted through the morning/afternoon")
    if n_h < 8:
        reasons.append(f"only **{n_h} hitter(s)** have a confirmed or projectable batting order "
                       "(confirmed orders post ~3–4h before first pitch)")
    why = " and ".join(reasons) if reasons else "the pool is otherwise too thin to build a valid roster"
    st.info(f"Lineups can't build yet — {why}. Showing a **placeholder** below so you can see the "
            "layout. Tap **🔄 Refresh (free)** periodically; if pitchers are the blocker, spending "
            "credits again won't help until DK posts more props (this is disk-cached free once it's "
            "up), only time will.")


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
        f"<tr><td class='pos'>{r['slot']}</td>"
        f"<td class='nm'>{r['player']}{' <span style=\"color:#ffd97a\">*</span>' if r.get('projected') else ''}</td>"
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
             "proj": p["proj"], "ceil": p["ceiling"], "own": p.get("own", 0),
             "pos": "/".join(sorted(p["pos"])), "game": p.get("game", ""),
             "conf": p.get("conf", ""), "projected": not p.get("confirmed", True)}
            for p, slot in sorted(r["lineup"], key=lambda x: dfs_opt.SLOTS.index(x[1]))]


def save_entry_button(mode, rows):
    """Pin the lineup you actually entered on DK (saved to disk, shared with
    scripts/dfs_swap.py --pin on your computer) so later refreshes/sessions
    can late-swap it and it can be graded tomorrow."""
    saved = dfs_swap.load_pinned_entry(ROOT, slate_date, mode)
    is_saved = saved and {r["player"] for r in saved} == {r["player"] for r in rows}
    label = "📌 Saved as my DK entry ✓" if is_saved else "📌 Save this as my DK entry"
    col1, col2 = st.columns([3, 2])
    with col1:
        if st.button(label, key=f"save_{mode}", use_container_width=True,
                     help="Saved to disk. Tap 🔄 Refresh as lineups post — Late-swap flags anyone ruled out."):
            dfs_swap.save_pinned_entry(ROOT, slate_date, mode, rows)
            st.rerun()
    with col2:
        st.download_button("⬇️ backup copy", data=_entry_csv_bytes(rows), key=f"dl_entry_{mode}",
                           file_name=f"dfs_entry_{slate_date}_{mode}.csv", mime="text/csv",
                           use_container_width=True,
                           help="The saved copy above can be lost if the app sleeps overnight — "
                                "keep this file too if you want guaranteed next-day grading.")


def render_swaps():
    st.caption("Pin your DK entry on the CASH/GPP tab, then tap 🔄 Refresh (free) as orders post. "
               "Anyone whose team posts a lineup without them shows here with fitting replacements. "
               "DK locks each player at THEIR game's first pitch.")
    started = cached_started(slate_date, 0)
    any_saved = False
    for mode in ("cash", "gpp"):
        saved = dfs_swap.load_pinned_entry(ROOT, slate_date, mode)
        if not saved:
            continue
        any_saved = True
        st.markdown(f"**{mode.upper()} entry**")
        recs = dfs_swap.suggest_swaps(saved, res["hitters"], started, mode=mode, top=4)
        outs = [r for r in recs if r["status"] == "out"]
        holds = [r for r in recs if r["status"] == "hold"]
        upgraded = [r for r in recs if r["status"] == "confirmed" and r.get("was_projected")]
        if not outs:
            st.success(f"No swaps needed — {len(upgraded)} projected pick(s) confirmed in, "
                       f"{len(holds)} still awaiting their team's lineup.")
        for rec in outs:
            if rec["locked"]:
                st.error(f"✗ {rec['player']} ({rec['team']}) is OUT and their game already locked — stuck at 0.")
                continue
            st.warning(f"✗ {rec['player']} ({rec['team']}, ${rec['salary']:,}) OUT of the posted order — "
                       f"replace with ≤ ${rec['max_salary']:,}:")
            if rec["suggestions"]:
                st.dataframe([{"replacement": s["name"], "team": s["team"], "salary": s["salary"],
                               mode: s["val"], "own%": s["own"], "stack": "✓" if s["same_team"] else ""}
                              for s in rec["suggestions"]], use_container_width=True, hide_index=True)
            else:
                st.caption("(no eligible replacement fits the freed salary / unlocked games)")
        if holds:
            st.caption("Still projected (team lineup not posted): " + ", ".join(r["player"] for r in holds))
    if not any_saved:
        st.info("No saved entry yet. Build a lineup, tap **📌 Save this as my DK entry** on the CASH or "
                "GPP tab, then return here after tapping 🔄 Refresh as official lineups post. (Pinning from "
                "`scripts/dfs_swap.py --pin` on your computer works too — same file, either device sees it.)")


t_cash, t_gpp, t_swap = st.tabs(["💵 CASH", "🚀 GPP", "🔁 Late-swap"])
with t_cash:
    if show_ph:
        render_compact(PLACEHOLDER_CASH, placeholder=True)
    elif _rows_for("cash"):
        render_compact(_rows_for("cash"))
        save_entry_button("cash", _rows_for("cash"))
    else:
        st.caption("Cash lineup not ready.")
with t_gpp:
    if show_ph:
        st.caption("4-man stack + ceiling (sample)")
        render_compact(PLACEHOLDER_GPP, placeholder=True)
    elif _rows_for("gpp"):
        # Report the lineup's ACTUAL team composition, not the construction
        # target -- found live 2026-07-11 that the secondary stack can fall
        # short of its target n (position conflicts with the primary stack),
        # and a caption asserting "3-man X stack" when only 1 X hitter made
        # the final lineup would be actively misleading, not just imprecise.
        import collections
        teams = collections.Counter(r["team"] for r in _rows_for("gpp") if "P" not in r["pos"])
        parts = [f"{n}-man {t}" for t, n in sorted(teams.items(), key=lambda kv: -kv[1]) if n > 1]
        st.caption(" + ".join(parts) + " stack" if parts else "no multi-team stack this build")
        render_compact(_rows_for("gpp"))
        save_entry_button("gpp", _rows_for("gpp"))
    else:
        st.caption("GPP lineup not ready.")
with t_swap:
    render_swaps()

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
