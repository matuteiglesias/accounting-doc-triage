#!/usr/bin/env python3
"""
Smarter mover for triage outputs.

Put in ./scripts/move_triage_files.py and call from your run script.

Example dry-run (safe):
  ./scripts/move_triage_files.py --pf 4_Analysis_Workflows/triage_output_from_pf.jsonl \
    --orig 4_Analysis_Workflows/triage_input_with_id.jsonl

Apply with rename + backup:
  ./scripts/move_triage_files.py --pf ... --orig ... --apply --backup

See --help for options.
"""
from pathlib import Path
import json
import shutil
import argparse
import time
import tarfile
import sys
import re
import unicodedata

FALLBACK_INBOX_SUBDIR = "00_inbox"
MAX_FILENAME_LEN = 220

def parse_args():
    p = argparse.ArgumentParser(description="Move & (optionally) rename files according to triage outputs.")
    p.add_argument("--pf", default="4_Analysis_Workflows/triage_output_from_pf.jsonl", help="PromptFlow output JSONL")
    p.add_argument("--orig", default="4_Analysis_Workflows/triage_input_with_id.jsonl", help="Original indexed JSONL with metadata")
    p.add_argument("--base-input", default="1_Input_Raw", help="Base folder where raw files live")
    p.add_argument("--out", default="moved", help="Destination base folder")
    p.add_argument("--log", default=None, help="Log file path")
    p.add_argument("--apply", action="store_true", help="Perform moves (default is dry-run)")
    p.add_argument("--backup", action="store_true", help="Create a tar.gz backup of the inbox before apply")
    p.add_argument("--wipe-inbox", action="store_true", help="Delete remaining files in the inbox after apply (dangerous)")
    p.add_argument("--dry-run", action="store_true", help="Force dry-run")
    p.add_argument("--allow-filename-fallback", action="store_true", help="If metadata missing, attempt filename lookup under base-input")
    p.add_argument("--quarantine-on-missing", action="store_true", help="If essential fields missing, move file to moved/quarantine/<ts> instead of role folder")
    p.add_argument("--no-rename", action="store_true", help="Do not rename files; only move (keep original filename)")
    return p.parse_args()

def resolve_meta_path(md):
    if not md:
        return None
    for k in ("doc_path", "file_path", "path"):
        if k in md and md[k]:
            return md[k]
    return None

def make_backup(inbox_dir: Path, backups_dir: Path):
    ts = time.strftime("%Y%m%dT%H%M%S")
    backups_dir.mkdir(parents=True, exist_ok=True)
    archive = backups_dir / f"inbox_backup_{ts}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(str(inbox_dir), arcname=inbox_dir.name)
    return archive

def build_orig_map(orig_path: Path):
    m = {}
    if not orig_path.exists():
        print("WARN: orig file not found:", orig_path, file=sys.stderr)
        return m
    with orig_path.open("r", encoding="utf8") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception as e:
                print(f"skip invalid json in orig at line {i}: {e}", file=sys.stderr)
                continue
            key = obj.get("id") or obj.get("id")
            if not key:
                continue
            meta = obj.get("metadata") or {}
            if not meta and "filename" in obj:
                meta = {"filename": obj["filename"]}
            m[key] = meta
    return m

def find_file_by_filename(base_input_dir: Path, filename: str):
    if not filename:
        return None
    candidates = [
        base_input_dir / filename,
        base_input_dir / FALLBACK_INBOX_SUBDIR / filename
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    # last resort: limited recursive search
    for p in base_input_dir.rglob(filename):
        if p.is_file():
            return p.resolve()
    return None

# ---------- helpers for deterministic naming ----------
def sanitize_text(s: str) -> str:
    if s is None:
        return ""
    # normalize accents to ascii
    s = unicodedata.normalize("NFKD", str(s))
    s = s.encode("ascii", "ignore").decode("ascii")
    # replace non-alnum with underscore
    s = re.sub(r"[^0-9A-Za-z]+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_").lower()
    return s[:60]  # keep hint reasonably short

def valid_iso_date(s: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", s or ""))

def pick_best_date(tri: dict):
    # Payment-specific: try common keys
    for k in ("payment_date",):
        v = tri.get(k)
        if isinstance(v, str) and valid_iso_date(v):
            return v
    # normalized_dates may exist
    nd = tri.get("normalized_dates") or tri.get("dates")
    if isinstance(nd, list) and nd:
        for cand in nd:
            if isinstance(cand, str) and valid_iso_date(cand):
                return cand
    # fallback: try any YYYY-... pattern in strings (last resort)
    for k in ("review_reason","summary"):
        txt = tri.get(k) or ""
        m = re.search(r"(\d{4}-\d{2}-\d{2})", txt)
        if m:
            return m.group(1)
    return None

def pick_best_amount_cents(tri: dict):
    # try direct field
    v = tri.get("amount_cents")
    if isinstance(v, int) and v >= 0:
        return v
    # try amounts array with value_cents
    amounts = tri.get("amounts") or []
    if isinstance(amounts, list) and amounts:
        # choose the max non-null 'value_cents' (heuristic: total)
        vals = [a.get("value_cents") for a in amounts if isinstance(a, dict) and isinstance(a.get("value_cents"), int)]
        if vals:
            return max(vals)
    # try other common names
    for k in ("total_cents","gross_cents"):
        v = tri.get(k)
        if isinstance(v, int):
            return v
    return None

def amount_cents_to_pesos_str(cents: int):
    # returns integer pesos without decimals (cents // 100)
    if cents is None:
        return None
    try:
        return str(int(cents) // 100)
    except Exception:
        return None

# add near top if not already present
import os

def extract_year_for_statement(tri: dict):
    """
    Prefer year from due_date -> statement_period -> period_start -> any normalized date.
    Returns string YYYY or 'undated' if none available.
    """
    for k in ("due_date",):
        v = tri.get(k)
        if isinstance(v, str) and len(v) >= 4:
            return v[:4]
    sp = tri.get("statement_period")
    if isinstance(sp, str) and len(sp) >= 4:
        return sp[:4]
    ps = tri.get("period_start")
    if isinstance(ps, str) and len(ps) >= 4:
        return ps[:4]
    nd = tri.get("normalized_dates") or tri.get("normalized_date") or tri.get("dates") or []
    if isinstance(nd, list) and nd:
        for cand in nd:
            if isinstance(cand, str) and len(cand) >= 4:
                return cand[:4]
    # last resort: fall back to current year
    return time.strftime("%Y")

def _safe_issuer_slug_from_tri(tri: dict) -> str:
    """
    Use issuer_slug exactly if present and looks canonical-ish (no spaces, lowercase).
    Otherwise sanitize minimally. Do NOT attempt heavy canonicalization here — that should
    happen earlier when building the LLM schema or in a post-processing step.
    """
    raw = tri.get("issuer_slug")
    if raw and isinstance(raw, str) and raw.strip():
        s = raw.strip()
        # if user already supplied typical slug form, keep it (only normalize whitespace)
        if re.match(r'^[a-z0-9_]+$', s):
            return s
        # otherwise sanitize to a safe slug (lower, underscores)
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        s = re.sub(r'[^0-9a-z]+', '_', s.lower()).strip('_')
        return s[:40] or "unknown_issuer"
    # fallback to other tri fields
    fallback = tri.get("issuer") or tri.get("filename_hint") or "unknown_issuer"
    fallback = unicodedata.normalize("NFKD", str(fallback)).encode("ascii", "ignore").decode("ascii")
    fallback = re.sub(r'[^0-9a-z]+', '_', fallback.lower()).strip('_')
    return fallback[:40] or "unknown_issuer"

def build_target_filename(orig_name: str, tri: dict, id: str, no_rename: bool):
    """
    Build a compact, deterministic filename suitable for both payments and statements.
    For statements we prefer: {due_date}_{amount_pesos}_{issuer}_{unit_or_invoice}_{shortid}.pdf
    For payments we keep existing logic (date_amount_ref_hint...)
    """
    if no_rename:
        return orig_name

    ext = Path(orig_name).suffix.lower() or ".pdf"
    # numeric amounts
    amount_cents = pick_best_amount_cents(tri)
    amount_pesos = amount_cents_to_pesos_str(amount_cents) if amount_cents is not None else ""

    # common short id
    shortid = (id or "")[:8]

    # prefer LLM provided issuer slug (do minimal sanitization)
    issuer_slug = _safe_issuer_slug_from_tri(tri)

    # prefer unit_slug or invoice_number for statements, otherwise filename_hint
    unit_or_invoice = None
    if tri.get("unit_slug"):
        unit_or_invoice = re.sub(r'[^0-9A-Za-z_\-]+', '_', str(tri.get("unit_slug")))[:24]
    elif tri.get("invoice_number"):
        unit_or_invoice = re.sub(r'[^0-9A-Za-z_\-]+', '_', str(tri.get("invoice_number")))[:24]
    else:
        fh = tri.get("filename_hint") or ""
        unit_or_invoice = sanitize_text(fh)[:24] if fh else ""

    # statement-specific filename when doc_role == "statement"
    if tri.get("doc_role") == "statement":
        due_date = tri.get("due_date") or tri.get("period_start") or tri.get("statement_period") or ""
        # normalize statement_period YYYY-MM -> YYYY-MM-01 if used in filename (we prefer due_date)
        if re.match(r"^\d{4}-\d{2}$", due_date):
            # keep YYYY-MM (safe) — but due_date is preferred for precise partitioning
            pass
        parts = []
        if due_date:
            parts.append(due_date)
        if amount_pesos:
            parts.append(amount_pesos)
        parts.append(issuer_slug)
        if unit_or_invoice:
            parts.append(unit_or_invoice)
        parts.append(shortid or sanitize_text(Path(orig_name).stem)[:8])

        base = "_".join([p for p in parts if p])
        base = re.sub(r'_+', '_', base)
        filename = f"{base}{ext}"
        if len(filename) > MAX_FILENAME_LEN:
            stem = Path(filename).stem[:MAX_FILENAME_LEN - len(ext)]
            filename = f"{stem}{ext}"
        return filename

    # default (payments / other) fallback — keep previous logic but ensure short id always included
    date = pick_best_date(tri) or ""
    ref = (tri.get("payment_reference") or tri.get("transaction_id") or "")[:40]
    hint = (tri.get("filename_hint") or issuer_slug or "")[:40]

    parts = []
    if date:
        parts.append(date)
    if amount_pesos:
        parts.append(amount_pesos)
    if ref:
        parts.append(sanitize_text(ref))
    else:
        parts.append(shortid)
    if hint:
        parts.append(sanitize_text(hint))

    base = "_".join([p for p in parts if p])
    base = sanitize_text(base)
    filename = f"{base}{ext}"
    if len(filename) > MAX_FILENAME_LEN:
        stem = Path(filename).stem[:MAX_FILENAME_LEN - len(ext)]
        filename = f"{stem}{ext}"
    return filename

def build_target_path_for_role(out_dir: Path, role: str, tri: dict, target_filename: str):
    """
    New folder layout:
      OUT_DIR/<role>/<YYYY>/<issuer_slug>/
    Year resolution:
      - for statements use due_date year -> statement_period year -> period_start year -> current year (last resort)
      - for payments prefer payment_date year
    """
    role = role or "other"
    if role == "statement":
        year = extract_year_for_statement(tri)
    else:
        # payments: try payment_date then normalized_dates etc.
        pd = pick_best_date(tri)
        if pd:
            year = pd[:4]
        else:
            year = extract_year_for_statement(tri)

    issuer_slug = _safe_issuer_slug_from_tri(tri)
    role_folder = out_dir / role / year / issuer_slug
    return role_folder.resolve(), target_filename



# # put near top of file with imports
# import re
# import difflib

# # whitelist of canonical issuer slugs (expand over time)
# ISSUER_WHITELIST = [
#     "aysa", "agip", "metrogas", "edenor", "epec", "municipalidad_tigre",
#     "rapipago", "mercadopago", "mastercard", "visa", "banco_nacion",
#     "anses", "aysa_oficina", "aguas", "other"
# ]

# # regex patterns mapping
# ISSUER_PATTERNS = [
#     (re.compile(r"\baysa\b", re.I), "aysa"),
#     (re.compile(r"\bagip\b|\babl\b", re.I), "agip"),
#     (re.compile(r"\bmetroga?s\b", re.I), "metrogas"),
#     (re.compile(r"\bedenor\b", re.I), "edenor"),
#     (re.compile(r"\brapi ?pago\b", re.I), "rapipago"),
#     (re.compile(r"\bmastercard\b", re.I), "mastercard"),
#     (re.compile(r"\bmercadopago\b|\bmercado ?pago\b", re.I), "mercadopago"),
#     (re.compile(r"\bmunicipalidad\b.*\btigre\b", re.I), "municipalidad_tigre"),
#     # add more rules as you discover issuers
# ]

# def canonicalize_issuer(raw: str):
#     if not raw:
#         return "unknown_issuer", None
#     s = str(raw).strip()
#     # try regex rules first
#     for pat, slug in ISSUER_PATTERNS:
#         if pat.search(s):
#             return slug, slug
#     # try exact lower normalization
#     low = re.sub(r"[^0-9a-z]+", "_", s.lower()).strip("_")
#     if low in ISSUER_WHITELIST:
#         return low, low
#     # fuzzy match to whitelist
#     candidates = difflib.get_close_matches(low, ISSUER_WHITELIST, n=1, cutoff=0.75)
#     if candidates:
#         return candidates[0], candidates[0]
#     # fallback: sanitized short text but mark as unknown (so path uses unknown_issuer)
#     sanitized = re.sub(r"[^0-9a-z]+", "_", s.lower())[:40]
#     return "unknown_issuer", sanitized

# minimal validator (no external deps required)

def validate_triage(tri):
    """
    Role-aware validation:
      - payment: ensure payment_date, amount_cents (int), currency (ISO3)
      - statement: ensure due_date (or a pick_best_date), total_amount_cents (int), issuer_slug, currency
      - other: be permissive (only check currency if present)
    Returns: list of error strings (empty => OK)
    """
    errs = []
    role = (tri.get("doc_role") or "").lower()

    # common currency check helper
    def _valid_currency(c):
        return bool(re.match(r"^[A-Z]{3}$", str(c or "")))

    if role == "payment":
        # payment: strict
        if tri.get("doc_role") != "payment":
            errs.append("doc_role!=payment")
        # prefer explicit payment_date or try pick_best_date
        pay_date = tri.get("payment_date") or pick_best_date(tri)
        if not pay_date or not valid_iso_date(pay_date):
            errs.append("missing or invalid payment_date")
        if not isinstance(tri.get("amount_cents"), int):
            errs.append("missing or invalid amount_cents")
        if not _valid_currency(tri.get("Currency")):
            errs.append("missing or invalid currency")
    elif role == "statement":
        # statement: require due_date (or best date), total_amount_cents, issuer_slug
        if tri.get("doc_role") != "statement":
            errs.append("doc_role!=statement")
        due = tri.get("due_date") or pick_best_date(tri)
        if not due or not valid_iso_date(due):
            errs.append("missing or invalid due_date")
        if not isinstance(tri.get("total_amount_cents"), int):
            errs.append("missing or invalid total_amount_cents")
        if not tri.get("issuer_slug"):
            errs.append("missing issuer_slug")
        if not _valid_currency(tri.get("Currency")):
            errs.append("missing or invalid currency")
    else:
        # permissive for other roles (no hard quarantine)
        if tri.get("Currency") and not _valid_currency(tri.get("Currency")):
            errs.append("invalid currency")

    return errs



# def build_target_path_for_role(out_dir: Path, role: str, tri: dict, target_filename: str):
#     date = pick_best_date(tri)
#     year = date[:4] if date and len(date) >= 4 else "undated"
#     raw_issuer = tri.get("issuer_slug") or tri.get("issuer") or tri.get("filename_hint")
#     issuer_slug, issuer_note = canonicalize_issuer(raw_issuer)
#     role_folder = out_dir / role / year / issuer_slug
#     return role_folder.resolve(), target_filename



# --- robust path resolution (replacement) ---
def resolve_meta_to_path(meta_path, base: Path, base_input_dir: Path):
    """
    Try multiple sensible resolutions in order:
      1) if meta_path is absolute and exists -> use it
      2) base / meta_path (repo-root relative)
      3) base_input_dir / meta_path (legacy inbox-root relative)
      4) find by filename under base_input_dir
    Returns tuple(Path_or_None, reason_str)
    """
    if not meta_path:
        return None, "empty_meta"

    cand = Path(meta_path)
    # 1) absolute or as given
    try:
        if cand.is_absolute() and cand.exists():
            return cand.resolve(), "absolute"
        # try expanduser then check
        cand2 = Path(os.path.expanduser(meta_path))
        if cand2.exists():
            return cand2.resolve(), "expanded_user"
    except Exception:
        pass

    # 2) repo-root relative (BASE / meta_path) — handles "moved/..." cases
    try:
        cand = (base / meta_path).resolve()
        if cand.exists():
            return cand, "base_relative"
    except Exception:
        pass

    # 3) base_input_dir relative (legacy behaviour)
    try:
        cand = (base_input_dir / meta_path).resolve()
        if cand.exists():
            return cand, "base_input_relative"
    except Exception:
        pass

    # 4) try base_input_dir filename search (fast fallback)
    try:
        name = Path(meta_path).name
        f = find_file_by_filename(base_input_dir, name)
        if f:
            return f.resolve(), "found_by_filename"
    except Exception:
        pass

    return None, "not_found"


# ---------- main ----------
def main():
    args = parse_args()
    PF = Path(args.pf)
    ORIG = Path(args.orig)
    BASE = Path.cwd()
    BASE_INPUT_DIR = (BASE / args.base_input).resolve()
    OUT_DIR = (BASE / args.out).resolve()
    LOG_TS = time.strftime("%Y%m%dT%H%M%S")
    LOG = Path(args.log) if args.log else (OUT_DIR / f"move_report_{LOG_TS}.log")
    BACKUPS = BASE / "backups"

    if not PF.exists():
        print("ERROR: pf file not found:", PF)
        return 2

    orig_map = build_orig_map(ORIG)

    # ensure base folders exist
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for sub in ("payment","statement","other","quarantine"):
        (OUT_DIR / sub).mkdir(parents=True, exist_ok=True)

    dry = (not args.apply) or args.dry_run
    rename_enabled = not args.no_rename

    if args.backup and args.apply:
        print("Creating backup of inbox (this may take a while)...")
        archive = make_backup(BASE_INPUT_DIR, BACKUPS)
        print("Backup created at:", archive)
    elif args.backup and not args.apply:
        print("Backup requested but --apply not provided. Backup will only be created when you run with --apply.")

    counters = {"moved":0, "missing_path":0, "not_found":0, "move_failed":0, "skipped":0, "resolved_by_filename":0, "quarantined":0}

    with open(LOG, "w", encoding="utf8") as logf, open(PF, "r", encoding="utf8") as fh:
        header = f"# move_triage_files smart log {time.ctime()}\n# PF: {PF}\n# ORIG: {ORIG}\n# BASE_INPUT_DIR: {BASE_INPUT_DIR}\n# OUT_DIR: {OUT_DIR}\n# DRY_RUN: {dry}\n# RENAME_ENABLED: {rename_enabled}\n\n"
        logf.write(header)
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception as e:
                logf.write(f"{i}\tINVALID_JSON\t{e}\n")
                counters["skipped"] += 1
                continue

            pf_key = obj.get("id") or obj.get("id")
            if not pf_key:
                pf_key = obj.get("line_number")
            id_for_log = pf_key or "<no-key>"

            tri = obj.get("triage_result", {}) or {}
            role = (tri.get("doc_role") or "other")
            meta = obj.get("metadata") or {}



            errs = validate_triage(tri)
            if errs:
                role = (tri.get("doc_role") or "other").lower()
                logf.write(f"{i}\t{id_for_log}\tVALIDATION_FAILED\t{errs}\n")

                # Payment rows: keep existing conservative behaviour (skip/mask for manual review)
                # Statements: don't auto-quarantine — let the mover attempt normal resolution & rename
                # Other roles: permissive by default
                if role == "payment":
                    counters["quarantined"] += 1
                    # we do NOT attempt to move files here because path resolution happens later
                    # the admin can inspect the log and decide to re-run with fixes
                    continue
                else:
                    # For statements and others we'll allow the pipeline to continue and try to move/rename.
                    # This helps when documents are statements (your new schema) and so they can be placed/renamed.
                    # If you prefer to quarantine statements too, change this behavior.
                    pass






            # try orig_map if metadata missing
            if (not meta or not resolve_meta_path(meta)) and pf_key and pf_key in orig_map:
                meta = orig_map.get(pf_key) or {}

            meta_path = resolve_meta_path(meta)
            filename = meta.get("filename") if isinstance(meta, dict) else None

            if not meta_path and filename and args.allow_filename_fallback:
                candidate = find_file_by_filename(BASE_INPUT_DIR, filename)
                if candidate:
                    try:
                        rel = candidate.relative_to(BASE)
                        meta_path = str(rel)
                    except Exception:
                        meta_path = str(candidate)
                    counters["resolved_by_filename"] += 1

            if not meta_path:
                logf.write(f"{i}\t{id_for_log}\tMISSING_PATH\trole={role}\n")
                counters["missing_path"] += 1
                # optionally quarantine
                if args.quarantine_on_missing and args.apply and not dry:
                    TS = time.strftime("%Y%m%dT%H%M%S")
                    QUAR = OUT_DIR / "quarantine" / TS
                    QUAR.mkdir(parents=True, exist_ok=True)
                    # try to move by filename fallback if available
                    if filename:
                        candidate = find_file_by_filename(BASE_INPUT_DIR, filename)
                        if candidate and candidate.exists():
                            target = QUAR / candidate.name
                            try:
                                shutil.move(str(candidate), str(target))
                                logf.write(f"{i}\t{id_for_log}\tQUARANTINED_BY_FILENAME\t{candidate} -> {target}\n")
                                counters["quarantined"] += 1
                            except Exception as e:
                                logf.write(f"{i}\t{id_for_log}\tQUARANTINE_MOVE_FAILED\t{candidate} -> {target} : {e}\n")
                                counters["move_failed"] += 1
                    continue
                continue

            # use it
            p_resolved, reason = resolve_meta_to_path(meta_path, BASE, BASE_INPUT_DIR)
            if p_resolved is None:
                logf.write(f"{i}\t{id_for_log}\tNOT_FOUND\trole={role}\tsrc={meta_path}\treason={reason}\n")
                counters["not_found"] += 1
                continue
            p = Path(p_resolved)

            # decide whether to quarantine due to missing essential fields (payments)
            should_quarantine = False
            if args.quarantine_on_missing and role == "payment":
                # ensure minimal fields present for deterministic rename: date + amount or filename_hint/id
                date = pick_best_date(tri)
                amount = pick_best_amount_cents(tri)
                if not date or amount is None:
                    should_quarantine = True

            if should_quarantine and args.apply and not dry:
                TS = time.strftime("%Y%m%dT%H%M%S")
                QUAR = OUT_DIR / "quarantine" / TS
                QUAR.mkdir(parents=True, exist_ok=True)
                target = QUAR / p.name
                try:
                    shutil.move(str(p), str(target))
                    logf.write(f"{i}\t{id_for_log}\tQUARANTINED\t{p} -> {target}\n")
                    counters["quarantined"] += 1
                except Exception as e:
                    logf.write(f"{i}\t{id_for_log}\tQUARANTINE_MOVE_FAILED\t{p} -> {target} : {e}\n")
                    counters["move_failed"] += 1
                continue
            elif should_quarantine:
                logf.write(f"{i}\t{id_for_log}\tDRYRUN_QUARANTINE\t{p}\n")
                continue

            # build target path + filename
            orig_name = p.name
            id = obj.get("id") or pf_key or ""
            target_filename = build_target_filename(orig_name, tri, id, args.no_rename)
            role_folder, final_filename = build_target_path_for_role(OUT_DIR, role, tri, target_filename)
            role_folder.mkdir(parents=True, exist_ok=True)

            # collision avoidance
            target = role_folder / final_filename
            suffix = 0
            while target.exists():
                suffix += 1
                stem = Path(final_filename).stem
                ext = Path(final_filename).suffix
                candidate_name = f"{stem}_{suffix}{ext}"
                # ensure length
                if len(candidate_name) > MAX_FILENAME_LEN:
                    stem = stem[:MAX_FILENAME_LEN - len(ext) - 5]
                    candidate_name = f"{stem}_{suffix}{ext}"
                target = role_folder / candidate_name

            if dry:
                logf.write(f"{i}\t{id_for_log}\tDRYRUN\t{p} -> {target}\n")
            else:
                try:
                    shutil.move(str(p), str(target))
                    logf.write(f"{i}\t{id_for_log}\tMOVED\t{p} -> {target}\n")
                    counters["moved"] += 1
                except Exception as e:
                    logf.write(f"{i}\t{id_for_log}\tMOVE_FAILED\t{p} -> {target} : {e}\n")
                    counters["move_failed"] += 1

    with open(LOG, "a", encoding="utf8") as logf:
        logf.write("\n# SUMMARY\n")
        for k,v in counters.items():
            logf.write(f"# {k}: {v}\n")
    print("Log written to:", LOG)
    print("Summary:", counters)

    # wipe inbox if requested
    if args.apply and args.wipe_inbox:
        if dry:
            print("NOTE: --wipe-inbox requested but run was dry-run. Re-run with --apply to actually wipe.")
            return 0
        remaining = list(BASE_INPUT_DIR.rglob("*"))
        remaining_files = [p for p in remaining if p.is_file()]
        if remaining_files:
            print(f"WARNING: {len(remaining_files)} files still in inbox ({BASE_INPUT_DIR}).")
            for p in remaining_files:
                try:
                    p.unlink()
                except Exception as e:
                    print("Failed to unlink", p, e)
            print("Inbox wiped: removed remaining files under", BASE_INPUT_DIR)
        else:
            print("Inbox empty: nothing to wipe.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
