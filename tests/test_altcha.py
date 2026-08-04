"""Unit tests for the ALTCHA proof-of-work solver."""

import base64
import hashlib
import json
from unittest.mock import MagicMock

import pytest
import requests

from paper_fetch.sources.altcha import (
    build_payload_b64,
    extract_challenge_id,
    solve_altcha,
    solve_pow,
)


def _challenge(salt="test-salt?expires=1785849000", nonce=42, max_number=100_000):
    """A sci-hub.jp-style challenge JSON: challenge == sha256(salt + nonce)."""
    challenge_hex = hashlib.sha256(f"{salt}{nonce}".encode()).hexdigest()
    return {
        "algorithm": "SHA-256",
        "challenge": challenge_hex,
        "maxNumber": max_number,
        "salt": salt,
        "signature": "7d0eeaa45578987a2e561c79c34659792548e3f109440ec2072ce2dff29a98c7",
    }


class TestExtractChallengeId:
    def test_finds_id(self):
        html = '<altcha-widget challengeurl="/captcha/challenge/93890155">'
        assert extract_challenge_id(html) == "93890155"

    def test_no_challenge(self):
        assert extract_challenge_id("<html><body>nothing</body></html>") is None


class TestSolvePow:
    def test_target_model_finds_nonce(self):
        assert solve_pow(_challenge(nonce=42)) == 42

    def test_target_model_nonce_beyond_range(self):
        assert solve_pow(_challenge(nonce=10_000, max_number=100)) is None

    def test_target_model_uses_full_salt_with_expiry(self):
        """The ?expires= suffix is part of the hashed salt."""
        assert solve_pow(_challenge(salt="8bac1e9a5437c4ef7994f480?expires=1785849000", nonce=7)) == 7

    def test_leading_zeros_model(self):
        # Craft a salt where nonce 0 already satisfies difficulty=8
        # (two leading zero hex chars), so the test is deterministic.
        salt = "s"
        while True:
            if hashlib.sha256(f"challenge{salt}0".encode()).hexdigest().startswith("00"):
                break
            salt += "x"
        payload = {
            "algorithm": "SHA-256",
            "challenge": "challenge",
            "salt": salt,
            "maxNumber": 100,
            "difficulty": 8,
        }
        number = solve_pow(payload)
        assert number == 0
        digest = hashlib.sha256(f"challenge{salt}{number}".encode()).hexdigest()
        assert digest.startswith("00")

    def test_unsupported_maxnumber_string(self):
        payload = _challenge()
        payload["maxNumber"] = "1000"
        assert solve_pow(payload) == 42


class TestBuildPayloadB64:
    def test_roundtrip(self):
        challenge = _challenge()
        b64 = build_payload_b64(challenge, 42, 123)
        obj = json.loads(base64.b64decode(b64))
        assert obj == {
            "algorithm": "SHA-256",
            "challenge": challenge["challenge"],
            "number": 42,
            "salt": challenge["salt"],
            "signature": challenge["signature"],
            "took": 123,
        }


class TestSolveAltcha:
    def test_full_flow_success(self):
        session = MagicMock()
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = _challenge(nonce=42)
        post_resp = MagicMock(status_code=200)
        post_resp.json.return_value = {"success": True}
        session.get.return_value = get_resp
        session.post.return_value = post_resp

        html = '<altcha-widget challengeurl="/captcha/challenge/777">'
        assert solve_altcha(session, html, base_url="https://sci-hub.jp", timeout=30) is True

        # GET hits the challenge endpoint
        get_url = session.get.call_args.args[0]
        assert get_url.endswith("/captcha/challenge/777")
        # POST body matches the browser shape: {"captcha": <base64 JSON>}
        post_url = session.post.call_args.args[0]
        assert post_url.endswith("/captcha/solution/777")
        body = session.post.call_args.kwargs["json"]
        assert set(body) == {"captcha"}
        payload = json.loads(base64.b64decode(body["captcha"]))
        assert payload["number"] == 42
        assert payload["salt"] == _challenge()["salt"]

    def test_server_rejects_solution(self):
        session = MagicMock()
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = _challenge(nonce=42)
        post_resp = MagicMock(status_code=200)
        post_resp.json.return_value = {"success": False}
        session.get.return_value = get_resp
        session.post.return_value = post_resp

        html = '<altcha-widget challengeurl="/captcha/challenge/777">'
        assert solve_altcha(session, html, base_url="https://sci-hub.jp", timeout=30) is False

    def test_no_challenge_id(self):
        session = MagicMock()
        assert (
            solve_altcha(session, "<html>no widget</html>", base_url="https://x", timeout=30)
            is False
        )
        session.get.assert_not_called()

    def test_challenge_fetch_raises(self):
        session = MagicMock()
        session.get.side_effect = requests.RequestException("boom")
        html = '<altcha-widget challengeurl="/captcha/challenge/777">'
        assert solve_altcha(session, html, base_url="https://sci-hub.jp", timeout=30) is False

    def test_non_200_challenge_fetch(self):
        session = MagicMock()
        session.get.return_value = MagicMock(status_code=403)
        html = '<altcha-widget challengeurl="/captcha/challenge/777">'
        assert solve_altcha(session, html, base_url="https://sci-hub.jp", timeout=30) is False

    def test_solve_pow_misses_returns_false(self):
        session = MagicMock()
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = _challenge(nonce=500_000, max_number=100)
        session.get.return_value = get_resp
        html = '<altcha-widget challengeurl="/captcha/challenge/777">'
        assert solve_altcha(session, html, base_url="https://sci-hub.jp", timeout=30) is False
        session.post.assert_not_called()
