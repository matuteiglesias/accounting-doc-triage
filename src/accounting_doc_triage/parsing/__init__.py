"""Replaceable document parsing substrates."""

from accounting_doc_triage.parsing.docling_adapter import (
    DoclingConfig,
    DoclingConversionError,
    DoclingUnavailableError,
    ParsedDocumentArtifact,
    convert_with_docling,
)

__all__ = [
    "DoclingConfig",
    "DoclingConversionError",
    "DoclingUnavailableError",
    "ParsedDocumentArtifact",
    "convert_with_docling",
]
