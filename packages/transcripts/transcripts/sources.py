"""Which sources are worth reading: an append-only ledger of graded results
per source (a YouTube channel, a podcast), per domain.

Grading is the caller's job, because only the caller knows what "right"
means: a DFS project records how much a channel's mentions improved an
ownership fit, a betting project records whether a tout's pick covered. The
ledger just keeps every observation with where it came from, and ranks
sources on any metric once enough of them are in, so a noisy source can be
dropped on evidence instead of on a hunch.
"""
from __future__ import annotations

import csv
import statistics
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ["recorded", "domain", "source_id", "source", "item", "metric", "value"]


class Ledger:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def rows(self, domain: str | None = None, metric: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(newline="") as fh:
            return [r for r in csv.DictReader(fh)
                    if (domain is None or r["domain"] == domain)
                    and (metric is None or r["metric"] == metric)]

    def record(self, domain: str, source_id: str, source: str, item: str,
               metric: str, value) -> None:
        """One observation. Re-recording the same (domain, source, item,
        metric) replaces the old value, so re-running a grader is safe."""
        keep = [r for r in self.rows() if (r["domain"], r["source_id"], r["item"], r["metric"])
                != (domain, source_id, item, metric)]
        keep.append({"recorded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "domain": domain, "source_id": source_id, "source": source,
                     "item": item, "metric": metric, "value": value})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(keep)

    def scores(self, domain: str, metric: str, min_n: int = 1) -> list[dict]:
        """[{source_id, source, n, mean}] best first, for numeric metrics."""
        by: dict = {}
        for r in self.rows(domain, metric):
            try:
                by.setdefault(r["source_id"], (r["source"], []))[1].append(float(r["value"]))
            except ValueError:
                continue
        out = [{"source_id": sid, "source": name, "n": len(v), "mean": statistics.fmean(v)}
               for sid, (name, v) in by.items() if len(v) >= min_n]
        return sorted(out, key=lambda s: -s["mean"])
