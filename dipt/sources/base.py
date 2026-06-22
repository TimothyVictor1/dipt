"""Abstract base class for paper sources.

Each concrete source (OpenAlex, arXiv, etc.) implements :meth:`fetch`, yielding
normalised :class:`PaperRecord` objects. The fetch agent depends only on this
interface, not on any concrete source — satisfying the Dependency Inversion
Principle and making new sources trivial to add.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Iterator

from dipt.models.schemas import PaperRecord

logger = logging.getLogger(__name__)


class PaperSourceFetcher(ABC):
    """Interface that every paper source must implement.

    Args:
        contact_email: Email used in polite-pool headers / params.
        timeout_seconds: Per-request network timeout.
        days_back: How many days back to look for new papers.
    """

    #: Human-readable name of the source, set by subclasses.
    source_name: str = "base"

    def __init__(
        self,
        contact_email: str,
        timeout_seconds: float,
        days_back: int,
    ) -> None:
        self._contact_email = contact_email
        self._timeout = timeout_seconds
        self._days_back = days_back

    @property
    def from_date(self) -> str:
        """Return the lower-bound publication date as an ISO string."""
        cutoff = datetime.now() - timedelta(days=self._days_back)
        return cutoff.strftime("%Y-%m-%d")

    @abstractmethod
    def fetch(self) -> Iterator[PaperRecord]:
        """Yield normalised paper records from this source.

        Implementations must not raise on a single bad record; they should log
        and skip it. They may raise :class:`SourceError` subclasses for whole-
        source failures (e.g. the API being unreachable).

        Yields:
            Normalised :class:`PaperRecord` objects.
        """
        raise NotImplementedError
