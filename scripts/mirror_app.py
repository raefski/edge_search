#!/usr/bin/env python3
"""Mirror one sport's app into its own deployable repo.

    python3 scripts/mirror_app.py --app ncaaf
    python3 scripts/mirror_app.py --app nascar --push
    python3 scripts/mirror_app.py --app ncaaf --dry-run

WHY THIS EXISTS RATHER THAN A SECOND COPY OF THE CODE
Two repos exist for these sports (raefski/ncaaf_fantasy, raefski/nascar_fantasy)
so each can be its own Streamlit Community Cloud app with its own URL on the
phone. The obvious way to fill them is to write the sport twice. That is the
failure this repo has already had once and documents in ODDS_LAYER.md: two
copies of a shared core drift, and in DFS the drift costs money rather than
tidiness.

So there is ONE source of truth -- this repo -- and the other two repos are
GENERATED. Everything in them is copied, nothing is authored there, and the
README this writes says so at the top so that a future edit does not get made
in the wrong place and then lost.

WHAT GETS COPIED, AND WHY IT IS THE WHOLE `edge` PACKAGE
It would be neater to copy only the modules one sport touches. It is also not
possible to keep neat: `edge/dfs.py` reaches `edge/arb/http.py` for the
DraftKings HTTP/2 shim, `edge/odds/profiles.py` reaches `edge/arb/config.py`,
and the odds collector reaches the scrapers. Tracing that closure by hand and
keeping it traced is a maintenance liability with no upside, since the package
is a few hundred KB of text. The whole thing goes.

WHAT DELIBERATELY DOES NOT GET COPIED
  * `data/odds.db` -- the accumulated scrape history. It is gitignored here for
    a reason (it cannot be re-derived, and these repos may be public) and the
    cloud app reads a published snapshot instead.
  * the other sports' pages, scripts and docs.
  * `.env`, anything under `data/cache/`.

THE CLOUD APP CANNOT SCRAPE, so the snapshots it reads are copied in:
`data/odds_snapshot_<profile>.json` for prices and `data/draftables_snapshot/`
for DraftKings salaries (api.draftkings.com 403s datacenter IPs --
DRAFTKINGS_ACCESS.md §1). Both are refreshed from the desktop by
`scripts/odds_collect.py --push` and `scripts/draftables_publish.py`; this
script only carries whatever is current at the moment it runs.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: One entry per generated repo.
APPS = {
    "ncaaf": {
        "repo": "https://github.com/raefski/ncaaf_fantasy.git",
        "title": "DK College Football DFS",
        "page": "pages/3_🏈_NCAAF_DFS.py",
        "profile": "dfs_ncaaf",
        "docs": ["NCAAF_STATUS.md", "DRAFTKINGS_ACCESS.md"],
        # dfs_calibration.py is deliberately ABSENT: ncaaf_calibration.py used
        # to import parse_contest_file from it, which dragged scripts.dfs_grade
        # and the whole MLB actuals pipeline into a college-only repo. That
        # function now lives in edge/dfs_contest.py.
        "scripts": ["dfs_lineups_ncaaf.py", "ncaaf_fit.py", "ncaaf_ladder_fit.py",
                    "ncaaf_overround_fit.py", "ncaaf_calibration.py",
                    "odds_collect.py", "draftables_publish.py",
                    "dk_categories.py", "mirror_app.py"],
        "tests": ["test_dfs_ladder.py", "test_dfs_ncaaf.py"],
        "blurb": (
            "College football is the one sport in this family where DraftKings "
            "posts **one-sided milestone ladders** instead of two-sided "
            "Over/Under lines, so the projection is an integral over the "
            "book's own survival curve rather than a mean read off a single "
            "line. Read `NCAAF_STATUS.md` first."),
    },
    "nascar": {
        "repo": "https://github.com/raefski/nascar_fantasy.git",
        "title": "DK NASCAR DFS",
        "page": "pages/6_🏁_NASCAR_DFS.py",
        "profile": None,          # NASCAR needs no odds profile -- see below
        "docs": ["NASCAR_STATUS.md", "DRAFTKINGS_ACCESS.md"],
        "scripts": ["dfs_lineups_nascar.py", "nascar_fit.py",
                    "nascar_calibration.py", "draftables_publish.py",
                    "mirror_app.py"],
        # The driver-form cache for the seasons the app actually reads. NASCAR's
        # feeds are reachable from a datacenter IP (they are not DraftKings), so
        # this is a COLD-START optimisation rather than a requirement: without
        # it the first build on Streamlit Cloud fetches ~70 race files before it
        # can compute anyone's form. Only two seasons are carried -- the full
        # 2022-2026 cache is 23MB and edge/dfs_run_nascar.FORM_SEASONS reads
        # two.
        "data_globs": ["nascar_cache/*_2025_*.json",
                       "nascar_cache/*_2026_*.json",
                       "nascar_cache/schedule_*.json"],
        "tests": ["test_dfs_nascar.py"],
        "blurb": (
            "NASCAR scores place differential, laps led and fastest laps, none "
            "of which any sportsbook prices. The model is built on NASCAR's "
            "own public timing feeds rather than on odds. Read "
            "`NASCAR_STATUS.md` first."),
    },
}

COMMON = [
    "requirements.txt",
    ".streamlit/config.toml",
    "conftest.py",
]


def run(*args, cwd: Path, check: bool = True):
    return subprocess.run(args, cwd=str(cwd), check=check,
                          capture_output=True, text=True)


def copy(src: Path, dst: Path, dry: bool) -> None:
    if not src.exists():
        print(f"  ! missing, skipped: {src.relative_to(ROOT)}")
        return
    print(f"  {src.relative_to(ROOT)}")
    if dry:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        shutil.copy2(src, dst)


def readme(app: str, spec: dict) -> str:
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""# {spec['title']}

> **Generated repository — do not edit here.**
>
> Every file in this repo is copied from `edge_search`, which is the single
> source of truth. Regenerate with:
>
> ```bash
> python3 scripts/mirror_app.py --app {app} --push
> ```
>
> An edit made here will be silently overwritten on the next mirror. Make it in
> `edge_search` instead. Last mirrored {when}.

{spec['blurb']}

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy (Streamlit Community Cloud)

1. share.streamlit.io → **New app** → this repo
2. **Main file** = `app.py`
3. No secrets are required. This app never calls a paid odds API.

Streamlit Cloud runs on a datacenter IP, which DraftKings blocks
(`DRAFTKINGS_ACCESS.md`), so it cannot scrape. It reads committed snapshots
instead — `data/odds_snapshot_*.json` for prices and `data/draftables_snapshot/`
for salaries. Both are pushed from the desktop; this app is a reader.

## Tests

```bash
pytest -q
```
"""


def entrypoint(spec: dict) -> str:
    """A tiny `app.py` that runs the sport's page as the main file.

    Streamlit Cloud wants one entry file. Rather than rename the page (which
    would make the diff against edge_search noisy and the mirror harder to
    verify), the page is imported and executed. `runpy` rather than `import`
    because a Streamlit page is a script, not a module: it calls
    st.set_page_config at import time and expects to be __main__.
    """
    return f'''"""Streamlit Cloud entry point. GENERATED -- see README.md.

The real page is {spec['page']}, copied verbatim from edge_search. This file
exists only because Streamlit Cloud deploys one named main file, and keeping
the page at its original path makes the mirror trivially diffable against its
source.
"""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

runpy.run_path(str(ROOT / "{spec['page']}"), run_name="__main__")
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True, choices=sorted(APPS))
    ap.add_argument("--dest", default=None,
                    help="target directory (default: ../<app>_fantasy)")
    ap.add_argument("--push", action="store_true",
                    help="commit and push to the app's GitHub remote")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be copied and change nothing")
    ap.add_argument("--message", default=None)
    args = ap.parse_args()

    spec = APPS[args.app]
    dest = Path(args.dest) if args.dest else ROOT.parent / f"{args.app}_fantasy"
    print(f"mirroring {args.app} -> {dest}")
    if args.dry_run:
        print("(dry run — nothing will be written)")

    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        if not (dest / ".git").exists():
            print(f"  git init + remote {spec['repo']}")
            run("git", "init", "-q", cwd=dest)
            run("git", "remote", "add", "origin", spec["repo"], cwd=dest,
                check=False)
        # Carry edge_search's own committer identity across. This machine has
        # no --global user.name, only a per-repo one, so a freshly `git init`ed
        # mirror cannot commit at all ("Author identity unknown") -- and it
        # fails at the commit step, after everything has already been copied,
        # which reads like a mirroring bug rather than a config one.
        for key in ("user.name", "user.email"):
            val = run("git", "config", key, cwd=ROOT, check=False).stdout.strip()
            if val:
                run("git", "config", key, val, cwd=dest, check=False)

    print("\nedge package:")
    copy(ROOT / "edge", dest / "edge", args.dry_run)

    print("\npage + entry point:")
    copy(ROOT / spec["page"], dest / spec["page"], args.dry_run)
    if not args.dry_run:
        (dest / "app.py").write_text(entrypoint(spec))
        (dest / "README.md").write_text(readme(args.app, spec))
    print(f"  app.py  (generated)\n  README.md  (generated)")

    print("\nscripts:")
    for name in spec["scripts"]:
        copy(ROOT / "scripts" / name, dest / "scripts" / name, args.dry_run)
    if not args.dry_run:
        (dest / "scripts" / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
        init = dest / "scripts" / "__init__.py"
        if (ROOT / "scripts" / "__init__.py").exists():
            copy(ROOT / "scripts" / "__init__.py", init, args.dry_run)
        elif not init.exists():
            init.write_text("")

    print("\ntests:")
    for name in spec["tests"]:
        copy(ROOT / "tests" / name, dest / "tests" / name, args.dry_run)

    print("\ndocs + config:")
    for name in spec["docs"] + COMMON:
        copy(ROOT / name, dest / name, args.dry_run)

    print("\ndata the cloud app reads (it cannot scrape):")
    if spec["profile"]:
        copy(ROOT / "data" / f"odds_snapshot_{spec['profile']}.json",
             dest / "data" / f"odds_snapshot_{spec['profile']}.json", args.dry_run)
    copy(ROOT / "data" / "draftables_snapshot",
         dest / "data" / "draftables_snapshot", args.dry_run)
    for extra in spec.get("data", []):
        copy(ROOT / "data" / extra, dest / "data" / extra, args.dry_run)
    for pattern in spec.get("data_globs", []):
        hits = sorted((ROOT / "data").glob(pattern))
        print(f"  data/{pattern}  ({len(hits)} files)")
        if not args.dry_run:
            for src in hits:
                rel = src.relative_to(ROOT / "data")
                dst = dest / "data" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    if not args.dry_run:
        # A .gitignore that does NOT exclude the snapshots -- they are the whole
        # point of the mirror. Deliberately much shorter than edge_search's.
        (dest / ".gitignore").write_text(
            "__pycache__/\n*.pyc\n.env\n.venv/\ndata/cache/\ndata/odds.db*\n"
            ".pytest_cache/\n")

    if args.push and not args.dry_run:
        msg = args.message or f"mirror {args.app} from edge_search"
        run("git", "add", "-A", cwd=dest)
        staged = run("git", "diff", "--cached", "--quiet", cwd=dest, check=False)
        if staged.returncode == 0:
            print("\nnothing changed — not pushing")
            return 0
        run("git", "commit", "-q", "-m", msg, cwd=dest)
        push = run("git", "push", "-u", "origin", "HEAD:main", cwd=dest, check=False)
        if push.returncode != 0:
            print(f"\n! push failed:\n{push.stderr.strip()[:600]}", file=sys.stderr)
            print("The mirror on disk is complete; only the push failed. "
                  "Authenticate (gh auth login) and re-run with --push.",
                  file=sys.stderr)
            return 1
        print(f"\npushed to {spec['repo']}")
    elif not args.dry_run:
        print(f"\nmirrored. To publish:\n  cd {dest} && git add -A && "
              f"git commit -m 'mirror' && git push -u origin HEAD:main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
