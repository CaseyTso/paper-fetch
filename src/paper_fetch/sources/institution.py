"""Institutional access via EasyConnect/aTrust SOCKS5 proxy."""

from __future__ import annotations

from pathlib import Path
import warnings

import requests
from urllib3.exceptions import InsecureRequestWarning

from ..config import Config
from ..models import PaperIdentity, SourceResult, Status
from ..pdf import download_candidate, extract_pdf_candidates


class InstitutionSource:
    """Discover and download entitled PDF through a configured SOCKS5 proxy."""

    name = "institution"

    def __init__(self, session: requests.Session, config: Config) -> None:
        self._session = session
        self._config = config
        self._timeout = config.request_timeout

    def fetch(self, identity: PaperIdentity, destination: Path) -> SourceResult:
        socks5 = self._config.institution_socks5
        if not socks5:
            return SourceResult.failure(
                source=self.name,
                status=Status.CONFIGURATION_ERROR,
                detail="institution_socks5 not configured",
            )

        if not identity.doi:
            return SourceResult.failure(
                source=self.name,
                status=Status.NOT_FOUND,
                detail="no DOI for institution access",
            )

        proxies = {"http": socks5, "https": socks5}

        # 1. Follow DOI to landing page
        doi_url = f"https://doi.org/{identity.doi}"
        try:
            with warnings.catch_warnings():
                if self._config.institution_tls_verify is False:
                    warnings.simplefilter("ignore", InsecureRequestWarning)
                resp = self._session.get(
                    doi_url,
                    timeout=self._timeout,
                    proxies=proxies,
                    allow_redirects=True,
                    verify=self._config.institution_tls_verify,
                )
        except requests.exceptions.ProxyError:
            return SourceResult.failure(
                source=self.name,
                status=Status.PROXY_UNAVAILABLE,
                detail="SOCKS5 proxy connection failed",
            )
        except requests.exceptions.Timeout:
            return SourceResult.failure(
                source=self.name, status=Status.TIMEOUT, detail="DOI resolution timed out"
            )
        except requests.RequestException as exc:
            return SourceResult.failure(
                source=self.name,
                status=Status.NETWORK_ERROR,
                detail=f"request failed: {exc}",
            )

        final_url = resp.url
        content_type = resp.headers.get("content-type", "").lower()

        # 2. Direct PDF response
        if "application/pdf" in content_type:
            dest = destination
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with dest.open("wb") as fh:
                    fh.write(resp.content)
            except Exception:
                return SourceResult.failure(
                    source=self.name, status=Status.NETWORK_ERROR, detail="write failed"
                )
            from ..pdf import validate_pdf
            return validate_pdf(dest)

        # 3. HTML page — extract candidates
        html = resp.text[:200_000]  # reasonable upper bound
        candidates = extract_pdf_candidates(html, final_url)

        # Check for login wall
        lower = html[:5000].lower()
        if any(sig in lower for sig in ("sign in", "log in", "institution", "shibboleth", "carsi")):
            # Could be a login page — try anyway, but note it
            pass

        for pdf_url in candidates[:3]:
            result = download_candidate(
                session=self._session,
                url=pdf_url,
                destination=destination,
                source=self.name,
                timeout=self._timeout,
                proxies=proxies,
                verify=self._config.institution_tls_verify,
            )
            if result.success:
                return result

        return SourceResult.failure(
            source=self.name,
            status=Status.NO_PDF,
            detail=f"no PDF found on {final_url}",
        )
