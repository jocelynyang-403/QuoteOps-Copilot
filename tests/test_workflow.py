"""State machine tests."""

import pytest

from quoteops import crm, workflow
from quoteops.fixtures import load_fixture
from quoteops.models import WorkflowStatus
from quoteops.rules import load_rules


def test_approve_from_review_required_raises(tmp_path):
    """An illegal transition raises. It does not return False.

    A REVIEW_REQUIRED draft has no price, so approving it is not a decision the
    system is entitled to refuse quietly — it is an operation that must never
    be attempted. Contrast test_double_approve_creates_one_row, where a
    repeated approval returns quietly because it is a replay of a legitimate
    event rather than an illegal one.
    """
    db_path = tmp_path / "quoteops.db"
    audit_path = tmp_path / "audit.jsonl"

    rules = load_rules()
    request = load_fixture("F01", rules=rules)
    draft = workflow.process_request(request, rules, audit_path=audit_path)

    assert draft.status is WorkflowStatus.REVIEW_REQUIRED
    assert draft.clarification_question
    assert draft.unit_price_usd is None
    assert draft.final_total_usd is None

    with pytest.raises(workflow.IllegalTransitionError):
        workflow.approve_draft(
            draft,
            reviewer="sales-manager-demo",
            db_path=db_path,
            audit_path=audit_path,
        )

    assert crm.count_opportunities(db_path) == 0
