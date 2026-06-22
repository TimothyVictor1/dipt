"""Parser agent: downloads each paper's PDF and extracts its full text.

Papers that have a usable PDF advance to ``parsed`` with their extracted text
written to disk. Papers with no PDF, an invalid PDF, or no extractable text are
marked accordingly so downstream agents can fall back to the abstract.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import fitz  # PyMuPDF
import requests

from dipt.config import Settings
from dipt.database.repository import PaperRepository
from dipt.exceptions import RepositoryError
from dipt.models.schemas import PaperStatus

logger = logging.getLogger(__name__)

_MIN_TEXT_LENGTH: Final[int] = 500
_DOWNLOAD_DELAY_SECONDS: Final[float] = 2.0
_PDF_MAGIC: Final[bytes] = b"%PDF"
_USER_AGENT: Final[str] = (
    "DIPT-Research-Bot/1.0 (mailto:tira25@student.bth.se)"
)


@dataclass
class ParserSummary:
    """Outcome counts for a parser run.

    Attributes:
        parsed: Papers successfully parsed with extracted text.
        no_pdf: Papers with no usable PDF.
        no_text: Papers whose PDF yielded too little text.
        total: Total papers processed.
    """

    parsed: int = 0
    no_pdf: int = 0
    no_text: int = 0
    total: int = 0


def _clean_text(text: str) -> str:
    """Collapse excessive whitespace in extracted text.

    Args:
        text: Raw extracted text.

    Returns:
        Cleaned text.
    """
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {3,}", " ", text)
    return text.strip()


class ParserAgent:
    """Downloads PDFs and extracts text for fetched papers.

    Args:
        repository: Persistence layer.
        settings: Application settings (paths, timeout).
    """

    def __init__(self, repository: PaperRepository, settings: Settings) -> None:
        self._repo = repository
        self._settings = settings
        settings.ensure_directories()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _USER_AGENT})

    def run(self, limit: int | None = None) -> ParserSummary:
        """Parse all papers currently in the ``fetched`` state.

        Args:
            limit: Optional cap on the number of papers to process.

        Returns:
            A :class:`ParserSummary` for the run.
        """
        papers = self._repo.fetch_papers_by_status([PaperStatus.FETCHED], limit=limit)
        summary = ParserSummary(total=len(papers))
        logger.info("Parser agent: %d papers to process", summary.total)

        for index, paper in enumerate(papers, start=1):
            paper_id = paper["id"]
            title = paper["title"]
            pdf_url = paper.get("pdf_url") or ""

            logger.info("[%d/%d] Parsing paper %d", index, summary.total, paper_id)

            outcome = self._process_paper(paper_id, title, pdf_url)
            setattr(summary, outcome, getattr(summary, outcome) + 1)
            time.sleep(_DOWNLOAD_DELAY_SECONDS)

        logger.info(
            "Parser complete: parsed=%d, no_pdf=%d, no_text=%d",
            summary.parsed,
            summary.no_pdf,
            summary.no_text,
        )
        return summary

    def _process_paper(self, paper_id: int, title: str, pdf_url: str) -> str:
        """Download and extract one paper, updating its status.

        Args:
            paper_id: Paper primary key.
            title: Paper title (for logging).
            pdf_url: Direct PDF URL, possibly empty.

        Returns:
            The summary attribute name to increment: ``"parsed"``, ``"no_pdf"``,
            or ``"no_text"``.
        """
        if not pdf_url.strip():
            self._safe_update(paper_id, PaperStatus.NO_PDF)
            return "no_pdf"

        pdf_path = self._settings.pdf_dir / f"paper_{paper_id}.pdf"
        if not self._download_pdf(pdf_url, pdf_path):
            self._safe_update(paper_id, PaperStatus.NO_PDF)
            return "no_pdf"

        text = self._extract_text(pdf_path)
        if len(text) < _MIN_TEXT_LENGTH:
            logger.info("Paper %d yielded only %d chars", paper_id, len(text))
            self._safe_update(paper_id, PaperStatus.NO_TEXT)
            return "no_text"

        text_path = self._settings.text_dir / f"paper_{paper_id}.txt"
        try:
            text_path.write_text(text, encoding="utf-8")
        except OSError:
            logger.exception("Failed to write text file for paper %d", paper_id)
            self._safe_update(paper_id, PaperStatus.NO_TEXT)
            return "no_text"

        self._safe_update(paper_id, PaperStatus.PARSED, str(text_path))
        logger.info("Paper %d parsed (%d chars)", paper_id, len(text))
        return "parsed"

    def _download_pdf(self, url: str, save_path: Path) -> bool:
        """Download a URL to disk if it is a genuine PDF.

        Args:
            url: Source URL.
            save_path: Destination path.

        Returns:
            ``True`` on success, ``False`` if the content is not a valid PDF or
            the download fails.
        """
        try:
            response = self._session.get(
                url, timeout=self._settings.http_timeout_seconds, allow_redirects=True
            )
        except requests.RequestException as exc:
            logger.warning("Download failed for %s: %s", url[:70], exc)
            return False

        if response.status_code != 200:
            logger.warning("Download HTTP %d for %s", response.status_code, url[:70])
            return False

        if "html" in response.headers.get("Content-Type", "").lower():
            logger.debug("URL returned HTML, not a PDF: %s", url[:70])
            return False

        content = response.content
        if not content.startswith(_PDF_MAGIC):
            logger.debug("Content is not a valid PDF: %s", url[:70])
            return False

        try:
            save_path.write_bytes(content)
        except OSError:
            logger.exception("Failed to write PDF to %s", save_path)
            return False

        return True

    @staticmethod
    def _extract_text(pdf_path: Path) -> str:
        """Extract and clean all text from a PDF.

        Args:
            pdf_path: Path to the PDF on disk.

        Returns:
            The cleaned extracted text, or an empty string on failure.
        """
        try:
            with fitz.open(pdf_path) as document:
                text = "".join(page.get_text() for page in document)
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning("Text extraction failed for %s: %s", pdf_path.name, exc)
            return ""
        return _clean_text(text)

    def _safe_update(
        self,
        paper_id: int,
        status: PaperStatus,
        full_text_path: str | None = None,
    ) -> None:
        """Update paper status, logging any repository failure.

        Args:
            paper_id: Paper primary key.
            status: New status.
            full_text_path: Optional text file path.
        """
        try:
            self._repo.update_status(paper_id, status, full_text_path)
        except RepositoryError:
            logger.exception("Failed to update status for paper %d", paper_id)
