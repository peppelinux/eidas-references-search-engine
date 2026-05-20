#!/usr/bin/env python3
"""
Sync eIDAS consolidated regulation and eIDAS/EUDI implementing acts from EU Cellar.

Sources: Publications Office Cellar (publications.europa.eu), not EUR-Lex HTML
(which blocks unattended downloads). Official PDF/XHTML from OJ publications.

Usage:
  ./eidas-legal-tech-references/scripts/sync-legal-texts.py              # download / refresh changed
  ./eidas-legal-tech-references/scripts/sync-legal-texts.py --check-only # report drift vs lockfile
  ./eidas-legal-tech-references/scripts/sync-legal-texts.py --dry-run
  ./eidas-legal-tech-references/scripts/sync-legal-texts.py --id 2024-2979
  ./eidas-legal-tech-references/scripts/sync-legal-texts.py --workers 10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

USER_AGENT = "Wallet-Presentations/1.0 (+https://github.com/peppelinux/Wallet-Presentations; legal-sync)"
SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR_CELEX = "http://publications.europa.eu/resource/celex/{celex}?format=xml"
LANG_ENG = "http://publications.europa.eu/resource/authority/language/ENG"
PDF_TYPES = frozenset({"pdfa1a", "pdfa2a", "pdfa2b", "pdf", "pdf1x"})
HTML_TYPES = frozenset({"xhtml", "html", "fmx4"})
DEFAULT_WORKERS = 10

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "manifest.yaml"
LOCK_PATH = ROOT / "manifest.lock.json"
REG_DIR = ROOT / "regulation"
ACTS_DIR = ROOT / "implementing-acts"
DECISIONS_DIR = ROOT / "implementing-decisions"

_print_lock = threading.Lock()


@dataclass
class ActEntry:
    id: str
    title: str
    celex: str
    eli: str | None = None
    kind: str = "implementing_regulation"
    consolidated_as_of: str | None = None


@dataclass
class SyncResult:
    act_id: str
    status: str
    meta: dict | None = None
    error: str | None = None


def load_manifest() -> tuple[list[ActEntry], str]:
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    lang = data.get("language", "ENG")
    entries: list[ActEntry] = []
    for item in data.get("regulation", []):
        entries.append(
            ActEntry(
                id=item["id"],
                title=item["title"],
                celex=item["celex"],
                eli=item.get("eli"),
                kind="regulation",
                consolidated_as_of=item.get("consolidated_as_of"),
            )
        )
    for item in data.get("implementing_acts", []):
        entries.append(
            ActEntry(
                id=item["id"],
                title=item["title"],
                celex=item["celex"],
                eli=item.get("eli"),
                kind="implementing_regulation",
            )
        )
    for item in data.get("implementing_decisions", []):
        entries.append(
            ActEntry(
                id=item["id"],
                title=item["title"],
                celex=item["celex"],
                eli=item.get("eli"),
                kind="implementing_decision",
            )
        )
    return entries, lang


def output_dir(entry: ActEntry) -> Path:
    if entry.kind == "regulation":
        return REG_DIR / entry.id
    if entry.kind == "implementing_decision":
        return DECISIONS_DIR / entry.id
    return ACTS_DIR / entry.id


def http_get(url: str, headers: dict | None = None, timeout: int = 120) -> bytes:
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_cellar_work_uri(celex: str) -> str:
    url = CELLAR_CELEX.format(celex=celex)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        final = resp.geturl()
        body = resp.read().decode("utf-8", "replace")
    m = re.search(
        r"http://publications\.europa\.eu/resource/cellar/[a-f0-9-]+", final
    ) or re.search(
        r"http://publications\.europa\.eu/resource/cellar/[a-f0-9-]+", body
    )
    if not m:
        raise RuntimeError(f"Could not resolve Cellar work URI for CELEX {celex}")
    return m.group(0)


def sparql_items(work_uri: str) -> list[tuple[str, str]]:
    query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?item ?type WHERE {{
  ?expr cdm:expression_belongs_to_work <{work_uri}> ;
        cdm:expression_uses_language <{LANG_ENG}> .
  ?manif cdm:manifestation_manifests_expression ?expr ;
         cdm:manifestation_type ?type .
  ?item cdm:item_belongs_to_manifestation ?manif .
}}
"""
    params = urllib.parse.urlencode(
        {
            "query": query,
            "format": "application/sparql-results+json",
        }
    )
    url = f"{SPARQL_ENDPOINT}?{params}"
    raw = http_get(url)
    data = json.loads(raw.decode("utf-8"))
    out: list[tuple[str, str]] = []
    for b in data.get("results", {}).get("bindings", []):
        out.append((b["item"]["value"], b["type"]["value"]))
    return out


def pick_item(items: list[tuple[str, str]], preferred_types: frozenset[str]) -> str | None:
    matches = [(u, t) for u, t in items if t in preferred_types]
    if not matches:
        return None
    order = ["pdfa2a", "pdfa1a", "pdfa2b", "pdf", "xhtml", "html", "fmx4"]
    matches.sort(key=lambda x: order.index(x[1]) if x[1] in order else 99)
    return matches[0][0]


def download_formats(
    to_fetch: list[tuple[str, str, Path]],
    *,
    check_only: bool,
    dry_run: bool,
    prev_files: dict,
) -> tuple[str | None, dict[str, dict], bool]:
    """
    Download PDF/HTML for one act. Uses a small thread pool when both formats exist.
    Returns early status for check_only (new/drift/unchanged), files_meta, changed flag.
    """
    if dry_run or not to_fetch:
        return None, {}, False

    def fetch_one(fmt: str, item_url: str, path: Path) -> tuple[str, str, Path, bytes]:
        return fmt, item_url, path, http_get(item_url)

    files_meta: dict[str, dict] = dict(prev_files)
    changed = False
    max_inner = min(len(to_fetch), 2)

    with ThreadPoolExecutor(max_workers=max_inner) as pool:
        futures = [
            pool.submit(fetch_one, fmt, item_url, path)
            for fmt, item_url, path in to_fetch
        ]
        for fut in as_completed(futures):
            fmt, item_url, path, data = fut.result()
            digest = sha256_bytes(data)
            prev_hash = prev_files.get(fmt, {}).get("sha256")
            if check_only:
                if prev_hash is None:
                    return "new", files_meta, False
                if prev_hash != digest:
                    return "drift", files_meta, False
                continue
            if prev_hash == digest and path.exists():
                continue
            changed = True
            path.write_bytes(data)
            files_meta[fmt] = {
                "sha256": digest,
                "size": len(data),
                "item_url": item_url,
                "path": str(path.relative_to(ROOT)),
            }

    return None, files_meta, changed


def sync_entry(
    entry: ActEntry,
    prev_entry: dict | None,
    *,
    dry_run: bool,
    check_only: bool,
) -> SyncResult:
    dest = output_dir(entry)
    dest.mkdir(parents=True, exist_ok=True)

    try:
        work_uri = resolve_cellar_work_uri(entry.celex)
        items = sparql_items(work_uri)
        pdf_item = pick_item(items, PDF_TYPES)
        html_item = pick_item(items, HTML_TYPES)
        if not pdf_item:
            raise RuntimeError("No English PDF item in Cellar")
    except Exception as exc:
        return SyncResult(entry.id, "error", error=str(exc))

    prev_files = dict(prev_entry.get("files", {})) if prev_entry else {}
    to_fetch: list[tuple[str, str, Path]] = []
    if pdf_item:
        to_fetch.append(("pdf", pdf_item, dest / f"{entry.id}.pdf"))
    if html_item:
        to_fetch.append(("html", html_item, dest / f"{entry.id}.html"))

    if dry_run:
        return SyncResult(entry.id, "dry-run")

    early, files_meta, changed = download_formats(
        to_fetch,
        check_only=check_only,
        dry_run=dry_run,
        prev_files=prev_files,
    )
    if early is not None:
        return SyncResult(entry.id, early)

    if check_only:
        return SyncResult(entry.id, "unchanged")

    if not prev_entry:
        status = "new"
    elif changed:
        status = "updated"
    else:
        status = "unchanged"

    meta = {
        "id": entry.id,
        "title": entry.title,
        "celex": entry.celex,
        "eli": entry.eli,
        "kind": entry.kind,
        "cellar_work": work_uri,
        "consolidated_as_of": entry.consolidated_as_of,
        "files": files_meta,
    }
    meta_path = dest / "metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return SyncResult(entry.id, status, meta=meta)


def load_lock() -> dict:
    if LOCK_PATH.exists():
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return {"acts": {}, "synced_at": None}


def save_lock(lock: dict) -> None:
    lock["synced_at"] = datetime.now(timezone.utc).isoformat()
    LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_parallel_sync(
    entries: list[ActEntry],
    lock: dict,
    *,
    workers: int,
    dry_run: bool,
    check_only: bool,
) -> list[SyncResult]:
    results: list[SyncResult] = []
    acts = lock.get("acts", {})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                sync_entry,
                entry,
                acts.get(entry.id),
                dry_run=dry_run,
                check_only=check_only,
            ): entry
            for entry in entries
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = SyncResult(entry.id, "error", error=str(exc))
            with _print_lock:
                suffix = f" ({result.error})" if result.error else ""
                print(f"• {result.act_id} ({entry.celex}) … {result.status}{suffix}")
            results.append(result)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Report changes without writing")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--id", action="append", help="Sync only these manifest ids")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        metavar="N",
        help=f"Parallel HTTP workers (default: {DEFAULT_WORKERS})",
    )
    args = parser.parse_args()

    if args.workers < 1:
        print("--workers must be >= 1", file=sys.stderr)
        return 1

    if not MANIFEST_PATH.exists():
        print(f"Missing {MANIFEST_PATH}", file=sys.stderr)
        return 1

    entries, _lang = load_manifest()
    if args.id:
        ids = set(args.id)
        entries = [e for e in entries if e.id in ids]
        unknown = ids - {e.id for e in entries}
        if unknown:
            print(f"Unknown ids: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1

    lock = load_lock()
    counts: dict[str, int] = {}

    print(
        f"Syncing {len(entries)} act(s) from Cellar → {ROOT} "
        f"({args.workers} workers)"
    )
    results = run_parallel_sync(
        entries,
        lock,
        workers=args.workers,
        dry_run=args.dry_run,
        check_only=args.check_only,
    )

    if not args.check_only and not args.dry_run:
        for result in results:
            if result.meta is not None:
                lock.setdefault("acts", {})[result.act_id] = result.meta
        save_lock(lock)

    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1

    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if args.check_only and (counts.get("drift") or counts.get("new")):
        return 2
    if counts.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
