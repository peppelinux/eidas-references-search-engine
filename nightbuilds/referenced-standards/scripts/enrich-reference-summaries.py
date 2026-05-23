#!/usr/bin/env python3
"""
Add summary and scope_keywords to reference.json for downloaded specifications.

Usage:
  ./scripts/enrich-reference-summaries.py
  ./scripts/enrich-reference-summaries.py --body ETSI
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARDS = ROOT / "standards"
LEGAL_ROOT = ROOT.parent

from catalogue_metadata import (  # noqa: E402
    build_unavailable_summary,
    enrich_catalogue_metadata,
)
from spec_summarizer import (  # noqa: E402
    DOWNLOADED_STATUSES,
    enrich_reference_document,
)
from datetime import datetime, timezone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standards-root", type=Path, default=STANDARDS)
    parser.add_argument("--body", help="Only process one SDO folder (e.g. ETSI)")
    parser.add_argument("--dry-run", action="store_true", help="Print counts only")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip network catalogue lookups (ISO series + legal context only)",
    )
    args = parser.parse_args()

    standards_root = args.standards_root.resolve()
    if not standards_root.is_dir():
        print(f"Not found: {standards_root}", file=sys.stderr)
        return 1

    updated = skipped = errors = 0
    for ref_path in sorted(standards_root.rglob("reference.json")):
        if args.body and ref_path.parts[-3] != args.body:
            continue
        try:
            doc = json.loads(ref_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors += 1
            print(f"✗ {ref_path}: {exc}", file=sys.stderr)
            continue
        before = json.dumps(
            {
                "title": doc.get("title"),
                "purpose": doc.get("purpose"),
                "summary": doc.get("summary"),
                "scope_keywords": doc.get("scope_keywords"),
            },
            sort_keys=True,
        )
        if doc.get("status") in DOWNLOADED_STATUSES:
            enriched = enrich_reference_document(doc, ref_path.parent, ref_root=ROOT)
            des_u = (enriched.get("designation") or "").strip().upper()
            body = enriched.get("body")
            enriched["parent_specifications"] = [
                sp
                for sp in enriched.get("parent_specifications") or []
                if not (
                    sp.get("body") == body
                    and (sp.get("designation") or "").strip().upper() == des_u
                )
            ]
        else:
            enriched = enrich_catalogue_metadata(
                doc,
                legal_root=LEGAL_ROOT,
                cache_root=ROOT,
                use_network=not args.offline,
            )
            summary = build_unavailable_summary(enriched)
            if summary:
                enriched["summary"] = summary
            sources = list((enriched.get("catalogue_meta") or {}).get("sources") or [])
            sources.extend(["designation", "parent_legal_regulations"])
            if enriched.get("reason"):
                sources.append("reason")
            enriched["summary_meta"] = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "catalogue_fallback",
                "sources": sorted(set(sources)),
            }
        after = json.dumps(
            {
                "title": enriched.get("title"),
                "purpose": enriched.get("purpose"),
                "summary": enriched.get("summary"),
                "scope_keywords": enriched.get("scope_keywords"),
            },
            sort_keys=True,
        )
        if before == after:
            skipped += 1
            continue
        if args.dry_run:
            print(f"would update {ref_path.relative_to(ROOT)}")
            updated += 1
            continue
        ref_path.write_text(json.dumps(enriched, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        updated += 1
        label = f"{enriched.get('body')} {enriched.get('designation')}"
        kw = len(enriched.get("scope_keywords") or [])
        print(f"• {label} — {kw} keywords")

    print(f"Updated {updated}, skipped {skipped}, errors {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
