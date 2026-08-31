from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TextFragment:
    """One textual element recovered from a structured document derivative."""

    text: str
    fragment_index: int
    page_no: int | None = None
    bbox: Any | None = None


@dataclass(frozen=True, slots=True)
class ExtractedField:
    """A proposed document field with bounded source provenance."""

    value: str | int | float | None
    extraction_method: str
    fragment_index: int | None = None
    page_no: int | None = None
    source_excerpt: str | None = None


@dataclass(frozen=True, slots=True)
class AccountingDocumentObservation:
    """Private, reviewable interpretation of one evidence document.

    This object is not an accounting fact. It may inform candidate matching, but
    canonical transaction semantics remain downstream in Accounting Workflows.
    """

    evidence_id: str
    conversion_id: str
    document_kind: str
    issuer: str | None
    currency: str | None
    amount: float | None
    document_date: str | None
    payment_date: str | None
    due_date: str | None
    external_reference: str | None
    account_reference: str | None
    period: str | None
    parser_confidence_grade: str | None
    review_required: bool
    review_reasons: tuple[str, ...] = ()
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    classifier_version: str = "accounting-doc-rules-v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["review_reasons"] = list(self.review_reasons)
        return payload
