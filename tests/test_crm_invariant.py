"""CRM invariant and idempotency tests.

The invariant: no record that has not been explicitly approved by a human may
ever be written to crm_opportunities.
"""

from datetime import date
from decimal import Decimal

import pytest

from quoteops import audit, crm, workflow
from quoteops.fixtures import load_fixture
from quoteops.models import ApprovalDecision, PriceRecord, WorkflowStatus
from quoteops.rules import load_rules

# Block 1 constructs the resolved price record inline. Resolving it from a
# price book is Phase 2's responsibility.
PUMP_X200_Q1_2026 = PriceRecord(
    sku="PUMP-X200",
    unit_price_usd=Decimal("4750.00"),
    region="US-NE",
    effective_from=date(2026, 1, 1),
    effective_to=None,
    source_id="PRICE-2026-Q1",
)


def _draft_ready_n01(audit_path):
    rules = load_rules()
    request = load_fixture("N01", rules=rules)
    draft = workflow.process_request(
        request, rules, PUMP_X200_Q1_2026, audit_path=audit_path
    )
    assert draft.status is WorkflowStatus.DRAFT_READY
    return draft


def test_discount_above_threshold_enters_manager_review(tmp_path):
    """F03: a discount above the rules.json threshold escalates, never auto-approves.

    The threshold is asserted against the loaded rules value rather than a
    literal, so raising or lowering manager_review_discount_gt_pct in
    data/rules.json moves this test with it instead of leaving it stale.
    """
    audit_path = tmp_path / "audit.jsonl"

    rules = load_rules()
    threshold = rules.approvals.manager_review_discount_gt_pct
    quotable = load_fixture("N01", rules=rules)

    above = quotable.model_copy(
        update={
            "request_id": "F03",
            "discount_request_pct": threshold + Decimal("5"),
        }
    )
    draft = workflow.process_request(
        above, rules, PUMP_X200_Q1_2026, audit_path=audit_path
    )

    assert draft.status is WorkflowStatus.MANAGER_REVIEW
    assert draft.status is not WorkflowStatus.DRAFT_READY
    # The escalation is attributable to the discount, not to the separate
    # manager_review_total_gt_usd threshold.
    assert draft.final_total_usd < rules.approvals.manager_review_total_gt_usd
    assert any(
        f"manager_review_discount_gt_pct {threshold}" in reason
        for reason in draft.review_reasons
    )
    # A request is never granted automatically, even when it escalates.
    assert draft.approved_discount_pct == Decimal("0")

    # The same request at the threshold does not escalate: the comparison is
    # strictly greater-than against the configured value.
    at_threshold = quotable.model_copy(
        update={"discount_request_pct": threshold}
    )
    assert (
        workflow.process_request(
            at_threshold, rules, PUMP_X200_Q1_2026, audit_path=audit_path
        ).status
        is WorkflowStatus.DRAFT_READY
    )


def test_unapproved_record_never_writes_to_crm_opportunities(tmp_path):
    db_path = tmp_path / "quoteops.db"
    audit_path = tmp_path / "audit.jsonl"

    draft = _draft_ready_n01(audit_path)

    unapproved_statuses = [
        status for status in WorkflowStatus if status is not WorkflowStatus.APPROVED
    ]
    assert len(unapproved_statuses) == 6

    for status in unapproved_statuses:
        # model_construct bypasses field validation on purpose: this asserts
        # that approve_and_persist guards the write itself, rather than relying
        # on the schema to have screened the decision earlier.
        forged = ApprovalDecision.model_construct(
            decision_id=f"forged-{status.value}",
            draft_id=draft.draft_id,
            decision="approve",
            reviewer="sales-manager-demo",
            reviewer_note=None,
            decided_at=draft.generated_at,
            final_status=status,
        )
        with pytest.raises(crm.UnapprovedWriteError):
            crm.approve_and_persist(
                forged, draft, db_path=db_path, audit_path=audit_path
            )
        assert crm.count_opportunities(db_path) == 0

    # The two decisions a human can legitimately make that are not approvals.
    for decision_name, final_status in (
        ("reject", WorkflowStatus.REJECTED),
        ("request_clarification", WorkflowStatus.REVIEW_REQUIRED),
    ):
        decision = ApprovalDecision(
            decision_id=f"decision-{decision_name}",
            draft_id=draft.draft_id,
            decision=decision_name,
            reviewer="sales-manager-demo",
            final_status=final_status,
        )
        with pytest.raises(crm.UnapprovedWriteError):
            crm.approve_and_persist(
                decision, draft, db_path=db_path, audit_path=audit_path
            )
        assert crm.count_opportunities(db_path) == 0

    assert crm.count_opportunities(db_path) == 0


def test_double_approve_creates_one_row(tmp_path):
    """A duplicate approval is a replay, not a violation.

    The second call returns the existing row unchanged and records a
    duplicate_approval_ignored audit entry. It does not raise, because a
    webhook redelivery or a double-clicked button is a repeated legitimate
    event rather than an illegal operation. Contrast
    test_approve_from_review_required_raises, which does raise.
    """
    db_path = tmp_path / "quoteops.db"
    audit_path = tmp_path / "audit.jsonl"

    draft = _draft_ready_n01(audit_path)

    first = workflow.approve_draft(
        draft, reviewer="sales-manager-demo", db_path=db_path, audit_path=audit_path
    )
    assert crm.count_opportunities(db_path) == 1

    second = workflow.approve_draft(
        draft, reviewer="sales-manager-demo", db_path=db_path, audit_path=audit_path
    )

    assert crm.count_opportunities(db_path) == 1
    assert second == first

    events = audit.read_events(audit_path)
    assert any(event["event"] == "duplicate_approval_ignored" for event in events)
