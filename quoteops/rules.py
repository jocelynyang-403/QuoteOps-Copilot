"""Loads and validates ``data/rules.json``.

Every threshold, permission, and table name the system obeys is read from the
rules file through this module. Nothing downstream hardcodes them.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES_PATH = REPO_ROOT / "data" / "rules.json"


class RegionClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ambiguous_terms: list[str]


class PricingRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    only_active_price_books: bool
    require_exact_region_for_regional_price: bool


class ApprovalRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Decimal, not float: these are compared against money.
    manager_review_discount_gt_pct: Decimal
    manager_review_total_gt_usd: Decimal
    never_auto_approve: bool


class SecurityRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_body_role: str
    note: str


class CrmRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    write_requires_status: str


class Rules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_quote_fields: list[str]
    recognized_regions: list[str]
    clarification_templates: dict[str, str]
    region_clarification: RegionClarification
    pricing: PricingRules
    approvals: ApprovalRules
    security: SecurityRules
    crm: CrmRules

    def clarification_for(self, field_name: str) -> str | None:
        """Static template for a missing field. No LLM, no retrieval."""
        return self.clarification_templates.get(field_name)


def load_rules(path: Path | str | None = None) -> Rules:
    rules_path = Path(path) if path is not None else DEFAULT_RULES_PATH
    return Rules.model_validate(json.loads(rules_path.read_text(encoding="utf-8")))
