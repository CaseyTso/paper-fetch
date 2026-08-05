"""Minimal configuration with deterministic precedence and secret redaction."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".paper-fetch"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_SCIHUB_DOMAINS: tuple[str, ...] = (
    # sci-hub.jp is the current primary mirror; .st/.ru redirect here.
    # sci-hub.se was dropped: its edge terminates TLS (SSL UNEXPECTED_EOF).
    "https://sci-hub.jp",
    "https://sci-hub.st",
    "https://sci-hub.ru",
)

SENSITIVE_FIELDS = {
    "zotero_api_key",
}

SENSITIVE_SUBSTRINGS = ("_api_key", "_password", "_token")


class ConfigError(Exception):
    """Raised when the configuration is invalid or missing required values."""


def _is_sensitive(key: str) -> bool:
    if key in SENSITIVE_FIELDS:
        return True
    return any(s in key.lower() for s in SENSITIVE_SUBSTRINGS)


def _guess_sensitive(field_name: str) -> bool:
    """Check whether a config field carries credentials."""
    return _is_sensitive(field_name) or "proxy" in field_name.lower() or "socks" in field_name.lower()


@dataclass
class Config:
    """Immutable runtime configuration."""

    output_dir: Path = field(default_factory=lambda: Path.home() / "Downloads" / "paper-fetch-papers")
    unpaywall_email: str = ""
    institution_socks5: str | None = None
    institution_tls_verify: bool = True
    clash_proxy: str | None = None
    scihub_domains: tuple[str, ...] = DEFAULT_SCIHUB_DOMAINS
    zotero_library_id: str | None = None
    zotero_library_type: str = "user"
    zotero_inbox_collection_key: str | None = None
    zotero_api_key: str | None = None
    ablesci_url: str | None = None
    ablesci_driver: str | None = None  # auto | http | browser | opencli
    request_timeout: float = 30.0
    pdf_validation_retries: int = 1

    def redacted_dict(self) -> dict[str, Any]:
        """Return a dict safe for logging and error reporting."""
        result: dict[str, Any] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if _guess_sensitive(f.name):
                result[f.name] = "[REDACTED]" if val else None
            else:
                result[f.name] = val
        return result


def load_config(
    path: Path | str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Config:
    """Load configuration with precedence: explicit overrides > env > JSON > defaults.

    Environment variables follow the pattern ``PAPER_FETCH_<FIELD>`` (uppercase).
    """
    raw: dict[str, Any] = {}

    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if config_path.exists():
        try:
            raw.update(json.loads(config_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            raise ConfigError(f"Failed to parse {config_path}: {exc}") from exc

    # Env override
    for f in fields(Config):
        env_name = f"PAPER_FETCH_{f.name.upper()}"
        env_val = os.environ.get(env_name)
        if env_val is not None:
            raw[f.name] = env_val

    # Explicit overrides
    if overrides:
        raw.update(overrides)

    # Coerce known types
    if isinstance(raw.get("output_dir"), str):
        raw["output_dir"] = Path(raw["output_dir"])
    if isinstance(raw.get("scihub_domains"), str):
        raw["scihub_domains"] = tuple(
            d.strip() for d in raw["scihub_domains"].split(",") if d.strip()
        )
    if isinstance(raw.get("institution_tls_verify"), str):
        raw["institution_tls_verify"] = raw["institution_tls_verify"].strip().lower() not in {
            "0", "false", "no", "off"
        }
    if isinstance(raw.get("request_timeout"), str):
        raw["request_timeout"] = float(raw["request_timeout"])
    if isinstance(raw.get("pdf_validation_retries"), str):
        raw["pdf_validation_retries"] = int(raw["pdf_validation_retries"])

    valid_keys = {f.name for f in fields(Config)}
    filtered = {k: v for k, v in raw.items() if k in valid_keys}

    return Config(**filtered)
