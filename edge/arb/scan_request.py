"""Phone asks, desktop scrapes.

Streamlit Community Cloud is a datacenter IP and the books 403 it, so the app
that runs there can never fetch odds itself. The machine in Connecticut can.
This is the message bus between them, and it is a file in the repo:

    phone  -> GitHub contents API -> data/scan_request.json
    desktop poller sees it        -> scrapes -> data/arb_snapshot.json -> push
    phone  -> reads the new snapshot on the next Cloud redeploy

Git rather than a queue because both ends already have credentials for it and
it works through a home router with no inbound ports. The costs are real and
worth naming: a round trip is minutes, not seconds, and every scan is a commit.

Everything here is pure except the two functions that name GitHub in their
docstring, so the button and the poller share one definition of "is this
request worth acting on" instead of each guessing.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from . import http

REQUEST_PATH = "data/scan_request.json"
API = "https://api.github.com"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ScanRequest:
    requested_at: str
    request_id: str
    sports: list[str] = field(default_factory=list)
    note: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1) + "\n"

    @classmethod
    def new(cls, sports: list[str] | None = None, note: str = "") -> "ScanRequest":
        ts = _now()
        return cls(requested_at=ts.isoformat(),
                   request_id=ts.strftime("%Y%m%dT%H%M%S%f"),
                   sports=list(sports or []), note=note)

    @classmethod
    def parse(cls, raw: str | bytes | None) -> "ScanRequest | None":
        if not raw:
            return None
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(d, dict) or not d.get("request_id"):
            return None
        return cls(requested_at=str(d.get("requested_at") or ""),
                   request_id=str(d["request_id"]),
                   sports=list(d.get("sports") or []),
                   note=str(d.get("note") or ""))

    def age_seconds(self, now: datetime | None = None) -> float:
        try:
            ts = datetime.fromisoformat(self.requested_at)
        except ValueError:
            return float("inf")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ((now or _now()) - ts).total_seconds()


def should_handle(req: "ScanRequest | None", last_handled_id: str | None,
                  max_age_seconds: float = 900.0,
                  now: datetime | None = None) -> tuple[bool, str]:
    """Should the poller act on this request? Returns (yes, why-not).

    Three refusals, each for a failure seen in this shape of system:

      * already handled -- the request file stays in the repo after a scan, so
        without this the poller rescans it forever, every cycle.
      * too old -- a desktop that was asleep must not wake up and act on a
        request from last night. The user has long since stopped looking, and
        the prices it would fetch are for games that already started.
      * unparseable -- a half-written or hand-edited file is not a reason to
        scrape; say so rather than treating malformed input as a request.
    """
    if req is None:
        return False, "no request file"
    if last_handled_id and req.request_id == last_handled_id:
        return False, "already handled"
    age = req.age_seconds(now)
    if age > max_age_seconds:
        return False, f"stale request ({age / 60:.0f} min old, cap {max_age_seconds / 60:.0f})"
    return True, ""


def snapshot_is_newer(snapshot_generated_at: str | None,
                      req: "ScanRequest | None") -> bool:
    """Has a scan already answered this request?

    What the phone shows while it waits. Comparing timestamps rather than
    tracking state means a refresh from any device gets the same answer, and a
    scan run by hand on the desktop satisfies a pending request too.
    """
    if req is None or not snapshot_generated_at:
        return False
    try:
        snap = datetime.fromisoformat(snapshot_generated_at)
        asked = datetime.fromisoformat(req.requested_at)
    except (ValueError, TypeError):
        return False
    if snap.tzinfo is None:
        snap = snap.replace(tzinfo=timezone.utc)
    if asked.tzinfo is None:
        asked = asked.replace(tzinfo=timezone.utc)
    return snap >= asked


# --------------------------------------------------------------------------
# GitHub contents API -- the only I/O in this module
# --------------------------------------------------------------------------
def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def get_file_sha(repo: str, path: str, token: str, branch: str = "main",
                 session=None) -> str | None:
    """Blob sha of `path`, or None if it does not exist yet.

    The contents API refuses to overwrite without it, which is the whole point:
    two phones pressing the button at once cannot silently clobber each other.
    """
    s = session or http
    r = s.get(f"{API}/repos/{repo}/contents/{path}", params={"ref": branch},
              headers=_headers(token), timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return (r.json() or {}).get("sha")


def put_request(repo: str, token: str, req: ScanRequest, branch: str = "main",
                path: str = REQUEST_PATH, session=None) -> dict:
    """Commit the request file. Needs a token with `contents: write`."""
    s = session or http
    body = {
        "message": f"scan request {req.request_id}"
                   + (f" ({', '.join(req.sports)})" if req.sports else ""),
        "content": base64.b64encode(req.to_json().encode()).decode(),
        "branch": branch,
    }
    sha = get_file_sha(repo, path, token, branch, session=s)
    if sha:
        body["sha"] = sha
    r = s.put(f"{API}/repos/{repo}/contents/{path}", json=body,
              headers=_headers(token), timeout=20)
    r.raise_for_status()
    return r.json() or {}
