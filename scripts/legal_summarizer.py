"""Extract summary and scope keywords from EU legal act markdown in the corpus."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CORPUS_ROOT = Path(__file__).resolve().parents[1]
SPEC_SCRIPTS = CORPUS_ROOT / "referenced-standards" / "scripts"
if str(SPEC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SPEC_SCRIPTS))

from spec_summarizer import (  # noqa: E402
    SUMMARY_MAX_LEN,
    _clean_ws,
    _strip_html,
    extract_scope_keywords,
)

LEGAL_SECTIONS = ("regulation", "implementing-acts", "implementing-decisions")
FRONT_MATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_front_matter(text: str) -> str:
    return FRONT_MATTER_RE.sub("", text, count=1)


def _extract_eli_subtitle(text: str) -> str | None:
    m = re.search(
        r'class="eli-main-title">\s*(.*?)</div>',
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return None
    block = _strip_html(m.group(1))
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    for line in lines:
        if re.match(
            r"^(laying down|on |concerning|amending|establishing|supplementing|setting|repealing)",
            line,
            re.I,
        ):
            return line[:SUMMARY_MAX_LEN]
    # Last line that is not the instrument header or date line
    for line in reversed(lines):
        if re.match(r"^of\s+\d", line, re.I):
            continue
        if re.search(
            r"\b(REGULATION|DIRECTIVE|DECISION)\b.*\b(EU|EC|EEC)\b",
            line,
            re.I,
        ):
            continue
        if len(line) > 30:
            return line[:SUMMARY_MAX_LEN]
    return None


def _extract_recitals(text: str, *, limit: int = 2) -> list[str]:
    recitals: list[tuple[int, str]] = []
    for m in re.finditer(
        r"\|\s*\\\((\d+)\\\)\s*\|\s*([^|]+?)\s*\|",
        text,
    ):
        n = int(m.group(1))
        body = _clean_ws(_strip_html(m.group(2)))
        if len(body) > 50:
            recitals.append((n, body))
    recitals.sort(key=lambda x: x[0])
    out: list[str] = []
    for _, body in recitals[:limit]:
        if len(body) > 420:
            body = body[:417].rsplit(" ", 1)[0] + "…"
        out.append(body)
    return out


def _extract_article1_subject(text: str) -> str | None:
    m = re.search(
        r"(?is)Article\s*1\b.*?(?:Subject[- ]matter|Subject matter)\s*(.+?)"
        r"(?:\n\s*Article\s*2\b|<div id=\"art_2\"|</div>\s*<div id=\"art_2\")",
        text,
    )
    if not m:
        m = re.search(
            r"(?is)<div id=\"art_1\"[^>]*>.*?Subject[- ]matter\s*</[^>]+>\s*(.+?)"
            r"(?:<div id=\"art_2\"|Article\s*2\b)",
            text,
        )
    if m:
        return _clean_ws(_strip_html(m.group(1)))[:SUMMARY_MAX_LEN]
    return None


def load_legal_markdown(act_dir: Path, act_id: str) -> str:
    candidates = [act_dir / f"{act_id}.md"]
    candidates.extend(
        p for p in sorted(act_dir.glob("*.md")) if p.name.lower() != "readme.md"
    )
    for path in candidates:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return ""


def extract_legal_insights(
    text: str,
    *,
    title: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Parse EU legal markdown into summary text and provenance hints."""
    text = _strip_front_matter(text)
    sources: list[str] = []
    summary_parts: list[str] = []

    subtitle = _extract_eli_subtitle(text)
    if subtitle:
        summary_parts.append(subtitle)
        sources.append("eli-subtitle")

    recitals = _extract_recitals(text)
    if recitals:
        summary_parts.extend(recitals)
        sources.append("recitals")

    if not recitals:
        subject = _extract_article1_subject(text)
        if subject:
            summary_parts.append(subject)
            sources.append("article-1-subject-matter")

    summary = " ".join(summary_parts).strip() if summary_parts else None
    if summary and len(summary) > SUMMARY_MAX_LEN:
        summary = summary[: SUMMARY_MAX_LEN - 1].rsplit(" ", 1)[0] + "…"

    if not summary and title:
        summary = title
        sources.append("metadata-title")

    if not summary:
        plain = _clean_ws(_strip_html(text[:8000]))
        if len(plain) > 120:
            summary = plain[:SUMMARY_MAX_LEN]
            sources.append("document-lead")

    plain_for_kw = _strip_html(text)
    keywords = extract_scope_keywords(
        plain_for_kw,
        summary,
        [],
        [kind] if kind else [],
        title or "",
        [],
    )

    return {
        "summary": summary,
        "scope_keywords": keywords,
        "sources": sources,
    }


def enrich_legal_metadata(
    meta: dict[str, Any],
    act_dir: Path,
) -> dict[str, Any]:
    act_id = meta.get("id") or act_dir.name
    md = load_legal_markdown(act_dir, act_id)
    if not md.strip():
        meta["summary"] = meta.get("title")
        meta["scope_keywords"] = meta.get("scope_keywords") or []
        meta["summary_meta"] = {
            "generated_at": _utc_now(),
            "status": "no_markdown",
            "artifact": None,
            "sources": ["metadata-title"] if meta.get("title") else [],
        }
        return meta

    insights = extract_legal_insights(
        md,
        title=meta.get("title"),
        kind=meta.get("kind"),
    )
    meta["summary"] = insights.get("summary")
    meta["scope_keywords"] = insights.get("scope_keywords") or []
    meta["summary_meta"] = {
        "generated_at": _utc_now(),
        "artifact": "md",
        "sources": insights.get("sources") or [],
        "status": "ok" if insights.get("summary") else "partial",
    }
    return meta
