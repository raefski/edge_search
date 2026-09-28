"""A directory of JSON files, one per (kind, key). Everything fetched is kept,
so a transcript is downloaded once no matter how many projects read it."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("TRANSCRIPTS_CACHE", Path.home() / ".cache" / "transcripts"))


class Cache:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else DEFAULT_ROOT

    def path(self, kind: str, key: str) -> Path:
        return self.root / kind / f"{key}.json"

    def get(self, kind: str, key: str):
        p = self.path(kind, key)
        return json.loads(p.read_text()) if p.exists() else None

    def put(self, kind: str, key: str, value) -> None:
        p = self.path(kind, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        tmp.replace(p)
