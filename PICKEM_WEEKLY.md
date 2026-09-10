# Weekly Checklist — Feeding the Model

**What this is:** the actual steps to run each week, in order. `PICKEM_MODEL.md` explains
*why* the model works; `PICKEM_STATUS.md` is the 60-second state check; **this is the one
you follow with a terminal open.** Nothing here requires understanding the model — just
running commands in order.

**Always start here:**
```bash
cd /home/asr/edge_search
```
Every command below assumes that directory. `/home/asr/pickem` is your own workspace — it
has a synced copy of the two other docs, but none of the scripts live there.

---

## Where the data actually lives (read this once)

| | |
|---|---|
| **The file** | `data/pickem_line_log.csv` — one row per game per snapshot |
| **The database** | **git.** Every capture appends rows; committing and pushing is what makes them permanent. There is no other store. |
| **Why git** | Streamlit Cloud rebuilds from the repo, so anything committed survives. Anything not committed does not exist. |
| **Your picks/standings** | `data/pickem/` — **gitignored on purpose**, stays off the public repo |

> **Changed 2026-09-08 — captures are now FREE and mostly automatic.** They used
> to cost 2 Odds-API credits each (The Odds API bills per market per region, and a
> capture asks for spreads + totals = 2). They now read the scraped DK / FanDuel /
> Fanatics board that `odds-collect-pickem-nfl.timer` already collects, so a
> capture costs nothing. Six systemd timers run them — **one per deadline**,
> because your deadline is per day. Credit-cost tables further down are kept only
> for the `--source paid` fallback.

**⚠ You cannot capture from your phone, and it is important to know why.** The Streamlit page
**only reads** the log — it never writes to it. And Streamlit Community Cloud's filesystem is
**ephemeral**: anything written there vanishes on the next restart or redeploy, and it cannot push
to git. So tapping "Pull fresh lines" on your phone shows you current numbers but **saves
nothing**. The phone is a viewer. Capture happens either on a machine with git, or automatically
(below).

### The automated capture — set this up once, before Week 1

Six systemd timers run the **market half** of the capture on this desktop, commit the rows, and
push. Nobody has to be awake. That matters because a market reading not taken at the right
moment is **gone forever** — the historical file holds only two snapshots per game and can never
fill the hole.

```bash
cp deploy/pickem-capture@.service deploy/pickem-capture-*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pickem-capture-post.timer \
  pickem-capture-lock-wed.timer pickem-capture-lock-thu.timer \
  pickem-capture-midweek.timer pickem-capture-lock-sun.timer \
  pickem-capture-lock-mon.timer
systemctl --user list-timers 'pickem-capture*'
```

Needs `sudo loginctl enable-linger $USER` (same as `arb-agent`), or the timers stop when your
last shell exits.

| timer | fires (ET) | label | why then |
|---|---|---|---|
| `pickem-capture-post` | Tue 13:15 | `post` | CBS posts by 1pm and freezes |
| `pickem-capture-lock-wed` | Wed 19:00 | `lock-wed` | before a Wednesday opener |
| `pickem-capture-lock-thu` | Thu 19:00 | `lock-thu` | before the Thursday nighter |
| `pickem-capture-midweek` | Fri 12:35 | `midweek` | the time-profile reading |
| `pickem-capture-lock-sun` | Sun 12:35 | `lock-sun` | before the 1pm slate |
| `pickem-capture-lock-mon` | Mon 19:00 | `lock-mon` | before Monday night |

> **Why the two `12:35`s, changed 2026-09-09.** The board is collected at 12:20 and 23:20
> only, so a capture at 12:00 fired *twenty minutes before* the day's collection and read the
> **previous night's** prices — Sunday's biggest deadline measured 12h40m stale, and every
> `midweek` reading really Thursday night. `captured_at` said `utcnow()`, so nothing could
> detect it. The service now runs a collection itself (`ExecStartPre=`) immediately before
> each capture, `captured_at` is the board's own finish time, and the new
> `board_age_seconds` column records how stale the board was. These two timers moved behind
> the collection as well, so the fix survives the `ExecStartPre` ever being dropped.

**Each deadline has its own label on purpose.** The log de-dupes on
`(season, week, snapshot, home_team)`, so if every deadline reused a bare `lock` you would keep
the *first* reading of the week and silently discard the rest — which is the opposite of what a
per-day deadline needs.

**`.github/workflows/pickem-capture.yml` is now only the paid fallback.** It cannot do this for
free: the scraped board lives in `data/odds.db`, which is gitignored and 1.5GB, and a GitHub
runner cannot rebuild it because DraftKings 403s datacenter IPs. To use it at all you must add
`ODDS_API_KEY` as a repository secret (Settings → Secrets and variables → Actions) — **it is not
set as of 2026-09-08, which is why nothing has been captured yet.**

**The CBS half still needs you** — those lines are behind a login. But the two halves join on
`(season, week, snapshot, home_team)`, so CBS's numbers get filled in *afterwards* by re-running
the **same** `--snapshot` label, and the time-critical market reading never waits for you:

```bash
# the timer already banked the market half, CBS columns blank:
python3 scripts/pickem_capture.py --snapshot post --week N --confirm
#   -> wrote 0 new, completed 16
```

> **Fixed 2026-09-09 — before that date this workflow wrote nothing.** `pickem_log.append`
> de-dupes on `(season, week, snapshot, home_team)` and *dropped* the colliding row instead of
> merging it, so the second run reported "0 new" and the CBS half never landed. Since every
> market-only row stayed CBS-less, `pickem_transferability.py` (which skips a game with no CBS
> line) printed "Nothing to measure yet" no matter how many rows the timers banked.
> `pickem_log.complete` now fills columns that were never measured and **never** overwrites one
> that was, so the log stays append-only in the sense that matters.

**Backfilling a deadline that was missed.** `data/odds.db` keeps 400 days of scans, so a capture
that never ran can still be reconstructed from the scan that did — stamped with *that scan's*
finish time, not with now:

```bash
python3 scripts/pickem_capture.py --snapshot post --week 1 \
    --scan-id 224 --market-only --confirm
# or, by clock time:
python3 scripts/pickem_capture.py --snapshot post --week 1 \
    --at 2026-09-08T17:15:00Z --market-only --confirm
```

---

## The five-minute version

```bash
cd /home/asr/edge_search

# 1. Tuesday, right after CBS posts — paste the Picks page, import it
python3 scripts/pickem_pool_import.py picks.txt --week N --write

# 2. Immediately after — log the freeze (free).
#    Steps 3 and 4 now run themselves on timers; this one is still worth doing
#    by hand, because it should be contemporaneous with step 1.
python3 scripts/pickem_capture.py --snapshot post --week N --confirm

# 3. Thursday or Friday — the mid-week reading (free, automatic).
#    This is the ONLY way to learn the TIME PROFILE of post-freeze drift.
python3 scripts/pickem_capture.py --snapshot midweek --week N --confirm

# 4. Before each day's deadline (free, automatic — ONE LABEL PER DEADLINE).
#    Never reuse a bare `lock` for two deadlines: the log de-dupes on the
#    label, so the second one is silently dropped. The timers use
#    lock-wed / lock-thu / lock-sun / lock-mon.
python3 scripts/pickem_capture.py --snapshot lock-sun --week N --confirm

# 5. After results are in — tell Claude the scores, or edit tracker.csv

# 6. Whenever — how much of the backtested edge is actually transferring? (free)
python3 scripts/pickem_transferability.py

# 7. Whenever — check progress on everything blocked (free)
python3 scripts/pickem_blocked.py
```

> **The single most valuable thing in this file is steps 2–4.** After ~4 weeks,
> `pickem_transferability.py` answers the biggest open question in the project: how much of
> the backtested 55.9% actually survives the fact that CBS freezes before you pick. It needs
> **no game results** — only the two line readings. A week you don't capture is permanently
> uncapturable. See PICKEM_MODEL.md 5j rounds 3 and 6.

Everything below is the same five steps with the why, the gotchas, and what to do when
something looks wrong.

---

## 1. Tuesday — after CBS posts (the week's most important five minutes)

CBS posts by 1pm ET Tuesday and freezes the line for the whole week. This step captures
that frozen number before anything can move it, which is the one piece of data nobody else
in the pool is recording.

1. Log into the CBS pick'em app, open **this week's Picks page** — the one listing all 16
   matchups with spreads, not Standings and not Settings.
2. Select all (Ctrl+A / Cmd+A), copy (Ctrl+C / Cmd+C).
3. Paste into a text file, e.g. `picks.txt`, anywhere convenient (your Desktop, a scratch
   folder — it doesn't need to live in the repo).
4. Import it:
   ```bash
   python3 scripts/pickem_pool_import.py picks.txt --week N --write
   ```
   Drop `--write` first if you want to eyeball the parsed table before it commits to
   `data/pickem_current_week.csv` — it's a dry run by default.

   **Check the output for this line before moving on:**
   ```
   N game(s) came through without community percentages
   ```
   If it prints that, the copy missed the pick-percentage numbers — the ones like "30% /
   70%" next to each team. Those are what unblock the public-pick-fading experiment, so
   worth a re-copy if they're missing. Everything else still works without them.

5. Log the frozen line against a live market reading, in the same few minutes:
   ```bash
   python3 scripts/pickem_capture.py --snapshot post --week N --confirm
   ```
   This is the step that makes CBS-bias isolation possible at all — it's worthless if it
   happens hours later, because by then the market has already moved and "the gap right
   now" isn't "the gap at post" anymore. **Do step 5 within a few minutes of step 2-3, not
   at the end of the day.**

6. Ask Claude for the week's picks. Everything needed is now on disk.

**Cost:** free. Step 5 used to spend 2 Odds-API credits (spreads + totals, one call for the
whole slate); it now reads the scraped board.

---

## 2. Before each deadline this week

Your deadline is **the start of that day's first game**, not a fixed "2 hours before" — so
there can be up to four separate deadlines in a week (a Wed or Thu opener, the full Sunday
slate at 1pm ET, Monday night). Before each one:

```bash
python3 scripts/pickem_capture.py --snapshot lock-sun --week N --confirm
```

Give each deadline its own label — `lock-wed`, `lock-thu`, `lock-sun`, `lock-mon` — which is
what the timers do. A shared `lock` keeps only the first reading of the week.

Then ask Claude for the refreshed picks for whatever's about to lock, and enter them in the
CBS app. **Enter picks for every game as early as you can stomach, then revise before each
deadline** — missed-week scoring is zero, so an empty day is the one truly fatal mistake.

### The mid-week reading — added 2026-08-23, free since 2026-09-08

```bash
python3 scripts/pickem_capture.py --snapshot midweek --week N --confirm
```

Run this Thursday or Friday, between the Tuesday freeze and the weekend deadlines. It is not
optional housekeeping: **the 2014–2024 archive has only two snapshots per game (open and close),
so it can never tell us WHEN post-freeze movement happens.** One extra call a week buys the one thing no amount of historical analysis can recover, and
`pickem_transferability.py` reports it back as a freeze→midweek and midweek→lock split.

Any label works if you want extra readings; `midweek` is the one the tooling looks for.

**Cost:** free. Re-running the same snapshot label is a no-op (de-duped), so it's safe to run
twice by accident — and that same de-duping is why two *different* deadlines must not share a
label.

---

## 3. After the games finish

```bash
python3 scripts/pickem_grade.py                 # dry run — prints, writes nothing
python3 scripts/pickem_grade.py --week N --write  # fills tracker.csv's graded columns
```

Free, no key. Takes CBS's frozen line and the **last `lock*` reading before each kickoff**,
runs the *shipped* `make_pick` over them, joins nflverse final scores, and grades with the
same `ats_result` the backtest uses — so "covered" means here exactly what it means there.
Prints W-L-P overall, by tier, and split into signal vs fallback games, each beside its
backtested benchmark (55.9% / 56.7% / 50.6%) with a 95% interval.

> **Read the interval, not the rate.** Sixteen games is worth about ±12 percentage points —
> a 9-7 week is indistinguishable from the backtest *and* from a coin flip at the same time.
> The 55.9% headline took 543 games.

`--write` fills only **blank** cells of `data/pickem/tracker.csv` (gitignored, never leaves
your machine); anything you typed — `my_pick`, `confidence_1to3`, `notes` — is never touched.

Also worth a minute once a week: check the pool's **Standings** page and update
`data/pickem/standings.csv` — your rank, wins, and the leader's wins. This is what feeds the
late-season strategy module once Week 14 arrives (`edge/pickem_strategy.py`), and it only
works if the numbers are current.

---

## 4. Whenever — check where things stand

```bash
python3 scripts/pickem_blocked.py
```

Free, instant, no credits. Shows progress toward every experiment that's blocked on data
that didn't used to exist — CBS-bias isolation, line velocity, sharp-book disagreement,
public-pick fading — and marks each `** RUNNABLE **` the moment it has enough rows. Run it
any time you're curious, or just once a month to see the bars fill in.

```bash
cat data/odds_api_credits.json
```
Your remaining Odds-API credits for the cycle, if you want a sanity check before a capture.

---

## Quick reference — costs and cadence

| Command | When | Cost |
|---|---|---|
| `pickem_pool_import.py ... --write` | Tuesday, right after copying the Picks page | free |
| `pickem_capture.py --snapshot post` | Tuesday, same few minutes | **free** |
| `pickem_capture.py --snapshot lock-<day>` | Before each of the week's deadlines | **free** (automatic) |
| `pickem_capture.py --snapshot midweek` | Thursday or Friday — **not optional** | **free** (automatic) |
| `pickem_grade.py` | After each week's games finish | free |
| `pickem_transferability.py` | Any time (needs ~4 weeks of captures) | free |
| `pickem_blocked.py` | Any time | free |

**A full season now costs 0 credits.** Captures read the scraped DK / FanDuel / Fanatics board.
`--source paid` switches back to The Odds API's ~10 books at `markets x regions` = 2 credits a
call (~216 for a season at 6 captures a week). The free board is 3 books, a thinner consensus —
`edge/pickem_free.py` is honest that most of the gain from averaging arrives by the third or
fourth book.

---

## If something looks wrong

- **`No such file or directory`** — you're not in `/home/asr/edge_search`. `cd` there first.
- **`Parsed 0 games`** from the importer — you copied the wrong page (Standings or Settings
  instead of Picks), or copied a screenshot instead of text. It needs real page text.
- **`No ODDS_API_KEY set`** from a capture — only reachable with `--source paid` now, and the
  key is read from `~/arbitrage/.env` automatically. Drop `--source paid` to use the free board.
- **`StaleOdds: newest 'pickem_nfl' scan finished ...`** — the scraped board is older than the
  profile's one-day contract. Collect a fresh one:
  `python3 scripts/odds_collect.py --profile pickem_nfl`.
- **`... home team(s) appear more than once`** — the board is spanning two weeks and the capture
  refused rather than guess which game you meant. Check `--week`.
- **A capture says `(0 new: this snapshot was already recorded)`** — that exact
  snapshot (season/week/label) was already logged. Not an error; re-running the same label
  is designed to be a safe no-op. Use a new label if you genuinely want a fresh reading.
- **Anything else** — paste the output to Claude. Everything these scripts touch is either
  in this repo or in your own `data/pickem/`, so there's always a file to look at.
