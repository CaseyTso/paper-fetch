"""Tests for source adapters — open access, institution, Sci-Hub, ableSci."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from paper_fetch.config import Config
from paper_fetch.models import PaperIdentity, Status
from paper_fetch.sources.open_access import OpenAccessSource
from paper_fetch.sources.institution import InstitutionSource
from paper_fetch.sources.scihub import SciHubSource
from paper_fetch.sources.ablesci import AbleSciSource, OpenCLIError


def _make_identity(**kwargs):
    defaults = {"original_input": "test", "doi": "10.1000/xyz123", "pmcid": "PMC1182327"}
    defaults.update(kwargs)
    return PaperIdentity(**defaults)


def _fake_response(json_data, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    return mock


def _fake_pdf_response(pages=2):
    from pypdf import PdfWriter
    from io import BytesIO

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buf = BytesIO()
    writer.write(buf)
    data = buf.getvalue()

    mock = MagicMock()
    mock.status_code = 200
    mock.headers = {"content-type": "application/pdf"}
    mock.text = ""  # won't be used for PDF responses

    def iter_content(chunk_size=8192):
        yield data

    mock.iter_content = iter_content
    mock.raise_for_status = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# OpenAccessSource
# ---------------------------------------------------------------------------


class TestOpenAccess:
    def test_pmc_direct(self):
        session = requests.Session()
        with patch.object(session, "get", return_value=_fake_pdf_response()):
            source = OpenAccessSource(session, Config())
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(_make_identity(), dest)
        assert result.success is True
        assert result.status == Status.SUCCESS

    def test_pmc_not_found_falls_through(self):
        session = requests.Session()
        # PMC returns 404
        pmc_404 = MagicMock()
        pmc_404.status_code = 404

        def side_effect(url, timeout=None, **kwargs):
            if "ncbi.nlm.nih.gov" in url:
                return pmc_404
            return _fake_response({"resultList": {"result": []}})

        with patch.object(session, "get", side_effect=side_effect):
            source = OpenAccessSource(session, Config())
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(_make_identity(doi="10.1000/no-pdf"), dest)
        # No OA copy found — returns failure
        assert result.success is False

    def test_europepmc_extracts_pdf(self):
        session = requests.Session()
        epmc_data = {
            "resultList": {
                "result": [{
                    "fullTextUrlList": {
                        "fullTextUrl": [
                            {"url": "https://example.com/paper.pdf", "documentStyle": "pdf"}
                        ]
                    }
                }]
            }
        }

        def side_effect(url, timeout=None, **kwargs):
            if "europepmc" in url:
                return _fake_response(epmc_data)
            if "example.com/paper.pdf" in url:
                return _fake_pdf_response()
            return _fake_response({"resultList": {"result": []}})

        with patch.object(session, "get", side_effect=side_effect):
            source = OpenAccessSource(session, Config())
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(_make_identity(pmcid=None), dest)
        assert result.success is True

    def test_pubmed_linkout_extracts_pdf(self):
        """When EPMC gives a PMID but no direct PDF, PubMed full-text links are tried."""
        session = requests.Session()
        epmc_data = {
            "resultList": {
                "result": [{
                    "pmid": "42462154",
                    "fullTextUrlList": {},
                }]
            }
        }
        pubmed_html = """<div class="full-text-links-list">
        <a class="link-item dialog-focus" href="https://example.com/paper.pdf">Free article</a>
        </div>"""

        def side_effect(url, timeout=None, **kwargs):
            if "europepmc" in url:
                return _fake_response(epmc_data)
            if "pubmed" in url:
                mock = MagicMock()
                mock.status_code = 200
                mock.text = pubmed_html
                mock.raise_for_status = MagicMock()
                return mock
            if "example.com/paper.pdf" in url:
                return _fake_pdf_response()
            return _fake_response({})

        with patch.object(session, "get", side_effect=side_effect):
            source = OpenAccessSource(session, Config())
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(
                    PaperIdentity(original_input="10.1/x", doi="10.1/x"), dest
                )
        assert result.success is True
        assert result.source == "pubmed"

    def test_unpaywall_candidates(self):
        session = requests.Session()
        unpaywall_data = {
            "best_oa_location": {
                "url_for_pdf": "https://repo.example/paper.pdf",
                "host_type": "repository",
            },
            "oa_locations": [
                {"url_for_pdf": "https://publisher.example/paper.pdf", "host_type": "publisher"},
            ],
        }

        def side_effect(url, timeout=None, **kwargs):
            if "unpaywall" in url:
                return _fake_response(unpaywall_data)
            if "repo.example" in url:
                return _fake_pdf_response()
            if "publisher.example" in url:
                return _fake_pdf_response()
            return _fake_response({})

        with patch.object(session, "get", side_effect=side_effect):
            source = OpenAccessSource(session, Config(unpaywall_email="test@example.com"))
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(_make_identity(pmcid=None), dest)
        assert result.success is True

    def test_unpaywall_missing_email(self):
        session = requests.Session()
        source = OpenAccessSource(session, Config(unpaywall_email=""))
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            result = source.fetch(_make_identity(pmcid=None), dest)
        assert result.success is False
        assert result.status == Status.CONFIGURATION_ERROR

    def test_no_doi_no_pmcid(self):
        session = requests.Session()
        source = OpenAccessSource(session, Config())
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            result = source.fetch(
                PaperIdentity(original_input="test"), dest
            )
        assert result.success is False
        assert result.status == Status.NOT_FOUND


# ---------------------------------------------------------------------------
# InstitutionSource
# ---------------------------------------------------------------------------


class TestInstitution:
    def test_no_socks5_configured(self):
        session = requests.Session()
        source = InstitutionSource(session, Config())
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            result = source.fetch(_make_identity(), dest)
        assert result.success is False
        assert result.status == Status.CONFIGURATION_ERROR
        assert "socks5" in result.detail.lower()

    def test_proxy_unavailable(self):
        session = requests.Session()
        with patch.object(session, "get", side_effect=requests.exceptions.ProxyError()):
            source = InstitutionSource(session, Config(institution_socks5="socks5h://bad:1080"))
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(_make_identity(), dest)
        assert result.success is False
        assert result.status == Status.PROXY_UNAVAILABLE

    def test_citation_pdf_url(self):
        session = requests.Session()

        html_resp = MagicMock()
        html_resp.status_code = 200
        html_resp.headers = {"content-type": "text/html"}
        html_resp.url = "https://publisher.example/article"
        html_resp.text = (
            '<meta name="citation_pdf_url" content="https://publisher.example/article.pdf">'
        )
        html_resp.raise_for_status = MagicMock()

        pdf_resp = _fake_pdf_response()

        def side_effect(url, timeout=None, proxies=None, allow_redirects=True, stream=None, **kwargs):
            if "doi.org" in url:
                return html_resp
            if "article.pdf" in url:
                return pdf_resp
            return MagicMock(status_code=404)

        with patch.object(session, "get", side_effect=side_effect):
            source = InstitutionSource(session, Config(institution_socks5="socks5h://127.0.0.1:1080"))
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(_make_identity(), dest)
        assert result.success is True

    def test_tls_verify_setting_is_forwarded(self):
        session = requests.Session()
        captured_verify = []
        html_resp = MagicMock()
        html_resp.status_code = 200
        html_resp.headers = {"content-type": "text/html"}
        html_resp.url = "https://publisher.example/article"
        html_resp.text = (
            '<meta name="citation_pdf_url" content="https://publisher.example/article.pdf">'
        )

        def side_effect(url, verify=None, **kwargs):
            captured_verify.append(verify)
            if "doi.org" in url:
                return html_resp
            return _fake_pdf_response()

        with patch.object(session, "get", side_effect=side_effect):
            source = InstitutionSource(
                session,
                Config(
                    institution_socks5="http://127.0.0.1:18080",
                    institution_tls_verify=False,
                ),
            )
            with tempfile.TemporaryDirectory() as td:
                result = source.fetch(_make_identity(), Path(td) / "out.pdf")

        assert result.success is True
        assert captured_verify == [False, False]


# ---------------------------------------------------------------------------
# SciHubSource
# ---------------------------------------------------------------------------


class TestSciHub:
    def test_no_clash_proxy(self):
        session = requests.Session()
        source = SciHubSource(session, Config())
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            result = source.fetch(_make_identity(), dest)
        assert result.success is False
        assert result.status == Status.CONFIGURATION_ERROR

    def test_direct_pdf_response(self):
        session = requests.Session()

        pdf_resp = _fake_pdf_response()

        def side_effect(url, timeout=None, proxies=None, allow_redirects=True, stream=None, **kwargs):
            return pdf_resp

        with patch.object(session, "get", side_effect=side_effect):
            source = SciHubSource(
                session,
                Config(
                    clash_proxy="http://proxy.example:7897",
                    scihub_domains=("https://sci-hub.se",),
                ),
            )
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(_make_identity(), dest)
        assert result.success is True

    def test_challenge_detected(self):
        session = requests.Session()

        def side_effect(url, timeout=None, proxies=None, allow_redirects=True, stream=None, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "text/html"}
            resp.text = "<html><body>Just a moment... Checking your browser</body></html>"
            return resp

        with patch.object(session, "get", side_effect=side_effect):
            source = SciHubSource(
                session,
                Config(
                    clash_proxy="http://proxy.example:7897",
                    scihub_domains=("https://sci-hub.se",),
                ),
            )
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(_make_identity(), dest)
        assert result.success is False
        assert result.status == Status.CHALLENGE_REQUIRED

    def test_article_not_found(self):
        session = requests.Session()

        def side_effect(url, timeout=None, proxies=None, allow_redirects=True, stream=None, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "text/html"}
            resp.text = "<html><body>article not found</body></html>"
            return resp

        with patch.object(session, "get", side_effect=side_effect):
            source = SciHubSource(
                session,
                Config(
                    clash_proxy="http://proxy.example:7897",
                    scihub_domains=("https://sci-hub.se",),
                ),
            )
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "out.pdf"
                result = source.fetch(_make_identity(), dest)
        assert result.success is False
        assert result.status == Status.NOT_FOUND


# ---------------------------------------------------------------------------
# AbleSciSource
# ---------------------------------------------------------------------------


class TestAbleSci:
    def test_no_url_configured(self):
        session = requests.Session()
        source = AbleSciSource(session, Config())
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            result = source.fetch(_make_identity(), dest)
        assert result.success is False
        assert result.status == Status.CONFIGURATION_ERROR

    def test_no_doi_no_title(self):
        session = requests.Session()
        source = AbleSciSource(session, Config(ablesci_url="https://ablesci.com"))
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            result = source.fetch(
                PaperIdentity(original_input="test"), dest
            )
        assert result.success is False
        assert result.status == Status.NOT_FOUND

    def test_opencli_raises_expected_status(self):
        with pytest.raises(OpenCLIError) as exc:
            raise OpenCLIError("not found", Status.EXTERNAL_COMMAND_MISSING)
        assert exc.value.status == Status.EXTERNAL_COMMAND_MISSING

    def test_ablesci_always_uses_background_window(self):
        """Lock down that opencli is always called with --window background."""
        session = requests.Session()
        config = Config(ablesci_url="https://ablesci.com")
        source = AbleSciSource(session, config)

        with (
            tempfile.TemporaryDirectory() as td,
            patch("browser_cookie3.chrome") as mock_chrome,
            patch("subprocess.run") as mock_run,
            patch("paper_fetch.sources.ablesci._POLL_TIMEOUT_S", 1),
        ):
            # HTTP path: cookies fail → fallback to OpenCLI
            mock_chrome.side_effect = Exception("no Chrome")
            # subprocess.run returns empty JSON
            fake_proc = MagicMock()
            fake_proc.returncode = 0
            fake_proc.stdout = "{}"
            mock_run.return_value = fake_proc

            dest = Path(td) / "out.pdf"
            source.fetch(_make_identity(), dest)

            # Collect all calls — find the "open" command
            for call_args, _ in mock_run.call_args_list:
                cmd = call_args[0]  # first positional arg = command list
                if "open" in cmd:
                    assert "--window" in cmd, f"open call missing --window: {cmd}"
                    assert "background" in cmd, f"open call missing background: {cmd}"
                    # Check they're adjacent (no flag interference)
                    win_idx = cmd.index("--window")
                    assert cmd[win_idx + 1] == "background", (
                        f"--window not followed by background: {cmd}"
                    )
                    break
            else:
                pytest.fail("No 'open' subprocess.run call found")

    def test_ablesci_http_highspeed_download(self):
        """HTTP path: assert highspeed=1 is sent in the token request."""
        session = requests.Session()
        config = Config(ablesci_url="https://ablesci.com")
        source = AbleSciSource(session, config)

        with (
            tempfile.TemporaryDirectory() as td,
            patch("browser_cookie3.chrome") as mock_chrome,
            patch.object(session, "get") as mock_get,
            patch.object(session, "post") as mock_post,
        ):
            # Mock cookie reading
            mock_cookie = MagicMock()
            mock_cookie.name = "test"
            mock_cookie.value = "val"
            mock_chrome.return_value = [mock_cookie]

            # Mock responses in sequence
            create_resp = MagicMock()
            create_resp.text = '<input name="_csrf" value="csrf123">'
            create_resp.status_code = 200

            submit_resp = MagicMock()
            submit_resp.json.return_value = {"code": 0, "data": {"id": "req999"}}
            submit_resp.status_code = 200

            detail_resp = MagicMock()
            detail_resp.text = '<a href="/assist/download?id=hash999">download</a>'
            detail_resp.status_code = 200

            dl_page_resp = MagicMock()
            dl_page_resp.text = '<script>const config = {"csrfToken":"csrf456","hashid":"hash999","expectedSize":1000};</script>'
            dl_page_resp.status_code = 200

            token_resp = MagicMock()
            token_resp.json.return_value = {
                "code": 0,
                "data": {
                    "host": "https://filehub.example.com",
                    "token": "tok123",
                    "output_filename": "paper.pdf",
                },
            }
            token_resp.status_code = 200

            pdf_resp = _fake_pdf_response()

            def get_side_effect(url, timeout=None, **kwargs):
                if "/assist/create" in url:
                    return create_resp
                if "/assist/detail" in url:
                    return detail_resp
                if "/assist/download" in url:
                    return dl_page_resp
                if "/my/assist-my" in url:
                    # Return page with request IDs (no download links)
                    my_resp = MagicMock()
                    my_resp.text = '<a href="/assist/detail?id=req999">Clinical</a>'
                    my_resp.status_code = 200
                    return my_resp
                if "filehub.example.com" in url:
                    return pdf_resp
                return MagicMock(status_code=404)

            def post_side_effect(url, data=None, headers=None, timeout=None, **kwargs):
                if "/assist/create" in url:
                    assert data.get("Assist[type]") == "1"
                    return submit_resp
                if "/file/request-download-token" in url:
                    # ★★★ KEY ASSERTION: highspeed=1 ★★★
                    assert data.get("highspeed") == "1", f"highspeed not 1: {data}"
                    assert data.get("channel") == "highspeed", f"channel not highspeed: {data}"
                    return token_resp
                return MagicMock(status_code=404)

            mock_get.side_effect = get_side_effect
            mock_post.side_effect = post_side_effect

            dest = Path(td) / "out.pdf"
            result = source.fetch(_make_identity(), dest)

        assert result.success is True
        assert result.status == Status.SUCCESS

    def test_ablesci_falls_back_to_opencli(self):
        """When HTTP path fails (no cookies), fallback to OpenCLI."""
        session = requests.Session()
        config = Config(ablesci_url="https://ablesci.com")
        source = AbleSciSource(session, config)

        with (
            tempfile.TemporaryDirectory() as td,
            patch("browser_cookie3.chrome") as mock_chrome,
            patch("subprocess.run") as mock_run,
            patch("paper_fetch.sources.ablesci._POLL_TIMEOUT_S", 1),
        ):
            # browser_cookie3 raises an exception → HTTP path returns None
            mock_chrome.side_effect = Exception("no Chrome")

            # OpenCLI path: subprocess.run returns empty JSON
            fake_proc = MagicMock()
            fake_proc.returncode = 0
            fake_proc.stdout = "{}"
            mock_run.return_value = fake_proc

            dest = Path(td) / "out.pdf"
            source.fetch(_make_identity(), dest)

            # Verify OpenCLI was called (at least one 'open' command)
            open_calls = [
                call_args[0]
                for call_args, _ in mock_run.call_args_list
                if "open" in call_args[0]
            ]
            assert len(open_calls) >= 1, "No OpenCLI 'open' call found — fallback not triggered"


class TestAbleSciHttpPath:
    """Tests for the HTTP+cookie code path in AbleSciSource."""

    def test_find_existing_download_matches_doi(self):
        """_http_find_existing_download should match by DOI substring in page content."""
        from paper_fetch.sources.ablesci import AbleSciSource

        session = requests.Session()
        source = AbleSciSource(session, Config(ablesci_url="https://ablesci.com"))

        # Step 1: /my/assist-my returns list of request IDs
        list_html = """<html>
        <a class="detail-item" href="/assist/detail?id=ABC123">Request</a>
        </html>"""

        # Step 2: /assist/detail?id=ABC123 shows DOI + download link
        detail_html = """<html>
        <div class="description-item">
          <span>10.1097/qai.0000000000003914</span>
        </div>
        <a class="btn-primary" href="/assist/download?id=ABC123">Download</a>
        </html>"""

        list_resp = MagicMock(status_code=200, text=list_html)
        detail_resp = MagicMock(status_code=200, text=detail_html)

        mock_get = MagicMock()
        mock_get.side_effect = [
            list_resp,   # /my/assist-my
            detail_resp, # /assist/detail?id=ABC123
        ]

        with patch.object(session, "get", mock_get):
            result = source._http_find_existing_download(
                session, "10.1097/qai.0000000000003914"
            )

        assert result == "ABC123"

    def test_find_existing_download_no_match(self):
        """When DOI doesn't appear on page, return None."""
        from paper_fetch.sources.ablesci import AbleSciSource

        session = requests.Session()
        source = AbleSciSource(session, Config(ablesci_url="https://ablesci.com"))

        list_html = """<html>
        <a class="detail-item" href="/assist/detail?id=XYZ789">Some other paper</a>
        </html>"""

        detail_html = """<html>
        <div class="description-item"><span>10.1234/other</span></div>
        </html>"""

        list_resp = MagicMock(status_code=200, text=list_html)
        detail_resp = MagicMock(status_code=200, text=detail_html)

        mock_get = MagicMock()
        mock_get.side_effect = [
            list_resp,
            detail_resp,
        ]

        with patch.object(session, "get", mock_get):
            result = source._http_find_existing_download(
                session, "10.1097/qai.0000000000003914"
            )

        assert result is None
