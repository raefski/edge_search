"""Read a book's PUBLIC promotions and turn the profit boosts into Boost rules.

    POST https://api.draftkings.com/en/api/promotions/v2/promotions/query

No authentication. The endpoint answers a plain anonymous request -- which is
the whole reason this is possible, and worth stating because the obvious
assumption is the opposite. There are two different kinds of boost and only one
of them is here:

  * PUBLIC opt-in offers -- the carousel on the logged-out homepage. Anyone can
    read them, so they can be discovered, and that is what this module does.
  * ACCOUNT tokens -- the "Rewards" panel inside the bet slip, issued to you
    with your own countdown. Those need your session and stay hand-entered.
    Confirmed, not assumed: v2/rewards/summary and v2/rewards/details both
    answer 401 "User is unauthenticated" to an anonymous request. See
    ACCOUNT_REWARDS_ENDPOINTS.

WHAT IS PARSED, AND WHAT IS NOT
The percentage, minimum odds, sport, expiry and whether the offer is
parlay-only all come out of the terms text reliably. Maximum stake does not:
DraftKings writes "Max betting limits apply" without a number, and the actual
cap only appears on the token once it is claimed. So max_stake is left at the
caller's default and has to be corrected by hand -- it is the one term the
scanner cannot know, and it scales every profit figure linearly.

CONSERVATIVE BY CONSTRUCTION
A promotion becomes a Boost only when the percentage AND the sport are both
read confidently. Everything else degrades to "no constraint", which is the
safe direction for a filter but NOT for a promise -- so anything unparsed is
returned alongside in `unparsed` rather than dropped silently. A boost invented
from a misread term sends you at bets you cannot place, which is exactly the
failure this project keeps finding.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import http
from .engine import Boost

# v3 is a superset of v2 -- same request shape, 28 promotions where v2 returns
# 13. Both answer anonymously. Route table read out of the Reward Center's own
# JS bundle (/mf/rewardcenter/static/js/main.*.js), which carries the whole map
# of query names to paths.
API = "https://api.draftkings.com/en/api/promotions/v3/promotions/query"

# The ACCOUNT rewards endpoints from that same table, recorded so nobody
# re-derives them. Both answer 401 "User is unauthenticated" without a session,
# which settles the question: claimed tokens cannot be read anonymously, and
# reaching them means storing DraftKings credentials.
#     v2/rewards/summary   UserRewardsSummaryQuery
#     v2/rewards/details   UserRewardsDetailsQuery
ACCOUNT_REWARDS_ENDPOINTS = (
    "https://api.draftkings.com/en/api/promotions/v2/rewards/summary",
    "https://api.draftkings.com/en/api/promotions/v2/rewards/details",
)

# The zones the homepage asks for. `zoneIdentifierId` is a content-slot id, not
# an account id, and the request works without the long inline-promo list the
# browser sends.
DEFAULT_ZONES = [
    {"zoneName": "WebCarousel", "zoneIdentifierId": "LNLHNDNData",
     "inliningEntityIds": {}},
    {"zoneName": "WebHomeScreen", "zoneIdentifierId": "Z3YASNFData",
     "inliningEntityIds": {}},
]

HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": "https://sportsbook.draftkings.com",
    "referer": "https://sportsbook.draftkings.com/",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
}

# Longest first: "College Football" must win over "Football", and "WNBA" over
# "NBA", or a college boost is priced against the NFL board.
SPORT_WORDS: list[tuple[str, str]] = [
    ("college football", "americanfootball_ncaaf"),
    ("college basketball", "basketball_ncaab"),
    ("tour championship", "golf_pga"),
    ("presidents cup", "golf_pga"),
    ("ryder cup", "golf_pga"),
    ("pga", "golf_pga"),
    ("golf", "golf_pga"),
    ("tennis", "tennis_atp"),
    ("us open", "tennis_atp"),
    ("wnba", "basketball_wnba"),
    ("nba", "basketball_nba"),
    ("nfl", "americanfootball_nfl"),
    ("mlb", "baseball_mlb"),
    ("nhl", "icehockey_nhl"),
]

_PCT = re.compile(r"profit\s*boost\s*:?\s*(\d{1,3})\s*%|(\d{1,3})\s*%\s*profit\s*boost",
                  re.I)
_MIN_ODDS = re.compile(
    r"(?:minimum|min\.?)\s*(?:total\s*)?(?:bet\s*)?odds[^+\-\d]{0,24}([+-]?\d{2,5})"
    r"|odds\s+must\s+be\s+([+-]?\d{2,5})", re.I)
# Only the SPECIFIC phrasing. "All other bet types are excluded" appears in
# every promotion's boilerplate and means "other than the type named above" --
# for the golf prop boost that type is a Prop bet, not a parlay. Matching it
# marked a single-bet token parlay-only, which makes it unusable for hedging:
# a boost that can price a single is the one that can be arbitraged at all.
_PARLAY_ONLY = re.compile(r"only\s+applies\s+to\s+a[^.]{0,60}\bparlay\b", re.I)
_MIN_LEGS = re.compile(r"parlays?\s*req(?:uire)?\.?\s*min\.?\s*(\d+)\s*legs?", re.I)
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class ParsedPromotion:
    """One promotion, and what could and could not be read from it."""
    promotion_id: str
    headline: str
    category: str
    expires_at: datetime | None
    terms: str
    boost: Boost | None = None
    unparsed: list[str] = field(default_factory=list)


def _text(promo: dict) -> str:
    md = promo.get("merchandisingData") or {}
    raw = " ".join(str(md.get(k) or "") for k in
                   ("promotionHeadline", "promotionDescription", "terms",
                    "loggedOutTerms", "additionalDetail", "inlineDetails"))
    return re.sub(r"\s+", " ", _TAGS.sub(" ", raw)).strip()


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    txt = re.sub(r"\.(\d{1,6})\d*", r".\1", str(value).replace("Z", "+00:00"))
    try:
        ts = datetime.fromisoformat(txt)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def american_to_decimal_floor(american: int) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def parse_promotion(promo: dict, default_max_stake: float = 10.0) -> ParsedPromotion:
    """One promotion -> a Boost, where the terms support one."""
    md = promo.get("merchandisingData") or {}
    text = _text(promo)
    out = ParsedPromotion(
        promotion_id=str(promo.get("publicPromotionId") or ""),
        headline=str(md.get("promotionHeadline") or ""),
        category=str(promo.get("category") or ""),
        expires_at=_when(promo.get("expirationDate")),
        terms=text,
    )

    m = _PCT.search(text)
    pct = None
    if m:
        pct = int(m.group(1) or m.group(2)) / 100.0
    if pct is None:
        # "Profit Boost percentage varies" is a real thing DraftKings writes,
        # and guessing a number there would be inventing terms.
        out.unparsed.append("percentage")
        return out

    low = text.lower()
    sport = next((key for word, key in SPORT_WORDS if word in low), None)
    if sport is None:
        out.unparsed.append("sport")
        return out

    min_decimal = 1.0
    mo = _MIN_ODDS.search(text)
    if mo:
        try:
            min_decimal = american_to_decimal_floor(int(mo.group(1) or mo.group(2)))
        except (ValueError, ZeroDivisionError):
            out.unparsed.append("min odds")
    else:
        out.unparsed.append("min odds")

    # DraftKings says "Max betting limits apply" with no figure; the real cap is
    # only on the token once claimed. Left at the caller's default and flagged,
    # because it scales every profit number linearly.
    out.unparsed.append("max stake")

    parlay = bool(_PARLAY_ONLY.search(text))
    legs = _MIN_LEGS.search(text)
    label = out.headline or f"{pct:.0%} boost"
    if parlay:
        label += f" (parlay only{', min ' + legs.group(1) + ' legs' if legs else ''})"

    out.boost = Boost(book="draftkings", pct=pct, max_stake=default_max_stake,
                      sports=[sport], min_decimal=min_decimal,
                      requires_parlay=parlay, expires_at=out.expires_at,
                      label=label)
    return out


def parse_response(payload: dict, default_max_stake: float = 10.0) -> list[ParsedPromotion]:
    seen, out = set(), []
    for zone in payload.get("zones") or []:
        for promo in zone.get("promotions") or []:
            pid = promo.get("publicPromotionId")
            if pid in seen:
                continue          # the same offer appears in several zones
            seen.add(pid)
            out.append(parse_promotion(promo, default_max_stake))
    return out


def fetch(site: str = "US-CT-SB", geo: str = "US-CT", session=None,
          timeout: float = 25.0) -> dict:
    """The raw promotions payload. No auth, no cookies -- verified."""
    body = {"filterByProduct": True, "geoLocation": geo, "siteExperience": site,
            "productName": "Sportsbook", "zones": DEFAULT_ZONES, "language": "en"}
    s = session or http
    r = s.post(API, json=body, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json() or {}


def discover(site: str = "US-CT-SB", geo: str = "US-CT",
             default_max_stake: float = 10.0, session=None,
             now: datetime | None = None) -> list[ParsedPromotion]:
    """Live public boosts, expired ones dropped. Fails soft: [] on any error."""
    try:
        parsed = parse_response(fetch(site, geo, session=session), default_max_stake)
    except Exception:                                  # noqa: BLE001
        return []
    when = now or datetime.now(timezone.utc)
    return [p for p in parsed
            if p.boost is not None and (p.expires_at is None or p.expires_at > when)]
