"""Open-access sources: PMC, Europe PMC, Unpaywall."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import requests

from ..config import Config
from ..models import PaperIdentity, SourceResult, Status
from ..pdf import download_candidate


class OpenAccessSource:
    """PMC → Europe PMC → Unpaywall cascade."""

    name = "open_access"

    def __init__(self, session: requests.Session, config: Config) -> None:
        self._session = session
        self._config = config
        self._timeout = config.request_timeout

    def fetch(self, identity: PaperIdentity, destination: Path) -> SourceResult:
        # 1. PMC direct
        if identity.pmcid:
            result = self._try_pmc(identity.pmcid, destination)
            if result.success:
                return result

        # 2. Europe PMC (by DOI) — also captures PMID for PubMed linkout
        epmc_pmid: str | None = None
        if identity.doi:
            result, epmc_pmid = self._try_europepmc(identity.doi, destination)
            if result.success:
                return result

        # 3. PubMed full-text links (by PMID from Europe PMC or identity)
        pmid = epmc_pmid or identity.pmid
        if pmid:
            result = self._try_pubmed_linkout(pmid, destination)
            if result.success:
                return result

        # 4. Convert PMCID via NCBI if we have one but no DOI
        if identity.pmcid and not identity.doi:
            result = self._try_pmc(identity.pmcid, destination)
            if result.success:
                return result

        # 5. Unpaywall
        if identity.doi:
            return self._try_unpaywall(identity.doi, destination)

        return SourceResult.failure(
            source=self.name, status=Status.NOT_FOUND, detail="no OA identifiers"
        )

    # -- PMC ----------------------------------------------------------------

    def _try_pmc(self, pmcid: str, destination: Path) -> SourceResult:
        # Try Europe PMC render (more reliable than NCBI from many IPs)
        url = f"https://europepmc.org/articles/{pmcid}?pdf=render"
        result = download_candidate(
            session=self._session,
            url=url,
            destination=destination,
            source="pmc",
            timeout=self._timeout,
        )
        if result.success:
            return result
        # Fallback to NCBI direct
        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
        return download_candidate(
            session=self._session,
            url=url,
            destination=destination,
            source="pmc",
            timeout=self._timeout,
        )

    # -- Europe PMC ---------------------------------------------------------

    def _try_europepmc(self, doi: str, destination: Path) -> tuple[SourceResult, str | None]:
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query=DOI:{quote(doi)}&format=json&pageSize=3"
        )
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return SourceResult.failure(
                source="europepmc", status=Status.NETWORK_ERROR, detail="request failed"
            ), None

        results = data.get("resultList", {}).get("result", [])
        first_pmid: str | None = None
        for r in results:
            # Capture PMID from first result for PubMed fallback
            if first_pmid is None:
                first_pmid = r.get("pmid")

            # Check fullTextUrlList for direct PDF URLs
            ft_list = r.get("fullTextUrlList", {}).get("fullTextUrl", [])
            if isinstance(ft_list, dict):
                ft_list = [ft_list]
            for entry in ft_list:
                if not isinstance(entry, dict):
                    continue
                pdf_url = entry.get("url", "")
                style = str(entry.get("documentStyle", "")).lower()
                if not pdf_url or style != "pdf":
                    continue
                result = download_candidate(
                    session=self._session,
                    url=pdf_url,
                    destination=destination,
                    source="europepmc",
                    timeout=self._timeout,
                )
                if result.success:
                    return result, first_pmid

            # Check fullTextIdList for PMC IDs
            ft_id_list = r.get("fullTextIdList", {}).get("fullTextId", [])
            if isinstance(ft_id_list, str):
                ft_id_list = [ft_id_list]
            for ft_id in ft_id_list:
                if ft_id.upper().startswith("PMC"):
                    result = self._try_pmc(ft_id, destination)
                    if result.success:
                        result.source = "europepmc"
                        return result, first_pmid

        return SourceResult.failure(
            source="europepmc", status=Status.NOT_FOUND, detail="no PDF in results"
        ), first_pmid

    # -- PubMed linkout ------------------------------------------------------

    def _try_pubmed_linkout(self, pmid: str, destination: Path) -> SourceResult:
        """Extract full-text PDF links from the PubMed page's 'Full text links' section."""
        import re

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
        except Exception:
            return SourceResult.failure(
                source="pubmed", status=Status.NETWORK_ERROR, detail="request failed"
            )

        html = resp.text
        # Extract links from the full-text-links section
        pdf_urls: list[str] = []

        # Pattern 1: direct .pdf links in full-text-links div
        for m in re.finditer(
            r'full-text-links[^>]*>.*?</div', html, re.DOTALL | re.IGNORECASE
        ):
            block = m.group(0)
            for link in re.findall(r'href="(https?://[^"]+)"', block, re.IGNORECASE):
                pdf_urls.append(link)

        # Pattern 2: any link containing 'pdf' in the full-text section
        if not pdf_urls:
            ft_section = re.search(
                r'<div class="full-text-links[^"]*">(.*?)</div>\s*</div>',
                html, re.DOTALL | re.IGNORECASE
            )
            if ft_section:
                for link in re.findall(r'href="(https?://[^"]+)"', ft_section.group(0)):
                    if link not in pdf_urls:
                        pdf_urls.append(link)

        for pdf_url in pdf_urls[:3]:
            result = download_candidate(
                session=self._session,
                url=pdf_url,
                destination=destination,
                source="pubmed",
                timeout=self._timeout,
            )
            if result.success:
                return result

        return SourceResult.failure(
            source="pubmed", status=Status.NOT_FOUND,
            detail="no PDF in PubMed full-text links" if pdf_urls else "no full-text links on PubMed"
        )

    # -- Unpaywall ----------------------------------------------------------

    def _try_unpaywall(self, doi: str, destination: Path) -> SourceResult:
        email = self._config.unpaywall_email
        if not email:
            return SourceResult.failure(
                source="unpaywall",
                status=Status.CONFIGURATION_ERROR,
                detail="unpaywall_email not set",
            )
        url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}?email={quote(email)}"
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return SourceResult.failure(
                source="unpaywall", status=Status.NETWORK_ERROR, detail="request failed"
            )

        candidates: list[str] = []

        # best_oa_location
        best = data.get("best_oa_location") or {}
        if isinstance(best, dict):
            pdf = best.get("url_for_pdf")
            if pdf:
                candidates.append(pdf)

        # oa_locations
        for loc in data.get("oa_locations", []):
            if not isinstance(loc, dict):
                continue
            pdf = loc.get("url_for_pdf")
            if pdf and pdf not in candidates:
                is_publisher = loc.get("host_type") == "publisher"
                if is_publisher:
                    candidates.append(pdf)  # publisher later
                else:
                    candidates.insert(1 if len(candidates) > 1 else len(candidates), pdf)

        for pdf_url in candidates[:2]:
            result = download_candidate(
                session=self._session,
                url=pdf_url,
                destination=destination,
                source="unpaywall",
                timeout=self._timeout,
            )
            if result.success:
                return result

        return SourceResult.failure(
            source="unpaywall", status=Status.NOT_FOUND, detail="no open PDF"
        )
