import json
import os
import tempfile
from pathlib import Path

from paper_fetch.config import Config, ConfigError, load_config


class TestConfigDefaults:
    def test_all_fields_have_defaults(self):
        c = Config()
        assert isinstance(c.output_dir, Path)
        assert c.unpaywall_email == ""
        assert c.institution_socks5 is None
        assert c.clash_proxy is None
        assert isinstance(c.scihub_domains, tuple)
        assert c.zotero_library_id is None
        assert c.zotero_library_type == "user"
        assert c.zotero_api_key is None
        assert c.ablesci_url is None
        assert c.request_timeout == 30.0
        assert c.pdf_validation_retries == 1


class TestConfigRedaction:
    def test_redacts_api_key(self):
        c = Config(zotero_api_key="secret123", clash_proxy="http://proxy:8080")
        d = c.redacted_dict()
        assert d["zotero_api_key"] == "[REDACTED]"
        assert d["clash_proxy"] == "[REDACTED]"

    def test_redacts_proxy_config(self):
        c = Config(institution_socks5="socks5h://secret@host")
        d = c.redacted_dict()
        assert d["institution_socks5"] == "[REDACTED]"

    def test_keeps_non_sensitive_fields(self):
        c = Config(unpaywall_email="me@example.com", output_dir=Path("/tmp/papers"))
        d = c.redacted_dict()
        assert d["unpaywall_email"] == "me@example.com"
        assert d["output_dir"] == Path("/tmp/papers")

    def test_null_sensitive_is_still_redacted(self):
        c = Config(zotero_api_key=None)
        d = c.redacted_dict()
        assert d["zotero_api_key"] is None


class TestLoadConfig:
    def test_defaults(self, monkeypatch):
        # Ensure no env or file interference
        monkeypatch.delenv("PAPER_FETCH_ZOTERO_API_KEY", raising=False)
        monkeypatch.delenv("PAPER_FETCH_CLASH_PROXY", raising=False)
        c = load_config(path="/nonexistent/path.json")
        assert c.zotero_api_key is None
        assert c.clash_proxy is None
        assert isinstance(c.scihub_domains, tuple)

    def test_json_file(self):
        data = {
            "output_dir": "/tmp/test-papers",
            "unpaywall_email": "test@example.com",
            "clash_proxy": "http://proxy.example:7897",
            "request_timeout": 45.0,
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            tmp = f.name
        try:
            c = load_config(path=tmp)
            assert c.output_dir == Path("/tmp/test-papers")
            assert c.unpaywall_email == "test@example.com"
            assert c.clash_proxy == "http://proxy.example:7897"
            assert c.request_timeout == 45.0
        finally:
            os.unlink(tmp)

    def test_environment_override(self, monkeypatch):
        monkeypatch.setenv("PAPER_FETCH_ZOTERO_API_KEY", "env-key")
        monkeypatch.setenv("PAPER_FETCH_CLASH_PROXY", "http://env-proxy:7890")
        c = load_config(path="/nonexistent/path.json")
        assert c.zotero_api_key == "env-key"
        assert c.clash_proxy == "http://env-proxy:7890"

    def test_explicit_override_wins(self, monkeypatch):
        monkeypatch.setenv("PAPER_FETCH_ZOTERO_API_KEY", "env-key")
        c = load_config(
            path="/nonexistent/path.json",
            overrides={"zotero_api_key": "explicit-key", "request_timeout": 60.0},
        )
        assert c.zotero_api_key == "explicit-key"
        assert c.request_timeout == 60.0

    def test_missing_file_is_ok(self):
        c = load_config(path="/nonexistent/path.json")
        assert isinstance(c, Config)

    def test_malformed_json_raises(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            f.write("{bad json")
            tmp = f.name
        try:
            import pytest
            with pytest.raises(ConfigError):
                load_config(path=tmp)
        finally:
            os.unlink(tmp)

    def test_scihub_domains_from_env(self, monkeypatch):
        monkeypatch.setenv("PAPER_FETCH_SCIHUB_DOMAINS", "https://a.example,https://b.example")
        c = load_config(path="/nonexistent/path.json")
        assert c.scihub_domains == ("https://a.example", "https://b.example")
