#!/usr/bin/env python3
"""Import DK contest-standings exports: file them, tag them from DK itself, grade.

    python3 -m harness run import-contests                 # the inbox + Windows Downloads
    python3 scripts/dfs_import_contests.py --from DIR --retag

WHERE THEY GO: data/contest_inbox/<sport>/<cash|gpp>/, one folder per sport
(mlb nfl ncaaf nhl nba ncaab nascar mma) with cash and gpp inside. Drop in the
.zip DraftKings downloads, or the .csv inside it; the file name has to keep DK's
contest id (contest-standings-<id>; a " (1)" re-download suffix is fine). Each
one is filed as data/contest-standings-<id>.csv -- where every grader,
scripts/contest_db.py and the backtests read exports -- and REMOVED from the
inbox, so an empty inbox means everything is in. A file that could not be filed
stays where it is, the reason is printed, and `harness status` lists it.

THE FOLDER IS A LABEL; DRAFTKINGS IS THE TRUTH. Every contest is looked up on
DK's contest endpoint, which for a finished contest returns its name, sport, fee,
entries, places paid and -- the part that used to be guessed -- its DRAFT GROUP,
the exact slate. That fills data/contest_meta.json: type (cash = double-up /
50-50, otherwise gpp), sport, field, places_paid, entry_fee, prize_pool,
draft_group, slate. Where DK disagrees with the folder a file was dropped in, DK
wins, the report says so and the folder is kept as `dropped_as`: on 2026-10-14
two NFL exports handed over as NHL were graded against NHL nights. When the
lookup fails (a DK block, no network) the file stays in the inbox for the next
run rather than being filed untagged, because an untagged export gets matched to
whichever night looks closest.

Windows Downloads is still swept (unlabelled, and an export already in data/ is
left alone there -- Downloads keeps every old copy), and exports put straight into
data/ that were never tagged get tagged.

Then data/contests.db is updated, the user's finish is printed, and each new
contest is graded by its sport's calibration script against the pre-lock log of
ITS draft group, where one was logged. Fitting constants from those grades stays
a deliberate, held-out step (CLAUDE.md standards; the sport's STATUS file).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.dfs_contest import lookup  # noqa: E402

DOWNLOADS = Path("/mnt/c/Users/ASR/Downloads")
DATA = ROOT / "data"
META = DATA / "contest_meta.json"
INBOX = DATA / "contest_inbox"
USER = "gorillabiscuit"
#: the inbox's sport folders, in this repo's sport names (DK's own lobby codes
#: are accepted too: a cfb/ folder is NCAAF)
SPORTS = ("mlb", "nfl", "ncaaf", "nhl", "nba", "ncaab", "nascar", "mma")
ALIASES = {"cfb": "ncaaf", "cbb": "ncaab", "nas": "nascar", "ufc": "mma"}
TYPES = ("cash", "gpp")
#: folder keepers, never exports
KEEP = {".gitkeep", "README.md"}
#: Windows writes this stream beside a file copied from Explorer into WSL
#: ("contest-standings-1.zip:Zone.Identifier"); it carries the id in its name
ZONE = ":Zone.Identifier"
#: DRAFTKINGS_ACCESS.md: >= 1 s between ad-hoc lookups, and a 403 means stop
LOOKUP_GAP_S = 1.1
#: sport -> (projection log, column holding the draft group); MLB logs one file
#: per slate with the draft group in its name (data/dfs_proj_log_<date>_g<gid>.csv)
LOGS = {"NHL": ("data/dfs_proj_log_nhl.csv", "gid"), "NFL": ("data/dfs_proj_log_nfl.csv", "gid"),
        "NCAAF": ("data/dfs_proj_log_ncaaf.csv", "gid"), "NASCAR": ("data/dfs_proj_log_nascar.csv", "gid"),
        "MMA": ("data/dfs_proj_log_mma.csv", "gid"), "NBA": ("data/dfs_proj_log_nba.csv", "gid"),
        "NCAAB": ("data/dfs_proj_log_ncaab.csv", "gid"), "MLB": ("data/dfs_proj_log_*_g{gid}.csv", None)}
#: sport -> the grader run on each new export whose draft group was logged before
#: lock ("{f}" = the export). Each matches the export to the log by its tagged
#: draft group (NCAAF/NASCAR/MMA through edge.dfs_contest.tagged_slate) except
#: MLB, which dates an export by its box-score ground truth. NFL and MLB grade
#: every export in one pass, so they run once per import.
GRADERS = {"NHL": ["scripts/nhl_calibration.py", "{f}", "--fit-ownership"],
           "NBA": ["scripts/nba_calibration.py", "{f}"],
           "NCAAB": ["scripts/nba_calibration.py", "{f}"],
           "NCAAF": ["scripts/ncaaf_calibration.py", "{f}"],
           "NASCAR": ["scripts/nascar_calibration.py", "{f}"],
           "MMA": ["scripts/mma_calibration.py", "--fit-ownership", "{f}"],
           "NFL": ["scripts/dfs_calibration_nfl.py"],
           "MLB": ["scripts/dfs_calibration.py"]}


# ---------------------------------------------------------------- one download
def export_id(name: str) -> str | None:
    """DK's contest id from a download's name. Names vary: "...-196164828 (1).zip"
    (a re-download), "...-186465288_2025-12-25.csv", or an unzipped folder with no
    extension. The id is the 6+ digit run."""
    m = re.match(r"contest-standings-(\d{6,})", name)
    return m.group(1) if m and not name.endswith(ZONE) else None


def export_bytes(src: Path) -> bytes | None:
    """The standings CSV inside one download -- a .zip, the .csv itself, or the
    folder Windows' "Extract all" makes. None when it is not a DK standings export
    (every one starts with a Rank header) or the zip is broken (a partial download)."""
    try:
        if src.is_dir():
            inner = next(iter(sorted(src.glob("*.csv"))), None)
            data = inner.read_bytes() if inner else None
        elif zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                name = next((n for n in z.namelist() if n.endswith(".csv")), None)
                data = z.read(name) if name else None
        else:
            data = src.read_bytes()
    except (OSError, zipfile.BadZipFile):
        return None
    return data if data and data.startswith((b"Rank", b"\xef\xbb\xbfRank")) else None


def extract(src: Path, dest_dir: Path) -> Path | None:
    """Copy one Downloads export into data/contest-standings-<id>.csv, unless it
    is already there (Downloads keeps every old copy; the inbox is where a file
    is handed over on purpose)."""
    cid = export_id(src.name)
    if not cid:
        return None
    dest = dest_dir / f"contest-standings-{cid}.csv"
    if dest.exists():
        return None
    data = export_bytes(src)
    if data is None:
        return None
    dest.write_bytes(data)
    return dest


# ---------------------------------------------------------------- the inbox
def drop_label(path: Path, inbox: Path = INBOX) -> tuple[str | None, str | None]:
    """(sport, type) the folders a file was dropped in say: nhl/gpp/x.zip ->
    ("NHL", "gpp"). Either is None where the folders do not say; the order of the
    two folders does not matter."""
    parts = [p.lower() for p in path.relative_to(inbox).parts[:-1]]
    sport = next((ALIASES.get(p, p) for p in parts if ALIASES.get(p, p) in SPORTS), None)
    ctype = next((p for p in parts if p in TYPES), None)
    return (sport.upper() if sport else None), ctype


def inbox_files(inbox: Path = INBOX) -> list[Path]:
    """Every file waiting in the inbox, folder keepers and Zone.Identifier streams aside."""
    if not inbox.exists():
        return []
    return sorted(p for p in inbox.rglob("*")
                  if p.is_file() and p.name not in KEEP and not p.name.endswith(ZONE))


def prune(inbox: Path = INBOX) -> None:
    """Clear what filing leaves behind: the Zone.Identifier streams of files that
    are gone, and emptied folders BELOW the sport/type ones (an "Extract all" folder)."""
    if not inbox.exists():
        return
    for z in inbox.rglob(f"*{ZONE}"):
        if not z.with_name(z.name[:-len(ZONE)]).exists():
            z.unlink()
    for d in sorted((p for p in inbox.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if len(d.relative_to(inbox).parts) > 2 and not any(d.iterdir()):
            d.rmdir()


class Lookups:
    """DK contest lookups at most one per LOOKUP_GAP_S, each id once per run, and
    none after the first failure -- a 403 means stop, not retry (a broad sweep
    earns a residential IP block for hours)."""

    def __init__(self, fn=lookup, gap: float = LOOKUP_GAP_S, sleep=time.sleep):
        self.fn, self.gap, self.sleep = fn, gap, sleep
        self.cache: dict = {}
        self.last: float | None = None
        self.failed: Exception | None = None

    def __call__(self, cid: str) -> dict:
        if cid in self.cache:
            return self.cache[cid]
        if self.failed is not None:
            raise RuntimeError(f"not asked: an earlier lookup this run failed ({self.failed})")
        if self.last is not None:
            wait = self.gap - (time.monotonic() - self.last)
            if wait > 0:
                self.sleep(wait)
        try:
            self.cache[cid] = self.fn(cid)
        except Exception as exc:                            # noqa: BLE001
            self.failed = exc
            raise
        finally:
            self.last = time.monotonic()
        return self.cache[cid]


def label_note(label: tuple, info: dict) -> str:
    """'' when the drop folder agrees with DK, else what DK says instead."""
    sport, ctype = label
    wrong = ([f"{info['sport']}, not {sport}"] if sport and sport != info["sport"] else []) + \
            ([f"{info['type']}, not {ctype}"] if ctype and ctype != info["type"] else [])
    return f"DK says {' and '.join(wrong)} -- filed as DK says" if wrong else ""


def tag(meta: dict, cid: str, info: dict, dropped_as: str | None = None) -> None:
    entry = meta.setdefault(cid, {})
    if not isinstance(entry, dict):                       # a legacy bare "cash"
        entry = meta[cid] = {}
    entry.update({k: info[k] for k in ("type", "sport", "field", "entry_fee", "places_paid",
                                       "prize_pool", "draft_group", "name", "guaranteed")})
    entry["slate"] = f"{et_date(info['start'])} (gid {info['draft_group']})"
    if dropped_as:
        entry["dropped_as"] = dropped_as


def import_inbox(inbox: Path, data: Path, meta: dict, look) -> list[dict]:
    """File every inbox export into data/ and tag it from DK. One record per
    file: {"src", "cid", "dest", "info", "status", "note"}, status one of
    filed / replaced / already filed / left (note says why it was left)."""
    out = []
    for src in inbox_files(inbox):
        rec = {"src": src, "cid": export_id(src.name), "dest": None, "info": None, "note": ""}
        label = drop_label(src, inbox)
        if not rec["cid"]:
            out.append({**rec, "status": "left",
                        "note": "no contest id in the file name -- keep DK's name, contest-standings-<id>"})
            continue
        body = export_bytes(src)
        if body is None:
            out.append({**rec, "status": "left",
                        "note": "not a DK contest-standings export (no Rank header) -- a partial download?"})
            continue
        dest = data / f"contest-standings-{rec['cid']}.csv"
        have = meta.get(rec["cid"])
        if dest.exists() and dest.read_bytes() == body and isinstance(have, dict) \
                and have.get("draft_group"):
            # the same file, filed and tagged before: no DK call needed
            src.unlink()
            out.append({**rec, "dest": dest, "status": "already filed", "note": label_note(label, have)})
            continue
        try:
            info = look(rec["cid"])
        except Exception as exc:                            # noqa: BLE001
            out.append({**rec, "status": "left",
                        "note": f"DK lookup failed ({exc}); left for the next run"})
            continue
        status = ("filed" if not dest.exists() else
                  "already filed" if dest.read_bytes() == body else "replaced")
        if status != "already filed":
            dest.write_bytes(body)
        note = label_note(label, info)
        tag(meta, rec["cid"], info,
            "/".join(x.lower() for x in label if x) if note else None)
        src.unlink()
        out.append({**rec, "dest": dest, "info": info, "status": status, "note": note})
    prune(inbox)
    return out


def pending(inbox: Path = INBOX, data: Path = DATA, meta_path: Path = META) -> list[str]:
    """What the next import would file or tag: inbox files (as nhl/gpp/<name>) and
    exports in data/ that were never tagged. `harness status` shows this."""
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        meta = {}
    waiting = [str(p.relative_to(inbox)) for p in inbox_files(inbox)]
    waiting += [f"data/{p.name} (untagged)" for p in sorted(data.glob("contest-standings-*.csv"))
                if export_id(p.name) not in meta]
    return waiting


# ---------------------------------------------------------------- the report
def et_date(iso: str | None) -> str:
    """DK start times are UTC; a Sunday-night kickoff is Monday in UTC."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if not iso:
        return ""
    t = datetime.fromisoformat(iso.split(".")[0].replace("Z", "") + "+00:00")
    return t.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def my_entry(path: Path, user: str):
    for line in path.open(encoding="utf-8-sig"):
        f = line.split(",")
        if f[0].isdigit() and len(f) > 4 and f[2].lower() == user.lower():
            return int(f[0]), float(f[4])
    return None


def logged(sport: str, gid, root: Path = ROOT) -> bool | None:
    """Was this draft group built and logged before lock? None: no log for the sport."""
    spec = LOGS.get(sport)
    if not spec:
        return None
    if spec[1] is None:
        return bool(glob.glob(str(root / spec[0].format(gid=gid))))
    if not (root / spec[0]).exists():
        return None
    with (root / spec[0]).open(newline="") as fh:
        return any(str(r.get(spec[1])) == str(gid) for r in csv.DictReader(fh))


def update_db(cids: list[str]) -> None:
    """Fold the new exports into data/contests.db (incremental, no DK calls) and
    say whether each one matched its pre-lock projection there."""
    import sqlite3
    from scripts import contest_db
    try:
        contest_db.build(quiet=True)
    except Exception as exc:                                # noqa: BLE001
        print(f"\ncontests.db NOT updated ({exc}) -- run `python3 -m harness run contest-db`")
        return
    con = sqlite3.connect(str(contest_db.DB_PATH))
    rows = {str(r[0]): r[1:] for r in con.execute(
        "SELECT contest_id, method, proj_draft_group, ok_points, ok_own, note FROM contest_match")}
    con.close()
    print("\ncontests.db updated:")
    for cid in cids:
        method, gid, ok_pts, ok_own, note = rows.get(cid, ("not ingested", None, 0, 0, ""))
        print(f"  {cid}: {method}{f' {gid}' if gid else ''}  points {'✓' if ok_pts else '✗'}  "
              f"ownership {'✓' if ok_own else '✗'}  {note or ''}")


def grade(report: list[tuple]) -> None:
    """Each new contest through its sport's grader, against its own draft group."""
    once: set[str] = set()
    for cid, info, _me, is_logged, path in report:
        sport = info["sport"]
        cmd = GRADERS.get(sport)
        if not cmd:
            print(f"\n== {cid}: no {sport} model in this repo -- filed and tagged, nothing to grade")
            continue
        if not is_logged:
            print(f"\n== {cid} {sport} {info['type']}: draft group {info['draft_group']} was never "
                  "built and logged before lock (a phone build on Cloud is not logged) -- not gradeable")
            continue
        if "{f}" not in cmd:
            if sport in once:
                continue
            once.add(sport)
        print(f"\n== {cid} {sport} {info['type']}: {cmd[0]}", flush=True)
        f = str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
        subprocess.run([sys.executable] + [c.replace("{f}", f) for c in cmd], cwd=ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", default=str(DOWNLOADS), help="unlabelled downloads to sweep")
    ap.add_argument("--inbox", default=str(INBOX), help="the labelled drop folders")
    ap.add_argument("--retag", action="store_true", help="re-look-up every contest in data/")
    ap.add_argument("--user", default=USER)
    ap.add_argument("--no-grade", action="store_true")
    args = ap.parse_args()
    meta = json.loads(META.read_text())
    look = Lookups()

    # 1. the inbox: labelled, handed over on purpose
    inbox = Path(args.inbox)
    dropped = import_inbox(inbox, DATA, meta, look)
    left = [r for r in dropped if r["status"] == "left"]
    print(f"inbox {inbox.relative_to(ROOT) if inbox.is_relative_to(ROOT) else inbox}: "
          f"{len(dropped) - len(left)} filed, {len(left)} left")
    for r in dropped:
        print(f"  {r['status']:13} {r['src'].relative_to(inbox)}"
              + (f"  -- {r['note']}" if r["note"] else ""))
    # contests whose file is new or changed: these are graded
    changed = {r["cid"] for r in dropped if r["status"] in ("filed", "replaced")}

    # 2. Windows Downloads: unlabelled, never replaces what is filed
    new = [got for src in sorted(Path(args.src).glob("contest-standings-*"))
           if (got := extract(src, DATA))]
    print(f"Downloads: {len(new)} new export(s) copied into data/")
    changed |= {export_id(p.name) for p in new}

    # 3. look up what is filed but untagged (or everything, --retag)
    report = [(r["cid"], r["info"], r["dest"]) for r in dropped if r["info"]]
    for path in sorted(DATA.glob("contest-standings-*.csv")):
        cid = export_id(path.name)
        if not cid or any(cid == c for c, _, _ in report):
            continue
        if not (args.retag or cid in changed or not isinstance(meta.get(cid), dict)):
            continue
        try:
            info = look(cid)
        except Exception as exc:                            # noqa: BLE001
            print(f"  {cid}: DK lookup failed ({exc}) -- still untagged; rerun later")
            continue
        if not isinstance(meta.get(cid), dict):
            changed.add(cid)                                # tagged for the first time
        tag(meta, cid, info)
        report.append((cid, info, path))
    META.write_text(json.dumps(meta, indent=1) + "\n")

    rows = [(cid, info, my_entry(path, args.user), logged(info["sport"], info["draft_group"]), path)
            for cid, info, path in report]
    if rows:
        print(f"\n{'contest':10} {'sport':6} {'type':4} {'date':10} {'entries':>9} {'paid':>5}  "
              f"{'you':>12} {'cashed':6} {'slate logged':12}  name")
    for cid, info, me, is_logged, _ in rows:
        you = f"#{me[0]} {me[1]:.1f}" if me else "-"
        cashed = ("YES" if me[0] <= (info["places_paid"] or 0) else "no") if me else ""
        lg = {True: "yes", False: "NO", None: "n/a"}[is_logged]
        print(f"{cid:10} {info['sport']:6} {info['type']:4} {et_date(info['start']):10} "
              f"{info['field']:>4}/{info['max_entries']:<4} {info['places_paid'] or '':>5}  {you:>12} "
              f"{cashed:6} {lg:12}  {info['name']}")
    # an identical re-drop, or a --retag of a contest already tagged, is not graded again
    fresh = [r for r in rows if r[0] in changed]
    if fresh:
        update_db([r[0] for r in fresh])
        if not args.no_grade:
            grade(fresh)
    if left:
        print(f"\n{len(left)} file(s) still in {args.inbox} -- see above")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
