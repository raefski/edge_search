"""Catalog of everything in edge_search, by the role it plays.

The harness does not move code. edge/ (library), scripts/ (CLIs), pages/ +
app.py (the phone apps) and deploy/ (systemd timers) stay where the deployed
apps, the mirrors and the timers expect them. This file maps them onto the
roles an orchestration layer needs:

    ingest    -> prices, salaries, lines, transcripts, contest exports in
    model     -> projections, ownership, simulators (edge/*_sim, *_theory)
    optimize  -> lineup builders, leverage, late swap
    evaluate  -> calibration, backtests, grading, model accuracy
    publish   -> snapshots/mirrors pushed for Streamlit Cloud
    monitor   -> line/goalie watch, overlays, health
    app       -> the Streamlit pages
    research  -> one-off labs and studies (run by hand)

JOBS are the runnable entries (`python3 -m harness run <name> [-- args]`):
each one is an existing script with the arguments the timers or the docs use.
Everything else is classified by its file name (classify()), and
tests/test_harness.py fails if a new script lands that no rule covers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = "python3"

ROLES = ("ingest", "model", "optimize", "evaluate", "publish", "monitor", "app", "research")
SPORTS = ("mlb", "nfl", "ncaaf", "nhl", "nba", "ncaab", "nascar", "mma", "pickem", "arb", "parlay")


@dataclass(frozen=True)
class Job:
    name: str
    role: str
    argv: tuple
    sport: str | None = None
    timer: str | None = None          # the systemd user timer that runs it, if any
    timeout: int = 1800
    doc: str = ""
    tests: tuple = field(default_factory=tuple)


def _j(name, role, script, *args, **kw) -> Job:
    return Job(name, role, (PY, script, *args), **kw)


JOBS: dict[str, Job] = {j.name: j for j in [
    # ── ingest ────────────────────────────────────────────────────────────
    *[_j(f"odds-{p}", "ingest", "scripts/odds_collect.py", "--profile", p, "--verbose",
         sport=p.split("_")[-1], timeout=900,
         timer=(f"odds-collect-{p.replace('_', '-')}.timer"
                if (ROOT / "deploy" / f"odds-collect-{p.replace('_', '-')}.timer").exists() else None),
         doc=f"scrape the {p} profile into data/odds.db (no push)")
      for p in ("dfs_mlb", "dfs_nfl", "dfs_nhl", "dfs_ncaaf", "dfs_nba", "dfs_ncaab", "pickem_nfl")],
    _j("odds-status", "ingest", "scripts/odds_collect.py", "--status", timeout=120,
       doc="what the odds store holds, per profile"),
    _j("import-contests", "ingest", "scripts/dfs_import_contests.py",
       doc="file DK contest exports from Windows Downloads, tag them from DK, grade"),
    _j("entry-history", "evaluate", "scripts/dfs_entry_history.py",
       doc="real-money ROI from DK entry-history exports"),
    _j("buzz-nhl", "ingest", "scripts/buzz_nhl.py", sport="nhl", timer="buzz-nhl-publish.timer",
       doc="tonight's NHL DFS YouTube mentions"),
    _j("buzz-nfl", "ingest", "scripts/buzz_nfl.py", sport="nfl", timer="buzz-publish.timer"),
    _j("buzz-ncaaf", "ingest", "scripts/buzz_ncaaf.py", sport="ncaaf"),
    _j("tout-picks", "ingest", "scripts/tout_picks.py", sport="pickem",
       doc="extract and grade tout picks from transcripts"),
    _j("pickem-tick", "ingest", "scripts/pickem_deadline_tick.py", sport="pickem",
       timer="pickem-deadline-tick.timer", doc="capture pick'em lines on the deadline schedule"),
    # ── optimize ──────────────────────────────────────────────────────────
    _j("autolog", "optimize", "scripts/dfs_autolog.py", timer="dfs-autolog.timer", timeout=3000,
       doc="build + log every DFS slate 10-15 min before lock (all six sports)"),
    _j("autolog-plan", "optimize", "scripts/dfs_autolog.py", "--dry-run", timeout=300,
       doc="which slates the autolog would build now"),
    _j("lineups-nhl", "optimize", "scripts/dfs_lineups_nhl.py", sport="nhl"),
    _j("lineups-nba", "optimize", "scripts/dfs_lineups_nba.py", sport="nba",
       tests=("tests/test_dfs_nba.py",)),
    _j("lineups-ncaab", "optimize", "scripts/dfs_lineups_ncaab.py", sport="ncaab",
       tests=("tests/test_dfs_ncaab.py",)),
    _j("lineups-nfl", "optimize", "scripts/dfs_lineups_nfl.py", sport="nfl"),
    _j("lineups-ncaaf", "optimize", "scripts/dfs_lineups_ncaaf.py", sport="ncaaf"),
    _j("lineups-nascar", "optimize", "scripts/dfs_lineups_nascar.py", sport="nascar"),
    _j("lineups-mma", "optimize", "scripts/dfs_lineups_mma.py", sport="mma"),
    _j("lineups-mlb", "optimize", "scripts/dfs_lineups.py", sport="mlb"),
    _j("swap-mlb", "optimize", "scripts/dfs_swap.py", sport="mlb",
       doc="MLB late swap: replace a projected hitter ruled out of the order"),
    Job("late-swap", "optimize", (PY, "-m", "harness", "late-swap"), timeout=900,
        doc="multi-sport late swap (NHL goalies first; MLB delegates to swap-mlb)"),
    _j("parlay", "optimize", "scripts/parlay_build.py", sport="parlay"),
    # ── evaluate ──────────────────────────────────────────────────────────
    _j("calibrate-mlb", "evaluate", "scripts/dfs_calibration.py", sport="mlb"),
    _j("calibrate-nfl", "evaluate", "scripts/dfs_calibration_nfl.py", sport="nfl"),
    _j("calibrate-nhl", "evaluate", "scripts/nhl_calibration.py", sport="nhl",
       doc="needs a contest export path: harness run calibrate-nhl -- data/contest-standings-N.csv"),
    _j("calibrate-ncaaf", "evaluate", "scripts/ncaaf_calibration.py", sport="ncaaf"),
    _j("calibrate-nba", "evaluate", "scripts/nba_calibration.py", sport="nba",
       doc="NBA/college build vs a contest export, by draft group: "
           "harness run calibrate-nba -- data/contest-standings-N.csv [--fit-ownership]"),
    _j("nba-onoff-backtest", "evaluate", "scripts/nba_onoff_backtest.py", sport="nba",
       doc="held-out test of teammate-out splits on the NBA model (2026-10-16: no gain, not shipped)"),
    _j("hoops-verify", "ingest", "scripts/hoops_dk_discover.py", timeout=300,
       doc="read off DK what the NBA/CBB build assumed (Classic id, roster, FPPG id, prop tabs)"),
    _j("calibrate-nascar", "evaluate", "scripts/nascar_calibration.py", sport="nascar"),
    _j("calibrate-mma", "evaluate", "scripts/mma_calibration.py", "--grade", sport="mma"),
    _j("contest-db", "evaluate", "scripts/contest_db.py",
       doc="(re)build data/contests.db from every contest export + projection log"),
    _j("gpp-meta", "evaluate", "scripts/gpp_meta.py", doc="what finishes in the money: chalk or contrarian"),
    _j("model-accuracy", "evaluate", "scripts/model_accuracy.py",
       doc="MAE / bias / rank corr of points and ownership, per sport and contest type"),
    _j("buzz-trust", "evaluate", "scripts/buzz_trust.py", doc="which buzz/tout sources carry signal"),
    _j("pickem-grade", "evaluate", "scripts/pickem_grade.py", sport="pickem"),
    # ── publish ───────────────────────────────────────────────────────────
    _j("draftables-publish", "publish", "scripts/draftables_publish.py", "--push",
       timer="draftables-publish.timer"),
    _j("nhl-lines-publish", "publish", "scripts/nhl_lines_publish.py", "--push", sport="nhl",
       timer="nhl-lines-publish.timer"),
    *[_j(f"mirror-{a}", "publish", "scripts/mirror_app.py", "--app", a, "--push", sport=a,
         doc=f"regenerate + push the standalone {a} app repo (never edit the mirror)")
      for a in ("nhl", "ncaaf", "nascar", "mma")],
    # ── monitor ───────────────────────────────────────────────────────────
    Job("monitor", "monitor", (PY, "-m", "harness", "monitor"), timeout=600, timer="edge-monitor.timer",
        doc="goalie changes + line moves near lock; --push sends ntfy alerts"),
    _j("overlay", "monitor", "scripts/dfs_overlay.py", doc="guaranteed contests that are not filling (table)"),
    Job("overlay-alert", "monitor", (PY, "-m", "harness", "overlay"), timeout=900,
        timer="edge-overlay.timer",
        doc="push guaranteed contests whose next entry is worth it at lock (ntfy)"),
    Job("code-sync", "publish", (PY, "-m", "harness.code_sync"), timeout=120,
        timer="edge-code-sync.timer",
        doc="fast-forward this checkout to origin/main when it has no local commits"),
    Job("data-pull", "ingest", (PY, "-m", "harness", "data-pull"), timeout=300,
        doc="copy the data branch's published files into a fresh clone (never overwrites)"),
    Job("status", "monitor", (PY, "-m", "harness", "status"), timeout=300,
        doc="slates, timers, data freshness, failures"),
]}


#: File-name rules for everything that is not a JOB. First match wins.
RULES: list[tuple[str, str]] = [
    (r"(^|_)(collect|ingest|fetch|capture|scaffold|import|bootstrap|from_cookie|discover|"
     r"categories|tick|history_collect|odds_capture)", "ingest"),
    (r"^buzz_(?!fit)|^tout_|^pickem_pool|^pickem_session|^dfs_entry_history", "ingest"),
    (r"(publish|mirror_app)", "publish"),
    (r"(calibration|backtest|_fit\b|_fit_|fit$|grade|_eval|validate|replay|measure|"
     r"compare|parity|sweep|accuracy|trust|meta|contest_db|contest_stats|transferability|plot|"
     r"clv_|cash_field)", "evaluate"),
    (r"(lineups|_build$|^dfs_build|swap|portfolio|^parlay|^dfs_board|^arb_|rebuild)", "optimize"),
    (r"(overlay|agent|blocked|live|alert|monitor|watchlist|scan)", "monitor"),
    (r"(lab|idea|discovery|endorsement|scout|correlation|pilot|rounds|season_sim|"
     r"situational|threes|variance|sigma|td_|skill|nfl_matchup|dupe|slate_size|stack_shape|"
     r"^mans_)",
     "research"),
]

EDGE_RULES: list[tuple[str, str]] = [
    (r"^(git_lock|data_branch)$", "publish"),
    (r"^dfs_entry_edge$", "model"),
    (r"^(nba_minutes|nba_project)$", "model"),
    (r"^(dfs_opt|dfs_leverage|dfs_swap|dfs_roster|dfs_lineup|sgp|parlay)", "optimize"),
    (r"(_sim$|_theory$|_field$|_onoff$|dfs_project|dfs_ladder|pickem_strategy|pickem_features|"
     r"pickem$|fairodds|oddsmath|dfs_contest|dfs_nfl_contest|dfs_validate|clv)", "model"),
    (r"^pagereload$", "app"),
    (r"^(dfs_run|dfs_sport|repo_files|names|scanner)", "optimize"),
    (r"^(client|dfs$|nfl$|nba$|ncaab$|nhl$|mma$|nascar$|ncaaf$|mma_market|buzz|pickem_cbs|pickem_data|"
     r"pickem_free|pickem_live|pickem_log|pickem_deadline|pickem_week|nba_lineups)", "ingest"),
]


def _sport(stem: str) -> str | None:
    for s in SPORTS:
        if re.search(rf"(^|_){s}(_|$)", stem):
            return s
    if stem.startswith("cfb") or "ncaaf" in stem:
        return "ncaaf"
    if stem.startswith("dfs_") and not any(s in stem for s in SPORTS):
        return "mlb"            # the first DFS sport; un-suffixed dfs_* files are MLB's
    return None


def classify(path: str) -> str | None:
    p = Path(path)
    stem = p.stem
    if p.parts[0] == "pages" or p.name == "app.py":
        return "app"
    rules = EDGE_RULES if p.parts[0] == "edge" else RULES
    for pat, role in rules:
        if re.search(pat, stem):
            return role
    return None


def catalog() -> list[dict]:
    """Every script, edge module and page with its role and sport."""
    by_script = {j.argv[1]: j for j in JOBS.values() if len(j.argv) > 1 and j.argv[1].endswith(".py")}
    rows = []
    files = sorted([*ROOT.glob("scripts/*.py"), *ROOT.glob("edge/*.py"), *ROOT.glob("pages/*.py"),
                    ROOT / "app.py"])
    for f in files:
        rel = str(f.relative_to(ROOT))
        if f.name == "__init__.py":
            continue
        job = by_script.get(rel)
        rows.append({"path": rel, "role": job.role if job else classify(rel),
                     "sport": (job.sport if job and job.sport else _sport(f.stem)),
                     "job": job.name if job else None})
    return rows
