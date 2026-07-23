# Referenced technical standards

Part of the [eIDAS legal & technical references method](../README.md): this module helps **implementers** (and legal reviewers of technical annexes) navigate normative references without manually chasing each ETSI deliverable or RFC.

It discovers **technical standards and specifications** cited in the parent legal corpus, classifies them by **standardization body**, downloads openly available copies in parallel, records **provenance** (`reference.json`), and **recursively** follows references inside downloaded documents.

## Folder layout (`standards/`)

| Subfolder | Body |
|-----------|------|
| `ARF/` | [EUDI ARF complementary technical specifications](https://github.com/eu-digital-identity-wallet/eudi-doc-architecture-and-reference-framework/tree/main/docs/technical-specifications) — `ARF/reference.json` is the **catalogue index**; each TS lives in `ARF/TSnn-<version>/` with its markdown and `reference.json` |
| `ETSI/` | ETSI EN / TS / TR / SR |
| `IETF/` | RFCs |
| `W3C/` | W3C Recommendations (catalogued entries) |
| `ISO-IEC/` | ISO / IEC (metadata only — typically not free) |
| `CEN/` | CEN / CEN TS (metadata only) |
| `ITU-T/` | ITU-T (metadata only) |
| `IEEE/` | IEEE (metadata only) |
| `other/` | Unclassified |

Each specification has its own directory with downloaded files and/or `reference.json` when no public copy is available.

## Usage

From the parent directory:

```bash
make specs            # discover + download (WORKERS=10, DEPTH=2)
make discover-specs   # list references only
```

Or from here:

```bash
make specs
make discover
```

### Variables

- `WORKERS` — parallel HTTP workers (default `10`)
- `DEPTH` — recursion into downloaded specs (default `2`; PDF via `pdftotext` when installed)
- `LEGAL` — path to parent legal corpus (default `..`)
- `FORCE=1` — re-download existing files

## How discovery works

1. Scan `../**/*.md` for normative references.
2. Include the [ARF technical specifications](https://github.com/eu-digital-identity-wallet/eudi-doc-architecture-and-reference-framework/tree/v3.0.0/docs/technical-specifications) catalogue (TS01–TS14, ARF `v3.0.0`) as first-class sources (always synced, not only when cited in EU law). Full markdown is pulled from [eudi-doc-standards-and-technical-specifications](https://github.com/eu-digital-identity-wallet/eudi-doc-standards-and-technical-specifications/tree/main/docs/technical-specifications) (`main`). Override with `ARF_REF` / `ARF_CONTENT_REF`.
3. Download into `standards/<body>/`.
4. Extract text from downloaded files and repeat for nested references up to `DEPTH`.
5. **Prune superseded versions** — if the same specification appears in several versions (e.g. ETSI TS 119 612 V2.3.1 and V2.4.1, or an ARF TS bump), only the **latest** is kept on disk and in `manifest.lock.json`; older folders are deleted automatically.

`manifest.lock.json` records status per reference.

Each spec folder includes **`reference.json`** with:

- `download_url` / `download_urls` — HTTPS catalogue/search links (ISO uses [iso.org search](https://www.iso.org/search.html), not `/standard/{id}.html`, which is an internal catalogue id)
- `version` — normative version string when parsed from citations
- `released_at` — best-effort ISO-8601 release date (e.g. from ETSI `(YYYY-MM)`)
- `parent_legal_regulations` — EU acts that cite this spec (`id`, `title`, `celex`, `eli`, …)
- `parent_specifications` — other standards that cite this spec (nested references)
- `tags` — **small fixed vocabulary** for filtering only (~12 values: `downloaded`, `cited-by-eu-law`, `319-series`, `arf-technical-spec`, …). Assigned in `compute_tags()` from provenance and series; **not** mined from document text. See `scripts/tag_normalize.py`.
- `title` — official or catalogue title when known (especially for unavailable ISO/CEN/ITU specs)
- `purpose` — one-line scope from public catalogues or ISO series heuristics
- `summary` — short description (from downloaded text, or catalogue + EU legal context when unavailable)
- `scope_keywords` — **subject terms** (wallet, attestation, RFC 5280, …): ETSI Keywords line + designation + domain glossary hits in abstract/summary — **not** random frequent words
- `summary_meta` — how the summary was derived (`artifact`, `sources`, `generated_at`)
- `catalogue_meta` — optional provenance for catalogue lookups (Wikipedia, RFC Editor, legal citation, …)

Refresh metadata without re-downloading: `make metadata`  
Regenerate summaries: `make summaries` (downloaded specs need `pdftotext` for ETSI PDFs; unavailable specs use public catalogue lookups, cached in `.catalogue-cache.json`)

### Report

```bash
make -C .. report
```

Writes under **`../report/`** (corpus root). Published on GitHub Pages at **`/eidas-legal-tech-references/report/index.html`** when the corpus is built by CI (see repository root `scripts/build-gh-pages-site.sh`).

| File | Content |
|------|---------|
| `index.html` | Full report: summary, tables, interactive hierarchical graph (search + SDO filters) |
| `search.html` | Full-text search (legal markdown, specs, tags, SDO filters) |
| `search-index.json` | Search index (generated with the report) |
| `REFERENCES-REPORT.md` | Same content in markdown |
| `references-graph.json` | Nodes and edges for tooling |
