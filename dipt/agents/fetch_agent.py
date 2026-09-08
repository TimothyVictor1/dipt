"""Fetch agent: orchestrates all paper sources through the quality gate.

The agent iterates every configured source, passes each candidate paper through
the LLM quality gate, and persists those that pass. It depends only on the
:class:`PaperSourceFetcher` interface, so adding or removing sources requires no
change to this class.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from dipt.agents.quality_gate import QualityGate
from dipt.database.repository import PaperRepository
from dipt.exceptions import RepositoryError, SourceError
from dipt.sources.base import PaperSourceFetcher

logger = logging.getLogger(__name__)


@dataclass
class FetchSummary:
    """Aggregated outcome of a fetch run.

    Attributes:
        saved: Count of newly persisted papers per source.
        rejected: Count of papers rejected by the quality gate per source.
        duplicates: Count of papers skipped as duplicates per source.
    """

    saved: dict[str, int] = field(default_factory=dict)
    rejected: dict[str, int] = field(default_factory=dict)
    duplicates: dict[str, int] = field(default_factory=dict)

    @property
    def total_saved(self) -> int:
        """Return the total number of papers saved across all sources."""
        return sum(self.saved.values())


class FetchAgent:
    """Coordinates fetching, quality-gating, and persisting papers.

    Args:
        sources: The paper sources to harvest.
        quality_gate: The SE-relevance gate every paper must pass.
        repository: Persistence layer.
    """

    def __init__(
        self,
        sources: list[PaperSourceFetcher],
        quality_gate: QualityGate,
        repository: PaperRepository,
    ) -> None:
        self._sources = sources
        self._gate = quality_gate
        self._repo = repository

    def run(self, limit: int | None = None) -> FetchSummary:
        """Execute a full fetch across every configured source.

        Args:
            limit: Optional cap on the total number of *new* papers saved
                across all sources. Once reached, remaining candidates and
                sources are skipped. ``None`` means save everything that passes
                the gate.

        Returns:
            A :class:`FetchSummary` describing the run.
        """
        summary = FetchSummary()

        for source in self._sources:
            name = source.source_name
            summary.saved.setdefault(name, 0)
            summary.rejected.setdefault(name, 0)
            summary.duplicates.setdefault(name, 0)

            if limit is not None and summary.total_saved >= limit:
                logger.info(
                    "Fetch agent: save limit %d reached, skipping source '%s'",
                    limit,
                    name,
                )
                continue

            logger.info("Fetch agent: starting source '%s'", name)
            try:
                self._process_source(source, summary, limit)
            except SourceError:
                logger.exception("Source '%s' failed; continuing with others", name)

            logger.info(
                "Fetch agent: source '%s' done (saved=%d, rejected=%d, dup=%d)",
                name,
                summary.saved[name],
                summary.rejected[name],
                summary.duplicates[name],
            )

        logger.info("Fetch agent complete: %d papers saved", summary.total_saved)
        return summary

    def _process_source(
        self,
        source: PaperSourceFetcher,
        summary: FetchSummary,
        limit: int | None = None,
    ) -> None:
        """Process every record from a single source.

        Args:
            source: The source to harvest.
            summary: The summary object to update in place.
            limit: Optional cap on the total number of saved papers; when the
                running total reaches it, processing stops early.
        """
        name = source.source_name

        for record in source.fetch():
            if limit is not None and summary.total_saved >= limit:
                logger.info(
                    "Fetch agent: save limit %d reached, stopping source '%s'",
                    limit,
                    name,
                )
                return

            if not self._gate.is_software_engineering(record.title, record.abstract):
                summary.rejected[name] += 1
                logger.debug("Rejected (not SE): %s", record.title[:60])
                continue

            try:
                inserted = self._repo.insert_paper(record)
            except RepositoryError:
                logger.exception("Failed to persist paper '%s'", record.source_id)
                continue

            if inserted:
                summary.saved[name] += 1
                logger.info("Saved: %s", record.title[:60])
            else:
                summary.duplicates[name] += 1
                logger.debug("Duplicate skipped: %s", record.title[:60])
