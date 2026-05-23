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
