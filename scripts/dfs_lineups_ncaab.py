#!/usr/bin/env python3
"""DK College Basketball cash + GPP lineups -- scripts/dfs_lineups_nba.py with
--sport ncaab (one CLI, so the two cannot drift). Same flags."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dfs_lineups_nba import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main("ncaab"))
