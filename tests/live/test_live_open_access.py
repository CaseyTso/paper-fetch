"""Live open-access integration tests — requires network.

Run with: pytest -m live tests/live/test_live_open_access.py -v -s
"""

import os
import tempfile
from pathlib import Path

import pytest
import requests

from paper_fetch.config import Config
from paper_fetch.models import PaperIdentity, Status
from paper_fetch.pdf import validate_pdf
from paper_fetch.resolver import Resolver
from paper_fetch.sources.open_access import OpenAccessSource


# Stable fixture: Ioannidis 2005, PLoS Med
DOI = "10.1371/journal.pmed.0020124"
PMID = "16060722"
PMCID = "PMC1182327"
TITLE = "Why most published research findings are false"

# Allow overriding email for Unpaywall
UNPAYWALL_EMAIL = os.environ.get("PAPER_FETCH_TEST_EMAIL", "test@example.com")


@pytest.mark.live
def test_pmcid_resolves_to_correct_doi():
    r = Resolver(requests.Session(), Config())
    identity = r.resolve(PMCID)
    assert identity.pmcid == PMCID
    assert identity.doi == DOI
    assert identity.pmid == PMID
    assert identity.title and "false" in identity.title.lower()


@pytest.mark.live
def test_pmid_resolves_to_correct_doi():
    r = Resolver(requests.Session(), Config())
    identity = r.resolve(PMID)
    assert identity.pmid == PMID
    assert identity.doi == DOI


@pytest.mark.live
def test_title_resolves():
    r = Resolver(requests.Session(), Config())
    identity = r.resolve(TITLE)
    assert identity.doi == DOI


@pytest.mark.live
def test_open_access_downloads_valid_pdf():
    """Verify that the open-access source produces a valid PDF for this known paper."""
    config = Config(unpaywall_email=UNPAYWALL_EMAIL)
    source = OpenAccessSource(requests.Session(), config)
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "out.pdf"
        result = source.fetch(
            PaperIdentity(original_input=DOI, doi=DOI, pmcid=PMCID, pmid=PMID), dest
        )
        # Accept whatever source succeeded (PMC, EuropePMC, or Unpaywall)
        assert result.success, (
            f"Open access download failed: source={result.source} "
            f"status={result.status.value} detail={result.detail}"
        )
        validation = validate_pdf(dest)
        assert validation.success, f"Downloaded file is not a valid PDF: {validation.detail}"
        assert validation.status == Status.SUCCESS
        print(f"\n   ✅ PDF from {result.source}: {dest} ({dest.stat().st_size} bytes)")
