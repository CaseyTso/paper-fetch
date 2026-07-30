# ableSci/科研通 HTTP API Protocol

Reverse-engineered 2026-07-27, confirmed working with VIP high-speed channel.

## Prerequisites

- User must be logged into ableSci in Chrome
- `browser_cookie3` must be able to read Chrome cookies (may fail on locked keychain)
- Account needs high-speed channel access (userId=610388 confirmed working)

## Full Download Flow

### Step 0: Get CSRF token and submit a new request

**GET** `{base}/assist/create` and extract the `_csrf` token from the form:
```html
<input name="_csrf" value="WwpJKNF8l4evNh3uRF_DnDVb89DGS1...">
```

**POST** `{base}/assist/create` to submit a new request:
```
_csrf=<csrfToken>
Assist[doi]=10.1097/qai.0000000000003914
Assist[title]=Clinical Characteristics of Adolescents...
Assist[type]=1
Assist[point]=10
```
Headers: `X-Requested-With: XMLHttpRequest`, `Referer: {base}/assist/create`

**CRITICAL: Title is required.** Returns `{"code":1,"msg":"提交失败。标题不能为空。"}` if missing.

### Response handling

**New request created** (code=0, has data.id):
```json
{"code": 0, "data": {"id": "YNQJaG"}}
```

**Success but no ID** (code=0, data=null):
```json
{"code": 0, "msg": "...求助发布成功...", "data": null}
```
→ The request was created but the ID wasn't returned. Fall back to scanning recent requests.

**Duplicate within 1 hour** (code=1, msg contains detail link):
```json
{"code": 1, "msg": "<a href=\"/assist/detail?id=YNQJaG\">点击查看</a>"}
```
→ Extract the request ID from msg. **BUT**: this is the NEW request (pending), not the old one with the file. See Step 1a below.

### Step 1a: Find existing download by DOI (TRY FIRST)

Before submitting a new request, check if this DOI already has an uploaded file:

1. GET `{base}/my/assist-my` — collect all request IDs
2. For each request (up to 10), GET `{base}/assist/detail?id=<rid>`
3. Check if the page contains our DOI string (case-insensitive)
4. If found AND the page has a download link (`/assist/download?id=<hashid>`), use it immediately
5. Proceed to Step 2 (download config + token)

This avoids the duplicate-detection race where ableSci's `code:1` error links to the NEW empty request, not the old one that already has the file.

### Step 1b: Submit new request (only if no existing download found)

The detail page URL is `{base}/assist/detail?id=<request_id>`.
Extract the download link with regex:
```
<a[^>]*点击下载[^>]*href="([^"]+)"
```
This yields `/assist/download?id=<hashid>`.

### Step 2: GET the download page to extract config

```
GET {base}/assist/download?id=<hashid>
```

The page contains an inline `<script>` with:
```javascript
const config = {"tokenUrl":"\/file\/request-download-token",...,"hashid":"<hash>","csrfToken":"<csrf>","userId":"610388","defaultServerId":"4","defaultChannel":"normal","expectedSize":6092423,...};
```

Extract `config` with regex: `const config = ({.+?});` — parse as JSON.

Key fields:
- `hashid`: file identifier
- `csrfToken`: CSRF token for API calls (changes every page load)
- `userId`: user ID
- `expectedSize`: expected file size in bytes
- `allowedNodeHosts`: list of file hub domains including VIP nodes

### Step 3: Request download token (HIGH-SPEED CHANNEL)

```
POST {base}/file/request-download-token
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
X-Requested-With: XMLHttpRequest
X-CSRF-Token: <csrfToken>
Referer: {base}/assist/download?id=<hashid>
Origin: {base}
```

Body parameters (urlencoded):
```
_csrf=<csrfToken>
type=assistFile
id=<hashid>
channel=highspeed
highspeed=1
fallback=0
file_server=0
```

Normal channel alternative (线路1/2/3):
```
channel=normal
highspeed=0
file_server=<serverId from config.defaultServerId>
```

### Step 4: Parse response and download

Success response (code=0):
```json
{
  "code": 0,
  "msg": "...高速线路15 自动下载...",
  "data": {
    "host": "https://filehub15vip.ablesci.com/file/download",
    "token": "HDMlK_UCI6_oBjGqDF7vS3-...",
    "output_filename": "paper-title(科研通-ablesci.com).pdf",
    "transport": "vip",
    "channel": "highspeed"
  }
}
```

Download URL construction:
```
download_url = f"{data.host}?token={data.token}"
```
Note: `host` already includes the full path — do NOT prepend an extra `https://` or `/file/download`.

#### Asynchronous request polling (mandatory)

Creating an ableSci request and obtaining a downloadable file are separate events. A successful submit response containing an `id` means **pending**, not failure.

1. After Step 0 returns a request ID, poll `GET {base}/assist/detail?id=<request_id>` (or refresh the recent-request list and resolve the same DOI) every approximately **5 seconds**.
2. Continue for a maximum of **60 seconds** per fetch attempt. On each poll, re-fetch and re-parse the page; do not rely on stale HTML or stale browser element references.
3. Stop early when a valid `/assist/download?...` link appears, then continue with Step 2.
4. If the request remains pending after 60 seconds, return `pending`/`poll_timeout` together with the request ID. Do not report “paper not found” or immediately submit duplicate requests.
5. If Chrome cookies are unavailable and the Browser Bridge is used, run it in background mode and apply the same polling window without stealing focus.

For duplicate responses, remember that the linked ID may be a newly created empty request. Always perform the DOI search in Step 1a first and prefer an existing request that already has a download link.

## Error responses

- `code=1, msg="对不起，普通下载服务器参数不合法。"` — serverId/channel mismatch
- `code=1, msg="对不起，下载通道参数不一致。"` — highspeed but params don't match
- `code=1, msg="对不起，请求参数出错..."` — missing required params (type, id)

## Critical Fix: www Subdomain

All API calls MUST use `https://www.ablesci.com` (with `www`), NOT `https://ablesci.com`. The cookie domain is `www.ablesci.com`, and the server's Referer validation rejects requests from the bare domain. The source code auto-corrects this in the constructor, but any manual config must use `www`.

## CSRF Token Lifecycle

- The CSRF token is embedded in the config JSON on the download page
- Each GET to the download page generates a NEW token
- The token must be used within the same cookie session
- If the POST fails, re-GET the download page for a fresh token

## VIP High-Speed Nodes

Confirmed VIP filehub domains:
- filehub11vip.ablesci.com
- filehub12vip.ablesci.com
- filehub13vip.ablesci.com
- filehub14vip.ablesci.com
- filehub15vip.ablesci.com  ← confirmed working 2026-07-27

Regular filehub domains:
- filehub2.ablesci.com (线路1)
- filehub3.ablesci.com (线路2)
- filehub4.ablesci.com (线路3)

## Cookie Requirements

Essential cookies (from Chrome):
- `_identity-frontend`: session authentication (HttpOnly)
- `advanced-frontend`: session token
- `_csrf`: CSRF cookie (domain: www.ablesci.com)
- `security_session_verify`: security verification

The `browser_cookie3.chrome(domain_name='ablesci.com')` call must successfully decrypt Chrome's cookie store.

## Real-World Latency

- Config extraction + token request: ~2-3 seconds
- File download: depends on size (~6MB = ~5-10 seconds on fast connection)
- Total end-to-end: < 15 seconds for typical papers

## Why Not OpenCLI

The OpenCLI Browser Bridge approach fails with `attach_failed: Cannot access a chrome-extension:// URL of different extension` on the current Chrome instance. This is a known Chrome extension interop issue (another extension blocks script injection). Use the HTTP protocol above instead — it's faster and more reliable.
