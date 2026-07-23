"""
EUDI ARF complementary technical specifications (EC TS series).

Index (catalogue): eu-digital-identity-wallet/eudi-doc-architecture-and-reference-framework
Content (full markdown): eu-digital-identity-wallet/eudi-doc-standards-and-technical-specifications
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from references import ExtractionResult, SpecReference, extract_from_text

import os

USER_AGENT = "Wallet-Presentations/1.0 (+https://github.com/peppelinux/Wallet-Presentations; arf-ts-sync)"

ARF_REPO = "eu-digital-identity-wallet/eudi-doc-architecture-and-reference-framework"
CONTENT_REPO = "eu-digital-identity-wallet/eudi-doc-standards-and-technical-specifications"
DOCS_PATH = "docs/technical-specifications"

# ARF framework release tag (index / stubs). Content repo has no release tags — use main.
ARF_REF = os.environ.get("ARF_REF", "v3.0.0")
CONTENT_REF = os.environ.get("ARF_CONTENT_REF", "main")

ARF_INDEX_TREE = (
    f"https://github.com/{ARF_REPO}/tree/{ARF_REF}/{DOCS_PATH}"
)
ARF_RAW_BASE = f"https://raw.githubusercontent.com/{ARF_REPO}/{ARF_REF}/{DOCS_PATH}"
CONTENT_RAW_BASE = (
    f"https://raw.githubusercontent.com/{CONTENT_REPO}/{CONTENT_REF}/{DOCS_PATH}"
)
ARF_RELEASE_URL = (
    f"https://github.com/{ARF_REPO}/releases/tag/{ARF_REF}"
)

_STUB_REPO_RE = re.compile(
    r"github\.com/eu-digital-identity-wallet/eudi-doc-standards-and-technical-specifications"
    r"/blob/[^/]+/docs/technical-specifications/([^\s\)\"']+\.md)",
    re.I,
)
_VERSION_ROW_RE = re.compile(
    r"^\|\s*`([^`]+)`\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|",
    re.M,
)
_TS_FILE_RE = re.compile(r"^ts(\d{1,2})-.+\.md$", re.I)

ARF_SOURCE_PREFIX = "ARF/technical-specifications/"

# Fallback when GitHub API is unavailable
_STATIC_FILES: list[tuple[str, str]] = [
    ("TS01", "ts1-eudi-wallet-trust-mark.md", "Specification of EUDI Wallet Trust Mark"),
    ("TS02", "ts2-notification-publication-provider-information.md", "Specification of systems enabling notification and publication of Provider information"),
    ("TS03", "ts3-wallet-unit-attestation.md", "Specification of Wallet Unit Attestations (WUA)"),
    ("TS04", "ts4-zkp.md", "Specification of Zero-Knowledge Proof (ZKP) Implementation in EUDI Wallet"),
    ("TS05", "ts5-common-formats-and-api-for-rp-registration-information.md", "Specification of common formats and API for Relying Party Registration information"),
    ("TS06", "ts6-common-set-of-rp-information-to-be-registered.md", "Specification of common set of Relying Party information to be registered"),
    ("TS07", "ts7-common-interface-for-data-deletion-request.md", "Specification of common interface for data deletion requests to Relying Parties"),
    ("TS08", "ts8-common-interface-for-reporting-of-wrp-to-dpa.md", "Specification of common interface for lodging complaints to DPAs"),
    ("TS09", "ts9-wallet-to-wallet-interactions.md", "Specification of Wallet-to-Wallet interactions"),
    ("TS10", "ts10-data-portability-and-download-(export).md", "Specification of Data Portability and Download (export)"),
    ("TS11", "ts11-interfaces-and-formats-for-catalogue-of-attributes-and-catalogue-of-schemes.md", "Specification of interfaces and formats for the catalogue of Attestation Rulebooks and attributes"),
    ("TS12", "ts12-electronic-payments-SCA-implementation-with-wallet.md", "Specification of electronic payments SCA implementation with the Wallet"),
    ("TS13", "ts13-zksnarks.md", "Specification of zk-SNARKs"),
    ("TS14", "ts14-zkps-from-mms.md", "Specification of ZKPs from Merkle Membership Statements"),
]


@dataclass(frozen=True)
class ArfTechnicalSpec:
    designation: str
    filename: str
    title: str

    @property
    def source(self) -> str:
        return f"ARF/technical-specifications/{self.filename}"

    @property
    def ref(self) -> SpecReference:
        return SpecReference(
            body="ARF",
            designation=self.designation,
            title=self.title,
        )

    def arf_tree_url(self) -> str:
        return f"{ARF_INDEX_TREE}/{urllib.parse.quote(self.filename)}"

    def arf_raw_url(self) -> str:
        return f"{ARF_RAW_BASE}/{urllib.parse.quote(self.filename)}"

    def content_raw_url(self) -> str:
        name = self.filename
        if name == "ts13-zksnark.md":
            name = "ts13-zksnarks.md"
        return f"{CONTENT_RAW_BASE}/{urllib.parse.quote(name)}"

    def content_blob_url(self) -> str:
        """Human-readable GitHub page for the full TS markdown (preferred href)."""
        name = self.filename
        if name == "ts13-zksnark.md":
            name = "ts13-zksnarks.md"
        return (
            f"https://github.com/{CONTENT_REPO}/blob/{CONTENT_REF}/"
            f"{DOCS_PATH}/{urllib.parse.quote(name)}"
        )


def _http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _list_ts_files_via_api() -> list[str] | None:
    api = (
        f"https://api.github.com/repos/{ARF_REPO}/contents/{DOCS_PATH}?ref={ARF_REF}"
    )
    try:
        data = json.loads(_http_get(api, timeout=30).decode("utf-8"))
    except Exception:
        return None
    names: list[str] = []
    for entry in data:
        if entry.get("type") != "file":
            continue
        name = entry.get("name") or ""
        if _TS_FILE_RE.match(name):
            names.append(name)
    return sorted(names) if names else None


def _title_from_filename(filename: str) -> str:
    m = _TS_FILE_RE.match(filename)
    if not m:
        return filename
    slug = filename[m.end() : -3].replace("-", " ").replace("(export)", "(export)")
    return slug[:1].upper() + slug[1:] if slug else filename


def designation_from_ts_filename(filename: str) -> str | None:
    m = _TS_FILE_RE.match(filename)
    if not m:
        return None
    return f"TS{int(m.group(1)):02d}"


def storage_folder_for_designation(
    designation: str,
    standards_root: Path | None = None,
) -> str | None:
    """Resolve on-disk folder name (e.g. TS03-V1.5.1) for a TS designation."""
    prefix = designation.strip().upper()
    if standards_root is not None:
        arf_dir = standards_root / "ARF"
        if arf_dir.is_dir():
            for path in sorted(arf_dir.iterdir()):
                if path.is_dir() and path.name.upper().startswith(prefix):
                    return path.name
    return prefix


def resolve_body_folder_from_source(
    source: str,
    standards_root: Path | None = None,
) -> tuple[str, str] | None:
    """
    Map ARF/technical-specifications/tsN-….md sources to standards/ARF/<folder>/.
    """
    norm = source.replace("\\", "/").lstrip("/")
    for marker in ("referenced-standards/standards/", "standards/"):
        if norm.startswith(marker):
            norm = norm[len(marker) :]
            break
    if not norm.startswith(ARF_SOURCE_PREFIX):
        return None
    filename = Path(norm).name
    designation = designation_from_ts_filename(filename)
    if not designation:
        return None
    folder = storage_folder_for_designation(designation, standards_root)
    return ("ARF", folder) if folder else None


def catalog() -> list[ArfTechnicalSpec]:
    """TS01–TS14 from the ARF technical-specifications folder (ARF release tag)."""
    files = _list_ts_files_via_api()
    if files:
        out: list[ArfTechnicalSpec] = []
        for name in files:
            m = _TS_FILE_RE.match(name)
            if not m:
                continue
            des = f"TS{m.group(1).zfill(2)}"
            out.append(
                ArfTechnicalSpec(
                    designation=des,
                    filename=name,
                    title=_title_from_filename(name),
                )
            )
        return out

    return [
        ArfTechnicalSpec(designation=d, filename=f, title=t)
        for d, f, t in _STATIC_FILES
    ]


ARF_CATALOG_DESIGNATION = "EC technical specifications (TS01–TS14)"


def write_catalogue_reference(standards_root: Path) -> Path:
    """
    Write standards/ARF/reference.json — index for the EC TS catalogue (not a single TS).
    """
    arf_dir = standards_root / "ARF"
    arf_dir.mkdir(parents=True, exist_ok=True)

    children: list[dict[str, str | None]] = []
    for entry in catalog():
        folder = storage_folder_for_designation(entry.designation, standards_root)
        if not folder:
            continue
        child: dict[str, str | None] = {
            "designation": entry.designation,
            "folder": f"ARF/{folder}",
            "filename": entry.filename,
        }
        ref_path = arf_dir / folder / "reference.json"
        if ref_path.is_file():
            try:
                meta = json.loads(ref_path.read_text(encoding="utf-8"))
                child["version"] = meta.get("version")
                child["title"] = meta.get("title")
            except (json.JSONDecodeError, OSError):
                pass
        children.append(child)

    doc = {
        "body": "ARF",
        "designation": ARF_CATALOG_DESIGNATION,
        "version": ARF_REF.lstrip("v"),
        "title": "EUDI Wallet ARF complementary technical specifications",
        "status": "downloaded",
        "download_url": ARF_INDEX_TREE,
        "download_urls": [
            ARF_RELEASE_URL,
            ARF_INDEX_TREE,
            f"https://github.com/{CONTENT_REPO}/tree/{CONTENT_REF}/{DOCS_PATH}",
        ],
        "tags": ["arf-technical-spec"],
        "summary": (
            f"European Commission complementary technical specifications (TS01–TS14) "
            f"aligned with EUDI Wallet Architecture and Reference Framework {ARF_REF}; "
            "each TS is synced under standards/ARF/<designation>-<version>/ "
            f"(index: {ARF_REPO}@{ARF_REF}; full text: {CONTENT_REPO}@{CONTENT_REF})."
        ),
        "scope_keywords": [
            "attestation",
            "eudi",
            "pid",
            "trust",
            "wallet",
        ],
        "parent_legal_regulations": [],
        "parent_specifications": [],
        "child_specifications": children,
        "arf_ref": ARF_REF,
        "content_ref": CONTENT_REF,
    }
    path = arf_dir / "reference.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def collect_into(result: ExtractionResult) -> list[ArfTechnicalSpec]:
    specs = catalog()
    for entry in specs:
        result.add(entry.ref, entry.source)
    return specs


def resolve_content_url(arf_markdown: str, filename: str) -> str:
    """Prefer full text from the standards-and-technical-specifications repo."""
    m = _STUB_REPO_RE.search(arf_markdown)
    if m:
        return f"{CONTENT_RAW_BASE}/{urllib.parse.quote(m.group(1))}"
    if len(arf_markdown.strip()) < 800 and "standards-and-technical-specifications" in arf_markdown:
        return f"{CONTENT_RAW_BASE}/{urllib.parse.quote(filename)}"
    # ARF index uses ts13-zksnark.md; content repo publishes ts13-zksnarks.md
    if filename == "ts13-zksnark.md":
        return f"{CONTENT_RAW_BASE}/{urllib.parse.quote('ts13-zksnarks.md')}"
    return f"{ARF_RAW_BASE}/{urllib.parse.quote(filename)}"


def fetch_markdown(entry: ArfTechnicalSpec) -> tuple[str, str]:
    """Return (markdown text, download_url used for the file)."""
    arf_text = _http_get(entry.arf_raw_url()).decode("utf-8", errors="replace")
    url = resolve_content_url(arf_text, entry.filename)
    if url != entry.arf_raw_url():
        text = _http_get(url).decode("utf-8", errors="replace")
    else:
        text = arf_text
    return text, url


def parse_latest_version(markdown: str) -> str | None:
    versions: list[tuple[str, str]] = []
    for m in _VERSION_ROW_RE.finditer(markdown):
        versions.append((m.group(1), m.group(2)))
    if not versions:
        return None
    return versions[-1][0]


def parse_title_from_markdown(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return fallback


def download_urls_for(entry: ArfTechnicalSpec) -> list[str]:
    return [
        entry.content_blob_url(),
        entry.arf_tree_url(),
        entry.content_raw_url(),
        entry.arf_raw_url(),
    ]


def extract_nested_references(
    markdown: str,
    entry: ArfTechnicalSpec,
    result: ExtractionResult,
) -> None:
    extract_from_text(markdown, entry.source, result)
