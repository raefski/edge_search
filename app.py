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
import platform
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

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


def _et_label(date_str: str, hhmm: str) -> str:
    """'23:05' UTC on `date_str` -> '7:05 PM ET' for display. DK's draft-group
    StartDate (and this app's own slate list) is UTC -- the user asked
    directly whether the slide-list times were ET or UTC (2026-07-11), a real
    point of confusion since nothing on screen said either way. Anchored to
    the picked slate date rather than "today" so this stays correct even if
    a listed slate's own date differs slightly (late games rolling past
    midnight UTC); zoneinfo handles the EDT/EST switch automatically."""
    try:
        h, m = (int(x) for x in hhmm.split(":"))
        dt_utc = datetime.datetime.fromisoformat(date_str).replace(
            hour=h, minute=m, tzinfo=ZoneInfo("UTC"))
        dt_et = dt_utc.astimezone(ZoneInfo("America/New_York"))
        return dt_et.strftime("%-I:%M %p ET")
    except Exception:
        return "?"


def make_client(live: bool) -> OddsAPIClient:
    """live=False -> dry-run + effectively infinite TTL (reads cache, spends 0).
    live=True -> real paid pull (10 min TTL), same as the CLI without --from-cache."""
    return OddsAPIClient(cache_dir=CACHE_DIR, ledger_path=LEDGER,
                         dry_run=not live, live_ttl=600 if live else 10**9)


# ── cached wrappers (Streamlit session cache; the "Refresh" button clears it) ─
@st.cache_data(ttl=300, show_spinner=False)
def cached_slate_names(date: str, _nonce: int) -> list[tuple]:
    # Filtered to `date` (ET-aware, see list_slate_names) -- previously showed
    # every upcoming slate across every date mixed together with no way to
    # tell them apart, since the label only ever showed a bare HH:MM.
    return dfs.list_slate_names(dfs.mlb_draft_groups(), date=date)


@st.cache_data(ttl=120, show_spinner=False)
def cached_started(date: str, _nonce: int) -> dict:
    """Which games have locked (first pitch passed) — for late-swap eligibility."""
    return {str(k): v for k, v in dfs_swap.game_started_map(date).items()}


@st.cache_data(ttl=300, show_spinner=False)
def cached_team_list(date: str, draft_group, _nonce: int) -> tuple[list, dict]:
    """Cheap, exclude_teams-independent team discovery for the sidebar's
    "Exclude teams" multiselect -- see edge.dfs_run.team_list_for_slate."""
    all_teams, team_status, _error = dfs_run.team_list_for_slate(date, draft_group)
    return all_teams, team_status


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

    # Key input BEFORE the action buttons -- found 2026-07-17: a user reported
    # "I had my API key entered" but a live pull still didn't spend credits or
    # pull props. Tested that exact key directly against the real Odds API at
    # the same time: it worked fine (28 real events), ruling out an expired/
    # bad key or an API outage. The most likely remaining explanation is a
    # mobile-browser interaction race -- this code used to check
    # os.environ["ODDS_API_KEY"] in the "Pull fresh props" button (rendered
    # FIRST) before the text_input that actually sets it (rendered AFTER),
    # so a key typed and a button tapped in a way the phone browser merges
    # into a single interaction could see the button's check run against a
    # not-yet-committed value. Reordering removes that window entirely,
    # regardless of whether it was the actual cause this time.
    api_key = st.text_input("ODDS_API_KEY", value=os.environ.get("ODDS_API_KEY", ""),
                            type="password", help="Stored only for this session.")
    if api_key:
        os.environ["ODDS_API_KEY"] = api_key

    if st.button("🔄 Refresh (free)", use_container_width=True,
                 help="Re-fetch salaries + confirmed lineups (0 credits). Props stay from cache."):
        st.cache_data.clear()
        st.session_state.live = False
        st.rerun()

    if st.button("💰 Pull fresh pitcher props (spends credits)", use_container_width=True,
                 help="One paid live pull of sportsbook props for the projections, then cached."):
        if not os.environ.get("ODDS_API_KEY"):
            # A missing key here used to fail silently (build_slate would just
            # come back with 0 pitchers, no explanation of why) -- clear
            # upfront instead, since the whole point of tapping this button is
            # to spend credits on a real pull, not sit at 0.
            st.error("No ODDS_API_KEY set — paste your key above first, then tap this again.")
        else:
            st.cache_data.clear()
            st.session_state.live = True
            st.rerun()

    st.divider()
    slate_date = st.date_input("Slate date", value=datetime.date.today()).isoformat()

    names = []
    try:
        names = cached_slate_names(slate_date, 0)
    except Exception as e:
        st.caption(f"(couldn't list slates: {e})")
    labels = ["Main (auto)"] + [f"{n}  {s}Z ({_et_label(slate_date, s)})  {gc}g" for n, i, s, gc in names]
    choice = st.selectbox("Slate", labels, index=0)
    # the numeric draft-group id, NOT the bare name -- found live 2026-07-18:
    # DK can post two same-named slates (e.g. two "Turbo"s at different
    # times, or even different dates before the date filter above existed).
    # Passing the NAME made every option in this dropdown resolve through
    # build_slate's own independent "soonest future, most games" tie-break,
    # completely ignoring which specific row was actually clicked -- the
    # dropdown looked precise but wasn't. The id pins the EXACT slate chosen.
    draft_group = None if choice == "Main (auto)" else names[labels.index(choice) - 1][1]

    iters = st.slider("Optimizer restarts", 200, 3000, 800, step=200,
                      help="More restarts = closer to optimal, a bit slower.")

    # Learned fresh THIS run (see cached_team_list) rather than from
    # st.session_state written by the previous build -- that used to leave
    # this widget with "No options to select" on the very first build of a
    # session, since nothing had run yet to populate session_state.
    try:
        _known_teams, _team_status = cached_team_list(slate_date, draft_group, 0)
    except Exception:
        _known_teams, _team_status = [], {}
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


# ── debug log ────────────────────────────────────────────────────────────
# Streamlit Cloud's own crash box explicitly REDACTS the error message ("to
# prevent data leaks") -- exactly the box the user has to screenshot, email,
# and repaste here. This catches the same exception ourselves, before it
# ever reaches that redacted handler, and renders the FULL unredacted
# traceback + enough context to diagnose it inside an st.code() block, which
# Streamlit gives a one-click copy button for free -- the "copy to clipboard
# and paste into email" the user asked for, without the email step.
def _render_debug_error() -> None:
    lines = [f"=== DFS app crash report — {datetime.datetime.now(datetime.timezone.utc).isoformat()} ==="]
    for label, get in (
        ("slate_date", lambda: slate_date),
        ("draft_group (sidebar choice)", lambda: draft_group),
        ("live/cache mode", lambda: "LIVE" if st.session_state.get("live", False) else "CACHE"),
        ("ODDS_API_KEY set", lambda: bool(os.environ.get("ODDS_API_KEY"))),
        ("iters", lambda: iters),
        ("exclude_teams", lambda: sorted(exclude_teams)),
        ("preview mode", lambda: preview),
        ("python", lambda: platform.python_version()),
        ("streamlit", lambda: st.__version__),
        ("platform", lambda: platform.platform()),
    ):
        try:
            lines.append(f"{label}: {get()}")
        except Exception as e:  # a context value itself failing must not blank the whole report
            lines.append(f"{label}: <unavailable: {e}>")
    lines.append("")
    lines.append(traceback.format_exc())
    blob = "\n".join(lines)
    print(blob, flush=True)   # also lands in `streamlit run` stdout / Cloud's own (unredacted) app logs
    st.error("The app hit an error. Full details below — tap the copy icon in the top-right "
             "corner of the box, then paste that here (no email round-trip needed).")
    st.code(blob, language="text")


# ── main ─────────────────────────────────────────────────────────────────
def render_app() -> None:
    st.title("DK MLB DFS Lineup Generator")

    if not os.environ.get("ODDS_API_KEY"):
        # This used to be a promise the code didn't keep -- a missing key
        # crashed the ENTIRE app (edge/client.py raised in __init__, before
        # anything free-tier ever ran). Fixed 2026-07-11: the key is now only
        # checked at the point of an actual network call, so this message is
        # finally accurate. Also: pasting the key into the box below is
        # SESSION-ONLY and won't survive a Streamlit Cloud reboot -- add it
        # as a permanent secret (Manage app -> Settings -> Secrets ->
        # ODDS_API_KEY = "...") so a reboot doesn't lose it again.
        st.warning("No ODDS_API_KEY set. Salaries + confirmed lineups (free) still work; "
                   "pitcher projections need a key or a warm cache. Pasting one below only "
                   "lasts this session — add it as a permanent Streamlit Cloud secret "
                   "(Settings → Secrets → `ODDS_API_KEY`) so a reboot doesn't lose it.")

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

    if res.get("error"):
        st.error(f"{res['error']}. Available now: {', '.join(res.get('available', [])) or '—'}")
        st.stop()

    if res.get("slate_mismatch"):
        # Found live 2026-07-11: an "Early" build silently included STL/LAD,
        # teams not in that slate, and the user had to notice and hand-
        # exclude them. This can't reliably tell WHY a mismatch happened (a
        # wrong-slate resolution, a DK data quirk, a doubleheader...), only
        # THAT one exists -- said plainly rather than guessed at.
        st.error(f"⚠️ Slate mismatch: {res['slate_mismatch']}")

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

    if res.get("pitcher_fetch_error"):
        # Distinct from the generic "props haven't posted yet" placeholder
        # below -- this means the live pull actually FAILED (bad/rejected
        # key, credit floor, network/API error), not just "too early."
        # Found live 2026-07-17: a user reported a key entered but no props
        # pulled, and every possible cause (no key, bad key, API outage)
        # used to degrade identically to a silent empty pitcher pool with no
        # way to tell which one happened.
        st.error(f"⚠️ Pitcher props pull failed: `{res['pitcher_fetch_error']}` — this is not the "
                 "normal \"DK hasn't posted props yet\" case. If you just entered your key, verify "
                 "it's correct (no extra spaces) and tap **💰 Pull fresh pitcher props** again.")

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

    def save_entry_button(mode, rows, key_suffix=None):
        """Pin the lineup you actually entered on DK (saved to disk, shared with
        scripts/dfs_swap.py --pin on your computer) so later refreshes/sessions
        can late-swap it and it can be graded tomorrow.

        key_suffix: override the Streamlit element-key SUFFIX (default: `mode`)
        when the SAME pinned-entry mode gets a second save block in one render
        (the sim-EV panel saves as the "gpp" entry too) -- two blocks may share
        a MODE but every widget inside must still get a unique key. Both the
        button AND the download_button below derive from this one suffix so a
        future third widget can't repeat the first fix's mistake of updating
        only one of the two hardcoded keys (StreamlitDuplicateElementKey hit
        LIVE TWICE on the phone 2026-07-19 -- first on the button, then again
        on the download_button once the button's own key was fixed)."""
        tag = key_suffix or mode
        saved = dfs_swap.load_pinned_entry(ROOT, slate_date, mode)
        is_saved = saved and {r["player"] for r in saved} == {r["player"] for r in rows}
        label = "📌 Saved as my DK entry ✓" if is_saved else "📌 Save this as my DK entry"
        col1, col2 = st.columns([3, 2])
        with col1:
            if st.button(label, key=f"save_{tag}", use_container_width=True,
                         help="Saved to disk. Tap 🔄 Refresh as lineups post — Late-swap flags anyone ruled out."):
                dfs_swap.save_pinned_entry(ROOT, slate_date, mode, rows)
                st.rerun()
        with col2:
            st.download_button("⬇️ backup copy", data=_entry_csv_bytes(rows), key=f"dl_entry_{tag}",
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
            st.caption("5-man stack + 3-man secondary stack (sample)")
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

    # ── field simulation (free -- no API calls; correlated slate sim) ───────
    if lineups_ready and not preview:
        with st.expander("🎲 Field simulation (how do these lineups fare vs the field?)"):
            st.caption("Simulates the whole slate with real teammate/stack correlation and an "
                       "ownership-modeled field (edge/dfs_sim, calibrated on 2025 data + real "
                       "DK contests). Free — no credits.")
            if st.button("Run simulation", key="run_sim"):
                with st.spinner("Simulating 3,000 worlds…"):
                    for _mode, _label in (("cash", "💵 CASH"), ("gpp", "🚀 GPP")):
                        eq = dfs_run.simulate_lineup_vs_field(res, mode=_mode, log_date=slate_date)
                        if eq.get("error"):
                            st.caption(f"{_label}: {eq['error']}")
                            continue
                        c1, c2, c3 = st.columns(3)
                        c1.metric(f"{_label} median finish", f"{eq['median_pct']:.0f}th pctile")
                        c2.metric(f"P(beat ~{eq['cash_line_pct']:.0%} pay line)", f"{eq['p_cash']:.0%}")
                        c3.metric("P(top 1%)", f"{eq['p_top']:.1%}")
                        st.caption(f"{_label}: score mean {eq['our_mean']} · P95 {eq['our_p95']} · "
                                   f"field p50/p90/p99 {eq['field_q'][50]}/{eq['field_q'][90]}/{eq['field_q'][99]} "
                                   f"({eq['n_sims']} worlds × {eq['field_n']} field lineups)")
            if res.get("gpp") and st.button("🧪 GPP by sim-EV (experimental)", key="run_sim_ev",
                                            help="Ranks ~16 alternative constructions (stack team × fade × "
                                                 "shape) by expected payout against a simulated field. "
                                                 "Replay-validated +11 mean percentile over 8 real slates but "
                                                 "NOT yet statistically significant — the GPP tab's default "
                                                 "lineup is unchanged."):
                with st.spinner("Ranking candidate lineups across simulated worlds…"):
                    ev_r = dfs_run.gpp_sim_ev_lineup(res, seed=1)
                if ev_r.get("error"):
                    st.caption(f"sim-EV: {ev_r['error']}")
                else:
                    st.caption(f"picked from {ev_r['n_cands']} candidates · EV ${ev_r['ev']:.2f}/entry "
                               f"(synthetic $5 GPP curve) · sim mean finish {ev_r['mean_pct']:.0f}th pctile "
                               f"· P(top 1%) {ev_r['p_top1']:.1%}")
                    ev_rows = [{"slot": slot, "player": p["name"], "team": p["team"], "salary": p["salary"],
                                "proj": p["proj"], "ceil": p["ceiling"], "own": p.get("own", 0),
                                "pos": "/".join(sorted(p["pos"])), "game": p.get("game", ""),
                                "conf": p.get("conf", ""), "projected": not p.get("confirmed", True)}
                               for p, slot in sorted(ev_r["lineup"], key=lambda x: dfs_opt.SLOTS.index(x[1]))]
                    render_compact(ev_rows)
                    save_entry_button("gpp", ev_rows, key_suffix="gpp_simev")

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


try:
    render_app()
except Exception:
    _render_debug_error()
