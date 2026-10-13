"""Targeted verification: the tests that cover what changed, nothing else.

    python3 -m harness verify              # files changed vs HEAD (+ untracked)
    python3 -m harness verify edge/dfs_opt_nhl.py pages/8_🏒_NHL_DFS.py
    python3 -m harness verify --all        # the whole suite

Changed .py files are byte-compiled first (a syntax error in a page would
otherwise reach the phone). Tests are chosen by what they import: a test that
names `edge.dfs_leverage` / `from edge import ... dfs_leverage` covers
edge/dfs_leverage.py; a script is covered by tests that mention its file; the
Streamlit pages by the cache-isolation and Streamlit-API tests.

As a Claude Code Stop hook (`--hook`, .claude/settings.json) it runs once per
distinct set of changes: a passing fingerprint is remembered in
data/harness/verify_ok, so an unchanged tree costs nothing on the next stop,
and a failure blocks the stop with the failing tests as the reason.
"""
from __future__ import annotations

import hashlib
import json
import py_compile
import re
import subprocess
import sys
from pathlib import Path

from harness.registry import ROOT
from harness.runner import STATE

TESTS = ROOT / "tests"
STAMP = STATE / "verify_ok"
PAGE_TESTS = ("tests/test_page_cache_isolation.py", "tests/test_streamlit_api.py")
BUDGET_S = 420


def changed_files() -> list[str]:
    def git(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.split("\n")
    names = set(git("diff", "--name-only", "HEAD")) | set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(n for n in names if n.endswith(".py") and (ROOT / n).exists())


def _test_sources() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): p.read_text(errors="ignore") for p in TESTS.glob("test_*.py")}


def tests_for(paths: list[str]) -> list[str]:
    srcs = _test_sources()
    out: set[str] = set()
    for path in paths:
        p = Path(path)
        if p.parts[0] == "tests" and p.name.startswith("test_"):
            out.add(path)
        elif p.parts[0] == "edge":
            mod = ".".join(p.with_suffix("").parts)          # edge.odds.store
            leaf = p.stem
            parent = ".".join(p.parent.parts)                # edge / edge.odds
            pat = re.compile(rf"\b{re.escape(mod)}\b|from {re.escape(parent)} import [^\n]*\b{leaf}\b"
                             rf"|from {re.escape(parent)} import \([^)]*\b{leaf}\b")
            out.update(t for t, s in srcs.items() if pat.search(s))
        elif p.parts[0] == "scripts":
            pat = re.compile(rf"\b{re.escape(p.stem)}\b")
            out.update(t for t, s in srcs.items() if pat.search(s))
        elif p.parts[0] == "pages" or p.name == "app.py":
            out.update(PAGE_TESTS)
        elif p.parts[0] == "harness":
            out.update(t for t in srcs if "harness" in t)
        elif ".claude" in p.parts:
            out.update(t for t in srcs if "harness" in t)
    return sorted(t for t in out if (ROOT / t).exists())


def compile_errors(paths: list[str]) -> list[str]:
    errs = []
    for path in paths:
        try:
            py_compile.compile(str(ROOT / path), doraise=True)
        except py_compile.PyCompileError as exc:
            errs.append(f"{path}: {exc.msg.strip().splitlines()[-1]}")
    return errs


def fingerprint(paths: list[str]) -> str:
    h = hashlib.sha1()
    for path in sorted(paths):
        h.update(path.encode())
        h.update((ROOT / path).read_bytes())
    return h.hexdigest()


def run(paths: list[str] | None = None, all_tests: bool = False, quiet: bool = False,
        budget: int = BUDGET_S) -> tuple[int, str]:
    """(exit code, summary). 0 = compiled and every selected test passed."""
    paths = changed_files() if paths is None else paths
    errs = compile_errors(paths)
    if errs:
        return 1, "does not compile:\n  " + "\n  ".join(errs)
    tests = ["tests"] if all_tests else tests_for(paths)
    if not tests:
        return 0, f"{len(paths)} changed file(s) compile; no tests cover them"
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=budget)
    except subprocess.TimeoutExpired:
        return 1, f"tests did not finish in {budget}s: {' '.join(tests)}"
    tail = "\n".join(proc.stdout.strip().splitlines()[-25:])
    if not quiet:
        print(tail)
    head = f"{len(paths)} changed file(s), {len(tests)} test file(s): {' '.join(tests)}"
    return (0 if proc.returncode in (0, 5) else 1), f"{head}\n{tail}"


def hook() -> int:
    """Claude Code Stop hook: exit 2 (blocking, reason on stderr) on failure."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    if payload.get("stop_hook_active"):
        return 0                      # already blocked once this stop; never loop
    paths = changed_files()
    if not paths:
        return 0
    fp = fingerprint(paths)
    if STAMP.exists() and STAMP.read_text().strip() == fp:
        return 0
    rc, summary = run(paths, quiet=True)
    if rc == 0:
        STATE.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(fp)
        return 0
    print("harness verify failed for the files changed this session -- fix before "
          "finishing (or say why it is unrelated):\n" + summary, file=sys.stderr)
    return 2
