from __future__ import annotations

"""Thin, local-only Docling adapter for captured accounting evidence.

Docling owns generic document conversion. This repository owns evidence identity,
conversion provenance, accounting interpretation, and review semantics. The
original document remains authority; Docling JSON is a rebuildable derivative.
"""

from dataclasses import asdict, dataclass
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from accounting_doc_triage.intake.custody import sha256_file


class DoclingUnavailableError(RuntimeError):
    """Raised when real conversion is requested without the optional dependency."""


class DoclingConversionError(RuntimeError):
    """Raised when Docling cannot produce a structured document."""


@dataclass(frozen=True, slots=True)
class DoclingConfig:
    schema_version: str = "acct-docling-local-v1"
    max_num_pages: int = 200
    max_file_size: int = 50 * 1024 * 1024
    allowed_media: tuple[str, ...] = ("pdf", "image")
    remote_services: bool = False
    external_plugins: bool = False

    def __post_init__(self) -> None:
        if self.max_num_pages < 1:
            raise ValueError("max_num_pages must be positive")
        if self.max_file_size < 1:
            raise ValueError("max_file_size must be positive")
        if self.remote_services:
            raise ValueError("accounting evidence conversion must remain local")
        if self.external_plugins:
            raise ValueError("external Docling plugins are disabled by policy")


@dataclass(frozen=True, slots=True)
class ParsedDocumentArtifact:
    evidence_id: str
    source_sha256: str
    docling_version: str
    conversion_id: str
    status: str
    document_json_path: str
    metadata_json_path: str
    confidence_grade: str | None
    cached: bool = False


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink()


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value"):
        return _jsonable(value.value)
    return str(value)


def _find_grade(value: Any) -> str | None:
    payload = _jsonable(value)
    if isinstance(payload, str) and payload.lower() in {
        "poor",
        "fair",
        "good",
        "excellent",
    }:
        return payload.lower()
    if isinstance(payload, dict):
        for key in ("grade", "document_grade", "quality_grade"):
            if key in payload:
                found = _find_grade(payload[key])
                if found:
                    return found
        for item in payload.values():
            found = _find_grade(item)
            if found:
                return found
    return None


def _status_text(result: Any) -> str:
    status = getattr(result, "status", "unknown")
    if hasattr(status, "value"):
        status = status.value
    return str(status).strip().lower()


def _installed_docling_version() -> str:
    try:
        return metadata.version("docling")
    except metadata.PackageNotFoundError as exc:
        raise DoclingUnavailableError(
            "Docling is not installed. Install the optional parser with "
            "`pip install -e '.[docling]'`."
        ) from exc


def _default_converter_factory() -> Any:
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter
    except ImportError as exc:  # pragma: no cover - exercised only with optional runtime
        raise DoclingUnavailableError(
            "Docling runtime imports failed; install the `docling` project extra"
        ) from exc

    # Restrict this repository to the two evidence families explicitly supported
    # by its custody contract. Docling's default local pipelines keep remote
    # services and third-party plugins disabled unless explicitly opted in.
    return DocumentConverter(allowed_formats=[InputFormat.PDF, InputFormat.IMAGE])


def conversion_id(config: DoclingConfig, docling_version: str) -> str:
    payload = {
        "adapter": asdict(config),
        "docling_version": docling_version,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"docling-{docling_version}-{digest}"


def _artifact_paths(
    derivative_root: Path,
    conversion_identity: str,
    evidence_id: str,
) -> tuple[Path, Path]:
    base = Path(derivative_root) / "docling" / conversion_identity / evidence_id[:2]
    return base / f"{evidence_id}.json", base / f"{evidence_id}.meta.json"


def _load_cached(meta_path: Path) -> ParsedDocumentArtifact:
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    return ParsedDocumentArtifact(
        evidence_id=str(payload["evidence_id"]),
        source_sha256=str(payload["source_sha256"]),
        docling_version=str(payload["docling_version"]),
        conversion_id=str(payload["conversion_id"]),
        status=str(payload["status"]),
        document_json_path=str(payload["document_json_path"]),
        metadata_json_path=str(meta_path),
        confidence_grade=payload.get("confidence_grade"),
        cached=True,
    )


def convert_with_docling(
    source: Path,
    *,
    derivative_root: Path,
    config: DoclingConfig | None = None,
    force: bool = False,
    converter_factory: Callable[[], Any] | None = None,
    docling_version: str | None = None,
) -> ParsedDocumentArtifact:
    """Convert one captured local PDF/image into lossless Docling JSON.

    `converter_factory` and `docling_version` are injectable for deterministic unit
    tests; ordinary callers should leave them unset.
    """

    source = Path(source)
    if not source.is_file():
        raise DoclingConversionError(f"source is not a local file: {source}")
    if source.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise DoclingConversionError(
            f"unsupported parsing input: {source.suffix or '<no extension>'}"
        )

    config = config or DoclingConfig()
    source_sha = sha256_file(source)
    version = docling_version or _installed_docling_version()
    conv_id = conversion_id(config, version)
    document_path, meta_path = _artifact_paths(
        Path(derivative_root), conv_id, source_sha
    )

    if not force and document_path.exists() and meta_path.exists():
        cached = _load_cached(meta_path)
        if cached.source_sha256 != source_sha:
            raise DoclingConversionError("cached parser metadata source hash mismatch")
        return cached

    converter = (converter_factory or _default_converter_factory)()
    result = converter.convert(
        source,
        raises_on_error=False,
        max_num_pages=config.max_num_pages,
        max_file_size=config.max_file_size,
    )
    status = _status_text(result)
    document = getattr(result, "document", None)
    if document is None or not hasattr(document, "export_to_dict"):
        raise DoclingConversionError(
            f"Docling did not produce a structured document; status={status}"
        )

    document_payload = document.export_to_dict()
    confidence = getattr(result, "confidence", None)
    confidence_payload = _jsonable(confidence)
    confidence_grade = _find_grade(confidence)
    _atomic_json(document_path, document_payload)

    metadata_payload = {
        "schema_version": config.schema_version,
        "evidence_id": source_sha,
        "source_sha256": source_sha,
        "source_name": source.name,
        "docling_version": version,
        "conversion_id": conv_id,
        "config": asdict(config),
        "status": status,
        "confidence_grade": confidence_grade,
        "confidence": confidence_payload,
        "document_json_path": str(document_path),
        "source_is_authority": True,
        "parsed_derivative_is_rebuildable": True,
    }
    _atomic_json(meta_path, metadata_payload)
    return ParsedDocumentArtifact(
        evidence_id=source_sha,
        source_sha256=source_sha,
        docling_version=version,
        conversion_id=conv_id,
        status=status,
        document_json_path=str(document_path),
        metadata_json_path=str(meta_path),
        confidence_grade=confidence_grade,
        cached=False,
    )
