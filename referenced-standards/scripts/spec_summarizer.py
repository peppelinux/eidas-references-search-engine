"""Extract summary and scope/purpose keywords from downloaded specification artifacts."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

from resolvers import extract_text_for_recursion

DOWNLOADED_STATUSES = frozenset({"downloaded", "unchanged"})
TEXT_SUFFIXES = (".txt", ".md", ".html", ".htm", ".pdf")
SUMMARY_MAX_LEN = 900
SCOPE_MAX_LEN = 4000
KEYWORD_MAX = 16

STOPWORDS = frozenset(
    """
    a an the and or but in on at to for of as is are was were be been being
    this that these those with from by not no it its into such other than
    which may can will shall should must also any all each both when where
    how what who whom whose why if then than only very just more most some
    use used using one two three four five six seven eight nine ten
    document section part version standard specification requirements
    etu etsi ietf w3c iso iec cen itu ieee rfc en ts tr sr
    according accordingly another across present presents comprising comprise
    actors ensuring types technical connection specifies provides including
    within between during without through over under however therefore
    furthermore moreover whereas otherwise namely example examples noted
    described describes description following previous subsequent respective
    relevant applicable appropriate necessary possible available respective
    order case cases means method methods process processes procedure
    information data details detail noted notes note well work works working
    made make makes making given give given takes take taken using used
    based related relating relation respect accordance accordance
    """.split()
)

# Terms often relevant to eIDAS / trust services (boost if present in text)
DOMAIN_TERMS = frozenset(
    """
    certificate certificates pki x509 crl ocsp signature signatures sealing
    seal timestamp timestamps validation qualified trust wallet wallets
    credential credentials attestation attestations authentication
    authorization identification eidas eudi qtsp tsp ca ra
    revocation path profile profiles policy policies key keys
    encryption hash hashing algorithm algorithms tls https http
    attribute attributes pid mdl driving licence license
    archiving preservation ledger delivery registered
    conformity accreditation audit risk management
    verifiable presentation issuer holder subject
    """.split()
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_ws(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html)
    text = re.sub(r"(?is)<[^>]+>", " ", html)
    return _clean_ws(unescape(text))


def _artifact_paths(dest_dir: Path, files: dict[str, Any] | None, ref_root: Path) -> list[Path]:
    paths: list[Path] = []
    if files:
        for meta in files.values():
            if not isinstance(meta, dict):
                continue
            rel = meta.get("path")
            if rel:
                p = ref_root / rel
                if p.is_file():
                    paths.append(p)
    if not paths:
        for p in sorted(dest_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES and p.name != "reference.json":
                paths.append(p)
    # Prefer plain text, then markdown, html, pdf
    order = {".txt": 0, ".md": 1, ".html": 2, ".htm": 2, ".pdf": 3}
    paths.sort(key=lambda p: (order.get(p.suffix.lower(), 9), p.name))
    return paths


def load_spec_text(dest_dir: Path, files: dict[str, Any] | None, ref_root: Path) -> tuple[str, str | None]:
    """Return (text, artifact_suffix) from the best local artifact."""
    for path in _artifact_paths(dest_dir, files, ref_root):
        if path.suffix.lower() == ".pdf" and not shutil.which("pdftotext"):
            continue
        text = extract_text_for_recursion(path)
        if text and len(text.strip()) > 80:
            return text, path.suffix.lower().lstrip(".")
    return "", None


def _extract_rfc_title_and_abstract(text: str) -> tuple[str | None, str | None, list[str]]:
    lines = text.splitlines()
    title_parts: list[str] = []
    in_title = False
    for i, line in enumerate(lines[:80]):
        s = line.strip()
        if not s or s.startswith("Network Working Group") or s.startswith("Request for Comments"):
            continue
        if re.match(r"^RFC \d+", s, re.I) or s.startswith("Category:") or s.startswith("Obsoletes:"):
            continue
        if re.match(r"^[A-Z][a-z].*(\d{4})?$", s) and i > 5 and not in_title:
            in_title = True
        if in_title and s and not s.startswith("Status of"):
            if "Status of This Memo" in s or s == "Abstract":
                break
            title_parts.append(s)
        if s == "Abstract":
            rest = "\n".join(lines[i + 1 :])
            m = re.search(
                r"(?is)^\s*(.+?)(?:\n\s*\n\s*(?:\d+\.\s|Table of Contents|1\.\s+Introduction|\Z))",
                rest,
            )
            abstract = _clean_ws(m.group(1)) if m else _clean_ws(rest[:2500])
            title = _clean_ws(" ".join(title_parts)) if title_parts else None
            return title, abstract[:SUMMARY_MAX_LEN], []
    return None, None, []


def _extract_etsi_fields(text: str) -> tuple[str | None, str | None, list[str]]:
    title = None
    m = re.search(
        r"(?im)^(ETSI\s+(?:EN|TS|TR|SR)\s+[\d\s\-]+.*?)\s*(?:\(|V\d)",
        text[:8000],
    )
    if m:
        title = _clean_ws(m.group(1))

    keywords: list[str] = []
    km = re.search(r"(?im)^Keywords\s*\n(.+?)(?:\n\s*\n|\n\d|\Z)", text[:15000], re.DOTALL)
    if km:
        raw = km.group(1).replace("\n", " ")
        keywords = [k.strip() for k in re.split(r"[,;]", raw) if k.strip() and len(k.strip()) > 2]

    scope = None
    sm = re.search(
        r"(?is)\b1\s+Scope\s*\n+(.+?)(?:\n\s*\n\s*2\s+|\n2\s+References|\n2\s+Normative|\Z)",
        text[:25000],
    )
    if sm:
        scope = _clean_ws(sm.group(1))[:SCOPE_MAX_LEN]

    summary = scope or None
    if not summary and keywords:
        summary = "Covers: " + ", ".join(keywords[:12]) + "."

    return title, summary[:SUMMARY_MAX_LEN] if summary else None, keywords


def _extract_html_fields(text: str) -> tuple[str | None, str | None, list[str]]:
    title_m = re.search(r"(?is)<title[^>]*>([^<]+)</title>", text)
    title = _clean_ws(unescape(title_m.group(1))) if title_m else None

    abstract = None
    for pat in (
        r'(?is)<section[^>]*id=["\']abstract["\'][^>]*>(.+?)</section>',
        r"(?is)<div[^>]*class=[\"'][^\"']*abstract[^\"']*[\"'][^>]*>(.+?)</div>",
        r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
    ):
        m = re.search(pat, text)
        if m:
            abstract = _strip_html(m.group(1))[:SUMMARY_MAX_LEN]
            break

    if not abstract:
        plain = _strip_html(text)
        intro = re.search(r"(?is)(introduction|abstract)\s+(.{200,1200})", plain)
        if intro:
            abstract = _clean_ws(intro.group(2))[:SUMMARY_MAX_LEN]

    return title, abstract, []


_SKIP_MD_SECTIONS = frozenset(
    {
        "licensing and reuse",
        "licensing",
        "versioning",
        "references",
        "table of contents",
        "acknowledgements",
        "changelog",
        "github discussion",
    }
)


def _strip_markdown_markup(text: str) -> str:
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    return _clean_ws(text)


def _markdown_section_body(text: str, heading_pattern: str) -> str | None:
    m = re.search(
        rf"(?is)^##\s+{heading_pattern}\s*$\n+(.+?)(?=^##\s|\Z)",
        text,
        re.M,
    )
    if not m:
        return None
    body = m.group(1)
    sub = re.search(r"(?m)^###\s", body)
    if sub:
        body = body[: sub.start()]
    return body.strip()


def _markdown_section_plain(text: str, heading_pattern: str) -> str | None:
    raw = _markdown_section_body(text, heading_pattern)
    if not raw:
        return None
    plain = _strip_markdown_markup(raw)
    return plain if len(plain) > 40 else None


def _markdown_focus_text(text: str) -> str:
    """Abstract + introduction (for keywords) — skip licensing, versioning, etc."""
    chunks: list[str] = []
    for pattern in (
        r"Abstract",
        r"1\s+Introduction\s+and\s+Overview",
        r"Introduction\s+and\s+Overview",
        r"Introduction",
    ):
        plain = _markdown_section_plain(text, pattern)
        if plain:
            chunks.append(plain)
    return "\n\n".join(chunks)


def _extract_markdown_fields(
    text: str,
) -> tuple[str | None, str | None, str, list[str]]:
    """Title, summary, and focus text from ARF / GitHub markdown technical specs."""
    sources: list[str] = []
    title: str | None = None
    m = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    if m:
        title = _clean_ws(_strip_markdown_markup(m.group(1)))
        sources.append("md-title")

    summary = _markdown_section_plain(text, r"Abstract")
    if summary:
        summary = summary[:SUMMARY_MAX_LEN]
        sources.append("md-abstract")
    else:
        for pattern in (
            r"1\s+Introduction\s+and\s+Overview",
            r"Introduction\s+and\s+Overview",
            r"Introduction",
        ):
            intro = _markdown_section_plain(text, pattern)
            if intro:
                summary = intro[:SUMMARY_MAX_LEN]
                sources.append("md-intro")
                break

    focus = _markdown_focus_text(text)

    return title, summary, focus, sources


def _extract_generic_scope(text: str) -> str | None:
    for pat in (
        r"(?is)\babstract\s*\n+(.+?)(?:\n\s*\n\s*(?:\d+\.|introduction|table of contents)\b)",
        r"(?is)\b1\s+scope\s*\n+(.+?)(?:\n\s*\n\s*2\s+)",
        r"(?is)\bpurpose\s*\n+(.+?)(?:\n\s*\n)",
    ):
        m = re.search(pat, text[:20000])
        if m:
            return _clean_ws(m.group(1))[:SUMMARY_MAX_LEN]
    return None


def extract_document_insights(
    text: str,
    *,
    body: str,
    designation: str,
    artifact: str | None,
) -> dict[str, Any]:
    """Parse title, summary, and keyword hints from specification text."""
    title: str | None = None
    summary: str | None = None
    keyword_hints: list[str] = []
    sources: list[str] = []

    focus_text = ""
    des_u = designation.upper()

    if body == "ARF" or artifact == "md":
        t, s, focus, md_sources = _extract_markdown_fields(text)
        sources.extend(md_sources)
        if t:
            title = t
        if s:
            summary = s
        if focus:
            focus_text = focus

    if body == "IETF" or des_u.startswith("RFC ") or artifact == "txt":
        t, s, _ = _extract_rfc_title_and_abstract(text)
        if t:
            title, sources = t, sources + ["rfc-title"]
        if s:
            summary, sources = s, sources + ["rfc-abstract"]

    if body == "ETSI" or artifact == "pdf":
        t, s, kw = _extract_etsi_fields(text)
        if t and not title:
            title, sources = t, sources + ["etsi-title"]
        if s:
            summary, sources = s, sources + ["etsi-scope-or-keywords"]
        keyword_hints.extend(kw)

    if artifact in {"html", "htm"} or body == "W3C":
        t, s, _ = _extract_html_fields(text)
        if t and not title:
            title, sources = t, sources + ["html-title"]
        if s:
            summary, sources = s, sources + ["html-abstract"]

    if not summary:
        g = _extract_generic_scope(text)
        if g:
            summary, sources = g, sources + ["generic-scope"]

    if not summary and keyword_hints:
        summary = "European / normative specification addressing: " + ", ".join(keyword_hints[:10]) + "."
        sources.append("etsi-keywords-line")

    if not summary and body != "ARF":
        plain = _clean_ws(_strip_markdown_markup(text[:3000]))
        if len(plain) > 120:
            summary = plain[:SUMMARY_MAX_LEN]
            sources.append("document-lead")

    if not focus_text and body == "ARF":
        focus_text = _markdown_focus_text(text)

    return {
        "title": title,
        "summary": summary,
        "keyword_hints": keyword_hints,
        "sources": sources,
        "focus_text": focus_text,
    }


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
    return [w for w in words if w not in STOPWORDS and not w.isdigit()]


def _add_keyword(out: list[str], seen: set[str], raw: str) -> None:
    w = re.sub(r"\s+", " ", raw.strip().lower())
    if not w or len(w) < 3 or w in STOPWORDS or w in seen:
        return
    seen.add(w)
    out.append(w)


def _designation_keywords(designation: str) -> list[str]:
    """Stable tokens from the normative reference string (RFC 5280, TS 119 612, …)."""
    found: list[str] = []
    seen: set[str] = set()
    for num in re.findall(r"\d{3,}(?:-\d+)?", designation):
        _add_keyword(found, seen, num)
    for token in re.findall(r"[A-Za-z]{2,}", designation):
        t = token.lower()
        if t in {"en", "ts", "tr", "sr", "iec", "iso"}:
            continue
        _add_keyword(found, seen, t)
    return found


def extract_scope_keywords(
    text: str,
    summary: str | None,
    keyword_hints: list[str],
    tags: list[str],
    designation: str,
    legal_parents: list[dict[str, Any]],
) -> list[str]:
    """
    Scope terms for reports/search — not the same as ``tags``.

    Only keeps: ETSI catalogue keywords, designation tokens, and domain
    vocabulary (wallet, certificate, attestation, …) found in summary/abstract.
    Generic prose terms (according, present, another, …) are never emitted.
    """
    out: list[str] = []
    seen: set[str] = set()

    for hint in keyword_hints:
        for part in re.split(r"[,;/]+", hint):
            _add_keyword(out, seen, part)

    for token in _designation_keywords(designation):
        _add_keyword(out, seen, token)

    focus_parts = [
        summary or "",
        " ".join(keyword_hints),
        designation,
        " ".join(lp.get("title", "") or "" for lp in legal_parents[:3]),
    ]
    sample = " ".join(filter(None, focus_parts)).lower()
    if text:
        sample = f"{sample}\n{text[:8000].lower()}"

    domain_hits = [w for w in DOMAIN_TERMS if w in sample]
    for w in sorted(domain_hits):
        _add_keyword(out, seen, w)

    return out[:KEYWORD_MAX]


def fallback_spec_summary(doc: dict[str, Any]) -> str | None:
    """Short summary when no local artifact is available."""
    try:
        from catalogue_metadata import build_unavailable_summary

        built = build_unavailable_summary(doc)
        if built:
            return built
    except ImportError:
        pass

    parts: list[str] = []
    title = doc.get("title")
    if title:
        parts.append(str(title).strip())
    else:
        label = " ".join(
            p for p in (doc.get("body"), doc.get("designation")) if p
        ).strip()
        if label:
            parts.append(label)
    purpose = doc.get("purpose")
    if purpose:
        parts.append(str(purpose).strip())
    legal = doc.get("parent_legal_regulations") or []
    if legal:
        acts = ", ".join(lp.get("id", "") for lp in legal[:4] if lp.get("id"))
        if acts:
            parts.append(f"Cited by EU act(s): {acts}.")
    reason = doc.get("reason")
    if reason:
        parts.append(str(reason).strip())
    if not parts:
        return None
    text = " ".join(parts)
    if len(text) > SUMMARY_MAX_LEN:
        text = text[: SUMMARY_MAX_LEN - 1].rsplit(" ", 1)[0] + "…"
    return text


def enrich_reference_document(
    doc: dict[str, Any],
    dest_dir: Path,
    *,
    ref_root: Path,
) -> dict[str, Any]:
    """
    Add summary, scope_keywords, and summary_meta to a reference document
    when a local artifact is available.
    """
    status = doc.get("status") or ""
    if status not in DOWNLOADED_STATUSES:
        return doc

    text, artifact = load_spec_text(dest_dir, doc.get("files"), ref_root)
    if not text:
        summary = fallback_spec_summary(doc)
        doc["summary"] = summary
        doc["scope_keywords"] = doc.get("scope_keywords") or []
        doc["summary_meta"] = {
            "generated_at": _utc_now(),
            "status": "fallback_no_extractable_text" if summary else "no_extractable_text",
            "artifact": artifact,
            "sources": ["designation", "parent_legal_regulations", "reason"],
        }
        return doc

    insights = extract_document_insights(
        text,
        body=doc.get("body") or "",
        designation=doc.get("designation") or "",
        artifact=artifact,
    )

    summary = insights.get("summary")
    if summary and len(summary) > SUMMARY_MAX_LEN:
        summary = summary[: SUMMARY_MAX_LEN - 1].rsplit(" ", 1)[0] + "…"

    body = doc.get("body") or ""
    keyword_sample = insights.get("focus_text") or ""
    if body == "ARF" and keyword_sample:
        keyword_sample = keyword_sample[:SCOPE_MAX_LEN]
    else:
        keyword_sample = text

    keywords = extract_scope_keywords(
        keyword_sample,
        summary,
        insights.get("keyword_hints") or [],
        doc.get("tags") or [],
        doc.get("designation") or "",
        doc.get("parent_legal_regulations") or [],
    )

    if insights.get("title") and not doc.get("title"):
        doc["title"] = insights["title"]

    doc["summary"] = summary
    doc["scope_keywords"] = keywords
    doc["summary_meta"] = {
        "generated_at": _utc_now(),
        "artifact": artifact,
        "sources": insights.get("sources") or [],
        "status": "ok" if summary else "partial",
    }
    return doc


def enrich_existing_reference_json(ref_path: Path, *, ref_root: Path) -> bool:
    """Re-read reference.json, enrich, and write back. Returns True if updated."""
    try:
        doc = json.loads(ref_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    dest_dir = ref_path.parent
    before = (doc.get("summary"), tuple(doc.get("scope_keywords") or []))
    doc = enrich_reference_document(doc, dest_dir, ref_root=ref_root)
    after = (doc.get("summary"), tuple(doc.get("scope_keywords") or []))
    if before == after:
        return False
    ref_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True
