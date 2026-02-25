#!/usr/bin/env python3
"""tigre_parser.py
Deterministic parser for Tigre-style statements.
Usage:
  python tigre_parser.py --pdf 1_Input_Raw/00_inbox/CABA_2510.pdf --out-dir 4_Analysis_Workflows/tigre_out
  OR
  python tigre_parser.py --jsonl 4_Analysis_Workflows/triage_input.jsonl --doc-hash f405... --out-dir ...
"""

import argparse, json, csv, datetime, logging, re
from pathlib import Path
from uuid import uuid4

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# reuse extractor code (pypdf/pdfplumber/pdfminer paths)
# copy minimal needed helper functions (expect these to be present in triage_indexer_v2.py in the same folder)
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

def extract_text_from_pdf(path: Path) -> str:
    # pypdf
    if PYPDF_AVAILABLE:
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
            logging.debug("pypdf failed %s", e)
    # pdfplumber
    if PDFPLUMBER_AVAILABLE:
        try:
            with pdfplumber.open(str(path)) as pdf:
                pages = [(p.extract_text() or "") for p in pdf.pages]
            text = "\n\n".join(pages).strip()
            if text:
                return text
        except Exception as e:
            logging.debug("pdfplumber failed %s", e)
    # pdfminer
    if PDFMINER_AVAILABLE:
        try:
            text = pdfminer_extract_text(str(path)) or ""
            if text:
                return text
        except Exception as e:
            logging.debug("pdfminer failed %s", e)
    return ""

# --- Your deterministic parsers (adapted) ---
def clean_value(value):
    return value.split("\n")[0].strip() if value else value

def safe_extract(pattern, text, group=1, default=None, flags=re.MULTILINE):
    m = re.search(pattern, text, flags)
    if not m:
        return default
    return clean_value(m.group(group))

def extract_metadata(text):
    return {
        "N° Liq.": safe_extract(r"N° Liq\.\s*([\d\-]+)", text),
        "Cuenta": safe_extract(r"Cuenta\s*([\d\-]+)", text),
        "Contribuyente": safe_extract(r"Contribuyente\s*(.+?)(?:\n|Cuenta|Domicilio)", text),
        "Domicilio": safe_extract(r"Domicilio\s*(.+?)(?:\n|Localidad)", text),
        "Localidad": safe_extract(r"Localidad\s*(.+?)(?:\n|Nomenclatura)", text),
        "Nomenclatura": safe_extract(r"Nomenclatura\s*([\w\s\d\-]+)(?:\n|Fecha Emisión)", text),
        "Fecha Emisión": safe_extract(r"Fecha Emisión[:\s]*([\d/:\s\.apm]+)", text),
        "Vencimiento": safe_extract(r"Vencimiento[:\s]*([\d/]+)", text),
        "Importe a Pagar": safe_extract(r"Importe a [Pp]agar[:\s]*\$?([\d,\.]+)", text)
    }

def extract_outstanding_bills(text):
    # tuned to your earlier pattern; adjust if you see false negatives
    bill_pattern = re.compile(
        r"([A-ZÁÉÍÓÚÑ0-9\.\s\-\&\,]{8,50}?)\s+(\d{4})\s+(\d{1,4})\s+\$([\d,\.]+)\s+\$([\d,\.]+)\s+\$([\d,\.]+)",
        re.DOTALL
    )
    entries = []
    for match in bill_pattern.finditer(text):
        entries.append({
            "Concepto": match.group(1).strip(),
            "Período": match.group(2),
            "Cuota": match.group(3),
            "Importe": match.group(4).replace(",", ""),
            "Accesorios (Descuento)": match.group(5).replace(",", ""),
            "Total": match.group(6).replace(",", "")
        })
    return entries

# --- runner / wiring ---
def parse_pdf_path(path: Path):
    text = extract_text_from_pdf(path)
    if not text:
        logging.warning("No text extracted from %s (digital text may be missing)", path.name)
    meta = extract_metadata(text)
    bills = extract_outstanding_bills(text)
    return meta, bills, text

def load_record_from_jsonl(jsonl_path: Path, id: str):
    with jsonl_path.open("r", encoding="utf8") as fh:
        for line in fh:
            obj = json.loads(line)
            if obj.get("id") == id:
                return obj
    return None

import sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", help="Single PDF to parse")
    ap.add_argument("--jsonl", help="triage JSONL (use with --doc-hash)")
    ap.add_argument("--doc-hash", help="If using --jsonl, the id to parse")
    ap.add_argument("--out-dir", default="tigre_out", help="Output directory")
    args = ap.parse_args()
    outdir = Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    records = []
    if args.pdf:
        p = Path(args.pdf)
        meta, bills, text = parse_pdf_path(p)
        records.append((p, meta, bills, text))
    elif args.jsonl and args.id:
        rec = load_record_from_jsonl(Path(args.jsonl), args.id)
        if not rec:
            logging.error("id %s not found in %s", args.id, args.jsonl); sys.exit(1)
        p = Path(rec["metadata"]["doc_path"])
        # prefer text from jsonl (already extracted)
        text = rec.get("text", "") or extract_text_from_pdf(p)
        meta = extract_metadata(text)
        bills = extract_outstanding_bills(text)
        records.append((p, meta, bills, text))
    else:
        logging.error("Provide --pdf or both --jsonl and --doc-hash"); sys.exit(2)

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    meta_out = outdir / f"tigre_metadata_{timestamp}.csv"
    bills_out = outdir / f"tigre_bills_{timestamp}.csv"

    # write metadata CSV
    with meta_out.open("w", newline="", encoding="utf8") as mf:
        if records:
            fieldnames = ["statement_id","id","doc_path"] + list(records[0][1].keys())
            writer = csv.DictWriter(mf, fieldnames=fieldnames)
            writer.writeheader()
            for p, meta, bills, text in records:
                writer.writerow({
                    "statement_id": str(uuid4()),
                    "id": sha256_of_file(p) if p.exists() else "",
                    "doc_path": str(p),
                    **meta
                })

    # write bills CSV
    with bills_out.open("w", newline="", encoding="utf8") as bf:
        if records:
            fieldnames = ["statement_id","id","doc_path","Concepto","Período","Cuota","Importe","Accesorios (Descuento)","Total"]
            writer = csv.DictWriter(bf, fieldnames=fieldnames)
            writer.writeheader()
            for p, meta, bills, text in records:
                sid = str(uuid4())
                dh = sha256_of_file(p) if p.exists() else ""
                for b in bills:
                    writer.writerow({
                        "statement_id": sid,
                        "id": dh,
                        "doc_path": str(p),
                        **b
                    })
    logging.info("Wrote %s and %s", meta_out, bills_out)

# small helper for sha256 (same logic as indexer)
def sha256_of_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

if __name__ == "__main__":
    main()
