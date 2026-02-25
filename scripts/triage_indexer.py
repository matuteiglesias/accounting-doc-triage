#!/usr/bin/env python3
"""triage_indexer_v2.py
Minimal PDF indexer -> JSONL for triage (no OCR).
Usage:
  python triage_indexer_v2.py --input-dir 1_Input_Raw/00_inbox --out 4_Analysis_Workflows/triage_input.jsonl
"""

from pathlib import Path
import hashlib, json, argparse, datetime, logging, sys, re

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Try preferred extractors
try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except Exception:
    PYPDF_AVAILABLE = False

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except Exception:
    PDFPLUMBER_AVAILABLE = False

try:
    from pdfminer.high_level import extract_text as pdfminer_extract_text
    PDFMINER_AVAILABLE = True
except Exception:
    PDFMINER_AVAILABLE = False

def sha256_hex(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    s = re.sub(r'\n\s*\n\s*\n+', '\n\n', s)
    s = "\n".join([line.strip() for line in s.splitlines()])
    s = re.sub(r'[ \t]+', ' ', s)
    return s.strip()

def page_count(path: Path) -> int:
    if PYPDF_AVAILABLE:
        try:
            r = PdfReader(str(path))
            return len(r.pages)
        except Exception:
            logging.debug("pypdf page count fail for %s", path)
    try:
        b = path.read_bytes()
        return max(0, b.count(b"/Type /Page"))
    except Exception:
        return 0

def extract_text_from_pdf(path: Path, prefer_order=("pypdf","pdfplumber","pdfminer")) -> str:
    # 1) pypdf
    if "pypdf" in prefer_order and PYPDF_AVAILABLE:
        try:
            r = PdfReader(str(path))
            pages = []
            for p in r.pages:
                try:
                    t = p.extract_text() or ""
                    pages.append(t)
                except Exception:
                    pages.append("")
            text = "\n\n".join(pages).strip()
            if text:
                return text
        except Exception as e:
            logging.debug("pypdf extraction failed %s: %s", path, e)
    # 2) pdfplumber
    if "pdfplumber" in prefer_order and PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(str(path)) as pdf:
                pages = [(p.extract_text() or "") for p in pdf.pages]
            text = "\n\n".join(pages).strip()
            if text:
                return text
        except Exception as e:
            logging.debug("pdfplumber failed %s: %s", path, e)
    # 3) pdfminer
    if "pdfminer" in prefer_order and PDFMINER_AVAILABLE:
        try:
            text = pdfminer_extract_text(str(path)) or ""
            if text:
                return text
        except Exception as e:
            logging.debug("pdfminer failed %s: %s", path, e)
    return ""  # no OCR here by design

def make_record(path: Path, max_chars:int=20000) -> dict:
    stat = path.stat()
    id = sha256_hex(path)
    raw_text = extract_text_from_pdf(path)
    text = normalize_text(raw_text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    snippet = text[:4000]

    try:
        relpath = str(path.relative_to(Path.cwd()))
    except Exception:
        relpath = str(path.resolve())


    rec = {
        "id": id,
        "metadata": {
            "doc_path": relpath,
            "filename": path.name,
            "bytes": stat.st_size,
            "created_at": datetime.datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "modified_at": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "page_count": page_count(path)
        },
        "text": text,
        "snippet": snippet,
        "indexer": {
            "version": "triage_indexer:v0.3",
            "scanned_at": datetime.datetime.utcnow().isoformat() + "Z",
            "extractor": {
                "pypdf": bool(PYPDF_AVAILABLE),
                "pdfplumber": bool(PDFPLUMBER_AVAILABLE),
                "pdfminer": bool(PDFMINER_AVAILABLE)
            }
        }
    }
    return rec

def walk_and_write(input_dir: Path, out_file: Path, max_chars:int=20000):
    files = sorted([p for p in input_dir.rglob("*.pdf")])
    logging.info("Found %d pdf files under %s", len(files), input_dir)
    seen = set()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf8") as fh:
        for p in files:
            try:
                rec = make_record(p, max_chars=max_chars)
                if rec["id"] in seen:
                    logging.info("Skipping duplicate %s", p.name)
                    continue
                seen.add(rec["id"])
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception as e:
                logging.exception("Error processing %s: %s", p, e)

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True, help="Directory with PDFs")
    ap.add_argument("--out", required=True, help="Output JSONL file")
    ap.add_argument("--max-chars", type=int, default=20000, help="Max characters to store in text")
    return ap.parse_args()

def main():
    args = parse_args()
    walk_and_write(Path(args.input_dir), Path(args.out), max_chars=args.max_chars)

if __name__ == "__main__":
    main()
