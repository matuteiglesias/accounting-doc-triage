from __future__ import annotations

"""Review decisions and producer-owned `acct.transaction-evidence@1` artifact output."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Iterable

import pandas as pd

from accounting_doc_triage.intake.custody import EvidenceRecord


ARTIFACT_SCHEMA = "acct.transaction-evidence@1"
_RELATIONS = {
    "payment_proof",
    "transfer_proof",
    "statement_context",
    "liability_source",
    "other_support",
}
_STATUSES = {"approved", "candidate", "rejected"}


class EvidenceArtifactError(ValueError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def apply_review_decisions(
    candidates: pd.DataFrame,
    decisions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Convert matching candidates into explicit candidate/approved/rejected relations.

    A review decision may only resolve an existing candidate; it cannot silently
    invent a new transaction relationship. Manual non-candidate relations should
    use a future explicit review surface rather than bypass this first contract.
    """

    required = {"evidence_id", "candidate_tx_id", "relation", "match_status"}
    missing = sorted(required - set(candidates.columns))
    if missing:
        raise EvidenceArtifactError(f"candidate frame missing columns: {missing}")

    base = candidates.copy()
    if base.empty:
        return pd.DataFrame(columns=["tx_id", "evidence_id", "relation", "status"])
    base["tx_id"] = base["candidate_tx_id"].astype(str)
    base["status"] = "candidate"
    key_columns = ["evidence_id", "candidate_tx_id", "relation"]
    if base.duplicated(key_columns).any():
        raise EvidenceArtifactError("duplicate candidate relation keys")

    if decisions is not None and not decisions.empty:
        decision_required = {*key_columns, "decision"}
        missing_decisions = sorted(decision_required - set(decisions.columns))
        if missing_decisions:
            raise EvidenceArtifactError(
                f"review decisions missing columns: {missing_decisions}"
            )
        if decisions.duplicated(key_columns).any():
            raise EvidenceArtifactError("duplicate review decision keys")
        allowed_decisions = {"approved", "rejected"}
        invalid = set(decisions["decision"].astype(str)) - allowed_decisions
        if invalid:
            raise EvidenceArtifactError(f"unsupported review decisions: {sorted(invalid)}")

        candidate_keys = {
            tuple(map(str, row))
            for row in base[key_columns].itertuples(index=False, name=None)
        }
        for _, decision in decisions.iterrows():
            key = tuple(str(decision[column]) for column in key_columns)
            if key not in candidate_keys:
                raise EvidenceArtifactError(
                    f"review decision does not resolve an existing candidate: {key!r}"
                )
            mask = True
            for column, value in zip(key_columns, key):
                mask = mask & (base[column].astype(str) == value)
            base.loc[mask, "status"] = str(decision["decision"])

    return base[["tx_id", "evidence_id", "relation", "status"]].sort_values(
        ["evidence_id", "tx_id", "relation"]
    ).reset_index(drop=True)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        tmp = Path(handle.name)
    tmp.replace(path)


def write_transaction_evidence_artifact(
    records: Iterable[EvidenceRecord],
    relations: pd.DataFrame,
    *,
    out_dir: Path,
) -> dict:
    """Materialize the small optional artifact consumed by Accounting Workflows."""

    required = {"tx_id", "evidence_id", "relation", "status"}
    missing = sorted(required - set(relations.columns))
    if missing:
        raise EvidenceArtifactError(f"relation frame missing columns: {missing}")

    invalid_relations = set(relations["relation"].astype(str)) - _RELATIONS
    invalid_statuses = set(relations["status"].astype(str)) - _STATUSES
    if invalid_relations:
        raise EvidenceArtifactError(f"unsupported relations: {sorted(invalid_relations)}")
    if invalid_statuses:
        raise EvidenceArtifactError(f"unsupported statuses: {sorted(invalid_statuses)}")

    record_map: dict[str, EvidenceRecord] = {}
    for record in records:
        if record.evidence_id in record_map:
            raise EvidenceArtifactError(f"duplicate evidence record: {record.evidence_id}")
        if record.evidence_id != record.content_sha256:
            raise EvidenceArtifactError(
                f"evidence_id must equal exact content SHA-256: {record.evidence_id}"
            )
        record_map[record.evidence_id] = record

    referenced = set(relations["evidence_id"].astype(str))
    missing_records = sorted(referenced - set(record_map))
    if missing_records:
        raise EvidenceArtifactError(
            f"relations reference evidence without custody record: {missing_records}"
        )

    documents_rows: list[dict] = []
    for evidence_id in sorted(referenced):
        record = record_map[evidence_id]
        canonical = Path(record.canonical_path).expanduser().resolve()
        documents_rows.append(
            {
                "evidence_id": evidence_id,
                "content_sha256": record.content_sha256,
                "media_type": record.media_type,
                "display_name": record.original_filename,
                "href": canonical.as_uri(),
            }
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    documents_path = out_dir / "evidence_documents.csv"
    relations_path = out_dir / "transaction_evidence.csv"
    manifest_path = out_dir / "manifest.json"
    pd.DataFrame(
        documents_rows,
        columns=["evidence_id", "content_sha256", "media_type", "display_name", "href"],
    ).to_csv(documents_path, index=False)
    relations[["tx_id", "evidence_id", "relation", "status"]].to_csv(
        relations_path, index=False
    )
    manifest = {
        "schema": ARTIFACT_SCHEMA,
        "documents": len(documents_rows),
        "relations": int(len(relations)),
        "approved_relations": int((relations["status"] == "approved").sum()),
        "candidate_relations": int((relations["status"] == "candidate").sum()),
        "rejected_relations": int((relations["status"] == "rejected").sum()),
        "files": {
            "evidence_documents.csv": _sha256(documents_path),
            "transaction_evidence.csv": _sha256(relations_path),
        },
        "accounting_truth_created": False,
        "private_evidence_publication_implied": False,
    }
    _atomic_json(manifest_path, manifest)
    return {
        "documents_path": str(documents_path),
        "relations_path": str(relations_path),
        "manifest_path": str(manifest_path),
        **manifest,
    }
