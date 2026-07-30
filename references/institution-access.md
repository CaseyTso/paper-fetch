# Institution access probe (EasyConnect/aTrust)

**Priority rule:** after open-access sources fail to produce a valid PDF, this route must be probed before Sci-Hub or ableSci whenever `institution_socks5` is configured or an EasyConnect/aTrust listener is present. A paywall landing page is not a reason to skip the institutional route.

## 1. Discover the local proxy

On macOS, confirm the authenticated client is running and inspect its local listeners:

```bash
ps aux | grep -Ei '[a]Trust|[e]asyConnect'
lsof -nP -iTCP -sTCP:LISTEN
```

Do not assume that an aTrust listener is SOCKS5. Probe candidate ports with a SOCKS5 greeting and an HTTP absolute-form request. A port that returns an HTTP response to `GET http://example.com/` is an HTTP proxy and must be configured with `http://...`, even though the application field is named `institution_socks5`.

## 2. Verify transport before testing entitlement

```python
import requests
proxy = "http://127.0.0.1:<PORT>"
r = requests.get(
    "https://api.crossref.org/works/<DOI>",
    proxies={"http": proxy, "https": proxy},
    verify=False,  # only for aTrust's local MITM certificate
    timeout=20,
)
print(r.status_code, len(r.content))
```

A certificate error with `verify=True` can be caused by aTrust's local interception certificate, not by an unauthenticated institutional session. Prefer a trustable CA bundle if one is available. If Python cannot validate the macOS-installed aTrust certificate, use `institution_tls_verify: false` only for the institution source and document the tradeoff.

## 3. Configure paper-fetch

In `~/.paper-fetch/config.json`:

```json
{
  "institution_socks5": "http://127.0.0.1:<PORT>",
  "institution_tls_verify": false
}
```

Back up the config first and keep its mode restrictive (`chmod 600`) because the same file contains Zotero credentials. The port may change after aTrust restarts; re-probe if the source later reports `proxy_unavailable`.

## 4. Verify the source directly

A normal CLI run may stop at `open_access` before reaching the institution source. Use a direct source probe for a DOI:

```python
from pathlib import Path
import requests
from paper_fetch.config import load_config
from paper_fetch.models import PaperIdentity
from paper_fetch.sources.institution import InstitutionSource

config = load_config()
identity = PaperIdentity(original_input=DOI, doi=DOI)
result = InstitutionSource(requests.Session(), config).fetch(
    identity, Path("/tmp/institution-probe.pdf")
)
print(result.success, result.status.value, result.detail)
```

Interpretation:

- `proxy_unavailable` or TLS `network_error`: fix aTrust state, port, proxy scheme, or TLS setting.
- `no_pdf`: proxy transport worked, but the landing page exposed no candidate PDF; this does **not** prove that institutional subscription is absent.
- `success`: validate the PDF header/page structure, then test a known subscription-only DOI to establish entitlement.

A successful open-access paper through this source proves proxy transport and PDF validation only; it is not evidence that the institution has access to a paywalled journal.
