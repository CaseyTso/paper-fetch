# Zotero File Upload Protocol

The Zotero Web API file upload is a 4-step handshake. These are the common pitfalls encountered during implementation.

## Pitfall 1: Write token length

Zotero requires the `Zotero-Write-Token` header to be **5–32 characters**. Using `uuid.uuid4()` (36 chars) causes HTTP 400.

**Fix**: Use `secrets.token_hex(6)` (12 chars) or `''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))`.

## Pitfall 2: Register step key confusion

The upload protocol has TWO different keys:

| Key | Source | Purpose |
|---|---|---|
| `params.key` | `auth["params"]["key"]` | S3 object key for the actual file upload |
| `auth.uploadKey` | `auth["uploadKey"]` | Zotero-side key for the register step |

**The register step (step 4) must use `auth["uploadKey"]`, NOT `params["key"]`.** Using the wrong key causes HTTP 400 on register, leaving the attachment item created but the file unlinked — Zotero Desktop shows "file not found".

## Pitfall 3: Local storage mirror

After a successful Web API upload, the file exists on Zotero's cloud servers but NOT in `~/Zotero/storage/<attachment_key>/`. Zotero Desktop won't find it until the next sync.

**Fix**: After successful upload and register, copy the PDF to `~/Zotero/storage/<attachment_key>/<filename>`. This gives Zotero Desktop immediate access. The mirror function is wrapped in try/except — it never fails the upload, even if the local filesystem is unwritable.

```python
def _mirror_local(client, att_key, pdf_path):
    try:
        local = Path.home() / "Zotero" / "storage" / att_key
        local.mkdir(parents=True, exist_ok=True)
        dest = local / pdf_path.name
        if not dest.exists():
            dest.write_bytes(pdf_path.read_bytes())
    except Exception:
        pass  # convenience, never fail the upload
```

**Critical**: Mirror must run in ALL success paths — including when auth.get(exists) is true (same MD5 already on server). The early return path was missing the mirror call (fixed 2026-07-27).

```python
if auth.get("exists"):
    # Even when file already exists on cloud, mirror locally.
    # Without this, Zotero Desktop will not find the attachment until
    # the next cloud sync — which may fail silently if WebDAV is broken.
    _mirror_local(client, att_key, pdf_path)
    return att_key
```

## Pitfall 4: Auth step returns 400 "POST data not provided"

The `params` field in the upload authorisation request is an **output-only field** — Zotero sends `params` back in the response with S3 form fields. Including `"params": 1` in the request body causes Zotero's API to reject the request with 400 "POST data not provided".

**Fix**: Remove `params` from the auth request data entirely.

```python
# ✅ CORRECT — no params field
data = {
    "md5": digest.hexdigest(),
    "filename": pdf_path.name,
    "filesize": stat.st_size,
    "mtime": str(int(stat.st_mtime * 1000)),
    "contentType": "application/pdf",
}
```

The `params` field in the response (`auth["params"]`) contains S3 upload form fields — use those in Step 3, but never send them in the Step 2 request.

## Full protocol

1. **Create attachment item** — POST to `/items` with `itemType: attachment`, `linkMode: imported_file`, `contentType: application/pdf`, `filename`, `parentItem`. Include `Zotero-Write-Token` header (5-32 chars).
2. **Get upload authorisation** — POST to `/items/<att_key>/file` with MD5, filesize, mtime, `If-None-Match: *`. **Do NOT include `params` — it is an output-only field from Zotero's response, not a request parameter.**
3. **Upload to S3** — Multipart POST to `auth["url"]` with `auth["params"]` as form fields + file bytes.
4. **Register upload** — POST to `/items/<att_key>/file` with `upload=<auth["uploadKey"]>` and `If-None-Match: *`.
5. **Mirror locally** — Copy file to `~/Zotero/storage/<att_key>/`.

## Verification

```bash
# Check the file is on Zotero's servers
curl -H "Zotero-API-Key: <key>" \
  "https://api.zotero.org/users/<id>/items/<att_key>/file" \
  | wc -c
```
