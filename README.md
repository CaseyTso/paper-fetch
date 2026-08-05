# paper-fetch

Agent-first single-paper PDF acquisition Skill and CLI for Zotero.

[中文文档](README.zh-CN.md) · [English documentation](README.md)

`paper-fetch` is primarily designed to be invoked by an AI agent: give it one DOI, PMID, PMCID, exact title, complete citation, or Zotero item key, and let the agent parse the structured JSON result. It can also be used directly from a terminal.

- GitHub: [CaseyTso/paper-fetch](https://github.com/CaseyTso/paper-fetch)
- Hermes Skill source: [`SKILL.md`](SKILL.md)
- Skill protocol references: [`references/`](references/)

## What it does

- Resolves a single paper identifier or exact title.
- Tries configured acquisition sources in a fixed order.
- Validates that the result is a real multi-page PDF.
- Returns machine-readable JSON with source, path, attempts, status, and errors.
- Optionally creates or updates a Zotero item and uploads the PDF attachment.

This is not a keyword-search engine or a batch literature-discovery tool. For multiple papers, an agent should call the single-paper command once per paper.

## Install the CLI

Two install profiles are auto-detected by the bundled cross-platform
installer:

```bash
sh scripts/minis-install.sh    # from a clone of this repo / the skill dir
```

- **Minis (iSH/iOS)** — detected by `/var/minis` + `minis-browser-use` on
  PATH. Installs via Alpine packages + a `--system-site-packages` venv and
  prints Minis-specific ableSci setup notes (see
  [Minis (iOS / iSH) support](#minis-ios--ish-support)).
- **macOS / glibc Linux** — `uv tool install` (or venv + pip when `uv` is
  absent), with the classic Chrome-cookie ableSci path.

Manual equivalents:

### User-level installation from GitHub

```bash
uv tool install "git+https://github.com/CaseyTso/paper-fetch.git"
paper-fetch --help
```

### Development installation from a clone

```bash
git clone https://github.com/CaseyTso/paper-fetch.git
cd paper-fetch
uv sync --extra dev
uv run paper-fetch --help
```

### Editable local installation

Use this when developing the Skill and CLI. Changes under `src/` are used immediately by the CLI:

```bash
uv tool install --editable --force .
paper-fetch --help
```

## Minis (iOS / iSH) support

paper-fetch runs inside **Minis** — the cross-platform AI agent app
([OpenMinis/OpenMinis](https://github.com/OpenMinis/OpenMinis), GPL-3.0, by
the OpenMinis authors) as a first-class environment. The installer and the
CLI detect it automatically
(`/var/minis` exists and `minis-browser-use` is on PATH) and adapt:

### What changes inside Minis

| Area | Desktop (macOS/Linux) | Minis (iSH/iOS) |
|---|---|---|
| Install | `uv tool install` | `sh scripts/minis-install.sh` → Alpine packages + venv |
| ableSci transport | Chrome cookies (`browser-cookie3`) → OpenCLI fallback | **Minis WebView driver** (`minis-browser-use`) |
| ableSci login | Once in Google Chrome | Once in the in-app browser (session persists) |
| Open access / Unpaywall | normal | normal |
| Sci-Hub | via Clash proxy | via Clash proxy (requires Clash on the device) |
| Institution proxy | EasyConnect/aTrust SOCKS5 | same (if the VPN client runs on the device) |
| Zotero | full upload | upload works if Zotero Web API credentials are configured |

### Why a browser driver for ableSci?

Inside iSH none of the desktop ableSci paths can work:

- `browser-cookie3` needs DBUS/Secret Service and access to the iOS Chrome
  cookie store — neither exists in the iSH sandbox.
- OpenCLI drives a desktop Chrome — there is none on iOS.
- Plain HTTP clients are blocked by ableSci's Aliyun WAF
  (`security_session_verify` + TLS fingerprint checks) and redirected to
  `/site/login` even with valid cookies; served PDFs are encrypted and must
  be decrypted by the site's own JavaScript.

So paper-fetch drives the **in-app WebView** through the `minis-browser-use`
CLI: it checks the login (via a protected page, since the mobile header
collapses the logout text), submits the request form, polls for the download
link, opens the download page, waits for the native download to land in the
workspace, and accepts the file. Existing requests that already have a
downloadable file for the same DOI are reused, so no points are spent twice.

### Config

`ablesci_driver` (default `auto`) selects the transport: `auto` picks the
WebView driver inside Minis and the Chrome-cookie HTTP API on desktop;
`http`, `browser` and `opencli` force a specific path.

```bash
paper-fetch doctor --json    # reports which ableSci path is available
```

### Known limits inside Minis

- ableSci downloads land via the browser's native download; the file appears
  in `/var/minis/workspace/` (with a `科研通-ablesci.com` suffix) only after
  the download page's JS finishes decrypting it.
- The WebView driver is sequential and can take ~1–2 minutes per ableSci
  request (submit → poll → download → accept).
- Sci-Hub still needs a Clash proxy reachable from the device
  (`clash_proxy` in the config).

## Install into Hermes

The repository root contains the complete Skill: `SKILL.md` plus its `references/` directory. Choose one of the following modes.

### Recommended for Skill development: clone and symlink

This keeps the repository as the only editable source. Hermes reads the same files that you edit and test:

```bash
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/research"
git clone https://github.com/CaseyTso/paper-fetch.git "$HOME/paper-fetch"
ln -sfn "$HOME/paper-fetch" "${HERMES_HOME:-$HOME/.hermes}/skills/research/paper-fetch"
hermes skills list | grep paper-fetch
```

If the repository already exists, update it instead:

```bash
cd "$HOME/paper-fetch"
git pull --ff-only origin main
```

Verify the link:

```bash
python3 - <<'PY'
from pathlib import Path
import os
repo = Path.home() / "paper-fetch"
installed = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "skills/research/paper-fetch"
print("target:", installed.resolve())
print("matches repository:", installed.resolve() == repo.resolve())
PY
```

For a private development checkout, replace `$HOME/paper-fetch` with your local clone path. Do not copy a personalized `SKILL.md` into the public repository; keep personal configuration outside Git.

### Standard Hermes installation from the public Skill file

Hermes can install a Skill from a direct `SKILL.md` URL:

```bash
hermes skills install \
  "https://raw.githubusercontent.com/CaseyTso/paper-fetch/main/SKILL.md" \
  --category research \
  --name paper-fetch \
  --yes
```

Use this for a standalone Skill installation. For development, use the clone-and-symlink method above so the Skill and its references remain tied to the repository. After installation, verify it with:

```bash
hermes skills list | grep paper-fetch
hermes skills inspect paper-fetch
```

### Hermes profiles

The active profile determines the Skill directory. For another profile, set `HERMES_HOME` to that profile's home before linking or installing:

```bash
HERMES_HOME="$HOME/.hermes/profiles/<profile>" \
  hermes skills list
```

Do not modify another user's or profile's Skill directory unintentionally.

## Install into Claude Code

The same repository root is a Claude Code plugin: root `SKILL.md` plus `references/`, with manifests under [`.claude-plugin/`](.claude-plugin/). The Skill still depends on the `paper-fetch` CLI and `~/.paper-fetch/config.json` (see above). Hermes and Claude Code can share one clone.

### Recommended: marketplace from GitHub

```bash
claude plugin marketplace add CaseyTso/paper-fetch
claude plugin install paper-fetch@paper-fetch
```

Reload plugins (`/reload-plugins`) or start a new Claude Code session. Invoke the skill with `/paper-fetch` (measured on Claude Code 2.1.220), or ask in natural language to download a paper PDF to Zotero.

`plugin.json` sets `"skills": ["./"]` so the root `SKILL.md` is discovered on current Claude Code releases; Claude Code 2.1.142+ also auto-surfaces a root-level `SKILL.md` without that field.

Verify:

```bash
claude plugin list
```

You should see `paper-fetch@paper-fetch` enabled.

### Development: local marketplace or symlink

From a local clone (path is yours; examples use `$HOME/paper-fetch`):

```bash
claude plugin marketplace add "$HOME/paper-fetch"
claude plugin install paper-fetch@paper-fetch -s user
```

Or link the clone as a personal skill without marketplace:

```bash
mkdir -p "$HOME/.claude/skills"
ln -sfn "$HOME/paper-fetch" "$HOME/.claude/skills/paper-fetch"
```

CLI reminder (required either way):

```bash
uv tool install "git+https://github.com/CaseyTso/paper-fetch.git"
```

## Naming

| Layer | Name | Why |
|---|---|---|
| Distribution, CLI, repository, config directory, environment prefix | `paper-fetch` | Public product name |
| Python import package | `paper_fetch` | Python identifiers cannot contain hyphens |
| Personal config | `~/.paper-fetch/config.json` | Runtime-only configuration; never committed |
| Environment variables | `PAPER_FETCH_*` | Override JSON values |

## Configure

Create `~/.paper-fetch/config.json` and protect it with mode `0600`:

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

All fields are optional. With only `output_dir` and `unpaywall_email` set,
open-access downloads work; **each optional source stays disabled until its
fields are configured**:

| Source | Fields that enable it | While disabled |
|---|---|---|
| Institution | `institution_socks5` | route skipped |
| Sci-Hub | `clash_proxy` | route skipped |
| ableSci | `ablesci_url` + Chrome login | route skipped |
| Zotero | `zotero_library_id`, `zotero_library_type`, `zotero_inbox_collection_key`, `zotero_api_key` | PDF saved locally only (as with `--no-zotero`) |

Environment variables such as `PAPER_FETCH_ZOTERO_API_KEY` override JSON
values. Never commit API keys, cookies, local paths, or downloaded PDFs.

### First-run check: `paper-fetch doctor`

After installing the CLI, run the read-only health check:

```bash
paper-fetch doctor --json
```

It verifies, without writing anything: the config file (exists, parses,
permissions), the institution proxy (configured, URL valid, port reachable —
and it notices a running EasyConnect/aTrust client even when not configured),
the Clash proxy (same checks), the ableSci session (URL set, Chrome cookies
readable, OpenCLI fallback available), and the Zotero fields.

- JSON `overall` is `ok` when everything is ready, `needs_configuration` when
  optional checks are missing (exit code 0 — open-access downloads still
  work), and `error` when the config file is unreadable (exit code 5).
- Every check includes an `action` — the next step for a human or an agent.
- The report never prints credentials, cookie values, or API keys.

### Institution access (EasyConnect / aTrust)

1. **Log in to your institution VPN client first** (EasyConnect or aTrust)
   and stay connected.
2. **Find the local proxy port**:

   ```bash
   lsof -nP -iTCP -sTCP:LISTEN
   ```

   Look for the listener owned by the VPN client.
3. **Determine HTTP or SOCKS5.** aTrust often exposes an HTTP proxy even
   though the field is named `institution_socks5`. Probe a candidate port:
   if `curl -x http://127.0.0.1:<PORT> -sS -o /dev/null -w '%{http_code}\n' https://api.crossref.org`
   returns 200, configure `http://...`; otherwise use `socks5h://...`.
4. **Configure**:

   ```json
   {
     "institution_socks5": "http://127.0.0.1:<PORT>",
     "institution_tls_verify": true
   }
   ```

   `institution_tls_verify` defaults to `true`; set it to `false` only when
   the VPN client uses a local MITM certificate that Python does not trust.
   The setting applies only to institution requests. The port may change
   after a VPN restart — rerun `paper-fetch doctor` to confirm.
5. Verify with `paper-fetch doctor --json` (the `institution` row should be
   `ok`).

Full probe procedure and troubleshooting:
[`references/institution-access.md`](references/institution-access.md).

### Sci-Hub via Clash

1. Start Clash and connect a node.
2. Read the HTTP or Mixed port from your client's settings (ClashX commonly
   7890) — do not guess it.
3. Verify:
   `curl -x http://127.0.0.1:<PORT> -sS -o /dev/null -w '%{http_code}\n' https://api.ip.sb/ip`
   should return 200.
4. Configure `"clash_proxy": "http://127.0.0.1:<PORT>"` in the config file
   and confirm with `paper-fetch doctor --json`.
5. Sci-Hub challenges are solved automatically: the CLI detects the ALTCHA
   proof-of-work page, solves it, and retries with the resulting cookie.
   Only if automatic solving fails does a fetch return `challenge_required`
   (open the URL in a browser as a last resort, then rerun).

Setup guide: [`references/scihub-clash-setup.md`](references/scihub-clash-setup.md).

### ableSci / 科研通

1. Log in to <https://www.ablesci.com> **once** in the environment's browser:
   - **Inside Minis** → log in in the in-app browser (session persists across
     runs). paper-fetch then drives it via `minis-browser-use`; it never
     stores or asks for your ableSci password.
   - **On desktop** → log in in Google Chrome; paper-fetch reads the session
     from Chrome's cookies.
2. Configure `"ablesci_url": "https://www.ablesci.com"`.
3. Transport selection (`ablesci_driver`, default `auto`):
   - Inside Minis → Minis WebView driver (primary); Chrome cookies / OpenCLI
     cannot work in the iSH sandbox (no DBUS, no iOS Chrome store, Aliyun WAF
     blocks plain HTTP clients).
   - On desktop → Chrome-cookie HTTP API (primary), OpenCLI Browser Bridge
     fallback when `opencli` is installed.
   `paper-fetch doctor --json` reports which path is available.
4. If a fetch returns `authentication_required`, log in to ableSci again in
   the environment's browser (in-app browser inside Minis, Chrome on desktop)
   and rerun the same command.

User guide: [`references/ablesci-login.md`](references/ablesci-login.md).

## Agent usage

Agents should invoke the CLI once per paper and always request JSON:

```bash
paper-fetch fetch '<identifier>' --json
```

Accepted identifiers include DOI, PMID, PMCID, exact title, complete citation, and `zotero:<ITEM_KEY>`.

Important JSON fields:

| Field | Meaning |
|---|---|
| `success` | A valid multi-page PDF was acquired |
| `source` | Source that produced the PDF |
| `pdf_path` | Absolute path to the PDF |
| `zotero_item_key` | Parent Zotero item when attachment succeeded |
| `attempts` | Ordered source attempts, statuses, and timings |
| `error` | Human-readable failure summary |

Useful flags:

- `--output <directory>`: override the output directory.
- `--no-zotero`: save locally without changing Zotero.
- `--force`: acquire again even when cached or already attached.

Before the first fetch — or whenever a source behaves unexpectedly — run
`paper-fetch doctor --json` and act on the reported checks (see
[Configure](#configure)).

Example:

```bash
paper-fetch fetch \
  '10.1371/journal.pmed.0020124' \
  --json \
  --no-zotero \
  --output /tmp/paper-fetch-out
```

## Source order

The pipeline stops at the first valid multi-page PDF:

1. **Open access** — PMC → Europe PMC → PubMed full-text links → Unpaywall
2. **Institution access** — EasyConnect/aTrust SOCKS5 or HTTP proxy
3. **Sci-Hub** — Clash HTTP proxy
4. **ableSci/科研通** — Minis WebView driver (inside Minis), Chrome-cookie HTTP API (desktop primary), OpenCLI Browser Bridge fallback

The ableSci request may be asynchronous. `pending` or `poll_timeout` means the request is still processing, not that the paper is missing. `authentication_required` needs the user to log in to ableSci and rerun. Sci-Hub ALTCHA challenges are solved automatically, so `challenge_required` only appears when automatic solving failed.

## Zotero integration

By default, a successful fetch is attached to Zotero:

1. Match an existing item by DOI and attach the PDF, or create a journal-article item.
2. Upload the PDF through Zotero's official file-upload protocol.
3. Mirror the attachment into the local Zotero storage directory.

Use `--no-zotero` for a non-mutating local download.

## Tests

```bash
uv run pytest          # non-live tests
uv run pytest -m live  # network/credential/browser-backed tests
```

The default test command excludes live tests. The public-package checker also validates Skill references, naming, CLI metadata, and public-tree safety:

```bash
python scripts/check_public_package.py
```

## License

Copyright © 2026 CaseyTso.

This project is licensed under the [GNU Affero General Public License v3.0 only](LICENSE) (`AGPL-3.0-only`). If you modify the software and make it available to users over a network, the AGPL requires you to offer those users the corresponding source code. See `LICENSE` for the complete terms.

## Acknowledgements

Special thanks to the **OpenMinis** project
([OpenMinis/OpenMinis](https://github.com/OpenMinis/OpenMinis),
<https://openminis.app>, GPL-3.0) and its authors for building the Minis
agent app that paper-fetch runs in — the Minis WebView driver
(`ablesci_browser.py`), the iSH installer profile, and the dual install
paths would not exist without it.

paper-fetch also incorporates designs adapted from
[scansci-pdf](https://github.com/Rimagination/scansci-pdf),
Copyright © 2024–2026 scansci-pdf contributors,
licensed under the [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0).

## Legal and access notice

Use only sources and institutional access for which you are authorized. Respect publisher terms, institutional policies, applicable copyright law, and the terms of any external service.
