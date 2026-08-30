# Accounting evidence intake runbook

## Boundary

The current runtime captures private accounting evidence, converts it into rebuildable structured derivatives, and later produces reviewable accounting observations/transaction-evidence relations. It does not create canonical accounting facts.

Historical PromptFlow flow directories are no longer part of current `main`; old PromptFlow scripts are lineage only.

## Safety defaults

- Real documents live outside the repository.
- Ordinary tests use synthetic bytes only.
- Capture is a dry-run unless `APPLY=1` is explicitly supplied to the Make target.
- Evidence identity is SHA-256 of exact bytes; filenames and classifications are metadata.
- PDF, PNG and JPG/JPEG are accepted.
- Original captured evidence is immutable. Parser output is a rebuildable derivative.
- Docling conversion is local/in-process. The current adapter does not opt into remote services or third-party plugins.

## Suggested external layout

```text
<DATA_ROOT>/
  .accounting-doc-triage/
    inflight/
  evidence/
    original/
      <sha-prefix>/<sha>.pdf|png|jpg
      <sha-prefix>/<sha>.json
    derived/
      docling/<conversion-id>/<sha-prefix>/<sha>.json
      docling/<conversion-id>/<sha-prefix>/<sha>.meta.json
```

## Dry-run one capture

```bash
make capture \
  DATA_ROOT=/private/accounting \
  SOURCE=/private/inbox/proof.pdf
```

This computes the planned content identity/destination but does not move the source.

## Apply one capture

```bash
make capture \
  DATA_ROOT=/private/accounting \
  SOURCE=/private/inbox/proof.pdf \
  APPLY=1
```

The source is first atomically claimed into `inflight`; the canonical object is published only after its SHA-256 is verified. An identical file captured under another name resolves to the same evidence object.

## Recover interrupted capture

```bash
make recover DATA_ROOT=/private/accounting APPLY=1
```

Any regular file left in `inflight` is resumed. Do not manually delete inflight files merely because a previous command was interrupted.

## Docling conversion

Install the optional local parser runtime:

```bash
pip install -e '.[docling]'
```

Then convert a captured PDF/image:

```bash
make convert \
  DATA_ROOT=/private/accounting \
  SOURCE=/private/accounting/evidence/original/ab/<sha>.pdf
```

The adapter writes lossless `DoclingDocument` JSON plus parser/config metadata under a representation-specific conversion ID. Re-running with the same source, Docling version and adapter config reuses the derivative.

## Confidence and review

Parser confidence answers only whether document conversion appears reliable. It must remain distinct from later transaction-match confidence. A well-parsed document can still match more than one ledger transaction.

## Legacy adapters

Existing domain parsers such as the Tigre adapter are historical sources of accounting-specific extraction rules. Their generic PDF extraction stacks should not be extended; useful domain semantics will be rebuilt above Docling during the E4 migration.

## Failure model

- unsupported input type: reject before mutation;
- claim failure: source remains outside custody;
- capture failure after claim: file remains in `inflight` for explicit recovery;
- duplicate bytes: verify canonical hash, remove duplicate claim, reuse evidence identity;
- Docling failure: original evidence remains safely captured; parser failure becomes derivative/review state rather than deleting or relocating the original.
