SHELL := /usr/bin/env bash
.ONESHELL:
.DEFAULT_GOAL := help

# External data tree root (override per invocation)
DATA_ROOT ?= $(HOME)/RAG_Sync/Accounting

# Which flow to use: general | payment | statement
FLOW ?= payment

# Inbox path relative to DATA_ROOT (override when needed)
INBOX_REL ?= 1_Input_Raw/00_inbox

# Safety flags
APPLY ?= 0
BACKUP ?= 0
WIPE_INBOX ?= 0

# Extra PF args, quoted as needed, example:
# PF_ARGS='--environment default'
PF_ARGS ?=

help:
	@echo "accounting_doc_triage"
	@echo ""
	@echo "Core:"
	@echo "  make venv                 Create .venv and install package editable + pytest"
	@echo "  make test                 Run tests"
	@echo ""
	@echo "Triage cycles (run from repo root, write to DATA_ROOT):"
	@echo "  make triage-payment       Full cycle using flows/payment"
	@echo "  make triage-statement     Full cycle using flows/statement"
	@echo "  make triage-general       Full cycle using flows/general"
	@echo ""
	@echo "Atomic tools:"
	@echo "  make index-flow FLOW=payment|statement|general"
	@echo "  make concat               Concat + dedupe PF outputs into DATA_ROOT/artifacts/jsonl"
	@echo "  make digest               Build digest outputs into DATA_ROOT/artifacts/digest"
	@echo ""
	@echo "Variables:"
	@echo "  DATA_ROOT=/path/to/Accounting"
	@echo "  INBOX_REL=1_Input_Raw/00_inbox"
	@echo "  APPLY=0|1 BACKUP=0|1 WIPE_INBOX=0|1"
	@echo "  PF_ARGS='...'"
	@echo ""
	@echo "Examples:"
	@echo "  make triage-payment DATA_ROOT=$(HOME)/RAG_Sync/Accounting APPLY=0"
	@echo "  make triage-payment APPLY=1 BACKUP=1"
	@echo "  make triage-payment INBOX_REL=moved/payment APPLY=0"
	@echo "  make concat digest"

venv:
	python3 -m venv .venv
	source .venv/bin/activate
	pip install -U pip
	pip install -e . pytest

test:
	source .venv/bin/activate
	pytest -q

# -------- Triage cycles --------

triage-payment:
	$(MAKE) triage FLOW=payment

triage-statement:
	$(MAKE) triage FLOW=statement

triage-general:
	$(MAKE) triage FLOW=general

triage:
	@echo "Running triage cycle"
	@echo "  DATA_ROOT=$(DATA_ROOT)"
	@echo "  FLOW=$(FLOW)"
	@echo "  INBOX_REL=$(INBOX_REL)"
	@echo "  APPLY=$(APPLY) BACKUP=$(BACKUP) WIPE_INBOX=$(WIPE_INBOX)"
	bash scripts/run_triage_cycle_general.sh \
	  --data-root "$(DATA_ROOT)" \
	  --flow "flows/$(FLOW)" \
	  --inbox "$(DATA_ROOT)/$(INBOX_REL)" \
	  $(if $(filter 1,$(APPLY)),--apply,) \
	  $(if $(filter 1,$(BACKUP)),--backup,) \
	  $(if $(filter 1,$(WIPE_INBOX)),--wipe-inbox,) \
	  $(if $(strip $(PF_ARGS)),--pf-args '$(PF_ARGS)',)

# -------- Atomic helpers --------

index-flow:
	@echo "Index inbox only"
	@echo "  DATA_ROOT=$(DATA_ROOT)"
	@echo "  FLOW=$(FLOW)"
	@echo "  INBOX_REL=$(INBOX_REL)"
	TS=$$(date -u +%Y%m%dT%H%M%SZ)
	OUT_DIR="$(DATA_ROOT)/4_Analysis_Workflows/triage_runs/$(FLOW)/run_$${TS}"
	mkdir -p "$$OUT_DIR"
	python3 scripts/triage_indexer.py \
	  --input-dir "$(DATA_ROOT)/$(INBOX_REL)" \
	  --out "$$OUT_DIR/input.jsonl"
	ln -sf "$$(realpath "$$OUT_DIR/input.jsonl")" "$(DATA_ROOT)/4_Analysis_Workflows/triage_input_latest.jsonl"
	@echo "Wrote: $$OUT_DIR/input.jsonl"

concat:
	@echo "Concat + dedupe outputs into DATA_ROOT/artifacts/jsonl"
	mkdir -p "$(DATA_ROOT)/artifacts/jsonl"
	python3 scripts/concat_dedupe_jsonl.py \
	  "$(DATA_ROOT)/4_Analysis_Workflows/pf_runs/flow_payment/run_*/output.jsonl" \
	  "$(DATA_ROOT)/4_Analysis_Workflows/pf_runs/flow_statement/run_*/output.jsonl" \
	  -o "$(DATA_ROOT)/artifacts/jsonl/combined_dedup.jsonl"
	@echo "Wrote: $(DATA_ROOT)/artifacts/jsonl/combined_dedup.jsonl"

digest:
	@echo "Build digest outputs into DATA_ROOT/artifacts/digest"
	mkdir -p "$(DATA_ROOT)/artifacts/digest"
	python3 scripts/digest_payments_statements.py \
	  --input "$(DATA_ROOT)/4_Analysis_Workflows/triage_output_all_combined.jsonl" \
	  --out-dir "$(DATA_ROOT)/artifacts/digest"
	@echo "Wrote: $(DATA_ROOT)/artifacts/digest/*"
