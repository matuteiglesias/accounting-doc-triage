from __future__ import annotations

"""Deterministic first-pass accounting-document interpretation.

These rules deliberately distinguish a liability/bill from evidence that a payment
actually occurred. They are intended to produce reviewable observations, not
canonical accounting facts.
"""

from datetime import datetime
import re
from typing import Callable

from accounting_doc_triage.interpretation.docling_view import joined_text, text_fragments
from accounting_doc_triage.interpretation.model import (
    AccountingDocumentObservation,
    ExtractedField,
    TextFragment,
)


_ISSUER_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("aysa", ("agua y saneamientos argentinos", "aysa")),
    ("arba", ("agencia de recaudación", "agencia de recaudacion", "arba")),
    ("municipalidad_tigre", ("municipalidad de tigre", "tigre municipio")),
    ("edenor", ("empresa distribuidora y comercializadora norte", "edenor")),
    ("pagos360", ("pagos360",)),
    ("mercado_pago", ("mercado pago", "mercadopago")),
    ("banco_provincia", ("banco provincia",)),
)

_PAYMENT_MARKERS = (
    "comprobante de pago",
    "transacción se completó con éxito",
    "transaccion se completo con exito",
    "tu pago fue aprobado",
    "total pagado",
    "importe abonado",
    "fecha de pago",
)
_STATEMENT_MARKERS = (
    "resumen consolidado",
    "detalle del mes",
    "pago mínimo",
    "pago minimo",
    "saldo actual",
    "detalle de transaccion",
    "detalle de transacción",
)
_LIABILITY_MARKERS = (
    "liquidación de servicio público",
    "liquidacion de servicio publico",
    "liquidación de tasas",
    "liquidacion de tasas",
    "factura digital",
    "total a pagar",
    "vencimiento",
    "a pagar",
)

_AMOUNT_PATTERNS = (
    re.compile(r"(?:importe\s+abonado|total\s+pagado|monto)\s*[:$]?\s*\$?\s*([0-9][0-9.,]*)", re.I),
    re.compile(r"(?:importe\s+a\s+pagar|total\s+a\s+pagar|a\s+pagar)\s*[:$]?\s*\$?\s*([0-9][0-9.,]*)", re.I),
)
_PAYMENT_DATE_PATTERNS = (
    re.compile(r"fecha\s+de\s+pago\s*[:]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", re.I),
    re.compile(r"fecha\s+y\s+hora\s*[:]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", re.I),
)
_DUE_DATE_PATTERNS = (
    re.compile(r"(?:fecha\s+vto\.?|fecha\s+de\s+vencimiento|vencimiento)\s*[:]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", re.I),
)
_EXTERNAL_REF_PATTERNS = (
    re.compile(r"(?:id\s+de\s+transacci[oó]n|n[uú]mero\s+de\s+transacci[oó]n|transacci[oó]n)\s*[:#]?\s*([0-9A-Za-z-]{4,})", re.I),
    re.compile(r"(?:autorizaci[oó]n)\s*[:#]?\s*([0-9A-Za-z-]{3,})", re.I),
)
_ACCOUNT_REF_PATTERNS = (
    re.compile(r"(?:cuenta(?:\s+de\s+servicios)?|partida\s*n[º°]?|cliente)\s*[:nº°#]?\s*([0-9][0-9 .-]{3,})", re.I),
)
_PERIOD_PATTERNS = (
    re.compile(r"a[nñ]o\s*/\s*cuota\s*[:]?\s*(\d{4})\s*/\s*(\d{1,2})", re.I),
    re.compile(r"per[ií]odo\s*(?:/\s*cuotas?)?\s*[:]?\s*(\d{4})\s+(\d{1,4})", re.I),
)


def _normal(text: str) -> str:
    return " ".join(text.casefold().split())


def _parse_amount(raw: str) -> float | None:
    text = raw.strip().replace(" ", "")
    if not text:
        return None
    last_dot = text.rfind(".")
    last_comma = text.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        decimal = "." if last_dot > last_comma else ","
        thousands = "," if decimal == "." else "."
        text = text.replace(thousands, "")
        text = text.replace(decimal, ".")
    elif last_comma >= 0:
        suffix = text[last_comma + 1 :]
        text = text.replace(",", "." if len(suffix) == 2 else "")
    elif last_dot >= 0:
        suffix = text[last_dot + 1 :]
        if len(suffix) != 2:
            text = text.replace(".", "")
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _first_match(
    fragments: list[TextFragment],
    patterns: tuple[re.Pattern[str], ...],
    *,
    transform: Callable[[str], object] | None = None,
    method: str,
) -> ExtractedField | None:
    for fragment in fragments:
        for pattern in patterns:
            match = pattern.search(fragment.text)
            if not match:
                continue
            raw = match.group(1)
            value = transform(raw) if transform else raw.strip()
            if value is None:
                continue
            return ExtractedField(
                value=value,
                extraction_method=method,
                fragment_index=fragment.fragment_index,
                page_no=fragment.page_no,
                source_excerpt=fragment.text[:240],
            )
    # A label and its value may occupy adjacent Docling text elements. Use the
    # joined representation as a deterministic fallback but do not invent page
    # provenance when the exact source element is ambiguous.
    all_text = joined_text(fragments)
    for pattern in patterns:
        match = pattern.search(all_text)
        if match:
            raw = match.group(1)
            value = transform(raw) if transform else raw.strip()
            if value is not None:
                return ExtractedField(
                    value=value,
                    extraction_method=f"{method}:joined",
                    source_excerpt=match.group(0)[:240],
                )
    return None


def _issuer(text: str) -> str | None:
    normalized = _normal(text)
    for issuer, markers in _ISSUER_MARKERS:
        if any(marker in normalized for marker in markers):
            return issuer
    return None


def _score(text: str, markers: tuple[str, ...]) -> int:
    normalized = _normal(text)
    return sum(marker in normalized for marker in markers)


def _kind(text: str, issuer: str | None) -> str:
    payment = _score(text, _PAYMENT_MARKERS)
    statement = _score(text, _STATEMENT_MARKERS)
    liability = _score(text, _LIABILITY_MARKERS)

    # A positive-payment marker outranks generic bill language (payment proofs can
    # mention the underlying factura). Statements outrank generic liability terms.
    if payment >= 2 or any(
        marker in _normal(text)
        for marker in ("transacción se completó con éxito", "transaccion se completo con exito")
    ):
        if issuer in {"arba", "municipalidad_tigre"}:
            return "tax_payment_proof"
        if issuer in {"aysa", "edenor"}:
            return "utility_payment_proof"
        return "payment_proof"
    if statement >= 3:
        return "bank_or_card_statement"
    if liability >= 2:
        if issuer in {"arba", "municipalidad_tigre"}:
            return "tax_liability"
        if issuer in {"aysa", "edenor"}:
            return "utility_liability"
        return "liability"
    return "other"


def interpret_docling_document(
    document_payload: dict,
    *,
    evidence_id: str,
    conversion_id: str,
    parser_confidence_grade: str | None = None,
) -> AccountingDocumentObservation:
    fragments = text_fragments(document_payload)
    text = joined_text(fragments)
    issuer = _issuer(text)
    kind = _kind(text, issuer)

    fields: dict[str, ExtractedField] = {}
    amount_field = _first_match(
        fragments, _AMOUNT_PATTERNS, transform=_parse_amount, method="amount-regex-v1"
    )
    payment_date_field = _first_match(
        fragments,
        _PAYMENT_DATE_PATTERNS,
        transform=_parse_date,
        method="payment-date-regex-v1",
    )
    due_date_field = _first_match(
        fragments,
        _DUE_DATE_PATTERNS,
        transform=_parse_date,
        method="due-date-regex-v1",
    )
    external_ref_field = _first_match(
        fragments,
        _EXTERNAL_REF_PATTERNS,
        method="external-reference-regex-v1",
    )
    account_ref_field = _first_match(
        fragments,
        _ACCOUNT_REF_PATTERNS,
        method="account-reference-regex-v1",
    )

    period_field: ExtractedField | None = None
    for fragment in fragments:
        for pattern in _PERIOD_PATTERNS:
            match = pattern.search(fragment.text)
            if match:
                period_field = ExtractedField(
                    value=f"{match.group(1)}-{int(match.group(2)):02d}",
                    extraction_method="period-regex-v1",
                    fragment_index=fragment.fragment_index,
                    page_no=fragment.page_no,
                    source_excerpt=fragment.text[:240],
                )
                break
        if period_field:
            break

    for name, field_value in (
        ("amount", amount_field),
        ("payment_date", payment_date_field),
        ("due_date", due_date_field),
        ("external_reference", external_ref_field),
        ("account_reference", account_ref_field),
        ("period", period_field),
    ):
        if field_value is not None:
            fields[name] = field_value

    reasons: list[str] = []
    if kind == "other":
        reasons.append("document_kind_unknown")
    if parser_confidence_grade in {"poor", "fair"}:
        reasons.append(f"parser_confidence_{parser_confidence_grade}")
    if kind.endswith("payment_proof") or kind == "payment_proof":
        if amount_field is None:
            reasons.append("payment_amount_missing")
        if payment_date_field is None:
            reasons.append("payment_date_missing")
    if kind.endswith("liability") or kind == "liability":
        if amount_field is None:
            reasons.append("liability_amount_missing")
        if due_date_field is None:
            reasons.append("due_date_missing")

    payment_date = str(payment_date_field.value) if payment_date_field else None
    due_date = str(due_date_field.value) if due_date_field else None
    document_date = payment_date or due_date
    return AccountingDocumentObservation(
        evidence_id=evidence_id,
        conversion_id=conversion_id,
        document_kind=kind,
        issuer=issuer,
        currency="ARS" if amount_field else None,
        amount=float(amount_field.value) if amount_field else None,
        document_date=document_date,
        payment_date=payment_date,
        due_date=due_date,
        external_reference=(
            str(external_ref_field.value) if external_ref_field else None
        ),
        account_reference=(
            str(account_ref_field.value).strip() if account_ref_field else None
        ),
        period=str(period_field.value) if period_field else None,
        parser_confidence_grade=parser_confidence_grade,
        review_required=bool(reasons),
        review_reasons=tuple(reasons),
        fields=fields,
    )
