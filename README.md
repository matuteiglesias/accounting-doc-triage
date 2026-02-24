# accounting_doc_triage

Small, relocatable engine for triaging accounting PDFs (payments and statements) using PromptFlow, then moving files and generating digest outputs.

This repo contains only the engine:
- flows (PromptFlow graphs, prompts, schemas)
- scripts (index, run cycle, mover, digests)
- docs/runbook.md

It does NOT contain:
- raw PDFs
- moved/quarantine stores
- backups
- PromptFlow run artifacts

Those live in an external "data root" directory (typically your Accounting folder).

## Quick start

1) Create a config file:
   cp config/paths.example.yaml config/paths.yaml

2) Edit config/paths.yaml to point to your Accounting data root.

3) Run a dry cycle:
   bash scripts/run_triage_cycle_general.sh --flow flows/payment --apply=0 --backup=0

See docs/runbook.md for details.
