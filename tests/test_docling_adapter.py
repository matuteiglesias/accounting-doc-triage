from __future__ import annotations

import json
from pathlib import Path

from accounting_doc_triage.parsing.docling_adapter import (
    DoclingConfig,
    conversion_id,
    convert_with_docling,
)


class _FakeStatus:
    value = "success"


class _FakeDocument:
    def export_to_dict(self):
        return {
            "schema_name": "DoclingDocument",
            "version": "synthetic",
            "texts": [{"text": "synthetic payment proof"}],
            "tables": [],
        }


class _FakeResult:
    status = _FakeStatus()
    document = _FakeDocument()
    confidence = {
        "grade": "excellent",
        "parse_score": 0.99,
    }


class _FakeConverter:
    def __init__(self, calls: list[dict]):
        self.calls = calls

    def convert(self, source, **kwargs):
        self.calls.append({"source": str(source), **kwargs})
        return _FakeResult()


def test_conversion_identity_includes_docling_version_and_parser_config() -> None:
    base = DoclingConfig()
    changed = DoclingConfig(max_num_pages=99)
    assert conversion_id(base, "2.123.1") == conversion_id(base, "2.123.1")
    assert conversion_id(base, "2.123.1") != conversion_id(base, "2.124.0")
    assert conversion_id(base, "2.123.1") != conversion_id(changed, "2.123.1")


def test_docling_json_is_rebuildable_derivative_and_cacheable(tmp_path: Path) -> None:
    source = tmp_path / "proof.png"
    source.write_bytes(b"synthetic image bytes")
    calls: list[dict] = []

    artifact = convert_with_docling(
        source,
        derivative_root=tmp_path / "derived",
        converter_factory=lambda: _FakeConverter(calls),
        docling_version="2.123.1",
    )
    assert artifact.status == "success"
    assert artifact.confidence_grade == "excellent"
    assert artifact.cached is False
    assert len(calls) == 1
    assert calls[0]["raises_on_error"] is False

    document_payload = json.loads(
        Path(artifact.document_json_path).read_text(encoding="utf-8")
    )
    assert document_payload["schema_name"] == "DoclingDocument"

    metadata_payload = json.loads(
        Path(artifact.metadata_json_path).read_text(encoding="utf-8")
    )
    assert metadata_payload["source_is_authority"] is True
    assert metadata_payload["parsed_derivative_is_rebuildable"] is True
    assert metadata_payload["config"]["remote_services"] is False
    assert metadata_payload["config"]["external_plugins"] is False

    cached = convert_with_docling(
        source,
        derivative_root=tmp_path / "derived",
        converter_factory=lambda: _FakeConverter(calls),
        docling_version="2.123.1",
    )
    assert cached.cached is True
    assert len(calls) == 1


def test_pdf_and_image_share_same_bounded_parser_contract(tmp_path: Path) -> None:
    for filename in ("proof.pdf", "screenshot.png", "photo.jpg"):
        source = tmp_path / filename
        source.write_bytes(f"synthetic-{filename}".encode())
        artifact = convert_with_docling(
            source,
            derivative_root=tmp_path / "derived",
            converter_factory=lambda: _FakeConverter([]),
            docling_version="2.123.1",
        )
        assert Path(artifact.document_json_path).exists()
