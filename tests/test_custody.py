from __future__ import annotations

from pathlib import Path

from accounting_doc_triage.intake.custody import (
    EvidenceRecord,
    capture_document,
    capture_claimed_document,
    plan_capture,
    recover_inflight,
    sha256_file,
)


def test_dry_run_is_read_only_and_accepts_pdf_png_jpeg(tmp_path: Path) -> None:
    store = tmp_path / "evidence"
    inflight = tmp_path / "inflight"
    for name in ("proof.pdf", "screenshot.png", "photo.jpeg"):
        source = tmp_path / name
        source.write_bytes(f"synthetic-{name}".encode())
        plan = capture_document(
            source,
            inflight_dir=inflight,
            store_root=store,
            dry_run=True,
        )
        assert source.exists()
        assert plan.evidence_id == sha256_file(source)
        assert not inflight.exists()
        assert not plan.canonical_path.exists()


def test_capture_is_content_addressed_and_deduplicates(tmp_path: Path) -> None:
    store = tmp_path / "evidence"
    inflight = tmp_path / "inflight"
    first = tmp_path / "first.pdf"
    second = tmp_path / "renamed-copy.pdf"
    payload = b"synthetic private accounting proof"
    first.write_bytes(payload)
    second.write_bytes(payload)

    record1 = capture_document(first, inflight_dir=inflight, store_root=store)
    record2 = capture_document(second, inflight_dir=inflight, store_root=store)
    assert isinstance(record1, EvidenceRecord)
    assert isinstance(record2, EvidenceRecord)
    assert record1.evidence_id == record2.evidence_id
    assert record1.duplicate is False
    assert record2.duplicate is True
    canonical = Path(record1.canonical_path)
    assert canonical.exists()
    assert canonical.read_bytes() == payload
    assert not first.exists()
    assert not second.exists()
    assert list(store.rglob("*.pdf")) == [canonical]
    assert len(list(store.rglob("*.json"))) == 1


def test_interrupted_claim_remains_recoverable(tmp_path: Path) -> None:
    store = tmp_path / "evidence"
    inflight = tmp_path / "inflight"
    inflight.mkdir()
    claimed = inflight / "deadbeef__receipt.png"
    claimed.write_bytes(b"synthetic screenshot")

    records = recover_inflight(inflight, store)
    assert len(records) == 1
    assert not claimed.exists()
    assert Path(records[0].canonical_path).exists()


def test_existing_canonical_bytes_can_recover_missing_manifest(tmp_path: Path) -> None:
    store = tmp_path / "evidence"
    inflight = tmp_path / "inflight"
    source = tmp_path / "receipt.pdf"
    source.write_bytes(b"same bytes")
    plan = plan_capture(source, store)
    plan.canonical_path.parent.mkdir(parents=True)
    plan.canonical_path.write_bytes(source.read_bytes())

    inflight.mkdir()
    claimed = inflight / "abc__receipt.pdf"
    source.replace(claimed)
    record = capture_claimed_document(claimed, store)
    assert record.duplicate is True
    assert plan.manifest_path.exists()
    assert not claimed.exists()
