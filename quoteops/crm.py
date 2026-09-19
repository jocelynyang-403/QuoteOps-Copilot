"""Simulated CRM persistence.

``approve_and_persist()`` is the only function in this codebase that writes to
``crm_opportunities``. No other module imports ``sqlite3``.

Two failure modes are handled deliberately differently, and the difference is
the point:

* An **illegal state transition** — approving a draft that has no price, such
  as a ``REVIEW_REQUIRED`` draft — raises. It is an operation that must never
  be attempted, so it must be loud. See ``workflow.approve_draft`` and
  ``workflow.IllegalTransitionError``.
* A **duplicate approval** — the same ``request_id`` approved twice — returns
  quietly with the existing row. A repeated approval is a replay of a
  legitimate event, a webhook redelivery or a double-clicked button, not an
  illegal operation. The system recognizes it and declines to act.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import audit
from .models import ApprovalDecision, QuoteDraft, WorkflowStatus

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "storage" / "quoteops.db"

TABLE_NAME = "crm_opportunities"

SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_opportunities (
  opportunity_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  draft_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  final_total_usd TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  approved_at TEXT NOT NULL
);
"""

#: Statuses a draft may hold and still be eligible for human approval.
APPROVABLE_DRAFT_STATUSES = (
    WorkflowStatus.DRAFT_READY,
    WorkflowStatus.MANAGER_REVIEW,
)


class UnapprovedWriteError(RuntimeError):
    """Raised when anything other than an approved decision tries to persist."""


def _resolve(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_DB_PATH


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = _resolve(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: Path | str | None = None) -> None:
    with _connect(db_path) as connection:
        connection.executescript(SCHEMA)


def count_opportunities(db_path: Path | str | None = None) -> int:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(f"SELECT COUNT(*) AS n FROM {TABLE_NAME}").fetchone()
    return int(row["n"])


def get_opportunity(request_id: str, db_path: Path | str | None = None) -> dict | None:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE request_id = ?", (request_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def approve_and_persist(
    decision: ApprovalDecision,
    draft: QuoteDraft,
    *,
    db_path: Path | str | None = None,
    audit_path: Path | str | None = None,
) -> dict:
    """The sole write path into ``crm_opportunities``.

    Accepts only an ``ApprovalDecision`` whose final status is ``APPROVED``.
    Every other state raises ``UnapprovedWriteError`` before touching the
    database.
    """
    init_db(db_path)

    if decision.decision != "approve":
        raise UnapprovedWriteError(
            f"decision must be 'approve' to persist, got {decision.decision!r}"
        )
    if decision.final_status is not WorkflowStatus.APPROVED:
        raise UnapprovedWriteError(
            f"final_status must be APPROVED to persist, got "
            f"{getattr(decision.final_status, 'value', decision.final_status)!r}"
        )
    if decision.draft_id != draft.draft_id:
        raise UnapprovedWriteError("decision.draft_id does not reference this draft")
    if draft.status not in APPROVABLE_DRAFT_STATUSES:
        raise UnapprovedWriteError(
            f"draft status {draft.status.value} is not approvable"
        )
    if draft.sku is None or draft.quantity is None or draft.final_total_usd is None:
        raise UnapprovedWriteError("cannot persist a draft without sku, quantity, and final total")

    existing = get_opportunity(draft.request_id, db_path)
    if existing is not None:
        # Replay, not violation. Return the existing row untouched.
        audit.append_event(
            request_id=draft.request_id,
            event="duplicate_approval_ignored",
            from_status=WorkflowStatus.APPROVED,
            to_status=WorkflowStatus.APPROVED,
            actor="system",
            detail=(
                f"request_id {draft.request_id} already persisted as "
                f"{existing['opportunity_id']}; no second row created"
            ),
            path=audit_path,
        )
        return existing

    row = {
        "opportunity_id": decision.decision_id,
        "request_id": draft.request_id,
        "draft_id": draft.draft_id,
        "sku": draft.sku,
        "quantity": draft.quantity,
        # Money is stored as TEXT so the Decimal value survives the round trip
        # exactly. SQLite REAL would reintroduce binary floating point.
        "final_total_usd": str(draft.final_total_usd),
        "approved_by": decision.reviewer,
        "approved_at": decision.decided_at.isoformat(),
    }

    with _connect(db_path) as connection:
        connection.execute(
            f"INSERT INTO {TABLE_NAME} "
            "(opportunity_id, request_id, draft_id, sku, quantity, "
            "final_total_usd, approved_by, approved_at) "
            "VALUES (:opportunity_id, :request_id, :draft_id, :sku, :quantity, "
            ":final_total_usd, :approved_by, :approved_at)",
            row,
        )

    return row
