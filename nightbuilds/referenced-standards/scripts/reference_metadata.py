"""Build reference.json payloads with provenance and download URLs."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import sys

from references import SpecReference
from resolvers import (
    body_folder,
    catalog_download_urls,
    safe_filename,
    spec_dir,
)
from spec_summarizer import enrich_reference_document

REF_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_CORPUS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CORPUS_SCRIPTS))
from tag_normalize import normalize_tags  # noqa: E402

LEGAL_PREFIXES = ("regulation/", "implementing-acts/", "implementing-decisions/")
SDO_FOLDERS = frozenset(
    {"ARF", "ETSI", "IETF", "W3C", "ISO-IEC", "CEN", "ITU-T", "IEEE", "other"}
)
STANDARDS_PATH_MARKERS = ("referenced-standards/standards/", "standards/")


def _spec_body_folder_from_source(
    source: str,
    standards_root: Path | None = None,
) -> tuple[str, str] | None:
    """
    Resolve SDO body + folder from a citation source path.

    Accepts:
      IETF/RFC-5280/RFC-5280.txt
      referenced-standards/standards/IETF/RFC-5280/RFC-5280.txt
      standards/IETF/RFC-5280/RFC-5280.txt
      ARF/technical-specifications/ts3-wallet-unit-attestation.md
    """
    norm = source.replace("\\", "/").lstrip("/")
    for marker in STANDARDS_PATH_MARKERS:
        if norm.startswith(marker):
            norm = norm[len(marker) :]
            break
    if standards_root is not None:
        from arf_technical_specs import resolve_body_folder_from_source

        arf_pair = resolve_body_folder_from_source(norm, standards_root)
        if arf_pair:
            return arf_pair
    parts = Path(norm).parts
    if len(parts) < 2 or parts[0] not in SDO_FOLDERS:
        return None
    if parts[0] == "ARF" and len(parts) >= 2 and parts[1] == "technical-specifications":
        return None
    return parts[0], parts[1]


def _canonical_spec_source(source: str, standards_root: Path | None = None) -> str:
    """Normalize spec citation sources to {body}/{folder}/… under the standards tree."""
    pair = _spec_body_folder_from_source(source, standards_root)
    if not pair:
        return source
    body, folder = pair
    norm = source.replace("\\", "/").lstrip("/")
    for marker in STANDARDS_PATH_MARKERS:
        if norm.startswith(marker):
            norm = norm[len(marker) :]
            break
    if not norm.startswith(f"{body}/{folder}"):
        suffix = Path(norm).name
        norm = f"{body}/{folder}/{suffix}" if suffix else f"{body}/{folder}"
    return norm


def https_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url if url.startswith("https://") else None


def released_at_from_ref(ref: SpecReference) -> str | None:
    """Best-effort ISO-8601 release timestamp."""
    if ref.date:
        m = re.match(r"^(\d{4})-(\d{2})$", ref.date.strip())
        if m:
            return f"{m.group(1)}-{m.group(2)}-01T00:00:00Z"
    if ref.version and re.fullmatch(r"\d{4}", ref.version.strip()):
        return f"{ref.version.strip()}-01-01T00:00:00Z"
    return None


def _load_legal_metadata(legal_root: Path, act_dir: Path) -> dict[str, Any] | None:
    meta_path = act_dir / "metadata.json"
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _legal_parent_from_source(source: str, legal_root: Path) -> dict[str, Any] | None:
    if not any(source.startswith(p) for p in LEGAL_PREFIXES):
        return None
    parts = Path(source).parts
    if len(parts) < 2:
        return None
    section, act_id = parts[0], parts[1]
    act_dir = legal_root / section / act_id
    meta = _load_legal_metadata(legal_root, act_dir)
    parent: dict[str, Any] = {
        "id": act_id,
        "source": source,
        "section": section.rstrip("/"),
    }
    if meta:
        parent["title"] = meta.get("title")
        parent["celex"] = meta.get("celex")
        parent["eli"] = https_url(meta.get("eli"))
        parent["kind"] = meta.get("kind")
        if meta.get("consolidated_as_of"):
            parent["consolidated_as_of"] = meta["consolidated_as_of"]
    return parent


def _spec_parent_from_source(
    source: str,
    standards_root: Path,
    specs_index: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if any(source.startswith(p) for p in LEGAL_PREFIXES):
        return None
    pair = _spec_body_folder_from_source(source, standards_root)
    if not pair:
        return None
    body, folder = pair
    canon_source = _canonical_spec_source(source, standards_root)
    ref_path = standards_root / body / folder / "reference.json"
    if ref_path.is_file():
        try:
            existing = json.loads(ref_path.read_text(encoding="utf-8"))
            return {
                "body": existing.get("body", body),
                "designation": existing.get("designation"),
                "version": existing.get("version"),
                "source": canon_source,
                "folder": f"{body}/{folder}",
            }
        except (json.JSONDecodeError, OSError):
            pass
    if specs_index:
        for entry in specs_index.values():
            if entry.get("folder") == f"standards/{body}/{folder}":
                return {
                    "body": entry.get("body", body),
                    "designation": entry.get("designation"),
                    "version": entry.get("version"),
                    "source": canon_source,
                    "folder": f"{body}/{folder}",
                }
    return {
        "body": body,
        "designation": folder.replace("-", " "),
        "version": None,
        "source": canon_source,
        "folder": f"{body}/{folder}",
    }


def compute_tags(
    ref: SpecReference,
    status: str,
    legal_parents: list[dict[str, Any]],
    spec_parents: list[dict[str, Any]],
    files: dict[str, Any] | None,
) -> list[str]:
    """Small tag set for filtering (see tag_normalize.ALLOWED_TAGS)."""
    tags: set[str] = {status.replace("_", "-")}

    if spec_parents:
        tags.add("nested-reference")
    if legal_parents:
        tags.add("cited-by-eu-law")
        for lp in legal_parents:
            kind = lp.get("kind")
            if kind:
                tags.add(str(kind).replace("_", "-"))

    des = ref.designation.upper()
    etsi_m = re.match(r"^(EN|TS|TR|SR)\s+(.+)", des)
    if etsi_m:
        num = etsi_m.group(2).replace(" ", "")
        if num.startswith("119"):
            tags.add("119-series")
        elif num.startswith("319"):
            tags.update({"319-series", "trust-services"})
    elif des.startswith("CEN/TS"):
        if "419261" in des or "419" in des:
            tags.add("trust-services")
        if "18170" in des:
            tags.add("common-criteria")
    elif ref.body == "ISO-IEC" and "15408" in des:
        tags.add("common-criteria")
    elif ref.body == "ARF":
        tags.add("arf-technical-spec")

    return normalize_tags(tags)


def collect_parents(
    sources: set[str],
    legal_root: Path,
    standards_root: Path,
    specs_index: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    legal: dict[str, dict[str, Any]] = {}
    specs: dict[str, dict[str, Any]] = {}
    for src in sorted(sources):
        lp = _legal_parent_from_source(src, legal_root)
        if lp:
            legal[lp["id"]] = lp
            continue
        sp = _spec_parent_from_source(src, standards_root, specs_index)
        if sp:
            key = f"{sp.get('body')}|{sp.get('designation')}|{sp.get('version') or ''}"
            specs[key] = sp
    return list(legal.values()), list(specs.values())


def build_reference_document(
    ref: SpecReference,
    sources: set[str],
    legal_root: Path,
    standards_root: Path,
    *,
    status: str,
    download_url: str | None = None,
    download_urls: list[str] | None = None,
    reason: str | None = None,
    error: str | None = None,
    files: dict[str, Any] | None = None,
    specs_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    urls = [https_url(u) for u in (download_urls or catalog_download_urls(ref))]
    urls = [u for u in urls if u]
    primary = https_url(download_url) or (urls[0] if urls else None)

    legal_parents, spec_parents = collect_parents(
        sources, legal_root, standards_root, specs_index
    )
    spec_parents = [
        sp
        for sp in spec_parents
        if not (
            sp.get("body") == ref.body
            and (sp.get("designation") or "").strip().upper()
            == ref.designation.strip().upper()
        )
    ]
    tags = compute_tags(ref, status, legal_parents, spec_parents, files)

    doc: dict[str, Any] = {
        "body": ref.body,
        "designation": ref.designation,
        "version": ref.version,
        "released_at": released_at_from_ref(ref),
        "title": ref.title,
        "download_url": primary,
        "download_urls": urls,
        "status": status,
        "tags": tags,
        "parent_legal_regulations": legal_parents,
        "parent_specifications": spec_parents,
    }
    if reason:
        doc["reason"] = reason
    if error:
        doc["error"] = error
    if files:
        doc["files"] = files
    if status in {"downloaded", "unchanged"}:
        dest = spec_dir(standards_root, ref)
        doc = enrich_reference_document(doc, dest, ref_root=REF_ROOT)
    return doc


def write_reference_json(dest_dir: Path, document: dict[str, Any]) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "reference.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_reference_for_spec(
    ref: SpecReference,
    sources: set[str],
    legal_root: Path,
    standards_root: Path,
    *,
    status: str,
    download_url: str | None = None,
    download_urls: list[str] | None = None,
    reason: str | None = None,
    error: str | None = None,
    files: dict[str, Any] | None = None,
    specs_index: dict[str, dict[str, Any]] | None = None,
) -> Path:
    dest = spec_dir(standards_root, ref)
    doc = build_reference_document(
        ref,
        sources,
        legal_root,
        standards_root,
        status=status,
        download_url=download_url,
        download_urls=download_urls,
        reason=reason,
        error=error,
        files=files,
        specs_index=specs_index,
    )
    return write_reference_json(dest, doc)
