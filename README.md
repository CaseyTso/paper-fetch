# paper-fetch

Minimal single-paper PDF acquisition and Zotero attachment.

## Naming

| Layer | Name | Why |
|---|---|---|
| Distribution / CLI / repo / config dir / env prefix | `paper-fetch` | public product name |
| Python import package | `paper_fetch` | Python identifiers cannot contain hyphens |
| Config file | `~/.paper-fetch/config.json` | personal runtime only; never committed |
| Env vars | `PAPER_FETCH_*` | override JSON values |

Do not mix legacy product names into this tree.

## Install

Development (editable, from this repository):

```bash
cd paper-fetch
uv sync --extra dev
uv run paper-fetch --help
```

User-level CLI from GitHub:

```bash
uv tool install "git+https://github.com/CaseyTso/paper-fetch.git"
paper-fetch --help
```

Or editable from a local clone:

```bash
uv tool install --editable --force .
```

## Configure

Create `~/.paper-fetch/config.json` (recommended permissions `0600`):

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

All fields are optional. Fill in your own credentials; this repository never ships personal values.
Environment variables (`PAPER_FETCH_*`, e.g. `PAPER_FETCH_ZOTERO_API_KEY`) override JSON values.

## Institution access (EasyConnect/aTrust)

- `institution_socks5` accepts a SOCKS5 proxy URL or an HTTP proxy URL exposed by aTrust.
- `institution_tls_verify` defaults to `true`. Set it to `false` only for aTrust's local MITM proxy when its local CA is not trusted by Python; this disables certificate verification for institution requests only.

## Usage

```bash
# By DOI
paper-fetch fetch '10.1371/journal.pmed.0020124' --json

# By PMID
paper-fetch fetch '16060722' --json

# By PMCID
paper-fetch fetch 'PMC1182327' --json

# By exact title
paper-fetch fetch 'Why most published research findings are false' --json

# By Zotero item key
paper-fetch fetch 'zotero:ABCD1234' --json

# Skip Zotero; write only to a local directory
paper-fetch fetch '10.1371/journal.pmed.0020124' --json --no-zotero --output /tmp/paper-fetch-out
```

## Source order

1. **Open Access** — PMC → Europe PMC → PubMed linkout → Unpaywall
2. **Institution** — EasyConnect/aTrust SOCKS5 or HTTP proxy
3. **Sci-Hub** — Clash HTTP proxy
4. **ableSci** — 科研通 (Chrome cookies HTTP API, OpenCLI Browser Bridge fallback)

Stops at the first valid multi-page PDF.

## Hermes Skill

This repository includes `SKILL.md` and `references/`. For Hermes, link the skill install path to the repository root (or the skill directory Hermes expects) so the skill and CLI share one editable source.

## Tests

```bash
uv run pytest          # all non-live tests
uv run pytest -m live  # live integration tests (requires credentials)
```

## License & notice

This tool downloads papers through multiple sources. Respect publisher terms,
institutional access policies, and applicable copyright law. Configure only
sources you are authorised to use.
