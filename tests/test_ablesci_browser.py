"""Unit tests for the Minis WebView ableSci driver (ablesci_browser.py).

These tests mock the `minis-browser-use` CLI boundary — no real browser is
driven here (that is covered by the `live` marker suite).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paper_fetch.models import PaperIdentity, SourceResult, Status
from paper_fetch.sources.ablesci import AbleSciSource
from paper_fetch.sources.ablesci_browser import (
    AbleSciBrowserSource,
    MinisBrowserError,
    _js_result,
    is_minis_env,
)


def _identity(**kw):
    base = {
        "original_input": "10.1214/23-aihp1427",
        "doi": "10.1214/23-aihp1427",
        "title": "Non-uniqueness in law of the two-dimensional surface quasi-geostrophic equations",
    }
    base.update(kw)
    return PaperIdentity(**base)


class TestJsResult:
    def test_strips_tab_id_trailer(self):
        """Regression: the CLI appends '\n  tab_id: N' to the return value."""
        data = {
            "text": 'https://www.ablesci.com/assist/download?id=abc123\n  tab_id: 0',
        }
        assert _js_result(data) == "https://www.ablesci.com/assist/download?id=abc123"

    def test_plain_value(self):
        assert _js_result({"text": "yes"}) == "yes"

    def test_empty(self):
        assert _js_result({"text": ""}) == ""


class TestIsMinisEnv:
    def test_false_without_minis_dir(self, monkeypatch):
        monkeypatch.setattr("paper_fetch.sources.ablesci_browser.Path.is_dir", lambda self: False)
        monkeypatch.setattr("paper_fetch.sources.ablesci_browser.shutil.which", lambda _: None)
        assert is_minis_env() is False

    def test_false_without_cli(self, monkeypatch):
        monkeypatch.setattr("paper_fetch.sources.ablesci_browser.Path.is_dir", lambda self: True)
        monkeypatch.setattr("paper_fetch.sources.ablesci_browser.shutil.which", lambda _: None)
        assert is_minis_env() is False


class TestDriverOrder:
    def test_auto_minis_prefers_browser(self):
        with patch("paper_fetch.sources.ablesci.is_minis_env", return_value=True):
            assert AbleSciSource._driver_order("auto") == ["browser", "http", "opencli"]

    def test_auto_desktop_prefers_http(self):
        with patch("paper_fetch.sources.ablesci.is_minis_env", return_value=False):
            assert AbleSciSource._driver_order("auto") == ["http", "opencli"]

    def test_explicit_values(self):
        assert AbleSciSource._driver_order("http") == ["http"]
        assert AbleSciSource._driver_order("browser") == ["browser"]
        assert AbleSciSource._driver_order("opencli") == ["opencli"]


class TestBrowserSourceFailure:
    def test_fetch_not_minis_env(self):
        src = AbleSciBrowserSource("https://www.ablesci.com")
        with patch("paper_fetch.sources.ablesci_browser.is_minis_env", return_value=False):
            result = src.fetch(_identity(), Path("/tmp/out.pdf"))
        assert result.success is False
        assert result.status == Status.EXTERNAL_COMMAND_MISSING

    def test_fetch_login_redirect(self):
        """Navigating to /my/assist-my redirected to /site/login → auth required."""
        src = AbleSciBrowserSource("https://www.ablesci.com")
        fake_page = {"page_url": "https://www.ablesci.com/site/login"}
        with patch("paper_fetch.sources.ablesci_browser.is_minis_env", return_value=True), \
             patch("paper_fetch.sources.ablesci_browser._mb", return_value=fake_page) as mb:
            result = src.fetch(_identity(), Path("/tmp/out.pdf"))
        assert result.success is False
        assert result.status == Status.AUTHENTICATION_REQUIRED

    def test_mb_error_maps_to_status(self):
        err = MinisBrowserError("boom", Status.NETWORK_ERROR)
        src = AbleSciBrowserSource("https://www.ablesci.com")
        with patch("paper_fetch.sources.ablesci_browser.is_minis_env", return_value=True), \
             patch("paper_fetch.sources.ablesci_browser._mb", side_effect=err):
            result = src.fetch(_identity(), Path("/tmp/out.pdf"))
        assert result.success is False
        assert result.status == Status.NETWORK_ERROR
