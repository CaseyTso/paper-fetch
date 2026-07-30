# PubMed Linkout Extraction

When Europe PMC has metadata for a paper (including PMID) but no direct full-text URL, the PubMed page often has publisher PDF links in its "Full text links" section.

## Pattern

1. Query Europe PMC by DOI → get PMID
2. Fetch `https://pubmed.ncbi.nlm.nih.gov/<PMID>/`
3. Extract links from the `<div class="full-text-links">` section
4. Try each link as a PDF candidate

## Regex patterns

```python
# Pattern 1: links inside full-text-links div
for m in re.finditer(r'full-text-links[^>]*>.*?</div', html, re.DOTALL | re.I):
    for link in re.findall(r'href="(https?://[^"]+)"', m.group(0)):
        pdf_urls.append(link)

# Pattern 2: broader full-text section
ft = re.search(r'<div class="full-text-links[^"]*\">(.*?)</div>\s*</div>', html, re.DOTALL | re.I)
if ft:
    for link in re.findall(r'href="(https?://[^"]+)"', ft.group(0)):
        pdf_urls.append(link)
```

## Real-world example

Paper: "Revisiting the anatomy of the retroperitoneum and renal fascia"
- DOI: `10.1590/s1677-5538.ibju.2026.9913` — returned 404 on CrossRef (ahead-of-print)
- Europe PMC had metadata + PMID `42462154` but no full text
- PubMed page had "Free article" link → `https://www.intbrazjurol.com.br/pdf/vol53n02/e20269913.pdf`
- PDF downloaded successfully (3.9 MB)

## When this matters

- **Ahead-of-print papers**: DOI not yet activated on CrossRef, but PDF already published on journal site
- **Publisher-hosted OA papers**: PDF available on publisher site but not indexed by Unpaywall/PMC
- **Non-PMC papers in PubMed**: PubMed indexes them, but the full text is only linked, not hosted
