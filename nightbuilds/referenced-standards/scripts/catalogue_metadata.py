"""
Enrich unavailable (and other) specifications with public catalogue metadata:
titles, purpose text, and improved summaries.

Sources (best effort, no API keys):
  - Wikipedia (ISO/IEC, ITU, IEEE, POSIX, Common Criteria, …)
  - RFC Editor HTML (IETF)
  - ITU-T recommendation pages
  - ISO/IEC series heuristics + static CEN/ETSI hints
  - Sentences from citing EU legal markdown
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

USER_AGENT = "eidas-legal-tech-references/1.0 (catalogue enrichment; +https://github.com)"
_FETCH_DELAY_S = 0.35
_CACHE: dict[str, dict[str, Any]] | None = None

# Base standard → series title (ISO/IEC numbers cited in eIDAS corpus)
ISO_SERIES: dict[str, str] = {
    "19794": "Biometric data interchange formats",
    "39794": "Biometric recognition across devices and systems",
    "15408": "Information technology security evaluation (Common Criteria)",
    "18013": "Personal identification — ISO-compliant driving licence (mDL)",
    "27001": "Information security management systems",
    "17000": "Conformity assessment — vocabulary and general principles",
    "17011": "Conformity assessment — accreditation bodies",
    "17020": "Conformity assessment — inspection bodies",
    "17021": "Conformity assessment — certification of management systems",
    "17025": "Testing and calibration laboratories",
    "17029": "Conformity assessment — certification of persons",
    "17065": "Conformity assessment — product certification",
    "17067": "Conformity assessment — certification schemes",
    "9594": "Open Systems Interconnection — The Directory",
    "8824": "Abstract Syntax Notation One (ASN.1)",
    "8825": "ASN.1 encoding rules",
    "9834": "IT security techniques",
    "3166": "Codes for the representation of names of countries and subdivisions",
    "10646": "Universal Coded Character Set (UCS)",
    "14721": "Space data — Open archival information system (OAIS)",
    "14641": "Electronic document management",
    "23257": "Building information modelling",
    "5218": "Codes for the representation of human sexes",
    "30111": "Vulnerability handling processes",
    "8859": "Information technology — 8-bit single-byte coded graphic character sets",
    "100646": "Universal Coded Character Set (UCS)",  # common OCR typo for 10646
}

ISO_PART_HINTS: dict[str, dict[str, str]] = {
    "19794": {
        "5": "Face image data",
        "2": "Finger minutiae data",
        "4": "Finger image data",
        "6": "Iris image data",
    },
    "15408": {
        "1": "Introduction and general model",
        "2": "Security functional components",
        "3": "Security assurance components",
    },
    "18013": {"5": "Mobile driving licence (mDL) application"},
    "9594": {
        "1": "Overview of concepts, models and services",
        "2": "Models",
        "6": "Selected attribute types",
        "8": "Public-key and attribute certificate frameworks",
    },
}

CEN_HINTS: dict[str, str] = {
    "419261": "Electronic signatures and trust infrastructures — cryptographic suites and protocols",
    "18170": "Cybersecurity evaluation of ICT products (Common Criteria related)",
}

ETSI_HINTS: dict[str, str] = {
    "319403": "Electronic signatures and trust infrastructures — trust service provider policies",
    "319122": "Electronic signatures and trust infrastructures — CAdES digital signatures",
}

IEEE_HINTS: dict[str, str] = {
    "1003.1": "POSIX — portable operating system interface",
    "105": "Open Systems Interconnection — terminology",
}

# ITU-T recommendations frequently cited in trust services / directory work
ITU_HINTS: dict[str, str] = {
    "X.411": "Message Handling Systems (MHS) — message transfer system",
    "X.500": "Information technology — Open Systems Interconnection — The Directory: Overview of concepts, models and services",
    "X.501": "The Directory — Models",
    "X.509": "Information technology — Open Systems Interconnection — Public-key and attribute certificate frameworks",
    "X.520": "The Directory — Selected attribute types",
    "X.660": "Procedures for registration of object identifiers",
    "X.680": "Abstract Syntax Notation One (ASN.1): Specification of basic notation",
    "X.683": "ASN.1 — Parameterization of ASN.1 specifications",
    "X.690": "ASN.1 encoding rules — Specification of Basic Encoding Rules (BER)",
}

ISO_EXTRA: dict[str, str] = {
    "10181-5": "Information technology — Open Systems Interconnection — Security frameworks — Confidentiality architecture",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_path(root: Path) -> Path:
    return root / ".catalogue-cache.json"


def _load_cache(root: Path) -> dict[str, dict[str, Any]]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    path = _cache_path(root)
    if path.is_file():
        try:
            _CACHE = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _CACHE = {}
    else:
        _CACHE = {}
    return _CACHE


def _save_cache(root: Path) -> None:
    if _CACHE is None:
        return
    path = _cache_path(root)
    path.write_text(json.dumps(_CACHE, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _http_text(url: str, timeout: int = 20) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    return raw.decode("utf-8", errors="replace")


def _first_sentence(text: str, max_len: int = 420) -> str:
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    if not text:
        return ""
    m = re.match(r"^(.+?[.!?])(?:\s|$)", text)
    sent = m.group(1) if m else text
    if len(sent) > max_len:
        sent = sent[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return sent


def _iso_number(designation: str) -> tuple[str, str | None] | None:
    des = designation.upper().replace("ISO/IEC", "ISO IEC").replace("ISO IEC", "")
    m = re.search(r"(?:ISO|IEC)\s*(\d{3,6})(?:-(\d+))?", des)
    if not m:
        m = re.search(r"\b(\d{3,6})(?:-(\d+))?\b", des)
    if not m:
        return None
    base, part = m.group(1), m.group(2)
    return base, part


def _infer_iso_title(designation: str) -> tuple[str | None, str | None]:
    parsed = _iso_number(designation)
    if not parsed:
        return None, None
    base, part = parsed
    if base == "100646":
        base = "10646"
    series = ISO_SERIES.get(base)
    if not series:
        return None, None
    title = f"ISO/IEC {base}"
    purpose = series
    if part:
        part_name = (ISO_PART_HINTS.get(base) or {}).get(part)
        title = f"ISO/IEC {base}-{part}"
        if part_name:
            purpose = f"{series} — Part {part}: {part_name}"
        else:
            purpose = f"{series} — Part {part}"
    return title, purpose


def _wikipedia_titles(body: str, designation: str) -> list[str]:
    titles: list[str] = []
    if body == "ISO-IEC":
        parsed = _iso_number(designation)
        if parsed:
            base, part = parsed
            if part:
                titles.append(f"ISO/IEC_{base}-{part}")
            titles.append(f"ISO/IEC_{base}")
            titles.append(f"ISO_{base}")
        if "15408" in designation or "common criteria" in designation.lower():
            titles.append("Common Criteria")
    elif body == "ITU-T":
        rid = re.sub(r"^ITU-T\s*", "", designation, flags=re.I).strip()
        if rid:
            titles.append(rid)
            titles.append(f"ITU-T_{rid.replace('.', '_')}")
    elif body == "IEEE":
        m = re.search(r"IEEE\s*([\w.]+)", designation, re.I)
        if m:
            num = m.group(1)
            titles.append(f"IEEE_{num.replace('.', '_')}")
            if num == "1003.1":
                titles.append("POSIX")
    elif body == "CEN":
        m = re.search(r"(?:CEN/)?(?:EN|TS)\s*(\d+)", designation, re.I)
        if m:
            titles.append(f"CEN_EN_{m.group(1)}")
    return titles


def _fetch_wikipedia(title: str) -> dict[str, str] | None:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": title,
            "prop": "extracts",
            "exintro": "1",
            "explaintext": "1",
            "format": "json",
        }
    )
    url = f"https://en.wikipedia.org/w/api.php?{params}"
    text = _http_text(url)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    if page.get("missing"):
        return None
    wiki_title = page.get("title") or title
    extract = (page.get("extract") or "").strip()
    if not extract:
        return None
    return {"title": wiki_title, "description": extract}


def _fetch_rfc_title(designation: str) -> dict[str, str] | None:
    m = re.search(r"RFC\s*0*(\d+)", designation, re.I)
    if not m:
        return None
    num = str(int(m.group(1)))
    url = f"https://www.rfc-editor.org/rfc/rfc{num}.html"
    html = _http_text(url)
    if not html:
        return None
    mt = re.search(r"<title>\s*RFC\s*\d+:\s*([^<]+?)\s*</title>", html, re.I)
    if not mt:
        return None
    rfc_title = unescape(mt.group(1).strip())
    return {
        "title": f"RFC {num}: {rfc_title}",
        "description": rfc_title,
    }


def _fetch_itu_title(designation: str) -> dict[str, str] | None:
    rid = re.sub(r"^ITU-T\s*", "", designation, flags=re.I).strip()
    if not rid:
        return None
    slug = rid.replace(".", "-")
    url = f"https://www.itu.int/rec/T-REC-{slug}/en"
    html = _http_text(url)
    if not html:
        return None
    mt = re.search(r"<title>([^<]+)</title>", html, re.I)
    title = unescape(mt.group(1).strip()) if mt else ""
    title = re.sub(r"\s*\|\s*ITU.*$", "", title, flags=re.I).strip()
    if not title or title.lower() == "itu":
        return None
    return {"title": f"ITU-T {rid}: {title}", "description": title}


def _static_hint(body: str, designation: str) -> dict[str, str] | None:
    des = designation.upper()
    if body == "CEN":
        m = re.search(r"(\d{4,6})", des)
        if m and m.group(1) in CEN_HINTS:
            return {"title": designation.strip(), "description": CEN_HINTS[m.group(1)]}
    if body == "ETSI":
        m = re.search(r"319\s*(\d+)", des.replace(" ", ""))
        if m:
            key = "319" + m.group(1)[:3]
            for k, hint in ETSI_HINTS.items():
                if key.startswith(k) or k in des.replace(" ", ""):
                    return {"title": designation.strip(), "description": hint}
        m2 = re.search(r"319(\d{3})", des.replace(" ", ""))
        if m2 and m2.group(1) in ("403", "122"):
            key = "319" + m2.group(1)
            return {"title": designation.strip(), "description": ETSI_HINTS[key]}
    if body == "IEEE":
        m = re.search(r"IEEE\s*([\d.]+)", designation, re.I)
        if m and m.group(1) in IEEE_HINTS:
            return {"title": designation.strip(), "description": IEEE_HINTS[m.group(1)]}
    if body == "ITU-T":
        m = re.search(r"X\.\d+", designation, re.I)
        if m and m.group(0).upper() in ITU_HINTS:
            rid = m.group(0).upper()
            return {
                "title": f"ITU-T {rid}",
                "description": ITU_HINTS[rid],
                "source": "itu_static",
            }
    if body == "ISO-IEC":
        parsed = _iso_number(designation)
        if parsed:
            base, part = parsed
            key = f"{base}-{part}" if part else base
            if key in ISO_EXTRA:
                return {
                    "title": f"ISO/IEC {key}",
                    "description": ISO_EXTRA[key],
                    "source": "iso_static",
                }
    return None


def _citation_patterns(designation: str) -> list[re.Pattern[str]]:
    patterns: list[str] = [re.escape(designation)]
    parsed = _iso_number(designation)
    if parsed:
        base, part = parsed
        patterns.append(rf"ISO/?IEC\s*{base}(?:\s*[-–]\s*{part})?" if part else rf"ISO/?IEC\s*{base}")
        patterns.append(rf"\b{base}(?:-{part})?\b")
    m = re.search(r"RFC\s*0*(\d+)", designation, re.I)
    if m:
        patterns.append(rf"RFC\s*0*{m.group(1)}")
    m = re.search(r"X\.\d+", designation, re.I)
    if m:
        patterns.append(re.escape(m.group(0)))
    return [re.compile(p, re.I) for p in patterns]


def _extract_legal_context(
    doc: dict[str, Any], legal_root: Path | None
) -> list[str]:
    if not legal_root or not legal_root.is_dir():
        return []
    parents = doc.get("parent_legal_regulations") or []
    if not parents:
        return []
    patterns = _citation_patterns(doc.get("designation") or "")
    if not patterns:
        return []
    contexts: list[str] = []
    seen: set[str] = set()
    for parent in parents:
        act_id = parent.get("id")
        section = parent.get("section")
        if not act_id or not section:
            continue
        md_path = legal_root / section / act_id / f"{act_id}.md"
        if not md_path.is_file():
            continue
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = re.sub(r"<[^>]+>", " ", line)
            line = re.sub(r"\s+", " ", line).strip()
            if len(line) < 20 or len(line) > 600:
                continue
            if not any(p.search(line) for p in patterns):
                continue
            sent = _first_sentence(line, max_len=320)
            if sent and sent not in seen:
                seen.add(sent)
                contexts.append(sent)
            if len(contexts) >= 3:
                break
        if len(contexts) >= 3:
            break
    return contexts


def lookup_catalogue_metadata(
    body: str,
    designation: str,
    *,
    cache_root: Path | None = None,
    use_network: bool = True,
) -> dict[str, Any]:
    """Return {title, purpose, description, sources} from public catalogues."""
    key = f"{body}|{designation}"
    root = cache_root or Path(__file__).resolve().parents[1]
    cache = _load_cache(root)
    if key in cache:
        return dict(cache[key])

    result: dict[str, Any] = {"sources": []}
    fetched: list[dict[str, str] | None] = []

    if body == "IETF" and use_network:
        time.sleep(_FETCH_DELAY_S)
        fetched.append(_fetch_rfc_title(designation))
    elif body == "ITU-T" and use_network:
        time.sleep(_FETCH_DELAY_S)
        fetched.append(_fetch_itu_title(designation))
        for wt in _wikipedia_titles(body, designation):
            time.sleep(_FETCH_DELAY_S)
            fetched.append(_fetch_wikipedia(wt))
    elif use_network:
        for wt in _wikipedia_titles(body, designation):
            time.sleep(_FETCH_DELAY_S)
            fetched.append(_fetch_wikipedia(wt))

    if body == "ISO-IEC":
        iso_title, iso_purpose = _infer_iso_title(designation)
        if iso_title and not result.get("title"):
            result["title"] = iso_title
            result["purpose"] = iso_purpose
            result["sources"].append("iso_series")

    fetched.append(_static_hint(body, designation))

    wiki_used = False
    for item in fetched:
        if not item:
            continue
        if item.get("title") and not result.get("title"):
            result["title"] = item["title"]
        if item.get("description"):
            desc = item["description"]
            if not result.get("purpose"):
                result["purpose"] = _first_sentence(desc)
            result.setdefault("description", desc)
            if body in {"ISO-IEC", "ITU-T", "IEEE", "CEN"} and len(desc) > 40:
                wiki_used = True
    if wiki_used and "wikipedia" not in result["sources"]:
        result["sources"].append("wikipedia")
    if body == "IETF" and any(fetched):
        result["sources"].append("rfc_editor")
    if body == "ITU-T" and any(fetched):
        if "itu" not in result["sources"]:
            result["sources"].append("itu")

    cache[key] = result
    _save_cache(root)
    return result


def build_unavailable_summary(doc: dict[str, Any]) -> str | None:
    """Compose summary for specs without a local artifact."""
    parts: list[str] = []
    title = doc.get("title")
    designation = doc.get("designation") or ""
    body = doc.get("body") or ""

    if title and title != designation:
        parts.append(str(title).strip())
    else:
        label = " ".join(p for p in (body, designation) if p).strip()
        if label:
            parts.append(label)

    purpose = doc.get("purpose")
    if purpose:
        parts.append(str(purpose).strip())

    legal_ctx = doc.get("legal_citation_context") or []
    if legal_ctx:
        parts.append(_first_sentence(legal_ctx[0], max_len=280))

    legal = doc.get("parent_legal_regulations") or []
    if legal:
        acts = ", ".join(lp.get("id", "") for lp in legal[:4] if lp.get("id"))
        if acts:
            parts.append(f"Cited by EU act(s): {acts}.")

    reason = doc.get("reason")
    if reason and "licensed copy" not in " ".join(parts).lower():
        parts.append(str(reason).strip())
    elif reason and len(parts) < 2:
        parts.append(str(reason).strip())

    if not parts:
        return None
    text = " ".join(parts)
    if len(text) > 900:
        text = text[:899].rsplit(" ", 1)[0] + "…"
    return text


def enrich_catalogue_metadata(
    doc: dict[str, Any],
    *,
    legal_root: Path | None = None,
    cache_root: Path | None = None,
    use_network: bool = True,
) -> dict[str, Any]:
    """Add title, purpose, legal_citation_context, and catalogue_meta to a reference doc."""
    out = dict(doc)
    body = out.get("body") or ""
    designation = out.get("designation") or ""
    if not body or not designation:
        return out

    meta = lookup_catalogue_metadata(
        body, designation, cache_root=cache_root, use_network=use_network
    )
    sources = list(meta.get("sources") or [])

    if meta.get("title") and not out.get("title"):
        out["title"] = meta["title"]
    if meta.get("purpose") and not out.get("purpose"):
        out["purpose"] = meta["purpose"]

    legal_ctx = _extract_legal_context(out, legal_root)
    if legal_ctx:
        out["legal_citation_context"] = legal_ctx
        sources.append("legal_citation")

    catalogue_meta = {
        "fetched_at": _utc_now(),
        "sources": sources,
    }
    if meta.get("description"):
        catalogue_meta["description_excerpt"] = _first_sentence(meta["description"], 500)
    out["catalogue_meta"] = catalogue_meta

    if not out.get("scope_keywords"):
        from spec_summarizer import extract_scope_keywords

        purpose = (out.get("purpose") or "").strip()
        out["scope_keywords"] = extract_scope_keywords(
            purpose,
            purpose or None,
            [],
            out.get("tags") or [],
            designation,
            out.get("parent_legal_regulations") or [],
        )

    return out
