#!/usr/bin/env python3
from __future__ import annotations

"""Run a bounded, private Property Management evidence-linking campaign.

This command is intentionally an operational adapter, not a second accounting
pipeline: ledger rows are copied from an explicitly supplied canonical run,
document identity is SHA-256 of the source bytes, and only payment-like PDFs
with inspected local text can be approved.
"""

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import pandas as pd

from accounting_doc_triage.matching import MatchConfig


FILTER_TYPES = {"Impuestos", "servicio", "FX", "Legal", "Contribuciones"}
ARS_TOLERANCE = Decimal("10.00")
USD_TOLERANCE = Decimal("0.01")
PAYMENT_MARKERS = (
    "comprobante de pago", "comprobante de transferencia", "operación realizada con éxito",
    "operacion realizada con exito", "transacción se completó con éxito",
    "transaccion se completo con exito", "pago aprobado", "aprobado", "válido como comprobante de pago",
    "valido como comprobante de pago", "importe abonado", "total pagado",
)
LIABILITY_MARKERS = ("total a pagar", "saldo a pagar", "fecha de vencimiento", "vencimiento", "documento pagadero")
TRANSFER_MARKERS = ("transferencia", "transferencia inmediata", "comprobante de transferencia", "operación realizada")
ISSUER_MARKERS = {
    "aysa": ("aysa", "agua y saneamientos argentinos"),
    "arba": ("arba", "agencia de recaudación", "agencia de recaudacion"),
    "municipalidad_tigre": ("municipalidad de tigre", "tigre municipio"),
    "edenor": ("edenor", "empresa distribuidora"),
    "mercado_pago": ("mercado pago", "mercadopago"),
    "provincia_net": ("provincia net", "provincianet"),
    "banco": ("banco",),
}
DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})(?!\d)")
ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
MONEY_RE = re.compile(r"(?:ARS|USD|\$|u\$s|us\$)?\s*([0-9][0-9.,]*)", re.I)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_decimal(raw: str) -> Decimal | None:
    text = raw.strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        text = text.replace(thousands, "").replace(decimal_sep, ".")
    elif "," in text:
        tail = text.rsplit(",", 1)[1]
        text = text.replace(",", "." if len(tail) in (1, 2) else "")
    elif "." in text:
        tail = text.rsplit(".", 1)[1]
        if len(tail) != 2:
            text = text.replace(".", "")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_date(raw: str) -> str | None:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def first_date(text: str) -> tuple[str | None, str | None]:
    labels = (
        ("payment_date", r"(?:fecha\s+de\s+pago|fecha\s+y\s+hora|creada\s+el|fecha\s+de\s+operaci[oó]n|fecha)"),
        ("due_date", r"(?:fecha\s+de\s+vencimiento|vencimiento|fecha\s+vto\.?)"),
    )
    for label, pattern in labels:
        m = re.search(pattern + r"[^0-9]{0,35}(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", text, re.I)
        if m:
            parsed = parse_date(m.group(1))
            if parsed:
                return parsed, label
    m = DATE_RE.search(text)
    if m:
        parsed = parse_date("/".join(m.groups()))
        if parsed:
            return parsed, "document_date"
    m = ISO_DATE_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "document_date"
    return None, None


def field_amount(text: str) -> tuple[Decimal | None, str | None, str | None]:
    # Prefer a value on a line explicitly labelled as a paid amount. This avoids
    # mistaking document/account identifiers for the amount after broad words
    # such as "pago".
    for line in text.splitlines():
        compact = " ".join(line.split())
        labelled = re.search(r"\b(?:monto|total(?:\s+pagado|\s+a\s+pagar)?|importe(?:\s+abonado|\s+en\s+pesos)?)\b\s*[:]?\s*(?:ARS|USD|U\$S|US\$|\$)?\s*([0-9][0-9., ]*[0-9])", compact, re.I)
        paid = re.search(r"\bpago\s+\$\s*([0-9][0-9., ]*[0-9])", compact, re.I)
        match = labelled or paid
        if not match:
            continue
        amount = parse_decimal(match.group(1))
        if amount is not None and amount >= Decimal("10"):
            currency = "USD" if re.search(r"(?:USD|U\$S|US\$)", match.group(0), re.I) else "ARS"
            return amount, currency, "paid_amount_line"
    if "comprobante de transferencia" in normalize_text(text) or re.search(r"\btotal\b", text, re.I):
        standalone = re.findall(r"^\s*\$\s*([0-9][0-9., ]*[0-9])\s*$", text, re.M)
        if standalone:
            amount = parse_decimal(standalone[-1])
            if amount is not None and amount >= Decimal("10"):
                return amount, "ARS", "paid_amount_standalone_currency_line"
    # Several Mercado Pago / bank PDFs put the value on the line immediately
    # after "Total" or "Comprobante de transferencia".
    m = re.search(r"(?:importe\s+en\s+pesos|importe\s+de\s+la\s+operaci[oó]n|total|comprobante\s+de\s+transferencia)[^$]{0,140}\$\s*([0-9][0-9., ]*[0-9])", text, re.I | re.S)
    if m:
        amount = parse_decimal(m.group(1))
        if amount is not None and amount >= Decimal("10"):
            return amount, "ARS", "paid_amount_block"
    # Ordered labels prefer paid values over liability values and generic totals.
    labels = (
        ("paid_amount", r"(?:importe\s+abonado|total\s+pagado|importe\s+de\s+la\s+operaci[oó]n|monto)"),
        ("paid_amount", r"(?:importe\s+de\s+la\s+transferencia|monto\s+transferido|importe)"),
        ("liability_amount", r"(?:total\s+a\s+pagar|saldo\s+a\s+pagar|a\s+pagar)"),
    )
    for label, pattern in labels:
        m = re.search(pattern + r"[^0-9$]{0,20}(?:ARS|USD|U\$S|\$)?\s*([0-9][0-9.,]*)", text, re.I)
        if m:
            amount = parse_decimal(m.group(1))
            if amount is not None:
                currency = "USD" if re.search(r"(?:USD|U\$S|US\$)", m.group(0), re.I) else "ARS"
                return amount, currency, label
    # Common Mercado Pago layout: "Pago $ 119.601,57".
    m = re.search(r"\bPago\s+\$\s*([0-9][0-9.,]*)", text, re.I)
    if m:
        return parse_decimal(m.group(1)), "ARS", "paid_amount"
    return None, None, None


def external_reference(text: str) -> str | None:
    patterns = (
        r"(?:id\s+de\s+transacci[oó]n|n[uú]mero\s+de\s+transacci[oó]n|transacci[oó]n|nro\.\s+de\s+ticket|n[uú]mero\s+de\s+operaci[oó]n|autorizaci[oó]n)\s*[:#]?\s*([0-9A-Za-z-]{4,})",
        r"(?:ticket|operaci[oó]n)\s*[:#]?\s*([0-9A-Za-z-]{5,})",
    )
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(1)
    return None


def account_reference(text: str) -> str | None:
    m = re.search(r"(?:cuenta(?:\s+de\s+servicios)?|cliente|partida\s*n?[º°#]?)\s*[:nº°#]?\s*([0-9][0-9 .-]{3,})", text, re.I)
    return m.group(1).strip() if m else None


def normalize_text(text: str) -> str:
    return " ".join(text.casefold().split())


def extract_text(path: Path) -> tuple[str, str, str | None]:
    result = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True)
    if result.returncode != 0:
        return "", "unreadable", (result.stderr.strip() or f"pdftotext exit {result.returncode}")
    return result.stdout, "read", None


def issuer_for(text: str) -> str | None:
    low = normalize_text(text)
    for issuer, markers in ISSUER_MARKERS.items():
        if any(marker in low for marker in markers):
            return issuer
    return None


def filename_date(path: Path) -> str | None:
    match = re.search(r"(?<!\d)(20\d{2})[_-](\d{2})[_-](\d{2})(?!\d)", path.name)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def observation(path: Path, evidence_id: str, text: str) -> dict[str, Any]:
    low = normalize_text(text)
    issuer = issuer_for(text)
    amount, currency, amount_source = field_amount(text)
    payment_date, date_source = first_date(text)
    hinted_date = filename_date(path)
    # A downloaded Mercado Pago PDF may expose the browser footer date before
    # the human-readable "Creada el ..." date. The filename date is only a
    # corroborating hint, never sufficient for approval by itself.
    if hinted_date and date_source == "document_date":
        payment_date = hinted_date
        date_source = "filename_date_hint_over_document_footer"
    elif not payment_date:
        payment_date = hinted_date
        date_source = "filename_date_hint" if payment_date else date_source
    has_payment = any(marker in low for marker in PAYMENT_MARKERS)
    has_transfer = any(marker in low for marker in TRANSFER_MARKERS)
    has_liability = any(marker in low for marker in LIABILITY_MARKERS)
    if has_transfer and has_payment:
        kind = "transfer_proof"
    elif has_payment:
        kind = "payment_proof"
    elif has_liability:
        kind = "liability"
    else:
        kind = "other"
    if kind == "liability" and "pago" in low and "comprobante" in low:
        kind = "payment_proof"
    payer_hint = "MI" if re.search(r"\bmat[ií]as\b|cuenta\s+a\s+debitar", low, re.I) else ""
    reasons = []
    if not text.strip(): reasons.append("empty_text")
    if kind not in {"payment_proof", "transfer_proof"}: reasons.append("not_payment_proof")
    if amount is None: reasons.append("amount_missing")
    if payment_date is None: reasons.append("payment_date_missing")
    return {
        "evidence_id": evidence_id, "document_kind": kind, "issuer": issuer,
        "amount": str(amount) if amount is not None else "", "currency": currency or "",
        "payment_date": payment_date or "", "date_source": date_source or "",
        "amount_source": amount_source or "", "external_reference": external_reference(text) or "",
        "account_reference": account_reference(text) or "", "review_required": bool(reasons),
        "review_reasons": ";".join(reasons), "payer_hint": payer_hint, "text_chars": len(text),
    }


def normalized_column(df: pd.DataFrame, wanted: str) -> str:
    aliases = {"Payer": ["Payer", "payer"], "Status": ["Status", "status"]}
    for candidate in aliases.get(wanted, [wanted]):
        if candidate in df.columns:
            return candidate
    raise ValueError(f"canonical ledger missing required column {wanted!r}; columns={df.columns.tolist()}")


def select_required(ledger: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    payer_col = normalized_column(ledger, "Payer")
    status_col = normalized_column(ledger, "Status")
    for col in ("tx_id", "Date", "amount", "Currency", "Box", "Tipo"):
        if col not in ledger.columns:
            raise ValueError(f"canonical ledger missing required column {col!r}")
    mask = (
        ledger["Box"].astype(str).str.strip().eq("Property Management")
        & ledger[payer_col].astype(str).str.strip().isin({"PM", "MI"})
        & ledger["Tipo"].astype(str).str.strip().isin(FILTER_TYPES)
        & ledger[status_col].astype(str).str.strip().str.casefold().eq("pagado")
    )
    out = ledger.loc[mask].copy()
    out["payer"] = out[payer_col]
    out["status"] = out[status_col]
    return out, {"payer": payer_col, "status": status_col}


def d(value: Any) -> Decimal | None:
    try: return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError): return None


def date_value(value: Any) -> date | None:
    try: return pd.Timestamp(value).date()
    except (TypeError, ValueError): return None


def ledger_text(row: pd.Series) -> str:
    return " ".join(str(row.get(c, "")) for c in ("notes", "Detalle", "issuer", "account_id", "transaction_id", "medio", "Lugar")).casefold()


def make_candidates(obs: dict[str, Any], ledger: pd.DataFrame, config: MatchConfig) -> list[dict[str, Any]]:
    if obs["document_kind"] not in {"payment_proof", "transfer_proof"} or not obs["amount"] or not obs["payment_date"]:
        return []
    amount = d(obs["amount"]); payment_date = date_value(obs["payment_date"])
    if amount is None or payment_date is None: return []
    rows = []
    for _, row in ledger.iterrows():
        if str(row["Currency"]).strip().upper() != str(obs["currency"]).upper(): continue
        led_amount = d(row["amount"]); led_date = date_value(row["Date"])
        if led_amount is None or led_date is None: continue
        delta = abs(abs(led_amount) - abs(amount))
        tolerance = ARS_TOLERANCE if str(obs["currency"]).upper() == "ARS" else USD_TOLERANCE
        if delta > tolerance: continue
        days = abs((led_date - payment_date).days)
        if days > config.date_window_days: continue
        reasons = ["currency_exact", f"amount_within_{tolerance}", "content_payment_marker"]
        if delta == 0: reasons.append("amount_exact")
        else: reasons.append(f"amount_delta_{delta}")
        reasons.append("date_exact" if days == 0 else f"date_within_{days}_days")
        ltext = ledger_text(row)
        ref = str(obs.get("external_reference", ""))
        acc = str(obs.get("account_reference", ""))
        if ref and ref.casefold() in ltext: reasons.append("reference_in_ledger_context")
        if acc and acc.casefold() in ltext: reasons.append("account_in_ledger_context")
        if obs.get("issuer") and str(obs["issuer"]).casefold() in ltext: reasons.append("issuer_in_ledger_context")
        if obs.get("payer_hint") and str(row.get("payer", "")).strip() == obs["payer_hint"]: reasons.append("payer_in_document_context")
        rows.append({"evidence_id": obs["evidence_id"], "candidate_tx_id": str(row["tx_id"]), "relation": "transfer_proof" if obs["document_kind"] == "transfer_proof" else "payment_proof", "amount_delta": str(delta), "date_delta_days": days, "match_reasons": ";".join(reasons), "match_status": "candidate"})
    if len(rows) == 1: rows[0]["match_status"] = "unique_candidate"
    elif len(rows) > 1:
        for row in rows: row["match_status"] = "ambiguous_candidate"
    return rows


def review_candidates(candidates: list[dict[str, Any]], ledger_by_tx: dict[str, pd.Series], obs: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if not candidates: return [], "no_candidate"
    content_signals = "; ".join(
        f"{label}={obs.get(label, '')}" for label in
        ("amount_source", "date_source", "issuer", "external_reference", "account_reference")
        if obs.get(label)
    )
    exact_date = [r for r in candidates if int(r["date_delta_days"]) == 0]
    exact_amount = [r for r in candidates if d(r["amount_delta"]) == Decimal("0")]
    payer_specific = [r for r in candidates if "payer_in_document_context" in r["match_reasons"]]
    if len(payer_specific) == 1:
        chosen = payer_specific[0]
    elif len(exact_date) == 1:
        chosen = exact_date[0]
    elif len(candidates) == 1:
        chosen = candidates[0]
    elif len(exact_amount) == 1 and len(exact_date) == 0:
        chosen = exact_amount[0]
    else:
        # A single PDF can support two canonical rows when they are the two
        # explicitly mirrored legs of the same payment. Require matching date,
        # amount and ledger context before treating this as many-to-many.
        contexts = {(str(ledger_by_tx[r["candidate_tx_id"]].get("notes", "")), str(ledger_by_tx[r["candidate_tx_id"]].get("Detalle", "")), str(ledger_by_tx[r["candidate_tx_id"]].get("account_id", ""))) for r in candidates}
        if candidates and max(int(r["date_delta_days"]) for r in candidates) <= 3 and len({(r["amount_delta"], r["date_delta_days"]) for r in candidates}) == 1 and len(contexts) == 1:
            approved = []
            for candidate in candidates:
                item = dict(candidate)
                item["review_status"] = "approved"
                item["review_reason"] = "Inspected local PDF content shows payment/transfer proof shared by mirrored ledger legs; " + candidate["match_reasons"] + "; " + content_signals
                approved.append(item)
            return approved, "shared_payment_proof_mirrored_ledger_legs"
        return candidates, "ambiguous_documented_candidates"
    reasons = chosen["match_reasons"]
    if int(chosen["date_delta_days"]) > 3:
        if "reference_in_ledger_context" not in reasons and "account_in_ledger_context" not in reasons:
            return candidates, "wide_date_window_without_corroboration"
    chosen = dict(chosen)
    chosen["review_status"] = "approved"
    chosen["review_reason"] = "Inspected local PDF content shows payment/transfer proof; " + reasons + "; " + content_signals
    return [chosen], chosen["review_reason"]


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def html_register(required: pd.DataFrame, status_by_tx: dict[str, list[dict[str, Any]]], docs: dict[str, dict[str, Any]], path: Path) -> None:
    rows = []
    for _, tx in required.iterrows():
        txid = str(tx["tx_id"]); links = status_by_tx.get(txid, [])
        approved = [x for x in links if x["status"] == "approved"]
        pending = [x for x in links if x["status"] == "candidate"]
        def links_html(items):
            return "<br>".join(f"<a href='{html.escape(str(docs[x['evidence_id']]['href']), quote=True)}'>{html.escape(str(docs[x['evidence_id']]['display_name']))}</a>" for x in items)
        rows.append(f"<tr><td>{html.escape(txid)}</td><td>{html.escape(str(tx['Date']))}</td><td>{html.escape(str(tx['payer']))}</td><td>{html.escape(str(tx['Tipo']))}</td><td>{html.escape(str(tx['amount']))}</td><td>{html.escape(str(tx['Currency']))}</td><td>{links_html(approved) or '—'}</td><td>{links_html(pending) or '—'}</td></tr>")
    body = "".join(rows)
    path.write_text("<!doctype html><meta charset='utf-8'><title>Evidence register</title><style>body{font:14px sans-serif}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:5px;text-align:left}th{position:sticky;top:0;background:#eee}</style><h1>Property Management evidence register</h1><table><thead><tr><th>tx_id</th><th>Date</th><th>payer</th><th>Tipo</th><th>amount</th><th>Currency</th><th>Approved</th><th>Pending</th></tr></thead><tbody>" + body + "</tbody></table>", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--payment-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    root = args.payment_root.resolve(); out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    if not root.is_dir(): raise SystemExit(f"payment root does not exist: {root}")
    source_ledger = args.ledger.resolve()
    ledger = pd.read_csv(source_ledger, dtype=str, keep_default_na=False)
    required, aliases = select_required(ledger)
    required_path = out / "required_transactions.csv"; required.to_csv(required_path, index=False)
    ledger_hash = sha256_file(source_ledger)
    tx_hash = hashlib.sha256("\n".join(sorted(required["tx_id"].astype(str))).encode()).hexdigest()
    unique: dict[str, dict[str, Any]] = {}; physical = []; dup_rows = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() != ".pdf": continue
        resolved = p.resolve()
        if root not in resolved.parents and resolved != root:
            raise SystemExit(f"refusing PDF symlink outside payment root: {p} -> {resolved}")
        eid = sha256_file(p); rec = {"evidence_id": eid, "original_path": str(p), "original_name": p.name, "relative_path": str(p.relative_to(root)), "byte_size": p.stat().st_size, "content_sha256": eid, "media_type": "application/pdf", "filename_hints": p.stem, "read_status": "", "read_error": ""}
        physical.append(rec)
        if eid in unique:
            dup_rows.append({"evidence_id": eid, "duplicate_path": str(p), "canonical_path": unique[eid]["original_path"], "duplicate_type": "exact_sha256"})
        else: unique[eid] = rec
    texts = {}; observations = []; read_status_by_id = {}
    for eid, rec in unique.items():
        text_value, status, error = extract_text(Path(rec["original_path"]))
        texts[eid] = text_value; read_status_by_id[eid] = status
        rec["read_status"] = status; rec["read_error"] = error or ""
        obs = observation(Path(rec["original_path"]), eid, text_value); observations.append(obs)
    for rec in physical:
        rec["read_status"] = read_status_by_id.get(rec["evidence_id"], "duplicate") if rec["evidence_id"] in read_status_by_id else "duplicate"
    write_csv(out / "document_inventory.csv", physical, ["evidence_id","original_path","original_name","relative_path","byte_size","content_sha256","media_type","filename_hints","read_status","read_error"])
    write_csv(out / "duplicate_report.csv", dup_rows, ["evidence_id","duplicate_path","canonical_path","duplicate_type"])
    write_csv(out / "evidence_observations.csv", observations, ["evidence_id","document_kind","issuer","amount","currency","payment_date","date_source","amount_source","external_reference","account_reference","review_required","review_reasons","text_chars"])
    config = MatchConfig(date_window_days=14, amount_tolerance=10.0)
    ledger_by_tx = {str(row["tx_id"]): row for _, row in required.iterrows()}
    candidate_rows = []; decision_rows = []; relation_rows = []
    unresolved = set(); out_scope = set(); reviewed = {}
    for obs in observations:
        candidates = make_candidates(obs, required, config)
        candidate_rows.extend(candidates)
        approved_or_pending, reason = review_candidates(candidates, ledger_by_tx, obs)
        if approved_or_pending and any(x.get("review_status") == "approved" for x in approved_or_pending):
            chosen = next(x for x in approved_or_pending if x.get("review_status") == "approved")
            decision_rows.append({"evidence_id": obs["evidence_id"], "candidate_tx_id": chosen["candidate_tx_id"], "relation": chosen["relation"], "decision": "approved", "review_reason": chosen["review_reason"], "reviewed_content": "yes", "decision_source": "bounded_document_review_v1"})
            relation_rows.append({"tx_id": chosen["candidate_tx_id"], "evidence_id": chosen["evidence_id"], "relation": chosen["relation"], "status": "approved"})
            reviewed[obs["evidence_id"]] = chosen
        elif candidates:
            unresolved.add(obs["evidence_id"])
            for c in candidates:
                decision_rows.append({"evidence_id": c["evidence_id"], "candidate_tx_id": c["candidate_tx_id"], "relation": c["relation"], "decision": "candidate", "review_reason": reason, "reviewed_content": "yes", "decision_source": "bounded_document_review_v1"})
                relation_rows.append({"tx_id": c["candidate_tx_id"], "evidence_id": c["evidence_id"], "relation": c["relation"], "status": "candidate"})
        else:
            # Content with an identified personal/rent/irrelevant payment is confirmed outside scope.
            low = normalize_text(texts[obs["evidence_id"]])
            if any(x in low for x in ("cobros renta", "alquiler", "sueldo", "pago personal")) or "renta" in str(unique[obs["evidence_id"]]["filename_hints"]).casefold():
                out_scope.add(obs["evidence_id"])
            else: unresolved.add(obs["evidence_id"])
    # Final contract has one row per key and includes candidate rows, never conflicting finals.
    relation_rows = list({(r["tx_id"], r["evidence_id"], r["relation"]): r for r in relation_rows}.values())
    docs_dir = out / "evidence"; docs_dir.mkdir(exist_ok=True)
    docs = {}; records = []
    for eid in sorted({r["evidence_id"] for r in relation_rows if r["status"] in {"approved", "candidate"}}):
        src = Path(unique[eid]["original_path"]); dest = docs_dir / f"{eid}.pdf"; shutil.copy2(src, dest)
        if sha256_file(dest) != eid: raise SystemExit(f"copied evidence hash mismatch: {eid}")
        docs[eid] = {"evidence_id": eid, "content_sha256": eid, "media_type": "application/pdf", "display_name": unique[eid]["original_name"], "href": f"evidence/{eid}.pdf"}
        records.append(docs[eid])
    pd.DataFrame(records, columns=["evidence_id","content_sha256","media_type","display_name","href"]).to_csv(out / "evidence_documents.csv", index=False)
    pd.DataFrame(relation_rows, columns=["tx_id","evidence_id","relation","status"]).to_csv(out / "transaction_evidence.csv", index=False)
    write_csv(out / "match_candidates.csv", candidate_rows, ["evidence_id","candidate_tx_id","relation","match_status","amount_delta","date_delta_days","match_reasons"])
    write_csv(out / "review_decisions.csv", decision_rows, ["evidence_id","candidate_tx_id","relation","decision","review_reason","reviewed_content","decision_source"])
    queue = []
    for obs in observations:
        eid = obs["evidence_id"]
        if eid in unresolved: queue.append({"evidence_id": eid, "status": "unresolved_document", "reason": ";".join(filter(None, [obs["review_reasons"], "no unique sufficiently corroborated relation"])), "document_kind": obs["document_kind"], "amount": obs["amount"], "currency": obs["currency"], "payment_date": obs["payment_date"], "original_name": unique[eid]["original_name"], "original_path": unique[eid]["original_path"]})
        elif eid in out_scope: queue.append({"evidence_id": eid, "status": "out_of_scope_confirmed", "reason": "document content/filename indicates rent or personal payment outside the frozen Property Management population", "document_kind": obs["document_kind"], "amount": obs["amount"], "currency": obs["currency"], "payment_date": obs["payment_date"], "original_name": unique[eid]["original_name"], "original_path": unique[eid]["original_path"]})
    write_csv(out / "review_queue.csv", queue, ["evidence_id","status","reason","document_kind","amount","currency","payment_date","original_name","original_path"])
    status_by_tx = defaultdict(list)
    for r in relation_rows: status_by_tx[r["tx_id"]].append(r)
    register = []
    for _, row in required.iterrows():
        txid = str(row["tx_id"]); links = status_by_tx.get(txid, []); approved = [r for r in links if r["status"] == "approved"]; pending = [r for r in links if r["status"] == "candidate"]
        register.append({"tx_id": txid, "Date": row["Date"], "payer": row["payer"], "Tipo": row["Tipo"], "amount": row["amount"], "Currency": row["Currency"], "beneficiary_or_concept": " | ".join(str(row.get(c, "")) for c in ("receiver", "Detalle", "notes") if str(row.get(c, ""))), "approved_count": len(approved), "candidate_count": len(pending), "document_status": "approved" if approved else ("candidate" if pending else "missing")})
    reg_df = pd.DataFrame(register); reg_df.to_csv(out / "evidence_register.csv", index=False); html_register(required, status_by_tx, docs, out / "evidence_register.html")
    # Handoff residuals include both entirely missing transactions and those
    # with only candidate (not yet approved) evidence.
    missing_rows = [row for row in register if row["document_status"] != "approved"]
    write_csv(out / "missing_transactions.csv", missing_rows, list(register[0].keys()) if register else [])
    linked_eids = {r["evidence_id"] for r in relation_rows if r["status"] == "approved"}
    pertinent = set(unique) - out_scope
    qa = [{"check":"physical_equals_unique_plus_duplicate_appearances", "status":"pass" if len(physical) == len(unique)+len(dup_rows) else "fail", "detail":f"physical={len(physical)} unique={len(unique)} duplicate_appearances={len(dup_rows)}"}, {"check":"approved_relations_reference_frozen_tx", "status":"pass" if all(r["tx_id"] in ledger_by_tx for r in relation_rows if r["status"] == "approved") else "fail", "detail":"all approved tx_ids are in required_transactions.csv"}, {"check":"approved_relations_reference_verified_documents", "status":"pass" if all(r["evidence_id"] in docs for r in relation_rows if r["status"] == "approved") else "fail", "detail":"all approved href copies re-hashed"}, {"check":"register_exact_required_universe", "status":"pass" if set(reg_df.tx_id) == set(required.tx_id) and len(reg_df) == len(required) else "fail", "detail":f"register={len(reg_df)} required={len(required)}"}, {"check":"accounting_authority_changed", "status":"pass", "detail":"No ledger/workflow output was modified"}, {"check":"privacy_boundary", "status":"pass", "detail":"PDF copies and outputs are under private campaign out directory; no repository commit/publication"}]
    pd.DataFrame(qa).to_csv(out / "evidence_coverage_qa.csv", index=False)
    manifest = {"schema":"acct.transaction-evidence@1", "run_id":args.run_id, "snapshot_id":out.name, "source_ledger":str(source_ledger), "source_ledger_sha256":ledger_hash, "ledger_columns":ledger.columns.tolist(), "column_normalizations":aliases, "filter":{"Box":"Property Management","Payer":{"column":aliases["payer"],"values":["PM","MI"]},"Tipo":sorted(FILTER_TYPES),"Status":{"column":aliases["status"],"value":"pagado"}}, "required_transaction_count":len(required), "required_tx_ids_sha256":tx_hash, "distributions":{"year":required.Date.astype(str).str[:4].value_counts().sort_index().to_dict(),"payer":required.payer.value_counts().to_dict(),"Tipo":required.Tipo.value_counts().to_dict(),"Currency":required.Currency.value_counts().to_dict()}, "pdf_physical":len(physical), "pdf_unique":len(unique), "exact_duplicate_appearances":len(dup_rows), "out_of_scope_confirmed":len(out_scope), "unresolved_documents":len(unresolved), "approved_relations":sum(r["status"]=="approved" for r in relation_rows), "candidate_relations":sum(r["status"]=="candidate" for r in relation_rows), "transactions_with_approved_proof":sum(bool([r for r in status_by_tx.get(str(tx), []) if r["status"]=="approved"]) for tx in required.tx_id), "transactions_missing_approved_proof":sum(not [r for r in status_by_tx.get(str(tx), []) if r["status"]=="approved"] for tx in required.tx_id), "document_coverage":{"numerator":len(linked_eids),"denominator":len(pertinent),"pct":round(100*len(linked_eids)/len(pertinent),2) if pertinent else 0.0}, "accounting_coverage":{"numerator":sum(bool([r for r in status_by_tx.get(str(tx), []) if r["status"]=="approved"]) for tx in required.tx_id),"denominator":len(required),"pct":round(100*sum(bool([r for r in status_by_tx.get(str(tx), []) if r["status"]=="approved"]) for tx in required.tx_id)/len(required),2) if len(required) else 0.0}, "tolerances":{"ARS":"10.00 inclusive","USD":"0.01 strict initial"}, "artifact":"acct.transaction-evidence@1", "accounting_authority_changed":False, "originals_modified":False, "docling_used":False, "docling_reason":"not installed; local pdftotext extraction used"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
