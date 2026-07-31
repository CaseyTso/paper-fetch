# Sci-Hub via Clash proxy setup

paper-fetch routes Sci-Hub requests through your local Clash HTTP proxy. It
never modifies Clash settings — it only connects to the local port you
configure in `clash_proxy`.

## 1. Start Clash and pick a node

Start your Clash client (ClashX, Clash Verge, Clash for Windows, ...) and
make sure it is connected to a working node. Many default rule sets already
route `sci-hub.se`, `sci-hub.st`, and `sci-hub.ru` through the proxy; if the
Sci-Hub source later reports a network error, check the node and the rules.

## 2. Find the HTTP / Mixed port

The `clash_proxy` field expects an **HTTP proxy port**. A "Mixed" port serves
both HTTP and SOCKS on one port and works as well.

- ClashX (macOS): menu bar icon → config page or "Copy Terminal Command"
  shows the ports; the HTTP port is commonly 7890.
- Clash Verge: Settings → Clash Fields → Mixed Port (default 7890).
- Other clients: look for "HTTP Port" or "Mixed Port" in the settings.

Do not guess the port — read it from your client.

## 3. Verify the proxy before configuring paper-fetch

```bash
curl -x http://127.0.0.1:<PORT> -sS -o /dev/null -w '%{http_code}\n' https://api.ip.sb/ip
```

A response code of 200 means the port works. `connection refused` means the
port is wrong or Clash is not running; a timeout usually means the node is
slow or blocked.

## 4. Configure paper-fetch

In `~/.paper-fetch/config.json`:

```json
{
  "clash_proxy": "http://127.0.0.1:<PORT>"
}
```

Then rerun `paper-fetch doctor --json` — the `clash` check should be `ok`.

## 5. CAPTCHA / challenge pages

Sci-Hub sometimes presents a CAPTCHA/ALTCHA. paper-fetch returns
`challenge_required` in that case. Open the reported Sci-Hub URL in your
browser, complete the challenge, then rerun the same `paper-fetch fetch`
command.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `proxy_unavailable` / connection refused | wrong port, or Clash not running | re-check the port from step 2 |
| network error / timeout | node down, or Sci-Hub domain blocked | switch node; the CLI tries `scihub_domains` in order |
| `challenge_required` | CAPTCHA presented | complete it in a browser, then rerun |
