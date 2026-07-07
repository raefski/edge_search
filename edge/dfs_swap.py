"""Late-swap logic shared by the CLI (scripts/dfs_swap.py) and the phone app.

Given the lineup you ENTERED on DK and a freshly-built hitter pool (confirmed +
projected), classify each entered hitter and, for anyone now ruled OUT of a
posted order, suggest the best same-position replacement that fits the salary
freed up and whose game hasn't locked. Pure functions — no network except the
optional game_started_map helper — so the classification is unit-testable.
"""
import csv
from pathlib import Path

from edge import dfs

CAP = 50000

ENTRY_COLS = ("player", "team", "salary", "pos", "game", "conf")


# ── pinned "my DK entry" — the single canonical location both the CLI and the
# phone app read/write, so pinning from either device is visible to the other.
# CAVEAT: on Streamlit Community Cloud this disk is ephemeral — if the app
# sleeps from inactivity and the container restarts, the file resets to
# whatever's in the last git commit. It survives a closed tab or a refresh,
# but not a guaranteed overnight sleep/wake. The CLI, run on your own machine,
# has no such limit.
def entry_path(root: Path, date: str, mode: str) -> Path:
    return root / f"data/dfs_entries_{date}_{mode}.csv"


def load_pinned_entry(root: Path, date: str, mode: str) -> list[dict] | None:
    p = entry_path(root, date, mode)
    if not p.exists():
        return None
    rows = [r for r in csv.DictReader(open(p))]
    return rows or None


def save_pinned_entry(root: Path, date: str, mode: str, rows: list[dict]) -> Path:
    p = entry_path(root, date, mode)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(ENTRY_COLS))
        for r in rows:
            w.writerow([r.get(c, "") for c in ENTRY_COLS])
    return p


def game_started_map(date: str) -> dict:
    """gamePk -> True once that game has locked (first pitch passed / live / final)."""
    s = dfs._get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}")
    out = {}
    for d in s.get("dates", []):
        for g in d.get("games", []):
            out[g["gamePk"]] = g.get("status", {}).get("abstractGameState", "") in ("Live", "Final")
    return out


def _pos_set(v):
    if isinstance(v, (set, frozenset, list, tuple)):
        return set(v)
    return set(str(v).split("/")) if v else set()


def suggest_swaps(entered, hitters, started, mode="cash", cap=CAP, top=4):
    """entered: list of dicts (player, team, salary, pos, game, conf) — your DK entry.
    hitters: fresh pool hitters (name, team, pos, salary, proj, ceiling, own, game, confirmed).
    started: {gamePk: bool} lock map. mode: 'cash' (rank by proj) or 'gpp' (ceiling).

    Returns one record per entered HITTER:
      status: 'confirmed' (in a posted order), 'hold' (their team hasn't posted),
              'out' (team posted WITHOUT them -> needs a swap).
      For 'out': locked (their own game already started -> stuck) + suggestions list.
    """
    by_name = {dfs.norm(h["name"]): h for h in hitters}
    confirmed_names = {dfs.norm(h["name"]) for h in hitters if h.get("confirmed", True)}
    confirmed_teams = {h["team"] for h in hitters if h.get("confirmed", True)}
    lineup_names = {dfs.norm(r["player"]) for r in entered}
    total_sal = sum(int(r["salary"]) for r in entered)
    leftover = cap - total_sal
    key = "ceiling" if mode == "gpp" else "proj"
    # game keys can be int (statsapi) or str (CSV) -> compare as strings
    lockmap = {str(k): v for k, v in started.items()}
    is_locked = lambda gp: lockmap.get(str(gp), False)

    records = []
    for r in entered:
        pos = _pos_set(r.get("pos"))
        if "P" in pos:            # pitchers aren't projected-lineup driven
            continue
        n = dfs.norm(r["player"])
        was_proj = "PROJ" in (r.get("conf") or "")
        if n in confirmed_names:
            records.append({"player": r["player"], "team": r["team"], "salary": int(r["salary"]),
                            "status": "confirmed", "was_projected": was_proj})
            continue
        if r["team"] not in confirmed_teams:
            records.append({"player": r["player"], "team": r["team"], "salary": int(r["salary"]),
                            "status": "hold", "was_projected": was_proj})
            continue
        # team posted a lineup and this player isn't in it -> OUT
        out_sal = int(r["salary"])
        max_sal = out_sal + leftover
        out_game = r.get("game") or by_name.get(n, {}).get("game")
        locked = is_locked(out_game)
        cands = []
        if not locked:
            cands = [h for h in hitters
                     if h.get("confirmed", True)
                     and pos & set(h["pos"])
                     and dfs.norm(h["name"]) not in lineup_names
                     and int(h["salary"]) <= max_sal
                     and not is_locked(h["game"])]
            cands.sort(key=lambda h: (h.get(key, h["proj"]) + (0.5 if h["team"] == r["team"] else 0)), reverse=True)
        records.append({
            "player": r["player"], "team": r["team"], "salary": out_sal, "status": "out",
            "was_projected": was_proj, "locked": locked, "max_salary": max_sal,
            "suggestions": [{"name": h["name"], "team": h["team"], "salary": int(h["salary"]),
                             "val": round(h.get(key, h["proj"]), 1), "own": round(h.get("own", 0), 1),
                             "same_team": h["team"] == r["team"]} for h in cands[:top]],
        })
    return records
