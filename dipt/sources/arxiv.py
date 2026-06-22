"""arXiv paper source via the OAI-PMH harvesting protocol.

The OAI-PMH endpoint is purpose-built for bulk metadata harvesting and has far
more relaxed rate limits than the arXiv search API. The ``cs:cs:SE`` set
restricts results to Software Engineering, covering all major SE conference
papers (ICSE, FSE, ASE, ISSTA, MSR, etc.) that authors self-archive.
"""

from __future__ import annotations

import logging
import time
from typing import Final, Iterator
from xml.etree import ElementTree as ET

import requests

from dipt.exceptions import SourceParseError
from dipt.models.schemas import PaperRecord, PaperSource
from dipt.sources.base import PaperSourceFetcher

logger = logging.getLogger(__name__)

_OAI_URL: Final[str] = "https://oaipmh.arxiv.org/oai"
_SET: Final[str] = "cs:cs:SE"
_METADATA_PREFIX: Final[str] = "arXiv"
_PAGE_DELAY_SECONDS: Final[float] = 5.0
_MAX_PAGES: Final[int] = 50  # Safety bound to prevent runaway pagination.

_OAI_NS: Final[str] = "{http://www.openarchives.org/OAI/2.0/}"
_ARXIV_NS: Final[str] = "{http://arxiv.org/OAI/arXiv/}"


class ArxivSource(PaperSourceFetcher):
    """Harvests recent arXiv cs.SE papers via OAI-PMH with pagination."""

    source_name = "arxiv"

    def fetch(self) -> Iterator[PaperRecord]:
        """Yield recent cs.SE papers, following resumption tokens.

        Yields:
            Normalised :class:`PaperRecord` objects.
        """
        resumption_token: str | None = None
        page = 0

        while page < _MAX_PAGES:
            page += 1
            params = self._build_params(resumption_token)

            logger.info("arXiv OAI-PMH: fetching page %d", page)
            time.sleep(_PAGE_DELAY_SECONDS)

            try:
                response = requests.get(_OAI_URL, params=params, timeout=self._timeout)
            except requests.RequestException as exc:
                logger.warning("arXiv request failed on page %d: %s", page, exc)
                return

            if response.status_code != 200:
                logger.warning("arXiv HTTP %d on page %d", response.status_code, page)
                return

            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as exc:
                raise SourceParseError(self.source_name, str(exc)) from exc

            error = root.find(f"{_OAI_NS}error")
            if error is not None:
                logger.warning("arXiv OAI error: %s", error.text)
                return

            list_records = root.find(f"{_OAI_NS}ListRecords")
            if list_records is None:
                return

            records = list_records.findall(f"{_OAI_NS}record")
            logger.info("arXiv: page %d returned %d records", page, len(records))

            for record in records:
                parsed = self._parse_record(record)
                if parsed is not None:
                    yield parsed

            token_element = list_records.find(f"{_OAI_NS}resumptionToken")
            if token_element is None or not (token_element.text or "").strip():
                return
            resumption_token = token_element.text.strip()

        logger.warning("arXiv: reached max page bound (%d); stopping", _MAX_PAGES)

    def _build_params(self, resumption_token: str | None) -> dict[str, str]:
        """Build OAI-PMH query parameters for the next request.

        Args:
            resumption_token: Token from the previous page, if any.

        Returns:
            A parameter dict for the OAI-PMH request.
        """
        if resumption_token:
            return {"verb": "ListRecords", "resumptionToken": resumption_token}
        return {
            "verb": "ListRecords",
            "set": _SET,
            "metadataPrefix": _METADATA_PREFIX,
            "from": self.from_date,
        }

    def _parse_record(self, record: ET.Element) -> PaperRecord | None:
        """Convert an OAI-PMH record element into a :class:`PaperRecord`.

        Args:
            record: The ``<record>`` XML element.

        Returns:
            A normalised record, or ``None`` if it should be skipped.
        """
        header = record.find(f"{_OAI_NS}header")
        if header is not None and header.get("status") == "deleted":
            return None

        metadata = record.find(f"{_OAI_NS}metadata")
        if metadata is None:
            return None
        meta = metadata.find(f"{_ARXIV_NS}arXiv")
        if meta is None:
            return None

        title_el = meta.find(f"{_ARXIV_NS}title")
        abstract_el = meta.find(f"{_ARXIV_NS}abstract")
        id_el = meta.find(f"{_ARXIV_NS}id")
        created_el = meta.find(f"{_ARXIV_NS}created")

        if title_el is None or id_el is None or not (id_el.text or "").strip():
            return None

        title = (title_el.text or "").strip().replace("\n", " ")
        abstract = (
            (abstract_el.text or "").strip().replace("\n", " ")
            if abstract_el is not None
            else ""
        )
        arxiv_id = (id_el.text or "").strip()
        published = (created_el.text or "").strip() if created_el is not None else None

        authors = self._parse_authors(meta)

        try:
            return PaperRecord(
                title=title,
                abstract=abstract,
                authors=authors,
                doi=None,
                source=PaperSource.ARXIV,
                source_id=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                published_date=published,
            )
        except ValueError as exc:
            logger.debug("Skipping malformed arXiv record: %s", exc)
            return None

    @staticmethod
    def _parse_authors(meta: ET.Element) -> list[str]:
        """Extract author full names from arXiv metadata.

        Args:
            meta: The ``<arXiv>`` metadata element.

        Returns:
            A list of author display names.
        """
        authors: list[str] = []
        authors_el = meta.find(f"{_ARXIV_NS}authors")
        if authors_el is None:
            return authors

        for author in authors_el.findall(f"{_ARXIV_NS}author"):
            keyname = author.find(f"{_ARXIV_NS}keyname")
            forenames = author.find(f"{_ARXIV_NS}forenames")
            if keyname is not None and keyname.text:
                if forenames is not None and forenames.text:
                    authors.append(f"{forenames.text} {keyname.text}")
                else:
                    authors.append(keyname.text)
        return authors
