"""Explicit state machine and orchestration.

``PERMITTED_TRANSITIONS`` is the whole of section 2.1 and nothing else. There
is no path from ``REVIEW_REQUIRED`` to ``APPROVED``: a review-required draft
has no price, so there is nothing to approve.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import audit, crm, pricing
from .models import (
    ApprovalDecision,
    PriceRecord,
    QuoteDraft,
    QuoteRequest,
    WorkflowStatus,
)
from .rules import Rules

PERMITTED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.RECEIVED: frozenset({WorkflowStatus.EXTRACTED}),
    WorkflowStatus.EXTRACTED: frozenset(
        {
            WorkflowStatus.REVIEW_REQUIRED,
            WorkflowStatus.MANAGER_REVIEW,
            WorkflowStatus.DRAFT_READY,
        }
    ),
    WorkflowStatus.REVIEW_REQUIRED: frozenset({WorkflowStatus.REJECTED}),
    WorkflowStatus.DRAFT_READY: frozenset(
        {WorkflowStatus.APPROVED, WorkflowStatus.REJECTED}
    ),
    WorkflowStatus.MANAGER_REVIEW: frozenset(
        {WorkflowStatus.APPROVED, WorkflowStatus.REJECTED}
    ),
    # APPROVED is terminal in the state machine; what follows is persistence.
    WorkflowStatus.APPROVED: frozenset(),
    WorkflowStatus.REJECTED: frozenset(),
}


class IllegalTransitionError(RuntimeError):
    """Raised when a caller attempts a transition the plan does not permit.

    This raises rather than returning False because an illegal transition is an
    operation that must never be attempted. Contrast with a duplicate approval,
    which returns quietly because it is a replay of a legitimate event rather
    than an illegal one. See ``crm.approve_and_persist``.
    """


def assert_transition(from_status: WorkflowStatus, to_status: WorkflowStatus) -> None:
    allowed = PERMITTED_TRANSITIONS[from_status]
    if to_status not in allowed:
        raise IllegalTransitionError(
            f"{from_status.value} -> {to_status.value} is not a permitted transition; "
            f"permitted from {from_status.value}: "
            f"{sorted(status.value for status in allowed) or 'none'}"
        )


def missing_required_fields(request: QuoteRequest, rules: Rules) -> list[str]:
    """Recomputed from the request itself; a self-reported list is never trusted."""
    return [
        field_name
        for field_name in rules.required_quote_fields
        if getattr(request, field_name, None) is None
    ]


def process_request(
    request: QuoteRequest,
    rules: Rules,
    price_record: PriceRecord | None = None,
    *,
    audit_path: Path | str | None = None,
) -> QuoteDraft:
    """RECEIVED -> EXTRACTED -> one of the three draft states."""
    assert_transition(WorkflowStatus.RECEIVED, WorkflowStatus.EXTRACTED)
    audit.append_event(
        request_id=request.request_id,
        event="state_transition",
        from_status=WorkflowStatus.RECEIVED,
        to_status=WorkflowStatus.EXTRACTED,
        actor="system",
        detail=f"candidate loaded from {request.source}",
        path=audit_path,
    )

    missing = missing_required_fields(request, rules)
    if missing:
        clarification = rules.clarification_for(missing[0])
        if clarification is None:
            raise ValueError(f"no clarification template configured for {missing[0]!r}")
        draft = QuoteDraft(
            draft_id=pricing.draft_id_for(request.request_id),
            request_id=request.request_id,
            status=WorkflowStatus.REVIEW_REQUIRED,
            sku=request.sku,
            quantity=request.quantity,
            clarification_question=clarification,
            review_reasons=[f"missing required field: {field}" for field in missing],
        )
    else:
        if price_record is None:
            raise ValueError("a resolved PriceRecord is required to price a complete request")
        draft = pricing.build_priced_draft(request, price_record, rules)

    assert_transition(WorkflowStatus.EXTRACTED, draft.status)
    audit.append_event(
        request_id=request.request_id,
        event="state_transition",
        from_status=WorkflowStatus.EXTRACTED,
        to_status=draft.status,
        actor="system",
        detail="; ".join(draft.review_reasons) or "priced by deterministic rules",
        path=audit_path,
    )
    return draft


def approve_draft(
    draft: QuoteDraft,
    reviewer: str,
    *,
    reviewer_note: str | None = None,
    db_path: Path | str | None = None,
    audit_path: Path | str | None = None,
) -> dict:
    """The human approval path.

    Only an explicit human UI action calls this. It is the single place an
    ``ApprovalDecision`` with ``decision="approve"`` is constructed, and it is
    not reachable from automatic workflow code.
    """
    assert_transition(draft.status, WorkflowStatus.APPROVED)

    decision = ApprovalDecision(
        decision_id=str(uuid.uuid4()),
        draft_id=draft.draft_id,
        decision="approve",
        reviewer=reviewer,
        reviewer_note=reviewer_note,
        decided_at=datetime.now(timezone.utc),
        final_status=WorkflowStatus.APPROVED,
    )

    audit.append_event(
        request_id=draft.request_id,
        event="human_decision",
        from_status=draft.status,
        to_status=WorkflowStatus.APPROVED,
        actor="human",
        detail=f"approved by {reviewer}",
        path=audit_path,
    )

    return crm.approve_and_persist(
        decision, draft, db_path=db_path, audit_path=audit_path
    )
