"""Tests for resolver — identifier classification, DOI normalisation, title matching."""

import json
from unittest.mock import patch, MagicMock

import pytest
import requests

from paper_fetch.config import Config
from paper_fetch.models import PaperIdentity, Status
from paper_fetch.resolver import (
    ResolutionError,
    Resolver,
    _choose_candidate,
    classify_input,
    extract_doi,
    normalize_doi,
    normalize_title,
    title_similarity,
)

# ---------------------------------------------------------------------------
# Helpers for mocking requests
# ---------------------------------------------------------------------------


def _fake_json_response(json_data, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    return mock


def _mock_europepmc_response(json_data):
    """Return a function suitable for side_effect that handles Europe PMC URLs."""
    def side_effect(url, timeout=None, **kwargs):
        if "europepmc" in url:
            return _fake_json_response(json_data)
        # Fallback for Crossref URLs
        return _fake_json_response({"message": {"items": []}})
    return side_effect


# ---------------------------------------------------------------------------
# classify_input / normalize_doi
# ---------------------------------------------------------------------------

class TestClassifyInput:
    def test_bare_doi(self):
        t, k = classify_input("10.1000/xyz123")
        assert t == "doi"
        assert k == "10.1000/xyz123"

    def test_doi_url(self):
        t, k = classify_input("https://doi.org/10.1000/xyz123")
        assert t == "doi"
        assert k == "10.1000/xyz123"

    def test_trailing_period(self):
        t, k = classify_input("10.1000/xyz123.")
        assert t == "doi"
        assert k == "10.1000/xyz123"

    def test_pmcid(self):
        t, k = classify_input("PMC1234567")
        assert t == "pmcid"
        assert k == "PMC1234567"

    def test_pmid(self):
        t, k = classify_input("12345678")
        assert t == "pmid"
        assert k == "12345678"

    def test_zotero_key_bare(self):
        t, k = classify_input("ABCD1234")
        assert t == "zotero"
        assert k == "ABCD1234"

    def test_zotero_key_prefixed(self):
        t, k = classify_input("zotero:ABCD1234")
        assert t == "zotero"
        assert k == "ABCD1234"

    def test_text_title(self):
        t, k = classify_input("Why most published research findings are false")
        assert t == "text"
        assert "Why most" in k

    def test_citation_with_doi(self):
        t, k = classify_input("Some Paper (2020) doi:10.1000/abc.123")
        assert t == "doi"
        assert k == "10.1000/abc.123"


class TestNormalizeDoi:
    def test_strips_prefix(self):
        assert normalize_doi("https://doi.org/10.1000/xyz123") == "10.1000/xyz123"

    def test_unicode_dash(self):
        assert normalize_doi("10.1000/abc\u2013123") is not None


# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------

class TestTitleSimilarity:
    def test_exact_match(self):
        assert title_similarity("Foo bar", "Foo bar") == 1.0

    def test_case_insensitive(self):
        assert title_similarity("FOO BAR", "foo bar") == 1.0

    def test_close_match(self):
        score = title_similarity(
            "Why most published research findings are false",
            "Why Most Published Research Findings Are False",
        )
        assert score > 0.95

    def test_low_match(self):
        score = title_similarity("apple banana", "quantum computing")
        assert score < 0.5


class TestChooseCandidate:
    def test_clear_winner(self):
        candidates = [
            {"title": "Why most published research findings are false", "doi": "10.1/a"},
            {"title": "Quantum computing advances", "doi": "10.1/b"},
        ]
        result = _choose_candidate("Why most published research findings are false", candidates)
        assert isinstance(result, dict)
        assert result["doi"] == "10.1/a"

    def test_ambiguous(self):
        candidates = [
            {"title": "Foo bar baz qux quux 1", "doi": "10.1/a"},
            {"title": "Foo bar baz qux quux 2", "doi": "10.1/b"},
        ]
        result = _choose_candidate("Foo bar baz qux quux", candidates)
        assert isinstance(result, str)
        assert "ambiguous" in result.lower()

    def test_below_threshold(self):
        candidates = [{"title": "unrelated paper", "doi": "10.1/x"}]
        result = _choose_candidate("completely different topic", candidates)
        assert isinstance(result, str)
        assert "0.85" in result


# ---------------------------------------------------------------------------
# Resolver with mocked network
# ---------------------------------------------------------------------------

@pytest.fixture
def resolver():
    """Return a Resolver backed by a real session (mocked per-test)."""
    return Resolver(requests.Session(), Config())


class TestResolverDoiPmcidPmid:
    def test_doi_direct(self, resolver):
        identity = resolver.resolve("10.1000/xyz123")
        assert identity.doi == "10.1000/xyz123"

    def test_pmcid_resolve(self):
        epmc_data = {
            "resultList": {
                "result": [{
                    "doi": "10.1371/journal.pmed.0020124",
                    "pmid": "16060722",
                    "pmcid": "PMC1182327",
                    "title": "Why most published research findings are false.",
                    "authorString": "Ioannidis JPA",
                    "journalTitle": "PLoS Med",
                    "pubYear": 2005,
                }]
            }
        }
        session = requests.Session()
        with patch.object(session, "get", side_effect=_mock_europepmc_response(epmc_data)):
            r = Resolver(session, Config())
            identity = r.resolve("PMC1182327")
        assert identity.pmcid == "PMC1182327"
        assert identity.doi == "10.1371/journal.pmed.0020124"
        assert identity.pmid == "16060722"

    def test_pmid_resolve(self):
        epmc_data = {
            "resultList": {
                "result": [{
                    "doi": "10.1371/journal.pmed.0020124",
                    "pmid": "16060722",
                    "pmcid": "PMC1182327",
                    "title": "Test",
                    "authorString": "Doe J",
                    "journalTitle": "J Test",
                    "pubYear": 2005,
                }]
            }
        }
        session = requests.Session()
        with patch.object(session, "get", side_effect=_mock_europepmc_response(epmc_data)):
            r = Resolver(session, Config())
            identity = r.resolve("16060722")
        assert identity.pmid == "16060722"
        assert identity.doi == "10.1371/journal.pmed.0020124"


class TestResolverTitle:
    def test_exact_title(self):
        """Exact title resolves with high confidence."""
        title = "Why most published research findings are false"
        crossref_data = {
            "message": {
                "items": [{
                    "DOI": "10.1371/journal.pmed.0020124",
                    "title": [title],
                    "author": [],
                    "published-print": {"date-parts": [[2005]]},
                }]
            }
        }

        def side_effect(url, timeout=None, **kwargs):
            if "crossref" in url:
                return _fake_json_response(crossref_data)
            return _fake_json_response({"resultList": {"result": []}})

        session = requests.Session()
        with patch.object(session, "get", side_effect=side_effect):
            r = Resolver(session, Config())
            identity = r.resolve(title)
        assert identity.doi == "10.1371/journal.pmed.0020124"

    def test_ambiguous_title_raises(self):
        title = "similar paper"
        crossref_data = {
            "message": {
                "items": [
                    {"DOI": "10.1/a", "title": ["similar paper A"], "author": []},
                    {"DOI": "10.1/b", "title": ["similar paper B"], "author": []},
                ]
            }
        }

        def side_effect(url, timeout=None, **kwargs):
            if "crossref" in url:
                return _fake_json_response(crossref_data)
            return _fake_json_response({"resultList": {"result": []}})

        session = requests.Session()
        with patch.object(session, "get", side_effect=side_effect):
            r = Resolver(session, Config())
            with pytest.raises(ResolutionError) as exc:
                r.resolve(title)
        assert exc.value.status == Status.AMBIGUOUS_IDENTIFIER


class TestExtractDoi:
    def test_finds_in_citation(self):
        assert extract_doi("Smith et al. 2020, Nature, doi:10.1038/s41586-020-1234-5") == "10.1038/s41586-020-1234-5"

    def test_none_when_absent(self):
        assert extract_doi("no doi here") is None
