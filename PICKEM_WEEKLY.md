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

**⚠ You cannot capture from your phone, and it is important to know why.** The Streamlit page
**only reads** the log — it never writes to it. And Streamlit Community Cloud's filesystem is
**ephemeral**: anything written there vanishes on the next restart or redeploy, and it cannot push
to git. So tapping "Pull fresh lines" on your phone shows you current numbers but **saves
nothing**. The phone is a viewer. Capture happens either on a machine with git, or automatically
(below).

### The automated capture — set this up once, before Week 1

`.github/workflows/pickem-capture.yml` runs the **market half** of the capture on a schedule
(Tue / Fri / Sun), commits the rows to the repo, and needs nobody to be awake. That matters
because a market reading not taken at the right moment is **gone forever** — the historical file
holds only two snapshots per game and can never fill the hole.

**One manual step:** add `ODDS_API_KEY` as a repository secret —
GitHub → Settings → Secrets and variables → Actions → New repository secret. Until that exists
every run fails loudly rather than quietly banking nothing.

Then trigger it by hand once (Actions tab → *Pick'em line capture* → Run workflow) to prove it
works before the season starts.

**The CBS half still needs you** — those lines are behind a login. But the two halves join on
`(season, week, home_team)`, so CBS's numbers can be filled in *afterwards* with
`--snapshot <same label>` and the time-critical market reading never waits for you.

---

## The five-minute version

```bash
cd /home/asr/edge_search

# 1. Tuesday, right after CBS posts — paste the Picks page, import it
python3 scripts/pickem_pool_import.py picks.txt --week N --write

# 2. Immediately after — log the freeze (2 credits)
python3 scripts/pickem_capture.py --snapshot post --week N --confirm

# 3. Thursday or Friday — the mid-week reading (2 credits). NEW, do not skip:
#    this is the ONLY way to learn the TIME PROFILE of post-freeze drift.
python3 scripts/pickem_capture.py --snapshot midweek --week N --confirm

# 4. Before each day's deadline this week — log it again (2 credits)
python3 scripts/pickem_capture.py --snapshot lock --week N --confirm

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

**Cost:** step 5 spends 2 Odds-API credits (spreads + totals, one call for the whole
slate). Step 4 is free.

---

## 2. Before each deadline this week

Your deadline is **the start of that day's first game**, not a fixed "2 hours before" — so
there can be up to four separate deadlines in a week (a Wed or Thu opener, the full Sunday
slate at 1pm ET, Monday night). Before each one:

```bash
python3 scripts/pickem_capture.py --snapshot lock --week N --confirm
```

Then ask Claude for the refreshed picks for whatever's about to lock, and enter them in the
CBS app. **Enter picks for every game as early as you can stomach, then revise before each
deadline** — missed-week scoring is zero, so an empty day is the one truly fatal mistake.

### The mid-week reading — added 2026-08-23, and worth the 2 credits

```bash
python3 scripts/pickem_capture.py --snapshot midweek --week N --confirm
```

Run this Thursday or Friday, between the Tuesday freeze and the weekend deadlines. It is not
optional housekeeping: **the 2014–2024 archive has only two snapshots per game (open and close),
so it can never tell us WHEN post-freeze movement happens.** One extra call a week (~36 credits
a season) buys the one thing no amount of historical analysis can recover, and
`pickem_transferability.py` reports it back as a freeze→midweek and midweek→lock split.

Any label works if you want extra readings; `midweek` is the one the tooling looks for.

**Cost:** 2 credits each time you run it with `--confirm`. Re-running the same snapshot
label is a no-op (de-duped), so it's safe to run twice by accident.

---

## 3. After the games finish

**This step is currently manual — there's no script for it yet.** Two options, either is
fine:

- **Tell Claude the final scores** for the week and ask it to update `data/pickem/tracker.csv`
  (your season log, gitignored, never leaves your machine).
- **Edit the CSV yourself** — fill in `final_away_score`, `final_home_score`, and `ats_result`
  (W/L/P) for each row.

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
| `pickem_capture.py --snapshot post` | Tuesday, same few minutes | 2 credits |
| `pickem_capture.py --snapshot lock` | Before each of the week's deadlines | 2 credits |
| `pickem_capture.py --snapshot midweek` | Thursday or Friday — **not optional** | 2 credits |
| `pickem_transferability.py` | Any time (needs ~4 weeks of captures) | free |
| `pickem_blocked.py` | Any time | free |

A full 18-week season at 3 captures a week (post + midweek + one lock) is about **108 credits** — under
15% of the 500/month free tier, with plenty of room for extra mid-week readings.

---

## If something looks wrong

- **`No such file or directory`** — you're not in `/home/asr/edge_search`. `cd` there first.
- **`Parsed 0 games`** from the importer — you copied the wrong page (Standings or Settings
  instead of Picks), or copied a screenshot instead of text. It needs real page text.
- **`No ODDS_API_KEY set`** from a capture — the key isn't in your environment for this
  terminal session; check `.env` or ask Claude to help track it down.
- **A capture says `(0 new: this snapshot was already recorded)`** — that exact
  snapshot (season/week/label) was already logged. Not an error; re-running the same label
  is designed to be a safe no-op. Use a new label if you genuinely want a fresh reading.
- **Anything else** — paste the output to Claude. Everything these scripts touch is either
  in this repo or in your own `data/pickem/`, so there's always a file to look at.
