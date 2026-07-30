from pathlib import Path

from paper_fetch.models import (
    Attempt,
    FetchResult,
    PaperIdentity,
    SourceResult,
    Status,
)


class TestPaperIdentity:
    def test_to_dict(self):
        identity = PaperIdentity(
            original_input="10.1/x",
            doi="10.1/x",
            pmid="12345",
            pmcid="PMC67890",
            title="Test Paper",
            authors=["Doe, J", "Smith, A"],
            journal="Test Journal",
            year="2024",
            zotero_item_key="ABC123",
        )
        d = identity.to_dict()
        assert d["doi"] == "10.1/x"
        assert d["authors"] == ["Doe, J", "Smith, A"]
        assert d["zotero_item_key"] == "ABC123"
        assert d["original_input"] == "10.1/x"

    def test_minimal_identity(self):
        identity = PaperIdentity(original_input="some title")
        d = identity.to_dict()
        assert d["doi"] is None
        assert d["authors"] == []


class TestSourceResult:
    def test_success_result(self):
        r = SourceResult.success_result(source="pmc", path=Path("/tmp/a.pdf"), url="https://x")
        assert r.success is True
        assert r.status == Status.SUCCESS
        assert r.temporary_path == Path("/tmp/a.pdf")
        d = r.to_dict()
        assert d["source"] == "pmc"
        assert d["status"] == "success"

    def test_failure(self):
        r = SourceResult.failure(source="scihub", status=Status.CHALLENGE_REQUIRED, detail="ALTCHA")
        assert r.success is False
        assert r.status == Status.CHALLENGE_REQUIRED
        d = r.to_dict()
        assert d["source"] == "scihub"
        assert d["status"] == "challenge_required"
        assert d["detail"] == "ALTCHA"


class TestFetchResult:
    def test_to_dict_with_paths_and_nested(self):
        identity = PaperIdentity(original_input="10.1/x", doi="10.1/x")
        result = FetchResult(
            success=True,
            source="pmc",
            pdf_path=Path("/tmp/a.pdf"),
            identity=identity,
            zotero_item_key="ITEM1",
            attempts=[Attempt(source="pmc", status=Status.SUCCESS, detail="ok", elapsed_ms=12)],
        )
        payload = result.to_dict()
        assert payload["pdf_path"] == "/tmp/a.pdf"
        assert payload["identity"]["doi"] == "10.1/x"
        assert payload["attempts"][0]["status"] == "success"
        assert payload["zotero_item_key"] == "ITEM1"

    def test_failure_result(self):
        identity = PaperIdentity(original_input="10.1/x", doi="10.1/x")
        result = FetchResult(
            success=False,
            identity=identity,
            attempts=[
                Attempt(source="oa", status=Status.NO_PDF),
                Attempt(source="scihub", status=Status.NOT_FOUND),
            ],
            error="all_sources_failed",
        )
        payload = result.to_dict()
        assert payload["success"] is False
        assert payload["source"] == ""
        assert payload["pdf_path"] is None
        assert payload["error"] == "all_sources_failed"
        assert len(payload["attempts"]) == 2

    def test_no_secrets_in_output(self):
        """Ensure serialized output contains no raw credential markers."""
        result = FetchResult(
            success=False,
            attempts=[Attempt(source="zotero", status=Status.ZOTERO_WRITE_FAILED, detail="upload failed")],
            error="zotero_write_failed",
        )
        payload = result.to_dict()
        text = str(payload)
        for secret_pattern in ("api_key", "token=", "Bearer ", "X-API"):
            assert secret_pattern not in text, f"found {secret_pattern!r} in output"


class TestStatus:
    def test_all_expected_statuses(self):
        assert Status.SUCCESS == "success"
        assert Status.SUSPICIOUS_PDF == "suspicious_pdf"
        assert Status.CONFIGURATION_ERROR == "configuration_error"
        assert Status.ZOTERO_WRITE_FAILED == "zotero_write_failed"
        assert Status.EXTERNAL_COMMAND_MISSING == "external_command_missing"
