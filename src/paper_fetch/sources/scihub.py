"""Sci-Hub fallback via Clash HTTP proxy."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import requests

from ..config import Config
from ..models import PaperIdentity, SourceResult, Status
from ..pdf import download_candidate, extract_pdf_candidates


class SciHubSource:
    """Sequential Sci-Hub domain trial through Clash proxy."""

    name = "scihub"

    def __init__(self, session: requests.Session, config: Config) -> None:
        self._session = session
        self._config = config
        self._timeout = config.request_timeout

    def fetch(self, identity: PaperIdentity, destination: Path) -> SourceResult:
        if not identity.doi:
            return SourceResult.failure(
                source=self.name, status=Status.NOT_FOUND, detail="no DOI"
            )

        clash = self._config.clash_proxy
        if not clash:
            return SourceResult.failure(
                source=self.name,
                status=Status.CONFIGURATION_ERROR,
                detail="clash_proxy not configured",
            )

        domains = self._config.scihub_domains
        if not domains:
            return SourceResult.failure(
                source=self.name,
                status=Status.CONFIGURATION_ERROR,
                detail="no scihub domains configured",
            )

        proxies = {"http": clash, "https": clash}
        safe_doi = quote(identity.doi, safe="/")

        for domain in domains:
            landing = f"{domain.rstrip('/')}/{safe_doi}"
            result = self._try_domain(landing, destination, proxies)
            if result.success:
                return result
            # If challenge required, stop and signal manual action
            if result.status == Status.CHALLENGE_REQUIRED:
                return result

        return SourceResult.failure(
            source=self.name, status=Status.NOT_FOUND, detail="no domain succeeded"
        )

    def _try_domain(
        self, landing_url: str, destination: Path, proxies: dict[str, str]
    ) -> SourceResult:
        # 1. HTTP attempt
        try:
            resp = self._session.get(
                landing_url,
                timeout=self._timeout,
                proxies=proxies,
                allow_redirects=True,
                stream=True,
            )
        except requests.RequestException:
            return SourceResult.failure(
                source=self.name, status=Status.NETWORK_ERROR, detail="connection failed"
            )

        if resp.status_code >= 400:
            return SourceResult.failure(
                source=self.name, status=Status.NOT_FOUND, detail=f"HTTP {resp.status_code}"
            )

        # Check for direct PDF
        content_type = resp.headers.get("content-type", "").lower()
        if "application/pdf" in content_type:
            dest = destination
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with dest.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            fh.write(chunk)
            except Exception:
                return SourceResult.failure(
                    source=self.name, status=Status.NETWORK_ERROR, detail="write failed"
                )
            from ..pdf import validate_pdf
            return validate_pdf(dest)

        # Parse HTML
        html = resp.text[:200_000]
        lower = html.lower()

        # Article not found
        if any(sig in lower for sig in ("article not found", "статья не найдена", "не найден")):
            return SourceResult.failure(
                source=self.name, status=Status.NOT_FOUND, detail="article not in Sci-Hub"
            )

        # Cloudflare / ALTCHA
        if any(sig in lower for sig in ("checking your browser", "just a moment", "altcha", "not a robot")):
            return SourceResult.failure(
                source=self.name,
                status=Status.CHALLENGE_REQUIRED,
                detail="CAPTCHA or browser challenge detected — open browser to complete",
            )

        # Extract PDF candidates
        candidates = extract_pdf_candidates(html, landing_url)
        for pdf_url in candidates[:3]:
            result = download_candidate(
                session=self._session,
                url=pdf_url,
                destination=destination,
                source=self.name,
                timeout=self._timeout,
                proxies=proxies,
            )
            if result.success:
                return result

        return SourceResult.failure(
            source=self.name, status=Status.NO_PDF, detail="no PDF extracted"
        )
