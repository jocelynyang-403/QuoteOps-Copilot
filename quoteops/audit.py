"""Append-only JSONL audit log.

Append-only is enforced structurally: the log is opened with mode ``"a"`` and
this module exposes no update, rewrite, or delete operation. There is no code
path anywhere in the package that modifies a line once it has been written.

Every line carries the same seven keys, with ``null`` where a key does not
apply, so the file stays uniformly parseable by anything that reads it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .models import WorkflowStatus

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_PATH = REPO_ROOT / "storage" / "audit.jsonl"

AUDIT_KEYS = (
    "ts",
    "request_id",
    "event",
    "from_status",
    "to_status",
    "actor",
    "detail",
)

EventName = Literal[
    "state_transition",
    "human_decision",
    "duplicate_approval_ignored",
]
Actor = Literal["system", "human"]


def _status_value(status: WorkflowStatus | str | None) -> str | None:
    if status is None:
        return None
    if isinstance(status, WorkflowStatus):
        return status.value
    return str(status)


def append_event(
    *,
    request_id: str,
    event: EventName,
    actor: Actor,
    detail: str,
    from_status: WorkflowStatus | str | None = None,
    to_status: WorkflowStatus | str | None = None,
    path: Path | str | None = None,
) -> dict:
    """Append exactly one JSONL line and return the entry written."""
    audit_path = Path(path) if path is not None else DEFAULT_AUDIT_PATH
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "event": event,
        "from_status": _status_value(from_status),
        "to_status": _status_value(to_status),
        "actor": actor,
        "detail": detail,
    }

    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")

    return entry


def read_events(path: Path | str | None = None) -> list[dict]:
    """Read the log back. Read-only; provided for tests and the UI panel."""
    audit_path = Path(path) if path is not None else DEFAULT_AUDIT_PATH
    if not audit_path.exists():
        return []
    with audit_path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
