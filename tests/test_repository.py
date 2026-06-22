"""Unit tests for :mod:`dipt.database.repository`.

The connection pool and cursor are mocked so these tests exercise the
repository's SQL-issuing and result-mapping logic without a live database.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

import pytest

from dipt.database.repository import PaperRepository
from dipt.models.schemas import (
    CategoryAssignment,
    PaperRecord,
    PaperSource,
    PaperStatus,
)


class _FakePool:
    """Minimal connection-pool stand-in wrapping a mock cursor."""

    def __init__(self, cursor: MagicMock) -> None:
        self._cursor = cursor
        self.conn = MagicMock()

    @contextmanager
    def connection(self):
        """Yield a mock connection whose cursor is the provided mock."""
        cm = MagicMock()
        cm.__enter__.return_value = self._cursor
        cm.__exit__.return_value = False
        self.conn.cursor.return_value = cm
        yield self.conn


def _make_record() -> PaperRecord:
    """Build a representative paper record for insert tests."""
    return PaperRecord(
        title="An Empirical Study of CI Pipelines",
        abstract="We study CI.",
        authors=["Grace Hopper"],
        doi="10.1000/abc",
        source=PaperSource.ARXIV,
        source_id="https://arxiv.org/abs/2601.00001",
        pdf_url="https://arxiv.org/pdf/2601.00001",
        published_date=date(2026, 5, 1),
    )


def test_insert_paper_returns_true_on_new_row() -> None:
    """A returned id indicates a freshly inserted paper."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (42,)
    repo = PaperRepository(_FakePool(cursor))

    assert repo.insert_paper(_make_record()) is True


def test_insert_paper_returns_false_on_duplicate() -> None:
    """No returned id indicates a duplicate that was ignored."""
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    repo = PaperRepository(_FakePool(cursor))

    assert repo.insert_paper(_make_record()) is False


def test_fetch_papers_by_status_requires_statuses() -> None:
    """An empty status list is a programming error and must raise."""
    repo = PaperRepository(_FakePool(MagicMock()))
    with pytest.raises(ValueError):
        repo.fetch_papers_by_status([])


def test_fetch_papers_by_status_maps_rows_to_dicts() -> None:
    """Rows should be mapped to dicts keyed by column name."""
    cursor = MagicMock()
    cursor.description = [
        ("id",), ("title",), ("abstract",), ("status",),
        ("full_text_path",), ("source",),
    ]
    cursor.fetchall.return_value = [
        (1, "Title A", "Abs A", "parsed", "/tmp/a.txt", "arxiv"),
    ]
    repo = PaperRepository(_FakePool(cursor))

    rows = repo.fetch_papers_by_status([PaperStatus.PARSED])

    assert rows == [
        {
            "id": 1,
            "title": "Title A",
            "abstract": "Abs A",
            "status": "parsed",
            "full_text_path": "/tmp/a.txt",
            "source": "arxiv",
        }
    ]


def test_save_category_assignments_returns_count() -> None:
    """The number of persisted assignments should be returned."""
    cursor = MagicMock()
    repo = PaperRepository(_FakePool(cursor))

    assignments = [
        CategoryAssignment(category_id=1, category_name="Testing", confidence=0.9),
        CategoryAssignment(category_id=2, category_name="Empirical", confidence=0.8),
    ]

    assert repo.save_category_assignments(paper_id=1, assignments=assignments) == 2


def test_save_category_assignments_empty_is_noop() -> None:
    """Persisting an empty assignment list should write nothing."""
    repo = PaperRepository(_FakePool(MagicMock()))
    assert repo.save_category_assignments(paper_id=1, assignments=[]) == 0
