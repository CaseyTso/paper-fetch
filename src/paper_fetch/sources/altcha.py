"""ALTCHA proof-of-work solver for Sci-Hub's DDoS-Guard challenge pages.

Sci-Hub (sci-hub.jp, behind DDoS-Guard) presents an ALTCHA widget on its
landing pages. The protocol, reverse-engineered from the deployed
``altcha.min.js`` bundle, works as follows:

1. The landing page embeds ``<altcha-widget challengeurl="/captcha/challenge/<id>">``.
2. ``GET /captcha/challenge/<id>`` returns
   ``{"algorithm": "SHA-256", "challenge": "<64-hex>", "maxNumber": N,
   "salt": "<salt>?expires=...", "signature": "<hex>"}`` where
   ``challenge`` is the full SHA-256 of ``salt + number`` for a
   server-chosen random ``number`` in ``[0, maxNumber)``.
3. The client brute-forces ``number`` so that
   ``SHA-256(salt + number) == challenge`` (expected work ≈ maxNumber/2
   hashes; sub-second with hashlib).
4. The client POSTs ``{"captcha": <base64(json solution)>}`` to
   ``/captcha/solution/<id>``; the solution JSON carries
   ``{algorithm, challenge, number, salt, signature, took}``. On success
   the server answers ``{"success": true}`` and sets a cookie that
   unlocks the article page.

A fallback implements the classic ALTCHA leading-zeros model (used when
the challenge JSON carries a ``difficulty`` field), hashing
``challenge + salt + number`` and requiring ``difficulty`` leading zero
bits in the digest.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from urllib.parse import urljoin

import requests

_CHALLENGE_ID_RE = re.compile(r"captcha/challenge/(\d+)")

_DEFAULT_MAX_NUMBER = 200_000


def extract_challenge_id(html: str) -> str | None:
    """Return the ALTCHA challenge id embedded in a challenge page, if any."""
    match = _CHALLENGE_ID_RE.search(html)
    return match.group(1) if match else None


def _digest(algorithm: str, *parts: str) -> str:
    """Hex SHA digest of the concatenated ASCII parts."""
    name = algorithm.lower().replace("-", "")
    hasher = hashlib.new(name)
    hasher.update("".join(parts).encode("ascii"))
    return hasher.hexdigest()


def solve_pow(payload: dict) -> int | None:
    """Find the nonce for an ALTCHA challenge JSON, or ``None``.

    Two models are supported:

    * **Target-hash model** (Sci-Hub deployment): ``challenge`` is the
      full digest of ``salt + number``; search ``[0, maxNumber)`` for an
      exact match.
    * **Leading-zeros model** (classic ALTCHA): when the JSON carries a
      ``difficulty`` field, ``challenge + salt + number`` must digest to
      a hex string with ``difficulty`` leading zero bits.
    """
    algorithm = payload.get("algorithm") or "SHA-256"
    max_number = int(payload.get("maxNumber") or payload.get("maxnumber") or _DEFAULT_MAX_NUMBER)
    salt = str(payload["salt"])
    challenge = str(payload["challenge"])
    difficulty = payload.get("difficulty")

    if difficulty is not None:
        bits = int(difficulty)
        full_chars, remaining_bits = divmod(bits, 4)
        zero_prefix = "0" * full_chars
        upper_bound = 2 ** (4 - remaining_bits) if remaining_bits else 0
        for number in range(max_number):
            digest_hex = _digest(algorithm, challenge, salt, str(number))
            if digest_hex.startswith(zero_prefix) and (
                remaining_bits == 0 or int(digest_hex[full_chars], 16) < upper_bound
            ):
                return number
        return None

    for number in range(max_number):
        if _digest(algorithm, salt, str(number)) == challenge:
            return number
    return None


def build_payload_b64(challenge: dict, number: int, took_ms: int) -> str:
    """Encode the browser-shaped solution payload (base64 of JSON)."""
    solution = {
        "algorithm": challenge.get("algorithm") or "SHA-256",
        "challenge": challenge["challenge"],
        "number": number,
        "salt": challenge["salt"],
        "signature": challenge.get("signature", ""),
        "took": took_ms,
    }
    return base64.b64encode(json.dumps(solution).encode("utf-8")).decode("ascii")


def solve_altcha(
    session: requests.Session,
    challenge_html: str,
    *,
    base_url: str,
    timeout: float,
    proxies: dict[str, str] | None = None,
) -> bool:
    """Solve the ALTCHA challenge embedded in *challenge_html*.

    Returns ``True`` when the solution was accepted (a cookie is then
    stored on *session*), ``False`` when the challenge could not be
    solved or the server rejected the solution.
    """
    challenge_id = extract_challenge_id(challenge_html)
    if challenge_id is None:
        return False

    challenge_url = urljoin(base_url, f"/captcha/challenge/{challenge_id}")
    solution_url = urljoin(base_url, f"/captcha/solution/{challenge_id}")

    try:
        response = session.get(challenge_url, timeout=timeout, proxies=proxies)
        if response.status_code != 200:
            return False
        challenge = response.json()
    except (requests.RequestException, ValueError):
        return False

    started = time.monotonic()
    number = solve_pow(challenge)
    if number is None:
        return False
    took_ms = int((time.monotonic() - started) * 1000)

    payload_b64 = build_payload_b64(challenge, number, took_ms)
    try:
        response = session.post(
            solution_url,
            json={"captcha": payload_b64},
            timeout=timeout,
            proxies=proxies,
        )
        if response.status_code != 200:
            return False
        data = response.json()
    except (requests.RequestException, ValueError):
        return False

    return data.get("success") is True
