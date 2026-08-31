# Accounting document intake capability ledger

Decision date: 2026-08-30

This ledger resets the repository around one bounded authority: turn an unclassified accounting document into safely captured evidence plus reviewable accounting-document observations and proposed evidence relations. Canonical accounting truth remains downstream in `accounting-workflows`.

## Current authority

`accounting-doc-triage` owns:

- accounting-document inbox/capture semantics;
- content identity and immutable evidence custody;
- parsing derivatives and parser provenance;
- accounting-document family classification;
- accounting-specific deterministic extraction;
- proposed transaction matching and review frontier;
- production of approved transaction-evidence relation artifacts.

It does not own:

- canonical transaction identity or ledger semantics;
- transaction creation merely because a document exists;
- cash/debt/metric calculations;
- professional report calculations;
- generic estate-wide document ingestion;
- repository health;
- publication/access policy for private evidence.

## Legacy adjudication

| Legacy capacity | Disposition | Reason |
| --- | --- | --- |
| accounting PDF indexing | **MIGRATE / EVOLVE** | Preserve content identity and source provenance; move into governed custody. |
| payment/statement document roles | **KEEP / EVOLVE** | Real accounting semantics still needed. |
| Tigre-specific metadata/liability parsing | **KEEP SEMANTIC KNOWLEDGE** | Domain rules are useful; rebuild above structured parsing. |
| pypdf/pdfplumber/pdfminer fallback stack | **REPLACE WITH DOCLING** | Generic text/OCR/layout extraction is not a local authority. |
| PromptFlow payment/statement/general graphs | **RETIRE IMPLEMENTATION** | Current `main` no longer contains the advertised flow directories; do not restore runtime ghosts. |
| PromptFlow run artifacts/tracing | **RETIRE IMPLEMENTATION** | Run evidence will be repository-owned, framework-neutral. |
| classification-dependent filenames/folders | **RETIRE AS IDENTITY** | Friendly organization may remain a projection; evidence identity is content-based. |
| movement/quarantine safeguards | **MIGRATE / EVOLVE** | Preserve dry-run, recoverability and fail-safe custody. |
| digest generation | **LATENT** | Reintroduce only if a current human consumer requires it. |
| generic document triage platform | **RETIRE / NOT AN AUTHORITY** | No demonstrated multi-domain consumer. |
| agentic workflow runtime | **LATENT** | Microsoft Agent Framework may be reconsidered only for a real model-assisted/HITL workflow; deterministic E1–E6 does not require it. |

## External dependency boundary

Docling is adopted only as a replaceable parsing substrate:

```text
source PDF/PNG/JPEG
      ↓
content-addressed evidence
      ↓
DoclingDocument JSON (rebuildable derivative)
      ↓
accounting-owned interpretation
```

Docling does not decide whether a document is accounting truth, whether a payment occurred, or which canonical transaction it supports.

## Document-kind versus relation semantics

These are deliberately separate.

Example document kinds:

- `tax_liability`
- `utility_liability`
- `tax_payment_proof`
- `utility_payment_proof`
- `transfer_proof`
- `bank_or_card_statement`
- `other`

Example transaction relations:

- `payment_proof`
- `transfer_proof`
- `statement_context`
- `liability_source`
- `other_support`

A statement can support many transactions; a transaction can have several supporting documents. A bill/liquidation is not automatically proof that it was paid.
