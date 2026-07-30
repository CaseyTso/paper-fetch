"""Live institution/Sci-Hub/ableSci tests — require external services.

Each test is guarded by its own environment flag.
"""

import os

import pytest


@pytest.mark.live
def test_institution_requires_socks5():
    """Verify institution source reports proxy_unavailable without VPN."""
    if not os.environ.get("PAPER_FETCH_LIVE_INSTITUTION"):
        pytest.skip("Set PAPER_FETCH_LIVE_INSTITUTION=1 to run")

    import requests
    from paper_fetch.config import Config
    from paper_fetch.models import PaperIdentity
    from paper_fetch.sources.institution import InstitutionSource

    config = Config(institution_socks5=os.environ.get("PAPER_FETCH_INSTITUTION_SOCKS5", ""))
    source = InstitutionSource(requests.Session(), config)

    # Use a known open-access paper for verification
    doi = os.environ.get("PAPER_FETCH_LIVE_INSTITUTION_DOI", "10.1371/journal.pmed.0020124")

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "out.pdf"
        result = source.fetch(
            PaperIdentity(original_input=doi, doi=doi), dest
        )
        assert result.success, (
            f"Institution download failed: {result.status.value} {result.detail}"
        )
        print(f"\n   ✅ Institution PDF: {dest} ({dest.stat().st_size} bytes)")


@pytest.mark.live
def test_scihub_requires_clash():
    if not os.environ.get("PAPER_FETCH_LIVE_SCIHUB"):
        pytest.skip("Set PAPER_FETCH_LIVE_SCIHUB=1 to run")

    import requests
    from paper_fetch.config import Config
    from paper_fetch.models import PaperIdentity
    from paper_fetch.sources.scihub import SciHubSource

    config = Config(clash_proxy="http://proxy.example:7890")
    source = SciHubSource(requests.Session(), config)

    doi = os.environ.get("PAPER_FETCH_LIVE_SCIHUB_DOI", "10.1371/journal.pmed.0020124")

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "out.pdf"
        result = source.fetch(
            PaperIdentity(original_input=doi, doi=doi), dest
        )
        assert result.success, (
            f"Sci-Hub download failed: {result.status.value} {result.detail}"
        )
        print(f"\n   ✅ Sci-Hub PDF: {dest} ({dest.stat().st_size} bytes)")


@pytest.mark.live
def test_ablesci_requires_opencli():
    if not os.environ.get("PAPER_FETCH_LIVE_ABLESCI"):
        pytest.skip("Set PAPER_FETCH_LIVE_ABLESCI=1 to run")

    import requests
    from paper_fetch.config import Config
    from paper_fetch.models import PaperIdentity
    from paper_fetch.sources.ablesci import AbleSciSource

    url = os.environ.get("PAPER_FETCH_LIVE_ABLESCI_URL", "")
    config = Config(ablesci_url=url)
    source = AbleSciSource(requests.Session(), config)

    doi = os.environ.get("PAPER_FETCH_LIVE_ABLESCI_DOI", "10.1371/journal.pmed.0020124")

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "out.pdf"
        result = source.fetch(
            PaperIdentity(original_input=doi, doi=doi), dest
        )
        assert result.success, (
            f"ableSci download failed: {result.status.value} {result.detail}"
        )
        print(f"\n   ✅ ableSci PDF: {dest} ({dest.stat().st_size} bytes)")
