# DraftKings access — what blocks exist, and how not to trip them

Every product in this repo that reads DraftKings (Arbitrage, MLB DFS, NFL DFS,
Pick'em) sits behind the same Akamai-fronted infrastructure, and it enforces
**three independent things**. Conflating them has already cost time twice
(2026-09-18: a residential-IP block was first assumed to be the familiar
datacenter one; 2026-09-19: an HTTP/2 protocol gate was first assumed to be a
recurrence of the 09-18 residential block, because both present as the same
403/AkamaiGHost page). This file is the one place documenting all three, so
the next person — human or Claude — checks the right box before spending an
hour re-diagnosing something already known.

## 1. The three mechanisms

| | datacenter-IP block | residential volume/behavioral block | HTTP/2 protocol gate |
|---|---|---|---|
| Hosts affected | `api.draftkings.com`, `sportsbook-nash.draftkings.com` | `sportsbook-nash.draftkings.com` first seen 2026-09-18; `api.draftkings.com` joined it 2026-09-19 — not host-scoped, an IP-reputation flag that can cover every Akamai-fronted DK host at once | **both** `sportsbook-nash.draftkings.com` and `api.draftkings.com` (the latter confirmed 2026-09-19, correcting an earlier claim in this file — see §1a) |
| Triggered by | the request's IP being a datacenter/cloud IP (Streamlit Cloud, GitHub Actions) — happens on the very first request | a broad/high-volume request pattern, even from a legitimate residential IP | the client speaking HTTP/1.1 at all, **or** sending a stub User-Agent like `Mozilla/5.0`. On `api.draftkings.com` these are two independent gates and a request must clear BOTH; regardless of IP or volume |
| Symptom | `403`, `Server: AkamaiGHost`, "Access Denied" | identical `403` / `AkamaiGHost` page | identical `403` / `AkamaiGHost` page — this is what made 09-19 initially look like a recurrence of the 09-18 residential block |
| First confirmed | 2026-09-16, DK draftables to Streamlit Cloud (`edge/dfs.py` `_draftables_raw` docstring); ESPN and DK's sportsbook eventgroups endpoint hit the same wall for Pick'em's live line, undated but same era (`PICKEM_STATUS.md` §"App / deployment") | 2026-09-18, arbitrage sportsbook-odds sweep (see `scripts/arb_agent.py` docstring) | 2026-09-19, sometime between 18:14 EDT 09-18 (last known-good DK pull, 12,992 quotes, logged in `data/odds.db`'s `scan` table) and 07:14 EDT 09-19 (first pull after an overnight machine sleep, 0 DK quotes) — the transition happened in a window nothing was running to observe, so the exact trigger time is unknown |
| Duration | permanent / structural — that IP class is always blocked | temporary — cleared within roughly 3–10 hours in the one incident measured (blocked 07:37, still blocked 10:27, clear by 17:18) | not observed to clear on its own within several hours on 09-19 (still active ~11 hours in); may be a standing requirement now rather than a transient posture change |
| Fix | don't call it from Cloud/CI — run from the desktop's residential connection, and give Cloud a committed snapshot instead | reduce the volume/breadth of whatever triggered it, then wait — there is no request-shaping fix for an IP-reputation block | **fixed 2026-09-19**: `edge/arb/http.py` shells out to the system `curl` binary (`--http2`) for any `*.draftkings.com` host instead of stdlib `urllib` (HTTP/1.1-only, so structurally unable to pass), and sends a full Chrome User-Agent. `edge/dfs.py` was routed through that same shim later the same day — see §1a. Everything else (GitHub API calls, which also ride this shim from Streamlit Cloud) is untouched. See §2. |

**Correction to a claim this file used to make:** it previously said "`curl`
and this repo's `http.py` shim get identical treatment, so it is not a
Python/TLS-fingerprint issue." That was tested with a bare `User-Agent`-only
request and never isolated HTTP/2 vs HTTP/1.1 — **it was wrong**. With the
real production header set (`Accept`, `Referer`, `Origin`, `x-client-name`,
`x-client-page`, `x-pe-ep`, `x-pe-loc`), `curl --http2` gets a 200 with real
JSON from `sportsbook-nash`; the identical headers over forced HTTP/1.1 —
whether via `curl --http1.1` or Python's `urllib`, which can only ever speak
HTTP/1.1 — get the 403. It is a protocol/fingerprint issue, at least for this
host. `api.draftkings.com` DOES respond to the same fix once the
User-Agent is also a real browser string — see §1a, which corrects an earlier
claim here that it did not.

## 1a. Correction, 2026-09-19 (second pass): `api.draftkings.com` has TWO gates

This file previously said `api.draftkings.com` "does not respond to this fix
(still 403 over HTTP/2 with full headers)" and told the reader to treat it as
governed by the IP-reputation rows instead. **That was wrong, and it sent the
next day's debugging to the wrong place.** The test behind it varied the
protocol while holding a `Mozilla/5.0` User-Agent fixed — and that stub UA is
itself a gate. Re-isolated live on 2026-09-19 against one draftables URL:

| request | result |
|---|---|
| full Chrome UA, HTTP/2 | **200**, real JSON |
| full Chrome UA, HTTP/2, no `Referer`/`Origin` | **200** |
| full Chrome UA, forced HTTP/1.1 | 403 |
| `Mozilla/5.0`, HTTP/2 | 403 |

So: HTTP/2 **and** a real browser User-Agent, both required, and `Referer`,
`Origin` and `Accept-Language` are irrelevant. The IP was never blocked — the
same machine, the same minute, got a 200 as soon as both conditions were met.

**Why DFS stayed broken a day longer than arbitrage.** The 2026-09-19 fix was
applied to `edge/arb/http.py`, which arbitrage and pick'em both route through.
`edge/dfs.py` did not use that shim: it had its own private `_get` built on
`urllib` (HTTP/1.1-only) with `_UA = {"User-Agent": "Mozilla/5.0"}` — failing
both gates at once. Arbitrage recovered, DFS did not, and the difference looked
like a host-specific block rather than one module that simply had not been
migrated. `edge/dfs.py::_get` now dispatches DraftKings hosts to
`edge.arb.http` and leaves `statsapi.mlb.com` on `urllib`, so the transport rule
lives in exactly one place (`edge.arb.http.is_draftkings`).

**Why it went unnoticed for a day.** `_draftables_raw` catches *any* exception
and falls back to `data/draftables_snapshot/<gid>.json`, which is right for
Streamlit Cloud but silently served 09-18 salaries on the desktop, where the
live call should have worked. It now prints the reason and the snapshot's date
to stderr and records `edge.dfs.LAST_DRAFTABLES_SOURCE` (`"live"` /
`"snapshot <when>"` / `"failed"`). **A fallback that cannot be distinguished
from success will hide the next outage too — check that marker before
believing a number.**

**If `curl` is missing** (Streamlit Cloud is not guaranteed to have it), the
shim degrades to `urllib` rather than raising: the `www.draftkings.com` lobby
endpoint is not blocked from Cloud and must keep working over HTTP/1.1. The
calls that genuinely need HTTP/2 fail closed on their own.

## 2. Per-product guidance

### Arbitrage — `edge/arb/*`, `scripts/arb_agent.py`, `scripts/arb_scan.py`
- Host: `sportsbook-nash.draftkings.com`.
- **HTTP/2:** as of 2026-09-19, `edge/arb/http.py` routes every `*.draftkings.com`
  request through `curl --http2` rather than `urllib`. This is the fix for
  the protocol gate in §1, not for the datacenter or residential-volume
  blocks — a scan can still get 403'd by either of those for the reasons
  below, it just won't get 403'd by the protocol check anymore.
- **Safe pattern:** the phone-triggered flow (`arb_agent.py` polling
  `scan_request.json`) — one sport per request, sometimes one league. Has run
  clean for days.
- **Pattern that tripped the 2026-09-18 block (leading suspect, unconfirmed):**
  `scripts/arb_scan.py` run bare, no `--sports`. That scans DK's full
  catalog — up to ~35 leagues, and up to ~45 requests per league once main
  lines + main-line subcategories (≤4) + prop subcategories (≤40,
  `draftkings_max_prop_subcategories`, `edge/arb/config.py`) are counted —
  hundreds to 1000+ requests in one continuous burst, 0.35s apart
  (`request_gap_seconds`, same file).
- **Rule:** always pass `--sports <sport>` to `arb_scan.py`; never run it
  bare. Don't stack more than one broad scan close together. Don't shrink
  `request_gap_seconds` below its current 0.35s.

### MLB DFS / NFL DFS — `edge/dfs.py`, `scripts/draftables_publish.py`
- Two hosts, different exposure:
  - `api.draftkings.com/draftgroups/.../draftables` — datacenter-blocked
    (confirmed 2026-09-16), and subject to both gates in §1a. Works fine from
    the desktop's residential IP over HTTP/2 with a real browser UA.
  - `www.draftkings.com/lobby/getcontests` — **not** blocked even from Cloud,
    and served fine over HTTP/1.1.
- **Correction, 2026-09-19.** This section used to say the publish timer made
  "1–2 calls per tick (one per active draft group)" and was "low volume on its
  own". Both halves were wrong. It fetched *every* draft group in the lobby on
  every tick — measured live at **62 groups per tick, 34 ticks a day, ~2,100
  requests/day** to `api.draftkings.com`. The parenthetical was right about the
  mechanism (one call per draft group) and wrong by ~30x about the count,
  because nobody had counted the groups. **Measure a poll before describing it
  as low volume.** That figure also made this the largest single source of DK
  traffic in the repo — larger than a broad `arb_scan.py` sweep, and running
  every half hour rather than on demand.
- `scripts/draftables_publish.py` now throttles to **~160 requests/day** (92%
  less) with no loss of coverage, on two measured facts:
  - a group DK has already **priced** cannot change (salaries are frozen — the
    same invariant that makes the snapshot fallback exact), so it drops to a
    6-hour heartbeat (`--refresh-hours`);
  - ~45 of the 62 groups are Tiers / Snake / Best Ball / season-long types that
    carry **no salary field at all** and returned "unpriced" forever. These back
    off by doubling (30min → 1h → 2h → 4h → cap) per consecutive salary-less
    answer, capped at `--cold-hours` (6h) near tip-off and 4× that further out.
  - The backoff is keyed on **observed** salary-less responses, not on a
    `GameTypeId` allow-list, so it cannot silently drop a slate the picker
    offers, and a gid seen for the first time is never throttled — a newly
    posted slate still reaches the phone within ~30 minutes.
  - Per-request gap is `--gap` (0.5s) and now runs in a `finally`, so a 403
    storm no longer fires every request back-to-back with no delay — the old
    code's `continue` skipped the sleep on exactly the error path.
- The datacenter block already has its fix in place: Cloud reads
  `data/draftables_snapshot/*.json`, which this same timer publishes, so the
  deployed app never calls DK directly.

### Pick'em — `edge/pickem_free.py`, `deploy/pickem-capture@.service`
- Reuses the **same client and host as arbitrage**
  (`edge.arb.draftkings_league.DraftKingsLeague`, `sportsbook-nash`) for its
  live line — one `dk.fetch(sport_key)` call per capture, no props, no
  subcategories.
- Runs on six fixed weekly timers (`lock-wed/thu/sun/mon`, `midweek`,
  `post`), not continuously — low volume on its own.
- Because it shares both the host and the machine's IP with arbitrage, a
  heavy `arb_scan.py` sweep run close to a pick'em deadline draws on the same
  block budget. Avoid running a broad manual arb scan right before or during
  a pick'em capture window (see `deploy/pickem-capture-*.timer` for the exact
  times).
- ESPN's scoreboard sits behind the same Akamai infrastructure and hit the
  identical block independently — not fixable by header changes there
  either (`PICKEM_STATUS.md`).

## 3. If a block happens again

- One lightweight request is fine to confirm status. Don't retry-loop
  rapidly — that does nothing for a datacenter block (permanent by design)
  and risks extending a behavioral one.
- Datacenter block: already solved structurally (see §2 — run from the
  residential machine, snapshot-fallback for Cloud).
- Residential/behavioral block: one data point so far (§1's duration row).
  Expect a similar order of magnitude, not permanent, and cut the volume of
  whatever's running before assuming otherwise.
- HTTP/2 + User-Agent gate: already solved for `sportsbook-nash` and
  `api.draftkings.com` (§1, §1a, §2). If a *new* host shows this symptom, run
  the **four-cell grid** before concluding anything: {full Chrome UA, stub
  `Mozilla/5.0`} × {`curl --http2`, `curl --http1.1`}. Varying one axis while
  holding the other at a failing value is exactly the mistake that produced the
  wrong entry corrected in §1a. A 200 in any cell proves the IP is fine.
- **Check `edge.dfs.LAST_DRAFTABLES_SOURCE` (or the stderr line) before
  believing DFS numbers.** A silent snapshot fallback is what made a broken
  transport look like working software for a day.

**On fingerprint-matching generally:** this file used to rule out TLS/HTTP2
fingerprint changes outright, on the reasoning that DK's terms prohibit
automated access and this is an anti-fraud control, not an incidental rate
limit — the same call made for ESPN in `PICKEM_STATUS.md`. The HTTP/2 switch
in §1 crosses that line, and was done deliberately, not by default: Adam was
asked directly given what this file already said, confirmed no DK account
credentials or session ever flow through this scraper (checked in
`edge/arb/draftkings_league.py` — no cookie, token, or login; this reads the
same anonymous odds feed a logged-out browser gets), and explicitly said yes
knowing DK's ToS still nominally prohibits automated access regardless of
authentication. That reasoning doesn't extend automatically to the next
escalation — proxy/IP rotation, solving a JS challenge, replaying a real
session's cookies, anything that *would* touch account credentials — each of
those is a bigger step than this one and should get the same explicit
conversation, not be inferred as "already decided" from this precedent.
