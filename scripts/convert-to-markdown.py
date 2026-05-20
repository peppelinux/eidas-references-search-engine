#!/usr/bin/env python3
"""
Convert synced OJ XHTML legal texts to Markdown (GFM) via pandoc.

Reads {id}.html in each act folder; writes {id}.md with YAML front matter.
Requires pandoc on PATH (https://pandoc.org/).

Usage:
  ./eidas-legal-tech-references/scripts/convert-to-markdown.py
  ./eidas-legal-tech-references/scripts/convert-to-markdown.py --id 2024-2979
  ./eidas-legal-tech-references/scripts/convert-to-markdown.py --force
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEARCH_DIRS = (
    ROOT / "regulation",
    ROOT / "implementing-acts",
    ROOT / "implementing-decisions",
)


def find_pandoc(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = shutil.which("pandoc")
    if not found:
        print("pandoc not found; install pandoc or set PANDOC=/path/to/pandoc", file=sys.stderr)
        sys.exit(1)
    return found


def load_metadata(act_dir: Path) -> dict:
    meta_path = act_dir / "metadata.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    act_id = act_dir.name
    return {"id": act_id, "title": act_id}


def front_matter(meta: dict, html_path: Path) -> str:
    lines = ["---"]
    for key in ("id", "title", "celex", "eli", "kind", "consolidated_as_of"):
        val = meta.get(key)
        if val is not None:
            lines.append(f'{key}: "{val}"' if isinstance(val, str) else f"{key}: {val}")
    html_hash = meta.get("files", {}).get("html", {}).get("sha256")
    if html_hash:
        lines.append(f'source_html_sha256: "{html_hash}"')
    lines.append(f'converted_at: "{datetime.now(timezone.utc).isoformat()}"')
    lines.append(f'source: "{html_path.name}"')
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def convert_html(pandoc: str, html_path: Path, md_path: Path, meta: dict) -> None:
    body = subprocess.run(
        [
            pandoc,
            "-f",
            "html",
            "-t",
            "gfm",
            "--wrap=none",
            str(html_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    md_path.write_text(front_matter(meta, html_path) + body, encoding="utf-8")


def iter_act_dirs(only_id: str | None) -> list[Path]:
    dirs: list[Path] = []
    for base in SEARCH_DIRS:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            if only_id and child.name != only_id:
                continue
            dirs.append(child)
    return dirs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", action="append", help="Convert only these manifest ids")
    parser.add_argument("--force", action="store_true", help="Rebuild even if .md is up to date")
    parser.add_argument(
        "--pandoc",
        default=os.environ.get("PANDOC"),
        help="Pandoc executable (default: $PANDOC or pandoc on PATH)",
    )
    args = parser.parse_args()

    pandoc = find_pandoc(args.pandoc)
    only = set(args.id) if args.id else None
    counts: dict[str, int] = {}

    act_dirs = iter_act_dirs(None)
    if only:
        act_dirs = [d for d in act_dirs if d.name in only]
        missing = only - {d.name for d in act_dirs}
        if missing:
            print(f"Unknown or missing ids: {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    print(f"Converting HTML → Markdown with {pandoc} ({len(act_dirs)} act folder(s))")

    for act_dir in act_dirs:
        act_id = act_dir.name
        html_path = act_dir / f"{act_id}.html"
        md_path = act_dir / f"{act_id}.md"

        if not html_path.exists():
            print(f"  SKIP {act_id}: no {html_path.name} (run make sync first)")
            counts["skip"] = counts.get("skip", 0) + 1
            continue

        if (
            not args.force
            and md_path.exists()
            and md_path.stat().st_mtime >= html_path.stat().st_mtime
        ):
            print(f"  OK   {act_id} (unchanged)")
            counts["unchanged"] = counts.get("unchanged", 0) + 1
            continue

        meta = load_metadata(act_dir)
        try:
            convert_html(pandoc, html_path, md_path, meta)
        except subprocess.CalledProcessError as exc:
            print(f"  ERROR {act_id}: pandoc failed: {exc.stderr}", file=sys.stderr)
            counts["error"] = counts.get("error", 0) + 1
            continue

        print(f"  OK   {act_id} → {md_path.relative_to(ROOT)}")
        counts["converted"] = counts.get("converted", 0) + 1

    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
