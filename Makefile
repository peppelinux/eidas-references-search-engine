# eIDAS / EUDI legal texts — sync from EU Cellar
#
#   cd eidas-legal-tech-references
#   make              # sync all acts (default)
#   make check        # compare upstream hashes (no writes)
#   make sync ID=2024-2979
#
# From repo root:
#   make -C eidas-legal-tech-references

ROOT     := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
SYNC     := $(ROOT)scripts/sync-legal-texts.py
CONVERT  := $(ROOT)scripts/convert-to-markdown.py
REQ      := $(ROOT)requirements.txt
MANIFEST := $(ROOT)manifest.yaml

PYTHON  ?= python3
PIP     ?= pip3
PANDOC  ?= pandoc
WORKERS ?= 10

# Optional: sync a single manifest id (e.g. make sync ID=2024-2979)
SYNC_ARGS := --workers $(WORKERS) $(if $(ID),--id $(ID),)
ID_ARGS := $(if $(ID),--id $(ID),)
CONVERT_ARGS := $(if $(ID),--id $(ID),) $(if $(FORCE),--force,)

TECH     := $(ROOT)referenced-standards/
REPORT   := $(ROOT)scripts/generate-references-report.py
REPORT_DIR := $(ROOT)report

.PHONY: all sync check deps clean help dry-run markdown specs discover-specs metadata-specs summaries summaries-legal summaries-specs report report-specs clean-specs

all: sync markdown specs report-specs

help:
	@echo "eIDAS legal texts — Makefile targets"
	@echo ""
	@echo "  make / make all    Sync, markdown, standards, and references report"
	@echo "  make sync          Download or refresh all acts from Cellar"
	@echo "  make markdown      Convert synced .html to .md (needs pandoc)"
	@echo "  make specs         Download referenced standards (referenced-standards/)"
	@echo "  make summaries     Summaries for legal acts + specs, then refresh report/"
	@echo "  make report        Report index + search UI (report/index.html, search.html)"
	@echo "  make report-specs  Alias for make report"
	@echo "  make check         Report upstream drift (exit 2 if changed)"
	@echo "  make dry-run       List what would be synced (no downloads)"
	@echo "  make deps          Install Python deps (PyYAML)"
	@echo "  make clean         Remove legal PDF/HTML/Markdown + standards tree"
	@echo ""
	@echo "Variables:"
	@echo "  ID=<manifest-id>   Limit sync or markdown to one act"
	@echo "  FORCE=1            Rebuild .md even if newer than .html"
	@echo "  WORKERS=10         Parallel HTTP workers for Cellar sync (default: 10)"
	@echo "  PYTHON=python3     Python interpreter"
	@echo "  PANDOC=pandoc      Pandoc executable for markdown target"
	@echo ""
	@echo "Examples:"
	@echo "  make sync ID=eidas-consolidated"
	@echo "  make markdown FORCE=1"
	@echo "  make -C eidas-legal-tech-references check"

deps:
	$(PYTHON) -m pip install -r "$(REQ)"

sync: $(SYNC) $(MANIFEST)
	$(PYTHON) "$(SYNC)" $(SYNC_ARGS)

check: $(SYNC) $(MANIFEST)
	$(PYTHON) "$(SYNC)" --check-only $(SYNC_ARGS)

dry-run: $(SYNC) $(MANIFEST)
	$(PYTHON) "$(SYNC)" --dry-run $(SYNC_ARGS)

markdown: $(CONVERT)
	PANDOC="$(PANDOC)" $(PYTHON) "$(CONVERT)" $(CONVERT_ARGS)

specs:
	$(MAKE) -C "$(TECH)" specs WORKERS=$(WORKERS)

discover-specs:
	$(MAKE) -C "$(TECH)" discover WORKERS=$(WORKERS)

metadata-specs:
	$(MAKE) -C "$(TECH)" metadata WORKERS=$(WORKERS)

summaries: summaries-legal summaries-specs report

summaries-legal:
	$(PYTHON) "$(ROOT)scripts/enrich-legal-summaries.py"

summaries-specs:
	$(MAKE) -C "$(TECH)" summaries

report report-specs: $(REPORT)
	@mkdir -p "$(REPORT_DIR)"
	$(PYTHON) "$(REPORT)" --output-dir "$(REPORT_DIR)"

clean-specs:
	$(MAKE) -C "$(TECH)" clean

clean: clean-specs
	rm -rf "$(REPORT_DIR)"
	find "$(ROOT)regulation" "$(ROOT)implementing-acts" "$(ROOT)implementing-decisions" \
		\( -name '*.pdf' -o -name '*.html' -o -name '*.md' \) -delete 2>/dev/null || true
	@echo "Removed downloaded PDF/HTML/Markdown under regulation/, implementing-acts/, implementing-decisions/"
