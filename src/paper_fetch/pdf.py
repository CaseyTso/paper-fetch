"""PDF validation, safe filenames, and HTML candidate extraction."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from pypdf import PdfReader

from .models import PaperIdentity, SourceResult, Status
from .config import Config


def validate_pdf(path: Path) -> SourceResult:
    """Check whether a file is a usable PDF.

    Returns ``SourceResult`` with appropriate status for success, invalid, or
    single-page suspicious cases.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return SourceResult.failure(
            source="pdf", status=Status.INVALID_PDF, detail="cannot stat file"
        )

    if size < 100:
        return SourceResult.failure(
            source="pdf",
            status=Status.INVALID_PDF,
            detail=f"file too small ({size} bytes)",
        )

    # Header check
    try:
        with path.open("rb") as fh:
            header = fh.read(20)  # enough for HTML detection
    except OSError:
        return SourceResult.failure(
            source="pdf", status=Status.INVALID_PDF, detail="cannot read file"
        )

    if not header.startswith(b"%PDF-"):
        if b"<!DOCTYPE html" in header:
            return SourceResult.failure(
                source="pdf",
                status=Status.INVALID_PDF,
                detail="HTML file disguised as PDF",
            )
        return SourceResult.failure(
            source="pdf", status=Status.INVALID_PDF, detail="no %PDF- header"
        )

    # Tail check
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, size - 1024))
            tail = fh.read()
        if b"%%EOF" not in tail:
            return SourceResult.failure(
                source="pdf", status=Status.INVALID_PDF, detail="missing %%EOF marker"
            )
    except OSError:
        return SourceResult.failure(
            source="pdf", status=Status.INVALID_PDF, detail="cannot read file tail"
        )

    # Page count
    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
    except Exception as exc:
        return SourceResult.failure(
            source="pdf",
            status=Status.INVALID_PDF,
            detail=f"pypdf cannot open: {exc}",
        )

    # Small single-page PDF is suspicious
    if page_count <= 1 and size < 100_000:
        return SourceResult.failure(
            source="pdf",
            status=Status.SUSPICIOUS_PDF,
            detail="single-page PDF, possibly cover or preview",
        )

    return SourceResult.success_result(source="pdf", path=path)


def safe_pdf_filename(identity: PaperIdentity) -> str:
    """Build a safe filename from identity metadata."""
    parts: list[str] = []

    if identity.authors:
        first_author = identity.authors[0].split(",")[0].strip()
        safe = re.sub(r"[^\w\-]", "_", first_author).strip("_")
        if safe:
            parts.append(safe)

    if identity.year:
        parts.append(identity.year)

    if identity.title:
        safe = re.sub(r"[^\w\-]", "_", identity.title)[:80].strip("_")
        parts.append(safe)
    elif identity.doi:
        safe = re.sub(r"[^\w\-]", "_", identity.doi).strip("_")
        parts.append(safe)
    else:
        parts.append("paper")

    name = "_".join(parts)[:200]
    return f"{name}.pdf"


class _PDFCandidateParser(HTMLParser):
    """Lightweight HTML parser that extracts PDF candidate URLs."""

    def __init__(self):
        super().__init__()
        self.candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}

        # citation_pdf_url meta
        if tag == "meta":
            name = attr_map.get("name", "").lower()
            if name == "citation_pdf_url":
                content = attr_map.get("content", "")
                if content:
                    self.candidates.append(content)

        # iframe, embed, object → src (object uses data=)
        if tag in ("iframe", "embed", "object"):
            src = attr_map.get("src") or attr_map.get("data", "")
            if src:
                self.candidates.append(src)

        # anchor → href (only .pdf)
        if tag == "a":
            href = attr_map.get("href", "")
            if href.lower().endswith(".pdf") or "/pdf" in href.lower():
                self.candidates.append(href)


def extract_pdf_candidates(html: str, base_url: str) -> list[str]:
    """Return deduplicated, absolute PDF candidate URLs from HTML."""
    parser = _PDFCandidateParser()
    parser.feed(html)
    seen: set[str] = set()
    result: list[str] = []
    for raw in parser.candidates:
        absolute = urljoin(base_url, raw)
        if absolute not in seen:
            seen.add(absolute)
            result.append(absolute)
    return result


# ---------------------------------------------------------------------------
# Streaming downloader
# ---------------------------------------------------------------------------

import requests  # noqa: E402
import time as _time  # noqa: E402
import warnings  # noqa: E402
from urllib3.exceptions import InsecureRequestWarning  # noqa: E402


def download_candidate(
    *,
    session: requests.Session,
    url: str,
    destination: Path,
    source: str,
    timeout: float,
    proxies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    verify: bool | str = True,
) -> SourceResult:
    """Stream *url* through *session*, validate, and atomically rename.

    On failure the ``.part`` temporary file is removed.  The returned
    ``SourceResult.source`` carries *source* so the pipeline can attribute
    success or failure.
    """
    req_headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) paper-fetch/0.1",
        "Accept": "application/pdf,*/*",
    }
    if headers:
        req_headers.update(headers)

    try:
        with warnings.catch_warnings():
            if verify is False:
                warnings.simplefilter("ignore", InsecureRequestWarning)
            resp = session.get(
                url,
                timeout=timeout,
                proxies=proxies,
                headers=req_headers or None,
                allow_redirects=True,
                stream=True,
                verify=verify,
            )
    except requests.exceptions.Timeout:
        return SourceResult.failure(
            source=source, status=Status.TIMEOUT, detail="connection timed out"
        )
    except requests.exceptions.ConnectionError as exc:
        return SourceResult.failure(
            source=source,
            status=Status.NETWORK_ERROR,
            detail=f"connection error: {exc}",
        )
    except requests.exceptions.RequestException as exc:
        return SourceResult.failure(
            source=source,
            status=Status.NETWORK_ERROR,
            detail=f"request failed: {exc}",
        )

    status_code = resp.status_code

    if status_code == 429:
        return SourceResult.failure(
            source=source, status=Status.RATE_LIMITED, detail="HTTP 429"
        )
    if status_code in (401, 403):
        return SourceResult.failure(
            source=source,
            status=Status.AUTHENTICATION_REQUIRED,
            detail=f"HTTP {status_code}",
        )
    if status_code == 404:
        return SourceResult.failure(
            source=source, status=Status.NOT_FOUND, detail="HTTP 404"
        )
    if status_code >= 400:
        return SourceResult.failure(
            source=source,
            status=Status.NETWORK_ERROR,
            detail=f"HTTP {status_code}",
        )

    # Stream to .part
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".part")
    try:
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        return SourceResult.failure(
            source=source,
            status=Status.NETWORK_ERROR,
            detail=f"download interrupted: {exc}",
        )

    # Validate
    result = validate_pdf(tmp)
    if result.success:
        try:
            tmp.replace(destination)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            return SourceResult.failure(
                source=source,
                status=Status.NETWORK_ERROR,
                detail=f"atomic rename failed: {exc}",
            )
        return SourceResult.success_result(source=source, path=destination, url=url)

    # Remove invalid file
    tmp.unlink(missing_ok=True)
    result.source = source
    return result
