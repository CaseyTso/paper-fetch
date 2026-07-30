"""Single-paper sequential acquisition pipeline."""

from __future__ import annotations

import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from .config import Config
from .models import Attempt, FetchResult, PaperIdentity, Status
from .pdf import safe_pdf_filename, validate_pdf
from .resolver import ResolutionError, Resolver
from .sources import Source
from .zotero import ZoteroClient, attach_pdf, upsert_item


class Pipeline:
    """Orchestrate identifier resolution → source cascade → Zotero attachment."""

    def __init__(
        self,
        *,
        config: Config,
        sources: list[Source],
        zotero: ZoteroClient | None = None,
    ) -> None:
        self._config = config
        self._sources = sources
        self._zotero = zotero
        self._session = requests.Session()
        self._resolver = Resolver(self._session, config)

    def fetch(
        self,
        raw: str,
        *,
        output_dir: Path | None = None,
        force: bool = False,
        no_zotero: bool = False,
    ) -> FetchResult:
        """Resolve *raw*, acquire PDF, and attach to Zotero.

        Returns ``FetchResult`` regardless of outcome — callers decide how to
        present it (CLI, JSON, Skill).
        """
        attempts: list[Attempt] = []
        out = output_dir or self._config.output_dir

        # 1. Resolve
        try:
            identity = self._resolver.resolve_with_zotero(raw, self._zotero)
        except ResolutionError as exc:
            return FetchResult(
                success=False,
                attempts=[Attempt(source="resolver", status=exc.status, detail=exc.detail)],
                error=f"resolution failed: {exc.detail}",
            )

        # 2. Short-circuit: existing Zotero PDF
        if not force and not no_zotero and self._zotero is not None:
            try:
                existing = self._zotero.find_pdf_attachment(identity.zotero_item_key or "")
                if existing:
                    return FetchResult(
                        success=True,
                        source="zotero-cache",
                        identity=identity,
                        zotero_item_key=identity.zotero_item_key,
                        attempts=[Attempt(source="zotero-cache", status=Status.SUCCESS)],
                    )
                # Also check by DOI lookup if no item key
                if identity.doi and not identity.zotero_item_key:
                    items = self._zotero.find_by_doi(identity.doi)
                    for item in items:
                        key = item.get("key")
                        if key and self._zotero.find_pdf_attachment(key):
                            identity.zotero_item_key = key
                            return FetchResult(
                                success=True,
                                source="zotero-cache",
                                identity=identity,
                                zotero_item_key=key,
                                attempts=[Attempt(source="zotero-cache", status=Status.SUCCESS)],
                            )
            except Exception:
                pass  # Zotero lookup is best-effort preflight

        # 3. Sequential source cascade
        filename = safe_pdf_filename(identity)
        dest = out / filename
        out.mkdir(parents=True, exist_ok=True)

        for source in self._sources:
            t0 = _time.monotonic()
            result = source.fetch(identity, dest)
            elapsed_ms = int((_time.monotonic() - t0) * 1000)
            attempts.append(
                Attempt(
                    source=source.name,
                    status=result.status,
                    detail=result.detail,
                    elapsed_ms=elapsed_ms,
                )
            )
            if result.success and result.temporary_path is not None:
                # Source already moved the file; validate it's still good
                validation = validate_pdf(dest)
                if validation.success:
                    source_name = source.name
                    break
                else:
                    # Invalid — record and continue to next source
                    attempts[-1].status = validation.status
                    attempts[-1].detail = validation.detail
        else:
            # All sources exhausted
            return FetchResult(
                success=False,
                identity=identity,
                attempts=attempts,
                error="all_sources_failed",
            )

        # 4. Zotero attachment
        zotero_key = identity.zotero_item_key
        if not no_zotero and self._zotero is not None:
            try:
                zotero_key = upsert_item(self._zotero, identity)
                if zotero_key:
                    attach_pdf(self._zotero, zotero_key, dest)
                    # Add to the configured 00 inbox collection when available
                    collection_key = self._config.zotero_inbox_collection_key
                    if collection_key:
                        self._zotero._session.post(
                            f"{self._zotero._base}/collections/{collection_key}/items",
                            headers={
                                "Zotero-API-Key": self._zotero._headers["Zotero-API-Key"],
                                "Content-Type": "text/plain",
                            },
                            data=zotero_key,
                            timeout=15,
                        )
            except Exception as exc:
                # PDF was downloaded — report partial success
                return FetchResult(
                    success=True,
                    source=source_name,
                    pdf_path=dest,
                    identity=identity,
                    zotero_item_key=zotero_key,
                    attempts=attempts,
                    error=f"zotero_write_failed: {exc}",
                )

        return FetchResult(
            success=True,
            source=source_name,
            pdf_path=dest,
            identity=identity,
            zotero_item_key=zotero_key,
            attempts=attempts,
        )
