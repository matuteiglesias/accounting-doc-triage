from __future__ import annotations

"""Content-addressed, recoverable custody for private accounting documents.

The custody layer is intentionally ignorant of accounting semantics. It knows only
how to claim a supported local document, identify its exact bytes, and preserve one
immutable canonical copy. Parsing/classification happens after capture.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable
from uuid import uuid4


_MEDIA_BY_SUFFIX = {
    ".pdf": ("application/pdf", ".pdf"),
    ".png": ("image/png", ".png"),
    ".jpg": ("image/jpeg", ".jpg"),
    ".jpeg": ("image/jpeg", ".jpg"),
}


class CustodyError(RuntimeError):
    """Raised when a document cannot be safely captured."""


@dataclass(frozen=True, slots=True)
class CapturePlan:
    source: Path
    evidence_id: str
    content_sha256: str
    media_type: str
    byte_size: int
    original_filename: str
    canonical_path: Path
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    content_sha256: str
    media_type: str
    byte_size: int
    original_filename: str
    canonical_path: str
    captured_at_utc: str
    duplicate: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_for(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix not in _MEDIA_BY_SUFFIX:
        raise CustodyError(
            f"unsupported accounting evidence type: {suffix or '<no extension>'}; "
            "supported: PDF, PNG, JPG/JPEG"
        )
    return _MEDIA_BY_SUFFIX[suffix]


def _canonical_paths(store_root: Path, sha256: str, canonical_suffix: str) -> tuple[Path, Path]:
    bucket = Path(store_root) / sha256[:2]
    return bucket / f"{sha256}{canonical_suffix}", bucket / f"{sha256}.json"


def plan_capture(source: Path, store_root: Path) -> CapturePlan:
    """Compute immutable evidence identity and destination without mutating files."""

    source = Path(source)
    if not source.is_file():
        raise CustodyError(f"source is not a file: {source}")
    media_type, canonical_suffix = _media_for(source)
    digest = sha256_file(source)
    canonical_path, manifest_path = _canonical_paths(
        Path(store_root), digest, canonical_suffix
    )
    return CapturePlan(
        source=source,
        evidence_id=digest,
        content_sha256=digest,
        media_type=media_type,
        byte_size=source.stat().st_size,
        original_filename=source.name,
        canonical_path=canonical_path,
        manifest_path=manifest_path,
    )


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink()


def _copy_then_replace(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp_name, destination)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink()


def _claim(source: Path, inflight_dir: Path) -> Path:
    inflight_dir.mkdir(parents=True, exist_ok=True)
    claimed = inflight_dir / f"{uuid4().hex}__{source.name}"
    try:
        os.replace(source, claimed)
    except OSError as exc:
        raise CustodyError(f"failed to claim source atomically: {source}: {exc}") from exc
    return claimed


def _original_name_from_claim(claimed: Path) -> str:
    name = claimed.name
    return name.split("__", 1)[1] if "__" in name else name


def _record_from_manifest(path: Path, *, duplicate: bool) -> EvidenceRecord:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return EvidenceRecord(
        evidence_id=str(payload["evidence_id"]),
        content_sha256=str(payload["content_sha256"]),
        media_type=str(payload["media_type"]),
        byte_size=int(payload["byte_size"]),
        original_filename=str(payload["original_filename"]),
        canonical_path=str(payload["canonical_path"]),
        captured_at_utc=str(payload["captured_at_utc"]),
        duplicate=duplicate,
    )


def capture_claimed_document(claimed: Path, store_root: Path) -> EvidenceRecord:
    """Finish a previously claimed document.

    On any failure before canonicalization the claimed file remains in `inflight`,
    making recovery explicit. If identical bytes already exist in the store, the
    duplicate claim is removed only after the canonical object is verified.
    """

    claimed = Path(claimed)
    if not claimed.is_file():
        raise CustodyError(f"claimed document is missing: {claimed}")

    original_name = _original_name_from_claim(claimed)
    original_suffix = Path(original_name).suffix.lower()
    if original_suffix not in _MEDIA_BY_SUFFIX:
        raise CustodyError(f"claimed document has unsupported type: {original_suffix}")
    media_type, canonical_suffix = _MEDIA_BY_SUFFIX[original_suffix]
    digest = sha256_file(claimed)
    canonical_path, manifest_path = _canonical_paths(
        Path(store_root), digest, canonical_suffix
    )

    if canonical_path.exists():
        if sha256_file(canonical_path) != digest:
            raise CustodyError(
                f"canonical evidence hash mismatch; refusing overwrite: {canonical_path}"
            )
        claimed.unlink()
        if manifest_path.exists():
            return _record_from_manifest(manifest_path, duplicate=True)
        # Recover an interrupted historical capture whose bytes landed before metadata.
        record = EvidenceRecord(
            evidence_id=digest,
            content_sha256=digest,
            media_type=media_type,
            byte_size=canonical_path.stat().st_size,
            original_filename=original_name,
            canonical_path=str(canonical_path),
            captured_at_utc=_now_iso(),
            duplicate=True,
        )
        _atomic_json(manifest_path, asdict(record) | {"duplicate": False})
        return record

    # Copy into the destination directory and atomically publish there. The
    # claimed source is retained until the destination hash has been verified.
    _copy_then_replace(claimed, canonical_path)
    if sha256_file(canonical_path) != digest:
        canonical_path.unlink(missing_ok=True)
        raise CustodyError("canonical evidence verification failed after copy")

    record = EvidenceRecord(
        evidence_id=digest,
        content_sha256=digest,
        media_type=media_type,
        byte_size=canonical_path.stat().st_size,
        original_filename=original_name,
        canonical_path=str(canonical_path),
        captured_at_utc=_now_iso(),
        duplicate=False,
    )
    _atomic_json(manifest_path, asdict(record))
    claimed.unlink()
    return record


def capture_document(
    source: Path,
    *,
    inflight_dir: Path,
    store_root: Path,
    dry_run: bool = False,
) -> CapturePlan | EvidenceRecord:
    """Plan or execute one recoverable capture from an inbox/source path."""

    source = Path(source)
    plan = plan_capture(source, store_root)
    if dry_run:
        return plan
    claimed = _claim(source, Path(inflight_dir))
    return capture_claimed_document(claimed, Path(store_root))


def recover_inflight(
    inflight_dir: Path,
    store_root: Path,
) -> list[EvidenceRecord]:
    """Resume every supported regular file left in `inflight` after interruption."""

    inflight_dir = Path(inflight_dir)
    if not inflight_dir.exists():
        return []
    records: list[EvidenceRecord] = []
    for path in sorted(inflight_dir.iterdir()):
        if not path.is_file():
            continue
        records.append(capture_claimed_document(path, Path(store_root)))
    return records


def supported_documents(paths: Iterable[Path]) -> list[Path]:
    return [
        Path(path)
        for path in paths
        if Path(path).is_file() and Path(path).suffix.lower() in _MEDIA_BY_SUFFIX
    ]
