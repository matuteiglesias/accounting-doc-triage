SHELL := /bin/bash
.ONESHELL:
.DEFAULT_GOAL := help

PYTHON ?= python3
DATA_ROOT ?=
SOURCE ?=
APPLY ?= 0

INFLIGHT ?= $(DATA_ROOT)/.accounting-doc-triage/inflight
EVIDENCE_STORE ?= $(DATA_ROOT)/evidence/original
DERIVED_ROOT ?= $(DATA_ROOT)/evidence/derived

help:
	@echo "accounting-doc-triage"
	@echo ""
	@echo "Development:"
	@echo "  make venv                  Create .venv with test dependencies"
	@echo "  make venv-docling          Add optional Docling runtime"
	@echo "  make test                  Run synthetic/unit tests only"
	@echo ""
	@echo "Evidence intake (external DATA_ROOT only):"
	@echo "  make capture SOURCE=/path/proof.pdf DATA_ROOT=/path/accounting"
	@echo "                              Dry-run by default"
	@echo "  make capture ... APPLY=1   Atomically claim and capture one document"
	@echo "  make recover DATA_ROOT=... APPLY=1"
	@echo "                              Resume files left in inflight"
	@echo "  make convert SOURCE=/path/captured.pdf DATA_ROOT=/path/accounting"
	@echo "                              Write Docling derivative; source stays immutable"
	@echo ""
	@echo "Supported evidence inputs: PDF, PNG, JPG/JPEG"
	@echo "Historical PromptFlow scripts remain lineage only; they are not current commands."

venv:
	$(PYTHON) -m venv .venv
	source .venv/bin/activate
	pip install -U pip
	pip install -e . pytest

venv-docling: venv
	source .venv/bin/activate
	pip install -e '.[docling]'

test:
	$(PYTHON) -m pytest -q

_require_data_root:
	@test -n "$(DATA_ROOT)" || (echo "ERROR: DATA_ROOT is required" >&2; exit 2)

_require_source:
	@test -n "$(SOURCE)" || (echo "ERROR: SOURCE is required" >&2; exit 2)

capture: _require_data_root _require_source
	@echo "Capture source: $(SOURCE)"
	@echo "Evidence store: $(EVIDENCE_STORE)"
	@if [ "$(APPLY)" = "1" ]; then \
		$(PYTHON) -m accounting_doc_triage.cli capture "$(SOURCE)" \
		  --inflight "$(INFLIGHT)" --store "$(EVIDENCE_STORE)"; \
	else \
		$(PYTHON) -m accounting_doc_triage.cli capture "$(SOURCE)" \
		  --inflight "$(INFLIGHT)" --store "$(EVIDENCE_STORE)" --dry-run; \
	fi

recover: _require_data_root
	@test "$(APPLY)" = "1" || (echo "ERROR: recover mutates inflight state; rerun with APPLY=1" >&2; exit 2)
	$(PYTHON) -m accounting_doc_triage.cli recover \
	  --inflight "$(INFLIGHT)" --store "$(EVIDENCE_STORE)"

convert: _require_data_root _require_source
	$(PYTHON) -m accounting_doc_triage.cli convert "$(SOURCE)" \
	  --derived "$(DERIVED_ROOT)"
