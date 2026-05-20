#!/usr/bin/env python3
"""
Add summary and scope_keywords to metadata.json for each EU legal act in the corpus.

Usage:
  ./scripts/enrich-legal-summaries.py
  ./scripts/enrich-legal-summaries.py --section implementing-acts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from legal_summarizer import CORPUS_ROOT, LEGAL_SECTIONS, enrich_legal_metadata

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=CORPUS_ROOT,
        help="eidas-legal-tech-references root",
    )
    parser.add_argument(
        "--section",
        choices=LEGAL_SECTIONS,
        help="Only process one top-level section",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    corpus = args.corpus_root.resolve()
    sections = [args.section] if args.section else list(LEGAL_SECTIONS)

    updated = skipped = errors = 0
    for section in sections:
        base = corpus / section
        if not base.is_dir():
            continue
        for meta_path in sorted(base.glob("*/metadata.json")):
            act_dir = meta_path.parent
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                errors += 1
                print(f"✗ {meta_path}: {exc}", file=sys.stderr)
                continue

            before = json.dumps(
                {
                    "summary": meta.get("summary"),
                    "scope_keywords": meta.get("scope_keywords"),
                },
                sort_keys=True,
            )
            enriched = enrich_legal_metadata(meta, act_dir)
            after = json.dumps(
                {
                    "summary": enriched.get("summary"),
                    "scope_keywords": enriched.get("scope_keywords"),
                },
                sort_keys=True,
            )
            if before == after:
                skipped += 1
                continue
            if args.dry_run:
                print(f"would update {meta_path.relative_to(corpus)}")
                updated += 1
                continue
            meta_path.write_text(
                json.dumps(enriched, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            updated += 1
            kw = len(enriched.get("scope_keywords") or [])
            print(f"• {enriched.get('id')} — {kw} keywords")

    print(f"Updated {updated}, skipped {skipped}, errors {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
