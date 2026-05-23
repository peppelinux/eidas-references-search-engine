"""Resolve standard references to download URLs and local paths."""

from __future__ import annotations

import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from references import SpecReference

from arf_technical_specs import (
    ARF_INDEX_TREE,
    ArfTechnicalSpec,
    catalog as arf_catalog,
    download_urls_for as arf_download_urls,
    fetch_markdown,
)

USER_AGENT = "Wallet-Presentations/1.0 (+https://github.com/peppelinux/Wallet-Presentations; tech-specs-sync)"


@dataclass
class ResolveResult:
    status: str  # downloaded | unchanged | unavailable | error | skipped
    path: Path | None = None
    url: str | None = None
    download_urls: list[str] | None = None
    error: str | None = None
    reason: str | None = None
    extra_paths: list[Path] | None = None


def body_folder(body: str) -> str:
    mapping = {
        "ARF": "ARF",
        "ETSI": "ETSI",
        "ISO-IEC": "ISO-IEC",
        "IETF": "IETF",
        "W3C": "W3C",
        "CEN": "CEN",
        "ITU-T": "ITU-T",
        "IEEE": "IEEE",
    }
    return mapping.get(body, "other")


def safe_filename(ref: SpecReference) -> str:
    s = ref.designation.replace("/", "-").replace(" ", "-")
    if ref.version:
        s += f"-V{ref.version}"
    s = re.sub(r"[^\w\-.]+", "_", s)
    return s[:180]


def spec_dir(standards_root: Path, ref: SpecReference) -> Path:
    return standards_root / body_folder(ref.body) / safe_filename(ref)


def http_download(url: str, dest: Path, timeout: int = 120) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    dest.write_bytes(data)


def http_exists(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception:
        return False


def normalize_etsi_number(num_str: str) -> str:
    nums = [int(x) for x in re.findall(r"\d+", num_str)]
    if len(nums) >= 3:
        base = "".join(f"{n:03d}" for n in nums[:-1])
        return f"{base}{nums[-1]:02d}"
    if len(nums) == 2:
        return f"{nums[0]:03d}{nums[1]:03d}"
    return f"{nums[0]:06d}"


def etsi_range(docnum: str) -> str:
    n = int(docnum)
    if n >= 1_000_000:
        low = (n // 10000) * 100
    else:
        low = (n // 100) * 100
    return f"{low:06d}_{low + 99:06d}"


def etsi_version_path(ver: str) -> str:
    parts = ver.split(".")[:3]
    while len(parts) < 3:
        parts.append("0")
    return ".".join(f"{int(p):02d}" for p in parts)


def etsi_pdf_urls(ref: SpecReference) -> list[str]:
    m = re.match(
        r"(EN|TS|TR|SR)\s+((?:\d+\s*){1,3}\d+(?:-\d+)?)",
        ref.designation,
        re.I,
    )
    if not m or not ref.version:
        return []
    typ = m.group(1).lower()
    doc = normalize_etsi_number(m.group(2))
    rng = etsi_range(doc)
    vp = etsi_version_path(ref.version)
    vcompact = vp.replace(".", "")
    return [
        f"https://www.etsi.org/deliver/etsi_{typ}/{rng}/{doc}/{vp}_60/"
        f"{typ}_{doc}v{vcompact}p.pdf"
    ]


def rfc_urls(ref: SpecReference) -> list[tuple[str, str]]:
    m = re.search(r"(\d{3,5})", ref.designation)
    if not m:
        return []
    n = m.group(1)
    base = f"https://www.rfc-editor.org/rfc/rfc{n}"
    return [
        (f"{base}.txt", ".txt"),
        (f"{base}.pdf", ".pdf"),
    ]


W3C_KNOWN = {
    ("vc-data-model", "1.1"): [
        ("https://www.w3.org/TR/vc-data-model/", ".html"),
        ("https://www.w3.org/TR/vc-data-model-1.1/", ".html"),
    ],
}


def iso_catalogue_urls(ref: SpecReference) -> list[str]:
    """
    Catalogue links for ISO/IEC standards.

    Do not use https://www.iso.org/standard/{digits}.html — that path is ISO's
    internal catalogue id (e.g. /standard/17000.html is ISO 9328-2:1991, not ISO/IEC 17000).
  """
    des = ref.designation.strip()
    if not re.search(r"\bISO\b", des, re.I):
        des = f"ISO {des}"
    des = re.sub(r"ISO\s*/\s*IEC", "ISO/IEC", des, flags=re.I)
    query = urllib.parse.quote_plus(des)
    return [
        f"https://www.iso.org/search.html?q={query}",
    ]


def _arf_entry_for_ref(ref: SpecReference) -> ArfTechnicalSpec | None:
    m = re.match(r"TS(\d{1,2})$", ref.designation.strip(), re.I)
    if not m:
        return None
    des = f"TS{int(m.group(1)):02d}"
    for entry in arf_catalog():
        if entry.designation.upper() == des:
            return entry
    return None


def catalog_download_urls(ref: SpecReference) -> list[str]:
    """Known or inferred HTTPS download / catalogue URLs (may be empty)."""
    if ref.body == "ARF":
        entry = _arf_entry_for_ref(ref)
        return arf_download_urls(entry) if entry else [ARF_INDEX_TREE]
    if ref.body == "ETSI":
        return etsi_pdf_urls(ref) if ref.version else []
    if ref.body == "IETF":
        return [u for u, _ in rfc_urls(ref)]
    if ref.body == "W3C":
        ver = ref.version or "1.1"
        return [u for u, _ in W3C_KNOWN.get((ref.designation, ver), [])]
    if ref.body == "ISO-IEC":
        return iso_catalogue_urls(ref)
    if ref.body == "CEN":
        num = re.sub(r"^CEN/(?:EN|TS)\s*", "", ref.designation, flags=re.I).strip()
        return [
            f"https://standards.cencenelec.eu/dyn/www/f?p=204:32:0::::FSP_PROJECT,FSP_LANG_ID:{num},25",
            "https://www.cencenelec.eu/standards/",
        ]
    if ref.body == "ITU-T":
        rid = re.sub(r"^ITU-T\s*", "", ref.designation, flags=re.I).strip()
        return [f"https://www.itu.int/rec/T-REC-{rid.replace('.', '-')}/en"]
    if ref.body == "IEEE":
        return [f"https://standards.ieee.org/standard/{ref.designation.replace('IEEE ', '').replace(' ', '_')}.html"]
    return []


def extract_text_for_recursion(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".html"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf" and shutil.which("pdftotext"):
        try:
            out = subprocess.run(
                ["pdftotext", "-q", str(path), "-"],
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
            return out.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""
    return ""


def resolve_and_download(
    ref: SpecReference,
    standards_root: Path,
    *,
    force: bool = False,
) -> ResolveResult:
    dest_dir = spec_dir(standards_root, ref)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if ref.body == "ETSI":
        candidates = etsi_pdf_urls(ref)
        if not ref.version:
            return ResolveResult(
                "unavailable",
                reason="ETSI reference without version",
                download_urls=candidates,
            )
        for url in candidates:
            dest = dest_dir / f"{safe_filename(ref)}.pdf"
            if dest.exists() and not force:
                return ResolveResult(
                    "unchanged", dest, url, download_urls=candidates
                )
            if http_exists(url):
                try:
                    http_download(url, dest)
                    return ResolveResult(
                        "downloaded", dest, url, download_urls=candidates
                    )
                except Exception as exc:
                    return ResolveResult(
                        "error",
                        error=str(exc),
                        url=url,
                        download_urls=candidates,
                    )
        return ResolveResult(
            "unavailable",
            reason="ETSI PDF URL not found (tried deliver path patterns)",
            download_urls=candidates,
        )

    if ref.body == "IETF":
        extras: list[Path] = []
        primary: Path | None = None
        primary_url: str | None = None
        for url, ext in rfc_urls(ref):
            dest = dest_dir / f"{safe_filename(ref)}{ext}"
            if dest.exists() and not force:
                extras.append(dest)
                if ext == ".txt":
                    primary = dest
                    primary_url = url
                continue
            try:
                http_download(url, dest)
                extras.append(dest)
                if ext == ".txt":
                    primary = dest
                    primary_url = url
            except Exception:
                continue
        if primary:
            status = "unchanged" if not force and len(extras) == 2 else "downloaded"
            urls = [u for u, _ in rfc_urls(ref)]
            return ResolveResult(
                status,
                primary,
                primary_url,
                download_urls=urls,
                extra_paths=extras,
            )
        return ResolveResult(
            "unavailable",
            reason="RFC download failed",
            download_urls=[u for u, _ in rfc_urls(ref)],
        )

    if ref.body == "W3C":
        ver = ref.version or "1.1"
        urls = W3C_KNOWN.get((ref.designation, ver), [])
        for url, ext in urls:
            dest = dest_dir / f"{safe_filename(ref)}{ext}"
            if dest.exists() and not force:
                return ResolveResult(
                    "unchanged", dest, url, download_urls=[u for u, _ in urls]
                )
            try:
                http_download(url, dest)
                return ResolveResult(
                    "downloaded", dest, url, download_urls=[u for u, _ in urls]
                )
            except Exception:
                continue
        return ResolveResult(
            "unavailable",
            reason="W3C document not in local catalog; add URL to W3C_KNOWN in resolvers.py",
            download_urls=[u for u, _ in urls],
        )

    if ref.body in {"ISO-IEC", "CEN", "ITU-T", "IEEE"}:
        return ResolveResult(
            "unavailable",
            reason=f"{ref.body} standards typically require a licensed copy; not freely redistributable",
            download_urls=catalog_download_urls(ref),
        )

    if ref.body == "ARF":
        entry = _arf_entry_for_ref(ref)
        if not entry:
            return ResolveResult(
                "unavailable",
                reason="Unknown ARF technical specification designation",
                download_urls=[ARF_INDEX_TREE],
            )
        dest = dest_dir / f"{safe_filename(ref)}.md"
        urls = arf_download_urls(entry)
        if dest.exists() and not force:
            return ResolveResult(
                "unchanged", dest, urls[0], download_urls=urls
            )
        try:
            text, content_url = fetch_markdown(entry)
            dest.write_text(text, encoding="utf-8")
            return ResolveResult(
                "downloaded", dest, content_url, download_urls=urls
            )
        except Exception as exc:
            return ResolveResult(
                "error",
                error=str(exc),
                url=entry.content_raw_url(),
                download_urls=urls,
            )

    return ResolveResult(
        "unavailable",
        reason=f"No resolver for body {ref.body}",
        download_urls=[],
    )
