"""Build search-index.json for report/search.html (legal markdown + specifications)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tag_normalize import ALLOWED_TAGS, legal_act_tags, normalize_tags

CORPUS_ROOT = Path(__file__).resolve().parents[1]
REF_ROOT = CORPUS_ROOT / "referenced-standards"
STANDARDS_DIR = REF_ROOT / "standards"
LEGAL_DIRS = (
    CORPUS_ROOT / "regulation",
    CORPUS_ROOT / "implementing-acts",
    CORPUS_ROOT / "implementing-decisions",
)

CHUNK_SIZE = 1800
MAX_SPEC_FILE_BYTES = 400_000
SPEC_FILE_SUFFIXES = (".md", ".txt", ".html")

# Canonical SDO folders (order used in search UI)
SDO_BODIES = ("ARF", "ETSI", "IETF", "W3C", "CEN", "ISO-IEC", "ITU-T", "IEEE")

_FRONT_MATTER = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _rel(path: Path) -> str:
    return path.relative_to(CORPUS_ROOT).as_posix()


def _rel_from_report(path: Path) -> str:
    return "../" + _rel(path)


def _strip_front_matter(text: str) -> str:
    return _FRONT_MATTER.sub("", text, count=1).strip()


def _chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            break_at = text.rfind("\n\n", start, end)
            if break_at > start + size // 3:
                end = break_at + 2
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def _load_legal_documents() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for base in LEGAL_DIRS:
        if not base.is_dir():
            continue
        for act_dir in sorted(base.iterdir()):
            if not act_dir.is_dir():
                continue
            act_id = act_dir.name
            md_path = act_dir / f"{act_id}.md"
            if not md_path.is_file():
                continue
            meta_path = act_dir / "metadata.json"
            meta: dict[str, Any] = {}
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            try:
                raw = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            body_text = _strip_front_matter(raw)
            kind = meta.get("kind") or "legal"
            tags = legal_act_tags(kind, base.name)
            title = meta.get("title") or act_id
            label = f"{act_id} — {title}"
            links = {
                "markdown": _rel_from_report(md_path),
                "metadata": _rel_from_report(meta_path) if meta_path.is_file() else None,
                "eli": meta.get("eli"),
                "html": _rel_from_report(act_dir / f"{act_id}.html")
                if (act_dir / f"{act_id}.html").is_file()
                else None,
                "pdf": _rel_from_report(act_dir / f"{act_id}.pdf")
                if (act_dir / f"{act_id}.pdf").is_file()
                else None,
            }
            meta_out = {
                k: meta.get(k)
                for k in ("id", "title", "celex", "eli", "kind", "consolidated_as_of")
                if meta.get(k) is not None
            }
            meta_out["section"] = base.name
            legal_files: dict[str, Any] = {}
            for ext in ("html", "pdf"):
                p = act_dir / f"{act_id}.{ext}"
                if p.is_file():
                    legal_files[ext] = {"path": p.relative_to(CORPUS_ROOT).as_posix()}
            if legal_files:
                meta_out["files"] = legal_files
            ref_key = f"legal:{act_id}"
            for i, chunk in enumerate(_chunk_text(body_text)):
                docs.append(
                    {
                        "id": f"legal:{act_id}:{i}",
                        "reference_key": ref_key,
                        "kind": "legal",
                        "body": None,
                        "tags": tags,
                        "title": label,
                        "text": chunk,
                        "chunk": i,
                        "links": {k: v for k, v in links.items() if v},
                        "metadata": meta_out,
                    }
                )
    return docs


def _spec_label(doc: dict[str, Any]) -> str:
    parts = [doc.get("body", ""), doc.get("designation", "")]
    if doc.get("version"):
        parts.append(f"V{doc['version']}")
    return " ".join(p for p in parts if p).strip()


def _spec_document_text(folder: Path) -> tuple[str | None, str | None]:
    for path in sorted(folder.iterdir()):
        if path.name == "reference.json" or path.suffix.lower() not in SPEC_FILE_SUFFIXES:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_SPEC_FILE_BYTES:
            text = path.read_text(encoding="utf-8", errors="replace")[:MAX_SPEC_FILE_BYTES]
            return text, _rel_from_report(path)
        return path.read_text(encoding="utf-8", errors="replace"), _rel_from_report(path)
    return None, None


def _metadata_blob(doc: dict[str, Any]) -> str:
    parts = [
        _spec_label(doc),
        doc.get("title") or "",
        doc.get("purpose") or "",
        doc.get("summary") or "",
        " ".join(doc.get("scope_keywords") or []),
        doc.get("status") or "",
        doc.get("reason") or "",
        doc.get("version") or "",
        doc.get("released_at") or "",
        " ".join(doc.get("tags") or []),
    ]
    for p in doc.get("parent_legal_regulations") or []:
        parts.extend(
            [
                p.get("id", ""),
                p.get("title", ""),
                p.get("celex", ""),
            ]
        )
    for p in doc.get("parent_specifications") or []:
        parts.append(f"{p.get('body')} {p.get('designation')}")
    return " ".join(x for x in parts if x)


def _load_spec_documents(standards_root: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for ref_path in sorted(standards_root.rglob("reference.json")):
        try:
            doc = json.loads(ref_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        folder = ref_path.parent
        folder_rel = folder.relative_to(standards_root).as_posix()
        body = doc.get("body")
        tags = normalize_tags(doc.get("tags") or [])
        label = _spec_label(doc)
        links: dict[str, Any] = {
            "reference_json": _rel_from_report(ref_path),
            "download": doc.get("download_url"),
            "folder": _rel_from_report(folder),
        }
        meta_out = {
            k: doc.get(k)
            for k in (
                "body",
                "designation",
                "version",
                "title",
                "purpose",
                "summary",
                "scope_keywords",
                "status",
                "released_at",
                "reason",
                "files",
                "parent_legal_regulations",
                "parent_specifications",
            )
            if doc.get(k) is not None
        }
        meta_text = _metadata_blob(doc)
        ref_key = f"spec:{folder_rel}"
        docs.append(
            {
                "id": f"spec-meta:{folder_rel}",
                "reference_key": ref_key,
                "kind": "specification",
                "body": body,
                "tags": tags,
                "title": label,
                "text": meta_text,
                "chunk": None,
                "links": {k: v for k, v in links.items() if v},
                "metadata": meta_out,
            }
        )
        doc_text, doc_link = _spec_document_text(folder)
        if doc_link:
            links["document"] = doc_link
        if doc_text:
            for i, chunk in enumerate(_chunk_text(doc_text)):
                docs.append(
                    {
                        "id": f"spec-doc:{folder_rel}:{i}",
                        "reference_key": ref_key,
                        "kind": "specification_document",
                        "body": body,
                        "tags": tags + ["document-text"],
                        "title": f"{label} (document §{i + 1})",
                        "text": chunk,
                        "chunk": i,
                        "links": {k: v for k, v in links.items() if v},
                        "metadata": meta_out,
                    }
                )
    return docs


def build_search_index(
    *,
    standards_root: Path = STANDARDS_DIR,
) -> dict[str, Any]:
    legal = _load_legal_documents()
    specs = _load_spec_documents(standards_root)
    documents = legal + specs
    found_bodies = {d["body"] for d in documents if d.get("body")}
    sdo_bodies = [b for b in SDO_BODIES if b in found_bodies]
    for b in sorted(found_bodies):
        if b not in sdo_bodies:
            sdo_bodies.append(b)

    body_counts: dict[str, int] = {b: 0 for b in sdo_bodies}
    for d in documents:
        if d.get("kind") not in ("specification", "specification_document"):
            continue
        b = d.get("body")
        if b in body_counts:
            body_counts[b] += 1

    tag_set: set[str] = set()
    for d in documents:
        tag_set.update(d.get("tags") or [])
    suggested_tags = sorted(t for t in tag_set if t in ALLOWED_TAGS)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_root": "..",
        "document_count": len(documents),
        "facets": {
            "bodies": sdo_bodies,
            "body_counts": body_counts,
            "tags": suggested_tags,
        },
        "documents": documents,
    }


def write_search_index(out_dir: Path, index: dict[str, Any]) -> tuple[Path, Path]:
    payload = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    json_path = out_dir / "search-index.json"
    json_path.write_text(payload + "\n", encoding="utf-8")
    js_path = out_dir / "search-index.js"
    js_path.write_text(f"window.EIDAS_SEARCH_INDEX={payload};\n", encoding="utf-8")
    return json_path, js_path
