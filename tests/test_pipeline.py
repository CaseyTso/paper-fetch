"""Pipeline tests with fake sources."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from paper_fetch.config import Config
from paper_fetch.models import PaperIdentity, SourceResult, Status
from paper_fetch.pipeline import Pipeline
from paper_fetch.pdf import validate_pdf


# ---------------------------------------------------------------------------
# Fake sources
# ---------------------------------------------------------------------------


class _FakeOA:
    name = "open_access"

    def fetch(self, identity, destination):
        return SourceResult.failure(
            source=self.name, status=Status.NOT_FOUND, detail="no OA copy"
        )


class _FakeInstitution:
    name = "institution"

    def fetch(self, identity, destination):
        return SourceResult.failure(
            source=self.name, status=Status.PROXY_UNAVAILABLE, detail="proxy down"
        )


class _FakeSciHub:
    name = "scihub"

    def fetch(self, identity, destination):
        # Write a valid 2-page PDF
        from pypdf import PdfWriter

        writer = PdfWriter()
        for _ in range(2):
            writer.add_blank_page(width=612, height=792)
        with destination.open("wb") as fh:
            writer.write(fh)
        return SourceResult.success_result(source=self.name, path=destination)


class _AllFail:
    name = "all_fail"

    def fetch(self, identity, destination):
        return SourceResult.failure(
            source=self.name, status=Status.NOT_FOUND
        )


class _FakeZotero:
    """Minimal fake for Pipeline's Zotero typing."""

    def find_pdf_attachment(self, key):
        return None

    def find_by_doi(self, doi):
        return []

    def upsert_item(self, identity):
        return "FAKE_KEY"

    def attach_pdf(self, key, path):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipelineOrder:
    def test_sources_follow_configured_order(self):
        sources = [
            _FakeOA(),
            _FakeInstitution(),
            _FakeSciHub(),
        ]
        p = Pipeline(config=Config(), sources=sources)
        with tempfile.TemporaryDirectory() as td:
            result = p.fetch("10.1000/xyz123", output_dir=Path(td))
        assert result.success is True
        assert result.source == "scihub"
        assert len(result.attempts) == 3
        assert result.attempts[0].status == Status.NOT_FOUND
        assert result.attempts[1].status == Status.PROXY_UNAVAILABLE
        assert result.attempts[2].status == Status.SUCCESS

    def test_first_success_short_circuits(self):
        class _EarlySuccess:
            name = "open_access"

            def fetch(self, identity, destination):
                from pypdf import PdfWriter

                writer = PdfWriter()
                for _ in range(2):
                    writer.add_blank_page(width=612, height=792)
                with destination.open("wb") as fh:
                    writer.write(fh)
                return SourceResult.success_result(source=self.name, path=destination)

        sources = [_EarlySuccess(), _FakeSciHub()]
        p = Pipeline(config=Config(), sources=sources)
        with tempfile.TemporaryDirectory() as td:
            result = p.fetch("10.1000/xyz123", output_dir=Path(td))
        assert result.success is True
        assert result.source == "open_access"
        # Only one attempt recorded (short-circuited)
        assert len(result.attempts) == 1

    def test_all_fail(self):
        p = Pipeline(config=Config(), sources=[_AllFail(), _AllFail()])
        with tempfile.TemporaryDirectory() as td:
            result = p.fetch("10.1000/xyz123", output_dir=Path(td))
        assert result.success is False
        assert result.error == "all_sources_failed"
        assert len(result.attempts) == 2

    def test_invalid_pdf_continues(self):
        """Source that returns an invalid file is skipped and next source tried."""

        class _InvalidPDF:
            name = "broken"

            def fetch(self, identity, destination):
                destination.write_text("not a pdf")
                return SourceResult.success_result(source=self.name, path=destination)

        class _Good:
            name = "good"

            def fetch(self, identity, destination):
                from pypdf import PdfWriter

                writer = PdfWriter()
                for _ in range(2):
                    writer.add_blank_page(width=612, height=792)
                with destination.open("wb") as fh:
                    writer.write(fh)
                return SourceResult.success_result(source=self.name, path=destination)

        p = Pipeline(config=Config(), sources=[_InvalidPDF(), _Good()])
        with tempfile.TemporaryDirectory() as td:
            result = p.fetch("10.1000/xyz123", output_dir=Path(td))
        assert result.success is True
        assert result.source == "good"
        assert result.attempts[0].status == Status.INVALID_PDF

    def test_resolution_error(self):
        p = Pipeline(config=Config(), sources=[_FakeOA()])
        result = p.fetch("")  # empty input
        assert result.success is False
        assert result.error is not None

    def test_no_zotero_skips_zotero_calls(self):
        source = _FakeSciHub()
        p = Pipeline(config=Config(), sources=[source], zotero=None)
        with tempfile.TemporaryDirectory() as td:
            result = p.fetch("10.1000/xyz123", output_dir=Path(td), no_zotero=True)
        assert result.success is True
        assert result.zotero_item_key is None
