from __future__ import annotations

"""Small schema-tolerant view over exported DoclingDocument JSON.

The accounting layer intentionally avoids depending on Docling Python internals.
It consumes the serialized derivative and searches textual elements while keeping
page/bounding-box provenance when present.
"""

from collections.abc import Iterator
from typing import Any

from accounting_doc_triage.interpretation.model import TextFragment


def _page_and_bbox(node: dict[str, Any]) -> tuple[int | None, Any | None]:
    provenance = node.get("prov") or node.get("provenance")
    if isinstance(provenance, list) and provenance:
        first = provenance[0]
        if isinstance(first, dict):
            page = first.get("page_no") or first.get("page")
            try:
                page_no = int(page) if page is not None else None
            except (TypeError, ValueError):
                page_no = None
            return page_no, first.get("bbox")
    return None, None


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def text_fragments(document_payload: dict[str, Any]) -> list[TextFragment]:
    """Return unique textual nodes in deterministic traversal order."""

    fragments: list[TextFragment] = []
    seen: set[tuple[str, int | None, str]] = set()
    for node in _walk(document_payload):
        text = node.get("text")
        if not isinstance(text, str):
            continue
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            continue
        page_no, bbox = _page_and_bbox(node)
        key = (cleaned, page_no, repr(bbox))
        if key in seen:
            continue
        seen.add(key)
        fragments.append(
            TextFragment(
                text=cleaned,
                fragment_index=len(fragments),
                page_no=page_no,
                bbox=bbox,
            )
        )
    return fragments


def joined_text(fragments: list[TextFragment]) -> str:
    return "\n".join(fragment.text for fragment in fragments)
