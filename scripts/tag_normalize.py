"""Canonical tags: small vocabulary for filtering (project is already eIDAS / EUDI wallet scope)."""

from __future__ import annotations

import re
from typing import Iterable

# Tags worth keeping — SDO is a separate search filter; omit wallet/eudi and generic labels.
ALLOWED_TAGS = frozenset(
    {
        # EU legal corpus
        "eu-legal-act",
        "regulation",
        "implementing-regulation",
        "implementing-decision",
        "implementing-act",
        # How a spec entered the corpus
        "cited-by-eu-law",
        "nested-reference",
        # Download state
        "downloaded",
        "unchanged",
        "unavailable",
        # ETSI / trust domain (series-level, not per-SDO)
        "119-series",
        "319-series",
        "trust-services",
        "common-criteria",
        # Search index: full specification text chunks
        "document-text",
        # EUDI ARF complementary technical specifications (EC TS series)
        "arf-technical-spec",
    }
)

# Aliases → canonical (must be in ALLOWED_TAGS or dropped)
TAG_ALIASES: dict[str, str] = {
    "implementing-regulations": "implementing-regulation",
    "implementing_regulation": "implementing-regulation",
    "implementing_regulations": "implementing-regulation",
    "implementing-decisions": "implementing-decision",
    "implementing_decision": "implementing-decision",
    "implementing_decisions": "implementing-decision",
    "implementing-acts": "implementing-act",
    "implementing_acts": "implementing-act",
    "regulations": "regulation",
}


def _base_normalize(tag: str) -> str:
    t = str(tag).strip().lower()
    t = t.replace("_", "-")
    t = re.sub(r"-+", "-", t)
    return t.strip("-")


def normalize_tag(tag: str) -> str | None:
    """Map to canonical form, or None if not in the allowed vocabulary."""
    t = _base_normalize(tag)
    if not t:
        return None
    t = TAG_ALIASES.get(t, t)
    return t if t in ALLOWED_TAGS else None


def normalize_tags(tags: Iterable[str]) -> list[str]:
    """Deduplicate, canonicalize, and keep only allowed tags."""
    canonical: set[str] = set()
    for tag in tags:
        c = normalize_tag(tag)
        if c:
            canonical.add(c)
    return sorted(canonical)


def legal_act_tags(kind: str | None, section: str) -> list[str]:
    """Tags for EU legal markdown chunks."""
    raw: list[str] = ["eu-legal-act"]
    if kind:
        raw.append(kind)
    elif section == "regulation":
        raw.append("regulation")
    return normalize_tags(raw)
