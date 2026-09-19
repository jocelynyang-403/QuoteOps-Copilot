"""Deterministic price, discount, and approval-threshold logic.

This module does not look up prices. It receives a ``PriceRecord`` that the
caller has already resolved and turns it into money. Choosing between
competing price records — effectivity, region, version — is Phase 2's job in
``retrieval.py``.

All arithmetic here is ``Decimal``. No float touches a currency value.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import PriceRecord, QuoteDraft, QuoteRequest, WorkflowStatus
from .rules import Rules

CENTS = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def draft_id_for(request_id: str) -> str:
    return f"DRAFT-{request_id}"


def _evidence_line(price_record: PriceRecord, rules: Rules) -> str:
    effective_to = price_record.effective_to.isoformat() if price_record.effective_to else "open"
    return (
        f"{price_record.source_id}: {price_record.sku} "
        f"region {price_record.region} "
        f"effective {price_record.effective_from.isoformat()} to {effective_to} "
        f"unit {price_record.unit_price_usd} {rules.pricing.currency}"
    )


def build_priced_draft(
    request: QuoteRequest,
    price_record: PriceRecord,
    rules: Rules,
) -> QuoteDraft:
    """Compute amounts and apply approval thresholds for a quotable request."""
    if request.quantity is None:
        raise ValueError("build_priced_draft requires a quantity; caller must screen missing fields first")

    unit_price = price_record.unit_price_usd
    subtotal = _money(unit_price * Decimal(request.quantity))

    # A customer's requested discount is never granted automatically. The
    # approved discount starts at zero and only a human may change it.
    approved_discount_pct = Decimal("0")
    final_total = _money(subtotal - (subtotal * approved_discount_pct / Decimal("100")))

    review_reasons: list[str] = []
    discount_threshold = rules.approvals.manager_review_discount_gt_pct
    total_threshold = rules.approvals.manager_review_total_gt_usd

    if request.discount_request_pct is not None and request.discount_request_pct > discount_threshold:
        review_reasons.append(
            f"discount_request_pct {request.discount_request_pct} exceeds "
            f"manager_review_discount_gt_pct {discount_threshold}"
        )
    if final_total > total_threshold:
        review_reasons.append(
            f"final_total_usd {final_total} exceeds "
            f"manager_review_total_gt_usd {total_threshold}"
        )

    status = WorkflowStatus.MANAGER_REVIEW if review_reasons else WorkflowStatus.DRAFT_READY

    return QuoteDraft(
        draft_id=draft_id_for(request.request_id),
        request_id=request.request_id,
        status=status,
        sku=request.sku,
        quantity=request.quantity,
        unit_price_usd=unit_price,
        subtotal_usd=subtotal,
        approved_discount_pct=approved_discount_pct,
        final_total_usd=final_total,
        evidence_ids=[price_record.source_id],
        evidence_summary=[_evidence_line(price_record, rules)],
        review_reasons=review_reasons,
    )
