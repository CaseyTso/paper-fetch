"""Live Zotero integration — requires API credentials.

Run with:
  PAPER_FETCH_ZOTERO_API_KEY=xxx \\
  PAPER_FETCH_ZOTERO_LIBRARY_ID=12345 \\
  PAPER_FETCH_LIVE_ZOTERO=1 \\
  pytest -m live tests/live/test_live_zotero.py -v -s
"""

import os
import tempfile
import time
from pathlib import Path

import pytest
import requests
from pypdf import PdfWriter

from paper_fetch.config import Config
from paper_fetch.zotero import ZoteroClient, ZoteroConfigError, attach_pdf, upsert_item


@pytest.mark.live
def test_upsert_and_upload():
    """Create a disposable item, upload a PDF, read it back."""
    api_key = os.environ.get("PAPER_FETCH_ZOTERO_API_KEY")
    lib_id = os.environ.get("PAPER_FETCH_ZOTERO_LIBRARY_ID")
    live_flag = os.environ.get("PAPER_FETCH_LIVE_ZOTERO")

    if not all([api_key, lib_id, live_flag]):
        pytest.skip("Zotero live credentials not set")

    config = Config(
        zotero_api_key=api_key,
        zotero_library_id=lib_id,
        zotero_library_type="user",
    )
    client = ZoteroClient(requests.Session(), config)

    # Generate a unique synthetic DOI
    ts = int(time.time())
    synthetic_doi = f"10.0000/paper-fetch-test-{ts}"
    synthetic_title = f"paper-fetch Test Paper {ts}"

    from paper_fetch.models import PaperIdentity

    identity = PaperIdentity(
        original_input=synthetic_doi,
        doi=synthetic_doi,
        title=synthetic_title,
        authors=["Test, Automated"],
        year="2026",
    )

    # Create item
    key = upsert_item(client, identity, force=True)
    assert key, "Failed to create Zotero item"
    print(f"\n   Created item: {key}")

    # Generate a 2-page PDF
    with tempfile.TemporaryDirectory() as td:
        pdf_path = Path(td) / "test.pdf"
        writer = PdfWriter()
        for _ in range(2):
            writer.add_blank_page(width=612, height=792)
        with pdf_path.open("wb") as fh:
            writer.write(fh)

        # Upload attachment
        att_key = attach_pdf(client, key, pdf_path)
        assert att_key, "Failed to upload PDF attachment"
        print(f"   Attachment: {att_key}")

        # Readback
        children = client.get_children(key)
        pdf_att = client.find_pdf_attachment(key)
        assert pdf_att is not None, "PDF attachment not found in readback"
        assert pdf_att.get("filename") == "test.pdf"
        print(f"   Readback OK: filename={pdf_att.get('filename')}, key={pdf_att.get('key')}")

    print(f"\n   ✅ Zotero live test passed.")
    print(f"   Parent key: {key}")
    print(f"   Verify in Zotero Desktop, then delete manually.")
