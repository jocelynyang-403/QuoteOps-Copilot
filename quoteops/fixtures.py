"""Fixture-backed candidate loading.

``load_fixture`` is the only source of ``QuoteRequest`` objects in this
version. The LLM boundary is declared by ``QuoteRequest.source`` and in the
README, but nothing here calls a model.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import QuoteRequest
from .rules import Rules, load_rules

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INQUIRIES_DIR = REPO_ROOT / "data" / "inquiries"


def load_fixture(
    request_id: str,
    *,
    inquiries_dir: Path | str | None = None,
    rules: Rules | None = None,
) -> QuoteRequest:
    """Read ``data/inquiries/<request_id>.json`` into a validated QuoteRequest."""
    directory = Path(inquiries_dir) if inquiries_dir is not None else DEFAULT_INQUIRIES_DIR
    fixture_path = directory / f"{request_id}.json"
    if not fixture_path.exists():
        raise FileNotFoundError(f"no fixture for request_id {request_id!r} at {fixture_path}")

    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    # missing_fields is computed here, never read from the file. A candidate's
    # own account of what it is missing is not evidence.
    active_rules = rules if rules is not None else load_rules()
    payload.pop("missing_fields", None)
    request = QuoteRequest.model_validate(payload)
    request.missing_fields = [
        field_name
        for field_name in active_rules.required_quote_fields
        if getattr(request, field_name, None) is None
    ]
    return request
