#!/usr/bin/env python3
"""
Build reports of technical references and links (legal acts ↔ specs ↔ specs).

Writes under eidas-legal-tech-references/report/:
  index.html              — summary landing only (links to other pages)
  graph.html              — interactive hierarchical graph (opt-in load)
  search.html             — full-text search UI
  downloaded.html         — downloaded references (paged catalogue)
  unavailable.html        — unavailable references (paged catalogue)
  legal-links.html        — legal act → specification links (paged)
  spec-links.html         — specification cross-references (paged)
  data/<catalogue>/       — paged row payloads (page-NNNN.js)
  graph-data.json/js      — graph nodes/edges (loaded only from graph.html)
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

CORPUS_ANCHOR_SECTION = "regulation"
CORPUS_ANCHOR_ACT_ID = "eidas-consolidated"

ARF_TS_INDEX_URL = (
    "https://github.com/eu-digital-identity-wallet/"
    "eudi-doc-architecture-and-reference-framework/tree/v3.0.0/docs/technical-specifications"
)


def esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def preferred_online_url(doc: dict[str, Any] | None) -> str | None:
    """Best public HTTPS link for a specification (prefer human pages over raw)."""
    if not doc:
        return None
    candidates: list[str] = []
    for key in ("download_url",):
        u = doc.get(key)
        if isinstance(u, str) and u.startswith("http"):
            candidates.append(u)
    for u in doc.get("download_urls") or []:
        if isinstance(u, str) and u.startswith("http") and u not in candidates:
            candidates.append(u)
    if not candidates:
        return None
    for u in candidates:
        if "raw.githubusercontent.com" not in u:
            return u
    return candidates[0]


def render_online_href_html(url: str | None, label: str | None = None) -> str:
    if not url:
        return "—"
    text = label or url
    return f'<a href="{esc(url)}" rel="noopener" target="_blank">{esc(text)}</a>'


def render_online_href_md(url: str | None, label: str | None = None) -> str:
    if not url:
        return "—"
    return f"[{label or 'link'}]({url})"


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


def render_corpus_source_links_html(
    source: str | None,
    *,
    online_url: str | None = None,
) -> str:
    if not source and not online_url:
        return "—"
    parts: list[str] = []
    if online_url:
        parts.append(
            f'<a class="src-online" href="{esc(online_url)}" rel="noopener" target="_blank">'
            f"Online</a>"
        )
    if not source:
        return " · ".join(parts) if parts else "—"
    stem = source_stem(source)
    if not stem:
        parts.append(f"<code>{esc(source)}</code>")
        return " · ".join(parts)
    formats = _corpus_format_paths(stem)
    parts.append(f'<code class="src-path">{esc(source)}</code>')
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
        parts.append(" · ".join(link_bits))
    return "<br/>".join(parts) if len(parts) > 1 else (parts[0] if parts else "—")


def render_corpus_source_links_md(
    source: str | None,
    *,
    online_url: str | None = None,
) -> str:
    bits: list[str] = []
    if online_url:
        bits.append(f"[online]({online_url})")
    if not source:
        return " · ".join(bits) if bits else "—"
    stem = source_stem(source)
    if not stem:
        bits.append(f"`{source}`")
        return " · ".join(bits)
    formats = _corpus_format_paths(stem)
    local_bits: list[str] = []
    for ext in ("md", "html", "pdf"):
        path = formats.get(ext)
        if path:
            local_bits.append(f"[{ext}]({report_rel_href(path)})")
        else:
            local_bits.append(f"{ext}:—")
    bits.append(f"`{source}` — " + ", ".join(local_bits))
    return " · ".join(bits)


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


def _version_key(version: str | None) -> tuple[int, ...]:
    if not version:
        return (0,)
    nums = [int(x) for x in re.findall(r"\d+", str(version))]
    return tuple(nums) if nums else (0,)


_ARF_TS_IDENTITY_RE = re.compile(
    r"^TS\s*0*(?P<num>\d{1,2})(?:\s+V?(?P<ver>[\d.]+))?$",
    re.IGNORECASE,
)


def _normalize_spec_identity(
    body: str | None, designation: str | None, version: str | None
) -> tuple[str | None, str | None, str | None]:
    """Normalize ARF parents like designation 'TS03 V1.5.1' → TS03 + version 1.5.1."""
    if not body or not designation:
        return body, designation, version
    if str(body) != "ARF":
        return body, designation, version
    m = _ARF_TS_IDENTITY_RE.match(str(designation).strip())
    if not m:
        return body, designation, version
    des = f"TS{int(m.group('num')):02d}"
    ver = m.group("ver") or version
    return body, des, ver


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
    for key in ("title", "celex", "eli", "kind", "files", "consolidated_as_of"):
        if meta.get(key) is not None and not node.get(key):
            node[key] = meta[key]
    if meta.get("summary"):
        node["summary"] = meta["summary"]
    if meta.get("scope_keywords"):
        node["scope_keywords"] = meta["scope_keywords"]
    if meta.get("summary_meta"):
        node["summary_meta"] = meta["summary_meta"]


def ensure_corpus_anchor(nodes: dict[str, dict[str, Any]]) -> str:
    """Ensure consolidated eIDAS is in the graph (root node detail target)."""
    meta = load_legal_act_metadata(CORPUS_ANCHOR_SECTION, CORPUS_ANCHOR_ACT_ID)
    lid = legal_node_id({"id": CORPUS_ANCHOR_ACT_ID})
    if lid not in nodes:
        nodes[lid] = {
            "id": lid,
            "type": "legal_regulation",
            "act_id": CORPUS_ANCHOR_ACT_ID,
            "section": CORPUS_ANCHOR_SECTION,
            "title": meta.get("title"),
            "celex": meta.get("celex"),
            "eli": meta.get("eli"),
            "kind": meta.get("kind"),
            "files": meta.get("files") or {},
            "scope_keywords": meta.get("scope_keywords") or [],
            "summary": meta.get("summary"),
            "summary_meta": meta.get("summary_meta"),
        }
        if meta.get("consolidated_as_of"):
            nodes[lid]["consolidated_as_of"] = meta["consolidated_as_of"]
    else:
        _apply_legal_metadata(nodes[lid])
    nodes[lid]["search_text"] = _legal_search_text(nodes[lid])
    return lid


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

    by_exact_id = {spec_node_id(doc): doc for doc in refs}
    # Prefer on-disk specs when parent_specifications still cite pruned versions
    # (e.g. ARF TS01 V1.1.2 after V1.2 replaced it).
    latest_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for doc in refs:
        body = doc.get("body")
        des = doc.get("designation")
        if not body or not des:
            continue
        key = (str(body), str(des).strip().upper())
        prev = latest_by_identity.get(key)
        if prev is None or _version_key(doc.get("version")) >= _version_key(prev.get("version")):
            latest_by_identity[key] = doc

    def resolve_parent_spec(sp: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        body, des, ver = _normalize_spec_identity(
            sp.get("body"), sp.get("designation"), sp.get("version")
        )
        # Also recover identity from pruned folder paths: ARF/TS03-V1.5.1
        folder = str(sp.get("folder") or "")
        source = str(sp.get("source") or "")
        fm = re.search(
            r"(?:^|/)(TS\d{1,2})-V([\d.]+)(?:/|$)", folder or source, re.I
        )
        if (body == "ARF" or "ARF/" in (folder or source)) and fm:
            body, des, ver = _normalize_spec_identity(
                "ARF", fm.group(1), ver or fm.group(2)
            )
        # Drop known superseded ARF TS versions — always use the on-disk latest.
        if body == "ARF" and des:
            latest = latest_by_identity.get((str(body), str(des).strip().upper()))
            if latest is not None:
                return spec_node_id(latest), latest
        exact_id = spec_node_id(
            {"body": body, "designation": des, "version": ver}
        )
        if exact_id in by_exact_id:
            return exact_id, by_exact_id[exact_id]
        return exact_id, None

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
            "download_url": preferred_online_url(doc) or doc.get("download_url"),
            "download_urls": doc.get("download_urls") or [],
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
            pid, parent_doc = resolve_parent_spec(sp)
            if parent_doc is None:
                # Skip ghosts for pruned/missing parents (deprecated ARF TS versions).
                continue
            if pid not in nodes:
                nodes[pid] = {
                    "id": pid,
                    "type": "specification",
                    "body": parent_doc.get("body"),
                    "designation": parent_doc.get("designation"),
                    "version": parent_doc.get("version"),
                    "title": parent_doc.get("title"),
                    "status": parent_doc.get("status"),
                    "folder": parent_doc.get("_folder"),
                }
            edges.append(
                {
                    "from": pid,
                    "to": sid,
                    "kind": "references",
                    "source": sp.get("source"),
                }
            )

    by_spec_id = by_exact_id
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
        for act_id in ("eidas-consolidated", "2024-2979", "2024-2977", "2024-2982"):
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

    corpus_anchor_id = ensure_corpus_anchor(nodes)

    node_list = list(nodes.values())
    for n in node_list:
        if n.get("type") == "legal_regulation":
            _apply_legal_metadata(n)
            if "search_text" not in n or n.get("summary"):
                n["search_text"] = _legal_search_text(n)

    # Deduplicate identical (from, to, kind) edges — duplicate IDs blank vis-network.
    seen_edges: set[tuple[str, str, str]] = set()
    unique_edges: list[dict[str, Any]] = []
    for e in edges:
        key = (str(e.get("from")), str(e.get("to")), str(e.get("kind")))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        unique_edges.append(e)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_anchor_id": corpus_anchor_id,
        "nodes": node_list,
        "edges": unique_edges,
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
    by_spec_id = {spec_node_id(d): d for d in refs}

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
        "| Specification | Version | Summary | Scope keywords | Folder | Online |",
        "|---------------|---------|---------|----------------|--------|--------|",
        ]
    )
    for doc in sorted(downloaded, key=lambda d: (d.get("body", ""), d.get("designation", ""))):
        url = preferred_online_url(doc)
        url_md = render_online_href_md(url)
        sm = (doc.get("summary") or "")[:120].replace("|", "/")
        kw = ", ".join((doc.get("scope_keywords") or [])[:5])
        lines.append(
            f"| {doc.get('designation', '?')} | {doc.get('version') or '—'} | "
            f"{sm or '—'} | {kw or '—'} | `{doc.get('_folder', '')}` | {url_md} |"
        )

    if unavailable:
        lines.extend(
            [
                "",
                "## Unavailable references",
                "",
                "| Specification | Version | Tags | Online |",
                "|---------------|---------|------|--------|",
            ]
        )
        for doc in sorted(unavailable, key=lambda d: spec_label(d)):
            tags = ", ".join(doc.get("tags") or [])[:80]
            url = preferred_online_url(doc)
            lines.append(
                f"| {spec_label(doc)} | {doc.get('version') or '—'} | {tags} | "
                f"{render_online_href_md(url)} |"
            )

    lines.extend(
        [
            "",
            "## Links from EU legal acts",
            "",
            "| Legal act | CELEX | Specification cited | Online | Source in corpus |",
            "|-----------|-------|---------------------|--------|------------------|",
        ]
    )
    seen: set[tuple[str, str]] = set()
    for edge in sorted(legal_edges, key=lambda e: (e["from"], e["to"])):
        key = (edge["from"], edge["to"])
        if key in seen:
            continue
        seen.add(key)
        ln = legal_nodes.get(edge["from"], {})
        online = preferred_online_url(by_spec_id.get(edge["to"]))
        lines.append(
            f"| {ln.get('act_id', edge['from'])} | {ln.get('celex') or '—'} | {edge['to']} | "
            f"{render_online_href_md(online)} | "
            f"{render_corpus_source_links_md(edge.get('source'), online_url=online)} |"
        )

    if spec_edges:
        lines.extend(
            [
                "",
                "## Links between specifications",
                "",
                "| From | To | Online (to) | Source in corpus |",
                "|------|-----|-------------|------------------|",
            ]
        )
        seen_spec: set[tuple[str, str]] = set()
        for edge in sorted(spec_edges, key=lambda e: (e["from"], e["to"])):
            key = (edge["from"], edge["to"])
            if key in seen_spec:
                continue
            seen_spec.add(key)
            online = preferred_online_url(by_spec_id.get(edge["to"]))
            lines.append(
                f"| {edge['from']} | {edge['to']} | {render_online_href_md(online)} | "
                f"{render_corpus_source_links_md(edge.get('source'), online_url=online)} |"
            )

    lines.extend(["", "## Reference graph (Mermaid)", "", "```mermaid", mermaid_src, "```", ""])
    return "\n".join(lines) + "\n"


def _nav_item(href: str, label: str, *, current: str | None, page_id: str) -> str:
    attrs = f' href="{esc(href)}"'
    if current == page_id:
        attrs += ' aria-current="page"'
    return f"<li><a{attrs}>{esc(label)}</a></li>"


def render_site_nav(*, current: str, counts: dict[str, int] | None = None) -> str:
    """Shared report navigation across separate HTML pages."""
    counts = counts or {}
    n_dl = counts.get("downloaded")
    n_unav = counts.get("unavailable")
    n_legal = counts.get("legal_edges")
    n_spec = counts.get("spec_edges")

    def label(base: str, n: int | None) -> str:
        return f"{base} ({n})" if n is not None else base

    primary = [
        _nav_item("index.html", "Summary", current=current, page_id="index"),
        _nav_item("graph.html", "Interactive graph", current=current, page_id="graph"),
        _nav_item("search.html", "Search corpus", current=current, page_id="search"),
    ]
    catalogues = [
        _nav_item(
            "downloaded.html",
            label("Downloaded references", n_dl),
            current=current,
            page_id="downloaded",
        ),
        _nav_item(
            "unavailable.html",
            label("Unavailable references", n_unav),
            current=current,
            page_id="unavailable",
        ),
        _nav_item(
            "legal-links.html",
            label("Legal act → specification links", n_legal),
            current=current,
            page_id="legal-links",
        ),
        _nav_item(
            "spec-links.html",
            label("Specification cross-references", n_spec),
            current=current,
            page_id="spec-links",
        ),
    ]
    # Landing: only the three primary entry points; catalogues are linked from Summary.
    items = primary if current == "index" else primary + catalogues

    return f"""  <nav class="site-nav" id="site-nav" aria-labelledby="nav-heading">
    <div class="site-nav-bar">
      <span class="site-nav-title" id="nav-heading">Contents</span>
      <button type="button" class="nav-toggle" id="nav-toggle" aria-expanded="false" aria-controls="site-nav-panel">
        <span class="nav-toggle-label">Menu</span>
      </button>
    </div>
    <div class="site-nav-panel" id="site-nav-panel" role="navigation">
      <ul class="site-nav-list">
        {"".join(items)}
      </ul>
    </div>
  </nav>"""


def render_site_footer() -> str:
    return """  <footer class="site-footer" role="contentinfo">
    <p>
      Pages:
      <a href="index.html">Summary</a> ·
      <a href="graph.html">Graph</a> ·
      <a href="search.html">Search</a> ·
      <a href="downloaded.html">Downloaded</a> ·
      <a href="unavailable.html">Unavailable</a> ·
      <a href="legal-links.html">Legal → spec</a> ·
      <a href="spec-links.html">Spec cross-refs</a>
    </p>
    <p>
      Data:
      <a href="REFERENCES-REPORT.md">REFERENCES-REPORT.md</a> ·
      <a href="references-graph.json">references-graph.json</a> ·
      <a href="graph-data.json">graph-data.json</a> ·
      <a href="search-index.json">search-index.json</a>
    </p>
  </footer>"""



def render_html_document(
    *,
    title: str,
    generated: str,
    nav: str,
    main: str,
    extra_head: str = "",
    body_class: str = "",
    scripts: str = "",
    heading: str | None = None,
    meta: str | None = None,
) -> str:
    body_attr = f' class="{esc(body_class)}"' if body_class else ""
    h1 = heading or title
    meta_html = meta or (
        "For legal traceability and implementer conformance — official EU law cited "
        "against normative standards (ETSI, IETF, W3C, …) and EUDI ARF complementary "
        "technical specifications (EC TS01–TS14)."
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{esc(title)}</title>
  <link rel="stylesheet" href="report-layout.css"/>
  {extra_head}
</head>
<body{body_attr}>
  <a class="skip-link" href="#main">Skip to main content</a>
  <div class="site-shell">
  <header class="site-header" role="banner">
    <h1>{esc(h1)}</h1>
    <p class="site-meta">Generated {esc(generated)} · Toolchain: eidas-legal-tech-references</p>
    <p class="site-meta">{esc(meta_html)}</p>
  </header>

{nav}

  <main id="main" class="site-main">
{main}
  </main>

{render_site_footer()}
  </div>

  <script src="report-nav.js"></script>
{scripts}
</body>
</html>
"""


CATALOGUE_PAGE_SIZE = 200


def write_catalogue_pages(
    out_dir: Path,
    catalogue_id: str,
    columns: list[str],
    rows: list[list[str]],
    *,
    page_size: int = CATALOGUE_PAGE_SIZE,
) -> list[Path]:
    """Write paged catalogue payloads (one JS file per page). HTML shells stay tiny."""
    data_dir = out_dir / "data" / catalogue_id
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    total = len(rows)
    page_count = max(1, (total + page_size - 1) // page_size) if total else 1
    for page in range(page_count):
        start = page * page_size
        chunk = rows[start : start + page_size]
        payload = {
            "id": catalogue_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "columns": columns,
            "rows": chunk,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        js_path = data_dir / f"page-{page:04d}.js"
        js_path.write_text(f"window.EIDAS_CATALOGUE_PAGE={body};\n", encoding="utf-8")
        written.append(js_path)

    manifest = {
        "id": catalogue_id,
        "columns": columns,
        "total": total,
        "page_size": page_size,
        "page_count": page_count,
    }
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written.append(manifest_path)
    return written


def catalogue_shell(
    *,
    section_id: str,
    heading: str,
    intro_html: str,
    catalogue_id: str,
    columns: list[str],
    total: int,
    page_size: int = CATALOGUE_PAGE_SIZE,
) -> str:
    # Single-quoted attribute so JSON double-quotes need no &quot; escaping.
    cols_json = json.dumps(columns, ensure_ascii=False).replace("'", "&#39;")
    return f"""  <section id="{esc(section_id)}">
    <h2>{esc(heading)}</h2>
    <p>{intro_html} <a href="index.html">← Back to summary</a></p>
    <div id="catalogue"
         data-catalogue="{esc(catalogue_id)}"
         data-page-size="{page_size}"
         data-total="{total}"
         data-columns='{cols_json}'>
      <p id="catalogue-status" role="status" aria-live="polite">Loading…</p>
      <div class="table-wrap">
        <table>
          <thead id="catalogue-head"></thead>
          <tbody id="catalogue-body">
            <tr><td>Loading…</td></tr>
          </tbody>
        </table>
      </div>
      <nav id="catalogue-pager" class="catalogue-pager" aria-label="Catalogue pages"></nav>
    </div>
  </section>"""



def write_html_report(out_dir: Path, data: dict[str, Any], mermaid_src: str) -> list[Path]:
    """Write landing page + thin catalogue shells + paged row payloads."""
    del mermaid_src  # reserved for markdown export only
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
    by_spec_id = {spec_node_id(d): d for d in refs}
    counts = {
        "downloaded": len(downloaded),
        "unavailable": len(unavailable),
        "legal_edges": len(legal_edges),
        "spec_edges": len(spec_edges),
    }

    body_rows = []
    for body in sorted(by_body):
        items = by_body[body]
        n_dl = sum(1 for r in items if r.get("status") in DOWNLOADED_STATUSES)
        body_rows.append(
            f"<tr><td>{esc(body)}</td><td>{len(items)}</td><td>{n_dl}</td></tr>"
        )

    dl_rows: list[list[str]] = []
    for doc in sorted(downloaded, key=lambda d: (d.get("body", ""), d.get("designation", ""))):
        url = preferred_online_url(doc)
        tags = ", ".join(doc.get("tags") or [])
        summary = doc.get("summary") or ""
        if len(summary) > 160:
            summary = summary[:157].rsplit(" ", 1)[0] + "…"
        kw = ", ".join((doc.get("scope_keywords") or [])[:6])
        dl_rows.append(
            [
                esc(doc.get("body")),
                esc(doc.get("designation")),
                esc(doc.get("version")),
                f'<span class="summary">{esc(summary) or "—"}</span>',
                f'<span class="tags">{esc(kw) or "—"}</span>',
                f"<code>{esc(doc.get('_folder'))}</code>",
                render_online_href_html(url),
                f'<span class="tags">{esc(tags)}</span>',
            ]
        )

    unav_rows: list[list[str]] = []
    for doc in sorted(unavailable, key=lambda d: spec_label(d)):
        url = preferred_online_url(doc)
        unav_rows.append(
            [
                esc(spec_label(doc)),
                esc(doc.get("version")),
                f'<span class="tags">{esc(", ".join(doc.get("tags") or []))}</span>',
                render_online_href_html(url),
            ]
        )

    legal_rows: list[list[str]] = []
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
            act_cell = f'<a href="{esc(eli)}" rel="noopener" target="_blank">{act_cell}</a>'
        online = preferred_online_url(by_spec_id.get(edge["to"]))
        legal_rows.append(
            [
                act_cell,
                esc(ln.get("title")),
                esc(ln.get("celex")),
                esc(ln.get("kind")),
                esc(edge["to"]),
                render_online_href_html(online, "Online"),
                f'<span class="src-cell">{render_corpus_source_links_html(edge.get("source"), online_url=online)}</span>',
            ]
        )

    spec_link_rows: list[list[str]] = []
    seen_spec: set[tuple[str, str]] = set()
    for edge in sorted(spec_edges, key=lambda e: (e["from"], e["to"])):
        key = (edge["from"], edge["to"])
        if key in seen_spec:
            continue
        seen_spec.add(key)
        online = preferred_online_url(by_spec_id.get(edge["to"]))
        spec_link_rows.append(
            [
                esc(edge["from"]),
                esc(edge["to"]),
                render_online_href_html(online, "Online"),
                f'<span class="src-cell">{render_corpus_source_links_html(edge.get("source"), online_url=online)}</span>',
            ]
        )

    written_data: list[Path] = []
    written_data.extend(
        write_catalogue_pages(
            out_dir,
            "downloaded",
            [
                "Body",
                "Designation",
                "Version",
                "Summary",
                "Scope keywords",
                "Folder",
                "Online",
                "Tags",
            ],
            dl_rows,
        )
    )
    written_data.extend(
        write_catalogue_pages(
            out_dir,
            "unavailable",
            ["Specification", "Version", "Tags", "Online"],
            unav_rows,
        )
    )
    written_data.extend(
        write_catalogue_pages(
            out_dir,
            "legal-links",
            [
                "Act",
                "Title",
                "CELEX",
                "Kind",
                "Specification cited",
                "Online",
                "Source in corpus",
            ],
            legal_rows,
        )
    )
    written_data.extend(
        write_catalogue_pages(
            out_dir,
            "spec-links",
            ["Referencing", "Referenced", "Online", "Source in corpus"],
            spec_link_rows,
        )
    )

    index_main = f"""  <section id="summary">
    <h2>Summary</h2>
    <p>This landing page is only the summary. Graph, search, and every catalogue table live on <strong>separate HTML pages</strong> (opened via the links below).</p>
    <div class="stats">
      <div class="stat"><strong>{len(refs)}</strong><span>Total references</span></div>
      <a class="stat" href="downloaded.html"><strong>{len(downloaded)}</strong><span>Downloaded</span></a>
      <a class="stat" href="unavailable.html"><strong>{len(unavailable)}</strong><span>Unavailable</span></a>
      <div class="stat"><strong>{len(other)}</strong><span>Other status</span></div>
      <a class="stat" href="legal-links.html"><strong>{len(legal_edges)}</strong><span>Legal → spec links</span></a>
      <a class="stat" href="spec-links.html"><strong>{len(spec_edges)}</strong><span>Spec → spec links</span></a>
      <a class="stat" href="graph.html"><strong>{len(graph['nodes'])}</strong><span>Graph nodes</span></a>
    </div>

    <h3>Primary pages</h3>
    <ul class="page-links">
      <li><a href="graph.html"><strong>Interactive graph</strong></a> — hierarchical legal → specification view (loads large data only on that page)</li>
      <li><a href="search.html"><strong>Search corpus</strong></a> — full-text search over legal acts and specifications</li>
    </ul>

    <h3>Catalogue pages</h3>
    <ul class="page-links">
      <li><a href="downloaded.html">Downloaded references ({len(downloaded)})</a></li>
      <li><a href="unavailable.html">Unavailable references ({len(unavailable)})</a></li>
      <li><a href="legal-links.html">Legal act → specification links ({len(legal_edges)})</a></li>
      <li><a href="spec-links.html">Specification cross-references ({len(spec_edges)})</a></li>
    </ul>

    <h3>By standardization body</h3>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Body</th><th>Total</th><th>Downloaded</th></tr></thead>
        <tbody>{"".join(body_rows)}</tbody>
      </table>
    </div>
  </section>"""

    graph_main = """  <section id="graph">
    <h2>Interactive reference graph</h2>
    <p class="graph-legend">
      <span class="legal">EU legal act</span>
      <span class="ok">Downloaded specification</span>
      Hierarchical view (top → bottom): framework → legal acts → cited standards; ARF EC TS (catalogue node, linked to core wallet acts).
      <a href="index.html">← Back to summary</a>
    </p>
    <div id="graph-gate" class="graph-gate">
      <p>Graph data is large (~tens of MB). Click to load it on this page only (not on the summary landing page).</p>
      <button type="button" class="btn btn-primary" id="graph-load-btn">Load interactive graph</button>
      <p id="graph-gate-status" class="hint" role="status"></p>
    </div>
    <div id="graph-explorer" hidden>
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
  </section>"""

    catalogue_script = '  <script src="catalogue-viewer.js"></script>'
    graph_scripts = (
        '  <script src="eidas-search-core.js"></script>\n'
        '  <script src="document-links.js"></script>\n'
        '  <script src="graph-explorer.js"></script>'
    )
    pages = [
        (
            "index.html",
            render_html_document(
                title="eIDAS technical references report",
                generated=generated,
                nav=render_site_nav(current="index", counts=counts),
                main=index_main,
            ),
        ),
        (
            "graph.html",
            render_html_document(
                title="Interactive graph — eIDAS report",
                heading="Interactive reference graph",
                generated=generated,
                nav=render_site_nav(current="graph", counts=counts),
                main=graph_main,
                extra_head='  <link rel="stylesheet" href="graph-explorer.css"/>',
                scripts=graph_scripts,
                meta="Hierarchical graph of EU legal acts and cited technical specifications.",
            ),
        ),
        (
            "downloaded.html",
            render_html_document(
                title="Downloaded references — eIDAS report",
                heading="Downloaded references",
                generated=generated,
                nav=render_site_nav(current="downloaded", counts=counts),
                main=catalogue_shell(
                    section_id="downloaded",
                    heading="Downloaded references",
                    intro_html=(
                        "Specifications with a local copy under "
                        "<code>referenced-standards/standards/</code>."
                    ),
                    catalogue_id="downloaded",
                    columns=[
                        "Body",
                        "Designation",
                        "Version",
                        "Summary",
                        "Scope keywords",
                        "Folder",
                        "Online",
                        "Tags",
                    ],
                    total=len(dl_rows),
                ),
                meta="Catalogue of specifications with a local copy in this corpus.",
                scripts=catalogue_script,
            ),
        ),
        (
            "unavailable.html",
            render_html_document(
                title="Unavailable references — eIDAS report",
                heading="Unavailable references",
                generated=generated,
                nav=render_site_nav(current="unavailable", counts=counts),
                main=catalogue_shell(
                    section_id="unavailable",
                    heading="Unavailable references (catalogue)",
                    intro_html=(
                        "Typically licensed standards (ISO, CEN, …) — metadata and catalogue URLs only."
                    ),
                    catalogue_id="unavailable",
                    columns=["Specification", "Version", "Tags", "Online"],
                    total=len(unav_rows),
                ),
                meta="Licensed or otherwise undownloaded standards — catalogue URLs only.",
                scripts=catalogue_script,
            ),
        ),
        (
            "legal-links.html",
            render_html_document(
                title="Legal act → specification links — eIDAS report",
                heading="Legal act → specification links",
                generated=generated,
                nav=render_site_nav(current="legal-links", counts=counts),
                main=catalogue_shell(
                    section_id="legal-links",
                    heading="Links from EU legal acts",
                    intro_html=(
                        "Normative citations from implementing regulations and decisions "
                        "to technical specifications."
                    ),
                    catalogue_id="legal-links",
                    columns=[
                        "Act",
                        "Title",
                        "CELEX",
                        "Kind",
                        "Specification cited",
                        "Online",
                        "Source in corpus",
                    ],
                    total=len(legal_rows),
                ),
                meta="Normative citations from EU legal acts to technical specifications.",
                scripts=catalogue_script,
            ),
        ),
        (
            "spec-links.html",
            render_html_document(
                title="Specification cross-references — eIDAS report",
                heading="Specification cross-references",
                generated=generated,
                nav=render_site_nav(current="spec-links", counts=counts),
                main=catalogue_shell(
                    section_id="spec-links",
                    heading="Specification cross-references",
                    intro_html=(
                        "Nested references found inside downloaded standard texts. "
                        "<strong>Online</strong> points to the public catalogue / SDO copy. "
                        "Rows load 200 at a time."
                    ),
                    catalogue_id="spec-links",
                    columns=["Referencing", "Referenced", "Online", "Source in corpus"],
                    total=len(spec_link_rows),
                ),
                meta="Nested references found inside downloaded standard texts.",
                scripts=catalogue_script,
            ),
        ),
    ]

    written: list[Path] = []
    for name, content in pages:
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    written.extend(written_data)
    return written


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

    html_paths = write_html_report(out_dir, data, mermaid_src)
    md_path = out_dir / "REFERENCES-REPORT.md"
    json_path = out_dir / "references-graph.json"

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
        "catalogue-viewer.js",
    )
    for name in asset_names:
        src = REPORT_ASSETS / name
        if src.is_file():
            shutil.copy2(src, out_dir / name)

    n_dl = len(data["downloaded"])
    html_only = [p for p in html_paths if p.suffix == ".html"]
    for path in html_only:
        print(f"Wrote {path}")
    data_pages = [p for p in html_paths if p.parent.name in {"downloaded", "unavailable", "legal-links", "spec-links"} and p.suffix == ".js"]
    print(f"Wrote {len(data_pages)} catalogue page payload(s) under {out_dir / 'data'}")
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
