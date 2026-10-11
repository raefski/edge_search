#!/usr/bin/env python3
"""Import DK contest-standings exports: file them, tag them from DK itself, report.

    python3 scripts/dfs_import_contests.py                  # Windows Downloads -> data/
    python3 scripts/dfs_import_contests.py --from DIR --retag

For every contest-standings-<id>.zip / .csv in the Downloads folder that is not
already in data/, the CSV is extracted to data/. Every contest (new, or all of
them with --retag) is then looked up on DK's contest endpoint, which for a
finished contest returns its name, sport, fee, entries, places paid and -- the
part that used to be guessed -- its DRAFT GROUP, the exact slate. That fills
data/contest_meta.json: type (cash = double-up / 50-50, otherwise gpp), sport,
field, places_paid, entry_fee, prize_pool, draft_group, slate.

It then prints the user's finish and whether the slate is in that sport's
projection log (gradeable), and runs the NHL grader where it is.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.dfs_contest import lookup  # noqa: E402

DOWNLOADS = Path("/mnt/c/Users/ASR/Downloads")
META = ROOT / "data" / "contest_meta.json"
USER = "gorillabiscuit"
#: sport -> (projection log, column holding the draft group or None)
LOGS = {"NHL": ("data/dfs_proj_log_nhl.csv", "gid"), "NFL": ("data/dfs_proj_log_nfl.csv", "gid"),
        "NCAAF": ("data/dfs_proj_log_ncaaf.csv", "gid"), "NASCAR": ("data/dfs_proj_log_nascar.csv", "gid"),
        "MMA": ("data/dfs_proj_log_mma.csv", "gid")}


def extract(src: Path, dest_dir: Path) -> Path | None:
    """Copy one export into data/contest-standings-<id>.csv. Download names vary:
    "...-196164828 (1).zip" (a re-download), "...-186465288_2025-12-25.csv",
    or an unzipped folder with no extension. The id is the 6+ digit run."""
    m = re.match(r"contest-standings-(\d{6,})", src.name)
    if not m:
        return None
    dest = dest_dir / f"contest-standings-{m.group(1)}.csv"
    if dest.exists():
        return None
    if src.is_dir():
        inner = next(iter(sorted(src.glob("*.csv"))), None)
        data = inner.read_bytes() if inner else None
    elif zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            name = next((n for n in z.namelist() if n.endswith(".csv")), None)
            data = z.read(name) if name else None
    else:
        data = src.read_bytes()
    if not data or not data.startswith((b"Rank", b"\xef\xbb\xbfRank")):
        return None
    dest.write_bytes(data)
    return dest


def et_date(iso: str | None) -> str:
    """DK start times are UTC; a Sunday-night kickoff is Monday in UTC."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if not iso:
        return ""
    t = datetime.fromisoformat(iso.split(".")[0].replace("Z", "") + "+00:00")
    return t.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def my_entry(path: Path, user: str):
    for line in path.open():
        f = line.split(",")
        if f[0].isdigit() and len(f) > 4 and f[2].lower() == user.lower():
            return int(f[0]), float(f[4])
    return None


def logged(sport: str, gid) -> bool | None:
    spec = LOGS.get(sport)
    if not spec or not (ROOT / spec[0]).exists():
        return None
    return any(str(r.get(spec[1])) == str(gid) for r in csv.DictReader((ROOT / spec[0]).open()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", default=str(DOWNLOADS))
    ap.add_argument("--retag", action="store_true", help="re-look-up every contest in data/")
    ap.add_argument("--user", default=USER)
    ap.add_argument("--no-grade", action="store_true")
    args = ap.parse_args()
    data = ROOT / "data"
    new = []
    for src in sorted(Path(args.src).glob("contest-standings-*")):
        got = extract(src, data)
        if got:
            new.append(got)
    print(f"{len(new)} new export(s) copied into data/")
    meta = json.loads(META.read_text())
    targets = sorted(data.glob("contest-standings-*.csv")) if args.retag else new
    report = []
    for path in targets:
        cid = path.stem.split("-")[-1]
        try:
            info = lookup(cid)
        except Exception as exc:                            # noqa: BLE001
            print(f"  {cid}: DK lookup failed ({exc})")
            continue
        start = et_date(info["start"])
        entry = meta.setdefault(cid, {})
        entry.update({k: info[k] for k in ("type", "sport", "field", "entry_fee", "places_paid",
                                           "prize_pool", "draft_group", "name", "guaranteed")})
        entry["slate"] = f"{start} (gid {info['draft_group']})"
        report.append((cid, info, my_entry(path, args.user), logged(info["sport"], info["draft_group"]),
                       path))
    META.write_text(json.dumps(meta, indent=1) + "\n")
    print(f"{'contest':10} {'sport':6} {'type':4} {'date':10} {'entries':>9} {'paid':>5}  "
          f"{'you':>12} {'cashed':6} {'slate logged':12}  name")
    for cid, info, me, is_logged, _ in report:
        you = f"{me[0]}th {me[1]:.1f}" if me else "-"
        cashed = ("YES" if me[0] <= (info["places_paid"] or 0) else "no") if me else ""
        lg = {True: "yes", False: "NO", None: "n/a"}[is_logged]
        print(f"{cid:10} {info['sport']:6} {info['type']:4} {et_date(info['start']):10} "
              f"{info['field']:>4}/{info['max_entries']:<4} {info['places_paid'] or '':>5}  {you:>12} "
              f"{cashed:6} {lg:12}  {info['name']}")
    if not args.no_grade:
        for cid, info, _, is_logged, path in report:
            if info["sport"] == "NHL" and is_logged:
                print(f"\n== {cid} ({info['type']})")
                subprocess.run([sys.executable, "scripts/nhl_calibration.py", str(path.relative_to(ROOT)),
                                "--fit-ownership"], cwd=ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
