# ableSci / 科研通 login and session setup

ableSci (科研通) is a free document-delivery service. paper-fetch uses it as the
last fallback source when open access, institutional access, and Sci-Hub all
fail.

## One-time login

Where you log in depends on the environment paper-fetch runs in:

- **Inside Minis (iSH/iOS)** — log in once in the **in-app browser** at
  <https://www.ablesci.com>; the WebView session persists across runs.
  paper-fetch then drives the browser through the `minis-browser-use` CLI
  (`ablesci_driver: auto` picks this path automatically). Chrome cookies and
  OpenCLI cannot work in the iSH sandbox: `browser-cookie3` needs DBUS and
  access to the iOS Chrome cookie store, and ableSci's Aliyun WAF
  (``security_session_verify`` + TLS fingerprinting) redirects plain HTTP
  clients to `/site/login`.
- **Desktop (macOS / Linux)** — open <https://www.ablesci.com> in **Google
  Chrome** and log in once, and stay logged in. paper-fetch reads your
  ableSci session cookies from Chrome's cookie store; it **never asks for,
  stores, or writes your ableSci password**.

## Browser support

- The automatic cookie path is verified against **Google Chrome with its
  default configuration** only. Other Chromium-based browsers are not
  guaranteed to expose a readable cookie store; if your default browser is
  not Chrome, either log in with Chrome once, or use the OpenCLI fallback
  path described below.
- `paper-fetch doctor` never makes a login request to ableSci on your
  behalf. It only checks whether a usable session already exists in the
  local cookie store (or reports that the Minis WebView driver is available).

## How the session is used

- Desktop: the CLI reads cookies for the `ablesci.com` domain with
  `browser-cookie3` and calls ableSci's HTTP API directly — no browser
  window is opened.
- Inside Minis: the CLI drives the in-app WebView: it checks the login,
  submits the request form, polls for the download link, opens the download
  page (whose JS decrypts the encrypted served file), waits for the native
  download to land in the workspace, and accepts the file.
- If Chrome cookies cannot be read (Chrome is running with a locked cookie
  database, or the macOS Keychain is locked), the CLI falls back to the
  OpenCLI Browser Bridge when `opencli` is installed. The fallback opens
  Chrome in background mode without stealing focus.
- `paper-fetch doctor --json` reports whether a session is available:
  the `ablesci` check is `ok` only when the active path is ready (session
  cookies present on desktop; the WebView driver available inside Minis),
  and `missing` with an action when the session is incomplete or unreadable.

## Fixing a failed cookie read

1. Quit Chrome completely (Cmd+Q), then unlock the macOS Keychain.
2. Rerun `paper-fetch doctor --json` and check the `ablesci` row.
3. If cookies are still unavailable, install OpenCLI so the fallback path
   works: `opencli doctor` after installing it.

## During a fetch

- `authentication_required`: your ableSci session expired. Open
  <https://www.ablesci.com> in Chrome, log in again, then rerun the same
  `paper-fetch fetch` command.
- `pending` / `poll_timeout`: ableSci is still processing the request — this
  is not a missing paper. Rerun later with the reported request ID.

## Never

- Paste your ableSci password into `~/.paper-fetch/config.json` or into any
  chat prompt. paper-fetch does not accept or store ableSci credentials.
