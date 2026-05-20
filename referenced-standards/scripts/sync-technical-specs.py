#!/usr/bin/env python3
"""
Discover technical standards referenced in eIDAS legal texts (and recursively in
downloaded specs), classify by standardization body, and download freely available copies.

Usage:
  ./scripts/sync-technical-specs.py
  ./scripts/sync-technical-specs.py --max-depth 2 --workers 10
  ./scripts/sync-technical-specs.py --legal-root ..
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from arf_technical_specs import (
    ARF_INDEX_TREE,
    catalog as arf_catalog,
    collect_into as collect_arf_technical_specs,
    fetch_markdown,
    parse_latest_version,
    parse_title_from_markdown,
    write_catalogue_reference,
)
from prune_stale_specs import collapse_refs_to_latest, prune_stale_specs
from reference_metadata import write_reference_for_spec
from references import ExtractionResult, SpecReference, collect_from_legal_tree, extract_from_text
from resolvers import (
    ResolveResult,
    catalog_download_urls,
    extract_text_for_recursion,
    resolve_and_download,
)

ROOT = SCRIPT_DIR.parent
DEFAULT_LEGAL = ROOT.parent
STANDARDS_DIR = ROOT / "standards"
LOCK_PATH = ROOT / "manifest.lock.json"
DEFAULT_WORKERS = 10
DEFAULT_MAX_DEPTH = 2

_print_lock = threading.Lock()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_lock() -> dict:
    if LOCK_PATH.exists():
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return {"specs": {}, "synced_at": None}


def save_lock(lock: dict) -> None:
    lock["synced_at"] = datetime.now(timezone.utc).isoformat()
    LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ref_from_lock_entry(key: str, entry: dict) -> SpecReference:
    body = entry.get("body") or key.split("|", 1)[0]
    designation = entry.get("designation") or ""
    return SpecReference(
        body=body,
        designation=designation,
        version=entry.get("version"),
        date=entry.get("date"),
        title=entry.get("title"),
    )


def merged_sources(
    key: str,
    sources: set[str],
    lock: dict,
) -> set[str]:
    """Union runtime discovery with provenance stored in manifest.lock.json."""
    out = set(sources)
    lock_entry = lock.get("specs", {}).get(key, {})
    out.update(lock_entry.get("sources") or [])
    return out


def merge_extraction(
    target_refs: dict[str, SpecReference],
    target_sources: dict[str, set[str]],
    result: ExtractionResult,
) -> int:
    added = 0
    for k, ref in result.references.items():
        if k not in target_refs:
            target_refs[k] = ref
            added += 1
        target_sources.setdefault(k, set()).update(result.sources.get(k, ()))
    return added


def scan_standards_tree(standards_root: Path) -> ExtractionResult:
    """Extract references from already-downloaded spec files."""
    result = ExtractionResult()
    if not standards_root.is_dir():
        return result
    for path in standards_root.rglob("*"):
        if path.suffix.lower() not in {".txt", ".pdf", ".html", ".md"}:
            continue
        if path.name == "reference.json":
            continue
        text = extract_text_for_recursion(path)
        if text.strip():
            extract_from_text(text, str(path.relative_to(standards_root)), result)
    return result


def _files_meta(result: ResolveResult, standards_root: Path) -> dict[str, dict] | None:
    files: dict[str, dict] = {}
    if result.path and result.path.exists() and result.path.suffix != ".json":
        files[result.path.suffix.lstrip(".")] = {
            "path": str(result.path.relative_to(ROOT)),
            "sha256": sha256_file(result.path),
        }
    if result.extra_paths:
        for p in result.extra_paths:
            if p.exists() and p.suffix != ".json":
                files[p.suffix.lstrip(".")] = {
                    "path": str(p.relative_to(ROOT)),
                    "sha256": sha256_file(p),
                }
    return files or None


def _rekey_arf_refs(
    all_refs: dict[str, SpecReference],
    all_sources: dict[str, set[str]],
) -> None:
    """Use versioned keys (ARF|TS03|V1.5.1) so storage paths are stable."""
    for key in list(all_refs.keys()):
        ref = all_refs[key]
        if ref.body != "ARF":
            continue
        enriched = _arf_ref_enriched(ref)
        if enriched.key == key:
            all_refs[key] = enriched
            continue
        all_refs[enriched.key] = enriched
        all_sources[enriched.key] = all_sources.pop(key, set())
        all_refs.pop(key, None)


def _arf_ref_enriched(ref: SpecReference) -> SpecReference:
    """Resolve version/title from published markdown before choosing storage path."""
    if ref.body != "ARF":
        return ref
    for entry in arf_catalog():
        if entry.designation.upper() != ref.designation.upper():
            continue
        try:
            text, _url = fetch_markdown(entry)
        except Exception:
            return ref
        ver = parse_latest_version(text)
        title = parse_title_from_markdown(text, ref.title or entry.title)
        if not ver and not title:
            return ref
        return SpecReference(
            body=ref.body,
            designation=ref.designation,
            version=ver or ref.version,
            date=ref.date,
            title=title or ref.title,
        )
    return ref


def process_spec(
    ref: SpecReference,
    sources: set[str],
    legal_root: Path,
    standards_root: Path,
    lock: dict,
    *,
    force: bool,
    metadata_only: bool,
) -> tuple[str, str, dict | None]:
    specs_index = lock.get("specs", {})
    sources = merged_sources(ref.key, sources, lock)
    if metadata_only:
        prev = specs_index.get(ref.key, {})
        catalogue = catalog_download_urls(ref)
        result = ResolveResult(
            status=prev.get("status", "unchanged"),
            url=catalogue[0] if catalogue else prev.get("url"),
            download_urls=catalogue or prev.get("download_urls"),
            reason=prev.get("reason"),
        )
    else:
        ref = _arf_ref_enriched(ref)
        result = resolve_and_download(ref, standards_root, force=force)

    ref_path = write_reference_for_spec(
        ref,
        sources,
        legal_root,
        standards_root,
        status=result.status,
        download_url=result.url,
        download_urls=result.download_urls,
        reason=result.reason,
        error=result.error,
        files=_files_meta(result, standards_root),
        specs_index=specs_index,
    )

    lock_entry = {
        "body": ref.body,
        "designation": ref.designation,
        "version": ref.version,
        "date": ref.date,
        "title": ref.title,
        "status": result.status,
        "url": result.url,
        "download_urls": result.download_urls,
        "sources": sorted(sources),
        "folder": str(ref_path.parent.relative_to(ROOT)),
        "reference_json": str(ref_path.relative_to(ROOT)),
    }
    files = _files_meta(result, standards_root)
    if files:
        lock_entry["files"] = files
    if result.reason:
        lock_entry["reason"] = result.reason
    if result.error:
        lock_entry["error"] = result.error
    return ref.key, result.status, lock_entry  # ref.key includes version when ARF


def run_sync(
    legal_root: Path,
    standards_root: Path,
    *,
    workers: int,
    max_depth: int,
    force: bool,
    discover_only: bool,
    metadata_only: bool,
) -> dict[str, int]:
    if not legal_root.is_dir():
        print(f"Legal texts not found at {legal_root}; run: make -C eidas-legal-tech-references all", file=sys.stderr)
        return {"error": 1}

    md_count = len(list(legal_root.rglob("*.md"))) - (1 if (legal_root / "README.md").exists() else 0)
    if md_count < 5:
        print(
            f"Few markdown files under {legal_root} ({md_count}); "
            "run 'make -C eidas-legal-tech-references markdown' first.",
            file=sys.stderr,
        )

    standards_root.mkdir(parents=True, exist_ok=True)
    all_refs: dict[str, SpecReference] = {}
    all_sources: dict[str, set[str]] = {}
    merge_extraction(all_refs, all_sources, collect_from_legal_tree(legal_root))
    arf_wave = ExtractionResult()
    arf_entries = collect_arf_technical_specs(arf_wave)
    merge_extraction(all_refs, all_sources, arf_wave)
    _rekey_arf_refs(all_refs, all_sources)
    dropped = collapse_refs_to_latest(all_refs, all_sources)
    print(
        f"Found {len(all_refs) - len(arf_entries)} reference(s) in legal texts; "
        f"+{len(arf_entries)} ARF technical specification(s) "
        f"({ARF_INDEX_TREE})"
        + (f"; dropped {dropped} older duplicate version(s)" if dropped else "")
    )

    if discover_only:
        for ref in sorted(all_refs.values(), key=lambda r: (r.body, r.designation)):
            print(f"  [{ref.body}] {ref.designation}" + (f" V{ref.version}" if ref.version else ""))
        return {"discovered": len(all_refs)}

    lock = load_lock()
    counts: dict[str, int] = {}
    fetched_keys: set[str] = set()

    if metadata_only:
        lock_specs = lock.get("specs", {})
        pending = {
            key: ref_from_lock_entry(key, entry)
            for key, entry in lock_specs.items()
        }
        print(
            f"Refreshing reference.json for {len(pending)} spec(s) "
            f"(manifest; {len(all_refs)} cited in legal texts) …"
        )
    else:
        pending = None

    depth_range = [0] if metadata_only else range(max_depth + 1)

    for depth in depth_range:
        if not metadata_only:
            pending = {k: v for k, v in all_refs.items() if k not in fetched_keys or force}
            if not pending:
                break
            if depth > 0:
                wave = scan_standards_tree(standards_root)
                added = merge_extraction(all_refs, all_sources, wave)
                print(f"Depth {depth}: +{added} nested reference(s), {len(all_refs)} total")
                pending = {k: v for k, v in all_refs.items() if k not in fetched_keys}
                if not pending:
                    break
            print(f"Downloading wave {depth} ({len(pending)} spec(s), {workers} workers) …")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    process_spec,
                    ref,
                    merged_sources(key, all_sources.get(key, set()), lock),
                    legal_root,
                    standards_root,
                    lock,
                    force=force,
                    metadata_only=metadata_only,
                ): ref
                for key, ref in pending.items()
            }
            for future in as_completed(futures):
                ref = futures[future]
                try:
                    spec_key, status, meta = future.result()
                except Exception as exc:
                    spec_key, status, meta = ref.key, "error", {"error": str(exc)}
                if meta:
                    lock.setdefault("specs", {})[spec_key] = meta
                counts[status] = counts.get(status, 0) + 1
                fetched_keys.add(spec_key)
                with _print_lock:
                    label = f"{ref.body} {ref.designation}"
                    if ref.version:
                        label += f" V{ref.version}"
                    print(f"• {label} … {status}")

    if not discover_only:
        dirs_rm, lock_rm = prune_stale_specs(standards_root, all_refs, lock)
        if dirs_rm or lock_rm:
            print(
                f"Pruned superseded: {dirs_rm} folder(s), {lock_rm} manifest entry/entries"
            )
    if not discover_only and standards_root.joinpath("ARF").is_dir():
        cat_path = write_catalogue_reference(standards_root)
        print(f"Wrote ARF catalogue {cat_path.relative_to(ROOT)}")

    save_lock(lock)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legal-root",
        type=Path,
        default=DEFAULT_LEGAL,
        help=f"eIDAS legal texts root (default: {DEFAULT_LEGAL})",
    )
    parser.add_argument(
        "--standards-root",
        type=Path,
        default=STANDARDS_DIR,
        help=f"Output tree root (default: {STANDARDS_DIR})",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Rewrite reference.json from discovered sources (no downloads)",
    )
    args = parser.parse_args()

    if args.workers < 1:
        print("--workers must be >= 1", file=sys.stderr)
        return 1

    counts = run_sync(
        args.legal_root.resolve(),
        args.standards_root.resolve(),
        workers=args.workers,
        max_depth=args.max_depth,
        force=args.force,
        discover_only=args.discover_only,
        metadata_only=args.metadata_only,
    )

    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if counts.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
