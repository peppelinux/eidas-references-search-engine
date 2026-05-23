# Technical specifications referenced by eIDAS / EUDI legal texts
#
#   make specs          # discover + download (requires legal markdown in parent)
#   make discover       # list references only
#
# Invoked from parent:  make specs   or   make -C referenced-standards specs

ROOT      := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
SYNC      := $(ROOT)scripts/sync-technical-specs.py
LEGAL     ?= $(ROOT)..
STANDARDS := $(ROOT)standards

PYTHON  ?= python3
WORKERS ?= 10
DEPTH   ?= 2

SPECS_ARGS := --workers $(WORKERS) --max-depth $(DEPTH) --legal-root "$(LEGAL)"
SPECS_ARGS += $(if $(FORCE),--force,)

SUMMARIZE := $(ROOT)scripts/enrich-reference-summaries.py

.PHONY: all specs discover metadata summaries clean help

all: specs

help:
	@echo "eIDAS technical specifications"
	@echo ""
	@echo "  make / make specs   Discover refs in legal texts + download (parallel)"
	@echo "  make discover       List references without downloading"
	@echo "  make metadata       Refresh all reference.json (provenance + URLs)"
	@echo "  make summaries      Add summaries (downloaded + fallback for unavailable)"
	@echo "  make clean          Remove downloaded standards tree"
	@echo ""
	@echo "Reports: run  make report  from parent eidas-legal-tech-references/  → report/index.html"
	@echo ""
	@echo "Variables:"
	@echo "  WORKERS=10          Parallel download workers"
	@echo "  DEPTH=2             Recursion into downloaded specs (nested references)"
	@echo "  LEGAL=..            Path to parent eidas-legal-tech-references/"
	@echo "  FORCE=1             Re-download existing files"
	@echo ""
	@echo "Prerequisite: make -C .. sync markdown  (regulations + markdown in parent)"

specs: $(SYNC)
	$(PYTHON) "$(SYNC)" $(SPECS_ARGS)

discover: $(SYNC)
	$(PYTHON) "$(SYNC)" $(SPECS_ARGS) --discover-only

metadata: $(SYNC)
	$(PYTHON) "$(SYNC)" $(SPECS_ARGS) --metadata-only

summaries: $(SUMMARIZE)
	$(PYTHON) "$(SUMMARIZE)"

clean:
	rm -rf "$(STANDARDS)"
	@echo "Removed $(STANDARDS)/"
