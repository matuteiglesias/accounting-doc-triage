# accounting_doc_triage runbook

This repo is an engine for triaging accounting PDFs (statements, payment receipts, other bills) using PromptFlow, moving files into structured folders, and generating digests.

The engine lives in this repo.
The accounting data (PDFs, moved store, backups, run artifacts) lives in a separate "data root" folder, typically:

  ~/RAG_Sync/Accounting

Goal: you should run everything from the repo root. No manual cd to the data folder.

---

## Concepts and invariants

### Engine root vs Data root
- Engine root: this repo folder (example: ~/repos/accounting_doc_triage)
- Data root: the Accounting data tree folder (example: ~/RAG_Sync/Accounting)

Data root contains:
- 1_Input_Raw/00_inbox
- moved/
- 4_Analysis_Workflows/
- backups/
- artifacts/

This repo contains:
- flows/ (PromptFlow flows and prompts)
- scripts/ (indexer, mover, digests, orchestration)
- docs/ (this runbook)
- config/ (examples for future configuration)

### Safety defaults
- Moves are dry-run unless --apply is provided.
- Backups should happen only when you apply.
- Never write outputs into the engine repo. Outputs go into data root.

### PromptFlow runs
PromptFlow writes its raw run state under:
  ~/.promptflow/.runs/...

The driver script archives the important bits (input/output/logs tail) under:
  <data_root>/4_Analysis_Workflows/pf_runs/<flow_name>/run_<timestamp>/

---

## Capabilities overview

### Flows (PromptFlow)
Located under:
- flows/general
- flows/payment
- flows/statement

Each flow includes:
- triage prompt template (triage_prompt.jinja2)
- triage schema (triage_schema.json)
- flow DAG (flow.dag.yaml)
- run config (run.yml)
- wrapper code (llm_wrapper.py)
- init.json (PF init)

### Scripts (atomic tools)
- scripts/triage_indexer.py
  Indexes an inbox directory and produces JSONL input records.

- scripts/run_triage_cycle_general.sh
  Orchestrates a full run: index inbox, run PromptFlow, archive outputs, run mover.

- scripts/run_triage_cycle.sh
  Older runner kept for reference. Prefer run_triage_cycle_general.sh.

- scripts/move_triage_files.py
  Consumes PromptFlow output JSONL plus original input JSONL, then moves/quarantines PDFs.

- scripts/concat_dedupe_jsonl.py
  Combines multiple output JSONLs (glob patterns) and deduplicates them.

- scripts/digest_payments_statements.py
  Builds digest CSVs and markdown/text summaries from a combined triage output JSONL.

- scripts/adapters/tigre_parser.py
  Special case parser, treated as an adapter.

---

## Required prerequisites

1) Python environment
This repo is a thin Python package. Create a venv and install dependencies:
  make venv

2) PromptFlow available
The driver uses:
  python -m promptflow._cli.pf run create ...

Ensure PromptFlow is installed in the same environment used to run the script, or accessible on PATH.

3) Credentials
PromptFlow needs your LLM credentials set, either through:
- environment variables, or
- PromptFlow connection files, or
- init.json as you already use

Never commit secrets:
- flows/**/.promptflow/connection.* should be gitignored.

---

## Data root configuration

You can run without any config file by passing a data root explicitly.

Typical:
  DATA_ROOT=~/RAG_Sync/Accounting

The Makefile defaults DATA_ROOT to ~/RAG_Sync/Accounting but you can override on the command line.

---

## Atomic tasks (run from repo root)

### 1) Index inbox only
Indexes inbox PDFs into a JSONL file.
Output goes to:
  <data_root>/4_Analysis_Workflows/triage_runs/<flow_name>/run_<ts>/input.jsonl
and updates:
  <data_root>/4_Analysis_Workflows/triage_input_latest.jsonl

Command pattern:
  python3 scripts/triage_indexer.py --input-dir <inbox> --out <out_jsonl>

Make targets:
  make index-flow FLOW=payment
  make index-flow FLOW=statement
  make index-flow FLOW=general

### 2) Run PromptFlow only (advanced)
Normally you do not run PF directly; use the driver.
If you do:
  cd flows/payment
  python -m promptflow._cli.pf run create --flow . --data <input.jsonl>

Note: the driver will archive output into data root.

### 3) Move files only (from existing PF output)
If you already have:
  <data_root>/4_Analysis_Workflows/pf_runs/<flow_name>/run_<ts>/output.jsonl
and the corresponding input.jsonl

Then:
  python3 scripts/move_triage_files.py --pf <output.jsonl> --orig <input.jsonl>
Add --apply to actually move.

Make target:
  make move-run FLOW=payment RUN_TS=<timestamp> APPLY=0|1

### 4) Concat + dedupe outputs
Combine output JSONLs across runs:
  python3 scripts/concat_dedupe_jsonl.py \
    "4_Analysis_Workflows/pf_runs/flow_payment/run_*/output.jsonl" \
    "4_Analysis_Workflows/pf_runs/flow_statement/run_*/output.jsonl" \
    -o artifacts/jsonl/combined_dedup.jsonl

Make target:
  make concat

### 5) Build digests
From a combined triage output JSONL:
  python3 scripts/digest_payments_statements.py \
    --input <data_root>/4_Analysis_Workflows/triage_output_all_combined.jsonl \
    --out-dir <data_root>/artifacts/digest

Make target:
  make digest

---

## Compounded tasks (standard workflows)

### Workflow A: Full triage cycle (recommended)
This does:
1) index inbox
2) run PromptFlow
3) archive output
4) mover dry-run or apply moves

Targets:
  make triage-payment
  make triage-statement
  make triage-general

Override inbox:
  make triage-payment INBOX_REL="moved/payment" APPLY=0 BACKUP=0

### Workflow B: Full cycle + apply moves + backup
  make triage-payment APPLY=1 BACKUP=1

Optional (dangerous):
  make triage-payment APPLY=1 BACKUP=1 WIPE_INBOX=1

### Workflow C: Produce consolidated output and digest
1) run your triage cycles as needed
2) concat + dedupe
3) digest

Targets:
  make concat
  make digest

---

## Operational checks

### Check that outputs land in data root
After running any make target, verify:
- <data_root>/4_Analysis_Workflows/triage_runs/...
- <data_root>/4_Analysis_Workflows/pf_runs/...
- <data_root>/moved/move_report_*.log
- <data_root>/backups/inbox_backup_*.tar.gz (only with APPLY=1 BACKUP=1)

If you ever see new moved/ or backups/ directories inside the engine repo, treat it as a bug and fix the run invocation.

---

## Common failure modes

1) PromptFlow exits non-zero
The driver continues to archive what it can.
Inspect:
  ~/.promptflow/.runs/<latest>/logs.txt

2) PF output missing
The driver will abort mover step.
This usually means the flow failed before writing flow_outputs/output.jsonl.

3) Zero indexed records
The driver aborts PF submit.
Check:
- inbox path
- file extensions in inbox
- indexer filters

4) Moving unexpectedly resolves paths incorrectly
Your mover resolves paths using metadata and fallbacks.
If resolution logic changes, keep a small fixture inbox and do dry-run comparisons.

