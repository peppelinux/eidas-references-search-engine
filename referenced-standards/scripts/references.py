"""Extract and normalize technical standard references from legal/spec text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ETSI EN/TS/TR/SR 319 401 V3.1.1 (2024-06)  |  ETSI TS 119 172-4 V1.1.1
ETSI_RE = re.compile(
    r"ETSI\s+(?P<type>EN|TS|TR|SR)\s+"
    r"(?P<num>(?:\d+\s*){1,3}\d+(?:-\d+)?)\s+"
    r"V(?P<ver>\d+(?:\.\d+)+)"
    r"(?:\s*\((?P<date>\d{4}-\d{2})\))?",
    re.IGNORECASE,
)

ISO_RE = re.compile(
    r"\bISO(?:/IEC)?(?:\s+IEEE)?\s*"
    r"(?P<num>\d[\d\-]*(?:\s*\(parts?\s*\d+(?:\s+to\s+\d+)?\))?)"
    r"(?::(?P<year>\d{4}))?",
    re.IGNORECASE,
)

RFC_RE = re.compile(
    r"\b(?:IETF\s+)?RFC\s*(?P<num>\d{3,5})\b",
    re.IGNORECASE,
)

CEN_RE = re.compile(
    r"\bCEN/(?P<type>EN|TS)\s*"
    r"(?P<num>[\d\s]+)"
    r"(?::(?P<year>\d{4}))?",
    re.IGNORECASE,
)

ITU_RE = re.compile(
    r"\bITU-T\s+(?:Recommendation\s+)?(?P<id>[A-Z]\.\d{3}(?:\.\d+)?(?:bis)?)",
    re.IGNORECASE,
)

IEEE_RE = re.compile(
    r"\bIEEE\s+(?:Std\s+)?(?P<num>[\d\-\.]+)",
    re.IGNORECASE,
)

W3C_NAMED_RE = re.compile(
    r"['\"]?(?P<title>Verifiable Credentials Data Model(?:\s+\d+\.\d+)?)['\"]?",
    re.IGNORECASE,
)

W3C_TR_RE = re.compile(
    r"\bW3C\s+(?:Recommendation|Note|Working Draft|Candidate Recommendation)",
    re.IGNORECASE,
)

# OpenID Foundation (OIDF) — EUDI wallet protocols / profiles
OPENID_URL_RE = re.compile(
    r"https?://openid\.net/specs/(?P<slug>[a-zA-Z0-9._\-]+?)(?:\.html)?\b",
    re.IGNORECASE,
)

OPENID_NAMED_RE = re.compile(
    r"""
    (?P<name>
        OpenID\s+for\s+Verifiable\s+Credential\s+Issuance
        | OpenID\s+for\s+Verifiable\s+Presentations?
        | OpenID\s+for\s+Verifiable\s+Credential\s+Presentation
        | OpenID4VC-HAIP
        | OpenID4VC\s+HAIP
        | OIDF\s+OpenID4VC\s+High\s+Assurance\s+Interoperability\s+Profile
        | OpenID4VC\s+High\s+Assurance\s+Interoperability\s+Profile
        | OpenID\s+Federation
        | OpenID4VCI
        | OID4VCI
        | OpenID4VP
        | OID4VP
    )
    (?:\s+v(?P<ver>\d+(?:\.\d+)*))?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_OPENID_NAME_TO_DESIGNATION = (
    ("for verifiable credential issuance", "OpenID4VCI"),
    ("for verifiable presentations", "OpenID4VP"),
    ("for verifiable credential presentation", "OpenID4VP"),
    ("high assurance interoperability", "OpenID4VC-HAIP"),
    ("openid4vc-haip", "OpenID4VC-HAIP"),
    ("openid4vc haip", "OpenID4VC-HAIP"),
    ("openid federation", "OpenID Federation"),
    ("openid4vci", "OpenID4VCI"),
    ("oid4vci", "OpenID4VCI"),
    ("openid4vp", "OpenID4VP"),
    ("oid4vp", "OpenID4VP"),
)


def _openid_from_slug(slug: str) -> tuple[str, str] | None:
    s = slug.lower().removesuffix(".html")
    if "verifiable-presentations" in s:
        return "OpenID4VP", "1.0"
    if "verifiable-credential-issuance" in s:
        return "OpenID4VCI", "1.0"
    if "high-assurance-interoperability" in s:
        return "OpenID4VC-HAIP", "1.0"
    if "openid-federation" in s or s.startswith("openid-federation"):
        return "OpenID Federation", "1.0"
    return None


def _openid_from_name(name: str, ver: str | None) -> SpecReference | None:
    key = re.sub(r"\s+", " ", name.strip().lower())
    designation = None
    for needle, des in _OPENID_NAME_TO_DESIGNATION:
        if needle in key:
            designation = des
            break
    if not designation:
        return None
    version = ver or "1.0"
    titles = {
        "OpenID4VP": "OpenID for Verifiable Presentations",
        "OpenID4VCI": "OpenID for Verifiable Credential Issuance",
        "OpenID4VC-HAIP": "OpenID4VC High Assurance Interoperability Profile",
        "OpenID Federation": "OpenID Federation",
    }
    return SpecReference(
        body="OpenID",
        designation=designation,
        version=version,
        title=titles.get(designation),
    )


@dataclass(frozen=True)
class SpecReference:
    body: str
    designation: str
    version: str | None = None
    date: str | None = None
    title: str | None = None

    @property
    def key(self) -> str:
        parts = [self.body, self.designation]
        if self.version:
            parts.append(f"V{self.version}")
        return "|".join(parts)


@dataclass
class ExtractionResult:
    references: dict[str, SpecReference] = field(default_factory=dict)
    sources: dict[str, set[str]] = field(default_factory=dict)

    def add(self, ref: SpecReference, source: str) -> None:
        k = ref.key
        if k not in self.references:
            self.references[k] = ref
        self.sources.setdefault(k, set()).add(source)


def _clean_iso_num(raw: str) -> str:
    s = re.sub(r"\s+", "", raw)
    s = re.sub(r"\(parts?\d+to\d+\)", "", s, flags=re.I)
    return s.strip("-")


def extract_from_text(text: str, source: str, result: ExtractionResult) -> None:
    for m in ETSI_RE.finditer(text):
        num = re.sub(r"\s+", " ", m.group("num").strip())
        result.add(
            SpecReference(
                body="ETSI",
                designation=f"{m.group('type').upper()} {num}",
                version=m.group("ver"),
                date=m.group("date"),
            ),
            source,
        )

    for m in ISO_RE.finditer(text):
        num = _clean_iso_num(m.group("num"))
        if len(num) < 3:
            continue
        year = m.group("year")
        designation = f"ISO/IEC {num}" if "IEC" in m.group(0).upper() else f"ISO {num}"
        result.add(
            SpecReference(
                body="ISO-IEC",
                designation=designation,
                version=year,
            ),
            source,
        )

    for m in RFC_RE.finditer(text):
        result.add(
            SpecReference(
                body="IETF",
                designation=f"RFC {m.group('num')}",
            ),
            source,
        )

    for m in CEN_RE.finditer(text):
        num = re.sub(r"\s+", "", m.group("num"))
        result.add(
            SpecReference(
                body="CEN",
                designation=f"CEN/{m.group('type').upper()} {num}",
                version=m.group("year"),
            ),
            source,
        )

    for m in ITU_RE.finditer(text):
        result.add(
            SpecReference(
                body="ITU-T",
                designation=f"ITU-T {m.group('id').upper()}",
            ),
            source,
        )

    for m in IEEE_RE.finditer(text):
        result.add(
            SpecReference(
                body="IEEE",
                designation=f"IEEE {m.group('num')}",
            ),
            source,
        )

    if W3C_TR_RE.search(text):
        for m in W3C_NAMED_RE.finditer(text):
            title = m.group("title").strip()
            if "verifiable credentials" in title.lower():
                result.add(
                    SpecReference(
                        body="W3C",
                        designation="vc-data-model",
                        version="1.1" if "1.1" in title else None,
                        title=title,
                    ),
                    source,
                )

    for m in OPENID_URL_RE.finditer(text):
        parsed = _openid_from_slug(m.group("slug"))
        if not parsed:
            continue
        designation, version = parsed
        result.add(
            SpecReference(
                body="OpenID",
                designation=designation,
                version=version,
            ),
            source,
        )

    for m in OPENID_NAMED_RE.finditer(text):
        ref = _openid_from_name(m.group("name"), m.group("ver"))
        if ref:
            result.add(ref, source)


def collect_from_paths(paths: list, legal_root) -> ExtractionResult:
    from pathlib import Path

    result = ExtractionResult()
    for path in paths:
        p = Path(path)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(legal_root)) if legal_root in p.parents or p == legal_root else p.name
        extract_from_text(text, rel, result)
    return result


def collect_from_legal_tree(legal_root) -> ExtractionResult:
    """Extract references from EU legal markdown only (not from downloaded standards)."""
    from pathlib import Path

    root = Path(legal_root)
    paths: list[Path] = []
    for section in ("regulation", "implementing-acts", "implementing-decisions"):
        base = root / section
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.suffix.lower() in {".md", ".txt"} and p.name != "README.md":
                paths.append(p)
    return collect_from_paths(paths, root)
