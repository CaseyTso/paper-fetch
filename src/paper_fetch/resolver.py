"""Paper identifier resolution: DOI, PMID, PMCID, titles, citations, Zotero keys."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
from urllib.parse import quote

import requests

from .config import Config
from .models import PaperIdentity, Status

if TYPE_CHECKING:
    from .zotero import ZoteroClient  # noqa: F401


# ---------------------------------------------------------------------------
# Pure helpers — no network
# ---------------------------------------------------------------------------

DOI_PATTERN = re.compile(
    r"\b(10\.\d{4,}(?:\.\d+)*/[-._;()/:a-zA-Z0-9]+)\b", re.IGNORECASE
)
PMID_PATTERN = re.compile(r"^\d{7,8}$")
PMCID_PATTERN = re.compile(r"^PMC\d{5,}$", re.IGNORECASE)
ZOTERO_KEY_PATTERN = re.compile(r"^(?:zotero:)?([A-Z0-9]{8})$", re.IGNORECASE)

INPUT_TYPE_DOI = "doi"
INPUT_TYPE_PMID = "pmid"
INPUT_TYPE_PMCID = "pmcid"
INPUT_TYPE_ZOTERO = "zotero"
INPUT_TYPE_TEXT = "text"


def normalize_doi(value: str) -> str | None:
    """Extract and normalise a DOI from *value*."""
    # Strip common URL prefixes
    cleaned = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.strip(), flags=re.I)
    m = DOI_PATTERN.search(cleaned)
    if m:
        return m.group(1).rstrip(".,;:")
    return None


def classify_input(value: str) -> tuple[str, str]:
    """Return (type, key) — key is the canonical identifier."""
    v = value.strip()

    # PMID before Zotero key (both are alphanumeric)
    if PMID_PATTERN.match(v):
        return INPUT_TYPE_PMID, v

    if ZOTERO_KEY_PATTERN.match(v):
        m = ZOTERO_KEY_PATTERN.match(v)
        return INPUT_TYPE_ZOTERO, m.group(1).upper() if m else v.upper()

    doi = normalize_doi(v)
    if doi:
        if PMCID_PATTERN.match(doi):
            return INPUT_TYPE_PMCID, doi.upper()
        return INPUT_TYPE_DOI, doi

    # Standalone PMCID or PMID
    if PMCID_PATTERN.match(v):
        return INPUT_TYPE_PMCID, v.upper()
    if PMID_PATTERN.match(v):
        return INPUT_TYPE_PMID, v

    # Could be a title or citation — text
    doi = DOI_PATTERN.search(v)
    if doi:
        return INPUT_TYPE_DOI, doi.group(1).rstrip(".,;:")

    return INPUT_TYPE_TEXT, v


def extract_doi(value: str) -> str | None:
    """Return the first DOI found or None."""
    return normalize_doi(value)


def normalize_title(title: str) -> str:
    """Lowercase and collapse whitespace for matching."""
    return re.sub(r"\s+", " ", title.strip().lower())


def title_similarity(left: str, right: str) -> float:
    """Return 0–1 similarity between two titles."""
    return SequenceMatcher(None, normalize_title(left), normalize_title(right)).ratio()


# ---------------------------------------------------------------------------
# Network-dependent resolver
# ---------------------------------------------------------------------------


class Resolver:
    """Resolve any of six input forms to a ``PaperIdentity``.

    Instantiate once per pipeline run.
    """

    def __init__(self, session: requests.Session, config: Config) -> None:
        self._session = session
        self._config = config
        self._timeout = config.request_timeout

    # -- public API ----------------------------------------------------------

    def resolve(self, raw: str) -> PaperIdentity:
        """Resolve *raw* input to a ``PaperIdentity``, or raise ``ResolutionError``."""
        itype, key = classify_input(raw)

        if itype == INPUT_TYPE_DOI:
            return self._resolve_doi(key, raw)

        if itype == INPUT_TYPE_PMCID:
            return self._resolve_pmcid(key, raw)

        if itype == INPUT_TYPE_PMID:
            return self._resolve_pmid(key, raw)

        if itype == INPUT_TYPE_TEXT:
            return self._resolve_text(key, raw)

        raise ResolutionError(raw, Status.CONFIGURATION_ERROR, "unsupported input type")

    def resolve_with_zotero(
        self, raw: str, zotero: ZoteroClient | None
    ) -> PaperIdentity:
        """Like ``resolve()`` but also handles Zotero item keys."""
        itype, key = classify_input(raw)
        if itype == INPUT_TYPE_ZOTERO:
            if zotero is None:
                raise ResolutionError(
                    raw, Status.CONFIGURATION_ERROR, "Zotero not configured"
                )
            return self._resolve_zotero(key, zotero)
        return self.resolve(raw)

    # -- internal ------------------------------------------------------------

    def _resolve_pmcid(self, pmcid: str, original: str) -> PaperIdentity:
        meta = self._europepmc_lookup(id_type="PMCID", id_value=pmcid)
        identity = PaperIdentity(original_input=original, pmcid=pmcid)
        if meta:
            identity.doi = meta.get("doi")
            identity.pmid = meta.get("pmid")
            identity.title = meta.get("title")
            identity.authors = meta.get("authors", [])
            identity.journal = meta.get("journal")
            identity.year = meta.get("year")
        else:
            identity.doi = self._pmcid_to_doi_ncbi(pmcid)
        return identity

    def _resolve_pmid(self, pmid: str, original: str) -> PaperIdentity:
        meta = self._europepmc_lookup(id_type="EXT_ID", id_value=pmid)
        identity = PaperIdentity(original_input=original, pmid=pmid)
        if meta:
            identity.doi = meta.get("doi")
            identity.pmcid = meta.get("pmcid")
            identity.title = meta.get("title")
            identity.authors = meta.get("authors", [])
            identity.journal = meta.get("journal")
            identity.year = meta.get("year")
        return identity

    def _resolve_doi(self, doi: str, original: str) -> PaperIdentity:
        meta = self._europepmc_lookup(id_type="DOI", id_value=doi)
        identity = PaperIdentity(original_input=original, doi=doi)
        if meta:
            identity.pmid = meta.get("pmid")
            identity.pmcid = meta.get("pmcid")
            identity.title = meta.get("title")
            identity.authors = meta.get("authors", [])
            identity.journal = meta.get("journal")
            identity.year = meta.get("year")
        return identity

    def _resolve_text(self, text: str, original: str) -> PaperIdentity:
        # See if there's an embedded DOI
        doi = extract_doi(text)
        if doi:
            return PaperIdentity(original_input=original, doi=doi)

        return self._resolve_title(text, original)

    def _resolve_title(self, title: str, original: str) -> PaperIdentity:
        from_crossref = self._crossref_candidates(title)
        from_epmc = self._europepmc_candidates(title)

        candidates = _merge_candidates(from_crossref, from_epmc)
        if not candidates:
            # Fallback: try shorter title (first sentence or first N words)
            short = _shorten_title(title)
            if short != title:
                from_crossref = self._crossref_candidates(short)
                from_epmc = self._europepmc_candidates(short)
                candidates = _merge_candidates(from_crossref, from_epmc)

        if not candidates:
            raise ResolutionError(original, Status.NOT_FOUND, "no candidates for title")

        chosen = _choose_candidate(title, candidates)
        if isinstance(chosen, str):
            raise ResolutionError(original, Status.AMBIGUOUS_IDENTIFIER, chosen)

        identity = PaperIdentity(original_input=original)
        identity.doi = chosen.get("doi")
        identity.title = chosen.get("title") or title
        identity.authors = chosen.get("authors", [])
        identity.journal = chosen.get("journal")
        identity.year = chosen.get("year")
        identity.pmid = chosen.get("pmid")
        return identity

    def _resolve_zotero(
        self, key: str, zotero: ZoteroClient
    ) -> PaperIdentity:
        item = zotero.get_item(key)
        if item is None:
            raise ResolutionError(key, Status.NOT_FOUND, "Zotero item not found")
        return zotero.identity_from_item(item, original_input=f"zotero:{key}")

    # -- Europe PMC helpers --------------------------------------------------

    def _europepmc_lookup(
        self, *, id_type: str, id_value: str
    ) -> dict | None:
        """Return a flat metadata dict or None."""
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query={id_type}:{quote(id_value)}&format=json&pageSize=1"
        )
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        results = data.get("resultList", {}).get("result", [])
        if not results:
            return None
        r = results[0]
        return {
            "doi": r.get("doi"),
            "pmid": r.get("pmid"),
            "pmcid": r.get("pmcid"),
            "title": r.get("title"),
            "authors": _parse_author_string(r.get("authorString", "")),
            "journal": r.get("journalTitle"),
            "year": str(r.get("pubYear")) if r.get("pubYear") else None,
        }

    def _pmcid_to_doi_ncbi(self, pmcid: str) -> str | None:
        """Query NCBI ID Converter for a PMCID → DOI mapping."""
        url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={pmcid}&format=json"
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        for rec in data.get("records", []):
            doi = rec.get("doi")
            if doi:
                return doi
        return None

    # -- Crossref / title search ---------------------------------------------

    def _crossref_candidates(self, title: str) -> list[dict]:
        url = "https://api.crossref.org/works"
        try:
            resp = self._session.get(
                url,
                params={"query.bibliographic": title, "rows": 5},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        items = data.get("message", {}).get("items", [])
        return [_flatten_crossref(i) for i in items if i.get("title")]

    def _europepmc_candidates(self, title: str) -> list[dict]:
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query={quote(title)}&format=json&pageSize=5"
        )
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        results = data.get("resultList", {}).get("result", [])
        return [
            {
                "doi": r.get("doi"),
                "pmid": r.get("pmid"),
                "title": r.get("title"),
                "authors": _parse_author_string(r.get("authorString", "")),
                "journal": r.get("journalTitle"),
                "year": str(r.get("pubYear")) if r.get("pubYear") else None,
            }
            for r in results
            if r.get("title")
        ]


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def _merge_candidates(cr: list[dict], epmc: list[dict]) -> list[dict]:
    seen_dois: set[str] = set()
    merged: list[dict] = []
    for c in cr + epmc:
        doi = c.get("doi") or ""
        if doi and doi in seen_dois:
            continue
        seen_dois.add(doi)
        merged.append(c)
    return merged


def _choose_candidate(
    query: str, candidates: list[dict]
) -> dict | str:
    """Return the best candidate, or an error message string."""
    if not candidates:
        return "no candidates"

    scored = []
    for c in candidates:
        score = title_similarity(query, c.get("title") or "")
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    if best_score < 0.85:
        return f"best title similarity {best_score:.2f} < 0.85"

    if len(scored) > 1:
        second_score, second = scored[1]
        # Same DOI? Not ambiguous — same paper from different sources
        if best.get("doi") and second.get("doi") and best["doi"].lower() == second["doi"].lower():
            return best
        # One has DOI, other doesn't → prefer the one with DOI
        if best.get("doi") and not second.get("doi"):
            return best
        if not best.get("doi") and second.get("doi"):
            return second
        # When scores are tied and DOIs differ, prefer the candidate whose
        # title length is closer to the query (avoids "Correction: Title" picks)
        if best_score >= 0.99:
            query_len = len(normalize_title(query))
            diff_best = abs(len(normalize_title(best.get("title", ""))) - query_len)
            diff_second = abs(len(normalize_title(second.get("title", ""))) - query_len)
            if diff_best <= diff_second:
                return best
            return second
        if best_score - second_score < 0.05:
            return f"ambiguous: scores {best_score:.2f} vs {second_score:.2f} too close"

    return best


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_author_string(raw: str) -> list[str]:
    """Split 'Doe J, Smith AB' into ['Doe J', 'Smith AB']."""
    return [a.strip() for a in raw.split(",") if a.strip()]


def _shorten_title(title: str) -> str:
    """Return a shortened version of *title* for broader search.

    Truncates at the first sentence boundary (period, question mark, colon)
    or limits to the first 10 words, whichever is shorter.
    """
    import re

    # Cut at first sentence-ending punctuation
    m = re.search(r"[.?!]\s+[A-Z]", title)
    if m and m.start() > 30:
        return title[: m.start() + 1].strip()

    # Cut at colon or dash (subtitle separators)
    m = re.search(r"[:–—\-]\s", title)
    if m and m.start() > 30:
        return title[: m.start()].strip()

    # Fallback: first 10 words
    words = title.split()
    if len(words) > 10:
        return " ".join(words[:10])

    return title


def _flatten_crossref(item: dict) -> dict:
    msg = item.get("message", item)
    title_list = msg.get("title", [])
    title = title_list[0] if title_list else None
    authors = []
    for a in msg.get("author", []):
        fam = a.get("family", "")
        giv = a.get("given", "")
        authors.append(f"{fam} {giv}".strip() or fam)
    published = msg.get("published-print") or msg.get("published-online") or {}
    date_parts = published.get("date-parts", [[None]])
    year = str(date_parts[0][0]) if date_parts[0][0] else None
    container = msg.get("container-title", [])
    return {
        "doi": msg.get("DOI"),
        "title": title,
        "authors": authors,
        "journal": container[0] if container else None,
        "year": year,
    }


class ResolutionError(Exception):
    """Non-recoverable resolution failure that the pipeline surfaces as a status."""

    def __init__(self, identifier: str, status: Status, detail: str = "") -> None:
        self.identifier = identifier
        self.status = status
        self.detail = detail
        super().__init__(detail or status.value)
