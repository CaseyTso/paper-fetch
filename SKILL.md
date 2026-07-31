---
name: paper-fetch
description: Use when user wants to download a paper PDF to Zotero.
version: 0.3.0
---

# paper-fetch

Download a single academic paper PDF through four ordered sources and attach it to Zotero.

CLI tool: `paper-fetch` — install from [CaseyTso/paper-fetch](https://github.com/CaseyTso/paper-fetch)

Install the user-level CLI directly from GitHub when it is not already available:

```bash
uv tool install "git+https://github.com/CaseyTso/paper-fetch.git"
```

## Trigger

Invoke this skill when the user:

- Provides a DOI, PMID, PMCID, exact paper title, complete citation, or Zotero item key
- Asks to download, fetch, or get the paper/text/PDF
- Wants to fill a missing Zotero attachment

Do **not** invoke for keyword searches, broad literature discovery, or when no specific paper is identified.

## Command

```bash
paper-fetch fetch '<identifier>' --json
```

Always use `--json` so you can parse the structured response. Additional flags:

- `--output <dir>` — override the default download directory
- `--no-zotero` — save the PDF locally, skip Zotero
- `--force` — re-acquire even if the paper is already cached or attached in Zotero

## First-run health check

Run `paper-fetch doctor --json` right after the CLI is installed and at the
start of every new session, before the first fetch. `doctor` is read-only and
never prints credentials. Act on the report:

- For every check whose `status` is not `ok`, tell the user the single next
  step from that check's `action` field.
- Do **not** block an open-access fetch because optional fallback sources
  (institution, Sci-Hub, ableSci) are unconfigured, and do not block a
  `--no-zotero` fetch because Zotero is unconfigured.
- When the user explicitly wants the Zotero attachment, or every source has
  failed, walk through the failing checks and give the exact action for each,
  linking to the README sections (`Institution access`, `Sci-Hub via Clash`,
  `ableSci / 科研通`) or to `references/ablesci-login.md` /
  `references/scihub-clash-setup.md`.
- Before writing to `~/.paper-fetch/config.json`, state exactly which fields
  you will add or change and get the user's explicit consent first.
- Never ask the user for an ableSci password — the tool reads the Chrome
  session; never echo cookie values or API keys into the chat.

## Acquisition policy

1. **Open access first** — use PMC, Europe PMC, PubMed linkout and Unpaywall when they provide a valid PDF.
2. **Institution before fallback services** — when an EasyConnect/aTrust proxy is configured, or when `paper-fetch doctor` reports a running EasyConnect/aTrust client, probe and attempt the institutional route before Sci-Hub or ableSci. Do not skip a configured institutional route merely because the publisher landing page initially appears paywalled.
3. **Do not over-interpret `no_pdf`** — an institutional landing-page probe that finds no PDF proves only that the transport worked but no candidate file was exposed; it does not prove that the institution lacks entitlement. Record the attempt and continue only after the institutional route is unavailable, unsuccessful, or explicitly not configured.
4. **ableSci is asynchronous** — submitting a request is not a download failure. Poll the request/detail state before returning. Wait up to **60 seconds** (for example, 5-second intervals); if the request is still pending, return its request ID and a `pending`/`poll_timeout` outcome rather than pretending that no paper exists.

## Interpreting the result

Read these fields from the JSON output:

| Field | Meaning |
|---|---|
| `success` | `true` when a valid multi-page PDF was acquired |
| `source` | which source produced the PDF (open_access, institution, scihub, ablesci) |
| `pdf_path` | absolute path to the downloaded file |
| `zotero_item_key` | Zotero item key if attachment succeeded |
| `attempts` | ordered list of every source tried, with status and timing |
| `error` | human-readable summary when `success` is false |

## Source order

The preferred policy is to stop at the first valid PDF, while preserving this gate order:

1. **Open Access** — PMC → Europe PMC → PubMed linkout → Unpaywall
2. **Institution** — EasyConnect/aTrust SOCKS5 or HTTP proxy; this route must be attempted before any fallback service when configured
3. **Sci-Hub** — Clash HTTP proxy
4. **ableSci/科研通** — HTTP API via Chrome cookies (primary), OpenCLI Browser Bridge (fallback), with polling wait described below

A detailed attempt list is always included in the JSON output. If the institution route is configured but cannot be reached, record `proxy_unavailable`/TLS `network_error` and then continue to the next source; do not silently skip the attempt.

## Zotero integration

By default, every successful `fetch` writes the downloaded PDF into your Zotero library:

1. If the DOI matches an existing Zotero item, the PDF is attached to that item (upsert).
2. If no matching item exists, a new journal-article item is created in your default collection with authors, title, journal, DOI, and PMID.
3. The PDF is uploaded via Zotero's official file upload protocol (auth → S3 upload → register) and mirrored to `~/Zotero/storage/<attachment_key>/` for immediate local access.

Use `--no-zotero` to skip Zotero and save the PDF locally only.

The JSON output includes `zotero_item_key` (parent item) and you can inspect children to find the PDF attachment key.

The fetcher must distinguish a completed download from an asynchronous ableSci request. These statuses require different handling:

| Status | What to do |
|---|---|
| `authentication_required` | Ask the user to log in to ableSci in their browser, then rerun the command. |
| `challenge_required` | Ask the user to open the Sci-Hub page in their browser, complete the CAPTCHA/ALTCHA, then rerun the command. |
| `pending` / `poll_timeout` | Do **not** call this a missing paper. Report the ableSci request ID and that the request remained pending after the 60-second polling window; rerun later or inspect the request directly. |

After the user confirms they have completed the manual step, re-run the same `paper-fetch fetch` command.

## Browser behavior

The CLI uses a **two-tier approach** for ableSci/科研通 downloads:

1. **HTTP API (primary)** — reads Chrome cookies via `browser-cookie3` and calls ableSci's API directly. No browser window is opened. This is the default path and requires you to be logged in to ableSci in Chrome at least once.
2. **OpenCLI Browser Bridge (fallback)** — only used when Chrome cookies are unavailable. Opens Chrome in background mode (`--window background`) without stealing focus.

For both ableSci paths, request creation and file availability are separate events. After submitting a new request, poll the request detail/recent-request state every ~5 seconds for up to **60 seconds**. Use a fresh page/state read on every poll (do not retain stale browser element refs or a stale detail page). Stop early when a valid download link appears; otherwise return `pending`/`poll_timeout` with the request ID for later retry. A newly created request with no download link is not a failed acquisition.

For Sci-Hub, the CLI uses OpenCLI only when a CAPTCHA challenge is detected (`challenge_required`). In that case, open the provided URL manually, complete the challenge, and rerun the command.

All other browser operations are silent and invisible to you.

## Multiple papers

For multiple papers, call the CLI once per paper sequentially. Do not request batch processing from the CLI itself — it handles only one paper at a time.

## Institution access

`institution_socks5` accepts a SOCKS5 URL or an HTTP proxy URL exposed by EasyConnect/aTrust. `institution_tls_verify` defaults to `true`; set it to `false` only when the institutional client uses a local MITM certificate that Python does not trust. The setting applies only to institution requests.

See `references/institution-access.md` for probe procedure and troubleshooting.

## Config reference

Create `~/.paper-fetch/config.json` (permissions `0600` recommended):

```json
{
  "output_dir": "/Users/you/Downloads/papers",
  "unpaywall_email": "you@example.com",
  "institution_socks5": "socks5h://127.0.0.1:<PORT>",
  "institution_tls_verify": true,
  "clash_proxy": "http://127.0.0.1:<PORT>",
  "zotero_library_id": "YOUR_LIBRARY_ID",
  "zotero_library_type": "user",
  "zotero_inbox_collection_key": "YOUR_00_INBOX_COLLECTION_KEY",
  "zotero_api_key": "YOUR_ZOTERO_API_KEY",
  "ablesci_url": "https://www.ablesci.com"
}
```

All fields are optional. Environment variables (`PAPER_FETCH_*`) override JSON values.
Fill credentials yourself; never commit personal config into the repository.

## References

- `references/institution-access.md` — aTrust proxy probe and TLS troubleshooting
- `references/ablesci-login.md` — ableSci/科研通 one-time Chrome login, cookies, and OpenCLI fallback
- `references/scihub-clash-setup.md` — Clash HTTP/Mixed port setup and CAPTCHA handling
- `references/zotero-upload-protocol.md` — Zotero Web API 4-step upload pitfalls
- `references/ablesci-api-protocol.md` — ableSci HTTP API reverse-engineered protocol
- `references/pubmed-linkout.md` — PubMed full-text link extraction
- `references/zotero-local-write-feasibility.md` — local Zotero write path evaluation
