"""Shared result models and status enumeration."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Status(str, enum.Enum):
    """Structured outcome for every source attempt."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    NO_PDF = "no_pdf"
    INVALID_PDF = "invalid_pdf"
    SUSPICIOUS_PDF = "suspicious_pdf"
    AMBIGUOUS_IDENTIFIER = "ambiguous_identifier"
    PROXY_UNAVAILABLE = "proxy_unavailable"
    AUTHENTICATION_REQUIRED = "authentication_required"
    CHALLENGE_REQUIRED = "challenge_required"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    EXTERNAL_COMMAND_MISSING = "external_command_missing"
    ZOTERO_WRITE_FAILED = "zotero_write_failed"
    CONFIGURATION_ERROR = "configuration_error"
    NETWORK_ERROR = "network_error"


@dataclass
class PaperIdentity:
    """Unified paper identifier resolved from any input form."""

    original_input: str
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    year: str | None = None
    zotero_item_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_input": self.original_input,
            "doi": self.doi,
            "pmid": self.pmid,
            "pmcid": self.pmcid,
            "title": self.title,
            "authors": self.authors,
            "journal": self.journal,
            "year": self.year,
            "zotero_item_key": self.zotero_item_key,
        }


@dataclass
class SourceResult:
    """The outcome of one source attempt."""

    success: bool
    source: str
    status: Status
    temporary_path: Path | None = None
    url: str | None = None
    detail: str = ""

    @classmethod
    def success_result(
        cls, *, source: str, path: Path, url: str | None = None
    ) -> SourceResult:
        return cls(
            success=True,
            source=source,
            status=Status.SUCCESS,
            temporary_path=path,
            url=url,
        )

    @classmethod
    def failure(cls, *, source: str, status: Status, detail: str = "") -> SourceResult:
        return cls(success=False, source=source, status=status, detail=detail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source,
            "status": self.status.value,
            "detail": self.detail,
        }


@dataclass
class Attempt:
    """One recorded attempt for the final result report."""

    source: str
    status: Status
    detail: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status.value,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class FetchResult:
    """Complete pipeline result, serializable to JSON."""

    success: bool
    source: str = ""
    pdf_path: Path | None = None
    identity: PaperIdentity | None = None
    zotero_item_key: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source,
            "pdf_path": str(self.pdf_path) if self.pdf_path else None,
            "identity": self.identity.to_dict() if self.identity else None,
            "zotero_item_key": self.zotero_item_key,
            "attempts": [a.to_dict() for a in self.attempts],
            "error": self.error,
        }
