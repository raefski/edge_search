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
# Reload every edge.arb module already resident, before anything below binds
# a name off one. Three passes because reload order here is not
# dependency-sorted: a module reloaded before something it does
# `from .x import y` on picks up that something's PRE-reload value the first
# time round, and only catches up once that something is reloaded too.
#
# Gated on a fingerprint of the source files' mtimes, via st.cache_resource --
# a PROCESS-wide cache, the same scope as sys.modules itself, so it is shared
# across every session and every rerun. Doing this unconditionally on every
# rerun (the original shape) reran 20 modules' top-level code 3x per single
# widget click with nothing to show for it locally, where the file on disk
# never changes between clicks. st.session_state would be the wrong tool
# here: it is per-BROWSER-SESSION, and a Streamlit Cloud redeploy that keeps
# the process (and an already-open tab's session_state) alive across the
# deploy is exactly the case this reload exists to catch -- a session-scoped
# gate would skip the reload for precisely the session that needs it.
def _arb_source_fingerprint() -> float:
    try:
        return max(p.stat().st_mtime for p in (ROOT / "edge" / "arb").glob("*.py"))
    except ValueError:
        return 0.0


@st.cache_resource(show_spinner=False)
def _reload_edge_arb(fingerprint: float) -> float:
    for _pass in range(3):
        for _name in sorted(k for k in sys.modules
                            if k == "edge.arb" or k.startswith("edge.arb.")):
            try:
                importlib.reload(sys.modules[_name])
            except Exception:                               # noqa: BLE001
                pass
    return fingerprint


_reload_edge_arb(_arb_source_fingerprint())

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
KIND_ICON = {"arb": "🟢", "middle": "🔵", "ev": "🟡", "gap": "🕳️"}
CASINO_BOOKS = {"draftkings", "fanduel"}
# A generous boost turns most of the board into an arbitrage -- a 50% token on
# one slate produced 2,568 of them. Ranking them all is right; RENDERING them
# all is a hung page, so the panel draws the best of them and says so.
BOOST_ROWS_SHOWN = 50
# Same reasoning applies to the plain opportunity list below, which had no cap
# at all -- every row is a full st.container + st.dataframe, and each of those
# carries real fixed component overhead. Unbounded rendering of that list was
# a direct cause of choppy scrolling; see HANDOFF.md §7.
OPP_ROWS_SHOWN = 100

st.set_page_config(page_title="Arbitrage", page_icon="⚖️", layout="wide")
st.title("⚖️ Arbitrage · DraftKings / FanDuel / Fanatics")


@st.cache_data(show_spinner=False, max_entries=4)
def _parse_snapshot(mtime: float) -> dict | None:
    # mtime, not a TTL: this is a 20-30MB file re-read from disk and
    # re-json.loads'd on every widget interaction otherwise -- twice per
    # rerun, since the sidebar peeks at it separately from the main render.
    # Keying on mtime means a rerun that changes nothing about the file
    # costs nothing, while a fresh scan (live button, agent push, git pull
    # redeploy) is picked up on the very next rerun, no TTL to wait out.
    try:
        return json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        return None


def load_snapshot() -> dict | None:
    if not SNAPSHOT.exists():
        return None
    return _parse_snapshot(SNAPSHOT.stat().st_mtime)


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


def kickoff_label(iso: str) -> str:
    """'Sat 3:30 PM' in US/Eastern, for picking one game out of a list.

    ET for the same reason `event_date_et` uses it: it is the books' own
    timezone and the one every CT bettor reads a kickoff in. Built by hand
    rather than with strftime's `%-I` because that flag is a glibc
    extension -- it is fine on Streamlit Community Cloud and raises on
    Windows, and this is the kind of line nobody would think to test there.
    """
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(ET)
    return f"{dt:%a} {dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


@st.cache_data(show_spinner=False, max_entries=4)
def game_choices(mtime: float, titles: dict) -> dict[str, tuple[str, str, str]]:
    """{event_id: (dropdown label, matchup on its own, sport_key)}.

    The label is 'NCAAF · Illinois State @ Northern Illinois · Sat 7:00 PM'.
    The bare matchup is carried alongside rather than sliced back out of the
    label, because a message that has to name the picked game wants the short
    form and splitting on " · " would break on the first matchup containing
    one. The sport_key rides along so the boost panels can tell that a token
    scoped to one game and to a different sport can never match anything.

    Keyed on `event_id`, not on the matchup text: two different games can
    carry the same matchup string (a doubleheader, or the same two colleges
    meeting in two sports), and the whole point of this control is to pick
    ONE game.

    Drawn from `candidates` and `middle_candidates` as well as
    `opportunities`, because the boost panels are priced from those two --
    the workflow this exists for is "a token is scoped to one game", and
    with a boost entered it is the boost panel doing the scrolling. A game
    that is only in `candidates` is still worth offering; it simply has
    nothing on the plain board yet, which `_empty_reason` says out loud
    rather than leaving the picker looking broken.

    Sorted by sport, then kickoff, then matchup. Streamlit's multiselect has
    no option groups, so sorting is the grouping -- one league's games land
    together in the dropdown, which is what makes 77 of these thumbable on a
    phone instead of a wall.

    Cached on the snapshot's mtime for the same reason `_parse_snapshot` is:
    this walks all three sections (~30,000 rows on a full slate, ~12ms) and
    would otherwise redo it on every widget click. See HANDOFF.md §7 on the
    page's choppiness.
    """
    snapshot = load_snapshot() or {}
    seen: dict[str, tuple[str, str, str, str]] = {}
    for section in ("opportunities", "candidates", "middle_candidates"):
        for row in (snapshot.get(section) or []):
            event_id = row.get("event_id")
            if not event_id or event_id in seen:
                continue
            key = row.get("sport_key") or ""
            # The snapshot's own `sport_title` is frequently just the raw
            # sport_key ("americanfootball_ncaaf"), so prefer the display
            # names sport_choices() builds and fall back only if it has none.
            sport = titles.get(key) or row.get("sport_title") or key
            seen[event_id] = (sport, row.get("commence_time") or "",
                              row.get("matchup") or event_id, key)
    ordered = sorted(seen.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[1][2]))
    # The sport prefix earns its width only when there is more than one to
    # tell apart. A Saturday snapshot is routinely all NCAAF -- 77 rows each
    # opening with the same eight characters, on a screen narrow enough that
    # the kickoff is what gets truncated instead.
    one_sport = len({s for _, (s, _, _, _) in ordered}) < 2
    return {event_id: (" · ".join(p for p in (
                           "" if one_sport else sport, matchup, kickoff_label(ct)) if p),
                       matchup, key)
            for event_id, (sport, ct, matchup, key) in ordered}


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
    book_state = st.text_input(
        "State", value="CT", max_chars=2,
        help="Which state's DraftKings/FanDuel skin to scan. Sportsbooks "
             "price state by state — a market (an alternate line especially) "
             "can be live in one state's app before it is approved in "
             "another's. Applies to both Scan live and Request a desktop "
             "scan. Defaults to Connecticut.").strip().upper() or "CT"

    _sport_choices = _from_scan_request(
        "sport_choices", lambda cfg, snap: dict(sorted(FALLBACK_SPORTS.items(),
                                                       key=lambda kv: kv[1])))
    _snap_peek = load_snapshot() or {}
    _sport_titles = _sport_choices(ArbConfig(), _snap_peek)
    _in_snapshot = {c.get("sport_key") for c in (_snap_peek.get("candidates") or [])}
    _market_choices = _from_scan_request("market_choices", lambda snap: {})
    _market_groups = _market_choices(_snap_peek)
    # One index, two consumers: the per-boost "Boost N game" picker below and
    # the "Filter by game" display filter further down. Computed here with
    # the other choice lists rather than next to either widget, so the two
    # can never end up offering different games out of the same snapshot.
    _games = game_choices(
        SNAPSHOT.stat().st_mtime if SNAPSHOT.exists() else 0.0, _sport_titles)
    _game_titles = {k: v[0] for k, v in _games.items()}
    _game_names = {k: v[1] for k, v in _games.items()}
    _games_sport = {k: v[2] for k, v in _games.items()}

    st.divider()
    st.subheader("Scan filters")
    st.caption("Sport and date both narrow the SCAN itself, not just what's "
               "shown afterward — combine them for the fastest scan. A sport "
               "alone still crawls the full rolling window (10 days by "
               "default), so \"NCAAF\" without a date still fetches every "
               "NCAAF game through next Saturday.")
    scan_sports = st.multiselect(
        "Restrict scan to sport(s)", options=list(_sport_titles),
        format_func=lambda k: _sport_titles.get(k, k), default=[],
        help="Leave empty to scan every league. Applies to both Scan live "
             "and Request a desktop scan.")

    _today_et = datetime.now(ET).date()
    date_mode = st.selectbox(
        "Game date", ["All upcoming", "Today", "Tomorrow", "Next 7 days", "Custom range"],
        help="Also narrows the scan itself (both Scan live and Request a "
             "desktop scan), not just what's shown -- filters by the event's "
             "calendar date in US/Eastern, the books' own timezone. A late "
             "kickoff can already be tomorrow in UTC, so this is not the "
             "same as filtering on the raw timestamp.")
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

    casino_mode = st.checkbox(
        "🎰 Casino mode (DraftKings + FanDuel only)", value=False,
        help="For standing at a physical sportsbook window with only these "
             "two books open. Keeps only positions where every leg is at "
             "DraftKings or FanDuel, drops Fanatics-only legs, and re-ranks "
             "the list: a real arbitrage or free middle (zero risk) always "
             "leads, then everything else -- including gap plays, crossed "
             "lines where you lose only in a narrow band -- ordered by the "
             "smallest chance of landing on the losing result. Good for "
             "low-risk volume toward casino comps, not for expected value.")

    st.divider()
    st.caption("A live scan takes ~40s and spends **no** API credits. "
               "It needs a connection the books accept — that usually means "
               "your own machine, not a cloud host.")
    run_live = st.button("🔄 Scan live", width="stretch")

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
    request_scan = st.button("📡 Request a desktop scan", width="stretch",
                             disabled=bool(_cred_problem))
    if _cred_problem:
        st.caption(f"⚠️ {_cred_problem}. Set `GITHUB_REPO` and `GITHUB_TOKEN` in "
                   "the app's Settings → Secrets.")

    st.divider()
    st.header("Profit boosts")
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
               "a locked profit. Two DIFFERENT tokens on two different books' "
               "legs — e.g. DraftKings' and FanDuel's — stack on the same "
               "market, since each is its own bet slip.")

    def _min_decimal(american: int) -> float:
        """'Min Total Odds of -200' -> 1.5, the decimal Boost.min_decimal wants."""
        if american == 0:
            return 1.0
        return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))

    # Boost fields are seeded from the URL's query string, not just a literal
    # default, and written back to it on every rerun. `st.session_state` lives
    # only as long as this browser tab's session on the server, and that
    # session can vanish with no warning: backgrounding this tab (switching
    # to another app on a phone) commonly suspends it, and reconnecting can
    # land on a brand-new server-side session with everything back at
    # defaults -- which reads exactly like an unwanted page refresh, because
    # for widget state it effectively is one. The query string is part of the
    # URL itself, so it survives that; seeding widgets from it turns "boosts
    # silently vanished" into "boosts silently restored." Scoped to the boost
    # panels because that's what was reported lost -- the same pattern would
    # apply to the Scan/Results filters below if those start disappearing too.
    def _qp_str(name: str, default: str) -> str:
        v = st.query_params.get(name)
        return v if v is not None else default

    def _qp_int(name: str, default: int) -> int:
        try:
            return int(st.query_params.get(name, default))
        except (TypeError, ValueError):
            return default

    def _qp_bool(name: str, default: bool) -> bool:
        v = st.query_params.get(name)
        return default if v is None else v == "1"

    def _qp_list(name: str) -> list[str]:
        v = st.query_params.get(name, "")
        return [s for s in v.split(",") if s]

    # WRITES ARE COLLECTED, NOT SENT (2026-09-11). Every `st.query_params[k] =
    # v` is its own ForwardMsg -- streamlit/runtime/state/query_params.py
    # calls _send_query_param_msg() from __setitem__ unconditionally, even
    # when the value is identical -- and the browser turns each one into a
    # history.pushState(). The eight fields per panel across two panels meant
    # SIXTEEN pushStates on every rerun, changed or not.
    #
    # Safari and Brave refuse more than 100 pushStates per 10 seconds, so
    # seven reruns in ten seconds was enough to break the page with
    # "Bad message format: Attempt to use history.pushState() more than 100
    # times per 10 seconds" -- which on a phone is one drag of the boost
    # slider, or a few taps on the sport filter. Reported from an iPhone on
    # 2026-09-11.
    #
    # So: gather the desired values here, and flush ONCE below, and only if
    # they actually differ from what the URL already says. A rerun that
    # changes no boost field now sends nothing at all, which is the common
    # case (sidebar filters, the Scan button, a reconnect).
    _qp_want: dict[str, str] = {}

    from edge.arb.engine import Boost

    _boost_book_order = {1: ["draftkings", "fanduel", "fanatics"],
                         2: ["fanduel", "draftkings", "fanatics"]}
    boosts: list[Boost] = []
    for _n in (1, 2):
        _qp = f"b{_n}_"
        with st.expander(f"Boost {_n}", expanded=True):
            _pct = st.slider(f"Boost {_n} %", 0, 100, _qp_int(_qp + "pct", 0), 5,
                             key=_qp + "pct",
                             help="0 turns this boost off. Profit boosts multiply "
                                  "your NET winnings, not the total return.")
            _qp_want[_qp + "pct"] = str(_pct)

            _book_opts = _boost_book_order[_n]
            _book_default = _qp_str(_qp + "book", _book_opts[0])
            _book = st.selectbox(
                f"Boost {_n} book", _book_opts,
                index=_book_opts.index(_book_default) if _book_default in _book_opts else 0,
                key=_qp + "book", format_func=lambda b: BOOK_NAMES.get(b, b))
            _qp_want[_qp + "book"] = _book
            _max_stake = st.number_input(
                f"Boost {_n} max stake ($)", 1, 5_000, _qp_int(_qp + "stake", 10), step=5,
                key=_qp + "stake",
                help="The token's cap. This bounds the WHOLE position, not just "
                     "the boosted leg — the hedge is sized off it.")
            _qp_want[_qp + "stake"] = str(_max_stake)
            _sport_opts = ["(every sport)"] + list(_sport_titles)
            _sport_default = _qp_str(_qp + "sport", "(every sport)")
            _sport = st.selectbox(
                f"Boost {_n} sport", _sport_opts,
                index=_sport_opts.index(_sport_default) if _sport_default in _sport_opts else 0,
                key=_qp + "sport",
                format_func=lambda k: (
                    "(every sport)" if k == "(every sport)"
                    # a sport the current snapshot cannot answer for is still
                    # selectable, but say so rather than silently returning nothing
                    else _sport_titles[k] + ("" if k in _in_snapshot else "  · not in snapshot")),
                help="The sport this token is tied to. Sports missing from the "
                     "current snapshot are still listed — request a desktop "
                     "scan to cover them.")
            _qp_want[_qp + "sport"] = _sport
            _sport = "" if _sport == "(every sport)" else _sport
            if _sport and _sport not in _in_snapshot:
                st.caption(f"⚠️ The current snapshot has no {_sport_titles[_sport]} "
                           "markets, so nothing can be found for it yet.")

            # The narrowest scope a book issues, and the one it issues most.
            # This is NOT the "Filter by game" control further down: that one
            # hides rows after the fact, this one tells the SEARCH the token
            # is only good on this game, so a boosted arbitrage is never
            # reported in a game the token cannot be spent on. Same class of
            # rule as parlay-only and minimum odds -- a number on screen for
            # a bet the book would refuse is worse than no number.
            #
            # When the picker offers a game the boost sport does not cover,
            # the game wins on its own: Boost.applies_to ANDs its filters, so
            # a mismatched pair simply finds nothing. Said out loud below
            # rather than left as a silently empty panel.
            _bgame_opts = ["(every game)"] + list(_game_titles)
            _bgame_default = _qp_str(_qp + "game", "(every game)")
            _bgame = st.selectbox(
                f"Boost {_n} game", _bgame_opts,
                index=(_bgame_opts.index(_bgame_default)
                       if _bgame_default in _bgame_opts else 0),
                key=_qp + "game",
                format_func=lambda g: ("(every game)" if g == "(every game)"
                                       else _game_titles.get(g, g)),
                help="For a token scoped to one matchup — \"50% profit boost "
                     "on Ohio State vs Michigan\". Leave on every game for a "
                     "sport-wide or market-wide token. Kickoffs are "
                     "US/Eastern.")
            _qp_want[_qp + "game"] = _bgame
            _bgame = "" if _bgame == "(every game)" else _bgame
            if _bgame and _sport and _games_sport.get(_bgame) not in (None, _sport):
                st.caption(f"⚠️ {_game_names.get(_bgame, _bgame)} is not "
                           f"{_sport_titles.get(_sport, _sport)}, and a token "
                           "has to satisfy both — clear one of them.")

            _market_opts = ["(every market)"] + list(_market_groups)
            _market_default = _qp_str(_qp + "market", "(every market)")
            _market = st.selectbox(
                f"Boost {_n} markets", _market_opts,
                index=_market_opts.index(_market_default) if _market_default in _market_opts else 0,
                key=_qp + "market",
                help="Boosts are often scoped to a market type as well as a "
                     "sport — a batter-props token cannot be used on a game line.")
            _qp_want[_qp + "market"] = _market
            _markets = _market_groups.get(_market, [])
            _min_odds = st.number_input(
                f"Boost {_n} min odds on the boosted leg (American)", -1000, 1000,
                _qp_int(_qp + "odds", -200), step=10, key=_qp + "odds",
                help="Most tokens carry a floor — 'Min Total Odds of -200'. A "
                     "shorter leg does not qualify and the book refuses it at the slip.")
            _qp_want[_qp + "odds"] = str(_min_odds)
            _sides = st.multiselect(
                f"Boost {_n} side", ["over", "under", "home", "away", "yes", "no"],
                default=_qp_list(_qp + "sides"), key=_qp + "sides",
                help="Leave empty for any. DraftKings' 'Batter Props Milestones' "
                     "are the over-only ladders, so that token is over only.")
            _qp_want[_qp + "sides"] = ",".join(_sides)
            _parlay = st.checkbox(
                f"Boost {_n} parlay only", value=_qp_bool(_qp + "parlay", False),
                key=_qp + "parlay",
                help="Books offer the same headline boost twice — straight bets "
                     "and parlays. Only the straight-bet one can be hedged, "
                     "because each side of an arbitrage is its own single bet.")
            _qp_want[_qp + "parlay"] = "1" if _parlay else "0"
            if _pct > 0:
                boosts.append(Boost(
                    book=_book, pct=_pct / 100.0, max_stake=float(_max_stake),
                    sports=[_sport] if _sport else [],
                    events=[_bgame] if _bgame else [], markets=_markets,
                    sides=list(_sides), min_decimal=_min_decimal(_min_odds),
                    requires_parlay=_parlay,
                    # The game goes in the label too. Every row the panel
                    # draws says which token it needs, and "50% boost on
                    # DraftKings" on a board narrowed to one game reads as
                    # though any DraftKings token would do.
                    label=(f"{_pct}% boost on {BOOK_NAMES.get(_book, _book)}"
                          + (f" ({_market.lower()})" if _markets else "")
                          + (f" · {_game_names.get(_bgame, _bgame)} only"
                             if _bgame else ""))))

    boost_mode = st.radio(
        # Named distinctly from the top "Show" multiselect (arb/middle/ev) --
        # the test harness answers widgets by label, and the two are only
        # different widget TYPES, not different labels. See HANDOFF.md on the
        # "Filter by sport" rename for the same trap.
        "Boosted view", ["Arbitrage (hedged)", "Best +EV (unhedged)"], index=0,
        help="A boost no second book can cover is not wasted — it stops being "
             "an arbitrage and becomes an +EV bet. Use that view when nothing "
             "can hedge the boosted side.")

    st.divider()
    st.header("Results")
    st.caption("These only change what's shown from an existing scan — none "
               "of them make a scan itself faster. For that, use Scan "
               "filters above.")
    kinds = st.multiselect("Show", ["arb", "middle", "gap", "ev"],
                           default=["arb", "middle", "ev"],
                           format_func=lambda k: {"arb": "Arbitrage", "middle": "Middles",
                                                  "gap": "Gaps", "ev": "+EV"}[k],
                           help="Gaps (crossed lines, small chance of losing both legs) "
                                "are off by default -- Casino mode below always includes "
                                "them regardless of this.")
    min_profit = st.slider("Minimum %", 0.0, 20.0, 0.0, 0.25)
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

    # Books issue profit boosts scoped to ONE GAME ("50% on Ohio State vs
    # Michigan"), which is the level nothing else on this page filters at.
    # Without it the workflow is: enter the token in the boost panel above,
    # then scroll fifty NCAAF games looking for the one it can be spent on --
    # on a phone, minutes before kickoff, that is the whole session.
    #
    # Labelled "Filter by game" and not "Game", distinctly from "Game date"
    # above and from the boost panels' "Boost N sport": tests/test_arb_page.py
    # answers widgets BY LABEL and cannot tell two same-labelled controls
    # apart. See HANDOFF.md on the "Filter by sport" rename.
    # An event_id seeded from the URL that this snapshot no longer contains
    # (a fresh scan since the link was made, or a different slate) is dropped
    # rather than passed through: real Streamlit raises StreamlitAPIException
    # on a multiselect default that is not among its options, which would
    # take the whole page down for what is only a stale bookmark.
    _game_seed = [g for g in _qp_list("arb_games") if g in _game_titles]
    game_filter = st.multiselect(
        "Filter by game", options=list(_game_titles), default=_game_seed,
        key="arb_games", format_func=lambda g: _game_titles.get(g, g),
        help="For a boost that is tied to one game. Kickoffs are US/Eastern, "
             "the books' own timezone. Options come from the current "
             "snapshot only — request a fresh scan to see a later slate.")
    # Namespaced. The query string belongs to the whole app, not to this
    # page -- a bare "games" is exactly the key another page would reach for,
    # and the two would then seed each other with ids neither understands.
    # (`b1_*`/`b2_*` above get away with it by being distinctive by accident.)
    _qp_want["arb_games"] = ",".join(game_filter)

    show_stale_alt_lines = st.checkbox(
        "Show alt lines far from the book's main line", value=False,
        help="Off by default. A book's alternate-total/spread ladder is built "
             "around its own current main line; when the ladder's own "
             "best-priced rung has drifted more than a few points from that "
             "line, the book has moved the real number and not yet repriced "
             "or pulled the ladder -- the price is real, but likely to "
             "vanish before you can bet it, the same way an alt total sat at "
             "52.5 for hours after DraftKings moved the real total to 62.5. "
             "Flagged opportunities carry a warning explaining which leg and "
             "by how much; turn this on to see them anyway.")

    # The single flush, and it has to stay single. `update()` exists
    # precisely for this -- its own comment in streamlit says it overrides
    # MutableMapping's version "to ensure only one ForwardMsg is sent" -- but
    # the guard in front of it is what does the real work, because the
    # overwhelming majority of reruns change no persisted field and should
    # touch the URL zero times.
    #
    # It sits HERE, at the very end of the sidebar, rather than directly
    # under the boost panels where it was written: the game filter is the
    # first persisted field declared outside those panels, and a second
    # `update()` for it would double this page's pushState count instead of
    # leaving it at one. Anything else that wants to survive a lost session
    # goes in `_qp_want` above this line -- never as a bare
    # `st.query_params[k] = v`, which is its own ForwardMsg and its own
    # history.pushState(), and which Safari and Brave cap at 100 per 10
    # seconds. Sixteen per rerun is what broke this page on an iPhone on
    # 2026-09-11. See tests/test_arb_page_url.py.
    #
    # `get(k, "")` and not `get(k)`: an empty value (the "sides" multiselect
    # with nothing picked, or this game filter) is stored by dropping the key
    # entirely -- is_empty_url_value() treats "" as "cleared" -- so a missing
    # key and "" are the same state and must compare equal. Reading it with
    # the same default _qp_list already uses is what makes the comparison
    # symmetric; without it the two sides keys never matched and the flush
    # fired on every single rerun, which is most of the bug still present.
    if any(st.query_params.get(k, "") != v for k, v in _qp_want.items()):
        st.query_params.update(_qp_want)

snap = load_snapshot()

# ------------------------------------------------------- desktop scan request
if request_scan:
    try:
        ScanRequest = _from_scan_request("ScanRequest")
        put_request = _from_scan_request("put_request")
        if ScanRequest is None or put_request is None:
            raise RuntimeError("stale module — reboot the app")
        req = ScanRequest.new(
            sports=list(scan_sports), note="requested from the Streamlit app",
            date_from=date_range[0].isoformat() if date_range else None,
            date_to=date_range[1].isoformat() if date_range else None,
            skip_live=not show_live, state=book_state)
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
        cfg.state = book_state
        cfg.bankroll.total = float(bankroll)
        cfg.detect.skip_live = not show_live
        if date_range:
            cfg.detect.date_from = date_range[0].isoformat()
            cfg.detect.date_to = date_range[1].isoformat()
        if scan_sports:
            cfg.sports = list(scan_sports)
        # so a live scan's own opportunity list reflects stacked boosts too,
        # not just the candidate-repricing panel below
        cfg.boosts = list(boosts)

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
# Re-priced from the snapshot's `candidates`/`middle_candidates` rather than
# its opportunities: the markets a boost turns INTO arbitrages (or turns an
# ordinary middle free) are by definition not opportunities yet, so
# re-scoring the found list would miss every one of them.
boost_rows = []
mid_boost_rows = []
if boosts:
    from edge.arb.engine import price_candidates, price_middle_candidates, top_rows_per_sport

    def _live_and_date_filtered(rows):
        # "Filter by game" is applied here and not only to the plain board
        # below, because with a boost entered THIS is the list being
        # scrolled -- up to BOOST_ROWS_SHOWN rows priced off `candidates`.
        # A token scoped to one game whose panel still lists fifty is the
        # exact problem the control was added for.
        if game_filter:
            rows = [c for c in rows if c.get("event_id") in game_filter]
        if not show_live:
            rows = [c for c in rows if not is_live(c.get("commence_time", ""))]
        if date_range:
            rows = [c for c in rows if in_date_range(c.get("commence_time", ""), date_range)]
        return rows

    cands = snap.get("candidates") or []
    mid_cands = snap.get("middle_candidates") or []
    if not cands and not mid_cands:
        st.warning("This snapshot predates the boost feature — it has no "
                   "`candidates` section. Press **Scan live** (or re-run "
                   "`scripts/arb_scan.py`) to rebuild it.")
    else:
        cands = _live_and_date_filtered(cands)
        mid_cands = _live_and_date_filtered(mid_cands)
        if not cands and not mid_cands:
            st.warning(
                "Nothing remains after the "
                + ("game, live and date" if game_filter else "live and date")
                + " filters in the sidebar — widen \"Include live/in-progress "
                + ("games\", the game-date range, or \"Filter by game\""
                   if game_filter else "games\" or the game-date range")
                + " to see boosted candidates.")
        else:
            bcfg = ArbConfig()
            bcfg.bankroll.total = float(bankroll)
            bcfg.detect.min_profit_pct = float(min_profit)
            _boost_summary = ", ".join(b.describe() for b in boosts)

            if boost_mode.startswith("Best +EV"):
                from edge.arb.engine import price_boosted_ev
                ev_rows = (price_boosted_ev(cands, boosts, bcfg, min_ev_pct=float(min_profit))
                          if cands else [])
                if all(b.requires_parlay for b in boosts):
                    st.info("Every configured boost is parlay-only, which cannot "
                            "be priced as a single bet.")
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
                            }], hide_index=True, width="stretch")
                    st.caption("Place the boosted leg only. Nothing hedges it, so "
                               "most of these lose — the edge is in the price, not "
                               "in certainty.")
                st.divider()
                st.stop()

            boost_rows = price_candidates(cands, boosts, bcfg,
                                          min_profit_pct=float(min_profit)) if cands else []
            mid_boost_rows = price_middle_candidates(mid_cands, boosts, bcfg) if mid_cands else []

            if not boost_rows and not mid_boost_rows:
                st.warning(f"No market clears {min_profit:.2f}% with {_boost_summary} "
                           f"· {len(cands)} two-way and {len(mid_cands)} middle "
                           "candidates checked.")
            else:
                # -------------------------------------- highest floor/ceiling
                # What the sidebar's two boosts are FOR: across every boosted
                # arb and middle on the board, which single position has the
                # best guaranteed floor (ties broken by ceiling)? An arb's
                # floor and ceiling are ~equal (a hedge pays the same either
                # way); a middle's floor is the guaranteed-worst-case return
                # and its ceiling is the extra payout if the window lands.
                _best = None
                if boost_rows:
                    _r = boost_rows[0]
                    _best = ("arb", _r["floor_pct"], _r["ceiling_pct"], _r)
                if mid_boost_rows:
                    _m = mid_boost_rows[0]
                    if _best is None or _m["floor_pct"] > _best[1]:
                        _best = ("middle", _m["floor_pct"], _m["ceiling_pct"], _m)
                _kind, _floor, _ceiling, _row = _best
                with st.container(border=True):
                    st.markdown(f"### 🏆 Best floor/ceiling right now: "
                                f"**{_floor:+.2f}% floor** · "
                                f"**{_ceiling:+.2f}% ceiling**")
                    _note = ("straight arbitrage — same locked return either way"
                             if _kind == "arb" else
                             f"middle {_row['window'][0]:g}–{_row['window'][1]:g} — "
                             "floor if it misses, ceiling if it lands")
                    st.caption(f"{_row['sport_title']} · **{_row['matchup']}** · "
                               f"{starts_in(_row.get('commence_time', ''))} · "
                               f"{_row['market']} · {_note}")

                if boost_rows:
                    shown = top_rows_per_sport(boost_rows, int(per_sport))[:BOOST_ROWS_SHOWN]
                    _plain = (price_candidates(cands, [], bcfg, min_profit_pct=float(min_profit))
                             if cands else [])
                    st.subheader(f"⚡ {len(boost_rows)} boosted "
                                 f"arbitrage{'s' if len(boost_rows) != 1 else ''}"
                                 + (f" · top {int(per_sport)} per sport"
                                    if int(per_sport) > 0 else ""))
                    if len(boost_rows) > len(shown):
                        st.caption(f"Showing the best {len(shown)} of {len(boost_rows)}. "
                                   f"Narrow with the sport filter or a per-sport cap "
                                   f"in the sidebar.")
                    st.caption(f"Without any boost the same board gives "
                               f"**{len(_plain)}**. Everything below needs its "
                               f"marked boost(s) applied — place the boosted "
                               f"leg(s) first and confirm they attached before hedging.")
                    for r in shown:
                        head = f"🟢 **{r['floor_pct']:+.2f}%** locked"
                        if abs(r["ceiling_pct"] - r["floor_pct"]) > 0.005:
                            head += f" · ceiling {r['ceiling_pct']:+.2f}%"
                        head += (f" · {money(r['profit_abs'])} on {money(r['stake_total'])} · "
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
                            } for l in r["legs"]], hide_index=True, width="stretch")
                    st.caption("Each boosted leg is capped at its token's max "
                               "stake, so the position is small by design. Place "
                               "the boosted leg(s) FIRST and confirm they applied "
                               "before placing the hedge — an unboosted first leg "
                               "leaves you with a plain "
                               f"{shown[0]['unboosted_pct']:+.2f}% position."
                               if shown else "")

                if mid_boost_rows:
                    _mid_shown = mid_boost_rows[:BOOST_ROWS_SHOWN]
                    st.subheader(f"🔵 {len(mid_boost_rows)} boosted middle "
                                 f"pairing{'s' if len(mid_boost_rows) != 1 else ''}")
                    st.caption("Two different point lines paired across books. "
                               "Boosting either leg raises the floor (guaranteed "
                               "even if the window misses) and, often, the "
                               "ceiling (if it lands) too.")
                    if len(mid_boost_rows) > len(_mid_shown):
                        st.caption(f"Showing the best {len(_mid_shown)} of "
                                   f"{len(mid_boost_rows)}.")
                    for r in _mid_shown:
                        if r["free_middle"]:
                            head = (f"🎯 **FREE MIDDLE** · +{r['floor_pct']:.2f}% "
                                    f"guaranteed · up to +{r['ceiling_pct']:.1f}% if it lands")
                        else:
                            head = (f"🔵 floor {r['floor_pct']:+.2f}% · "
                                    f"ceiling {r['ceiling_pct']:+.2f}% if it lands")
                        with st.container(border=True):
                            st.markdown(head)
                            sub = f" · {r['subject']}" if r.get("subject") else ""
                            st.caption(f"{r['sport_title']} · **{r['matchup']}** · "
                                       f"{starts_in(r.get('commence_time',''))} · "
                                       f"{r['market']}{sub} · window "
                                       f"{r['window'][0]:g}–{r['window'][1]:g}")
                            st.dataframe([{
                                "Book": BOOK_NAMES.get(l["book"], l["book"]),
                                "Bet": l["label"],
                                "Line": "" if l.get("point") is None else f"{l['point']:g}",
                                "Book odds": l["raw_american"],
                                "Boost": f"+{l['boost_pct']:.0%}" if l["boost_pct"] else "—",
                                "Pays": l["american"],
                                "Stake": money(l["stake"]),
                                "Returns": money(l["payout"]),
                            } for l in r["legs"]], hide_index=True, width="stretch")
                            if r.get("boost"):
                                st.caption(
                                    f"Needs {r['boost']} applied — without it this "
                                    f"middle's floor is {r['unboosted_floor_pct']:+.2f}% "
                                    f"and ceiling {r['unboosted_ceiling_pct']:+.1f}%.")
    st.divider()

# Casino mode always wants gap plays on the board regardless of the Show
# picker above -- that is the whole point of turning it on -- so it is added
# to whatever kinds are already checked rather than requiring a second click.
_effective_kinds = set(kinds) | ({"gap"} if casino_mode else set())
_all_opps = snap.get("opportunities", [])
opps = [o for o in _all_opps
        if o.get("kind") in _effective_kinds and o.get("profit_pct", 0) >= min_profit]
if sport_filter:
    opps = [o for o in opps
            if (o.get("sport_title") or o.get("sport_key")) in sport_filter]
# Before the live count below, so that picking a game that has already kicked
# off is reported as "the game you picked has started", not as "this whole
# scan is stale" -- see _empty_reason.
if game_filter:
    opps = [o for o in opps if o.get("event_id") in game_filter]
# Counted before the live filter runs, because "everything in this snapshot
# has already kicked off" and "there was nothing to find" are opposite
# situations that used to render the same sentence -- see _empty_reason.
_before_live = len(opps)
if not show_live:
    opps = [o for o in opps if not is_live(o.get("commence_time", ""))]
_lost_to_live = _before_live - len(opps)
if date_range:
    opps = [o for o in opps if in_date_range(o.get("commence_time", ""), date_range)]
if casino_mode:
    opps = [o for o in opps
           if all(l.get("book") in CASINO_BOOKS for l in o.get("legs", []))]
if not show_stale_alt_lines:
    opps = [o for o in opps if not o.get("stale_alt_line")]


def _picked_games() -> str:
    """The games "Filter by game" is holding, named the way you picked them.

    One game gets its matchup; several get a count, because three matchups
    inline is longer than the sentence around them on a phone.
    """
    if len(game_filter) == 1:
        return _game_names.get(game_filter[0], game_filter[0])
    return f"the {len(game_filter)} games you picked"


def _empty_reason() -> str:
    """Why the board is empty, in the words that tell you what to DO about it.

    Added 2026-09-11. The page used to say one thing for every empty board:
    "days with no arbitrage are normal". That is true and reassuring, and on
    2026-09-10 it was also wrong -- the snapshot held 30 live opportunities
    and every one had already commenced, so the honest answer was "this scan
    is old, ask for a new one", the opposite of "nothing to find here".

    Reading a reassurance when the real answer is "tap Scan" costs a whole
    session, so the reassurance is now only printed when it is actually true:
    the scan genuinely found nothing that matches.
    """
    if _lost_to_live and not _before_live - _lost_to_live:
        # Scoped to the picked game when there is one. "All N opportunities
        # in this scan have already kicked off" is a claim about the WHOLE
        # scan, and with a game filter on it is simply false -- the rest of
        # the board can be perfectly fresh while the one game you picked has
        # started, which minutes before kickoff is the likeliest case of all.
        # Sending someone to request a new scan they do not need is the same
        # kind of wasted session the honest empty state was written to stop.
        if game_filter:
            return (f"{_picked_games()} {'has' if len(game_filter) == 1 else 'have'} "
                    f"already kicked off — you cannot take these prices any more. "
                    f"The rest of the board may still be live: clear "
                    f"*Filter by game*, or tick *Include live/in-progress games* "
                    f"to look at {'it' if len(game_filter) == 1 else 'them'} anyway.")
        return (f"All {_lost_to_live} opportunit{'y' if _lost_to_live == 1 else 'ies'} "
                f"in this scan have already kicked off — you cannot take these "
                f"prices any more. This is a **stale scan, not a quiet board**: "
                f"request a fresh one above, or tick *Show live* to look at them "
                f"anyway.")
    if casino_mode:
        # Casino mode wins the ordering because its rule is the narrowest --
        # it drops whole legs, not whole games -- but a game filter set on
        # top of it is still the cheaper thing to relax first, so say so.
        return ("Nothing clears these filters, and only DraftKings/FanDuel legs "
                "qualify in Casino mode — try "
                + (f"clearing *Filter by game* ({_picked_games()}), widening "
                   "the date range" if game_filter else "widening the date range")
                + " or turning it off.")
    if game_filter:
        # Named ahead of the generic filters line because it is the filter
        # most likely to be the one at fault: every other control on this
        # page keeps whole leagues, and this one keeps a single game out of
        # a slate. A game can also be offered by the picker while having no
        # opportunity of its own -- the options come from `candidates` too,
        # since that is what the boost panels price -- so say that rather
        # than let the picker look broken.
        return (f"No opportunity in {_picked_games()} clears the filters above. "
                f"The boost panels may still have rows for "
                f"{'it' if len(game_filter) == 1 else 'them'}; otherwise widen "
                f"the date range, lower the minimum profit, or clear "
                f"*Filter by game* to see the rest of the board.")
    if _all_opps:
        return (f"This scan found {len(_all_opps)} opportunit"
                f"{'y' if len(_all_opps) == 1 else 'ies'}, but none clear the "
                f"filters above — widen the date range, lower the minimum "
                f"profit, or add a sport.")
    return ("Nothing clears these filters. With three books, days with no "
            "arbitrage are normal — middles and +EV are the usual finds.")

if not opps:
    if not boost_rows:
        st.warning(_empty_reason())
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


def _casino_rank(o: dict) -> tuple[bool, float]:
    """(is a zero-risk position, -risk_pct) for Casino mode.

    A real arbitrage or a free middle has no downside either way, so it
    always leads regardless of size -- same principle as `_rank`. Everything
    else is ordered by the SMALLEST chance of landing on its worst outcome,
    since the point of hunting these out at a casino window is safe volume
    toward comps, not the biggest edge.

    Unmeasured risk (no CDF ladder deep enough on that market) must sort as
    WORSE than a measured one, not better -- absence of a number is not
    evidence of safety.
    """
    zero_risk = o.get("kind") == "arb" or bool(o.get("free_middle"))
    risk = o.get("risk_pct")
    return (zero_risk, -float(risk if risk is not None else 101.0))


opps.sort(key=_casino_rank if casino_mode else _rank, reverse=True)

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

if len(opps) > OPP_ROWS_SHOWN:
    st.caption(f"Showing the best {OPP_ROWS_SHOWN} of {len(opps)}. Narrow with "
               "the sport or game filter, a per-sport cap, or a higher "
               "minimum % in the sidebar.")
    opps = opps[:OPP_ROWS_SHOWN]

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
    elif kind == "gap":
        # The mirror image of a middle's head: NOT guaranteed, so lead with
        # the risk of the bad outcome rather than the normal-case return --
        # that risk is exactly what a "smallest risk" casino search sorts on.
        hits = o.get("hit_values") or []
        on = "/".join(str(h) for h in hits[:4]) or "the gap"
        risk = o.get("risk_pct")
        risk_str = f"{risk:.1f}%" if risk is not None else "unmeasured"
        head = (f"{icon} risk **{risk_str}** of losing both legs on {on}  \n"
                f"+{o['profit_pct']:.2f}% otherwise (not guaranteed)")
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
        st.dataframe(rows, hide_index=True, width="stretch")
        for w in o.get("warnings", []):
            st.warning(w, icon="⚠️")

st.divider()
st.caption("Confirm both prices in the apps before staking — lines move in "
           "seconds. Fanatics Markets is reference only and is never a leg.")
