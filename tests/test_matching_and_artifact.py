from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from accounting_doc_triage.evidence_artifact import (
    EvidenceArtifactError,
    apply_review_decisions,
    write_transaction_evidence_artifact,
)
from accounting_doc_triage.intake.custody import EvidenceRecord
from accounting_doc_triage.interpretation.model import AccountingDocumentObservation
from accounting_doc_triage.matching import MatchConfig, candidate_matches


def _observation(**overrides) -> AccountingDocumentObservation:
    values = dict(
        evidence_id="a" * 64,
        conversion_id="docling-test",
        document_kind="tax_payment_proof",
        issuer="arba",
        currency="ARS",
        amount=74513.10,
        document_date="2026-04-14",
        payment_date="2026-04-14",
        due_date=None,
        external_reference="TX-1",
        account_reference="0001",
        period="2026-02",
        parser_confidence_grade="excellent",
        review_required=False,
    )
    values.update(overrides)
    return AccountingDocumentObservation(**values)


def _ledger() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "tx_id": "tx-right",
                "Date": "2026-04-14",
                "amount": 74513.10,
                "Currency": "ARS",
                "notes": "synthetic tax payment",
            },
            {
                "tx_id": "tx-wrong-amount",
                "Date": "2026-04-14",
                "amount": 100.00,
                "Currency": "ARS",
                "notes": "other",
            },
            {
                "tx_id": "tx-wrong-date",
                "Date": "2026-05-20",
                "amount": -74513.10,
                "Currency": "ARS",
                "notes": "other",
            },
        ]
    )


def test_payment_proof_matching_is_conservative_and_review_only() -> None:
    candidates = candidate_matches(_observation(), _ledger())
    assert candidates["candidate_tx_id"].tolist() == ["tx-right"]
    assert candidates.iloc[0]["match_status"] == "unique_candidate"
    assert candidates.iloc[0]["relation"] == "payment_proof"
    assert "amount_exact" in candidates.iloc[0]["match_reasons"]

    unresolved = apply_review_decisions(candidates)
    assert unresolved.iloc[0]["status"] == "candidate"

    decisions = pd.DataFrame(
        [
            {
                "evidence_id": "a" * 64,
                "candidate_tx_id": "tx-right",
                "relation": "payment_proof",
                "decision": "approved",
            }
        ]
    )
    approved = apply_review_decisions(candidates, decisions)
    assert approved.to_dict("records") == [
        {
            "tx_id": "tx-right",
            "evidence_id": "a" * 64,
            "relation": "payment_proof",
            "status": "approved",
        }
    ]


def test_ambiguous_same_amount_date_is_preserved_for_human_review() -> None:
    ledger = pd.concat(
        [
            _ledger().iloc[[0]],
            pd.DataFrame(
                [
                    {
                        "tx_id": "tx-also-plausible",
                        "Date": "2026-04-15",
                        "amount": -74513.10,
                        "Currency": "ARS",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    candidates = candidate_matches(_observation(), ledger, config=MatchConfig(date_window_days=2))
    assert len(candidates) == 2
    assert set(candidates["match_status"]) == {"ambiguous_candidate"}


def test_liability_and_statement_do_not_auto_match_payment_transactions() -> None:
    for kind in ("tax_liability", "utility_liability", "bank_or_card_statement"):
        observation = _observation(document_kind=kind, payment_date=None)
        assert candidate_matches(observation, _ledger()).empty


def test_review_cannot_invent_relationship() -> None:
    candidates = candidate_matches(_observation(), _ledger())
    bad = pd.DataFrame(
        [
            {
                "evidence_id": "a" * 64,
                "candidate_tx_id": "not-a-candidate",
                "relation": "payment_proof",
                "decision": "approved",
            }
        ]
    )
    with pytest.raises(EvidenceArtifactError, match="does not resolve"):
        apply_review_decisions(candidates, bad)


def test_approved_relation_materializes_contract_consumed_by_accounting_workflows(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private" / "proof.pdf"
    source.parent.mkdir()
    source.write_bytes(b"synthetic proof")
    evidence_id = "a" * 64
    record = EvidenceRecord(
        evidence_id=evidence_id,
        content_sha256=evidence_id,
        media_type="application/pdf",
        byte_size=source.stat().st_size,
        original_filename="synthetic-proof.pdf",
        canonical_path=str(source),
        captured_at_utc="2026-08-30T00:00:00+00:00",
        duplicate=False,
    )
    relations = pd.DataFrame(
        [
            {
                "tx_id": "tx-right",
                "evidence_id": evidence_id,
                "relation": "payment_proof",
                "status": "approved",
            }
        ]
    )
    result = write_transaction_evidence_artifact(
        [record], relations, out_dir=tmp_path / "artifact"
    )
    documents = pd.read_csv(result["documents_path"])
    produced_relations = pd.read_csv(result["relations_path"])
    assert documents.columns.tolist() == [
        "evidence_id",
        "content_sha256",
        "media_type",
        "display_name",
        "href",
    ]
    assert produced_relations.columns.tolist() == [
        "tx_id",
        "evidence_id",
        "relation",
        "status",
    ]
    assert documents.iloc[0]["href"].startswith("file://")
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["schema"] == "acct.transaction-evidence@1"
    assert manifest["approved_relations"] == 1
    assert manifest["accounting_truth_created"] is False
    assert manifest["private_evidence_publication_implied"] is False
