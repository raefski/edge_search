"""The DFS critique loop, unattended: one /dfs-critique cycle a night, gated, merged late.

    python3 -m harness critique-loop                 # tonight's cycle (the timer runs this)
    python3 -m harness critique-loop --sport nhl     # a given sport, rotation untouched
    python3 -m harness critique-loop --dry-run       # say what would run, run nothing
    python3 -m harness critique-loop --status        # rotation, pauses, pending candidates
    python3 -m harness critique-loop --drop nba      # veto a pending candidate (Adam)
    python3 -m harness critique-loop --merge nba     # approve one now (a "review" verdict waits for this)
    python3 -m harness critique-loop --resume nba    # lift a pause early

Why it is built this way (asked for 2026-11-07, after 24 hand-run NBA cycles):

1. The cycle itself is the skill Adam runs by hand (.claude/skills/dfs-critique), run by
   `claude -p "/dfs-critique <sport> unattended"` in its own git worktree on a branch
   from origin/main. The worktree gets main's untracked data/ (19 GB): small entries
   copied, big read-mostly stores (odds.db, contests.db, ground truth) symlinked, so a
   candidate can't rewrite main's lab caches. The agent commits to its branch; it never
   pushes.
2. The agent's own "improved" never ships anything. harness/critique_gate.py measures a
   fresh worktree of main and the candidate with the same fixed lab command, paired by
   row with a night-clustered bootstrap. Only a change whose primary held-out states are
   ALL better beyond noise (and whose guard period, e.g. NBA 2026-27, is not worse)
   becomes a pending candidate.
3. NBA's 2025-26 has been the test season for 24 cycles; picking winners on it over and
   over slowly fits it. So a passing candidate waits WAIT_DAYS and is re-gated on the
   nights played meanwhile (they land in NBA's guard and NHL's test split) before it
   merges. Adam gets a push at both points and can veto with --drop.
4. The critique log always lands, shipped or not: a not-shipped idea the next critic
   can't see gets re-proposed (the log's "not shipped" entries are the loop's memory).
5. A sport pauses for PAUSE_DAYS after PAUSE_AFTER cycles in a row with nothing passing
   the gate: no new data, no new holes, and each cycle costs a night of model time.
6. Nothing runs or merges with a Classic slate locking inside LOCK_CLEAR_H hours: a
   merge changes projections, and Adam builds from the phone before lock.

The loop never edits the critic prompt or the skill: a loop that rewrites its own
judge can quietly start grading itself easier. That stays a person's change.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "harness" / "critique_loop.json"
RUNS = ROOT / "data" / "harness" / "critique"
WT_DIR = ROOT / ".claude" / "worktrees"
GIT_LOCK = ROOT / ".git" / "edge_git.lock"

ROTATION = ("nba", "ncaab", "nhl")          # sports with a gate (critique_gate.SPECS)
LOGS = {"nba": "NBA_MINUTES_CRITIQUE_LOG.md", "ncaab": "NCAAB_CRITIQUE_LOG.md", "nhl": "NHL_CRITIQUE_LOG.md"}
MIRRORED = {"nhl", "ncaaf", "nascar", "mma"}  # CLAUDE.md: regenerate the mirror after a code change
PAUSE_AFTER = 3
PAUSE_DAYS = 21
WAIT_DAYS = 7
LOCK_CLEAR_H = 6.0
AGENT_TIMEOUT = 5 * 3600
#: untracked data/ entries above this are symlinked (read-mostly stores); the rest are
#: copied. Lab caches are copied whatever their size: a candidate rebuilds them.
LINK_ABOVE = 100 * 2 ** 20
#: The injury-report archive gains a PDF every 15 min and the NBA lab's 2026-27 rows read
#: it: symlinked, main measured against ITSELF 40 min apart differed on 2026-27 (6.0902 vs
#: 6.0616, 2026-11-07). Copied, each worktree holds a frozen snapshot.
ALWAYS_COPY = ("data/cache/nba_minutes_lab/", "data/nhl_cache/", "data/nba_injury_reports/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[-500:]}")
    return r.stdout.strip()


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"next": 0, "sports": {}}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, default=str))


def _sport(st: dict, s: str) -> dict:
    return st["sports"].setdefault(s, {"streak": 0, "paused_until": None, "pending": None, "history": []})


def notify(title: str, body: str, priority: str = "default") -> None:
    from harness import monitor
    monitor.push({"kind": "critique", "title": title, "body": body[:3800], "priority": priority,
                  "tags": "robot"})


def claude_bin() -> str:
    """`claude` on PATH, else the newest VS Code extension's bundled binary (the only one
    on the desktop, 2026-11-07; its path carries the version, so it is looked up each run)."""
    if os.environ.get("EDGE_CLAUDE"):
        return os.environ["EDGE_CLAUDE"]
    found = shutil.which("claude")
    if found:
        return found

    def ver(p: Path) -> tuple:
        m = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", str(p))
        return tuple(int(x) for x in m.groups()) if m else (0,)
    exts = sorted(Path.home().glob(".vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"),
                  key=ver)
    if not exts:
        raise RuntimeError("no claude binary: set EDGE_CLAUDE")
    return str(exts[-1])


def locks_soon(hours: float = LOCK_CLEAR_H) -> list[dict]:
    from harness import status
    try:
        return status.slates(hours)
    except Exception:            # noqa: BLE001 -- no listing must not block the night
        return []


# ── worktrees ───────────────────────────────────────────────────────────────

def _size(p: Path) -> int:
    if p.is_symlink() or p.is_file():
        return p.lstat().st_size
    return sum(f.lstat().st_size for f in p.rglob("*") if f.is_file() or f.is_symlink())


def link_data(wt: Path, refresh: bool = False) -> None:
    """Give a worktree main's untracked data/ (and .env): copy small entries and lab
    caches, symlink big read-mostly stores. `refresh` first removes what an earlier call
    put there, so a candidate made hours ago is measured on the same snapshot as a fresh
    worktree of main (critique_loop.judge)."""
    entries = git("ls-files", "--others", "--directory", "--", "data/").splitlines()
    for e in entries + [".env"]:
        if e.endswith(("-wal", "-shm", "-journal")):
            continue        # SQLite's side files belong beside the (symlinked) database only
        src, dst = ROOT / e.rstrip("/"), wt / e.rstrip("/")
        if refresh and (dst.is_symlink() or dst.is_file()):
            dst.unlink()
        elif refresh and dst.is_dir():
            shutil.rmtree(dst)
        if not src.exists() or dst.exists() or dst.is_symlink():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if e not in ALWAYS_COPY and _size(src) > LINK_ABOVE:
            dst.symlink_to(src)
        elif src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)


def worktree(name: str, branch: str | None) -> Path:
    git("fetch", "-q", "origin", "main")
    wt = WT_DIR / name
    if branch:
        git("worktree", "add", "-q", "-b", branch, str(wt), "origin/main")
    else:
        git("worktree", "add", "-q", "--detach", str(wt), "origin/main")
    link_data(wt)
    return wt


def drop_worktree(wt: Path, branch: str | None = None) -> None:
    git("worktree", "remove", "--force", str(wt), check=False)
    if branch:
        git("branch", "-D", branch, check=False)


def changed(wt: Path) -> list[str]:
    return git("diff", "--name-only", "origin/main...HEAD", cwd=wt).splitlines()


def classify(paths: list[str], sport: str, cand: Path, base: Path) -> str:
    """"protected": touches the loop's own judge (the skill, the critic prompts, hooks,
    this file, the gate) -- never merged without Adam; "record": logs, docs, labs and
    tests only -- verified, not gated; "model": anything else -- gated."""
    from harness import critique_gate
    if any(p.startswith((".claude/", "harness/critique_")) for p in paths):
        return "protected"
    if critique_gate.judge_changed(sport, base, cand):
        return "protected"
    if all(p.startswith(("scripts/", "tests/")) or p.endswith(".md") for p in paths):
        return "record"
    return "model"


def push_main(wt: Path) -> None:
    """Rebase the worktree's commits onto origin/main and push them, under the repo lock.
    A failed rebase is aborted and re-raised."""
    with open(GIT_LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        git("fetch", "-q", "origin", "main", cwd=wt)
        try:
            git("rebase", "-q", "origin/main", cwd=wt)
        except RuntimeError:
            git("rebase", "--abort", cwd=wt, check=False)
            raise
        git("push", "-q", "origin", "HEAD:main", cwd=wt)


def verify(wt: Path, paths: list[str]) -> tuple[bool, str]:
    """harness verify on the changed paths + the DFS suite (CLAUDE.md's table)."""
    py = sys.executable
    for cmd in ([py, "-m", "harness", "verify", *paths],
                [py, "-m", "pytest", "-q", "-x", "tests/test_page_cache_isolation.py", "tests/test_streamlit_api.py",
                 *sorted(str(p.relative_to(wt)) for p in (wt / "tests").glob("test_dfs*.py"))]):
        r = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            return False, (r.stdout + r.stderr)[-1500:]
    return True, "verify + DFS suite passed"


def note_on_branch(sport: str, cand: Path, note: str) -> None:
    """Append the gate's verdict under the branch's own log entry (it travels with it)."""
    log = cand / LOGS[sport]
    if not log.exists():
        return
    log.write_text(log.read_text().rstrip() + "\n\n" + note.strip() + "\n")
    git("add", LOGS[sport], cwd=cand)
    git("commit", "-q", "-m", f"{sport} critique log: unattended gate verdict\n\n"
        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>", cwd=cand)


def land_log(sport: str, cand: Path, note: str) -> None:
    """A branch that will not merge still leaves its log entry on main -- the log is how
    the next critic knows an idea was tried. Only what the branch APPENDED to the log is
    carried (main's log may have newer entries by then), plus the gate's verdict."""
    name = LOGS[sport]
    mb = git("merge-base", "origin/main", "HEAD", cwd=cand)
    old = git("show", f"{mb}:{name}", cwd=cand, check=False)
    new = (cand / name).read_text() if (cand / name).exists() else old
    added = new[len(old.rstrip()):] if new.startswith(old.rstrip()) else \
        f"\n\n(unattended cycle: the branch edited this log other than by appending; not carried)"
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    base = worktree(f"critique-{sport}-{stamp}-log", None)
    try:
        cur = (base / name).read_text() if (base / name).exists() else ""
        (base / name).write_text(cur.rstrip() + "\n" + added.rstrip() + "\n\n" + note.strip() + "\n")
        git("add", name, cwd=base)
        git("commit", "-q", "-m", f"{sport} critique log (unattended): {note.splitlines()[0][:160]}\n\n"
            "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>", cwd=base)
        push_main(base)
    finally:
        drop_worktree(base)


# ── one cycle ───────────────────────────────────────────────────────────────

def run_agent(sport: str, wt: Path, stamp: str) -> dict:
    """The skill, headless. Permissions are bypassed (nobody is there to approve); the
    project's PreToolUse guard (.claude/hooks/guard_bash.py) and Stop-hook verify still
    run, the agent works in its own worktree, and nothing it does reaches main except
    through the gate. Returns the agent's critique_result.json (its claim, used for the
    push text only)."""
    RUNS.mkdir(parents=True, exist_ok=True)
    cmd = [claude_bin(), "-p", f"/dfs-critique {sport} unattended", "--permission-mode", "bypassPermissions",
           "--output-format", "json"]
    if os.environ.get("EDGE_CRITIQUE_BUDGET"):
        cmd += ["--max-budget-usd", os.environ["EDGE_CRITIQUE_BUDGET"]]
    try:
        r = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=AGENT_TIMEOUT)
        out, rc = r.stdout, r.returncode
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        rc = "timeout"
    (RUNS / f"{stamp}-{sport}.json").write_text(out or "")
    try:
        claim = json.loads((wt / "critique_result.json").read_text())
    except (OSError, ValueError):
        claim = {}
    return {"rc": rc, "claim": claim}


def judge(sport: str, cand: Path, paths: list[str]) -> tuple[str, dict]:
    """(verdict, gate) for a candidate: pass / fail / review / record."""
    from harness import critique_gate
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    base = worktree(f"critique-{sport}-{stamp}-main", None)
    link_data(cand, refresh=True)           # both measured on one data snapshot
    try:
        kind = classify(paths, sport, cand, base)
        gate = {"table": "", "reasons": []}
        if kind == "model":
            gate = critique_gate.run(sport, cand, base)
        verdict = {"protected": "review", "record": "record"}.get(kind, gate["verdict"])
        if kind == "protected":
            gate["reasons"].append("touches the loop's own judge (skill / critic / hooks / gate): Adam decides")
    finally:
        drop_worktree(base)
    if verdict in ("pass", "record"):
        ok, msg = verify(cand, paths)
        if not ok:
            verdict, gate["reasons"] = "fail", gate["reasons"] + [msg]
    return verdict, gate


def _note(stamp: str, verdict: str, gate: dict, extra: str = "") -> str:
    return (f"Unattended gate ({stamp}): **{verdict}**{extra}\n\n{gate['table']}"
            + "".join(f"\n- {x.strip()[:400]}" for x in gate["reasons"][:6]))


def cycle(sport: str, st: dict) -> str:
    sp = _sport(st, sport)
    stamp = _now().strftime("%Y%m%d-%H%M")
    branch = f"critique/{sport}-{stamp}"
    cand = worktree(f"critique-{sport}-{stamp}", branch)
    res = run_agent(sport, cand, stamp)
    claim = res["claim"]
    head = claim.get("hole") or "(no critique_result.json)"
    paths = changed(cand)
    if not paths:
        drop_worktree(cand, branch)
        sp["history"].append({"at": stamp, "outcome": "no commits", "rc": res["rc"]})
        sp["streak"] += 1
        notify(f"{sport} critique: no result", f"the agent committed nothing (rc {res['rc']}). "
               f"Run log: data/harness/critique/{stamp}-{sport}.json", "high")
        return "no commits"
    verdict, gate = judge(sport, cand, paths)
    if verdict in ("pass", "review"):
        extra = (f" -- re-gated after {WAIT_DAYS} forward days, then merged unless vetoed" if verdict == "pass"
                 else " -- waiting for Adam: harness critique-loop --merge / --drop")
        note_on_branch(sport, cand, _note(stamp, verdict, gate, extra))
        sp["pending"] = {"branch": branch, "wt": str(cand), "created": stamp, "verdict": verdict,
                         "hole": head, "table": gate["table"]}
    elif verdict == "record":
        note_on_branch(sport, cand, _note(stamp, "not shipped (log / harness only, verified)", gate))
        try:
            push_main(cand)
        except RuntimeError as e:
            land_log(sport, cand, _note(stamp, "record branch did not rebase", {"table": "", "reasons": [str(e)]}))
        drop_worktree(cand, branch)
    else:
        land_log(sport, cand, _note(stamp, verdict, gate))
        drop_worktree(cand, branch)
    entry = {"at": stamp, "outcome": verdict, "hole": head, "paths": paths}
    sp["history"].append(entry)
    sp["streak"] = 0 if verdict in ("pass", "review") else sp["streak"] + 1
    if sp["streak"] >= PAUSE_AFTER:
        sp["paused_until"] = (_now() + timedelta(days=PAUSE_DAYS)).isoformat()
    body = (f"{head}\n\n{claim.get('summary', '')}\n\ngate: {verdict}\n{gate['table']}"
            + "".join(f"\n- {x.strip()[:300]}" for x in gate["reasons"][:4])
            + (f"\n\nmerging in {WAIT_DAYS} days after a re-gate; veto: harness critique-loop --drop {sport}"
               if verdict == "pass" else "")
            + (f"\n\napprove: harness critique-loop --merge {sport}; refuse: --drop {sport}"
               if verdict == "review" else "")
            + (f"\n\n{sport} paused {PAUSE_DAYS} days ({sp['streak']} cycles without a pass)"
               if sp["paused_until"] else ""))
    notify(f"{sport} critique: {verdict}", body, "high" if verdict in ("pass", "review") else "low")
    return verdict


def ship(sport: str, sp: dict, cand: Path, why: str) -> None:
    push_main(cand)
    if sport in MIRRORED:
        subprocess.run([sys.executable, "-m", "harness", "run", f"mirror-{sport}"], cwd=cand,
                       capture_output=True, timeout=1800)
    notify(f"{sport} critique: SHIPPED", f"{sp['pending']['hole']}\n\n{why}\n\n"
           "Reboot the Streamlit app to pick it up.", "high")


def regate(sport: str, st: dict) -> str | None:
    """A pending pass older than WAIT_DAYS: rebase, gate again on the nights since, merge."""
    sp = _sport(st, sport)
    p = sp.get("pending")
    if not p or p["verdict"] != "pass":
        return None
    created = datetime.strptime(p["created"], "%Y%m%d-%H%M").replace(tzinfo=timezone.utc)
    if _now() - created < timedelta(days=WAIT_DAYS):
        return None
    cand = Path(p["wt"])
    stamp = _now().strftime("%Y%m%d-%H%M")
    try:
        git("fetch", "-q", "origin", "main", cwd=cand)
        git("rebase", "-q", "origin/main", cwd=cand)
    except RuntimeError as e:
        git("rebase", "--abort", cwd=cand, check=False)
        verdict, gate = "fail", {"table": "", "reasons": [f"rebase onto main failed: {e}"]}
    else:
        verdict, gate = judge(sport, cand, changed(cand))
    if verdict == "pass":
        if locks_soon():
            return "waiting: a slate locks soon"
        note_on_branch(sport, cand, _note(stamp, "re-gate pass, merged", gate))
        try:
            ship(sport, sp, cand, f"re-gate after {WAIT_DAYS} days:\n{gate['table']}")
        except RuntimeError as e:
            verdict, gate = "fail", {"table": "", "reasons": [f"push failed: {e}"]}
    if verdict != "pass":
        land_log(sport, cand, _note(stamp, f"dropped at re-gate ({verdict})", gate))
        notify(f"{sport} critique: dropped at re-gate", f"{p['hole']}\n\n{gate['table']}"
               + "".join(f"\n- {x.strip()[:300]}" for x in gate["reasons"][:4]))
    drop_worktree(cand, p["branch"])
    sp["pending"] = None
    sp["history"].append({"at": stamp, "outcome": f"regate {verdict}", "hole": p["hole"]})
    return verdict


def pick(st: dict, now: datetime) -> str | None:
    for k in range(len(ROTATION)):
        s = ROTATION[(st["next"] + k) % len(ROTATION)]
        sp = _sport(st, s)
        paused = sp["paused_until"] and datetime.fromisoformat(sp["paused_until"]) > now
        if not paused and not sp["pending"]:
            st["next"] = (st["next"] + k + 1) % len(ROTATION)
            return s
    return None


def status(st: dict) -> str:
    out = [f"rotation {', '.join(ROTATION)}; next: {ROTATION[st['next'] % len(ROTATION)]}"]
    for s in ROTATION:
        sp = _sport(st, s)
        last = sp["history"][-1] if sp["history"] else {}
        out.append(f"  {s:<6} streak {sp['streak']}  paused until {sp['paused_until'] or '-'}  "
                   f"pending {(sp['pending'] or {}).get('branch', '-')}  last {last.get('at', '-')} "
                   f"{last.get('outcome', '')}")
    return "\n".join(out)


def main(sport: str | None = None, dry_run: bool = False, show: bool = False,
         drop: str | None = None, resume: str | None = None, merge: str | None = None) -> int:
    RUNS.mkdir(parents=True, exist_ok=True)
    with open(RUNS / ".lock", "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("another critique-loop run holds the lock")
            return 1
        st = load_state()
        if show:
            print(status(st))
            return 0
        if merge:
            sp = _sport(st, merge)
            p = sp["pending"]
            if not p:
                print(f"{merge}: nothing pending")
                return 1
            cand = Path(p["wt"])
            ok, msg = verify(cand, changed(cand))
            if not ok:
                print(msg)
                return 1
            ship(merge, sp, cand, "merged by Adam (harness critique-loop --merge)")
            drop_worktree(cand, p["branch"])
            sp["history"].append({"at": _now().strftime("%Y%m%d-%H%M"), "outcome": "merged by Adam",
                                  "hole": p["hole"]})
            sp["pending"] = None
            save_state(st)
            print(status(st))
            return 0
        if drop or resume:
            sp = _sport(st, drop or resume)
            if drop and sp["pending"]:
                cand = Path(sp["pending"]["wt"])
                land_log(drop, cand, f"Vetoed by Adam ({_now():%Y-%m-%d}): not merged.")
                drop_worktree(cand, sp["pending"]["branch"])
                sp["history"].append({"at": _now().strftime("%Y%m%d-%H%M"), "outcome": "vetoed",
                                      "hole": sp["pending"]["hole"]})
                sp["pending"] = None
            if resume:
                sp["paused_until"], sp["streak"] = None, 0
            save_state(st)
            print(status(st))
            return 0
        soon = locks_soon()
        if soon:
            print(f"skipped: {soon[0]['sport']} {soon[0]['label']} locks {soon[0]['lock']:%H:%M} UTC")
            if not dry_run:
                return 0
        for s in ROTATION:
            if not dry_run:
                r = regate(s, st)
                if r:
                    print(f"{s}: re-gate {r}")
                    save_state(st)
        s = sport or pick(st, _now())
        if not s:
            print("nothing to run: every sport paused or pending")
            save_state(st)
            return 0
        if dry_run:
            print(f"would run /dfs-critique {s} unattended with {claude_bin()}\n{status(st)}")
            return 0
        save_state(st)
        print(f"{s}: {cycle(s, st)}")
        save_state(st)
    return 0
