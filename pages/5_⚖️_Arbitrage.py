"""pages/5_⚖️_Arbitrage.py — three-book arbitrage, middles and +EV.

Connecticut licenses exactly three online sportsbooks (DraftKings, FanDuel,
Fanatics), and this page prices all three against each other. Unlike the other
tools here it spends **no Odds API credits**: each book is read from its own
public endpoint, with Fanatics Markets (the prediction market) as a vig-free
fair-value anchor. The anchor is never a bet leg -- CT enforcement against
sports event contracts is active.

Numbered 5_ to sit after Pickem; DFS_MULTISPORT_PLAN.md reserves 2_/3_.

Two data paths, the same free-vs-manual split app.py and Pickem already use:
  * Snapshot (default): data/arb_snapshot.json, written by
    `python3 scripts/arb_scan.py`. This is what makes the page work on
    Streamlit Community Cloud.
  * Live scan (button): runs the scrapers in-process. Works from a machine in
    Connecticut. It will likely FAIL on Community Cloud -- these endpoints sit
    behind Akamai and Cloudflare, the same wall that blocked ESPN and
    DraftKings for pickem_live.py. The button surfaces the error rather than
    pretending, and the snapshot stays available.

A scan takes ~40s, which is why the snapshot is the default rather than
scanning on every page load.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Streamlit Community Cloud pulls new commits and RERUNS this script WITHOUT
# restarting the Python process, so `sys.modules` keeps whatever module
# objects an earlier run imported. `from edge.arb.x import name` only notices
# a change when `name` is new -- the far more common case is an EXISTING
# name's BODY changing (an ordinary bugfix), and that goes on running the
# pre-fix code with no error at all, silently. That is what undid the boost
# cap-0 fix here: the page's own code updated on the next deploy (Streamlit
# always re-execs the entrypoint script), but `top_rows_per_sport` kept its
# pre-fix body -- returning zero rows for every sport -- because nothing
# forced `edge.arb.engine` back in sync with disk. `_from_scan_request` below
# only reloads on a MISSING name, which never catches this.
#
# Reload every edge.arb module already resident, every run, before anything
# below binds a name off one. Three passes because reload order here is not
# dependency-sorted: a module reloaded before something it does
# `from .x import y` on picks up that something's PRE-reload value the first
# time round, and only catches up once that something is reloaded too.
for _pass in range(3):
    for _name in sorted(k for k in sys.modules
                        if k == "edge.arb" or k.startswith("edge.arb.")):
        try:
            importlib.reload(sys.modules[_name])
        except Exception:                               # noqa: BLE001
            pass

from edge.arb import ArbConfig                      # noqa: E402

# Streamlit Community Cloud keeps sys.modules warm across reruns. A deploy that
# ADDS a function to an existing module therefore leaves the old module object
# in memory: the file on disk has it, `from ... import name` does not, and the
# page dies at that line before rendering anything -- taking the rest of the
# sidebar (including the scan-request button) with it. Import the module once,
# defensively, and look up names off it, so a stale process degrades to a
# reduced sidebar with a "reboot" hint instead of a white screen.
try:
    from edge.arb import scan_request as _sr        # noqa: E402
except Exception:                                   # noqa: BLE001
    _sr = None

_STALE: list[str] = []
_RELOADED: list[str] = []


def _from_scan_request(name, fallback=None):
    """Look a name up on the module, reloading it from disk if it is missing.

    Streamlit Community Cloud pulls new code and RERUNS the script without
    restarting the Python process, so `sys.modules` keeps the module object
    from the previous deploy. Anything added since is absent, and the only
    documented cure is a manual reboot from the dashboard -- which is not on
    the mobile UI, so a phone user is simply stuck.

    importlib.reload re-executes the file that is now on disk and rebinds the
    module in place, which is exactly the restart the process did not get. Done
    lazily, on the first name that turns up missing, so the normal path costs
    nothing.
    """
    global _sr
    fn = getattr(_sr, name, None) if _sr is not None else None
    if fn is None and _sr is not None:
        try:
            import importlib

            _sr = importlib.reload(_sr)
            fn = getattr(_sr, name, None)
            if fn is not None:
                _RELOADED.append(name)
        except Exception:                           # noqa: BLE001
            fn = None
    if fn is None:
        _STALE.append(name)
        return fallback
    return fn


SNAPSHOT = ROOT / "data" / "arb_snapshot.json"
FALLBACK_SPORTS = {
    "americanfootball_ncaaf": "NCAAF", "americanfootball_nfl": "NFL",
    "baseball_mlb": "MLB", "basketball_nba": "NBA", "basketball_ncaab": "NCAAB",
    "basketball_wnba": "WNBA", "icehockey_nhl": "NHL",
}
BOOK_NAMES = {"draftkings": "DraftKings", "fanduel": "FanDuel", "fanatics": "Fanatics",
              "fanatics_markets": "Fanatics Markets", "pinnacle": "Pinnacle"}
KIND_ICON = {"arb": "🟢", "middle": "🔵", "ev": "🟡"}
# A generous boost turns most of the board into an arbitrage -- a 50% token on
# one slate produced 2,568 of them. Ranking them all is right; RENDERING them
# all is a hung page, so the panel draws the best of them and says so.
BOOST_ROWS_SHOWN = 50

st.set_page_config(page_title="Arbitrage", page_icon="⚖️", layout="wide")
st.title("⚖️ Arbitrage · DraftKings / FanDuel / Fanatics")


def load_snapshot() -> dict | None:
    if not SNAPSHOT.exists():
        return None
    try:
        return json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        return None


def age_str(iso: str) -> str:
    try:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "unknown age"
    mins = int(delta.total_seconds() // 60)
    if mins < 60:
        return f"{mins}m ago"
    return f"{mins // 60}h{mins % 60:02d}m ago"


def money(x) -> str:
    return f"${float(x or 0):,.2f}"


def starts_in(iso: str) -> str:
    try:
        mins = int((datetime.fromisoformat(iso) - datetime.now(timezone.utc)).total_seconds() // 60)
    except (TypeError, ValueError):
        return ""
    if mins < 0:
        return "live"
    return f"in {mins}m" if mins < 60 else f"in {mins // 60}h{mins % 60:02d}m"


ET = ZoneInfo("America/New_York")


def event_date_et(iso: str):
    """The event's calendar date in US/Eastern -- the books' own timezone,
    and the one every CT bettor is in. `commence_time` is stored in UTC, and
    comparing a naive string prefix against "today" is wrong for anything
    that tips past 8pm ET: a 9pm ET kickoff is already tomorrow in UTC, and
    would silently vanish from a "today" filter built that way.
    """
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date()


def in_date_range(commence_time: str, date_range) -> bool:
    if not date_range:
        return True
    d = event_date_et(commence_time)
    return d is not None and date_range[0] <= d <= date_range[1]


def date_range_label(date_range) -> str:
    lo, hi = date_range
    return f"{lo}" if lo == hi else f"{lo} – {hi}"


def is_live(commence_time: str) -> bool:
    """Live at RENDER time, not scan time -- a game that had not started when
    the snapshot was built can easily have kicked off by the time this page
    is opened, and the "Include live" toggle is about what you see now."""
    try:
        dt = datetime.fromisoformat(commence_time)
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= datetime.now(timezone.utc)


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Scan")
    bankroll = st.number_input("Bankroll per opportunity ($)", 50, 100_000, 1000, step=50)
    kinds = st.multiselect("Show", ["arb", "middle", "ev"], default=["arb", "middle", "ev"],
                           format_func=lambda k: {"arb": "Arbitrage", "middle": "Middles",
                                                  "ev": "+EV"}[k])
    min_profit = st.slider("Minimum %", 0.0, 20.0, 0.0, 0.25)

    st.divider()
    st.header("Profit boost")
    if st.checkbox("Load DraftKings' public offers", value=False,
                   help="Reads the opt-in boosts off DraftKings' homepage — the "
                        "same carousel you see logged out. Account tokens in "
                        "the bet slip's Rewards panel are NOT here; those are "
                        "issued to you and have to be entered by hand."):
        try:
            from edge.arb.promotions import discover as discover_boosts

            _live = discover_boosts(default_max_stake=10.0)
            if not _live:
                st.caption("No live public boosts found.")
            for _p in _live:
                _b = _p.boost
                _bits = [f"**{_b.pct:.0%}**", _sport_titles.get(_b.sports[0], _b.sports[0])]
                if _b.requires_parlay:
                    _bits.append("parlay only — cannot be hedged")
                if _b.min_decimal > 1:
                    _bits.append(f"min {int(round((_b.min_decimal-1)*100)) if _b.min_decimal>=2 else -int(round(100/(_b.min_decimal-1)))}")
                if _p.expires_at:
                    _hrs = (_p.expires_at - datetime.now(timezone.utc)).total_seconds()/3600
                    _bits.append(f"{_hrs:.0f}h left")
                st.caption("· ".join(["🎁 " + _p.headline] + _bits))
                if _p.unparsed:
                    st.caption(f"   ⚠️ not readable from the terms: "
                               f"{', '.join(_p.unparsed)} — set by hand below")
        except Exception as exc:                       # noqa: BLE001
            st.caption(f"Could not read offers: {type(exc).__name__}")
        st.divider()
    st.caption("A boost is what creates the arbitrage. Two books priced fairly "
               "still sum to ~1.05 — that 5% is the vig and no amount of "
               "shopping removes it. One 50% boost on either leg pays 2.36 "
               "where the book posted 1.91, which clears the vig and leaves "
               "a locked profit.")
    boost_pct = st.slider("Boost %", 0, 100, 0, 5,
                          help="0 turns boosts off. Profit boosts multiply your "
                               "NET winnings, not the total return.")
    boost_book = st.selectbox("Book", ["fanduel", "draftkings", "fanatics"],
                              format_func=lambda b: BOOK_NAMES.get(b, b))
    boost_max = st.number_input("Boost max stake ($)", 1, 5_000, 10, step=5,
                                help="The token's cap. This bounds the WHOLE "
                                     "position, not just the boosted leg — the "
                                     "hedge is sized off it.")
    _sport_choices = _from_scan_request(
        "sport_choices", lambda cfg, snap: dict(sorted(FALLBACK_SPORTS.items(),
                                                       key=lambda kv: kv[1])))
    _snap_peek = load_snapshot() or {}
    _sport_titles = _sport_choices(ArbConfig(), _snap_peek)
    _in_snapshot = {c.get("sport_key") for c in (_snap_peek.get("candidates") or [])}
    boost_sport = st.selectbox(
        "Sport", ["(every sport)"] + list(_sport_titles),
        format_func=lambda k: (
            "(every sport)" if k == "(every sport)"
            # a sport the current snapshot cannot answer for is still
            # selectable, but say so rather than silently returning nothing
            else _sport_titles[k] + ("" if k in _in_snapshot else "  · not in snapshot")),
        help="The sport your boost is tied to. Sports missing from the current "
             "snapshot are still listed — request a desktop scan to cover them.")
    boost_sport = "" if boost_sport == "(every sport)" else boost_sport
    if boost_sport and boost_sport not in _in_snapshot:
        st.caption(f"⚠️ The current snapshot has no {_sport_titles[boost_sport]} "
                   "markets, so nothing can be found for it. Request a desktop "
                   "scan first.")
    _market_choices = _from_scan_request("market_choices", lambda snap: {})
    _market_groups = _market_choices(_snap_peek)
    boost_market = st.selectbox(
        "Markets", ["(every market)"] + list(_market_groups),
        help="Boosts are often scoped to a market type as well as a sport — "
             "a batter-props token cannot be used on a game line.")
    boost_markets = _market_groups.get(boost_market, [])
    boost_mode = st.radio(
        # Named distinctly from the top "Show" multiselect (arb/middle/ev) --
        # the test harness answers widgets by label, and the two are only
        # different widget TYPES, not different labels. See HANDOFF.md on the
        # "Filter by sport" rename for the same trap.
        "Boosted view", ["Arbitrage (hedged)", "Best +EV (unhedged)"], index=0,
        help="A boost no second book can cover is not wasted — it stops being "
             "an arbitrage and becomes an +EV bet. Use that view when nothing "
             "can hedge the boosted side.")
    boost_min_odds = st.number_input(
        "Min odds on the boosted leg (American)", -1000, 1000, -200, step=10,
        help="Most tokens carry a floor — 'Min Total Odds of -200'. A shorter "
             "leg does not qualify and the book refuses it at the slip.")
    boost_sides = st.multiselect(
        "Boosted side", ["over", "under", "home", "away", "yes", "no"],
        default=[],
        help="Leave empty for any. DraftKings' 'Batter Props Milestones' are "
             "the over-only ladders, so that token is over only.")
    boost_parlay = st.checkbox("Parlay only", value=False,
                               help="Books offer the same headline boost twice — "
                                    "straight bets and parlays. Only the straight-bet "
                                    "one can be hedged, because each side of an "
                                    "arbitrage is its own single bet.")
    # The main list is ranked by expected return across every sport at once --
    # you bet the best price on the board, not the best price in each league.
    # This caps a runaway sport if you want it; 0 means no cap.
    per_sport = st.number_input(
        "Cap per sport (0 = no cap)", 0, 25, 0, step=1,
        help="The list is ranked by expected return across all sports. Set a "
             "cap only if one sport is crowding out the rest.")
    _opp_sports = sorted({(o.get("sport_title") or o.get("sport_key") or "")
                          for o in (_snap_peek.get("opportunities") or [])} - {""})
    # Named distinctly from the boost section's "Sport", which asks a
    # different question: that one is the sport the TOKEN is tied to.
    sport_filter = st.multiselect(
        "Filter by sport", options=_opp_sports, default=[],
        help="Leave empty to rank every sport together, which is the point of "
             "the ordering. Pick one or more to narrow it.")

    _today_et = datetime.now(ET).date()
    date_mode = st.selectbox(
        "Game date", ["All upcoming", "Today", "Tomorrow", "Next 7 days", "Custom range"],
        help="Filters by the event's calendar date in US/Eastern -- the "
             "books' own timezone. A late kickoff can already be tomorrow "
             "in UTC, so this is not the same as filtering on the raw "
             "timestamp.")
    if date_mode == "Today":
        date_range = (_today_et, _today_et)
    elif date_mode == "Tomorrow":
        date_range = (_today_et + timedelta(days=1),) * 2
    elif date_mode == "Next 7 days":
        date_range = (_today_et, _today_et + timedelta(days=7))
    elif date_mode == "Custom range":
        _picked = st.date_input("Range (ET)", value=(_today_et, _today_et),
                                help="Both ends are included.")
        # A range date_input returns a ONE-element tuple while the user has
        # only picked the start -- not yet a valid range, so hold off
        # filtering rather than treat it as a single-day range by accident.
        date_range = tuple(_picked) if isinstance(_picked, (tuple, list)) else (_picked, _picked)
        if len(date_range) != 2:
            date_range = None
    else:
        date_range = None

    show_live = st.checkbox(
        "Include live/in-progress games", value=False,
        help="Off by default: an in-play line can already be a different "
             "price than the book is showing you by the time a bet lands. "
             "This also asks the NEXT scan to actually capture live games -- "
             "a snapshot scanned with this off has none to show even if you "
             "turn it on now, since it never kept them in the first place.")

    st.divider()
    st.caption("A live scan takes ~40s and spends **no** API credits. "
               "It needs a connection the books accept — that usually means "
               "your own machine, not a cloud host.")
    run_live = st.button("🔄 Scan live", use_container_width=True)

    st.divider()
    st.header("Ask the desktop")
    st.caption("This host cannot fetch odds — the books refuse datacenter IPs. "
               "This asks the machine in Connecticut to scan and push a fresh "
               "snapshot. It needs `arb_agent.py` running there.")
    _check_credentials = _from_scan_request(
        "check_credentials",
        lambda repo, token: "" if (repo and token) else "GITHUB_REPO/GITHUB_TOKEN not set")

    def _secret(name: str) -> str:
        # st.secrets raises rather than returning empty when no secrets file
        # exists at all, which is the normal case running locally
        try:
            return str(st.secrets.get(name, "") or "")
        except Exception:                              # noqa: BLE001
            return ""

    _repo, _token = _secret("GITHUB_REPO"), _secret("GITHUB_TOKEN")
    _cred_problem = _check_credentials(_repo, _token)
    request_scan = st.button("📡 Request a desktop scan", use_container_width=True,
                             disabled=bool(_cred_problem))
    if _cred_problem:
        st.caption(f"⚠️ {_cred_problem}. Set `GITHUB_REPO` and `GITHUB_TOKEN` in "
                   "the app's Settings → Secrets.")

snap = load_snapshot()

# ------------------------------------------------------- desktop scan request
if request_scan:
    try:
        ScanRequest = _from_scan_request("ScanRequest")
        put_request = _from_scan_request("put_request")
        if ScanRequest is None or put_request is None:
            raise RuntimeError("stale module — reboot the app")
        req = ScanRequest.new(
            sports=[], note="requested from the Streamlit app",
            date_from=date_range[0].isoformat() if date_range else None,
            date_to=date_range[1].isoformat() if date_range else None,
            skip_live=not show_live)
        put_request(_repo, _token, req)
        st.session_state["last_scan_request"] = req.requested_at
        st.success("Asked the desktop to scan. It polls every ~30s, the scan "
                   "takes ~40s, then this page picks up the new snapshot on "
                   "its next redeploy — give it a couple of minutes.")
    except Exception as exc:                      # noqa: BLE001 - surface, don't hide
        _refused = getattr(_sr, "RequestRefused", None)
        if _refused is not None and isinstance(exc, _refused):
            st.error(f"Could not file the request. {exc}")
        else:
            st.error(f"Could not file the request: {type(exc).__name__}: {exc}")
            st.caption("A 404 usually means the token cannot see the repo; a "
                       "403 means it lacks `contents: write`.")

if st.session_state.get("last_scan_request") and snap:
    ScanRequest = _from_scan_request("ScanRequest")
    snapshot_is_newer = _from_scan_request("snapshot_is_newer", lambda *_a: False)
    _pending = (None if ScanRequest is None else
                ScanRequest(requested_at=st.session_state["last_scan_request"],
                            request_id="local"))
    if snapshot_is_newer(snap.get("generated_at"), _pending):
        st.success("✅ The desktop answered — this snapshot is newer than your request.")
        st.session_state.pop("last_scan_request", None)
    else:
        st.info("⏳ Waiting on the desktop. Refresh in a minute; the snapshot "
                "below is still the previous one.")

if run_live:
    prog = st.progress(0.0, text="starting…")
    try:
        from edge.arb.run import snapshot as build_snapshot

        cfg = ArbConfig()
        cfg.bankroll.total = float(bankroll)
        cfg.detect.skip_live = not show_live
        if date_range:
            cfg.detect.date_from = date_range[0].isoformat()
            cfg.detect.date_to = date_range[1].isoformat()

        def on_progress(label, i, n):
            prog.progress((i + 1) / n, text=f"{label}…")

        snap = build_snapshot(cfg, progress=on_progress)
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(snap, indent=1))
        prog.empty()
        st.success(f"Scanned {snap['stats']['quotes']:,} quotes · 0 credits")
    except Exception as exc:                      # noqa: BLE001 - surface, don't hide
        prog.empty()
        st.error(f"Live scan failed: {type(exc).__name__}: {exc}")
        st.caption("If this is a 403, the host refused this server. Run "
                   "`python3 scripts/arb_scan.py` locally and commit "
                   "`data/arb_snapshot.json`.")

if not snap:
    st.info("No snapshot yet. Run `python3 scripts/arb_scan.py` and commit "
            "`data/arb_snapshot.json`, or press **Scan live**.")
    st.stop()

if _RELOADED and not _STALE:
    st.caption(f"♻️ Reloaded `edge.arb.scan_request` from disk "
               f"({', '.join(sorted(set(_RELOADED)))} were missing) — Streamlit "
               "reuses modules across reruns after a deploy. No reboot needed.")

if _STALE:
    st.warning(
        f"This app is running a stale copy of `edge.arb.scan_request` "
        f"(missing: {', '.join(sorted(set(_STALE)))}). Streamlit reuses "
        "imported modules across reruns, and reloading it from disk did not "
        "recover these either — so the deployed code really is behind. "
        "**Reboot the app** at share.streamlit.io (⋮ → Reboot); the sidebar "
        "is running on fallbacks until you do.", icon="♻️")

stats = snap.get("stats", {})
c1, c2, c3, c4 = st.columns(4)
c1.metric("Quotes", f"{stats.get('quotes', 0):,}")
c2.metric("Events", stats.get("events", 0))
c3.metric("Found", len(snap.get("opportunities", [])))
c4.metric("Credits", "0")
_skipped = stats.get("skipped_events") or {}
if _skipped:
    st.caption("Not scanned: "
               + " · ".join(f"{n} {k}" for k, n in sorted(_skipped.items()))
               + ".  Golf tournaments run for days, so they read as in progress "
                 "from the first tee onward and only appear before a round starts.")

st.caption(f"Snapshot {age_str(snap.get('generated_at', ''))} · "
           f"FanDuel {stats.get('fanduel', 0):,} · DraftKings {stats.get('draftkings', 0):,} · "
           f"Fanatics {stats.get('fanatics', 0):,} · anchor {stats.get('anchor', 0):,}")

# --------------------------------------------------------------- boosts
# Re-priced from the snapshot's `candidates` rather than its opportunities:
# the markets a boost turns INTO arbitrages are by definition not opportunities
# yet, so re-scoring the found list would miss every one of them.
boost_rows = []
if boost_pct > 0:
    from edge.arb.engine import Boost, price_candidates, top_rows_per_sport

    cands = snap.get("candidates") or []
    if not cands:
        st.warning("This snapshot predates the boost feature — it has no "
                   "`candidates` section. Press **Scan live** (or re-run "
                   "`scripts/arb_scan.py`) to rebuild it.")
    elif not show_live and not (cands := [
            c for c in cands if not is_live(c.get("commence_time", ""))]):
        st.warning("Every remaining candidate is live/in-progress, and "
                   "\"Include live/in-progress games\" is off in the sidebar.")
    elif date_range and not (cands := [c for c in cands if in_date_range(
            c.get("commence_time", ""), date_range)]):
        st.warning(f"No candidates land on {date_range_label(date_range)} "
                   "(US/Eastern). Widen the date filter in the sidebar.")
    else:
        bcfg = ArbConfig()
        bcfg.bankroll.total = float(bankroll)
        bcfg.detect.min_profit_pct = float(min_profit)
        boost = Boost(book=boost_book, pct=boost_pct / 100.0,
                      max_stake=float(boost_max),
                      sports=[boost_sport] if boost_sport else [],
                      markets=boost_markets,
                      sides=list(boost_sides),
                      min_decimal=(1.0 if boost_min_odds == 0 else
                                   1.0 + (boost_min_odds / 100.0 if boost_min_odds > 0
                                          else 100.0 / abs(boost_min_odds))),
                      requires_parlay=boost_parlay,
                      label=(f"{boost_pct}% boost on "
                             f"{BOOK_NAMES.get(boost_book, boost_book)}"
                             + (f" ({boost_market.lower()})"
                                if boost_markets else "")))
        if boost_mode.startswith("Best +EV"):
            from edge.arb.engine import price_boosted_ev
            ev_rows = price_boosted_ev(cands, [boost], bcfg,
                                       min_ev_pct=float(min_profit))
            if boost_parlay:
                st.info("A parlay-only token cannot be priced as a single bet.")
            elif not ev_rows:
                st.warning("Nothing qualifies. Check the side and minimum-odds "
                           "filters match the token's terms.")
            else:
                st.subheader(f"⚡ {len(ev_rows)} boosted +EV bets"
                             + (f" · top {int(per_sport)} per sport"
                                if int(per_sport) > 0 else ""))
                st.caption("These are NOT hedged — a boost no second book can "
                           "cover is an +EV bet, not an arbitrage. Higher "
                           "expected value than hedging, but it can lose.")
                _ev_shown = top_rows_per_sport(ev_rows, int(per_sport))[:BOOST_ROWS_SHOWN]
                if len(ev_rows) > len(_ev_shown):
                    st.caption(f"Showing the best {len(_ev_shown)} of "
                               f"{len(ev_rows)}. Narrow with the sport filter "
                               f"or a per-sport cap in the sidebar.")
                for r in _ev_shown:
                    with st.container(border=True):
                        st.markdown(
                            f"🟡 **{r['ev_pct']:+.2f}% EV** · "
                            f"{money(r['ev_abs'])} expected on {money(r['stake'])} · "
                            f"lands {r['fair_prob']:.0%} of the time")
                        sub = f" · {r['subject']}" if r.get("subject") else ""
                        pt = "" if r.get("point") is None else f" {r['point']:g}"
                        st.caption(f"{r['sport_title']} · **{r['matchup']}** · "
                                   f"{starts_in(r.get('commence_time',''))} · "
                                   f"{r['market']}{sub}{pt} · {r['side']}")
                        other = " · ".join(
                            f"{BOOK_NAMES.get(k, k)} {r['raw_american']}"
                            for k in r.get("other_books", {})) or "—"
                        st.dataframe([{
                            "Book": BOOK_NAMES.get(r["book"], r["book"]),
                            "Bet": r["side"],
                            "Line": "" if r.get("point") is None else f"{r['point']:g}",
                            "Book odds": r["raw_american"],
                            "Boost": f"+{r['boost_pct']:.0%}",
                            "Pays": r["american"],
                            "Stake": money(r["stake"]),
                            "Elsewhere": other,
                        }], hide_index=True, use_container_width=True)
                st.caption("Place the boosted leg only. Nothing hedges it, so "
                           "most of these lose — the edge is in the price, not "
                           "in certainty.")
            boost_rows = []
            st.divider()
            st.stop()

        boost_rows = price_candidates(cands, [boost], bcfg,
                                      min_profit_pct=float(min_profit))
        if boost_parlay:
            st.info("Parlay-only boosts cannot be arbitraged — each side of a "
                    "hedge is its own straight bet. Untick **Parlay only** to "
                    "price a straight-bet boost.")
        elif not boost_rows:
            st.warning(f"No market clears {min_profit:.2f}% with a {boost_pct}% "
                       f"boost on {BOOK_NAMES.get(boost_book, boost_book)}"
                       + (f" in {_sport_titles.get(boost_sport, boost_sport)}"
                          if boost_sport else "")
                       + (f" on {boost_market.lower()}" if boost_markets else "")
                       + f" · {len(cands)} candidates checked.")
        else:
            shown = top_rows_per_sport(boost_rows, int(per_sport))[:BOOST_ROWS_SHOWN]
            _plain = price_candidates(cands, [], bcfg,
                                      min_profit_pct=float(min_profit))
            st.subheader(f"⚡ {len(boost_rows)} boosted "
                         f"arbitrage{'s' if len(boost_rows) != 1 else ''}"
                         + (f" · top {int(per_sport)} per sport"
                            if int(per_sport) > 0 else ""))
            if len(boost_rows) > len(shown):
                st.caption(f"Showing the best {len(shown)} of {len(boost_rows)}. "
                           f"Narrow with the sport filter or a per-sport cap "
                           f"in the sidebar.")
            st.caption(f"Without the boost the same board gives "
                       f"**{len(_plain)}**. Everything below needs the token "
                       f"on the leg marked with a boost — place that leg "
                       f"first and confirm it attached before hedging.")
            for r in shown:
                head = (f"🟢 **{r['profit_pct']:+.2f}%** locked · "
                        f"{money(r['profit_abs'])} on {money(r['stake_total'])} · "
                        f"unboosted {r['unboosted_pct']:+.2f}%")
                if r.get("both_plus"):
                    head += "  ·  ➕ **both sides +money**"
                with st.container(border=True):
                    st.markdown(head)
                    sub = f" · {r['subject']}" if r.get("subject") else ""
                    pt = "" if r.get("point") is None else f" {r['point']:g}"
                    st.caption(f"{r['sport_title']} · **{r['matchup']}** · "
                               f"{starts_in(r.get('commence_time',''))} · "
                               f"{r['market']}{sub}{pt}")
                    st.dataframe([{
                        "Book": BOOK_NAMES.get(l["book"], l["book"]),
                        "Bet": l["label"],
                        "Line": "" if l.get("point") is None else f"{l['point']:g}",
                        "Book odds": l["raw_american"],      # verify this at the book
                        "Boost": f"+{l['boost_pct']:.0%}" if l["boost_pct"] else "—",
                        "Pays": l["american"],
                        "Stake": money(l["stake"]),
                        "Returns": money(l["payout"]),
                    } for l in r["legs"]], hide_index=True, use_container_width=True)
            st.caption("The boosted leg is capped at the token's max stake, so "
                       "the position is small by design. Place the boosted leg "
                       "FIRST and confirm it applied before placing the hedge — "
                       "an unboosted first leg leaves you with a plain "
                       f"{shown[0]['unboosted_pct']:+.2f}% position."
                       if shown else "")
    st.divider()

opps = [o for o in snap.get("opportunities", [])
        if o.get("kind") in kinds and o.get("profit_pct", 0) >= min_profit]
if sport_filter:
    opps = [o for o in opps
            if (o.get("sport_title") or o.get("sport_key")) in sport_filter]
if not show_live:
    opps = [o for o in opps if not is_live(o.get("commence_time", ""))]
if date_range:
    opps = [o for o in opps if in_date_range(o.get("commence_time", ""), date_range)]

if not opps:
    if not boost_rows:
        st.warning("Nothing clears these filters. With three books, days with no "
                   "arbitrage are normal — middles and +EV are the usual finds.")
    st.stop()


def _rank(o: dict) -> tuple[bool, float]:
    """(is a free middle, what this is worth per dollar staked) -- the tuple
    sorts free middles above everything else, and everything else against
    each other by the second element.

    A free middle is a middle whose worst case is STILL a profit: it is a
    straight arbitrage that also carries the middle's upside if the window
    lands. No downside, a higher ceiling than the arbitrage alone -- so it
    outranks every other opportunity regardless of size, not just ones with
    a smaller expected return.

    The second element is NOT profit_pct. For an arbitrage that is the
    guaranteed return, but for a middle it is what you collect ONLY if the
    window lands -- so ranking on it puts every "+130% if it hits" above
    every real arbitrage, which is the reverse of the order you would bet
    in. `expected_pct` is the guaranteed return for an arb, the edge for
    +EV, and P(window) x gain - P(miss) x cost for a middle, read off the
    books' own alternate ladders.

    A middle whose window probability could not be measured (no ladder deep
    enough on that market) falls back to its worst case, which is negative:
    unmeasured is not the same as good, and it must not outrank a real edge.
    """
    if o.get("expected_pct") is not None:
        value = float(o["expected_pct"])
    elif o.get("kind") == "middle":
        value = -float(o.get("max_loss_pct", 0.0))
    else:
        value = float(o.get("profit_pct", 0.0))
    return (bool(o.get("free_middle")), value)


opps.sort(key=_rank, reverse=True)

# Optional: stop one sport crowding the list. Off by default -- the ranking is
# global on purpose.
if int(per_sport) > 0:
    _by_sport: dict[str, int] = {}
    _kept = []
    for _o in opps:
        _k = _o.get("sport_key", "")
        if _by_sport.get(_k, 0) < int(per_sport):
            _by_sport[_k] = _by_sport.get(_k, 0) + 1
            _kept.append(_o)
    opps = _kept

# stakes were sized for the bankroll at scan time; rescale for this one
scale = float(bankroll) / max(float(snap.get("stats", {}).get("bankroll", 1000.0) or 1000.0), 1.0)

for o in opps:
    kind = o.get("kind", "")
    icon = KIND_ICON.get(kind, "")
    if kind == "arb":
        head = f"{icon} **{o['profit_pct']:+.2f}%** guaranteed"
    elif kind == "middle":
        hits = o.get("hit_values") or []
        on = "/".join(str(h) for h in hits[:4]) or "the window"
        if o.get("free_middle"):
            # No downside AND the middle's upside -- strictly better than an
            # ordinary middle or a plain arb of the same guaranteed size, so
            # this is called out rather than left to look like just another
            # row the ranking happened to put on top.
            floor = o.get("free_middle_floor_pct") or 0.0
            head = (f"🎯 **FREE MIDDLE — no downside** · "
                    f"+{floor:.2f}% guaranteed no matter what  \n"
                    f"up to +{o['profit_pct']:.1f}% if it lands on {on}")
        else:
            # Lead with the expected return, because that is what the list is
            # ordered by and what decides whether the bet is worth making. The
            # "+130% if it lands" is the headline a middle wants to be judged on
            # and the one that is misleading on its own.
            exp = o.get("expected_pct")
            prob = o.get("fair_prob")
            if exp is not None:
                lead = (f"{icon} **{exp:+.2f}%** expected · lands {prob * 100:.1f}% "
                        f"of the time vs {o.get('breakeven_hit_pct', 0):.1f}% needed")
            else:
                lead = (f"{icon} **?** expected — no ladder deep enough to price "
                        f"this window · needs {o.get('breakeven_hit_pct', 0):.1f}%")
            head = (f"{lead}  \n+{o['profit_pct']:.1f}% if it lands on {on}, "
                    f"−{o.get('max_loss_pct', 0):.2f}% otherwise")
    else:
        head = (f"{icon} **{o['profit_pct']:+.2f}%** edge vs "
                f"{o.get('anchor_book') or 'consensus'}")

    with st.container(border=True):
        st.markdown(head)
        st.caption(f"{o.get('sport_title', '')} · **{o.get('matchup', '')}** · "
                   f"{starts_in(o.get('commence_time', ''))} · {o.get('description', '')}")
        rows = []
        for leg in o.get("legs", []):
            rows.append({
                "Book": BOOK_NAMES.get(leg.get("book", ""), leg.get("book", "")),
                "Bet": leg.get("label", ""),
                "Line": "" if leg.get("point") is None else f"{leg['point']:g}",
                "Odds": leg.get("american", ""),
                "Stake": f"${leg.get('stake', 0) * scale:,.2f}",
                "Returns": f"${leg.get('payout', 0) * scale:,.2f}",
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)
        for w in o.get("warnings", []):
            st.warning(w, icon="⚠️")

st.divider()
st.caption("Confirm both prices in the apps before staking — lines move in "
           "seconds. Fanatics Markets is reference only and is never a leg.")
