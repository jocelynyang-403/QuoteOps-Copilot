"""Pydantic data contracts for QuoteOps Copilot.

Money rule: every monetary value in this codebase is ``decimal.Decimal``.
Floating-point types are never used for currency, because binary floats cannot
represent ordinary cent values exactly and the resulting drift would be a
priced commitment to a customer.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkflowStatus(str, Enum):
    """Section 2.1 of the plan. Only APPROVED may write to CRM."""

    RECEIVED = "RECEIVED"
    EXTRACTED = "EXTRACTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    MANAGER_REVIEW = "MANAGER_REVIEW"
    DRAFT_READY = "DRAFT_READY"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


#: The only statuses a QuoteDraft may hold.
DRAFT_STATUSES = (
    WorkflowStatus.REVIEW_REQUIRED,
    WorkflowStatus.MANAGER_REVIEW,
    WorkflowStatus.DRAFT_READY,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class QuoteRequest(BaseModel):
    """Section 2.2.

    The extraction signals are nullable on purpose so the system can stop
    safely and ask a question, rather than failing validation before it has a
    chance to generate a clarification.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str
    raw_email: str
    customer_name: str | None = None
    sku: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    region: str | None = None
    delivery_date: date | None = None
    discount_request_pct: Decimal | None = Field(default=None, ge=0, le=100)
    missing_fields: list[str] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    source: Literal["fixture", "llm"]


class PriceRecord(BaseModel):
    """Section 2.5.

    A price record is resolved by the caller and handed to ``pricing`` already
    chosen. Selecting between competing records is Phase 2's job.
    """

    model_config = ConfigDict(extra="forbid")

    sku: str
    unit_price_usd: Decimal
    region: str
    effective_from: date
    effective_to: date | None = None
    source_id: str


class QuoteDraft(BaseModel):
    """Section 2.3."""

    model_config = ConfigDict(extra="forbid")

    draft_id: str
    request_id: str
    status: WorkflowStatus
    sku: str | None = None
    quantity: int | None = None
    unit_price_usd: Decimal | None = None
    subtotal_usd: Decimal | None = None
    approved_discount_pct: Decimal = Decimal("0")
    final_total_usd: Decimal | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_summary: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    review_reasons: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _enforce_draft_rules(self) -> QuoteDraft:
        if self.status not in DRAFT_STATUSES:
            raise ValueError(
                f"QuoteDraft.status must be one of "
                f"{[s.value for s in DRAFT_STATUSES]}, got {self.status.value}"
            )
        if self.status is WorkflowStatus.DRAFT_READY and not self.evidence_ids:
            raise ValueError("evidence_ids cannot be empty for DRAFT_READY")
        if self.status is WorkflowStatus.REVIEW_REQUIRED and not self.clarification_question:
            raise ValueError("clarification_question is required for REVIEW_REQUIRED")
        return self


class ApprovalDecision(BaseModel):
    """Section 2.4.

    Only a human UI selection constructs this. No automatic workflow code may
    build one with ``decision="approve"``.
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    draft_id: str
    decision: Literal["approve", "reject", "request_clarification"]
    reviewer: str
    reviewer_note: str | None = None
    decided_at: datetime = Field(default_factory=_utcnow)
    final_status: WorkflowStatus

    @model_validator(mode="after")
    def _enforce_decision_mapping(self) -> ApprovalDecision:
        expected = {
            "approve": WorkflowStatus.APPROVED,
            "reject": WorkflowStatus.REJECTED,
            "request_clarification": WorkflowStatus.REVIEW_REQUIRED,
        }[self.decision]
        if self.final_status is not expected:
            raise ValueError(
                f"decision={self.decision!r} must map to final_status="
                f"{expected.value}, got {self.final_status.value}"
            )
        return self
