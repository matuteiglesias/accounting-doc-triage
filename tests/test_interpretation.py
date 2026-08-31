from __future__ import annotations

from accounting_doc_triage.interpretation.rules import interpret_docling_document


def _doc(*texts: str):
    return {
        "texts": [
            {
                "text": text,
                "prov": [{"page_no": 1, "bbox": {"l": 1, "t": 2, "r": 3, "b": 4}}],
            }
            for text in texts
        ]
    }


def test_tax_payment_proof_extracts_amount_date_reference_and_provenance() -> None:
    observation = interpret_docling_document(
        _doc(
            "ARBA AGENCIA DE RECAUDACION",
            "COMPROBANTE DE PAGO Impuesto Inmobiliario",
            "14/04/2026 14:40",
            "Transacción: TX921650537",
            "Importe Abonado: 74.513,10",
            "Fecha de pago: 14/04/2026",
            "Año / Cuota: 2026 / 02",
        ),
        evidence_id="e" * 64,
        conversion_id="docling-test",
        parser_confidence_grade="excellent",
    )
    assert observation.document_kind == "tax_payment_proof"
    assert observation.issuer == "arba"
    assert observation.amount == 74513.10
    assert observation.payment_date == "2026-04-14"
    assert observation.external_reference == "TX921650537"
    assert observation.period == "2026-02"
    assert observation.review_required is False
    assert observation.fields["amount"].page_no == 1
    assert observation.fields["amount"].source_excerpt


def test_utility_payment_proof_accepts_dot_thousands_comma_decimal() -> None:
    observation = interpret_docling_document(
        _doc(
            "Agua y Saneamientos Argentinos S.A. AYSA",
            "La transacción se completó con éxito.",
            "Id de Transacción 850400000001",
            "Fecha y Hora 09/07/2026 - 14:00:38",
            "Monto $ 120.066,58",
        ),
        evidence_id="a" * 64,
        conversion_id="docling-test",
        parser_confidence_grade="good",
    )
    assert observation.document_kind == "utility_payment_proof"
    assert observation.amount == 120066.58
    assert observation.payment_date == "2026-07-09"
    assert observation.external_reference == "850400000001"


def test_liability_is_not_misclassified_as_payment_proof() -> None:
    observation = interpret_docling_document(
        _doc(
            "MUNICIPALIDAD DE TIGRE",
            "LIQUIDACIÓN DE TASAS Y DERECHOS MUNICIPALES",
            "Vencimiento 15/09/2026 Importe a pagar $68,047.00",
            "El pago con este comprobante sólo es válido con el ticket adjunto.",
        ),
        evidence_id="b" * 64,
        conversion_id="docling-test",
        parser_confidence_grade="excellent",
    )
    assert observation.document_kind == "tax_liability"
    assert observation.amount == 68047.0
    assert observation.due_date == "2026-09-15"
    assert observation.payment_date is None


def test_card_statement_is_context_not_payment_proof() -> None:
    observation = interpret_docling_document(
        _doc(
            "Banco Provincia VISA PLATINUM",
            "RESUMEN CONSOLIDADO PESOS DOLARES",
            "SALDO ACTUAL 167646,17",
            "PAGO MINIMO 18430,00",
            "DETALLE DEL MES FECHA NRO CUPON PESOS DOLARES",
        ),
        evidence_id="c" * 64,
        conversion_id="docling-test",
        parser_confidence_grade="good",
    )
    assert observation.document_kind == "bank_or_card_statement"
    assert observation.issuer == "banco_provincia"
    assert observation.review_required is False


def test_low_parser_confidence_routes_even_known_document_to_review() -> None:
    observation = interpret_docling_document(
        _doc(
            "COMPROBANTE DE PAGO",
            "Tu pago fue aprobado",
            "Importe Abonado $ 8.840,00",
            "Fecha de pago 30/06/2026",
        ),
        evidence_id="d" * 64,
        conversion_id="docling-test",
        parser_confidence_grade="fair",
    )
    assert observation.document_kind == "payment_proof"
    assert observation.review_required is True
    assert "parser_confidence_fair" in observation.review_reasons
