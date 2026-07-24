# Historical NFL/NBA Prop Collection — 2026-07-24

**Final tally**: NFL 2025 season (261/262 games), NFL 2024 season (262/262 games), NBA
2024-25 sample (572 games). ~79,829 of the key's 91,742 credits spent; 20,171 left
deliberately unspent as a reserve, not drained to zero. **This data lives ONLY on local
disk (gitignored, ~69MB — see repo .gitignore) — it is NOT regenerable** (the key that
fetched it expires today), so back it up somewhere durable outside this machine/repo.

Collected in one session while a paid Odds API key (91,742 credits) was about to expire.
See DFS_MULTISPORT_PLAN.md for the plan this feeds; nothing downstream has been built yet
(no scoring/projection/backtest code) — this is raw material, not a working model.

## What's here

- `nfl_historical_props_2025/` — 261 of 262 real 2025 NFL regular-season games (one 404'd
  on the API side, not recovered). One JSON file per event: `{event: {...}, odds: {...}}`.
  `odds.bookmakers[].markets[]` carries the 8 core skill-position markets (see below) across
  multiple real books including DraftKings.
- `nfl_historical_props_2024/` — 2024 season, added specifically for a season-to-season
  robustness check (the same discipline DFS_METHODOLOGY.md's salary-regression split-sample
  check used) — don't trust a finding from 2025 alone if 2024 doesn't reproduce it.
- `nfl_historical_events_2025.json` / `_2024.json` — the flat event lists (id, teams,
  commence_time) each collector built before pulling odds; regenerable for free (1cr per
  weekly snapshot) if ever needed again, kept mainly so a resumed run doesn't re-pay for them.
- `nba_historical_props_2025/` — 572 real games, SAMPLED every 2 days across the full
  2024-25 NBA season (an exhaustive pull would have cost ~160k credits, more than the whole
  key's balance — a full season was never in budget). 7 core markets, DraftKings included.

## Markets collected

NFL (8): `player_pass_yds`, `player_pass_tds`, `player_pass_interceptions`,
`player_rush_yds`, `player_rush_tds`, `player_receptions`, `player_reception_yds`,
`player_reception_tds`. These are CLOSING lines (pulled at each event's own commence_time,
so it's the last posted number before kickoff, not an average across the week).

NBA (7): `player_points`, `player_rebounds`, `player_assists`, `player_threes`,
`player_blocks`, `player_steals`, `player_turnovers`. Also closing lines.

**`player_fantasy_points` checked separately and found EMPTY in the historical archive.**
DFS_MULTISPORT_PLAN.md's research confirmed this market exists going forward (live docs,
current markets list) — but a direct test against 3 different marquee 2024-25 games
(Celtics/Knicks opening night, two Lakers games) came back with zero bookmakers for all
three, at zero credit cost (the API doesn't charge for a market with no data). Read as: no
book had this market archived for that season, not a collection mistake — cost nothing to
find out (~40 test calls before stopping, all $0). Don't re-attempt for the 2024-25 season;
worth checking again for whatever season is current once NBA's live season starts.

## What this does NOT include yet (real next steps, not urgent — free data, no expiring key)

1. **Free ground-truth game logs** for both seasons (nflverse for NFL, stats.nba.com or
   balldontlie for NBA) — needed to actually grade whether these props would have beaten a
   skill-based projection, the exact test that validated MLB pitchers and killed MLB's
   original batter-prop model. No urgency: these sources don't expire, unlike the props.
2. **A DK scoring formula for either sport**, so raw prop lines can be converted into DK
   fantasy-point projections at all.
3. **Ingestion/collection code** analogous to `scripts/dfs_model_lab_collect.py` +
   `dfs_model_lab_eval.py` to turn these raw per-event JSON files into a leak-free
   train/test row dataset.

## Credits spent this session

91,742 → 20,171 remaining (79,829 spent). NFL 2025: 15,641cr. NBA sample: 40,010cr
(came in cheaper per-event than estimated, ~70cr not ~130cr, hence a bigger sample than
planned). NFL 2024: 15,650cr. NBA fantasy_points attempt: $0 (see above — confirmed empty,
not collected). This key expired 2026-07-24; the project now uses a new 500-credits/month
key — see the top-level `.env` (gitignored, not in this repo's history).
