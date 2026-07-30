"""Zotero Web API client — read, DOI lookup, attachment discovery, upsert, upload."""

from __future__ import annotations

import hashlib
import json as _json
import secrets
import string as _string
from pathlib import Path
from typing import Any

import requests

from .config import Config
from .models import PaperIdentity


class ZoteroClient:
    """Minimal Zotero Web API client for a single user or group library."""

    def __init__(self, session: requests.Session, config: Config) -> None:
        self._session = session
        api_key = config.zotero_api_key
        lib_type = config.zotero_library_type or "user"
        lib_id = config.zotero_library_id

        if not api_key or not lib_id:
            raise ZoteroConfigError("zotero_api_key and zotero_library_id are required")

        self._base = f"https://api.zotero.org/{lib_type}s/{lib_id}"
        self._headers = {
            "Zotero-API-Key": api_key,
            "Content-Type": "application/json",
        }
        self._timeout = config.request_timeout

    # -- public read API ----------------------------------------------------

    def get_item(self, key: str) -> dict | None:
        url = f"{self._base}/items/{key}"
        try:
            resp = self._session.get(url, headers=self._headers, timeout=self._timeout)
        except requests.RequestException:
            return None
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("data")

    def find_by_doi(self, doi: str) -> list[dict]:
        url = f"{self._base}/items"
        params = {"q": doi, "itemType": "journalArticle", "limit": 5}
        try:
            resp = self._session.get(
                url, headers=self._headers, params=params, timeout=self._timeout
            )
            resp.raise_for_status()
        except requests.RequestException:
            return []
        items = resp.json()
        seen: set[str] = set()
        matched: list[dict] = []
        for item in items:
            data = item.get("data", {})
            item_doi = (data.get("DOI") or "").strip()
            if item_doi.lower() != doi.lower():
                continue
            key = data.get("key")
            if key and key not in seen:
                seen.add(key)
                matched.append(data)
        return matched

    def get_children(self, key: str) -> list[dict]:
        url = f"{self._base}/items/{key}/children"
        try:
            resp = self._session.get(url, headers=self._headers, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException:
            return []
        return [c.get("data", {}) for c in resp.json()]

    def find_pdf_attachment(self, key: str) -> dict | None:
        for child in self.get_children(key):
            ct = (child.get("contentType") or "").lower()
            lm = child.get("linkMode", "")
            if ct == "application/pdf" and lm in ("imported_file", "imported_url"):
                return child
        return None

    def identity_from_item(
        self, data: dict, *, original_input: str = ""
    ) -> PaperIdentity:
        creators = data.get("creators", [])
        authors = [
            f"{c.get('lastName', '')} {c.get('firstName', '')}".strip()
            for c in creators
            if c.get("creatorType") == "author"
        ]
        return PaperIdentity(
            original_input=original_input or data.get("key", ""),
            doi=data.get("DOI"),
            title=data.get("title"),
            authors=authors,
            journal=data.get("publicationTitle"),
            year=data.get("date"),
            zotero_item_key=data.get("key"),
        )


class ZoteroConfigError(Exception):
    """Raised when required Zotero settings are missing."""


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def _identity_to_item(identity: PaperIdentity) -> dict:
    item: dict[str, Any] = {"itemType": "journalArticle"}
    if identity.doi:
        item["DOI"] = identity.doi
        item["url"] = f"https://doi.org/{identity.doi}"
    if identity.title:
        item["title"] = identity.title
    if identity.authors:
        item["creators"] = [
            {"creatorType": "author", "name": a} for a in identity.authors
        ]
    if identity.journal:
        item["publicationTitle"] = identity.journal
    if identity.year:
        item["date"] = identity.year
    return item


def upsert_item(
    client: ZoteroClient, identity: PaperIdentity, *, force: bool = False
) -> str | None:
    if identity.doi and not force:
        existing = client.find_by_doi(identity.doi)
        if existing:
            key = existing[0].get("key")
            if key:
                return key

    item = _identity_to_item(identity)
    url = f"{client._base}/items"
    headers = dict(client._headers)
    try:
        resp = client._session.post(
            url, headers=headers, json=[item], timeout=client._timeout
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    data = resp.json()
    success_map = data.get("successful") or data.get("success") or {}
    for entry in success_map.values():
        key_val = entry if isinstance(entry, str) else entry.get("key")
        if key_val:
            return key_val
    return None


# ---------------------------------------------------------------------------
# File upload (Zotero official protocol)
# ---------------------------------------------------------------------------


def _make_write_token() -> str:
    return "".join(
        secrets.choice(_string.ascii_letters + _string.digits) for _ in range(12)
    )


def attach_pdf(client: ZoteroClient, parent_key: str, pdf_path: Path) -> str | None:
    assert pdf_path.exists(), f"PDF not found: {pdf_path}"

    # Step 1: Create attachment item
    att = {
        "itemType": "attachment",
        "linkMode": "imported_file",
        "contentType": "application/pdf",
        "filename": pdf_path.name,
        "parentItem": parent_key,
    }
    url = f"{client._base}/items"
    headers = dict(client._headers)
    headers["Zotero-Write-Token"] = _make_write_token()
    try:
        resp = client._session.post(
            url, headers=headers, json=[att], timeout=client._timeout
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    data = resp.json()
    success_map = data.get("successful") or data.get("success") or {}
    att_key: str | None = None
    for entry in success_map.values():
        att_key = entry if isinstance(entry, str) else entry.get("key")
        if att_key:
            break
    if not att_key:
        return None

    # Step 2: Get upload authorisation
    digest = hashlib.md5()
    stat = pdf_path.stat()
    with pdf_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            digest.update(chunk)

    auth_url = f"{client._base}/items/{att_key}/file"
    auth_headers = {
        "Zotero-API-Key": client._headers["Zotero-API-Key"],
        "Content-Type": "application/x-www-form-urlencoded",
        "If-None-Match": "*",
    }
    auth_data = {
        "md5": digest.hexdigest(),
        "filename": pdf_path.name,
        "filesize": stat.st_size,
        "mtime": str(int(stat.st_mtime * 1000)),
        "contentType": "application/pdf",
    }
    try:
        resp = client._session.post(
            auth_url, data=auth_data, headers=auth_headers, timeout=client._timeout
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    auth = resp.json()
    if auth.get("exists"):
        _mirror_local(client, att_key, pdf_path)
        return att_key

    # Step 3: Upload to S3 storage
    params = auth.get("params", {})
    s3_key = params.get("key", "")
    files_payload = [("key", (None, s3_key))]
    for k, v in params.items():
        if k != "key":
            files_payload.append((k, (None, str(v))))
    files_payload.append(
        ("file", (pdf_path.name, pdf_path.read_bytes(), "application/pdf"))
    )

    try:
        resp = requests.post(
            auth["url"], files=files_payload, timeout=client._timeout * 3
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    # Step 4: Register with Zotero — use auth["uploadKey"], NOT params["key"]
    upload_key = auth.get("uploadKey", "")
    reg_headers = {
        "Zotero-API-Key": client._headers["Zotero-API-Key"],
        "Content-Type": "application/x-www-form-urlencoded",
        "If-None-Match": "*",
    }
    try:
        resp = client._session.post(
            auth_url,
            data={"upload": upload_key},
            headers=reg_headers,
            timeout=client._timeout,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    # Step 5: Mirror to local Zotero storage so Desktop finds it immediately
    _mirror_local(client, att_key, pdf_path)

    return att_key


def _mirror_local(client: ZoteroClient, att_key: str, pdf_path: Path) -> None:
    """Copy *pdf_path* into ``~/Zotero/storage/<att_key>/`` if that directory exists."""
    try:
        local = Path.home() / "Zotero" / "storage" / att_key
        local.mkdir(parents=True, exist_ok=True)
        dest = local / pdf_path.name
        if not dest.exists():
            dest.write_bytes(pdf_path.read_bytes())
    except Exception:
        pass  # local mirror is a convenience, never fail the upload
