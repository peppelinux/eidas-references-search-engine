# eIDAS legal and technical references

A **toolchain and repeatable method** for working with the complexity of the **eIDAS** framework (including EUDI amendments): consolidated regulation, dozens of implementing acts, and hundreds of normative references to technical standards.

It is aimed at two audiences:

| Audience | What this provides |
|----------|-------------------|
| **Legal** | Official EU legal texts (Cellar), readable markdown, traceable citations (`metadata.json`, CELEX, ELI), and a map of which act references which specification |
| **Implementers** | The same legal baseline plus a classified standards corpus (ETSI, IETF, W3C, …), download URLs, tags, nested references, and link reports for gap analysis and conformance work |

The goal is not to replace EUR-Lex or standards bodies, but to **reduce fragmentation**: one local, reproducible pipeline from regulation → markdown → standards → provenance graph.

## The method

```text
EU Cellar (official PDF/HTML)
        ↓  make sync
Legal corpus (regulation + implementing acts)
        ↓  make markdown
Searchable markdown + per-act metadata
        ↓  make specs
Referenced standards (by SDO) + reference.json per spec
        ↓  make report
report/index.html (interactive graph + tables), search.html (full-text search), REFERENCES-REPORT.md, references-graph.json
```

Each step is scripted, parallelised where possible, and **re-runnable** when EU law or standards are updated (`manifest.yaml` + lockfiles).

### Principles

1. **Official sources** — Publications Office Cellar for EU law; SDO catalogues (ETSI deliver, RFC Editor, …) for standards where openly available.
2. **Provenance** — Every technical reference records parent legal acts and parent specifications that cited it.
3. **Transparency** — Unavailable (licensed) standards still appear as catalogue entries with URLs and tags, not silent omissions.
4. **Automation** — `make all` at repo root, or step-by-step targets for partial updates.

## Published report

A browsable, up-to-date **interactive report** (graph, tables, search) generated from this repository is hosted at:

**[eIDAS technical references report](https://peppelinux.github.io/eidas-references-search-engine/report/index.html)**

## Repository layout

| Path | Content |
|------|---------|
| [`manifest.yaml`](manifest.yaml) | CELEX ids and ELI links for regulations / CIRs |
| [`regulation/`](regulation/) | Consolidated eIDAS |
| [`implementing-acts/`](implementing-acts/) | Commission implementing regulations |
| [`implementing-decisions/`](implementing-decisions/) | Commission implementing decisions |
| [`referenced-standards/`](referenced-standards/) | Standards corpus and sync scripts |
| [`report/`](report/) | Generated references report (`make report`) |
| [`scripts/`](scripts/) | Cellar sync, markdown conversion, report generator |

Each legal act folder: `{id}.pdf`, `{id}.html`, `{id}.md`, `metadata.json`.

## Quick start

```bash
pip install -r requirements.txt   # or: apt install python3-yaml
cd eidas-legal-tech-references
make help
make              # sync + markdown + standards + optional report
```

From the **repository root**: `make all`

### Common targets

| Target | Purpose |
|--------|---------|
| `make sync` | Download / refresh legal texts from Cellar |
| `make markdown` | Convert OJ HTML to markdown (pandoc) |
| `make specs` | Discover and download referenced standards |
| `make metadata-specs` | Refresh `reference.json` (URLs, parents, tags) |
| `make summaries` | Summaries for EU legal acts + all technical references |
| `make summaries-legal` | Add `summary` + `scope_keywords` to legal `metadata.json` |
| `make summaries-specs` | Summaries for downloaded specs (+ fallback for unavailable) |
| `make report` | Generate `report/index.html`, `search.html`, and JSON exports |

## Legal texts

- **Sync** — EU Cellar (`publications.europa.eu`), parallel workers (default 10)
- **Markdown** — pandoc on OJ XHTML → GFM

Scripts: `./scripts/sync-legal-texts.py`, `./scripts/convert-to-markdown.py`.

## Technical standards

See [`referenced-standards/README`](referenced-standards/README) for SDO folders, `reference.json` schema, recursion into nested references, and the references report.

## Presentations

Slide decks in the parent repository (e.g. trust management, IT-Wallet) use this corpus as background; they explain *concepts*, while this folder provides the *reproducible legal/technical mirror*.

## Sources

- [EUR-Lex](https://eur-lex.europa.eu/)
- [Publications Office Cellar](https://publications.europa.eu/)
- [EUDI ARF technical specifications](https://github.com/eu-digital-identity-wallet/eudi-doc-architecture-and-reference-framework/tree/main/docs/technical-specifications) (EC TS01–TS11; synced under `referenced-standards/standards/ARF/`)
- [EUDI implementing acts overview](https://docs.igrant.io/regulations/implementing-acts-overview/)
