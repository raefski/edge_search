#!/usr/bin/env python3
"""PreToolUse guard for Claude Code's Bash tool in edge_search.

Reads the hook payload on stdin and DENIES a command that would destroy work
or data this repo cannot get back, or trip a DraftKings block. Everything
else passes silently. It is a seatbelt against accidents, not a sandbox.

Why each rule exists (all of these are irreplaceable or were learned live):

  * data/*.db -- data/odds.db is the accumulated scrape history; scraping
    does not reach backwards, so a deleted store cannot be rebuilt
    (deploy/odds-collect@.service). contests.db is the contest database.
  * git reset --hard / clean -f / checkout . / restore . -- the working tree
    always holds uncommitted data the systemd timers wrote (proj logs,
    caches) and other agents' work in progress.
  * git push --force -- main is pushed by timers every hour; a force push
    discards their commits and the Streamlit Cloud apps deploy from it.
  * recursive rm on the repo, its code/data folders, $HOME or / .
  * scripts/arb_scan.py without --sports -- a full-catalog DK sweep earned a
    residential IP block on 2026-09-18 (DRAFTKINGS_ACCESS.md §2).
  * systemctl --user disable/mask -- silently stops a collector whose history
    cannot be backfilled; ask Adam first.
  * the auto-memory directory.

Commands are split into shell segments and tokenised, so `grep "git reset
--hard" notes.md` or a heredoc that merely MENTIONS a pattern is allowed.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

REPO = os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR")
                        or os.path.join(os.path.dirname(__file__), "..", ".."))
HOME = os.path.realpath(os.path.expanduser("~"))

#: Repo paths a recursive delete must never touch (relative to the repo).
PROTECTED = ("", ".git", ".claude", "edge", "scripts", "pages", "tests", "data",
             "packages", "deploy", "harness")


def _strip_heredocs(cmd: str) -> str:
    """Drop heredoc bodies: their text is input to a program, not commands."""
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        i += 1
        if m:
            end = m.group(2)
            while i < len(lines) and lines[i].strip() != end:
                i += 1
            i += 1
    return "\n".join(out)


def _segments(cmd: str) -> list[list[str]]:
    """Each simple command as a token list (split on ; && || | newlines)."""
    cmd = _strip_heredocs(cmd)
    parts = re.split(r"\|\||&&|;|\||\n|\(|\)", cmd)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            toks = shlex.split(p, posix=True)
        except ValueError:
            toks = p.split()
        # skip env assignments and wrappers: `FOO=1 timeout 60 nice git ...`
        while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0])
                        or toks[0] in ("sudo", "nice", "nohup", "time", "command", "exec")):
            toks = toks[1:]
        if toks and toks[0] == "timeout":
            toks = toks[1:]
            while toks and (toks[0].startswith("-") or re.match(r"^\d+[smhd]?$", toks[0])):
                toks = toks[1:]
        if toks:
            out.append(toks)
    return out


def _resolve(path: str, cwd: str) -> str:
    path = os.path.expandvars(os.path.expanduser(path))
    return os.path.realpath(os.path.join(cwd, path))


def _protected(target: str) -> bool:
    if target in ("/", HOME):
        return True
    for rel in PROTECTED:
        if target == os.path.realpath(os.path.join(REPO, rel)):
            return True
    return False


def _check_rm(toks: list[str], cwd: str) -> str | None:
    flags = [t for t in toks[1:] if t.startswith("-")]
    targets = [t for t in toks[1:] if not t.startswith("-")]
    recursive = any(f in ("-r", "-R", "--recursive") or (not f.startswith("--") and
                    ("r" in f or "R" in f)) for f in flags)
    for t in targets:
        if any(ch in t for ch in "*?") and recursive:
            base = _resolve(t.split("*")[0].split("?")[0] or ".", cwd)
            if _protected(base) or _protected(os.path.dirname(base.rstrip("/")) or "/"):
                return f"recursive rm with a wildcard under {base}"
        real = _resolve(t, cwd)
        if recursive and _protected(real):
            return f"recursive rm of a protected path ({real})"
        if re.search(r"\.db(-wal|-shm)?$", real) and real.startswith(os.path.join(REPO, "data")):
            return f"deleting a database ({os.path.relpath(real, REPO)}) -- its history cannot be re-scraped"
        if "/.claude/projects/" in real and "/memory" in real:
            return "deleting Claude's auto-memory"
    return None


def _check_git(toks: list[str]) -> str | None:
    args = toks[1:]
    while args and args[0] in ("-C", "-c"):
        args = args[2:]
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub == "reset" and "--hard" in rest:
        return "git reset --hard discards uncommitted timer data and other agents' work"
    if sub == "clean" and ("--force" in rest or any(
            r.startswith("-") and not r.startswith("--") and "f" in r for r in rest)):
        return "git clean -f deletes untracked data files (contest exports, proj logs)"
    if sub == "checkout" and "." in rest:
        return "git checkout . discards every uncommitted change in the tree"
    if sub == "restore" and "." in rest and "--staged" not in rest:
        return "git restore . discards every uncommitted change in the tree"
    if sub == "push" and any(r in ("-f", "--force", "--force-with-lease") or r.startswith("--force")
                             or (r.startswith("+") and len(r) > 1) for r in rest):
        return "force push to a branch the timers and Streamlit Cloud deploy from"
    if sub == "branch" and any(r in ("-D", "--delete") for r in rest) and "main" in rest:
        return "deleting main"
    if sub == "filter-branch" or (sub == "stash" and rest[:1] in (["clear"], ["drop"])):
        return f"git {sub} {' '.join(rest[:1])} destroys history or stashed work"
    return None


def _check_sql(toks: list[str]) -> str | None:
    if not toks or os.path.basename(toks[0]) != "sqlite3":
        return None
    text = " ".join(toks[1:]).upper()
    if re.search(r"\bDROP\s+(TABLE|INDEX|VIEW)\b", text) or \
            (re.search(r"\bDELETE\s+FROM\b", text) and " WHERE " not in f" {text} "):
        return "dropping a table / deleting every row of a store"
    return None


def check(cmd: str, cwd: str | None = None) -> str | None:
    """The reason to block `cmd`, or None to allow it."""
    cwd = cwd or os.getcwd()
    raw = _strip_heredocs(cmd)
    # redirection that truncates a database
    m = re.search(r"(^|[^>])>\s*(\S+\.db)\b", raw)
    if m and "data/" in m.group(2):
        return f"truncating {m.group(2)}"
    if re.search(r":\(\)\s*\{\s*:\|:&\s*\};:", raw):
        return "fork bomb"
    if re.search(r"\b(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(ba|z)?sh\b", raw):
        return "piping a download straight into a shell"
    for toks in _segments(cmd):
        exe = os.path.basename(toks[0])
        reason = None
        if exe == "rm":
            reason = _check_rm(toks, cwd)
        elif exe == "git":
            reason = _check_git(toks)
        elif exe in ("mkfs", "fdisk") or exe.startswith("mkfs."):
            reason = "formatting a disk"
        elif exe == "dd" and any(t.startswith("of=/dev/") for t in toks):
            reason = "dd onto a device"
        elif exe == "chmod" and "-R" in toks and any(t in ("777", "a+rwx") for t in toks):
            reason = "recursive chmod 777"
        elif exe == "systemctl" and "--user" in toks and any(t in ("disable", "mask") for t in toks):
            reason = ("disabling a timer stops a collector whose history cannot be "
                      "backfilled -- confirm with Adam first")
        elif exe in ("python", "python3") and any(t.endswith("arb_scan.py") for t in toks) \
                and not any(t.startswith("--sports") for t in toks) \
                and not any(t in ("-h", "--help") for t in toks):
            reason = ("scripts/arb_scan.py without --sports sweeps DK's whole catalog and "
                      "earned an IP block on 2026-09-18 (DRAFTKINGS_ACCESS.md)")
        else:
            reason = _check_sql(toks)
        if reason:
            return reason
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    reason = check(cmd, payload.get("cwd"))
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": f"Blocked by .claude/hooks/guard_bash.py: {reason}. "
                                        "If this is really intended, ask Adam to run it."}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
