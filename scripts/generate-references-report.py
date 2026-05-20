#!/usr/bin/env python3
"""
Build reports of technical references and links (legal acts ↔ specs ↔ specs).

Writes under eidas-legal-tech-references/report/:
  index.html              — full report + interactive hierarchical graph explorer
  graph-data.json/js      — graph nodes/edges for the explorer
  search.html / search.js — full-text search UI (uses search-index.json)
  REFERENCES-REPORT.md    — markdown export
  references-graph.json   — machine-readable nodes and edges
  search-index.json       — searchable legal markdown + specification corpus

Usage:
  ./scripts/generate-references-report.py
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from urllib.parse import urlencode
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CORPUS_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
REPORT_ASSETS = SCRIPTS_DIR / "report-assets"
REF_ROOT = CORPUS_ROOT / "referenced-standards"
STANDARDS_DIR = REF_ROOT / "standards"
DEFAULT_OUT = CORPUS_ROOT / "report"

import sys

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from build_search_index import build_search_index, write_search_index

DOWNLOADED_STATUSES = frozenset({"downloaded", "unchanged"})

ARF_TS_INDEX_URL = (
    "https://github.com/eu-digital-identity-wallet/"
    "eudi-doc-architecture-and-reference-framework/tree/main/docs/technical-specifications"
)


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def report_rel_href(abs_path: Path) -> str:
    rel = abs_path.relative_to(CORPUS_ROOT).as_posix()
    return f"../{rel}"


def source_stem(source: str) -> Path | None:
    """Filesystem stem (no suffix) for a corpus source path string."""
    if not source or not str(source).strip():
        return None
    norm = str(source).strip().replace("\\", "/")
    if norm.startswith(("implementing-acts/", "implementing-decisions/", "regulation/")):
        return (CORPUS_ROOT / norm).with_suffix("")
    return (STANDARDS_DIR / norm).with_suffix("")


def _corpus_format_paths(stem: Path) -> dict[str, Path]:
    """Resolve local md / html / pdf paths for a source stem (incl. reference.json files)."""
    found: dict[str, Path] = {}
    for ext in ("md", "html", "pdf"):
        candidate = stem.with_suffix(f".{ext}")
        if candidate.is_file():
            found[ext] = candidate
    ref_json = stem.parent / "reference.json"
    if ref_json.is_file():
        try:
            data = json.loads(ref_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        for kind, meta in (data.get("files") or {}).items():
            if not isinstance(meta, dict):
                continue
            k = kind.lower()
            ext = "html" if k == "htm" else k
            if ext not in ("md", "html", "pdf"):
                continue
            rel = meta.get("path", "")
            if not rel:
                continue
            p = REF_ROOT / rel if not Path(rel).is_absolute() else Path(rel)
            if p.is_file():
                found[ext] = p
    return found


def render_corpus_source_links_html(source: str | None) -> str:
    if not source:
        return "—"
    stem = source_stem(source)
    if not stem:
        return f"<code>{esc(source)}</code>"
    formats = _corpus_format_paths(stem)
    parts: list[str] = [f'<code class="src-path">{esc(source)}</code>']
    link_bits: list[str] = []
    for ext in ("md", "html", "pdf"):
        path = formats.get(ext)
        if path:
            href = report_rel_href(path)
            if ext in ("html", "pdf"):
                viewer_q = urlencode({"src": href, "title": path.name})
                view = (
                    f' <a href="viewer.html?{esc(viewer_q)}" '
                    f'target="eidas_doc_viewer" rel="noopener" class="src-view">view</a>'
                )
            else:
                view = ""
            link_bits.append(
                f'<a href="{esc(href)}" title="{esc(path.name)}">{ext.upper()}</a>{view}'
            )
        else:
            link_bits.append(f'<span class="src-missing" title="not in corpus">{ext}</span>')
    if link_bits:
        parts.append("<br/>" + " · ".join(link_bits))
    return "".join(parts)


def render_corpus_source_links_md(source: str | None) -> str:
    if not source:
        return "—"
    stem = source_stem(source)
    if not stem:
        return f"`{source}`"
    formats = _corpus_format_paths(stem)
    bits: list[str] = []
    for ext in ("md", "html", "pdf"):
        path = formats.get(ext)
        if path:
            bits.append(f"[{ext}]({report_rel_href(path)})")
        else:
            bits.append(f"{ext}:—")
    return f"`{source}` — " + ", ".join(bits)


def load_references(standards_root: Path) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for path in sorted(standards_root.rglob("reference.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        data["_path"] = str(path.relative_to(REF_ROOT))
        data["_folder"] = str(path.parent.relative_to(standards_root))
        refs.append(data)
    return refs


def spec_label(doc: dict[str, Any]) -> str:
    parts = [doc.get("body", ""), doc.get("designation", "")]
    if doc.get("version"):
        parts.append(f"V{doc['version']}")
    return " ".join(p for p in parts if p).strip()


def spec_node_id(doc: dict[str, Any]) -> str:
    return spec_label(doc).replace('"', "'")


def legal_node_id(parent: dict[str, Any]) -> str:
    return f"legal:{parent.get('id', 'unknown')}"


def mermaid_id(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", raw)[:80]


def _spec_search_text(doc: dict[str, Any]) -> str:
    parts = [
        doc.get("body"),
        doc.get("designation"),
        doc.get("version"),
        doc.get("title"),
        doc.get("purpose"),
        doc.get("summary"),
        " ".join(doc.get("scope_keywords") or []),
        " ".join(doc.get("tags") or []),
    ]
    for lp in doc.get("parent_legal_regulations") or []:
        parts.extend([lp.get("id"), lp.get("title"), lp.get("celex")])
    return " ".join(str(p) for p in parts if p)


def load_legal_act_metadata(section: str | None, act_id: str | None) -> dict[str, Any]:
    if not section or not act_id:
        return {}
    meta_path = CORPUS_ROOT / section / act_id / "metadata.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _apply_legal_metadata(node: dict[str, Any]) -> None:
    meta = load_legal_act_metadata(node.get("section"), node.get("act_id"))
    if not meta:
        return
    if meta.get("summary"):
        node["summary"] = meta["summary"]
    if meta.get("scope_keywords"):
        node["scope_keywords"] = meta["scope_keywords"]
    if meta.get("summary_meta"):
        node["summary_meta"] = meta["summary_meta"]


def _legal_search_text(node: dict[str, Any]) -> str:
    return " ".join(
        str(p)
        for p in (
            node.get("act_id"),
            node.get("title"),
            node.get("celex"),
            node.get("kind"),
            node.get("section"),
            node.get("summary"),
            " ".join(node.get("scope_keywords") or []),
        )
        if p
    )


def build_graph(refs: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    for doc in refs:
        sid = spec_node_id(doc)
        nodes[sid] = {
            "id": sid,
            "type": "specification",
            "body": doc.get("body"),
            "designation": doc.get("designation"),
            "version": doc.get("version"),
            "title": doc.get("title"),
            "purpose": doc.get("purpose"),
            "status": doc.get("status"),
            "download_url": doc.get("download_url"),
            "folder": doc.get("_folder"),
            "files": doc.get("files") or {},
            "tags": doc.get("tags", []),
            "summary": doc.get("summary"),
            "scope_keywords": doc.get("scope_keywords") or [],
            "parent_legal_regulations": doc.get("parent_legal_regulations") or [],
            "parent_specifications": doc.get("parent_specifications") or [],
            "search_text": _spec_search_text(doc),
        }
        for lp in doc.get("parent_legal_regulations") or []:
            lid = legal_node_id(lp)
            if lid not in nodes:
                nodes[lid] = {
                    "id": lid,
                    "type": "legal_regulation",
                    "act_id": lp.get("id"),
                    "title": lp.get("title"),
                    "celex": lp.get("celex"),
                    "eli": lp.get("eli"),
                    "kind": lp.get("kind"),
                    "section": lp.get("section"),
                }
                _apply_legal_metadata(nodes[lid])
                nodes[lid]["search_text"] = _legal_search_text(nodes[lid])
            edges.append(
                {
                    "from": lid,
                    "to": sid,
                    "kind": "cites",
                    "source": lp.get("source"),
                }
            )
        for sp in doc.get("parent_specifications") or []:
            pid = spec_node_id(
                {
                    "body": sp.get("body"),
                    "designation": sp.get("designation"),
                    "version": sp.get("version"),
                }
            )
            if pid not in nodes:
                nodes[pid] = {
                    "id": pid,
                    "type": "specification",
                    "body": sp.get("body"),
                    "designation": sp.get("designation"),
                    "version": sp.get("version"),
                    "status": None,
                }
            edges.append(
                {
                    "from": pid,
                    "to": sid,
                    "kind": "references",
                    "source": sp.get("source"),
                }
            )

    by_spec_id = {spec_node_id(doc): doc for doc in refs}
    for n in nodes.values():
        if n.get("type") != "specification":
            continue
        doc = by_spec_id.get(n["id"])
        if not doc:
            continue
        patched = False
        if not n.get("summary") and doc.get("summary"):
            n["summary"] = doc["summary"]
            patched = True
        if not n.get("scope_keywords") and doc.get("scope_keywords"):
            n["scope_keywords"] = doc["scope_keywords"]
        if not n.get("tags") and doc.get("tags"):
            n["tags"] = doc["tags"]
        if n.get("status") is None and doc.get("status"):
            n["status"] = doc["status"]
        if patched:
            n["search_text"] = _spec_search_text({**doc, **n})

    arf_catalog = next(
        (doc for doc in refs if doc.get("body") == "ARF" and doc.get("_folder") == "ARF"),
        None,
    )
    arf_ts_docs = [
        doc for doc in refs if doc.get("body") == "ARF" and doc.get("_folder") != "ARF"
    ]
    if arf_catalog:
        catalog_id = spec_node_id(arf_catalog)
        for doc in arf_ts_docs:
            sid = spec_node_id(doc)
            if sid not in nodes:
                continue
            edges.append(
                {
                    "from": catalog_id,
                    "to": sid,
                    "kind": "references",
                    "source": "ARF/technical-specifications",
                }
            )
        for act_id in ("2024-2979", "2024-2977", "2024-2982", "eidas-consolidated"):
            lid = legal_node_id({"id": act_id})
            if lid in nodes:
                edges.append(
                    {
                        "from": lid,
                        "to": catalog_id,
                        "kind": "related",
                        "source": "corpus:arf-wallet-acts",
                    }
                )
                break

    node_list = list(nodes.values())
    for n in node_list:
        if n.get("type") == "legal_regulation":
            _apply_legal_metadata(n)
            if "search_text" not in n or n.get("summary"):
                n["search_text"] = _legal_search_text(n)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": node_list,
        "edges": edges,
    }


def write_graph_bundle(out_dir: Path, graph: dict[str, Any]) -> tuple[Path, Path]:
    payload = json.dumps(graph, ensure_ascii=False, separators=(",", ":"))
    json_path = out_dir / "graph-data.json"
    json_path.write_text(payload + "\n", encoding="utf-8")
    js_path = out_dir / "graph-data.js"
    js_path.write_text(f"window.EIDAS_GRAPH_DATA={payload};\n", encoding="utf-8")
    return json_path, js_path


def render_mermaid(graph: dict[str, Any], *, downloaded_only: bool) -> str:
    lines = [
        "flowchart LR",
        "  classDef legal fill:#e8f4fc,stroke:#036",
        "  classDef spec fill:#f5f5f5,stroke:#666",
        "  classDef specOk fill:#e8fce8,stroke:#363",
    ]
    node_ids: dict[str, str] = {}

    for node in graph["nodes"]:
        nid = mermaid_id(node["id"])
        node_ids[node["id"]] = nid
        if node["type"] == "legal_regulation":
            label = node.get("act_id", "?")
            if node.get("title"):
                title = node["title"]
                if len(title) > 48:
                    title = title[:45] + "..."
                label += f"<br/>{title}"
            lines.append(f'  {nid}["{label}"]:::legal')
        else:
            status = node.get("status") or ""
            if downloaded_only and status not in DOWNLOADED_STATUSES:
                continue
            label = f"{node.get('body')} {node.get('designation')}"
            if node.get("version"):
                label += f"<br/>V{node['version']}"
            cls = "specOk" if status in DOWNLOADED_STATUSES else "spec"
            lines.append(f'  {nid}["{label}"]:::{cls}')

    for edge in graph["edges"]:
        fid = node_ids.get(edge["from"])
        tid = node_ids.get(edge["to"])
        if not fid or not tid:
            continue
        if edge["kind"] == "cites":
            lines.append(f"  {fid} -->|cites| {tid}")
        else:
            lines.append(f"  {fid} -.->|references| {tid}")

    return "\n".join(lines)


def report_data(refs: list[dict[str, Any]], graph: dict[str, Any]) -> dict[str, Any]:
    downloaded = [r for r in refs if r.get("status") in DOWNLOADED_STATUSES]
    unavailable = [r for r in refs if r.get("status") == "unavailable"]
    other = [r for r in refs if r not in downloaded and r not in unavailable]
    by_body: dict[str, list] = defaultdict(list)
    for r in refs:
        by_body[r.get("body", "other")].append(r)
    legal_edges = [e for e in graph["edges"] if e["kind"] == "cites"]
    spec_edges = [e for e in graph["edges"] if e["kind"] == "references"]
    legal_nodes = {n["id"]: n for n in graph["nodes"] if n["type"] == "legal_regulation"}
    return {
        "refs": refs,
        "graph": graph,
        "downloaded": downloaded,
        "unavailable": unavailable,
        "other": other,
        "by_body": dict(by_body),
        "legal_edges": legal_edges,
        "spec_edges": spec_edges,
        "legal_nodes": legal_nodes,
    }


def render_markdown(data: dict[str, Any], mermaid_src: str) -> str:
    refs = data["refs"]
    graph = data["graph"]
    downloaded = data["downloaded"]
    unavailable = data["unavailable"]
    other = data["other"]
    by_body = data["by_body"]
    legal_edges = data["legal_edges"]
    spec_edges = data["spec_edges"]
    legal_nodes = data["legal_nodes"]

    lines = [
        "# Technical references report",
        "",
        f"Generated: {graph['generated_at']}",
        "",
        "Open **`index.html`** in this folder for the interactive version.",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|------:|",
        f"| Total references | {len(refs)} |",
        f"| Downloaded / unchanged | {len(downloaded)} |",
        f"| Unavailable | {len(unavailable)} |",
        f"| Other | {len(other)} |",
        f"| Legal → specification links | {len(legal_edges)} |",
        f"| Specification → specification links | {len(spec_edges)} |",
        "",
        "### By standardization body",
        "",
        "| Body | Total | Downloaded |",
        "|------|------:|-----------:|",
    ]
    for body in sorted(by_body):
        items = by_body[body]
        n_dl = sum(1 for r in items if r.get("status") in DOWNLOADED_STATUSES)
        lines.append(f"| {body} | {len(items)} | {n_dl} |")

    lines.extend(
        [
            "",
            "## Downloaded references",
            "",
        "| Specification | Version | Summary | Scope keywords | Folder | Download |",
        "|---------------|---------|---------|----------------|--------|----------|",
        ]
    )
    for doc in sorted(downloaded, key=lambda d: (d.get("body", ""), d.get("designation", ""))):
        url = doc.get("download_url") or "—"
        if url != "—":
            url = f"[link]({url})"
        sm = (doc.get("summary") or "")[:120].replace("|", "/")
        kw = ", ".join((doc.get("scope_keywords") or [])[:5])
        lines.append(
            f"| {doc.get('designation', '?')} | {doc.get('version') or '—'} | "
            f"{sm or '—'} | {kw or '—'} | `{doc.get('_folder', '')}` | {url} |"
        )

    if unavailable:
        lines.extend(
            [
                "",
                "## Unavailable references",
                "",
                "| Specification | Version | Tags | Download URL |",
                "|---------------|---------|------|--------------|",
            ]
        )
        for doc in sorted(unavailable, key=lambda d: spec_label(d)):
            tags = ", ".join(doc.get("tags") or [])[:80]
            url = doc.get("download_url") or (doc.get("download_urls") or ["—"])[0]
            if url and url != "—":
                url = f"[link]({url})"
            lines.append(
                f"| {spec_label(doc)} | {doc.get('version') or '—'} | {tags} | {url} |"
            )

    lines.extend(
        [
            "",
            "## Links from EU legal acts",
            "",
            "| Legal act | CELEX | Specification cited | Source in corpus |",
            "|-----------|-------|---------------------|------------------|",
        ]
    )
    seen: set[tuple[str, str]] = set()
    for edge in sorted(legal_edges, key=lambda e: (e["from"], e["to"])):
        key = (edge["from"], edge["to"])
        if key in seen:
            continue
        seen.add(key)
        ln = legal_nodes.get(edge["from"], {})
        lines.append(
            f"| {ln.get('act_id', edge['from'])} | {ln.get('celex') or '—'} | {edge['to']} | "
            f"{render_corpus_source_links_md(edge.get('source'))} |"
        )

    if spec_edges:
        lines.extend(
            [
                "",
                "## Links between specifications",
                "",
                "| From | To | Source in corpus |",
                "|------|-----|------------------|",
            ]
        )
        seen_spec: set[tuple[str, str]] = set()
        for edge in sorted(spec_edges, key=lambda e: (e["from"], e["to"])):
            key = (edge["from"], edge["to"])
            if key in seen_spec:
                continue
            seen_spec.add(key)
            lines.append(
                f"| {edge['from']} | {edge['to']} | {render_corpus_source_links_md(edge.get('source'))} |"
            )

    lines.extend(["", "## Reference graph (Mermaid)", "", "```mermaid", mermaid_src, "```", ""])
    return "\n".join(lines) + "\n"


def render_html(data: dict[str, Any], mermaid_src: str) -> str:
    refs = data["refs"]
    graph = data["graph"]
    downloaded = data["downloaded"]
    unavailable = data["unavailable"]
    other = data["other"]
    by_body = data["by_body"]
    legal_edges = data["legal_edges"]
    spec_edges = data["spec_edges"]
    legal_nodes = data["legal_nodes"]
    generated = graph["generated_at"]

    # Summary rows by body
    body_rows = []
    for body in sorted(by_body):
        items = by_body[body]
        n_dl = sum(1 for r in items if r.get("status") in DOWNLOADED_STATUSES)
        body_rows.append(
            f"<tr><td>{esc(body)}</td><td>{len(items)}</td><td>{n_dl}</td></tr>"
        )

    # Downloaded table
    dl_rows = []
    for doc in sorted(downloaded, key=lambda d: (d.get("body", ""), d.get("designation", ""))):
        url = doc.get("download_url")
        url_cell = (
            f'<a href="{esc(url)}" rel="noopener">{esc(url)}</a>' if url else "—"
        )
        tags = ", ".join(doc.get("tags") or [])
        summary = doc.get("summary") or ""
        if len(summary) > 160:
            summary = summary[:157].rsplit(" ", 1)[0] + "…"
        kw = ", ".join((doc.get("scope_keywords") or [])[:6])
        dl_rows.append(
            f"<tr>"
            f"<td>{esc(doc.get('body'))}</td>"
            f"<td>{esc(doc.get('designation'))}</td>"
            f"<td>{esc(doc.get('version'))}</td>"
            f"<td class=\"summary\">{esc(summary) or '—'}</td>"
            f"<td class=\"tags\">{esc(kw) or '—'}</td>"
            f"<td><code>{esc(doc.get('_folder'))}</code></td>"
            f"<td>{url_cell}</td>"
            f"<td class=\"tags\">{esc(tags)}</td>"
            f"</tr>"
        )

    unav_rows = []
    for doc in sorted(unavailable, key=lambda d: spec_label(d)):
        url = doc.get("download_url") or ((doc.get("download_urls") or [None])[0])
        url_cell = (
            f'<a href="{esc(url)}" rel="noopener">{esc(url)}</a>' if url else "—"
        )
        unav_rows.append(
            f"<tr>"
            f"<td>{esc(spec_label(doc))}</td>"
            f"<td>{esc(doc.get('version'))}</td>"
            f"<td class=\"tags\">{esc(', '.join(doc.get('tags') or []))}</td>"
            f"<td>{url_cell}</td>"
            f"</tr>"
        )

    legal_rows = []
    seen: set[tuple[str, str]] = set()
    for edge in sorted(legal_edges, key=lambda e: (e["from"], e["to"])):
        key = (edge["from"], edge["to"])
        if key in seen:
            continue
        seen.add(key)
        ln = legal_nodes.get(edge["from"], {})
        eli = ln.get("eli")
        act_cell = esc(ln.get("act_id", ""))
        if eli:
            act_cell = f'<a href="{esc(eli)}" rel="noopener">{act_cell}</a>'
        legal_rows.append(
            f"<tr>"
            f"<td>{act_cell}</td>"
            f"<td>{esc(ln.get('title'))}</td>"
            f"<td>{esc(ln.get('celex'))}</td>"
            f"<td>{esc(ln.get('kind'))}</td>"
            f"<td>{esc(edge['to'])}</td>"
            f"<td class=\"src-cell\">{render_corpus_source_links_html(edge.get('source'))}</td>"
            f"</tr>"
        )

    spec_link_rows = []
    seen_spec: set[tuple[str, str]] = set()
    for edge in sorted(spec_edges, key=lambda e: (e["from"], e["to"])):
        key = (edge["from"], edge["to"])
        if key in seen_spec:
            continue
        seen_spec.add(key)
        spec_link_rows.append(
            f"<tr><td>{esc(edge['from'])}</td><td>{esc(edge['to'])}</td>"
            f"<td class=\"src-cell\">{render_corpus_source_links_html(edge.get('source'))}</td></tr>"
        )

    spec_links_section = ""
    spec_nav_item = ""
    if spec_link_rows:
        spec_nav_item = (
            f'<li><a href="#spec-links">Specification cross-references ({len(spec_edges)})</a></li>'
        )
        spec_links_section = f"""
    <section id="spec-links">
      <h2>Links between specifications</h2>
      <p>Nested references found inside downloaded standard texts.</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Referencing</th><th>Referenced</th><th>Source in corpus</th></tr></thead>
          <tbody>{"".join(spec_link_rows)}</tbody>
        </table>
      </div>
    </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>eIDAS technical references report</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <link rel="stylesheet" href="report-layout.css"/>
  <link rel="stylesheet" href="graph-explorer.css"/>
</head>
<body>
  <a class="skip-link" href="#main">Skip to main content</a>
  <div class="site-shell">
  <header class="site-header" role="banner">
    <h1>eIDAS technical references report</h1>
    <p class="site-meta">Generated {esc(generated)} · Toolchain: eidas-legal-tech-references</p>
    <p class="site-meta">For legal traceability and implementer conformance — official EU law cited against normative standards (ETSI, IETF, W3C, …) and EUDI ARF complementary technical specifications (EC TS01–TS11).</p>
  </header>

  <nav class="site-nav" id="site-nav" aria-labelledby="nav-heading">
    <div class="site-nav-bar">
      <span class="site-nav-title" id="nav-heading">Contents</span>
      <button type="button" class="nav-toggle" id="nav-toggle" aria-expanded="false" aria-controls="site-nav-panel">
        <span class="nav-toggle-label">Menu</span>
      </button>
    </div>
    <div class="site-nav-panel" id="site-nav-panel" role="navigation">
      <ul class="site-nav-list">
        <li><a href="#summary">Summary</a></li>
        <li><a href="#graph">Interactive graph</a></li>
        <li><a href="#downloaded">Downloaded references ({len(downloaded)})</a></li>
        <li><a href="#unavailable">Unavailable references ({len(unavailable)})</a></li>
        <li><a href="#legal-links">Legal act → specification links ({len(legal_edges)})</a></li>
        {spec_nav_item}
        <li><a href="search.html">Search corpus</a></li>
      </ul>
    </div>
  </nav>

  <main id="main" class="site-main">
  <section id="summary">
    <h2>Summary</h2>
    <div class="stats">
      <div class="stat"><strong>{len(refs)}</strong><span>Total references</span></div>
      <div class="stat"><strong>{len(downloaded)}</strong><span>Downloaded</span></div>
      <div class="stat"><strong>{len(unavailable)}</strong><span>Unavailable</span></div>
      <div class="stat"><strong>{len(other)}</strong><span>Other status</span></div>
      <div class="stat"><strong>{len(legal_edges)}</strong><span>Legal → spec links</span></div>
      <div class="stat"><strong>{len(spec_edges)}</strong><span>Spec → spec links</span></div>
      <div class="stat"><strong>{len(graph['nodes'])}</strong><span>Graph nodes</span></div>
    </div>
    <h3>By standardization body</h3>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Body</th><th>Total</th><th>Downloaded</th></tr></thead>
        <tbody>{"".join(body_rows)}</tbody>
      </table>
    </div>
  </section>

  <section id="graph">
    <h2>Interactive reference graph</h2>
    <p class="graph-legend">
      <span class="legal">EU legal act</span>
      <span class="ok">Downloaded specification</span>
      Hierarchical view (top → bottom): framework → legal acts → cited standards; ARF EC TS (catalogue node, linked to core wallet acts). Drag a node — it stays where you place it. Pan/zoom the canvas. Filters hide nodes without shifting the view.
    </p>
    <div id="graph-explorer">
      <div class="graph-toolbar">
        <div class="search-row">
          <label class="visually-hidden" for="graph-search">Filter graph</label>
          <input type="search" id="graph-search" placeholder='Filter: +required -excluded · "exact phrase"' autocomplete="off" aria-describedby="graph-status" title="Case-insensitive. Use +word, -word, or &quot;phrase&quot; (same syntax as corpus search)."/>
          <button type="button" class="btn btn-primary" id="graph-search-btn">Apply</button>
          <button type="button" class="btn" id="graph-clear">Reset filters</button>
          <a class="btn" href="search.html">Full corpus search</a>
        </div>
        <div id="sdo-filters" class="sdo-filters" aria-label="Standardization body filters"></div>
        <p id="graph-status"></p>
      </div>
      <div class="graph-layout">
        <div id="graph-network" aria-label="Reference graph visualization"></div>
        <aside id="graph-detail" aria-label="Node details"></aside>
      </div>
    </div>
  </section>

  <section id="downloaded">
    <h2>Downloaded references</h2>
    <p>Specifications with a local copy under <code>referenced-standards/standards/</code>.</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Body</th><th>Designation</th><th>Version</th><th>Summary</th>
            <th>Scope keywords</th><th>Folder</th><th>Download URL</th><th>Tags</th>
          </tr>
        </thead>
        <tbody>{"".join(dl_rows) if dl_rows else '<tr><td colspan="8">None</td></tr>'}</tbody>
      </table>
    </div>
  </section>

  <section id="unavailable">
    <h2>Unavailable references (catalogue)</h2>
    <p>Typically licensed standards (ISO, CEN, …) — metadata and catalogue URLs only.</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Specification</th><th>Version</th><th>Tags</th><th>Catalogue URL</th></tr>
        </thead>
        <tbody>{"".join(unav_rows) if unav_rows else '<tr><td colspan="4">None</td></tr>'}</tbody>
      </table>
    </div>
  </section>

  <section id="legal-links">
    <h2>Links from EU legal acts</h2>
    <p>Normative citations from implementing regulations and decisions to technical specifications.</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Act</th><th>Title</th><th>CELEX</th><th>Kind</th>
            <th>Specification cited</th><th>Source in corpus</th>
          </tr>
        </thead>
        <tbody>{"".join(legal_rows) if legal_rows else '<tr><td colspan="6">None</td></tr>'}</tbody>
      </table>
    </div>
  </section>
{spec_links_section}

  </main>

  <footer class="site-footer" role="contentinfo">
    <p>
      Also available:
      <a href="search.html">Search</a> ·
      <a href="REFERENCES-REPORT.md">REFERENCES-REPORT.md</a> ·
      <a href="references-graph.json">references-graph.json</a> ·
      <a href="graph-data.json">graph-data.json</a> ·
      <a href="search-index.json">search-index.json</a>
    </p>
  </footer>
  </div>

  <script src="report-nav.js"></script>
  <script src="graph-data.js"></script>
  <script src="eidas-search-core.js"></script>
  <script src="document-links.js"></script>
  <script src="graph-explorer.js"></script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--standards-root",
        type=Path,
        default=STANDARDS_DIR,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--downloaded-only-graph",
        action="store_true",
        help="Omit non-downloaded specification nodes from Mermaid graph",
    )
    args = parser.parse_args()

    standards_root = args.standards_root.resolve()
    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    refs = load_references(standards_root)
    if not refs:
        print(
            f"No reference.json under {standards_root}; run: make metadata-specs",
            file=__import__("sys").stderr,
        )
        return 1

    graph = build_graph(refs)
    data = report_data(refs, graph)
    mermaid_src = render_mermaid(graph, downloaded_only=args.downloaded_only_graph)

    html_path = out_dir / "index.html"
    md_path = out_dir / "REFERENCES-REPORT.md"
    json_path = out_dir / "references-graph.json"

    html_path.write_text(render_html(data, mermaid_src), encoding="utf-8")
    md_path.write_text(render_markdown(data, mermaid_src), encoding="utf-8")
    json_path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    graph_json_path, graph_js_path = write_graph_bundle(out_dir, graph)

    search_index = build_search_index(standards_root=standards_root)
    search_index_path, search_index_js = write_search_index(out_dir, search_index)
    asset_names = (
        "report-layout.css",
        "report-nav.js",
        "search.html",
        "search.js",
        "eidas-search-core.js",
        "document-links.js",
        "viewer.html",
        "viewer.js",
        "graph-explorer.js",
        "graph-explorer.css",
    )
    for name in asset_names:
        src = REPORT_ASSETS / name
        if src.is_file():
            shutil.copy2(src, out_dir / name)

    n_dl = len(data["downloaded"])
    print(f"Wrote {html_path}")
    print(f"Wrote {md_path} ({len(refs)} references, {n_dl} downloaded)")
    print(f"Wrote {json_path} ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")
    print(f"Wrote {graph_json_path} and {graph_js_path.name} (interactive graph)")
    print(
        f"Wrote {search_index_path} and {search_index_js.name} "
        f"({search_index['document_count']} searchable chunks)"
    )
    if (out_dir / "search.html").is_file():
        print(f"Wrote {out_dir / 'search.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
