"""ableSci/科研通 fallback via OpenCLI Browser Bridge.

Adapted from scansci-pdf — ableSci HTTP API protocol and OpenCLI integration
originally designed by Rimagination.
https://github.com/Rimagination/scansci-pdf (Apache 2.0)
"""

from __future__ import annotations

import json
import re
import time as _time
from pathlib import Path
from typing import Optional

import requests

from ..config import Config
from ..models import PaperIdentity, SourceResult, Status
from ..pdf import download_candidate


# ---------------------------------------------------------------------------
# OpenCLI wrapper (legacy — kept for fallback)
# ---------------------------------------------------------------------------

_OPENCLI = "opencli"
_SESSION = "scansci-ablesci"


def _opencli(*args: str, timeout: float = 30) -> dict:
    cmd = [_OPENCLI, "browser", _SESSION] + list(args)
    try:
        proc = __import__("subprocess").run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        raise OpenCLIError("opencli not found", Status.EXTERNAL_COMMAND_MISSING)
    except __import__("subprocess").TimeoutExpired:
        raise OpenCLIError("opencli timed out", Status.TIMEOUT)
    if proc.returncode != 0:
        detail = proc.stderr.strip()[:200] or f"exit {proc.returncode}"
        raise OpenCLIError(detail, Status.NETWORK_ERROR)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise OpenCLIError("invalid JSON from opencli", Status.NETWORK_ERROR)


class OpenCLIError(Exception):
    def __init__(self, detail: str, status: Status) -> None:
        self.detail = detail
        self.status = status
        super().__init__(detail)


# ---------------------------------------------------------------------------
# ableSci source — HTTP + cookie path (primary), OpenCLI fallback
# ---------------------------------------------------------------------------

_CREATE_URL = "/assist/create"
_POLL_TIMEOUT_S = 60  # 1 minute — ableSci typically fulfills within seconds


class AbleSciSource:
    name = "ablesci"

    def __init__(self, session: requests.Session, config: Config) -> None:
        self._session = session
        self._config = config
        self._timeout = config.request_timeout
        self._base_url = (config.ablesci_url or "").rstrip("/")
        # Ensure www prefix for cookie domain matching
        if "://ablesci.com" in self._base_url and "://www.ablesci.com" not in self._base_url:
            self._base_url = self._base_url.replace("://ablesci.com", "://www.ablesci.com")

    # ------------------------------------------------------------------
    # Public entry point — HTTP first, OpenCLI fallback
    # ------------------------------------------------------------------

    def fetch(self, identity: PaperIdentity, destination: Path) -> SourceResult:
        if not self._base_url:
            return self._failure(Status.CONFIGURATION_ERROR, "ablesci_url not configured")
        doi = identity.doi
        title = identity.title
        if not doi and not title:
            return self._failure(Status.NOT_FOUND, "no DOI or title")

        # Try HTTP+cookie path first
        result = self._fetch_http(identity, destination)
        if result is not None:
            return result

        # Fallback to legacy OpenCLI path
        return self._fetch_opencli(doi, title, destination)

    # ------------------------------------------------------------------
    # HTTP path — uses browser_cookie3 + requests
    # ------------------------------------------------------------------

    def _fetch_http(self, identity: PaperIdentity, destination: Path) -> Optional[SourceResult]:
        """Attempt download via ableSci HTTP API using Chrome cookies."""
        try:
            import browser_cookie3
        except ImportError:
            return None

        doi = identity.doi
        title = identity.title
        # ableSci requires title — if missing, use DOI as fallback
        if not title:
            title = doi

        # 1. Read cookies from Chrome
        try:
            cj = browser_cookie3.chrome(domain_name="ablesci.com")
        except Exception:
            return None

        # 2. Add Chrome cookies to the existing session
        try:
            for c in cj:
                self._session.cookies.set(c.name, c.value, domain="ablesci.com")
        except Exception:
            pass

        # 3. Check if this DOI already has a request with a download
        hashid = self._http_find_existing_download(self._session, doi)
        if hashid:
            dl_config = self._http_get_download_config(self._session, hashid)
            if dl_config:
                token_data = self._http_request_token(self._session, dl_config)
                if token_data:
                    return self._http_download_pdf(self._session, token_data, destination)

        # 4. No existing download — submit new request and poll
        csrf = self._http_get_csrf(self._session)
        if not csrf:
            return None

        # 5. Submit the request (or detect duplicate)
        req_id = self._http_submit_request(self._session, csrf, doi, title)
        if not req_id:
            # Submission likely succeeded (code=0) but no ID in response
            # (data: null). The new request is pending — ableSci fulfils
            # asynchronously, so poll recent requests for a download link
            # (up to 60 s) instead of giving up after one re-scan.
            hashid = self._http_poll_for_existing_download(self._session, doi)
            if not hashid:
                return self._failure(
                    Status.NOT_FOUND,
                    "no PDF within 1 minute — ableSci request pending",
                )
            dl_config = self._http_get_download_config(self._session, hashid)
            if not dl_config:
                return None
            token_data = self._http_request_token(self._session, dl_config)
            if not token_data:
                return None
            return self._http_download_pdf(self._session, token_data, destination)

        # 6. Poll the specific request for a download link
        hashid = self._http_poll_for_download(self._session, req_id)
        if not hashid:
            return self._failure(Status.NOT_FOUND, "no PDF within 1 minute — ableSci request pending")

        # 7. Get download config + token + download
        dl_config = self._http_get_download_config(self._session, hashid)
        if not dl_config:
            return None

        token_data = self._http_request_token(self._session, dl_config)
        if not token_data:
            return None

        return self._http_download_pdf(self._session, token_data, destination)

    # ------------------------------------------------------------------
    # HTTP helper methods
    # ------------------------------------------------------------------

    def _http_get_csrf(self, session: requests.Session) -> Optional[str]:
        """GET /assist/create and extract the _csrf token."""
        try:
            r = session.get(
                f"{self._base_url}{_CREATE_URL}", timeout=self._timeout
            )
            m = re.search(r'name="_csrf"\s+value="([^"]+)"', r.text)
            return m.group(1) if m else None
        except requests.RequestException:
            return None

    def _http_submit_request(
        self, session: requests.Session, csrf: str,
        doi: Optional[str], title: Optional[str],
    ) -> Optional[str]:
        """POST /assist/create to submit a new request.
        Returns the request ID (new or existing).
        """
        data = {
            "_csrf": csrf,
            "Assist[doi]": doi or "",
            "Assist[title]": title or "",
            "Assist[type]": "1",
            "Assist[point]": "10",
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": csrf,
            "Referer": f"{self._base_url}{_CREATE_URL}",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        }
        try:
            r = session.post(
                f"{self._base_url}{_CREATE_URL}",
                data=data, headers=headers, timeout=self._timeout,
            )
            result = r.json()
        except Exception:
            return None

        # New request created
        if result.get("code") == 0:
            # Newer API: data.id may be absent; try msg HTML for ID, then fallback
            d = result.get("data")
            if isinstance(d, dict):
                rid = d.get("id")
                if rid:
                    return str(rid)
            # Fallback: re-scan recent requests for our DOI
            return None  # caller will re-scan

        # Duplicate request — extract existing ID from error message
        if result.get("code") == 1:
            msg = str(result.get("msg", ""))
            m = re.search(r"/assist/detail\?id=(\w+)", msg)
            if m:
                return m.group(1)

        return None

    def _http_poll_for_download(
        self, session: requests.Session, req_id: str,
    ) -> Optional[str]:
        """Poll /assist/detail?id={req_id} for a download link.
        Falls back to checking all recent requests.
        Returns the hashid of the downloadable file, or None.
        """
        detail_url = f"{self._base_url}/assist/detail?id={req_id}"
        deadline = _time.monotonic() + _POLL_TIMEOUT_S
        while _time.monotonic() < deadline:
            try:
                r = session.get(detail_url, timeout=self._timeout)
                m = re.search(r"/assist/download\?id=(\w+)", r.text)
                if m:
                    return m.group(1)
            except requests.RequestException:
                pass
            _time.sleep(5)

        # Fallback: scan all recent requests for a download link
        return self._http_scan_recent_requests(session)

    def _http_find_existing_download(
        self, session: requests.Session, doi: str,
    ) -> Optional[str]:
        """Scan recent requests for one matching *doi* that already has a download link.

        Returns the hashid of the download, or None.
        """
        try:
            r = session.get(
                f"{self._base_url}/my/assist-my", timeout=self._timeout,
            )
            req_ids = list(dict.fromkeys(
                re.findall(r'/assist/detail\?id=(\w+)', r.text)
            ))
            for rid in req_ids[:10]:
                try:
                    dr = session.get(
                        f"{self._base_url}/assist/detail?id={rid}",
                        timeout=self._timeout,
                    )
                    # Check if this request is for our DOI
                    if doi.lower() not in dr.text.lower():
                        continue
                    m = re.search(r"/assist/download\?id=(\w+)", dr.text)
                    if m:
                        return m.group(1)
                except requests.RequestException:
                    continue
        except requests.RequestException:
            pass
        return None

    def _http_poll_for_existing_download(
        self, session: requests.Session, doi: str,
    ) -> Optional[str]:
        """Poll recent requests for one matching *doi* that gains a download link.

        Used when a submit succeeded but returned no request ID (data: null).
        ableSci fulfils new requests asynchronously; poll every 5 s for up to
        60 s (the same window as _http_poll_for_download).
        """
        deadline = _time.monotonic() + _POLL_TIMEOUT_S
        while _time.monotonic() < deadline:
            hashid = self._http_find_existing_download(session, doi)
            if hashid:
                return hashid
            _time.sleep(5)
        return None

    def _http_scan_recent_requests(
        self, session: requests.Session,
    ) -> Optional[str]:
        """Scan /my/assist-my for any request with a download link."""
        try:
            r = session.get(
                f"{self._base_url}/my/assist-my", timeout=self._timeout,
            )
            # Collect all request IDs from the page
            req_ids = list(dict.fromkeys(
                re.findall(r'/assist/detail\?id=(\w+)', r.text)
            ))
            # Check each request's detail page (up to 5)
            for rid in req_ids[:5]:
                try:
                    dr = session.get(
                        f"{self._base_url}/assist/detail?id={rid}",
                        timeout=self._timeout,
                    )
                    m = re.search(r"/assist/download\?id=(\w+)", dr.text)
                    if m:
                        return m.group(1)
                except requests.RequestException:
                    continue
        except requests.RequestException:
            pass
        return None

    def _http_get_download_config(
        self, session: requests.Session, hashid: str,
    ) -> Optional[dict]:
        """GET /assist/download?id={hashid} and extract the config JSON."""
        try:
            r = session.get(
                f"{self._base_url}/assist/download?id={hashid}",
                timeout=self._timeout,
            )
            m = re.search(r"const\s+\w+\s*=\s*({.+?});", r.text, re.DOTALL)
            if not m:
                return None
            raw = m.group(1).encode().decode("unicode_escape")
            config = json.loads(raw)
            # Normalise keys
            return {
                "_csrf": config.get("csrfToken", ""),
                "hashid": config.get("hashid", hashid),
                "expectedSize": config.get("expectedSize", 0),
            }
        except Exception:
            return None

    def _http_request_token(
        self, session: requests.Session, config: dict,
    ) -> Optional[dict]:
        """POST /file/request-download-token for high-speed download."""
        data = {
            "_csrf": config["_csrf"],
            "type": "assistFile",
            "id": config["hashid"],
            "channel": "highspeed",
            "highspeed": "1",
            "fallback": "0",
            "file_server": "0",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self._base_url}/assist/download?id={config['hashid']}",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        }
        try:
            r = session.post(
                f"{self._base_url}/file/request-download-token",
                data=data, headers=headers, timeout=self._timeout,
            )
            result = r.json()
            if result.get("code") == 0:
                return result.get("data")
            return None
        except Exception:
            return None

    def _http_download_pdf(
        self, session: requests.Session, token_data: dict,
        destination: Path,
    ) -> Optional[SourceResult]:
        """Download the PDF from the token-provided host."""
        download_url = f'{token_data["host"]}?token={token_data["token"]}'
        try:
            return download_candidate(
                session=session,
                url=download_url,
                destination=destination,
                source=self.name,
                timeout=self._timeout,
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # OpenCLI fallback (legacy)
    # ------------------------------------------------------------------

    def _fetch_opencli(self, doi: Optional[str], title: Optional[str],
                       destination: Path) -> SourceResult:
        """Legacy OpenCLI-based path — kept for when cookies aren't available."""
        # 1. Navigate to create page
        create_url = f"{self._base_url}{_CREATE_URL}"
        try:
            _opencli("open", create_url, "--window", "background", timeout=20)
        except OpenCLIError as exc:
            return self._failure(exc.status, exc.detail)
        _time.sleep(4)

        # 2. Check login state
        try:
            logged_in = _opencli(
                "eval",
                "!!document.querySelector('a[href*=\"logout\"], a[href*=\"退出\"]')",
                timeout=10,
            )
        except OpenCLIError:
            return self._failure(Status.NETWORK_ERROR, "unable to read page state")
        if not logged_in:
            return self._failure(Status.AUTHENTICATION_REQUIRED, "not logged in to ableSci")

        # 3. Fill the one-key input and trigger smart extraction
        query = doi or title
        for _retry in range(3):
            try:
                _opencli("fill", "#onekey", query, timeout=10)
                break
            except OpenCLIError:
                _time.sleep(2)
        else:
            return self._failure(Status.NETWORK_ERROR, "unable to fill search field")
        _time.sleep(1)

        try:
            _opencli("click", 'button[title*="查询该doi"]', timeout=10)
        except OpenCLIError:
            return self._failure(Status.NETWORK_ERROR, "unable to click extract button")
        _time.sleep(5)

        # 4. Verify form was filled
        try:
            check = _opencli(
                "eval",
                "document.getElementById('Assist-doi')?.value || document.getElementById('Assist-title')?.value || ''",
                timeout=10,
            )
        except OpenCLIError:
            check = ""
        if not check or (isinstance(check, str) and len(check) < 3):
            if doi:
                try:
                    _opencli("fill", "#Assist-doi", doi, timeout=10)
                except OpenCLIError:
                    pass
            if title and (not doi or check != doi):
                try:
                    _opencli("fill", "#Assist-title", title, timeout=10)
                except OpenCLIError:
                    pass
            _time.sleep(1)

        # 5. Submit via JavaScript fetch
        csrf_js = "document.querySelector('[name=_csrf]')?.value || ''"
        try:
            csrf = _opencli("eval", csrf_js, timeout=10)
        except OpenCLIError:
            csrf = ""
        if isinstance(csrf, dict):
            csrf = csrf.get("result", "")

        submit_js = f"""
        (async () => {{
            const fd = new FormData(document.querySelector('form'));
            const resp = await fetch('{_CREATE_URL}', {{
                method: 'POST', body: fd,
                headers: {{'X-CSRF-Token': '{csrf}', 'X-Requested-With': 'XMLHttpRequest'}},
            }});
            const data = await resp.json();
            return JSON.stringify(data);
        }})()
        """
        try:
            raw = _opencli("eval", submit_js, timeout=15)
        except OpenCLIError:
            return self._failure(Status.NETWORK_ERROR, "form submission failed")

        result_data = raw
        if isinstance(raw, str):
            try:
                result_data = json.loads(raw)
            except json.JSONDecodeError:
                pass

        if isinstance(result_data, dict):
            code = result_data.get("code")
            msg = result_data.get("msg", "")
            if code == 1 and "相同DOI" in str(msg):
                m = re.search(r"/assist/detail\?id=(\w+)", str(msg))
                if m:
                    return self._poll_request(m.group(1), destination)
            if code == 0:
                req_id = result_data.get("data", {}).get("id") if isinstance(result_data.get("data"), dict) else None
                if req_id:
                    return self._poll_request(str(req_id), destination)

        return self._poll_my_requests(destination)

    def _poll_request(self, req_id: str, destination: Path) -> SourceResult:
        """Poll a specific request page for PDF links (OpenCLI)."""
        detail_url = f"{self._base_url}/assist/detail?id={req_id}"
        try:
            _opencli("open", detail_url, "--window", "background", timeout=20)
        except OpenCLIError:
            pass
        _time.sleep(4)

        deadline = _time.monotonic() + _POLL_TIMEOUT_S
        while _time.monotonic() < deadline:
            try:
                pdfs = _opencli(
                    "eval",
                    'Array.from(document.querySelectorAll("a[href$=\".pdf\"]")).map(a=>a.href)',
                    timeout=10,
                )
                if isinstance(pdfs, list) and pdfs:
                    for pdf_url in pdfs:
                        result = download_candidate(
                            session=self._session, url=pdf_url,
                            destination=destination, source=self.name,
                            timeout=self._timeout,
                        )
                        if result.success:
                            return result
                    return self._failure(Status.INVALID_PDF, "PDF links found but download failed")
            except OpenCLIError:
                pass
            _time.sleep(5)

        return self._failure(Status.NOT_FOUND, "no PDF within 1 minute — ableSci request pending")

    def _poll_my_requests(self, destination: Path) -> SourceResult:
        """Fallback: check the 'my requests' page."""
        try:
            _opencli("open", f"{self._base_url}/my/assist-my", "--window", "background", timeout=20)
        except OpenCLIError:
            return self._failure(Status.NETWORK_ERROR, "unable to open my requests")
        _time.sleep(4)
        try:
            pdfs = _opencli(
                "eval",
                'Array.from(document.querySelectorAll("a[href$=\".pdf\"]")).map(a=>a.href)',
                timeout=10,
            )
            if isinstance(pdfs, list) and pdfs:
                for pdf_url in pdfs:
                    result = download_candidate(
                        session=self._session, url=pdf_url,
                        destination=destination, source=self.name,
                        timeout=self._timeout,
                    )
                    if result.success:
                        return result
        except OpenCLIError:
            pass
        return self._failure(Status.NOT_FOUND, "no PDF in my requests")

    def _failure(self, status: Status, detail: str) -> SourceResult:
        return SourceResult.failure(source=self.name, status=status, detail=detail)