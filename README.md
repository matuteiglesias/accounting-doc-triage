# accounting-doc-triage

Bounded accounting-document intake and evidence preparation.

This repository turns unclassified accounting documents into safely captured evidence, structured parsing derivatives, accounting-specific observations, and reviewable transaction-evidence candidates. Canonical ledger/accounting truth remains in [`accounting-workflows`](https://github.com/matuteiglesias/accounting-workflows).

## Current boundary

```text
accounting PDF / PNG / JPEG
        ↓
safe content-addressed custody
        ↓
Docling structured conversion
        ↓
accounting-specific interpretation
        ↓
reviewable transaction evidence
        ↓
accounting-workflows
```

The repository contains only code, synthetic tests, configuration examples, and run evidence. Real accounting documents, evidence stores, parser derivatives, quarantine state, and private review artifacts live under an external data root.

## Important lifecycle note

The historical PromptFlow payment/statement/general flow directories advertised by older docs are no longer present on current `main`. They are not restored by the current migration. Useful accounting semantics are being retained while PromptFlow-specific orchestration and duplicated low-level PDF parsing are retired.

See [`docs/CAPABILITY_LEDGER.md`](docs/CAPABILITY_LEDGER.md) for the explicit legacy disposition.

## Development

```bash
python -m pytest
```

Install the optional Docling parsing substrate when exercising real conversion:

```bash
pip install -e '.[docling]'
```

Real-file movement or conversion is never required for ordinary tests.
