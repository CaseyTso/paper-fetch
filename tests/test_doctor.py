"""Doctor command tests: status aggregation, exit semantics, redaction."""

import json
import os
import re
import socket
import sys
import threading
import time
import types
from pathlib import Path

from paper_fetch.cli import main
from paper_fetch.doctor import _read_ablesci_cookies


def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / ".paper-fetch" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)
    return path


def _names(report: dict) -> dict:
    return {c["name"]: c for c in report["checks"]}


def test_missing_config_needs_configuration(capsys):
    """No config file: overall needs_configuration, exit 0, every source has an action."""
    missing = "/nonexistent/paper-fetch-doctor-test/config.json"
    code = main(["doctor", "--json", "--config", missing])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["overall"] == "needs_configuration"
    assert out["config_path"] == missing
    checks = _names(out)
    assert checks["config"]["status"] == "missing"
    for source in ("institution", "clash", "ablesci", "zotero"):
        assert checks[source]["status"] == "missing"
        assert checks[source]["action"], f"{source} must carry a next-step action"


def test_human_output_has_next_steps(capsys):
    missing = "/nonexistent/paper-fetch-doctor-test/config.json"
    code = main(["doctor", "--config", missing])
    text = capsys.readouterr().out
    assert code == 0
    assert "overall: needs_configuration" in text
    assert "Next:" in text


def test_broken_config_exit5(capsys, tmp_path):
    """Unparseable config: exit 5 and still emit valid JSON with overall=error."""
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    code = main(["doctor", "--json", "--config", str(path)])
    out = json.loads(capsys.readouterr().out)
    assert code == 5
    assert out["overall"] == "error"
    assert out["checks"][0]["status"] == "error"


def test_proxy_reachable_then_unreachable(capsys, tmp_path):
    """A real local TCP listener is 'ok'; after it closes, 'unreachable'."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    proxy_url = f"http://127.0.0.1:{port}"
    cfg = _write_config(tmp_path, {"clash_proxy": proxy_url})
    try:
        code = main(["doctor", "--json", "--config", str(cfg)])
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        assert _names(out)["clash"]["status"] == "ok"
    finally:
        srv.close()
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    assert _names(out)["clash"]["status"] == "unreachable"


def test_invalid_proxy_urls(capsys, tmp_path):
    cfg = _write_config(
        tmp_path,
        {"institution_socks5": "not-a-url", "clash_proxy": "http://"},
    )
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert _names(out)["institution"]["status"] == "invalid"
    assert _names(out)["clash"]["status"] == "invalid"


def test_institution_vpn_process_candidate_guidance(monkeypatch, capsys, tmp_path):
    """Unconfigured institution + running VPN process: missing with candidate guidance."""
    monkeypatch.setattr("paper_fetch.doctor._find_vpn_processes", lambda: ["aTrustService"])
    cfg = _write_config(tmp_path, {})
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    inst = _names(out)["institution"]
    assert inst["status"] == "missing"
    assert "running" in inst["detail"].lower()
    assert "lsof" in inst["action"].lower()
    assert "ask the user" in inst["action"].lower()


def _full_session_cookies() -> set[str]:
    """All four essential ableSci session cookies (spec: ablesci-api-protocol.md)."""
    return {
        "_identity-frontend",
        "advanced-frontend",
        "_csrf",
        "security_session_verify",
    }


def test_ablesci_cookie_ok(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        "paper_fetch.doctor._read_ablesci_cookies", _full_session_cookies
    )
    cfg = _write_config(tmp_path, {"ablesci_url": "https://www.ablesci.com"})
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    assert _names(out)["ablesci"]["status"] == "ok"


def test_ablesci_statistics_cookies_only_not_ok(monkeypatch, capsys, tmp_path):
    """Regression A: a jar with only statistics cookies must not report ok."""
    monkeypatch.setattr(
        "paper_fetch.doctor._read_ablesci_cookies",
        lambda: {"_ga", "_gid"},
    )
    cfg = _write_config(tmp_path, {"ablesci_url": "https://www.ablesci.com"})
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    assert _names(out)["ablesci"]["status"] != "ok"


def test_ablesci_missing_one_essential_cookie_not_ok(monkeypatch, capsys, tmp_path):
    """Regression A: missing any single essential cookie must not report ok."""
    monkeypatch.setattr(
        "paper_fetch.doctor._read_ablesci_cookies",
        lambda: _full_session_cookies() - {"_csrf"},
    )
    cfg = _write_config(tmp_path, {"ablesci_url": "https://www.ablesci.com"})
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    assert _names(out)["ablesci"]["status"] != "ok"


def test_ablesci_empty_jar_not_ok(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("paper_fetch.doctor._read_ablesci_cookies", lambda: set())
    cfg = _write_config(tmp_path, {"ablesci_url": "https://www.ablesci.com"})
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    assert _names(out)["ablesci"]["status"] != "ok"


def test_ablesci_output_has_no_cookie_metadata(monkeypatch, capsys, tmp_path):
    """JSON and human output must not reveal cookie names, values, or counts."""
    monkeypatch.setattr(
        "paper_fetch.doctor._read_ablesci_cookies", _full_session_cookies
    )
    cfg = _write_config(tmp_path, {"ablesci_url": "https://www.ablesci.com"})
    code = main(["doctor", "--json", "--config", str(cfg)])
    text = capsys.readouterr().out
    assert code == 0
    for name in _full_session_cookies():
        assert name not in text
    assert "_ga" not in text and "_gid" not in text
    assert re.search(r"\d+\s*cookies?", text.lower()) is None

    code = main(["doctor", "--config", str(cfg)])
    text = capsys.readouterr().out
    assert code == 0
    for name in _full_session_cookies():
        assert name not in text
    assert re.search(r"\d+\s*cookies?", text.lower()) is None


def test_ablesci_no_cookie_no_opencli(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("paper_fetch.doctor._read_ablesci_cookies", lambda: None)
    monkeypatch.setattr("paper_fetch.doctor._opencli_available", lambda: False)
    cfg = _write_config(tmp_path, {"ablesci_url": "https://www.ablesci.com"})
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    ablesci = _names(out)["ablesci"]
    assert ablesci["status"] == "missing"
    assert "log in" in ablesci["action"].lower()
    assert "opencli" in ablesci["action"].lower()


def test_ablesci_no_cookie_opencli_fallback(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("paper_fetch.doctor._read_ablesci_cookies", lambda: None)
    monkeypatch.setattr("paper_fetch.doctor._opencli_available", lambda: True)
    cfg = _write_config(tmp_path, {"ablesci_url": "https://www.ablesci.com"})
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    ablesci = _names(out)["ablesci"]
    assert ablesci["status"] == "missing"
    assert "fallback" in ablesci["action"].lower()


class _FakeCookie:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


def test_read_ablesci_cookies_real_implementation(monkeypatch):
    """The real cookie reader: counts cookies from the browser_cookie3 boundary."""

    class _FakeChrome:
        def __init__(self, domain_name: str) -> None:
            assert domain_name == "ablesci.com"

        def __iter__(self):
            return iter([_FakeCookie("sessionid", "v"), _FakeCookie("uid", "x")])

    monkeypatch.setitem(
        sys.modules, "browser_cookie3", types.SimpleNamespace(chrome=_FakeChrome)
    )
    assert _read_ablesci_cookies() == {"sessionid", "uid"}


def test_read_ablesci_cookies_error_returns_none(monkeypatch):
    class _Raising:
        def __init__(self, domain_name: str) -> None:
            raise RuntimeError("cookie db locked")

    monkeypatch.setitem(
        sys.modules, "browser_cookie3", types.SimpleNamespace(chrome=_Raising)
    )
    assert _read_ablesci_cookies() is None


def test_doctor_redacts_secrets(capsys, tmp_path):
    """Credentials never appear in JSON or human output."""
    api_key = "ZOTERO" + "_SECRET" + "_MARKER_XYZ"
    socks_pass = "SOCKSPASS" + "WORD_XYZ"
    proxy_url = "socks5h://alice:" + socks_pass + "@127.0.0.1:1"
    cfg = _write_config(
        tmp_path,
        {
            "zotero_api_key": api_key,
            "institution_socks5": proxy_url,
            "clash_proxy": "http://127.0.0.1:1",
        },
    )
    code = main(["doctor", "--json", "--config", str(cfg)])
    text = capsys.readouterr().out
    assert code == 0
    assert api_key not in text
    assert socks_pass not in text
    assert "alice" not in text

    code = main(["doctor", "--config", str(cfg)])
    text = capsys.readouterr().out
    assert code == 0
    assert api_key not in text
    assert socks_pass not in text
    assert "alice" not in text


def test_zotero_invalid_type(capsys, tmp_path):
    cfg = _write_config(
        tmp_path,
        {
            "zotero_library_id": "1234567",
            "zotero_library_type": "team",
            "zotero_inbox_collection_key": "ABCD1234",
            "zotero_api_key": "YOUR_KEY_123",
        },
    )
    code = main(["doctor", "--json", "--config", str(cfg)])
    out = json.loads(capsys.readouterr().out)
    assert _names(out)["zotero"]["status"] == "invalid"


def test_doctor_all_ok(monkeypatch, capsys, tmp_path):
    """Every check green: overall ok, exit 0."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    accepted = []

    def _accept_loop() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            accepted.append(True)
            conn.close()

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(
            "paper_fetch.doctor._read_ablesci_cookies", _full_session_cookies
        )
        monkeypatch.setattr("paper_fetch.doctor._find_vpn_processes", lambda: [])
        proxy_url = f"http://127.0.0.1:{port}"
        socks_url = f"socks5h://127.0.0.1:{port}"
        cfg = _write_config(
            tmp_path,
            {
                "institution_socks5": socks_url,
                "clash_proxy": proxy_url,
                "ablesci_url": "https://www.ablesci.com",
                "zotero_library_id": "1234567",
                "zotero_library_type": "user",
                "zotero_inbox_collection_key": "ABCD1234",
                "zotero_api_key": "YOUR_KEY_123",
            },
        )
        code = main(["doctor", "--json", "--config", str(cfg)])
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        assert out["overall"] == "ok"
        assert all(c["status"] == "ok" for c in out["checks"])
        assert len(accepted) >= 1  # at least the first probe really connected
        deadline = 2.0
        while len(accepted) < 2 and deadline > 0:
            time.sleep(0.05)
            deadline -= 0.05
        assert len(accepted) >= 2  # both proxy probes really connected
    finally:
        srv.close()
