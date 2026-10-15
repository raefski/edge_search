"""edge_search harness CLI.

    python3 -m harness status [--write] [--live]   what is locking / running / stale / broken
    python3 -m harness list [--jobs] [--role R] [--sport S]   the catalog
    python3 -m harness run <job> [-- extra args]   run a cataloged job, logged
    python3 -m harness verify [paths...] [--all]   compile + the tests covering what changed
    python3 -m harness monitor [--push] [--hours H]   goalie changes + line moves
    python3 -m harness late-swap nhl [--gid G] [--players "a, b"] [--entry CSV] [--mode gpp|cash]
    python3 -m harness overlay [--push] [--min-roi 0.10] [--hours 2.5]   guaranteed contests not filling
    python3 -m harness navigate <url> [--match S] [--click TEXT] [--wait S] [--profile DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:
        k = argv.index("--")
        argv, extra = argv[:k], argv[k + 1:]
    ap = argparse.ArgumentParser(prog="python3 -m harness", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--write", action="store_true", help="also write data/harness/STATUS.md")
    s.add_argument("--live", action="store_true", help="ask DK's lobby (1 call per sport)")
    s = sub.add_parser("list")
    s.add_argument("--jobs", action="store_true")
    s.add_argument("--role")
    s.add_argument("--sport")
    s = sub.add_parser("run")
    s.add_argument("job")
    s.add_argument("--quiet", action="store_true")
    s = sub.add_parser("verify")
    s.add_argument("paths", nargs="*")
    s.add_argument("--all", action="store_true")
    s.add_argument("--hook", action="store_true", help="Claude Code Stop-hook protocol")
    s = sub.add_parser("monitor")
    s.add_argument("--push", action="store_true")
    s.add_argument("--hours", type=float, default=12.0)
    s.add_argument("--no-goalies", action="store_true")
    s.add_argument("--no-lines", action="store_true")
    s = sub.add_parser("late-swap")
    s.add_argument("sport", choices=["nhl", "mlb", "nfl", "ncaaf", "nascar", "mma"])
    s.add_argument("--gid", type=int)
    s.add_argument("--players")
    s.add_argument("--entry")
    s.add_argument("--mode", choices=["gpp", "cash"], default="gpp")
    s.add_argument("--sims", type=int, default=2000)
    s.add_argument("--improve", action="store_true",
                   help="also re-optimise healthy unlocked slots (default: only replace who is out)")
    s = sub.add_parser("overlay")
    s.add_argument("--push", action="store_true", help="send the alerts to the phone")
    s.add_argument("--hours", type=float, default=2.5, help="look this far ahead of lock")
    s.add_argument("--min-roi", type=float, default=0.10,
                   help="alert when one more entry at lock returns this much above its cost")
    s.add_argument("--max-fee", type=float, default=25.0)
    s.add_argument("--include-resizable", action="store_true")
    s.add_argument("--all-types", action="store_true",
                   help="include Showdown, Captain and Madden contests (default: Classic only)")
    s.add_argument("--report", action="store_true",
                   help="how often locked contests (entered or not) ended with an overlay")
    s = sub.add_parser("navigate")
    s.add_argument("url")
    s.add_argument("--match")
    s.add_argument("--click", action="append")
    s.add_argument("--wait", type=float, default=6.0)
    s.add_argument("--profile")
    s.add_argument("--headed", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        from harness import status
        text = status.report(live=a.live)
        print(text)
        if a.write:
            print(f"\nwritten to {status.write(text)}")
        return 0
    if a.cmd == "list":
        from harness.registry import JOBS, catalog
        if a.jobs:
            for j in JOBS.values():
                if (not a.role or j.role == a.role) and (not a.sport or j.sport == a.sport):
                    print(f"  {j.name:<20} {j.role:<9} {(j.timer or ''):<34} {j.doc}")
            return 0
        for r in catalog():
            if (not a.role or r["role"] == a.role) and (not a.sport or r["sport"] == a.sport):
                print(f"  {r['role'] or '?':<9} {r['sport'] or '':<7} {r['path']:<44} {r['job'] or ''}")
        return 0
    if a.cmd == "run":
        from harness.runner import run
        return run(a.job, extra, quiet=a.quiet)
    if a.cmd == "verify":
        from harness import verify
        if a.hook:
            return verify.hook()
        rc, summary = verify.run(a.paths or None, all_tests=a.all)
        print(("✓ " if rc == 0 else "✗ ") + summary.splitlines()[0])
        return rc
    if a.cmd == "monitor":
        from harness import monitor
        monitor.run(do_push=a.push, hours=a.hours, goalies=not a.no_goalies, lines=not a.no_lines)
        return 0
    if a.cmd == "late-swap":
        from harness import late_swap
        a.rest = extra
        return late_swap.main(a)
    if a.cmd == "overlay":
        from harness import overlay
        if a.report:
            print("\n".join(overlay.report()))
            return 0
        overlay.run(do_push=a.push, hours=a.hours, min_roi=a.min_roi, max_fee=a.max_fee,
                    include_resizable=a.include_resizable, all_types=a.all_types)
        return 0
    if a.cmd == "navigate":
        from harness import navigator
        navigator.run(a.url, match=a.match, clicks=a.click, wait=a.wait, profile=a.profile,
                      headed=a.headed)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
