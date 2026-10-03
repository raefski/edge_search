"""pages/9_🎰_Parlay_Builder.py — spend a parlay profit-boost token well.

DraftKings and FanDuel give out boosts that only work on parlays. This page
takes the token's rules (a preset, the promo text pasted from the app, or typed
in), scans the whole sport at the token's book, prices every leg against the
rest of the market, and returns the parlay with the best expected value at the
hit rate you are willing to live with -- plus the frontier of other choices.

All the maths and the reasoning behind it is in edge/arb/parlay.py; the short
version is printed in the "How this works" expander at the bottom.

Data is the same snapshot the Arbitrage page reads (data/arb_snapshot.json),
off GitHub's main when credentials exist, so a desktop scan shows here without
a redeploy. Traditional parlays only -- one leg per game. SGP / SGPx prices
come from each book's own correlation model and cannot be rebuilt from
single-leg prices.
"""
from __future__ import annotations

import html
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Parlay Builder", page_icon="🎰", layout="wide",
                   initial_sidebar_state="collapsed")

from edge.dfs_pagereload import reload_packages, source_fingerprint  # noqa: E402

_PKGS = ("edge.arb", "edge.repo_files")
_GLOBS = ("edge/arb/*.py", "edge/repo_files.py")


@st.cache_resource(show_spinner=False)
def _reload_edge(fingerprint: float) -> float:
    reload_packages(_PKGS)
    return fingerprint


_reload_edge(source_fingerprint(_GLOBS))

from edge import repo_files  # noqa: E402
from edge.arb import ArbConfig, oddsmath as om, parlay as PL  # noqa: E402
from edge.arb import scan_request as SR  # noqa: E402

ET = ZoneInfo("America/New_York")
SNAPSHOT = ROOT / "data" / "arb_snapshot.json"
STALE_MINUTES = 45

st.markdown("""
<style>
.block-container {padding-top: 2.0rem; padding-bottom: 2rem; max-width: 900px;}
h1 {font-size: 1.5rem !important; margin-bottom: .1rem;}
.sub {font-size: 13px; color: #9aa4b2; line-height: 1.5; margin: 0 0 .6rem;}
.kpis {display:flex; flex-wrap:wrap; gap:6px 18px; margin:.3rem 0 .5rem;}
.kpi {min-width: 130px;}
.kpi .v {font-size: 1.25rem; font-weight: 700;}
.kpi .l {font-size: 11px; color:#9aa4b2; text-transform: uppercase; letter-spacing:.03em;}
.good {color:#3fb950;} .bad {color:#f85149;} .muted {color:#9aa4b2;}
.tw {overflow-x:auto;}
table.pl {width:100%; border-collapse:collapse; font-size:14px;}
table.pl th {text-align:left; color:#7f8a9c; font-weight:600; font-size:11px;
             text-transform:uppercase; padding:3px 6px; border-bottom:1px solid rgba(128,128,128,.35);}
table.pl td {padding:6px 6px; border-bottom:1px solid rgba(128,128,128,.15); vertical-align:top;}
table.pl td.n {text-align:right; white-space:nowrap; font-variant-numeric: tabular-nums;}
table.pl .g {font-size:12px; color:#9aa4b2;}
table.pl tr.pick td {background: rgba(63,185,80,.10);}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------- formatting
def esc(x) -> str:
    return html.escape(str(x))


def kickoff(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso).astimezone(ET)
    except (TypeError, ValueError):
        return ""
    return dt.strftime("%a %-I:%M%p").replace("AM", "a").replace("PM", "p")


def et_date(iso: str) -> date | None:
    try:
        return datetime.fromisoformat(iso).astimezone(ET).date()
    except (TypeError, ValueError):
        return None


def money(x: float) -> str:
    return f"${x:,.2f}" if abs(x) < 100 else f"${x:,.0f}"


def signed_pct(x: float) -> str:
    return f"{x:+.1%}"


def one_in(p) -> str:
    return f"1 in {p.one_in:,.0f}" if p.one_in < 1e7 else "—"


def age_minutes(iso: str | None) -> float | None:
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


def age_text(minutes: float | None) -> str:
    if minutes is None:
        return "unknown age"
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    return f"{minutes / 60:.1f} h ago"


def american_or_blank(dec: float) -> str:
    return "" if dec <= 1.0 + 1e-9 else om.format_american(dec)


def parse_american(text: str) -> float:
    """'' -> no floor; '-250' -> 1.40; '+300' -> 4.00."""
    t = (text or "").strip().replace("−", "-")
    if not t:
        return 1.0
    try:
        return PL.american_to_decimal(float(t))
    except (ValueError, ZeroDivisionError):
        st.warning(f"Could not read odds {text!r} — ignoring it.")
        return 1.0


def schedule_text(promo: PL.ParlayPromo) -> str:
    if not promo.boost_by_legs:
        return "0"
    if not promo.stepped:
        return f"{next(iter(promo.boost_by_legs.values())) * 100:g}"
    return ", ".join(f"{n}:{b * 100:g}" for n, b in sorted(promo.boost_by_legs.items()))


def parse_schedule(text: str, min_legs: int) -> dict[int, float]:
    """'50' -> {min_legs: .5}; '4:20, 5:25' -> {4: .2, 5: .25}; '0' -> {}."""
    t = (text or "").replace("%", "").strip()
    if ":" not in t:
        try:
            v = float(t or 0)
        except ValueError:
            st.warning(f"Could not read boost {text!r}.")
            return {}
        return {min_legs: v / 100.0} if v > 0 else {}
    out = {}
    for part in t.replace(";", ",").split(","):
        if ":" in part:
            n, _, v = part.partition(":")
            try:
                out[int(n.strip())] = float(v.strip()) / 100.0
            except ValueError:
                st.warning(f"Could not read step {part!r}.")
    return out


# ---------------------------------------------------------------- snapshot
@st.cache_data(show_spinner=False, max_entries=3)
def _parlay_snapshot_json(key: tuple, _text: str) -> dict:
    return json.loads(_text)


def load_snapshot():
    found = repo_files.read(SNAPSHOT)
    if found is None:
        return None, None
    try:
        return _parlay_snapshot_json((found.source, found.updated_at.isoformat(), len(found.data)),
                      found.data), found
    except ValueError:
        return None, found


@st.cache_data(show_spinner=False, max_entries=24)
def _parlay_legs(snap_key: tuple, _cands: list, promo: PL.ParlayPromo, allow_book_only: bool,
          max_leg_dec: float | None, days: tuple, now_bucket: int) -> list:
    now = datetime.fromtimestamp(now_bucket * 60, timezone.utc)
    legs = PL.legs_from_candidates(_cands, promo, now=now, allow_book_only=allow_book_only,
                                   max_leg_decimal=max_leg_dec)
    if days:
        legs = [l for l in legs if et_date(l.commence_time) in days]
    return legs


@st.cache_data(show_spinner=False, max_entries=24)
def _parlay_build(legs_key: tuple, _legs: list, promo: PL.ParlayPromo, n_legs, stake: float,
           max_one_in, bankroll: float):
    return PL.build(_legs, promo, n_legs=n_legs, stake=stake, max_one_in=max_one_in,
                    bankroll=bankroll)


snap, found = load_snapshot()

st.title("🎰 Parlay Builder")
st.markdown('<div class="sub">Pick the legs a parlay profit boost is worth most on. '
            'Every leg is priced at your token\'s book against the rest of the market; '
            'one leg per game, pregame only.</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------- token
preset_names = list(PL.PRESETS)
PASTE = "📋 Paste the promo text"
choice = st.selectbox("Your token", preset_names + [PASTE], index=0,
                      help="Presets are the offers DraftKings keeps rerunning. Every term "
                           "is editable under 'Token terms'.")
if choice == PASTE:
    pasted = st.text_area("Paste the offer's details from the app", height=140,
                          placeholder="MLB 50% PARLAY BOOST\nGet a 50% Profit Boost to use on "
                                      "a 3+ leg MLB Parlay ...")
    base, missing = PL.parse_promo_text(pasted) if pasted.strip() else (PL.ParlayPromo(), [])
    if pasted.strip():
        if "book" in missing:
            st.caption("The text doesn't name the book — assuming DraftKings.")
        missing = [m for m in missing if m != "book"]
        if missing:
            st.warning("Could not read: " + ", ".join(missing) + " — set them under Token terms.")
        else:
            st.caption(f"Read: {PL.BOOK_NAMES[base.book]} · "
                       f"{', '.join(SR.SPORT_TITLES.get(k, k) for k in base.sports) or 'any sport'} · "
                       f"{base.describe()} · "
                       f"max ${base.max_stake:g}"
                       + (f" · each leg {om.format_american(base.min_leg_decimal)} or longer"
                          if base.min_leg_decimal > 1 else "")
                       + (f" · ticket {om.format_american(base.min_total_decimal)} or longer"
                          if base.min_total_decimal > 1 else ""))
    wkey = f"paste{abs(hash(pasted)) % 10**8}"
else:
    base = PL.PRESETS[choice]
    wkey = f"pre{preset_names.index(choice)}"

cfg = ArbConfig()
sport_titles = SR.sport_choices(cfg, snap)
on_board = sorted({c.get("sport_key") for c in (snap or {}).get("candidates", [])})

with st.expander("Token terms", expanded=(choice == PASTE)):
    c1, c2 = st.columns(2)
    book = c1.radio("Book", ["draftkings", "fanduel"], horizontal=True,
                    index=0 if base.book != "fanduel" else 1,
                    format_func=lambda b: PL.BOOK_NAMES[b], key=f"book_{wkey}")
    sport_opts = sorted(set(sport_titles) | set(on_board) | set(base.sports) | {"soccer"})
    default_sport = base.sports[0] if base.sports else (on_board[0] if on_board else sport_opts[0])
    sport = c2.selectbox("Sport", sport_opts, index=sport_opts.index(default_sport),
                         format_func=lambda k: ("⚽ Soccer (every league)" if k == "soccer"
                                                else sport_titles.get(k, k)) +
                         ("" if k in on_board or (k == "soccer" and any(
                             s.startswith("soccer_") for s in on_board)) else "  (not on board)"),
                         key=f"sport_{wkey}")
    c1, c2, c3 = st.columns(3)
    min_legs = c1.number_input("Min legs", 1, 20, int(base.min_legs), key=f"minl_{wkey}")
    max_legs = c2.number_input("Max legs", 1, 20, int(max(base.max_legs, base.min_legs)),
                               key=f"maxl_{wkey}")
    stake_cap = c3.number_input("Max bet $", 1.0, 1000.0, float(base.max_stake), step=5.0,
                                key=f"stake_{wkey}")
    boost_txt = st.text_input("Boost % — one number for a flat boost, or legs:% steps",
                              schedule_text(base), key=f"boost_{wkey}",
                              help="Flat: 50  ·  Stepped: 4:20, 5:25, 6:30, ... 11:105  ·  None: 0")
    c1, c2 = st.columns(2)
    per_leg_txt = c1.text_input("Each leg at least (American)", american_or_blank(base.min_leg_decimal),
                                key=f"perleg_{wkey}", help="'-250 or longer per leg' → -250. Blank = none.")
    total_txt = c2.text_input("Whole ticket at least (American)",
                              american_or_blank(base.min_total_decimal), key=f"total_{wkey}",
                              help="'Total odds +300 or longer' → +300. Blank = none.")

promo = PL.ParlayPromo(
    book=book, sports=[sport], boost_by_legs=parse_schedule(boost_txt, int(min_legs)),
    min_legs=int(min_legs), max_legs=int(max(max_legs, min_legs)),
    min_leg_decimal=parse_american(per_leg_txt), min_total_decimal=parse_american(total_txt),
    max_stake=float(stake_cap))

c1, c2 = st.columns(2)
leg_opts = ["Best"] + list(range(promo.min_legs, promo.max_legs + 1))
legs_pick = c1.selectbox("Legs", leg_opts, index=0,
                         help="'Best' searches every allowed leg count.")
CAPS = {"1 in 10": 10, "1 in 25": 25, "1 in 50": 50, "1 in 100": 100, "1 in 250": 250,
        "1 in 1,000": 1000, "Any": None}
cap_label = c2.select_slider("Longest shot I'll take", list(CAPS), value="1 in 100",
                             help="Max EV alone picks lottery tickets (1 in thousands). This caps "
                                  "the pick; the table below shows every hit rate.")
c1, c2 = st.columns(2)
DAYS = {"Today": 0, "Today + tomorrow": 1, "All upcoming": None}
days_label = c1.radio("Games", list(DAYS), horizontal=True, index=0)
stake = c2.number_input("Bet $", 1.0, float(promo.max_stake), float(promo.max_stake), step=1.0)

with st.expander("More options"):
    c1, c2 = st.columns(2)
    max_leg_txt = c1.text_input("No leg longer than (American)", "",
                                help="e.g. +300 keeps every leg at +300 or shorter. Blank = none.")
    bankroll = c2.number_input("Bankroll for the Kelly row $", 100.0, 1e6,
                               float(cfg.bankroll.total), step=100.0)
    allow_book_only = st.checkbox(
        f"Use legs only {PL.BOOK_NAMES[book]} prices (fair = its own no-vig line)", value=True,
        help="Without another book on the market, the leg's 'edge' is just its vig — "
             "these never look +EV, but low-hold ones still fill a ticket.")

today = datetime.now(ET).date()
ndays = DAYS[days_label]
days = () if ndays is None else tuple(today + timedelta(days=i) for i in range(ndays + 1))

# ------------------------------------------------------------- board status
gen_at = (snap or {}).get("generated_at")
mins = age_minutes(gen_at)
creds = repo_files.credentials()
c1, c2 = st.columns([3, 2])
if snap:
    sports_line = ", ".join(sport_titles.get(s, s) for s in on_board[:6]) or "nothing"
    msg = f"Board: {sports_line} · scanned {age_text(mins)}"
    (c1.warning if (mins or 0) > STALE_MINUTES else c1.caption)(msg)
else:
    c1.info("No board yet — ask the desktop for a scan.")
scan_sports = SR.expand_sports([sport], sport_titles)
if c2.button(f"🔄 Rescan {('soccer' if sport == 'soccer' else sport_titles.get(sport, sport))}",
             width="stretch", disabled=not all(creds),
             help="Asks the desktop to scrape this sport and push a fresh board (~5 min)."
             if all(creds) else "Needs GITHUB_REPO / GITHUB_TOKEN (set on the cloud app)."):
    try:
        req = SR.ScanRequest.new(
            sports=scan_sports, note="parlay builder",
            date_from=today.isoformat(),
            date_to=(days[-1].isoformat() if days else None))
        SR.put_request(creds[0], creds[1], req)
        st.session_state["parlay_scan_at"] = req.requested_at
        st.success("Asked the desktop. Expect a new board in about 5 minutes — "
                   "tap anything after that to reload.")
    except Exception as exc:                                     # noqa: BLE001
        st.error(f"Could not file the request: {type(exc).__name__}: {exc}")
if st.session_state.get("parlay_scan_at") and gen_at:
    if SR.snapshot_is_newer(gen_at, SR.ScanRequest(
            requested_at=st.session_state["parlay_scan_at"], request_id="local")):
        st.success("✅ Fresh board is in.")
        st.session_state.pop("parlay_scan_at", None)
    else:
        st.info("⏳ Waiting on the desktop scan — the board below is the previous one.")
if not all(creds):
    if st.button("Scan live here (desktop only)", help="Runs the scrapers in this process."):
        from edge.arb.run import snapshot as build_snapshot
        prog = st.progress(0.0, text="starting…")
        try:
            lcfg = ArbConfig()
            lcfg.sports = scan_sports
            lcfg.detect.date_from = today.isoformat()
            lcfg.detect.date_to = days[-1].isoformat() if days else None
            new = build_snapshot(lcfg, progress=lambda label, i, n: prog.progress(
                (i + 1) / n, text=f"{label}…"))
            SNAPSHOT.write_text(json.dumps(new, indent=1))
            prog.empty()
            st.rerun()
        except Exception as exc:                                 # noqa: BLE001
            prog.empty()
            st.error(f"Live scan failed: {type(exc).__name__}: {exc}")

if not snap:
    st.stop()

# ------------------------------------------------------------------ build
snap_key = (found.source, found.updated_at.isoformat(), len(found.data))
max_leg_dec = parse_american(max_leg_txt) if max_leg_txt.strip() else None
now_bucket = int(datetime.now(timezone.utc).timestamp() // 60)
legs = _parlay_legs(snap_key, snap.get("candidates", []), promo, allow_book_only, max_leg_dec,
             days, now_bucket)
n_legs = None if legs_pick == "Best" else int(legs_pick)
res = _parlay_build((snap_key, promo, allow_book_only, max_leg_dec, days, now_bucket), legs, promo,
             n_legs, float(stake), CAPS[cap_label], float(bankroll))

games = len({l.event_id for l in legs})
st.caption(f"{len(legs):,} legs from {games} games qualify at {PL.BOOK_NAMES[book]} "
           f"· {promo.describe()}")
if not legs:
    st.error(f"No {PL.BOOK_NAMES[book]} legs for this sport and day on the board. "
             "Rescan above, or widen 'Games'.")
    st.stop()
if res.note:
    st.warning(res.note)
pick = res.pick
if pick is None:
    st.stop()


def kpi(label: str, value: str, cls: str = "") -> str:
    return f'<div class="kpi"><div class="v {cls}">{esc(value)}</div><div class="l">{esc(label)}</div></div>'


ev_cls = "good" if pick.ev > 0 else "bad"
st.subheader(f"The ticket · {pick.n} legs · {pick.boost:.0%} boost")
st.markdown('<div class="kpis">' +
            kpi("expected value", f"{signed_pct(pick.ev)}  ({money(pick.ev_dollars)})", ev_cls) +
            kpi("hits", one_in(pick)) +
            kpi("odds → boosted", f"{om.format_american(pick.decimal)} → "
                                  f"{om.format_american(pick.boosted_decimal)}") +
            kpi(f"to win on {money(pick.stake)}", money(pick.to_win)) +
            "</div>", unsafe_allow_html=True)
if pick.ev <= 0:
    st.info("No positive-EV ticket under these terms. Without a boost that is the normal "
            "answer — a parlay of fairly priced legs only ever compounds the vig.")


def leg_rows(legs_, numbered=True, mark=None) -> str:
    rows = []
    for i, l in enumerate(legs_, 1):
        flags = []
        if l.suspect:
            flags.append("⚠️ far off market — likely a stale line")
        if l.book_only:
            flags.append(f"{BOOK_ABBR} only")
        src = "+".join(PL.BOOK_SHORT.get(b, b) for b in l.sources)
        edge_cls = "good" if l.raw_ratio >= 1 else ("muted" if l.raw_ratio >= .95 else "bad")
        cls = ' class="pick"' if mark and mark(l) else ""
        rows.append(
            f"<tr{cls}>" + (f'<td class="n muted">{i}</td>' if numbered else "") +
            f'<td><b>{esc(l.describe())}</b><br><span class="g">{esc(l.matchup)} · '
            f'{esc(kickoff(l.commence_time))}{" · " + esc("; ".join(flags)) if flags else ""}</span></td>'
            f'<td class="n"><b>{esc(l.american)}</b></td>'
            f'<td class="n">{l.fair_prob:.0%}<br><span class="g">{esc(src)}</span></td>'
            f'<td class="n {edge_cls}">{l.edge_pct:+.1f}%</td></tr>')
    head = ('<tr>' + ('<th></th>' if numbered else '') +
            f'<th>Leg</th><th style="text-align:right">{BOOK_ABBR}</th>'
            '<th style="text-align:right">Fair</th><th style="text-align:right">Edge</th></tr>')
    return f'<div class="tw"><table class="pl">{head}{"".join(rows)}</table></div>'


BOOK_ABBR = PL.BOOK_SHORT[book]
st.markdown(leg_rows(pick.legs), unsafe_allow_html=True)
st.caption("Fair = the leg's no-vig win chance from the other books (and the anchor, when "
           "present), shrunk toward this book's own line. Edge = fair × price − 1; a "
           "standard −110 leg is about −4.5%. Check every price in the slip before betting — "
           f"the board is {age_text(mins)}.")

with st.expander("🔁 Swaps — if a leg has moved or been pulled"):
    swaps = PL.bench(pick, legs)
    for i, alts in swaps.items():
        if not alts:
            continue
        st.markdown(f"**{i + 1}. {esc(pick.legs[i].describe())}** "
                    f"<span class='muted'>({esc(pick.legs[i].american)})</span> → swap for:",
                    unsafe_allow_html=True)
        st.markdown(leg_rows(alts, numbered=False), unsafe_allow_html=True)

# --------------------------------------------------------------- frontier
st.subheader("Other tickets — best EV at each hit rate")
growth_best = res.best_growth
rows = []
for p in res.frontier:
    tag = []
    if p is pick or [l.event_id + l.side + str(l.point) for l in p.legs] == \
            [l.event_id + l.side + str(l.point) for l in pick.legs]:
        tag.append("👉 pick")
    if growth_best and p.legs == growth_best.legs:
        tag.append("Kelly")
    rows.append(
        f'<tr class="{"pick" if "👉 pick" in tag else ""}"><td>{esc(one_in(p))}<br>'
        f'<span class="g">{esc(" · ".join(tag))}</span></td>'
        f'<td class="n">{p.n}</td>'
        f'<td class="n">{esc(om.format_american(p.decimal))}<br>'
        f'<span class="g">→ {esc(om.format_american(p.boosted_decimal))}</span></td>'
        f'<td class="n {"good" if p.ev > 0 else "bad"}">{signed_pct(p.ev)}<br>'
        f'<span class="g">{esc(money(p.ev_dollars))}</span></td>'
        f'<td class="n">{esc(money(p.to_win))}</td></tr>')
st.markdown('<div class="tw"><table class="pl"><tr><th>Hits</th><th style="text-align:right">Legs</th>'
            '<th style="text-align:right">Odds</th><th style="text-align:right">EV</th>'
            '<th style="text-align:right">To win</th></tr>' + "".join(rows) + "</table></div>",
            unsafe_allow_html=True)
if growth_best is not None:
    g_in = any(p.legs == growth_best.legs for p in res.frontier)
    st.caption(f"Kelly = the ticket that grows a {money(bankroll)} bankroll fastest at a "
               f"{money(stake)} stake: {growth_best.n} legs, {one_in(growth_best)}, "
               f"{signed_pct(growth_best.ev)} EV"
               + ("" if g_in else " (not shown above — it is not the top EV at its hit rate)")
               + ". Long shots score worse here even at higher EV.")

if promo.stepped or len(res.by_legs) > 1:
    st.subheader("By number of legs")
    be = PL.step_breakevens(promo)
    rows = []
    for n, p in sorted(res.by_legs.items()):
        need = be.get(n)
        rows.append(
            f'<tr class="{"pick" if p.n == pick.n else ""}"><td class="n">{n}</td>'
            f'<td class="n">{promo.boost_for(n):.0%}</td>'
            f'<td class="n {"good" if p.ev > 0 else "bad"}">{signed_pct(p.ev)}</td>'
            f'<td class="n">{esc(one_in(p))}</td>'
            f'<td class="n muted">{"" if need is None else f"{(need - 1) * 100:+.1f}%"}</td></tr>')
    st.markdown('<div class="tw"><table class="pl"><tr><th style="text-align:right">Legs</th>'
                '<th style="text-align:right">Boost</th><th style="text-align:right">EV</th>'
                '<th style="text-align:right">Hits</th>'
                '<th style="text-align:right">Leg breaks even at</th></tr>'
                + "".join(rows) + "</table></div>", unsafe_allow_html=True)
    if promo.stepped:
        st.caption("'Leg breaks even at': the worst edge a leg can have and still be worth "
                   "adding to reach that step. A −110 leg is about −4.5%, so any step that "
                   "allows worse than that should be taken.")

with st.expander(f"Best single legs at {PL.BOOK_NAMES[book]} (build your own)"):
    top = sorted(legs, key=lambda l: -l.raw_ratio)[:40]
    st.markdown(leg_rows(top, numbered=False, mark=lambda l: l in pick.legs),
                unsafe_allow_html=True)

with st.expander("How this works (and what the research said)"):
    st.markdown("""
**The formula.** For one leg per game, with fair win chance *p* and the book's decimal price *d*,
each leg returns *r = p·d* per $1 (1.00 = fair; a −110 leg ≈ 0.955). A profit boost *b* makes the
parlay's expected value **(1+b)·Πr − b·Πp − 1**.

- **Vig compounds** — Πr shrinks with every leg. Unabated's "parlays compound the house edge" is
  right, and so is their warning that SGPs carry extra hold on top (books pad their correlation
  model; state data shows parlay hold 10–21% vs ~5–7% on straight bets).
- **The boost multiplies almost all of it**, so a ticket is +EV once Πr > 1/(1+b). For the 105%
  step that is Πr > 0.49 — eleven ordinary legs clear it.
- **Stepped boosts:** a leg is worth adding when its r beats (1+bₙ₋₁)/(1+bₙ). From 6 legs on, DK's
  CFB ladder pays for even a 7%-hold leg — so the popular rule "every leg must be +EV on its own"
  is *sufficient, not necessary*, and "3–4 legs is the sweet spot" is a variance preference,
  not an EV fact.
- **Flat boosts:** no step to pay for extra legs, so use the fewest the token allows, and
  longer odds waste less of the boost (the −b·Πp term).
- **Variance is the real choice.** Max EV usually means a 1-in-thousands ticket. The slider caps
  the pick; the Kelly row shows what a bankroll-growth view prefers.

**Fair price** = de-vigged average of the other books (the vig-free Fanatics Markets anchor at
double weight when present) *and* this book's own line — shrinking toward the book on purpose,
because one soft book disagreeing is as often a stale line as an edge. Legs above +6% are capped
and flagged.

**Not covered:** SGP / SGPx (the book's own correlation pricing can't be rebuilt from single
legs), live betting, and anything one-sided (milestones) with no other book to price it.
""")
