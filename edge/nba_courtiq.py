"""NBA position shuffle: who takes a missing player's START and MINUTES. Our CourtIQ.

WHY
When a center sits, the backup center does not always take his place. The starting PF
can slide to C and a wing start at PF, or a PF-type comes off the bench for the big
minutes. The 2026-10-29 availability critique tested "the same-position backup gets the
minutes" on top of the minutes model and it was worse at every weight (k = 0 on 2024-25;
NBA_MINUTES_CRITIQUE_LOG.md cycle 21) -- position labels are not how coaches re-share
minutes. What a team actually did the nights that player sat is.

WHAT IT SHOWS (game level, like edge/nba_onoff.py, not on-court stints: the DFS question
is "he is OUT tonight", not "he is resting for four minutes")
  * the starting five by SLOT (NBA.com's PG/SG/SF/PF/C) in games the OUT players played
    vs. games they all missed -- who started at C without the center, and whether the
    starting PF moved there;
  * every teammate's minutes, DK points, DK points per minute, rebounds per minute and
    start rate, with vs. without, with n.
Small n is the norm (a regular misses ~10 games a season): every row shows its n, and
under ~3 games "without" it is an anecdote, not a split.

THE DATA (built on the desktop, read on the phone)
data/nba_courtiq.json is written by scripts/nba_history_publish.py (06:00 / 14:00 timer)
from data/nba_lineups/ (NBA.com starters with slots, since 2023-24) and the stats.nba.com
player gamelogs (data/nba_ground_truth/), neither of which is on Streamlit Cloud:
    {"built", "names": {pid: name}, "teams": {team: [[date, gid, {pid: slot},
                                                       {pid: [min, dk, reb, ast, blk]}], ...]}}
A team-game is kept only when NBA.com listed exactly five starters. A player "missed" a
game when he has no minutes in it and the game falls between his first and last game for
that team in the data (a trade or an arrival is not an absence).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "nba_courtiq.json"
SEASONS = ("2025-26", "2026-27")
SLOTS = ("PG", "SG", "SF", "PF", "C")
#: Below this many games without, a split is shown but flagged as thin.
THIN = 3


def _season_of(d: str) -> str:
    y, m = int(d[:4]), int(d[5:7])
    s = y if m >= 7 else y - 1
    return f"{s}-{str(s + 1)[2:]}"


def build(lineup_dir: Path | None = None, gt_dir: Path | None = None, seasons=SEASONS) -> dict:
    """The compact table, from the lineup cache and the regular-season gamelogs."""
    from edge import nba
    from edge.nba_lineups import CACHE, parse

    lineup_dir = lineup_dir or CACHE
    gt_dir = gt_dir or ROOT / "data" / "nba_ground_truth"
    box: dict = defaultdict(dict)                          # gid -> {pid: row}
    names: dict = {}
    for s in seasons:
        f = gt_dir / f"player_gamelog_{s}.json"
        if not f.exists():
            continue
        for r in json.loads(f.read_text()):
            m = float(r.get("MIN") or 0)
            if m <= 0:
                continue
            pid = str(r["PLAYER_ID"])
            names[pid] = r["PLAYER_NAME"]
            box[r["GAME_ID"]][pid] = (nba.team(r["TEAM_ABBREVIATION"]),
                                      [round(m, 1), nba.actual_points(r), int(r.get("REB") or 0),
                                       int(r.get("AST") or 0), int(r.get("BLK") or 0)])
    teams: dict = defaultdict(list)
    for path in sorted(lineup_dir.glob("*.json")):
        d = f"{path.stem[:4]}-{path.stem[4:6]}-{path.stem[6:8]}"
        if _season_of(d) not in seasons:
            continue
        for g in parse(json.loads(path.read_text())):
            gid = g["game_id"]
            if gid not in box:
                continue
            for team in {p["team"] for p in g["players"]}:
                five = {str(p["id"]): (p.get("position") or "?") for p in g["players"]
                        if p["team"] == team and p.get("starter")}
                if len(five) != 5:
                    continue
                played = {pid: v for pid, (t, v) in box[gid].items() if t == nba.team(team)}
                if played:
                    teams[nba.team(team)].append([d, gid, five, played])
    for t in teams:
        teams[t].sort()
    return {"names": names, "teams": dict(teams)}


def roster(table: dict, team: str, since: str | None = None) -> list[tuple[str, str, int, float]]:
    """[(pid, name, games, avg minutes)] for the team's players, most minutes first."""
    games = [g for g in table["teams"].get(team, []) if not since or g[0] >= since]
    mins: dict = defaultdict(list)
    for _, _, _, played in games:
        for pid, v in played.items():
            mins[pid].append(v[0])
    out = [(pid, table["names"].get(pid, pid), len(m), sum(m) / len(m)) for pid, m in mins.items()]
    return sorted(out, key=lambda x: -x[2] * x[3])


def split(table: dict, team: str, out_ids: list[str], since: str | None = None) -> dict:
    """With vs. without `out_ids` (all of them played / all of them missed)."""
    games = [g for g in table["teams"].get(team, []) if not since or g[0] >= since]
    spans = {}
    for pid in out_ids:
        dates = [g[0] for g in games if pid in g[3]]
        spans[pid] = (min(dates), max(dates)) if dates else None
    if not out_ids or any(v is None for v in spans.values()):
        return {"with": [], "without": [], "slots": {}, "mates": []}
    lo = max(v[0] for v in spans.values())
    hi = max(v[1] for v in spans.values())
    window = [g for g in games if lo <= g[0] <= hi] if len(out_ids) == 1 else \
        [g for g in games if all(spans[p][0] <= g[0] <= spans[p][1] for p in out_ids)]
    with_ = [g for g in window if all(p in g[3] for p in out_ids)]
    without = [g for g in window if all(p not in g[3] for p in out_ids)]
    # an absence at the very end of the span counts too (injured now): a single OUT
    # player's span ends at his last game, so tonight's injury streak is the games after
    # it -- include the team's games after his last one in "without"
    if len(out_ids) == 1:
        without += [g for g in games if g[0] > hi]

    def slot_shares(gs):
        c: dict = {s: Counter() for s in SLOTS}
        for _, _, five, _ in gs:
            for pid, s in five.items():
                if s in c:
                    c[s][pid] += 1
        return {s: [(pid, n / len(gs)) for pid, n in c[s].most_common(3)] for s in SLOTS} if gs else {}

    def agg(gs, pid):
        rows = [g[3][pid] for g in gs if pid in g[3]]
        n = len(rows)
        if not n:
            return None
        m = sum(r[0] for r in rows)
        starts = sum(1 for g in gs if pid in g[2])
        slot = Counter(g[2][pid] for g in gs if pid in g[2]).most_common(1)
        return {"n": n, "min": m / n, "dk": sum(r[1] for r in rows) / n,
                "dkpm": sum(r[1] for r in rows) / m if m else 0.0,
                "rebpm": sum(r[2] for r in rows) / m if m else 0.0,
                "start": starts / len(gs), "slot": slot[0][0] if slot else ""}

    mates = []
    seen = {pid for g in with_ + without for pid in g[3]} - set(out_ids)
    for pid in seen:
        a, b = agg(with_, pid), agg(without, pid)
        if b is None:
            continue
        mates.append({"pid": pid, "name": table["names"].get(pid, pid), "with": a, "without": b,
                      "dmin": b["min"] - (a["min"] if a else 0.0),
                      "ddk": b["dk"] - (a["dk"] if a else 0.0)})
    mates.sort(key=lambda r: -r["dmin"])
    return {"with": with_, "without": without, "mates": mates,
            "slots": {"with": slot_shares(with_), "without": slot_shares(without)}}


def save(table: dict, path: Path = OUT) -> str:
    text = json.dumps(table, separators=(",", ":"), sort_keys=True)
    path.write_text(text)
    return text
