from unittest.mock import MagicMock, patch, ANY

import pytest
import requests

from paper_fetch.config import Config
from paper_fetch.models import PaperIdentity
from paper_fetch.zotero import ZoteroClient, ZoteroConfigError, attach_pdf


def _make_config(**overrides):
    return Config(
        zotero_api_key="test-key",
        zotero_library_id="12345",
        zotero_library_type="user",
        **overrides,
    )


def _mock_response(json_data, status_code=200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    return mock


class TestZoteroConfig:
    def test_missing_key_raises(self):
        with pytest.raises(ZoteroConfigError):
            ZoteroClient(requests.Session(), Config())

    def test_missing_library_id_raises(self):
        with pytest.raises(ZoteroConfigError):
            ZoteroClient(requests.Session(), Config(zotero_api_key="key"))


class TestZoteroGetItem:
    def test_returns_data(self):
        session = requests.Session()
        with patch.object(session, "get", return_value=_mock_response(
            {"data": {"key": "ABC123", "DOI": "10.1/x"}}
        )):
            z = ZoteroClient(session, _make_config())
            item = z.get_item("ABC123")
        assert item == {"key": "ABC123", "DOI": "10.1/x"}

    def test_404_returns_none(self):
        session = requests.Session()
        mock = MagicMock()
        mock.status_code = 404
        with patch.object(session, "get", return_value=mock):
            z = ZoteroClient(session, _make_config())
            assert z.get_item("MISSING") is None


class TestZoteroFindByDoi:
    def test_exact_doi_match(self):
        session = requests.Session()
        api_resp = [
            {"data": {"key": "A1", "DOI": "10.1/a"}},
            {"data": {"key": "B1", "DOI": "10.1/b"}},
        ]
        with patch.object(session, "get", return_value=_mock_response(api_resp)):
            z = ZoteroClient(session, _make_config())
            results = z.find_by_doi("10.1/a")
        assert len(results) == 1
        assert results[0]["key"] == "A1"

    def test_deduplicates_by_key(self):
        session = requests.Session()
        api_resp = [
            {"data": {"key": "A1", "DOI": "10.1/a"}},
            {"data": {"key": "A1", "DOI": "10.1/a"}},
        ]
        with patch.object(session, "get", return_value=_mock_response(api_resp)):
            z = ZoteroClient(session, _make_config())
            results = z.find_by_doi("10.1/a")
        assert len(results) == 1


class TestZoteroPdfAttachment:
    def test_finds_imported_pdf(self):
        session = requests.Session()
        children = [
            {"data": {"key": "N1", "itemType": "note"}},
            {"data": {"key": "A1", "itemType": "attachment",
                       "contentType": "application/pdf", "linkMode": "imported_file"}},
        ]
        with patch.object(session, "get", return_value=_mock_response(children)):
            z = ZoteroClient(session, _make_config())
            att = z.find_pdf_attachment("PARENT")
        assert att is not None
        assert att["key"] == "A1"

    def test_ignores_linked_url_attachment(self):
        session = requests.Session()
        children = [
            {"data": {"key": "A1", "itemType": "attachment",
                       "contentType": "application/pdf", "linkMode": "linked_url"}},
        ]
        with patch.object(session, "get", return_value=_mock_response(children)):
            z = ZoteroClient(session, _make_config())
            att = z.find_pdf_attachment("PARENT")
        assert att is None

    def test_no_children_returns_none(self):
        session = requests.Session()
        with patch.object(session, "get", return_value=_mock_response([])):
            z = ZoteroClient(session, _make_config())
            att = z.find_pdf_attachment("PARENT")
        assert att is None


class TestZoteroIdentity:
    def test_from_item_data(self):
        z = ZoteroClient(requests.Session(), _make_config())
        data = {
            "key": "ABC123",
            "DOI": "10.1/x",
            "title": "Test Paper",
            "creators": [
                {"creatorType": "author", "lastName": "Doe", "firstName": "John"},
                {"creatorType": "author", "lastName": "Smith", "firstName": "A"},
            ],
            "publicationTitle": "Nature",
            "date": "2024",
        }
        identity = z.identity_from_item(data, original_input="zotero:ABC123")
        assert identity.doi == "10.1/x"
        assert identity.zotero_item_key == "ABC123"
        assert identity.authors == ["Doe John", "Smith A"]


class TestAttachPdfAuth:
    def test_auth_request_does_not_include_params_field(self, tmp_path):
        """Regression: auth_data must NOT contain 'params' or Zotero returns 400."""
        from paper_fetch.zotero import ZoteroClient, attach_pdf

        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 test content " * 100)  # > 100 bytes

        session = requests.Session()
        client = ZoteroClient(session, _make_config())

        # Mock Step 1 (create attachment) → returns att key
        create_resp = MagicMock()
        create_resp.status_code = 200
        create_resp.json.return_value = {"successful": {"0": "ATTKEY"}}
        create_resp.raise_for_status = MagicMock()

        # Mock Step 2 (auth) → captures the sent data
        auth_resp = MagicMock()
        auth_resp.status_code = 200
        auth_resp.json.return_value = {"exists": 1}
        auth_resp.raise_for_status = MagicMock()

        mock_post = MagicMock()
        mock_post.side_effect = lambda url, **kw: \
            auth_resp if "items/ATTKEY/file" in url else create_resp

        with patch.object(session, "post", mock_post):
            key = attach_pdf(client, "PARENT", pdf)

        assert key == "ATTKEY"
        # Verify auth was called WITHOUT params
        auth_call = [
            c for c in mock_post.call_args_list
            if "items/ATTKEY/file" in str(c)
        ][0]
        _, kwargs = auth_call
        sent_data = kwargs.get("data", {})
        assert "params" not in sent_data, "auth_data must not contain 'params' field"
