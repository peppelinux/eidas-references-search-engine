"""Drop superseded specification versions — keep only the latest per (body, designation)."""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from references import SpecReference
from resolvers import spec_dir

_VERSION_NUMS_RE = re.compile(r"\d+")


def version_tuple(version: str | None) -> tuple[int, ...]:
    if not version or not str(version).strip():
        return (0,)
    nums = [int(x) for x in _VERSION_NUMS_RE.findall(str(version))]
    return tuple(nums) if nums else (0,)


def is_newer_version(a: str | None, b: str | None) -> bool:
    return version_tuple(a) > version_tuple(b)


def spec_identity(ref: SpecReference) -> tuple[str, str]:
    return ref.body, ref.designation.strip().upper()


# When a product name replaces an RFC number (or similar), drop the obsolete identity.
OBSOLETE_IDENTITIES: dict[tuple[str, str], tuple[str, str]] = {
    ("IETF", "RFC 9901"): ("IETF", "SD-JWT"),
}


def apply_identity_aliases(
    all_refs: dict[str, SpecReference],
    all_sources: dict[str, set[str]],
) -> int:
    """Merge obsolete designations into their canonical product-name keys."""
    removed = 0
    for obsolete, canonical in OBSOLETE_IDENTITIES.items():
        obsolete_keys = [
            k
            for k, ref in list(all_refs.items())
            if spec_identity(ref) == obsolete
        ]
        if not obsolete_keys:
            continue
        canon_key = next(
            (k for k, ref in all_refs.items() if spec_identity(ref) == canonical),
            None,
        )
        if canon_key is None:
            # Rename first obsolete entry into the canonical designation.
            old = obsolete_keys[0]
            old_ref = all_refs.pop(old)
            new_ref = SpecReference(
                body=canonical[0],
                designation="SD-JWT" if canonical[1] == "SD-JWT" else old_ref.designation,
                version=old_ref.version,
                date=old_ref.date,
                title=old_ref.title or "Selective Disclosure for JWTs (RFC 9901)",
            )
            all_refs[new_ref.key] = new_ref
            all_sources[new_ref.key] = all_sources.pop(old, set())
            obsolete_keys = obsolete_keys[1:]
            canon_key = new_ref.key
            removed += 1
        for key in obsolete_keys:
            all_sources.setdefault(canon_key, set()).update(all_sources.pop(key, set()))
            all_refs.pop(key, None)
            removed += 1
    return removed


def collapse_refs_to_latest(
    all_refs: dict[str, SpecReference],
    all_sources: dict[str, set[str]],
) -> int:
    """
    When discovery cites multiple versions of the same spec, keep only the newest.
    Merges provenance sources into the surviving entry.
    """
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key, ref in all_refs.items():
        groups[spec_identity(ref)].append(key)

    removed = 0
    for keys in groups.values():
        if len(keys) <= 1:
            continue
        latest_key = max(keys, key=lambda k: version_tuple(all_refs[k].version))
        for key in keys:
            if key == latest_key:
                continue
            all_sources.setdefault(latest_key, set()).update(all_sources.pop(key, set()))
            all_refs.pop(key)
            removed += 1
    return removed


def _read_folder_identity(folder: Path) -> tuple[str, str, str | None] | None:
    ref_json = folder / "reference.json"
    if not ref_json.is_file():
        return None
    try:
        doc = json.loads(ref_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    body = doc.get("body")
    designation = doc.get("designation")
    if not body or not designation:
        return None
    return str(body), str(designation).strip().upper(), doc.get("version")


def prune_superseded_directories(
    standards_root: Path,
    canonical_refs: dict[str, SpecReference],
) -> int:
    """Remove on-disk folders for older versions of specs still in the corpus."""
    if not standards_root.is_dir():
        return 0

    canonical_by_id: dict[tuple[str, str], SpecReference] = {
        spec_identity(ref): ref for ref in canonical_refs.values()
    }
    removed = 0
    for body_dir in standards_root.iterdir():
        if not body_dir.is_dir() or body_dir.name.startswith("."):
            continue

        by_identity: dict[tuple[str, str], list[tuple[Path, str | None]]] = defaultdict(list)
        for folder in body_dir.iterdir():
            if not folder.is_dir():
                continue
            ident = _read_folder_identity(folder)
            if not ident:
                continue
            body, designation, version = ident
            identity = (body, designation)
            # Drop obsolete aliased folders when the canonical product-name folder exists.
            if identity in OBSOLETE_IDENTITIES:
                canon_id = OBSOLETE_IDENTITIES[identity]
                if canon_id in canonical_by_id:
                    shutil.rmtree(folder)
                    removed += 1
                    continue
            by_identity[identity].append((folder, version))

        for identity, entries in by_identity.items():
            if len(entries) <= 1:
                continue
            canon = canonical_by_id.get(identity)
            if canon:
                target = spec_dir(standards_root, canon).resolve()
            else:
                target = max(entries, key=lambda e: version_tuple(e[1]))[0].resolve()
            for folder, _version in entries:
                if folder.resolve() != target:
                    shutil.rmtree(folder)
                    removed += 1

    return removed


def prune_superseded_lock(
    lock: dict[str, Any],
    canonical_refs: dict[str, SpecReference],
) -> int:
    """Drop manifest.lock.json entries superseded by a newer version."""
    specs: dict[str, Any] = lock.get("specs") or {}
    canonical_by_id = {spec_identity(ref): ref for ref in canonical_refs.values()}
    removed = 0

    # Drop obsolete aliases (e.g. IETF|RFC 9901 when IETF|SD-JWT exists).
    for key, entry in list(specs.items()):
        body = entry.get("body")
        designation = (entry.get("designation") or "").strip().upper()
        if not body or not designation:
            continue
        identity = (body, designation)
        if identity in OBSOLETE_IDENTITIES and OBSOLETE_IDENTITIES[identity] in canonical_by_id:
            del specs[key]
            removed += 1

    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key, entry in specs.items():
        body = entry.get("body")
        designation = (entry.get("designation") or "").strip().upper()
        if body and designation:
            groups[(body, designation)].append(key)

    for identity, keys in groups.items():
        if len(keys) <= 1:
            continue
        canon = canonical_by_id.get(identity)
        if canon:
            keep_key = canon.key
        else:
            keep_key = max(keys, key=lambda k: version_tuple(specs[k].get("version")))
        for key in keys:
            if key != keep_key:
                del specs[key]
                removed += 1

    lock["specs"] = specs
    return removed


def prune_stale_specs(
    standards_root: Path,
    canonical_refs: dict[str, SpecReference],
    lock: dict[str, Any],
) -> tuple[int, int]:
    """Prune disk + lock; returns (dirs_removed, lock_entries_removed)."""
    dirs = prune_superseded_directories(standards_root, canonical_refs)
    lock_rm = prune_superseded_lock(lock, canonical_refs)
    return dirs, lock_rm
