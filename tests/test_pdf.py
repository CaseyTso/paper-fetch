import tempfile
from pathlib import Path

from pypdf import PdfWriter

from paper_fetch.models import PaperIdentity, Status
from paper_fetch.pdf import (
    download_candidate,
    extract_pdf_candidates,
    safe_pdf_filename,
    validate_pdf,
)


def _make_pdf(directory: Path, pages: int = 2) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    p = directory / f"test_{pages}p.pdf"
    with p.open("wb") as fh:
        writer.write(fh)
    return p


class TestValidatePdf:
    def test_valid_two_page_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_pdf(Path(td), pages=2)
            result = validate_pdf(path)
            assert result.success is True
            assert result.status == Status.SUCCESS

    def test_html_disguised_as_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fake.pdf"
            p.write_text("<!DOCTYPE html>\n" * 50 + "<html><body>login page</body></html>")
            result = validate_pdf(p)
            assert result.success is False
            assert result.status == Status.INVALID_PDF
            assert "HTML" in result.detail

    def test_truncated_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trunc.pdf"
            p.write_bytes(b"%PDF-1.4\nincomplete")
            result = validate_pdf(p)
            assert result.success is False
            assert result.status == Status.INVALID_PDF

    def test_missing_eof(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "noeof.pdf"
            # Make a valid 2page PDF then cut off the tail
            path = _make_pdf(Path(td), pages=2)
            data = bytearray(path.read_bytes())
            # Remove %%EOF
            idx = data.rfind(b"%%EOF")
            if idx >= 0:
                data[idx:] = b"X" * (len(data) - idx)
            p.write_bytes(data)
            result = validate_pdf(p)
            assert result.success is False
            assert result.status == Status.INVALID_PDF
            assert "%%EOF" in result.detail

    def test_single_page_small(self):
        with tempfile.TemporaryDirectory() as td:
            path = _make_pdf(Path(td), pages=1)
            result = validate_pdf(path)
            assert result.success is False
            assert result.status == Status.SUSPICIOUS_PDF

    def test_multi_page_large(self):
        """Multi-page PDF always passes regardless of suspicious size."""
        with tempfile.TemporaryDirectory() as td:
            path = _make_pdf(Path(td), pages=3)
            result = validate_pdf(path)
            assert result.success is True
            assert result.status == Status.SUCCESS


class TestSafeFilename:
    def test_full_identity(self):
        identity = PaperIdentity(
            original_input="10.1/x",
            doi="10.1/x",
            title="Why most published research findings are false",
            authors=["Ioannidis, John"],
            year="2005",
        )
        name = safe_pdf_filename(identity)
        assert name.endswith(".pdf")
        assert "Ioannidis" in name
        assert "2005" in name
        assert len(name) <= 210

    def test_no_title_falls_back_to_doi(self):
        identity = PaperIdentity(original_input="10.1/x", doi="10.1/x")
        name = safe_pdf_filename(identity)
        assert name.endswith(".pdf")
        assert "10_1_x" in name

    def test_no_doi_no_title(self):
        identity = PaperIdentity(original_input="unknown")
        name = safe_pdf_filename(identity)
        assert name == "paper.pdf"

    def test_removes_path_separators(self):
        identity = PaperIdentity(
            original_input="test", title="a/b:c*d?e<f>g", authors=["Test / User"]
        )
        name = safe_pdf_filename(identity)
        assert "/" not in name
        assert ":" not in name
        assert "?" not in name


class TestExtractPdfCandidates:
    def test_citation_pdf_url_meta(self):
        html = '<meta name="citation_pdf_url" content="https://example.com/paper.pdf">'
        candidates = extract_pdf_candidates(html, "https://example.com")
        assert "https://example.com/paper.pdf" in candidates

    def test_embed_iframe(self):
        html = '<embed src="/pdf/article.pdf">'
        candidates = extract_pdf_candidates(html, "https://example.com/dir/")
        assert "https://example.com/pdf/article.pdf" in candidates

    def test_object_data(self):
        """Sci-Hub article pages embed the PDF via <object data=...>."""
        html = '<object type="application/pdf" data="/storage/2024/7339/paper.pdf"></object>'
        candidates = extract_pdf_candidates(html, "https://sci-hub.jp/10.1000/xyz")
        assert "https://sci-hub.jp/storage/2024/7339/paper.pdf" in candidates

    def test_anchor_pdf(self):
        html = '<a href="download.pdf">PDF</a>'
        candidates = extract_pdf_candidates(html, "https://example.com/")
        assert "https://example.com/download.pdf" in candidates

    def test_deduplication(self):
        html = """
        <meta name="citation_pdf_url" content="paper.pdf">
        <a href="paper.pdf">PDF</a>
        """
        candidates = extract_pdf_candidates(html, "https://example.com/")
        assert len(candidates) == 1


# ---------------------------------------------------------------------------
# Streaming downloader tests
# ---------------------------------------------------------------------------


class TestDownloadCandidate:
    def test_successful_download(self, tmp_path):
        import requests

        pdf_path = _make_pdf(tmp_path, pages=2)

        class FakeResponse:
            status_code = 200

            def iter_content(self, chunk_size=8192):
                with pdf_path.open("rb") as fh:
                    while True:
                        chunk = fh.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk

            def close(self):
                pass

        class FakeSession:
            def get(self, url, timeout=None, proxies=None, headers=None,
                    allow_redirects=True, stream=True, verify=True):
                return FakeResponse()

        dest = tmp_path / "result.pdf"
        result = download_candidate(
            session=FakeSession(),
            url="https://example.com/paper.pdf",
            destination=dest,
            source="test",
            timeout=30.0,
        )
        assert result.success is True
        assert result.status == Status.SUCCESS
        assert result.source == "test"
        assert dest.exists()
        # Check no .part left behind
        assert not list(tmp_path.glob("*.part"))

    def test_html_response_rejected(self, tmp_path):
        import requests

        class FakeResponse:
            status_code = 200

            def iter_content(self, chunk_size=8192):
                yield b"<!DOCTYPE html><html><body>login</body></html>"

            def close(self):
                pass

        class FakeSession:
            def get(self, url, timeout=None, proxies=None, headers=None,
                    allow_redirects=True, stream=True, verify=True):
                return FakeResponse()

        dest = tmp_path / "result.pdf"
        result = download_candidate(
            session=FakeSession(),
            url="https://example.com/login",
            destination=dest,
            source="test",
            timeout=30.0,
        )
        assert result.success is False
        assert result.status == Status.INVALID_PDF
        assert not dest.exists()

    def test_http_401_maps_to_auth_required(self, tmp_path):
        import requests

        class FakeResponse:
            status_code = 401

            def iter_content(self, chunk_size=8192):
                yield b""
                return

            def close(self):
                pass

        class FakeSession:
            def get(self, url, timeout=None, proxies=None, headers=None,
                    allow_redirects=True, stream=True, verify=True):
                return FakeResponse()

        dest = tmp_path / "result.pdf"
        result = download_candidate(
            session=FakeSession(),
            url="https://example.com/restricted",
            destination=dest,
            source="institution",
            timeout=30.0,
        )
        assert result.success is False
        assert result.status == Status.AUTHENTICATION_REQUIRED

    def test_http_404_maps_to_not_found(self, tmp_path):
        import requests

        class FakeResponse:
            status_code = 404

            def iter_content(self, chunk_size=8192):
                yield b""
                return

            def close(self):
                pass

        class FakeSession:
            def get(self, url, timeout=None, proxies=None, headers=None,
                    allow_redirects=True, stream=True, verify=True):
                return FakeResponse()

        dest = tmp_path / "result.pdf"
        result = download_candidate(
            session=FakeSession(),
            url="https://example.com/missing",
            destination=dest,
            source="test",
            timeout=30.0,
        )
        assert result.success is False
        assert result.status == Status.NOT_FOUND

    def test_http_429_maps_to_rate_limited(self, tmp_path):
        import requests

        class FakeResponse:
            status_code = 429

            def iter_content(self, chunk_size=8192):
                yield b""
                return

            def close(self):
                pass

        class FakeSession:
            def get(self, url, timeout=None, proxies=None, headers=None,
                    allow_redirects=True, stream=True, verify=True):
                return FakeResponse()

        dest = tmp_path / "result.pdf"
        result = download_candidate(
            session=FakeSession(),
            url="https://example.com/throttled",
            destination=dest,
            source="test",
            timeout=30.0,
        )
        assert result.success is False
        assert result.status == Status.RATE_LIMITED

    def test_timeout_maps_to_timeout(self, tmp_path):
        import requests

        class FakeSession:
            def get(self, url, timeout=None, proxies=None, headers=None,
                    allow_redirects=True, stream=True, verify=True):
                raise requests.exceptions.Timeout()

        dest = tmp_path / "result.pdf"
        result = download_candidate(
            session=FakeSession(),
            url="https://example.com/slow",
            destination=dest,
            source="test",
            timeout=5.0,
        )
        assert result.success is False
        assert result.status == Status.TIMEOUT

    def test_passed_proxies_are_used(self, tmp_path):
        import requests

        captured_proxies: list[dict] = []

        class FakeResponse:
            status_code = 200

            def iter_content(self, chunk_size=8192):
                pdf_path = _make_pdf(tmp_path, pages=2)
                with pdf_path.open("rb") as fh:
                    yield fh.read()

            def close(self):
                pass

        class FakeSession:
            def get(self, url, timeout=None, proxies=None, headers=None,
                    allow_redirects=True, stream=True, verify=True):
                captured_proxies.append(proxies)
                return FakeResponse()

        dest = tmp_path / "result.pdf"
        result = download_candidate(
            session=FakeSession(),
            url="https://example.com/paper.pdf",
            destination=dest,
            source="test",
            timeout=30.0,
            proxies={"http": "socks5h://127.0.0.1:1080", "https": "socks5h://127.0.0.1:1080"},
        )
        assert result.success is True
        assert captured_proxies[0] == {
            "http": "socks5h://127.0.0.1:1080",
            "https": "socks5h://127.0.0.1:1080",
        }
