from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path

from accounting_doc_triage.intake.custody import capture_document, recover_inflight
from accounting_doc_triage.parsing.docling_adapter import convert_with_docling


def _print_payload(value) -> None:
    if is_dataclass(value):
        value = asdict(value)
    elif isinstance(value, list):
        value = [asdict(item) if is_dataclass(item) else item for item in value]
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="accounting-doc-triage",
        description="Bounded accounting evidence intake and parsing",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="Capture one PDF/image into evidence custody")
    capture.add_argument("source", type=Path)
    capture.add_argument("--inflight", type=Path, required=True)
    capture.add_argument("--store", type=Path, required=True)
    capture.add_argument("--dry-run", action="store_true")

    recover = sub.add_parser("recover", help="Resume files left in the inflight directory")
    recover.add_argument("--inflight", type=Path, required=True)
    recover.add_argument("--store", type=Path, required=True)

    convert = sub.add_parser("convert", help="Convert one captured local PDF/image with Docling")
    convert.add_argument("source", type=Path)
    convert.add_argument("--derived", type=Path, required=True)
    convert.add_argument("--force", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "capture":
        result = capture_document(
            args.source,
            inflight_dir=args.inflight,
            store_root=args.store,
            dry_run=args.dry_run,
        )
        _print_payload(result)
        return 0
    if args.command == "recover":
        _print_payload(recover_inflight(args.inflight, args.store))
        return 0
    if args.command == "convert":
        _print_payload(
            convert_with_docling(
                args.source,
                derivative_root=args.derived,
                force=args.force,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
