#!/usr/bin/env python3
"""Record every pool entry's picks, and when each game's picks locked.

WHY (2026-10-11)
The standings module (edge/pickem_strategy.py, PICKEM_MODEL.md section 7) was
built on CBS's NATIONAL pick percentages because "the pool's own picks are
visible in the CBS app after each lock" and nothing recorded them. They are on
the pool's Standings -> Weekly page, behind the same stored login session
scripts/pickem_pool_fetch.py already uses. The first pull (weeks 1-5) showed
the national numbers were the wrong proxy: rivals agree with Adam's side only
44-59% of the time, and the field-majority side went 35-42.

It also settles a question nobody could answer from documents. Adam says picks
lock two hours before kickoff; the pool's Settings page says "before the start
of the first game of the day". CBS shows other entries' picks for a game only
once it has locked, so a fetch just after each candidate deadline records which
rule is true (edge.pickem_deadline.infer_rule).

WHAT IT WRITES -- both gitignored (data/pickem/), real opponents' names:
  data/pickem/pool_picks.csv     one row per (season, week, entry, game)
  data/pickem/pool_lock_obs.csv  one row per (fetch, game): were picks visible?

    python3 scripts/pickem_pool_picks.py                 # this week, + lock obs
    python3 scripts/pickem_pool_picks.py --weeks 1-5     # backfill
    python3 scripts/pickem_pool_picks.py --report        # no fetch
"""
from __future__ import annotations

import argparse
import csv
import datetime
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem import ats_result  # noqa: E402
from edge.pickem_deadline import (  # noqa: E402
    KICKOFF_MINUS_2H, FIRST_GAME_OF_DAY, RULE, infer_rule, parse_utc)
from edge.pickem_log import load as load_line_log  # noqa: E402
from edge.pickem_week import EASTERN, SEASON_START, current_week  # noqa: E402

PICKS = ROOT / "data" / "pickem" / "pool_picks.csv"
OBS = ROOT / "data" / "pickem" / "pool_lock_obs.csv"
PICK_COLS = ["season", "week", "observed_at", "player", "mine", "away", "home",
             "cbs_line_home", "pick", "away_score", "home_score", "status"]
OBS_COLS = ["observed_at", "season", "week", "away", "home", "kickoff_utc",
            "visible_others", "n_others"]

#: The standings page spells two teams differently from the line log.
CANON = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR"}
LINE = re.compile(r"^\(([+-]?\d+(?:\.\d+)?)\)$")


def canon(abbr: str) -> str:
    return CANON.get(abbr, abbr)


ENTRY = re.compile(r"\n(\d+(?:st|nd|rd|th))\n([^\n]+)\n\t(\d+)\t(\d+)\t(.*?)"
                   r"(?=\n\d+(?:st|nd|rd|th)\n|\nMore\s+Pick'em|\Z)", re.S)
#: A cell CBS left empty: another entry's pick on a game that has not locked.
HIDDEN = ""


def parse_standings_text(text: str) -> tuple[list[dict], list[dict]]:
    """(games, entries) from the Weekly standings page's visible text.

    The text runs: header columns, then one block per game -- status line,
    away, home, [away score, home score], "(home line)" -- then one block per
    entry: rank, name, "<TAB>pts<TAB>ytd<TAB>", then one TAB-separated cell per
    game in game order. A cell is a team, "-" (no pick made), or EMPTY: another
    entry's pick on a game that has not locked yet. Empty cells collapse to
    nothing if the text is split on lines, which misaligns every pick after
    them -- so the cells are split on the tabs.
    """
    start = text.find("YTD")
    if start < 0:
        raise ValueError("not a standings page: no YTD column header")
    body = text[start + 3:]
    first = re.search(r"\n\d+(?:st|nd|rd|th)\n", body)
    head_end = first.start() if first else len(body)
    head = [l.strip() for l in body[:head_end].split("\n")]
    head = [l for l in head if l]

    games = []
    for i, tok in enumerate(head):
        m = LINE.match(tok)
        if not m:
            continue
        j, scores = i - 1, []
        while j >= 0 and head[j].isdigit():
            scores.insert(0, int(head[j]))
            j -= 1
        games.append(dict(away=head[j - 1], home=head[j],
                          line=float(m.group(1)),
                          away_score=scores[0] if len(scores) == 2 else None,
                          home_score=scores[1] if len(scores) == 2 else None,
                          status=head[j - 2] if j >= 2 else ""))

    entries = []
    for m in ENTRY.finditer(body[head_end:]):
        cells = [c.strip() for c in m.group(5).split("\t")]
        while len(cells) > len(games) and cells[-1] == HIDDEN:
            cells.pop()
        cells += [HIDDEN] * (len(games) - len(cells))
        if len(cells) != len(games):
            raise ValueError(f"{m.group(2)}: {len(cells)} cells for {len(games)} games")
        entries.append(dict(player=m.group(2).strip(), pts=int(m.group(3)),
                            ytd=int(m.group(4)), picks=cells))
    if not entries:
        raise ValueError("no entries found on the standings page")
    return games, entries


def my_entry_name(html: str) -> str | None:
    """CBS flags the logged-in entry in the page's embedded JSON."""
    m = re.search(r'"isMine":true,"name":"([^"]+)"', html)
    return m.group(1) if m else None


def fetch_week(week: int, timeout: int = 60) -> tuple[str, str]:
    """(visible text, html) of the Weekly standings page for `week`.

    `?week=` is ignored by CBS; the page's own MUI week dropdown is the only
    way to a past week, so this clicks it.
    """
    from playwright.sync_api import sync_playwright

    from edge.pickem_cbs import (DEFAULT_SESSION_PATH, STEALTH_ARGS, UA,
                                 SessionExpired, ensure_chromium_libs,
                                 patch_automation_tells, pool_url)
    url = pool_url()
    if not url:
        raise RuntimeError("No CBS_POOL_URL in edge_search/.env")
    base, _, query = url.partition("?")
    ensure_chromium_libs()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"] + STEALTH_ARGS)
        ctx = b.new_context(storage_state=str(DEFAULT_SESSION_PATH), user_agent=UA,
                            viewport={"width": 1600, "height": 1200})
        patch_automation_tells(ctx)
        pg = ctx.new_page()
        pg.goto(f"{base}/standings/weekly" + (f"?{query}" if query else ""),
                wait_until="networkidle", timeout=timeout * 1000)
        if "/join" in pg.url or "/login" in pg.url or "signin" in pg.url.lower():
            b.close()
            raise SessionExpired(f"landed on {pg.url}; re-run "
                                 "scripts/pickem_session_bootstrap.py")
        pg.locator('div[role="combobox"]').first.click()
        pg.get_by_role("option", name=f"Week {week}", exact=True).click()
        pg.wait_for_timeout(4000)
        text, html = pg.inner_text("body"), pg.content()
        b.close()
    return text, html


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _write(path: Path, cols: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def upsert_picks(season: int, week: int, games: list[dict], entries: list[dict],
                 mine: str | None, observed_at: str, path: Path = PICKS) -> int:
    """Merge one fetch into the picks file. A newer observation wins, except
    that a hidden "-" never erases a pick already seen."""
    rows = {(r["season"], r["week"], r["player"], r["away"], r["home"]): r
            for r in _read(path)}
    n = 0
    for e in entries:
        for g, pick in zip(games, e["picks"]):
            key = (str(season), str(week), e["player"], canon(g["away"]), canon(g["home"]))
            old = rows.get(key)
            if pick in ("-", HIDDEN):
                if old and old["pick"] not in ("-", HIDDEN):
                    pick = old["pick"]
                else:
                    pick = "-"
            rows[key] = dict(
                season=season, week=week, observed_at=observed_at,
                player=e["player"], mine=int(e["player"] == mine),
                away=canon(g["away"]), home=canon(g["home"]),
                cbs_line_home=g["line"], pick=canon(pick),
                away_score="" if g["away_score"] is None else g["away_score"],
                home_score="" if g["home_score"] is None else g["home_score"],
                status=g["status"])
            n += 1
    _write(path, PICK_COLS, sorted(rows.values(), key=lambda r: (
        int(r["season"]), int(r["week"]), r["home"], r["player"])))
    return n


def _kickoffs(season: int, week: int) -> dict[str, str]:
    out = {}
    for r in load_line_log():
        if str(r.get("season")) == str(season) and str(r.get("week")) == str(week) \
                and r.get("kickoff_utc"):
            out[r["home_team"]] = r["kickoff_utc"]
    return out


def record_lock_obs(season: int, week: int, games: list[dict], entries: list[dict],
                    mine: str | None, observed_at: str, path: Path = OBS) -> int:
    kicks = _kickoffs(season, week)
    others = [e for e in entries if e["player"] != mine]
    rows = _read(path)
    for gi, g in enumerate(games):
        rows.append(dict(
            observed_at=observed_at, season=season, week=week,
            away=canon(g["away"]), home=canon(g["home"]),
            kickoff_utc=kicks.get(canon(g["home"]), ""),
            visible_others=sum(e["picks"][gi] not in ("-", HIDDEN) for e in others),
            n_others=len(others)))
    _write(path, OBS_COLS, rows)
    return len(games)


def lock_observations(path: Path = OBS) -> list[dict]:
    """Observations in the shape edge.pickem_deadline.infer_rule takes.

    A game counts as locked when at least half the other entries' picks show
    -- not "any", because an entry that never picked shows "-" either way.
    """
    rows = [r for r in _read(path) if r.get("kickoff_utc")]
    first: dict[tuple, datetime.datetime] = {}
    for r in rows:
        k = parse_utc(r["kickoff_utc"])
        d = (r["season"], r["week"], k.astimezone(EASTERN).date())
        first[d] = min(first.get(d, k), k)
    out = []
    for r in rows:
        k = parse_utc(r["kickoff_utc"])
        out.append(dict(observed_at=parse_utc(r["observed_at"]), kickoff=k,
                        day_first=first[(r["season"], r["week"],
                                         k.astimezone(EASTERN).date())],
                        locked=int(r["visible_others"]) * 2 >= int(r["n_others"] or 1)))
    return out


def report(path: Path = PICKS) -> None:
    rows = _read(path)
    if not rows:
        print(f"No pool picks yet in {path}. Run without --report first.")
        return
    wins: dict[str, int] = defaultdict(int)
    decided: dict[str, int] = defaultdict(int)
    weekly: dict[tuple, int] = defaultdict(int)
    by_game: dict[tuple, dict[str, str]] = defaultdict(dict)
    mine = next((r["player"] for r in rows if r["mine"] == "1"), None)
    for r in rows:
        if r["pick"] == "-":
            continue
        g = (r["season"], r["week"], r["away"], r["home"])
        by_game[g][r["player"]] = r["pick"]
        if not r["status"].startswith("FINAL") or r["home_score"] == "":
            continue
        side = "home" if r["pick"] == r["home"] else "away"
        res = ats_result(float(r["home_score"]) - float(r["away_score"]),
                         float(r["cbs_line_home"]), side)
        decided[r["player"]] += res != "P"
        if res == "W":
            wins[r["player"]] += 1
            weekly[(r["player"], int(r["week"]))] += 1
    weeks = sorted({int(r["week"]) for r in rows})
    print("POOL STANDINGS (finished games, graded against CBS's frozen line)")
    print(f"{'entry':<24}{'W':>4}{'of':>5}  " + "".join(f"{'wk' + str(w):>6}" for w in weeks))
    for p in sorted(wins, key=lambda p: -wins[p]):
        tag = " *" if p == mine else ""
        print(f"{p[:22] + tag:<24}{wins[p]:>4}{decided[p]:>5}  "
              + "".join(f"{weekly.get((p, w), 0):>6}" for w in weeks))
    if mine:
        print(f"\nHOW OFTEN EACH RIVAL PICKS {mine.upper()}'S SIDE "
              "(low = more chances to gain or lose ground)")
        agree: dict[str, list[int]] = defaultdict(list)
        for picks in by_game.values():
            if mine not in picks:
                continue
            for p, s in picks.items():
                if p != mine:
                    agree[p].append(s == picks[mine])
        for p in sorted(agree, key=lambda p: -wins.get(p, 0))[:8]:
            print(f"  {p[:22]:<24}{sum(agree[p]) / len(agree[p]):>5.0%}  "
                  f"(n={len(agree[p])})")
    score = infer_rule(lock_observations())
    print("\nLOCK RULE CHECK (observations both rules disagree on)")
    for rule in (KICKOFF_MINUS_2H, FIRST_GAME_OF_DAY):
        s = score[rule]
        print(f"  {rule:<18} agrees {s['agree']:>3}  disagrees {s['disagree']:>3}")
    print(f"  edge.pickem_deadline.RULE is currently {RULE!r}.")


def _weeks(spec: str) -> list[int]:
    if spec == "auto":
        w = current_week()
        return [w] if w else []
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--weeks", default="auto", help="'auto', '5', '1-5' or '1,3'")
    ap.add_argument("--report", action="store_true", help="print, do not fetch")
    ap.add_argument("--no-obs", action="store_true",
                    help="do not record lock observations (use for backfills: a "
                         "past week's visibility says nothing about lock timing)")
    args = ap.parse_args()
    if args.report:
        report()
        return
    season = SEASON_START.year
    for week in _weeks(args.weeks):
        observed_at = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        text, html = fetch_week(week)
        games, entries = parse_standings_text(text)
        mine = my_entry_name(html)
        n = upsert_picks(season, week, games, entries, mine, observed_at)
        print(f"week {week}: {len(games)} games x {len(entries)} entries "
              f"({n} picks) -> {PICKS.relative_to(ROOT)}")
        if not args.no_obs:
            record_lock_obs(season, week, games, entries, mine, observed_at)
    report()


if __name__ == "__main__":
    main()
