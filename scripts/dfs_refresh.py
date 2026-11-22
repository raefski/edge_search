#!/usr/bin/env python3
"""Everything that has to follow a contest-export drop, in order, as one command.

import-contests files/tags/grades the exports, but the results views are
separate jobs with no timer, so a new import left the scorecard and the ROI
dashboard rows stale until someone remembered each one (Adam 11/13: "chain
these all together"). Each step runs through harness.runner so it is logged in
data/harness/runs.jsonl like any job; the chain stops at the first failure
(later steps would only rebuild from half-updated inputs).

Order matters: entry-history fills contest_meta (fees, fields) and the ROI the
rows read; the rows feed the dashboard; the scorecard reads the same rows.
The claude.ai dashboard upload stays a Claude step (it needs the Artifact
tool) -- the last line says which files to upload.

Usage: python3 -m harness run dfs-refresh
"""
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.runner import run  # noqa: E402

STEPS = ("import-contests", "entry-history", "roi-dashboard-rows",
         "critique-ledger-rows", "dfs-scorecard")


# DK entry-history exports land in Windows Downloads; import-contests only sweeps
# standings there, so a fresh history sat unread (11/13: rows stopped at 11/08 while
# Adam's history ran to 11/12). Rows are deduped by Entry_Key, so copying a newer
# full re-export beside the old ones is safe.
DOWNLOADS = Path("/mnt/c/Users/ASR/Downloads")
HIST = "draftkings-contest-entry-history*.csv"
STALE_DAYS = 2


def pull_history() -> None:
    have = list((ROOT / "data").glob(HIST))
    newest = max((f.stat().st_mtime for f in have), default=0)
    for f in sorted(DOWNLOADS.glob(HIST), key=lambda f: f.stat().st_mtime) if DOWNLOADS.is_dir() else []:
        dest = ROOT / "data" / f.name
        if f.stat().st_mtime > newest and not dest.exists():
            shutil.copy2(f, dest)
            newest = f.stat().st_mtime
            print(f"entry history: copied {f.name} from Downloads")
    age = (time.time() - newest) / 86400 if newest else None
    if age is None or age > STALE_DAYS:
        print(f"⚠ newest DK entry-history export is {'missing' if age is None else f'{age:.0f} days old'}: "
              "payouts after it are estimated -- DK My Contests > History > Download")


def main() -> int:
    pull_history()
    for name in STEPS:
        print(f"\n━━ {name} ━━", flush=True)
        rc = run(name)
        if rc:
            print(f"\n✗ dfs-refresh stopped at {name} (exit {rc}); later steps not run")
            return rc
    print("\n✓ dfs-refresh done: data/dfs_scorecard.json rebuilt. Dashboard upload "
          "(Claude): data/harness/dfs_roi/*.json -> the 'DFS ROI by Sport' artifact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
