# Agent guidance

## Repository purpose

This repository is an accounting-document intake and triage adapter. It classifies candidate PDFs, moves or quarantines files when explicitly authorized, and produces metadata and digest artifacts for the canonical accounting pipeline.

It does not own ledger semantics, accounting calculations, professional reports, or viewer presentation.

Read `README.md`, `docs/runbook.md`, `SYSTEM.yaml`, and the root `Makefile` before changing flows, scripts, paths, or command behavior.

## Authority and privacy

Matías retains authority over:

- which external data root may be accessed;
- which inbox or moved-document area is in scope;
- whether file movement, backup, quarantine, or deletion-like cleanup is authorized;
- classification meaning and accounting interpretation;
- publication or downstream promotion of triage results.

Treat all source documents, extracted text, metadata, filenames, paths, and digests as potentially private. Never copy real documents or credentials into tests, issues, logs, prompts, fixtures, or the repository.

## External data root

The repository code is relocatable; operational inputs and outputs live under an external `DATA_ROOT`.

Do not assume the Makefile default points to an approved environment. Every operational packet must state the exact resolved data root and inbox.

Before touching external files, report:

- `DATA_ROOT`;
- `INBOX_REL`;
- selected `FLOW`;
- `APPLY`, `BACKUP`, and `WIPE_INBOX` values;
- expected read and write paths;
- rollback or recovery plan.

Never broaden a root, follow an unexpected symlink, or traverse outside the approved tree.

## Command safety classes

### Repository-local checks

```bash
make test
```

This still assumes the declared virtual environment exists. Do not install or mutate environments silently during a check.

### Dry operational inspection

Triage commands with all mutation flags disabled may read real private documents and create run evidence under the external root. They are not ordinary unit tests.

Use explicit values such as:

```bash
make triage-payment DATA_ROOT=/approved/path APPLY=0 BACKUP=0 WIPE_INBOX=0
```

### Consequential operations

Any command using one or more of these is consequential:

- `APPLY=1`;
- `BACKUP=1`;
- `WIPE_INBOX=1`;
- direct mover, concat, digest, or index commands against a real data root;
- PromptFlow execution that sends document content to an external model or service.

Run consequential operations only under an explicit execution packet approved by Matías. Discovery of a command or configured path is not authorization to execute it.

## Source and generated artifacts

The repository must not contain raw PDFs, moved/quarantine stores, backups, PromptFlow run artifacts, credentials, or private external-root configuration.

Generated outputs must retain enough provenance to identify:

- source document identity without unnecessary disclosure;
- selected flow and prompt/schema version;
- run timestamp or run ID;
- dry-run versus applied mode;
- backup and movement decisions;
- first failure or partial-result evidence.

Do not hand-edit triage outputs to make downstream ingestion succeed. Correct the flow or adapter and rerun on an approved fixture or source packet.

## Classification and accounting boundary

A triage result is a candidate interpretation, not a canonical accounting fact.

Do not:

- create or modify ledger entries here;
- infer payment, debt, ownership, or reconciliation semantics beyond the approved schema;
- silently promote a classification into the accounting pipeline;
- duplicate calculations owned by `accounting-workflows`;
- treat a successful model response as verified accounting evidence.

Contract changes that affect downstream ingestion require an explicit compatibility review with `accounting-workflows`.

## Testing and fixtures

Prefer sanitized or synthetic PDFs and metadata fixtures. Fixtures must not preserve real names, account numbers, addresses, transaction identifiers, or embedded document metadata.

Tests should cover changed parser, schema, movement, deduplication, and failure behavior without requiring a live external data root or model service where possible.

Do not weaken safety checks merely to make an automated run pass.

## Change rules

Keep changes narrow. Avoid broad dependency, PromptFlow, path-layout, or shell-script modernization unless required by an approved defect.

For changes affecting mutation behavior:

- preserve dry-run as the default;
- require explicit flags for writes;
- make repeated execution safe or document non-idempotent behavior;
- preserve evidence before moving or replacing files;
- report partial execution clearly.

## Stop conditions

Stop and report when:

- an approved root or inbox is ambiguous;
- real private data would be required for a test;
- a symlink or resolved path escapes the approved root;
- PromptFlow credentials or external-service behavior are unclear;
- classification semantics conflict with downstream contracts;
- a move, backup, or wipe cannot be recovered;
- an operation has partially mutated external state.

## Completion evidence

Report:

- repository files changed;
- commands actually executed;
- exact external roots accessed, if any;
- whether private content was read or transmitted;
- dry-run and mutation flags;
- files created, moved, backed up, quarantined, or removed;
- tests and fixtures used;
- unresolved failures or downstream compatibility questions.

Never summarize a dry run as a completed applied triage cycle.
