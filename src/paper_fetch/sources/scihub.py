"""Sci-Hub fallback via Clash HTTP proxy, with automatic ALTCHA solving."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import requests

from ..config import Config
from ..models import PaperIdentity, SourceResult, Status
from ..pdf import download_candidate, extract_pdf_candidates, validate_pdf
from .altcha import extract_challenge_id, solve_altcha

# NOTE: bare "altcha" is deliberately NOT a signature here — sci-hub.jp
# article pages embed an ALTCHA widget for their "report" form
# (challengeurl without an id), so the word alone is ambiguous. The
# DDoS-Guard challenge page always carries a concrete widget id
# (captcha/challenge/<digits>) and/or one of the phrases below.
_CHALLENGE_SIGNATURES = (
    "checking your browser",
    "just a moment",
    "not a robot",
    "あなたはロボット",
)
_ARTICLE_NOT_FOUND_SIGNATURES = (
    "article not found",
    "статья не найдена",
    "не найден",
)


class SciHubSource:
    """Sequential Sci-Hub domain trial through Clash proxy.

    Domains are tried in order. When a landing page presents an ALTCHA
    challenge, the proof-of-work is solved automatically and the page is
    retried with the resulting cookie; only a failed solve falls back to
    ``challenge_required``.
    """

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

        challenge_result: SourceResult | None = None
        last_result: SourceResult | None = None
        for domain in domains:
            landing = f"{domain.rstrip('/')}/{safe_doi}"
            result = self._try_domain(landing, destination, proxies)
            if result.success:
                return result
            if result.status == Status.CHALLENGE_REQUIRED:
                challenge_result = result
            else:
                last_result = result

        # Prefer the challenge signal (an unsolved captcha gate) over the
        # plain no-result outcome so the caller knows a manual step exists.
        if challenge_result is not None:
            return challenge_result
        return last_result or SourceResult.failure(
            source=self.name, status=Status.NOT_FOUND, detail="no domain succeeded"
        )

    def _try_domain(
        self, landing_url: str, destination: Path, proxies: dict[str, str]
    ) -> SourceResult:
        return self._try_landing(landing_url, destination, proxies, allow_solve=True)

    def _try_landing(
        self,
        landing_url: str,
        destination: Path,
        proxies: dict[str, str],
        *,
        allow_solve: bool,
    ) -> SourceResult:
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
            return validate_pdf(dest)

        # Parse HTML
        html = resp.text[:200_000]
        lower = html.lower()

        # Article not found
        if any(sig in lower for sig in _ARTICLE_NOT_FOUND_SIGNATURES):
            return SourceResult.failure(
                source=self.name, status=Status.NOT_FOUND, detail="article not in Sci-Hub"
            )

        # Cloudflare / ALTCHA — solve the proof-of-work automatically.
        # A page counts as a challenge only when it embeds a widget with
        # a concrete challenge id or carries a generic bot-check phrase;
        # the bare word "altcha" (article-page report widget) is not enough.
        if extract_challenge_id(html) is not None or any(
            sig in lower for sig in _CHALLENGE_SIGNATURES
        ):
            if allow_solve and solve_altcha(
                self._session,
                html,
                base_url=resp.url,
                timeout=self._timeout,
                proxies=proxies,
            ):
                # Cookie obtained — retry once; never re-solve in a loop.
                return self._try_landing(landing_url, destination, proxies, allow_solve=False)
            return SourceResult.failure(
                source=self.name,
                status=Status.CHALLENGE_REQUIRED,
                detail="ALTCHA proof-of-work could not be solved automatically",
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
