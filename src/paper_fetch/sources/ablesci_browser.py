"""ableSci/科研通 via the Minis in-app browser driver.

This is the **Minis-specific** ableSci path. It drives the persistent in-app
WebView through the ``minis-browser-use`` CLI instead of reading Chrome
cookies or spawning a desktop browser, which is why it works inside iSH/iOS
where ``browser-cookie3`` (DBUS + iOS Chrome store) and OpenCLI (desktop
Chrome) both fail.

Why a browser at all? ableSci is protected by an Aliyun WAF
(``security_session_verify`` + TLS-fingerprint checks): plain ``requests``
calls get redirected to ``/site/login`` even with valid cookies, and the
served PDFs are encrypted — they must be decrypted by the site's own JS in a
real browser session. The Minis WebView session keeps the user's ableSci
login, and native downloads land in the Minis workspace, so the whole flow
(login check → submit request → poll → download → accept) is scriptable.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from ..models import PaperIdentity, SourceResult, Status

_OPENCLI_ERR = "opencli not found"  # placeholder to keep parity with legacy file

_MB = "minis-browser-use"
_WORKSPACE = Path("/var/minis/workspace")
_BROWSER_DIR = Path("/var/minis/browser")
_POLL_DOWNLOAD_S = 60          # ableSci typically fulfils in seconds
_POLL_FILE_S = 120             # native download + decrypt
_SETTLE_S = 3.0                # file-size settle window before copying
_ACCEPT_RETRIES = 3

# Browser-native downloads land in the workspace with a suffix like
# "...(科研通-ablesci.com).pdf"; we also watch /var/minis/browser.
_DOWNLOAD_SUFFIX = "ablesci.com"


class MinisBrowserError(Exception):
    def __init__(self, detail: str, status: Status) -> None:
        self.detail = detail
        self.status = status
        super().__init__(detail)


def is_minis_env() -> bool:
    """True when running inside Minis with the browser CLI available."""
    return (
        Path("/var/minis").is_dir()
        and shutil.which(_MB) is not None
    )


def _mb(action: str, *, timeout: float = 60, **params: Any) -> dict[str, Any]:
    """Run one minis-browser-use action and return its parsed ``data`` dict."""
    cmd = [_MB, action]
    for key, val in params.items():
        cmd.append(f"--{key}")
        cmd.append(str(val))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise MinisBrowserError(f"{_MB} not found", Status.EXTERNAL_COMMAND_MISSING)
    except subprocess.TimeoutExpired:
        raise MinisBrowserError(f"{_MB} {action} timed out", Status.TIMEOUT)
    out = proc.stdout.strip()
    if not out:
        detail = (proc.stderr or "").strip()[:200] or f"exit {proc.returncode}"
        raise MinisBrowserError(f"{_MB} {action}: {detail}", Status.NETWORK_ERROR)
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        raise MinisBrowserError(f"{_MB} {action}: invalid JSON output", Status.NETWORK_ERROR)
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if isinstance(parsed, dict) and parsed.get("ok") is False:
        raise MinisBrowserError(
            f"{_MB} {action}: {str(parsed.get('data'))[:200]}", Status.NETWORK_ERROR
        )
    if not isinstance(data, dict):
        # Some actions may return a bare dict; tolerate it.
        if isinstance(parsed, dict):
            return parsed
        raise MinisBrowserError(f"{_MB} {action}: unexpected output shape", Status.NETWORK_ERROR)
    if data.get("success") is False:
        raise MinisBrowserError(
            f"{_MB} {action}: {str(data.get('text', ''))[:200]}", Status.NETWORK_ERROR
        )
    return data


def _js_result(data: dict[str, Any]) -> str:
    """Extract the execute_js return value from a data dict.

    The CLI appends a ``\\n  tab_id: N`` trailer to the script's return value
    in the ``text`` field — take only the first line.
    """
    text = str(data.get("text", "")).strip()
    return text.splitlines()[0].strip() if text else ""


def _run_js(script: str) -> str:
    data = _mb("execute_js", script=script)
    return _js_result(data)


def _navigate(url: str) -> None:
    _mb("navigate", url=url)


def _type(selector: str, text: str) -> None:
    _mb("type", selector=selector, text=text)


class AbleSciBrowserSource:
    """ableSci source driven through the Minis in-app WebView."""

    name = "ablesci-browser"

    def __init__(self, base_url: str | None, request_timeout: float = 30.0) -> None:
        self._base_url = (base_url or "https://www.ablesci.com").rstrip("/")
        if "://ablesci.com" in self._base_url and "://www.ablesci.com" not in self._base_url:
            self._base_url = self._base_url.replace("://ablesci.com", "://www.ablesci.com")
        self._timeout = request_timeout

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def fetch(self, identity: PaperIdentity, destination: Path) -> SourceResult:
        if not is_minis_env():
            return self._failure(
                Status.EXTERNAL_COMMAND_MISSING,
                "minis-browser-use not available (not a Minis environment?)",
            )
        doi = identity.doi
        title = identity.title
        if not doi and not title:
            return self._failure(Status.NOT_FOUND, "no DOI or title")

        # 1. Login check — visit a protected page and verify we are not
        #    redirected to /site/login. (The mobile header collapses the
        #    username/logout text, so DOM-text heuristics are unreliable.)
        try:
            _navigate(f"{self._base_url}/my/assist-my")
            page = _mb("get_page_info")
            current = page.get("page_url") or page.get("url") or ""
            if "site/login" in current:
                return self._failure(
                    Status.AUTHENTICATION_REQUIRED,
                    "not logged in to ableSci in the Minis browser — log in once, then re-run",
                )
        except MinisBrowserError as exc:
            return self._failure(exc.status, exc.detail)

        # 2. Reuse an existing request that already has a downloadable file
        #    for this DOI (avoids a duplicate request + points spend).
        try:
            existing = self._find_existing_download(doi) if doi else None
        except MinisBrowserError as exc:
            return self._failure(exc.status, exc.detail)

        if existing:
            req_id, download_href = existing
        else:
            # 3. Create a new request and poll for the download link
            try:
                req_id = self._create_request(doi, title)
            except MinisBrowserError as exc:
                return self._failure(exc.status, exc.detail)
            if not req_id:
                return self._failure(
                    Status.NOT_FOUND,
                    "could not create ableSci request or locate its detail page",
                )
            try:
                download_href = self._poll_download_link(req_id)
            except MinisBrowserError as exc:
                return self._failure(exc.status, exc.detail)
            if not download_href:
                return self._failure(
                    Status.NOT_FOUND,
                    f"no download within {_POLL_DOWNLOAD_S}s — ableSci request pending (id={req_id})",
                )

        # 4. Download via the site's own JS (files are encrypted)
        try:
            new_file = self._download_via_browser(download_href)
        except MinisBrowserError as exc:
            return self._failure(exc.status, exc.detail)
        if not new_file:
            return self._failure(
                Status.NOT_FOUND,
                "browser download did not land in the workspace",
            )

        # 5. Move into the destination
        try:
            new_file.replace(destination)
        except OSError as exc:
            return self._failure(Status.NETWORK_ERROR, f"cannot move download into place: {exc}")

        # 6. Accept the file (best-effort; never fails the fetch)
        try:
            self._accept_file(req_id)
        except MinisBrowserError:
            pass

        return SourceResult(
            success=True,
            source=self.name,
            status=Status.SUCCESS,
            temporary_path=destination,
            detail=f"downloaded via Minis browser (id={req_id})",
        )

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _find_existing_download(self, doi: Optional[str]) -> Optional[tuple[str, str]]:
        """Return ``(req_id, download_href)`` when an existing request for
        this DOI already has a downloadable file. Assumes we are already on
        ``/my/assist-my``. Returns ``None`` otherwise."""
        try:
            raw = _run_js(
                "return JSON.stringify(Array.from(document.querySelectorAll("
                "'a[href*=\"/assist/detail?id=\"]')).map(function(a){return a.href;}));"
            )
            hrefs = json.loads(raw) if raw else []
        except (json.JSONDecodeError, MinisBrowserError):
            hrefs = []
        for href in hrefs[:10]:
            m = re.search(r"/assist/detail\?id=([\w\-]+)", href)
            if not m:
                continue
            rid = m.group(1)
            _navigate(f"{self._base_url}/assist/detail?id={rid}")
            page_text = _run_js("return document.body.innerText;")
            if doi and doi.lower() in page_text.lower():
                dl = _run_js(
                    "var a=document.querySelector('a[href*=\"/assist/download?id=\"]');"
                    "return a ? a.href : '';"
                )
                if dl:
                    return rid, dl
        return None

    def _create_request(self, doi: Optional[str], title: Optional[str]) -> Optional[str]:
        """Submit the assist form; return the detail-page id (new or existing)."""
        _navigate(f"{self._base_url}/assist/create")
        # Direct fill (smart extraction is unreliable; manual fields are robust)
        if doi:
            _type("#Assist-doi", doi)
        if title:
            _type("#Assist-title", title)
        note = self._compose_note()
        if note:
            _type("#Assist-note", note)
        _run_js(
            "var b=document.querySelector('#form-submit-btn');"
            "if(b){b.click();return 'submitted';}return 'no-submit-btn';"
        )
        # Small settle for the success dialog / navigation
        time.sleep(3)

        # 2a. Maybe the submit already redirected to an existing detail page
        page = _mb("get_page_info")
        url = page.get("page_url") or page.get("url") or ""
        m = re.search(r"/assist/detail\?id=([\w\-]+)", url)
        if m:
            return m.group(1)

        # 2b. Otherwise take the newest row from "my assists" (newest first)
        _navigate(f"{self._base_url}/my/assist-my")
        href = _run_js(
            "var a=document.querySelector('a[href*=\"/assist/detail?id=\"]');"
            "return a ? a.href : '';"
        )
        m = re.search(r"/assist/detail\?id=([\w\-]+)", href)
        return m.group(1) if m else None

    def _compose_note(self) -> str:
        """Optional short note; keeps parity with the manual flow."""
        return ""  # keep it minimal — title+doi already identify the paper

    def _poll_download_link(self, req_id: str) -> Optional[str]:
        deadline = time.monotonic() + _POLL_DOWNLOAD_S
        while time.monotonic() < deadline:
            _navigate(f"{self._base_url}/assist/detail?id={req_id}")
            href = _run_js(
                "var a=document.querySelector('a[href*=\"/assist/download?id=\"]');"
                "return a ? a.href : '';"
            )
            if href:
                return href
            time.sleep(5)
        return None

    def _download_via_browser(self, download_href: str) -> Optional[Path]:
        """Open the download page, let its JS fetch+decrypt, then wait for the
        native download to land in the workspace/browser dir."""
        before = self._snapshot_files()
        _navigate(download_href)
        deadline = time.monotonic() + _POLL_FILE_S
        last: dict[Path, int] = {}
        while time.monotonic() < deadline:
            # Ping the WebView: on iOS the native-download *save* event is
            # only processed while the WebView is being driven, so a plain
            # filesystem poll can otherwise miss the landing file.
            try:
                _mb("get_page_info", timeout=15)
            except MinisBrowserError:
                pass
            new = self._snapshot_files() - before
            for p in new:
                size = p.stat().st_size
                if p.suffix.lower() != ".pdf":
                    continue
                # settle: size unchanged for a while → download finished
                if last.get(p) == size:
                    if time.monotonic() - self._last_change[p] >= _SETTLE_S:
                        return p
                else:
                    last[p] = size
                    self._last_change[p] = time.monotonic()
            time.sleep(2)
        return None

    def _accept_file(self, req_id: str) -> None:
        """Accept the uploaded file so the request closes (best-effort)."""
        for _attempt in range(_ACCEPT_RETRIES):
            _navigate(f"{self._base_url}/assist/detail?id={req_id}")
            clicked = _run_js(
                "var els=Array.from(document.querySelectorAll('a,button'));"
                "var b=els.find(function(e){return /采纳文件/.test(e.textContent) && e.offsetParent!==null;});"
                "if(b){b.click();return 'clicked';}return 'no-adopt-btn';"
            )
            if clicked != "clicked":
                return
            time.sleep(2)
            ok = _run_js(
                "var layers=Array.from(document.querySelectorAll('.layui-layer'));"
                "var t=layers.find(function(l){return /接受应助/.test(l.textContent);});"
                "var b=t&&t.querySelector('.layui-layer-btn0');"
                "if(b){b.click();return 'ok';}return 'no-dialog';"
            )
            if ok == "ok":
                return
            time.sleep(2)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _snapshot_files(self) -> set[Path]:
        found: set[Path] = set()
        for d in (_WORKSPACE, _BROWSER_DIR):
            if d.is_dir():
                found.update(p for p in d.iterdir() if p.is_file())
        return found

    _last_change: dict[Path, float] = {}

    def _failure(self, status: Status, detail: str) -> SourceResult:
        return SourceResult.failure(source=self.name, status=status, detail=detail)
