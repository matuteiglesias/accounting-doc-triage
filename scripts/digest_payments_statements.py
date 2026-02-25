#!/usr/bin/env python3
"""
digest_payments_statements_md.py

Genera:
 - artifacts/digest/payments_recent.csv
 - artifacts/digest/statements_due.csv
 - artifacts/digest/digest.md    <-- digest en Markdown "bonito"
 - artifacts/digest/digest.txt   <-- resumen en texto plano

Uso:
 python scripts/digest_payments_statements_md.py \
   --input 4_Analysis_Workflows/triage_output_all_combined.jsonl \
   --out-dir artifacts/digest \
   --payments-days 30 \
   --statements-days 15
"""
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import argparse
import pandas as pd
import math

TZ = "America/Argentina/Buenos_Aires"

def safe_get(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        if k not in d:
            return default
        d = d[k]
    return d

def cents_to_float(c):
    try:
        if c is None:
            return None
        return float(c) / 100.0
    except Exception:
        return None

def fmt_amt_ar(x):
    """Formato argentino: miles con punto y decimales con coma. Ej: 1.234.567,89
       Si x es NaN o None devuelve '-'"""
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x))):
            return "-"
        s = f"{float(x):,.2f}"  # ej '1,234,567.89'
        # swap thousand/comma: ',' -> 'X', '.' -> ',', 'X' -> '.'
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        return s
    except Exception:
        return str(x)

def load_jsonl_records(p: Path):
    recs = []
    with p.open("r", encoding="utf-8") as fh:
        for ln, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                print(f"Advertencia: JSON inválido en {p}:{ln} -> se saltea")
                continue
            tri = safe_get(obj, "triage_result", default=None)
            recs.append({"raw": obj, "triage": tri})
    return recs

def build_dfs(records):
    pagos = []
    estados = []
    for r in records:
        tri = r.get("triage") or {}
        role = tri.get("doc_role")
        if role == "payment":
            payment_date = tri.get("payment_date") or (tri.get("normalized_dates") or [None])[0]
            amt = cents_to_float(tri.get("amount_cents"))
            pagos.append({
                "id": tri.get("id") or r["raw"].get("id"),
                "payment_date": payment_date,
                "payment_date_parsed": pd.to_datetime(payment_date, errors="coerce").date() if payment_date else pd.NaT,
                "amount": amt,
                "Currency": tri.get("Currency"),
                "payment_reference": tri.get("payment_reference") or tri.get("transaction_id"),
                "transaction_id": tri.get("transaction_id"),
                "payment_platform": tri.get("payment_platform"),
                "payment_method": tri.get("payment_method"),
                "card_last4": tri.get("card_last4"),
                "issuer_slug": tri.get("issuer_slug"),
                "filename_hint": tri.get("filename_hint"),
                "suggested_action": tri.get("suggested_action"),
                "needs_manual_review": bool(tri.get("needs_manual_review", False)),
                "summary": tri.get("summary"),
                "raw": r["raw"]
            })
        elif role == "statement":
            due = tri.get("due_date")
            amt = cents_to_float(tri.get("total_amount_cents"))
            estados.append({
                "id": tri.get("id") or r["raw"].get("id"),
                "issuer_slug": tri.get("issuer_slug"),
                "unit_slug": tri.get("unit_slug"),
                "statement_period": tri.get("statement_period"),
                "period_start": tri.get("period_start"),
                "period_end": tri.get("period_end"),
                "due_date": due,
                "due_date_parsed": pd.to_datetime(due, errors="coerce").date() if due else pd.NaT,
                "total_amount": amt,
                "Currency": tri.get("Currency"),
                "invoice_number": tri.get("invoice_number"),
                "statement_id": tri.get("statement_id"),
                "is_bundle": bool(tri.get("is_bundle", False)),
                "page_count": tri.get("page_count"),
                "filename_hint": tri.get("filename_hint"),
                "suggested_action": tri.get("suggested_action"),
                "needs_manual_review": bool(tri.get("needs_manual_review", False)),
                "summary": tri.get("summary"),
                "raw": r["raw"]
            })
        else:
            # roles ignorados: none/other
            continue
    df_pay = pd.DataFrame(pagos)
    df_stat = pd.DataFrame(estados)
    return df_pay, df_stat

def generar_md_digest(df_pay, df_stat, payments_days, statements_days, out_dir: Path):
    tz = ZoneInfo(TZ)
    now = datetime.now(tz)
    today = now.date()
    pay_since = today - timedelta(days=payments_days)
    stat_until = today + timedelta(days=statements_days)

    # Filtrado
    if df_pay.empty:
        recent_pay = df_pay
    else:
        recent_pay = df_pay[
            (df_pay["payment_date_parsed"].notna()) &
            (df_pay["payment_date_parsed"] >= pay_since) &
            (df_pay["payment_date_parsed"] <= today)
        ].copy()

    if df_stat.empty:
        due_stat = df_stat
    else:
        due_stat = df_stat[
            (df_stat["due_date_parsed"].notna()) &
            (df_stat["due_date_parsed"] > today) &
            (df_stat["due_date_parsed"] <= stat_until)
        ].copy()

    # Agregados
    total_payments = recent_pay["amount"].sum() if not recent_pay.empty else 0.0
    count_payments = len(recent_pay)
    avg_payment = recent_pay["amount"].mean() if count_payments else 0.0
    manual_pay_count = recent_pay["needs_manual_review"].sum() if not recent_pay.empty else 0

    total_statements = due_stat["total_amount"].sum() if not due_stat.empty else 0.0
    count_statements = len(due_stat)
    manual_stat_count = due_stat["needs_manual_review"].sum() if not due_stat.empty else 0

    # Totales por emisor (issuer_slug)
    issuer_pay_agg = (recent_pay.groupby("issuer_slug", dropna=False)
                                .agg(count=("id","size"), total_amount=("amount","sum"))
                                .sort_values("total_amount", ascending=False)
                                .reset_index()
                   ) if not recent_pay.empty else pd.DataFrame(columns=["issuer_slug","count","total_amount"])

    issuer_stat_agg = (due_stat.groupby("issuer_slug", dropna=False)
                                .agg(count=("id","size"), total_amount=("total_amount","sum"))
                                .sort_values("total_amount", ascending=False)
                                .reset_index()
                   ) if not due_stat.empty else pd.DataFrame(columns=["issuer_slug","count","total_amount"])

    # ordenados y top lists
    recent_pay_sorted = recent_pay.sort_values("payment_date_parsed", ascending=False) if not recent_pay.empty else recent_pay
    recent_pay_top = recent_pay_sorted.sort_values("amount", ascending=False).head(10) if not recent_pay_sorted.empty else recent_pay_sorted
    due_stat_sorted = due_stat.sort_values("due_date_parsed", ascending=True) if not due_stat.empty else due_stat

    # Preparar salida
    out_dir.mkdir(parents=True, exist_ok=True)
    payments_csv = out_dir / "payments_recent.csv"
    statements_csv = out_dir / "statements_due.csv"
    digest_md = out_dir / "digest.md"
    digest_txt = out_dir / "digest.txt"

    # Export CSVs completos para análisis
    if not df_pay.empty:
        df_pay.to_csv(out_dir / "payments_all_raw.csv", index=False)
    if not df_stat.empty:
        df_stat.to_csv(out_dir / "statements_all_raw.csv", index=False)
    recent_pay_sorted.to_csv(payments_csv, index=False)
    due_stat_sorted.to_csv(statements_csv, index=False)

    # Construir Markdown
    md_lines = []
    md_lines.append(f"# Digest financiero — generado {now.isoformat()}")
    md_lines.append("")
    md_lines.append(f"**Zona horaria:** `{TZ}`  ")
    md_lines.append(f"**Período de pagos analizado:** últimos {payments_days} días — `{pay_since.isoformat()}` a `{today.isoformat()}`  ")
    md_lines.append(f"**Período de vencimientos:** próximos {statements_days} días — `{today.isoformat()}` a `{stat_until.isoformat()}`  ")
    md_lines.append("")
    md_lines.append("## Resumen rápido")
    md_lines.append("")
    md_lines.append("| Métrica | Valor |")
    md_lines.append("|---:|---:|")
    md_lines.append(f"| Cantidad de pagos (últ. {payments_days}d) | {count_payments} |")
    md_lines.append(f"| Total pagos (ARS) | {fmt_amt_ar(total_payments)} |")
    md_lines.append(f"| Pagos que requieren revisión manual | {int(manual_pay_count)} |")
    md_lines.append("| | |")
    md_lines.append(f"| Cantidad de estados próximos a vencer (≤ {statements_days}d) | {count_statements} |")
    md_lines.append(f"| Total vencimientos (ARS) | {fmt_amt_ar(total_statements)} |")
    md_lines.append(f"| Estados que requieren revisión manual | {int(manual_stat_count)} |")
    md_lines.append("")
    md_lines.append(f"_CSV completos:_ `{payments_csv.name}`, `{statements_csv.name}` (en `{out_dir}`)")
    md_lines.append("")

    # Pagos - tabla con top 10 por monto
    md_lines.append("## Pagos — últimos {0} días (top 10 por monto)".format(payments_days))
    md_lines.append("")
    if recent_pay_top.empty:
        md_lines.append("_No se encontraron pagos en el período indicado._")
    else:
        md_lines.append("| Fecha | Referencia | Emisor | Monto (ARS) | Método | Archivo |")
        md_lines.append("|---|---|---|---:|---|---|")
        for _, r in recent_pay_top.iterrows():
            fecha = r.get("payment_date_parsed")
            fecha_s = fecha.isoformat() if (not pd.isna(fecha)) else "-"
            ref = r.get("payment_reference") or r.get("transaction_id") or "-"
            issuer = r.get("issuer_slug") or "-"
            monto = fmt_amt_ar(r.get("amount"))
            metodo = r.get("payment_method") or r.get("payment_platform") or "-"
            archivo = (r.get("filename_hint") or "-")
            md_lines.append(f"| {fecha_s} | {ref} | {issuer} | {monto} | {metodo} | {archivo} |")
    md_lines.append("")

    # Totales por emisor (pagos)
    md_lines.append("### Contribución por emisor — pagos")
    md_lines.append("")
    if issuer_pay_agg.empty:
        md_lines.append("_Sin datos de pagos por emisor._")
    else:
        md_lines.append("| Emisor | Cantidad | Total (ARS) |")
        md_lines.append("|---|---:|---:|")
        for _, r in issuer_pay_agg.iterrows():
            em = r["issuer_slug"] or "(sin_emisor)"
            cnt = int(r["count"])
            tot = fmt_amt_ar(r["total_amount"])
            md_lines.append(f"| {em} | {cnt} | {tot} |")
    md_lines.append("")

    # Statements due
    md_lines.append(f"## Estados próximos a vencer — próximos {statements_days} días")
    md_lines.append("")
    if due_stat_sorted.empty:
        md_lines.append("_No hay estados próximos a vencer en el rango indicado._")
    else:
        md_lines.append("| Vencimiento | Emisor | Factura | Monto (ARS) | Archivo |")
        md_lines.append("|---|---|---|---:|---|")
        for _, r in due_stat_sorted.iterrows():
            vd = r.get("due_date_parsed")
            vd_s = vd.isoformat() if (not pd.isna(vd)) else "-"
            em = r.get("issuer_slug") or "-"
            inv = r.get("invoice_number") or "-"
            monto = fmt_amt_ar(r.get("total_amount"))
            archivo = (r.get("filename_hint") or "-")
            md_lines.append(f"| {vd_s} | {em} | {inv} | {monto} | {archivo} |")
    md_lines.append("")

    # Totales por emisor (statements)
    md_lines.append("### Contribución por emisor — próximos vencimientos")
    md_lines.append("")
    if issuer_stat_agg.empty:
        md_lines.append("_Sin datos de vencimientos por emisor._")
    else:
        md_lines.append("| Emisor | Cantidad | Total (ARS) |")
        md_lines.append("|---|---:|---:|")
        for _, r in issuer_stat_agg.iterrows():
            em = r["issuer_slug"] or "(sin_emisor)"
            cnt = int(r["count"])
            tot = fmt_amt_ar(r["total_amount"])
            md_lines.append(f"| {em} | {cnt} | {tot} |")
    md_lines.append("")

    # Items para revisión manual (si existen)
    md_lines.append("## Items marcados para revisión manual")
    md_lines.append("")
    any_manual = False
    if not recent_pay.empty and recent_pay["needs_manual_review"].any():
        any_manual = True
        md_lines.append("### Pagos (requieren revisión)")
        md_lines.append("| Fecha | Ref | Emisor | Monto | Archivo |")
        md_lines.append("|---|---|---|---:|---|")
        for _, r in recent_pay[recent_pay["needs_manual_review"]].iterrows():
            fecha = r.get("payment_date_parsed")
            fecha_s = fecha.isoformat() if (not pd.isna(fecha)) else "-"
            md_lines.append(f"| {fecha_s} | {r.get('payment_reference') or '-'} | {r.get('issuer_slug') or '-'} | {fmt_amt_ar(r.get('amount'))} | {r.get('filename_hint') or '-'} |")
        md_lines.append("")
    if not due_stat.empty and due_stat["needs_manual_review"].any():
        any_manual = True
        md_lines.append("### Estados (requieren revisión)")
        md_lines.append("| Vencimiento | Emisor | Factura | Monto | Archivo |")
        md_lines.append("|---|---|---|---:|---|")
        for _, r in due_stat[due_stat["needs_manual_review"]].iterrows():
            vd = r.get("due_date_parsed")
            vd_s = vd.isoformat() if (not pd.isna(vd)) else "-"
            md_lines.append(f"| {vd_s} | {r.get('issuer_slug') or '-'} | {r.get('invoice_number') or '-'} | {fmt_amt_ar(r.get('total_amount'))} | {r.get('filename_hint') or '-'} |")
        md_lines.append("")
    if not any_manual:
        md_lines.append("_No hay items marcados para revisión manual en estos rangos._")
        md_lines.append("")

    # Pie / metadata
    md_lines.append("---")
    md_lines.append(f"*Digest generado automáticamente — {now.isoformat()} (zona {TZ}).*")
    md_text = "\n".join(md_lines)

    # Guardar MD y TXT
    with digest_md.open("w", encoding="utf-8") as fh:
        fh.write(md_text)
    # también texto plano breve
    txt_lines = [
        f"DIGEST: {now.isoformat()}",
        f"Pagos (últ. {payments_days}d): count={count_payments} total_ARS={fmt_amt_ar(total_payments)}",
        f"Vencimientos (próx. {statements_days}d): count={count_statements} total_ARS={fmt_amt_ar(total_statements)}",
    ]
    with digest_txt.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(txt_lines))

    # imprimir resumen breve en stdout
    print("\n".join(txt_lines))
    print(f"Escrito: {payments_csv}")
    print(f"Escrito: {statements_csv}")
    print(f"Escrito: {digest_md}")
    return {
        "now": now,
        "payments_csv": payments_csv,
        "statements_csv": statements_csv,
        "digest_md": digest_md,
        "counts": {"payments_recent": count_payments, "statements_due": count_statements}
    }

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, type=Path, help="archivo JSONL combinado (output combinado)")
    p.add_argument("--out-dir", type=Path, default=Path("artifacts/digest"), help="directorio de salida")
    p.add_argument("--payments-days", type=int, default=30, help="ventana atrás para pagos (días)")
    p.add_argument("--statements-days", type=int, default=15, help="ventana adelante para estados (días)")
    args = p.parse_args(argv)

    recs = load_jsonl_records(args.input)
    df_pay, df_stat = build_dfs(recs)
    res = generar_md_digest(df_pay, df_stat, args.payments_days, args.statements_days, args.out_dir)
    return res

if __name__ == "__main__":
    main()
