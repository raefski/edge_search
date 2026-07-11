---
name: run-dfs-app
description: Launch and drive the DK MLB DFS Streamlit app (app.py) headlessly for local dev/debugging -- catches the exact tracebacks a phone user would see, without relaying through the phone. Use when asked to run the DFS app, reproduce a Streamlit error the user reports, or verify an app.py/edge/dfs_*.py change actually works end-to-end before saying it's fixed.
---

Runs `app.py` (Streamlit) as a real local server and drives it with a headless
Chromium via Playwright (Python). Reproduces exactly what the user's phone
shows -- including Streamlit's crashed-app error box -- without a phone in
the loop. Defaults to CACHE mode (dry-run client, 0 Odds-API credits): the
main page always builds off `data/cache/` + free statsapi calls.

## Run (agent path)

```bash
cd /home/asr/Downloads/edge_search
tmux new-session -d -s dfsapp -x 200 -y 50 -c "$PWD"
tmux send-keys -t dfsapp 'python3 .claude/skills/run-dfs-app/driver.py' Enter
sleep 2
tmux send-keys -t dfsapp 'launch' Enter
# poll instead of a blind sleep -- first build can take several seconds
timeout 30 bash -c 'until tmux capture-pane -t dfsapp -p | grep -q "launched\."; do sleep 1; done'
tmux send-keys -t dfsapp 'ss landing' Enter
sleep 8   # let the cached_build spinner finish -- there is no clean "done" signal from a screenshot alone
tmux send-keys -t dfsapp 'ss built' Enter
tmux send-keys -t dfsapp 'errors' Enter
tmux capture-pane -t dfsapp -p
```

Then actually look at the screenshot (`Read` tool on `/tmp/shots/built.png`)
-- don't just check for the absence of a Python exception. A blank page or a
placeholder is a different failure mode than a crash.

Stop with `tmux send-keys -t dfsapp 'quit' Enter` (closes the browser +
kills the streamlit subprocess cleanly), then `tmux kill-session -t dfsapp`.

## Commands

| command | what it does |
|---|---|
| `launch` | start `streamlit run app.py` on port 8765 + open a headless Chromium page against it |
| `ss <name>` | screenshot -> `/tmp/shots/<name>.png` (override dir: `SCREENSHOT_DIR`) |
| `click <selector>` | Playwright `page.click`, e.g. `click text=GPP`, `click text=Refresh` |
| `wait <selector>` | wait up to 10s for a selector |
| `text [selector]` | print `innerText` (omit selector for the whole body) |
| `errors` | check for Streamlit's "has encountered an error" box + any JS console/page errors |
| `logs` | streamlit's own stdout (server-side, full tracebacks -- NOT redacted like Streamlit Cloud's) |
| `quit` | close browser + kill the streamlit process |

## Testing a specific change

- **App-level Python exception** (crashes on load, like the `log_forward_test`
  TypeError this skill was built to catch): `launch` + `errors`. Streamlit
  re-runs the whole script on every page load, so this alone catches most bugs.
- **A specific tab/button** (GPP construction, Late-swap, Save entry): `click
  text=<tab name>`, `sleep` a couple seconds for the rerun, `ss`, `errors`.
- **Live (paid) props path**: set `ODDS_API_KEY` in the environment before
  `launch`, then `click text=Pull fresh pitcher props`. This spends real
  credits -- only do it when actually testing that path, and check
  `data/odds_api_credits.json` before/after.

## Gotchas

- **No clean "build finished" signal.** `cached_build`'s spinner has no DOM
  marker this driver polls for -- `sleep 5-10` after `launch`/`click` before
  screenshotting, or the screenshot catches the "Building slate…" spinner
  mid-flight (harmless, just re-screenshot).
- **First launch after an `edge/`-module change needs a fresh process.**
  Streamlit's own file-watcher reruns the SCRIPT on save, but a change to an
  imported module (`edge/dfs_opt.py`, `edge/dfs_run.py`, etc.) sometimes
  needs a full `quit` + `launch` to pick up, not just a page click -- if a
  fix "doesn't seem to apply," restart the whole session before debugging
  further.
- **Streamlit Cloud's error box redacts messages** ("original error message
  is redacted to prevent data leaks") -- this local driver's `logs` command
  and Python's own unredacted traceback are strictly more informative than
  what the user's phone will ever show. Prefer reproducing locally over
  asking for another phone screenshot.
- **`--no-sandbox`** is required for Chromium under this container (no
  `CAP_SYS_ADMIN` / user namespaces) -- baked into the driver already.

## Prerequisites (already satisfied in this repo's env)

```bash
pip install playwright && playwright install chromium --with-deps
```
