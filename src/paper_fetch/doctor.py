"""Read-only health checks for paper-fetch configuration and environment.

``paper-fetch doctor`` never writes configuration and never prints secret
values: endpoint credentials, cookie names/values, and API keys are never
included in the report. Every check carries an ``action`` — the next step
for a human or an agent.
"""

from __future__ import annotations

import shutil
import socket
import stat
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config

OK = "ok"
MISSING = "missing"
INVALID = "invalid"
UNREACHABLE = "unreachable"
WARNING = "warning"
ERROR = "error"

OVERALL_OK = "ok"
OVERALL_NEEDS_CONFIGURATION = "needs_configuration"
OVERALL_ERROR = "error"

_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")
_VPN_PROCESS_MARKERS = ("easyconnect", "atrust")
_ZOTERO_REQUIRED = (
    "zotero_library_id",
    "zotero_library_type",
    "zotero_inbox_collection_key",
    "zotero_api_key",
)
# ableSci session is only ready when ALL of these are present
# (spec: references/ablesci-api-protocol.md, "Cookie Requirements").
_ABLESCI_ESSENTIAL_COOKIES = (
    "_identity-frontend",
    "advanced-frontend",
    "_csrf",
    "security_session_verify",
)


def _check(name: str, status: str, detail: str, action: str) -> dict[str, str]:
    """Build one check entry: name, status, detail, action."""
    return {"name": name, "status": status, "detail": detail, "action": action}


def _redact_url(url: str) -> str:
    """Return ``scheme://host[:port]`` with any userinfo stripped."""
    try:
        parts = urlparse(url)
    except ValueError:
        return "<invalid URL>"
    if not parts.scheme or not parts.hostname:
        return "<invalid URL>"
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    if parts.port is None:
        return f"{parts.scheme}://{host}"
    return f"{parts.scheme}://{host}:{parts.port}"


def _port_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Probe a single configured host:port. Never scans ranges."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_vpn_processes() -> list[str]:
    """Return basenames of running EasyConnect/aTrust processes (empty on failure)."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "comm"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return []
    found: set[str] = set()
    for line in out.splitlines():
        name = Path(line.strip()).name
        low = name.lower()
        if any(marker in low for marker in _VPN_PROCESS_MARKERS):
            found.add(name)
    return sorted(found)


def _read_ablesci_cookies() -> set[str] | None:
    """Return the set of ableSci cookie names readable from Chrome.

    Returns ``None`` when the cookie store cannot be read at all (locked
    database, locked Keychain, missing Chrome, missing dependency). A set —
    possibly empty — means the store was read successfully.
    """
    try:
        import browser_cookie3
    except ImportError:
        return None
    try:
        jar = browser_cookie3.chrome(domain_name="ablesci.com")
        return {cookie.name for cookie in jar}
    except Exception:
        return None


def _opencli_available() -> bool:
    return shutil.which("opencli") is not None


def _check_config_file(path: Path) -> tuple[dict[str, str], Config | None]:
    """Check the config file. Returns (check entry, loaded Config or None)."""
    if not path.exists():
        return (
            _check(
                "config",
                MISSING,
                f"config file not found: {path}",
                "create the file (see the README 'Configure' section), or keep the defaults for open-access-only use",
            ),
            None,
        )
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        return (
            _check(
                "config",
                ERROR,
                f"config file could not be parsed: {exc}",
                "fix the JSON syntax in the config file, then rerun doctor",
            ),
            None,
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return (
            _check(
                "config",
                WARNING,
                f"config file readable, but mode is {oct(mode)}",
                "restrict permissions with: chmod 600 on the config file (it may contain credentials)",
            ),
            cfg,
        )
    return _check("config", OK, f"config file readable, mode {oct(mode)}", ""), cfg


def _parse_proxy(value: str) -> tuple[str, str, int] | None:
    """Validate a proxy URL; return (scheme, host, port) or None when invalid."""
    parts = urlparse(value)
    if parts.scheme not in _PROXY_SCHEMES or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        return None
    return parts.scheme, parts.hostname, port


def _check_institution(cfg: Config) -> dict[str, str]:
    value = cfg.institution_socks5
    if not value:
        if _find_vpn_processes():
            return _check(
                "institution",
                MISSING,
                "institution_socks5 not configured, but an EasyConnect/aTrust process is running",
                "find the local proxy port with 'lsof -nP -iTCP -sTCP:LISTEN', determine whether it is HTTP or SOCKS5 (see references/institution-access.md), then ask the user before setting institution_socks5",
            )
        return _check(
            "institution",
            MISSING,
            "institution_socks5 not configured",
            "log in to your institution VPN client (EasyConnect/aTrust) first, then configure institution_socks5 (see the README 'Institution access' section)",
        )
    parsed = _parse_proxy(value)
    if parsed is None:
        return _check(
            "institution",
            INVALID,
            "institution_socks5 is not a valid proxy URL",
            "use scheme http, https, socks5 or socks5h with an explicit port, e.g. socks5h://127.0.0.1:<PORT>",
        )
    _scheme, host, port = parsed
    shown = _redact_url(value)
    if _port_reachable(host, port):
        return _check("institution", OK, f"institution proxy reachable: {shown}", "")
    return _check(
        "institution",
        UNREACHABLE,
        f"institution proxy not reachable: {shown}",
        "start the VPN client and re-check the port (it may change after a restart); see references/institution-access.md",
    )


def _check_clash(cfg: Config) -> dict[str, str]:
    value = cfg.clash_proxy
    if not value:
        return _check(
            "clash",
            MISSING,
            "clash_proxy not configured",
            "start Clash (ClashX / Clash Verge / ...), read the HTTP or Mixed port from its settings, then set clash_proxy to http://127.0.0.1:<PORT> (see references/scihub-clash-setup.md)",
        )
    parsed = _parse_proxy(value)
    if parsed is None:
        return _check(
            "clash",
            INVALID,
            "clash_proxy is not a valid proxy URL",
            "use scheme http, https or socks5 with an explicit port, e.g. http://127.0.0.1:<PORT>",
        )
    _scheme, host, port = parsed
    shown = _redact_url(value)
    if _port_reachable(host, port):
        return _check("clash", OK, f"clash proxy reachable: {shown}", "")
    return _check(
        "clash",
        UNREACHABLE,
        f"clash proxy not reachable: {shown}",
        "start Clash and verify the port; see references/scihub-clash-setup.md",
    )


def _check_ablesci(cfg: Config) -> dict[str, str]:
    url = cfg.ablesci_url
    if not url:
        return _check(
            "ablesci",
            MISSING,
            "ablesci_url not configured",
            "set ablesci_url to https://www.ablesci.com (see the README 'ableSci' section)",
        )
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return _check(
            "ablesci",
            INVALID,
            "ablesci_url is not a valid URL",
            "use a full http(s) URL, e.g. https://www.ablesci.com",
        )
    cookie_names = _read_ablesci_cookies()
    if cookie_names is None:
        if _opencli_available():
            return _check(
                "ablesci",
                MISSING,
                "ableSci session state cannot be read (Chrome cookie store locked or Keychain locked); the OpenCLI fallback is available",
                "log in at https://www.ablesci.com in Google Chrome once (see references/ablesci-login.md), or rely on the OpenCLI fallback",
            )
        return _check(
            "ablesci",
            MISSING,
            "ableSci session state cannot be read (Chrome cookie store locked or Keychain locked)",
            "log in at https://www.ablesci.com in Google Chrome once (see references/ablesci-login.md), and install OpenCLI for the fallback path",
        )
    if not set(_ABLESCI_ESSENTIAL_COOKIES).issubset(cookie_names):
        return _check(
            "ablesci",
            MISSING,
            "ableSci session cookies are incomplete — log in again in Google Chrome",
            "open https://www.ablesci.com in Google Chrome, log in once and keep the session, then rerun doctor (see references/ablesci-login.md)",
        )
    return _check(
        "ablesci",
        OK,
        "ableSci session ready (essential session cookies present)",
        "",
    )


def _check_zotero(cfg: Config) -> dict[str, str]:
    if cfg.zotero_library_type not in ("user", "group"):
        return _check(
            "zotero",
            INVALID,
            "zotero_library_type must be 'user' or 'group'",
            "fix zotero_library_type, or run fetch with --no-zotero for a local-only download",
        )
    missing = [key for key in _ZOTERO_REQUIRED if not getattr(cfg, key)]
    if missing:
        return _check(
            "zotero",
            MISSING,
            "Zotero fields missing: " + ", ".join(missing),
            "run fetch with --no-zotero for a local-only download, or add the missing fields with the user's consent",
        )
    return _check("zotero", OK, "all Zotero fields present", "")


def run_doctor(config_path: str | Path | None = None) -> dict[str, Any]:
    """Run all checks and return the machine-readable report."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    try:
        cfg_check, cfg = _check_config_file(path)
        checks = [cfg_check]
        if cfg_check["status"] == ERROR:
            return {
                "overall": OVERALL_ERROR,
                "config_path": str(path),
                "checks": checks,
            }
        if cfg is None:
            cfg = load_config(path)  # defaults when the file is missing
        checks.extend(
            [
                _check_institution(cfg),
                _check_clash(cfg),
                _check_ablesci(cfg),
                _check_zotero(cfg),
            ]
        )
        if all(c["status"] == OK for c in checks):
            overall = OVERALL_OK
        else:
            overall = OVERALL_NEEDS_CONFIGURATION
        return {"overall": overall, "config_path": str(path), "checks": checks}
    except Exception as exc:  # doctor itself failed — never crash without JSON
        return {
            "overall": OVERALL_ERROR,
            "config_path": str(path),
            "checks": [
                _check(
                    "doctor",
                    ERROR,
                    f"doctor failed: {type(exc).__name__}",
                    "report this error; configuration and environment are unaffected",
                )
            ],
        }


def format_human(report: dict[str, Any]) -> str:
    """Human-readable report with the next step for every check."""
    lines = [f"paper-fetch doctor — overall: {report['overall']}"]
    for entry in report["checks"]:
        lines.append(f"[{entry['status']}] {entry['name']}")
        if entry["detail"]:
            lines.append(f"    {entry['detail']}")
        if entry["action"]:
            lines.append(f"    Next: {entry['action']}")
    return "\n".join(lines)
