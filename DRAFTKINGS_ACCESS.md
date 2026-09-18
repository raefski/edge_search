# DraftKings access — what blocks exist, and how not to trip them

Every product in this repo that reads DraftKings (Arbitrage, MLB DFS, NFL DFS,
Pick'em) sits behind the same Akamai-fronted infrastructure, and it enforces
**two independent things**. Conflating them has already cost time once
(2026-09-18: a residential-IP block was first assumed to be the familiar
datacenter one). This file is the one place documenting both, so the next
person — human or Claude — checks the right box before spending an hour
re-diagnosing something already known.

## 1. The two blocks

| | datacenter-IP block | residential volume/behavioral block |
|---|---|---|
| Hosts affected | `api.draftkings.com`, `sportsbook-nash.draftkings.com` | `sportsbook-nash.draftkings.com` (only host it's been seen on so far) |
| Triggered by | the request's IP being a datacenter/cloud IP (Streamlit Cloud, GitHub Actions) — happens on the very first request | a broad/high-volume request pattern, even from a legitimate residential IP |
| Symptom | `403`, `Server: AkamaiGHost`, "Access Denied" | identical `403` / `AkamaiGHost` page — indistinguishable except by *when* it starts |
| First confirmed | 2026-09-16, DK draftables to Streamlit Cloud (`edge/dfs.py` `_draftables_raw` docstring); ESPN and DK's sportsbook eventgroups endpoint hit the same wall for Pick'em's live line, undated but same era (`PICKEM_STATUS.md` §"App / deployment") | 2026-09-18, arbitrage sportsbook-odds sweep (see `scripts/arb_agent.py` docstring) |
| Duration | permanent / structural — that IP class is always blocked | temporary — cleared within roughly 3–10 hours in the one incident measured (blocked 07:37, still blocked 10:27, clear by 17:18) |
| Fix | don't call it from Cloud/CI — run from the desktop's residential connection, and give Cloud a committed snapshot instead | reduce the volume/breadth of whatever triggered it, then wait — there is no request-shaping fix for an IP-reputation block |

Both are read the same way: a `403` whose response carries `Server:
AkamaiGHost` and an "Access Denied" body. `curl` and this repo's `http.py`
shim get identical treatment, so it is not a Python/TLS-fingerprint issue —
it's the edge deciding before the app ever sees the request.

## 2. Per-product guidance

### Arbitrage — `edge/arb/*`, `scripts/arb_agent.py`, `scripts/arb_scan.py`
- Host: `sportsbook-nash.draftkings.com`.
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
    (confirmed 2026-09-16), works fine from the desktop's residential IP.
  - `www.draftkings.com/lobby/getcontests` — **not** blocked even from Cloud.
- `draftables-publish.timer` polls every 30 minutes, 7am–11:30pm, 1–2 calls
  per tick (one per active draft group). Low volume; has never shown the
  residential/behavioral symptom. No change needed — just don't grow this
  poll (more sports, tighter interval) without re-checking that assumption.
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
- Don't attempt to route around either block — proxy/IP rotation, TLS
  fingerprint spoofing, scripting a browser to solve the JS challenge and
  replay session cookies. Same call already made for ESPN in
  `PICKEM_STATUS.md`; it applies at least as much here, since DK is a
  regulated sportsbook whose terms explicitly prohibit automated access —
  this is an anti-fraud control, not an incidental rate limit.
- Datacenter block: already solved structurally (see §2 — run from the
  residential machine, snapshot-fallback for Cloud).
- Residential/behavioral block: one data point so far (§1's duration row).
  Expect a similar order of magnitude, not permanent, and cut the volume of
  whatever's running before assuming otherwise.
