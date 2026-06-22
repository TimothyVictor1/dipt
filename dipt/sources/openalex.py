"""OpenAlex paper source.

Fetches open-access papers from a curated, manually verified list of top-tier
Software Engineering journals, filtered by ISSN. ISSN filtering guarantees that
every result comes from a known SE venue, which is far more reliable than
keyword or concept-based filtering.
"""

from __future__ import annotations

import logging
import time
from typing import Final, Iterator

import requests

from dipt.exceptions import SourceParseError, SourceRequestError
from dipt.models.schemas import PaperRecord, PaperSource
from dipt.sources.base import PaperSourceFetcher

logger = logging.getLogger(__name__)

_API_URL: Final[str] = "https://api.openalex.org/works"
_PER_PAGE: Final[int] = 50
_REQUEST_DELAY_SECONDS: Final[float] = 1.0

#: (journal name, ISSN) — every entry manually verified as 100% SE.
_SE_JOURNALS: Final[tuple[tuple[str, str], ...]] = (
    ("IEEE Transactions on Software Engineering", "0098-5589"),
    ("Information and Software Technology", "0950-5849"),
    ("Journal of Systems and Software", "0164-1212"),
    ("Empirical Software Engineering", "1382-3256"),
    ("Requirements Engineering", "0947-3602"),
    ("Software and Systems Modeling", "1619-1366"),
    ("Automated Software Engineering", "0928-8910"),
    ("IEEE Software", "0740-7459"),
    ("Software Quality Journal", "0963-9314"),
    ("ACM Transactions on Software Engineering (TOSEM)", "1049-331X"),
    ("Journal of Software: Evolution and Process", "2047-7473"),
    ("Software Practice and Experience", "0038-0644"),
    ("Software Testing Verification and Reliability", "0960-0833"),
    ("Intl Journal on Software Tools for Tech Transfer", "1433-2779"),
)

_SKIP_TITLES: Final[frozenset[str]] = frozenset(
    {
        "issue information",
        "erratum",
        "editorial",
        "corrigendum",
        "retraction",
        "table of contents",
        "front matter",
        "back matter",
        "author index",
        "contents",
        "preface",
    }
)


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Rebuild an abstract string from OpenAlex's inverted-index format.

    Args:
        inverted_index: Mapping of word to the positions at which it occurs.

    Returns:
        The reconstructed abstract, or an empty string if unavailable.
    """
    if not inverted_index:
        return ""
    positioned: list[tuple[int, str]] = [
        (pos, word)
        for word, positions in inverted_index.items()
        for pos in positions
    ]
    positioned.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in positioned)


def _should_skip(title: str) -> bool:
    """Return ``True`` for non-paper metadata entries."""
    stripped = title.strip()
    return (
        not stripped
        or stripped.lower() in _SKIP_TITLES
        or len(stripped) < 10
    )


class OpenAlexSource(PaperSourceFetcher):
    """Fetches recent open-access SE papers from OpenAlex by journal ISSN."""

    source_name = "openalex"

    def fetch(self) -> Iterator[PaperRecord]:
        """Yield recent open-access papers from each verified SE journal.

        Yields:
            Normalised :class:`PaperRecord` objects.
        """
        for journal_name, issn in _SE_JOURNALS:
            logger.info("OpenAlex: querying journal '%s'", journal_name)
            yield from self._fetch_journal(journal_name, issn)
            time.sleep(_REQUEST_DELAY_SECONDS)

    def _fetch_journal(self, journal_name: str, issn: str) -> Iterator[PaperRecord]:
        """Fetch and yield papers for a single journal.

        Args:
            journal_name: Display name (for logging).
            issn: Journal ISSN used for filtering.

        Yields:
            Normalised paper records. A failure on one journal is logged and
            results in that journal being skipped, not the whole run aborting.
        """
        params = {
            "filter": (
                f"primary_location.source.issn:{issn},"
                f"is_oa:true,"
                f"publication_date:>{self.from_date}"
            ),
            "sort": "publication_date:desc",
            "per_page": _PER_PAGE,
            "mailto": self._contact_email,
            "select": (
                "id,title,abstract_inverted_index,authorships,"
                "doi,publication_date,primary_location"
            ),
        }

        try:
            response = requests.get(_API_URL, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            logger.warning("OpenAlex request failed for '%s': %s", journal_name, exc)
            return

        if response.status_code != 200:
            logger.warning(
                "OpenAlex HTTP %d for '%s'", response.status_code, journal_name
            )
            return

        try:
            results = response.json().get("results", [])
        except ValueError as exc:
            raise SourceParseError(self.source_name, str(exc)) from exc

        logger.info("OpenAlex: '%s' returned %d papers", journal_name, len(results))

        for raw in results:
            record = self._parse_record(raw)
            if record is not None:
                yield record

    def _parse_record(self, raw: dict) -> PaperRecord | None:
        """Convert a raw OpenAlex work into a :class:`PaperRecord`.

        Args:
            raw: A single OpenAlex work object.

        Returns:
            A normalised record, or ``None`` if the work should be skipped.
        """
        title = raw.get("title") or ""
        if _should_skip(title):
            return None

        abstract = _reconstruct_abstract(raw.get("abstract_inverted_index"))
        if not abstract:
            return None

        authors = [
            authorship.get("author", {}).get("display_name", "")
            for authorship in raw.get("authorships", [])
            if authorship.get("author", {}).get("display_name")
        ]

        doi = raw.get("doi") or ""
        doi = doi.replace("https://doi.org/", "").strip() or None

        primary_location = raw.get("primary_location") or {}
        pdf_url = primary_location.get("pdf_url") or ""

        try:
            return PaperRecord(
                title=title,
                abstract=abstract,
                authors=authors,
                doi=doi,
                source=PaperSource.OPENALEX,
                source_id=raw.get("id", ""),
                pdf_url=pdf_url,
                published_date=raw.get("publication_date"),
            )
        except ValueError as exc:
            logger.debug("Skipping malformed OpenAlex record: %s", exc)
            return None
