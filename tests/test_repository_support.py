"""Unit tests for the dashboard / scheduler repository methods.

The connection pool and cursor are mocked, so these exercise the SQL-issuing
and result-mapping logic without a live database, matching the style of
``tests/test_repository.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from unittest.mock import MagicMock

import psycopg2
import pytest

from dipt.database.repository import PaperRepository
from dipt.exceptions import RepositoryError


class _FakePool:
    """Minimal connection-pool stand-in wrapping a mock cursor."""

    def __init__(self, cursor: MagicMock) -> None:
        self._cursor = cursor
        self.conn = MagicMock()

    @contextmanager
    def connection(self):
        cm = MagicMock()
        cm.__enter__.return_value = self._cursor
        cm.__exit__.return_value = False
        self.conn.cursor.return_value = cm
        yield self.conn


def _repo(cursor: MagicMock) -> PaperRepository:
    return PaperRepository(_FakePool(cursor))


# ── fetch_log support table ──────────────────────────────────────────────
def test_support_tables_present_true_when_fetch_log_exists() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    assert _repo(cursor).support_tables_present() is True


def test_support_tables_present_false_when_missing() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = (0,)
    assert _repo(cursor).support_tables_present() is False


def test_ensure_support_tables_short_circuits_when_present() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    repo = _repo(cursor)

    assert repo.ensure_support_tables() is True
    # Only the existence check ran, no DDL.
    assert cursor.execute.call_count == 1


def test_ensure_support_tables_degrades_on_privilege_error() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = (0,)
    cursor.execute.side_effect = [None, psycopg2.errors.InsufficientPrivilege()]
    repo = _repo(cursor)

    assert repo.ensure_support_tables() is False


# ── categories ───────────────────────────────────────────────────────────
def test_add_category_returns_new_id() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = (51,)
    assert _repo(cursor).add_category("New Topic", "desc") == 51


def test_add_category_wraps_errors() -> None:
    cursor = MagicMock()
    cursor.execute.side_effect = psycopg2.Error("duplicate")
    with pytest.raises(RepositoryError):
        _repo(cursor).add_category("Dup")


def test_list_all_categories_maps_rows() -> None:
    cursor = MagicMock()
    cursor.description = [
        ("id",), ("name",), ("description",), ("is_active",), ("paper_count",)
    ]
    cursor.fetchall.return_value = [(1, "Testing", "", True, 12)]
    rows = _repo(cursor).list_all_categories()
    assert rows == [
        {
            "id": 1,
            "name": "Testing",
            "description": "",
            "is_active": True,
            "paper_count": 12,
        }
    ]


# ── QA flags ─────────────────────────────────────────────────────────────
def test_resolve_qa_flag_executes_update() -> None:
    cursor = MagicMock()
    _repo(cursor).resolve_qa_flag(9)
    args = cursor.execute.call_args[0]
    assert args[1] == (9,)


def test_list_open_qa_flags_maps_rows() -> None:
    cursor = MagicMock()
    cursor.description = [
        ("id",), ("paper_id",), ("title",), ("flag_type",),
        ("description",), ("flagged_at",),
    ]
    cursor.fetchall.return_value = [
        (3, 21, "A paper", "score_still_failing", "why", "2026-09-07")
    ]
    rows = _repo(cursor).list_open_qa_flags()
    assert rows[0]["paper_id"] == 21
    assert rows[0]["flag_type"] == "score_still_failing"


# ── paper detail / overview ──────────────────────────────────────────────
def test_get_paper_detail_returns_none_when_missing() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    assert _repo(cursor).get_paper_detail(999) is None


def test_get_paper_detail_assembles_related_rows() -> None:
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        (5, "Title", "Abstract", ["A. Author"], "10.1/x", "arxiv",
         "approved", date(2026, 5, 1), "http://pdf"),
        ("Research Problem:\nx",),          # summaries
        (8.5, "Strong practical value."),   # scores
    ]
    cursor.fetchall.side_effect = [
        [("Software Testing", 0.9), ("DevOps", 0.7)],  # categories
    ]

    detail = _repo(cursor).get_paper_detail(5)

    assert detail["id"] == 5
    assert detail["authors"] == ["A. Author"]
    assert detail["categories"] == [("Software Testing", 0.9), ("DevOps", 0.7)]
    assert detail["summary"] == "Research Problem:\nx"
    assert detail["score"] == 8.5
    assert detail["rationale"] == "Strong practical value."


def test_list_papers_overview_without_status_filter() -> None:
    cursor = MagicMock()
    cursor.description = [
        ("id",), ("title",), ("status",), ("source",),
        ("published_date",), ("relevance_score",), ("categories",),
    ]
    cursor.fetchall.return_value = [
        (1, "T", "approved", "arxiv", date(2026, 1, 1), 7.0, "Testing, DevOps")
    ]
    rows = _repo(cursor).list_papers_overview(limit=10)
    assert rows[0]["categories"] == "Testing, DevOps"
    sql = cursor.execute.call_args[0][0]
    assert "WHERE p.status" not in sql


def test_list_papers_overview_with_status_filter() -> None:
    cursor = MagicMock()
    cursor.description = [
        ("id",), ("title",), ("status",), ("source",),
        ("published_date",), ("relevance_score",), ("categories",),
    ]
    cursor.fetchall.return_value = []
    _repo(cursor).list_papers_overview(status="scored", limit=5)
    params = cursor.execute.call_args[0][1]
    assert params[0] == "scored"


# ── approved-papers export ───────────────────────────────────────────────
def test_list_approved_papers_shapes_rows() -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        (
            10, "Paper A", "Abstract A", ["Jane Roe", "John Doe"], "10.1/a",
            "arxiv", date(2026, 6, 1), 9.0, "Very relevant.",
            "Research Problem:\n...", ["AI4SE", "Testing"],
        ),
        (
            11, "Paper B", None, None, None, "openalex", None, None, None,
            None, None,
        ),
    ]
    rows = _repo(cursor).list_approved_papers()

    assert rows[0]["authors"] == ["Jane Roe", "John Doe"]
    assert rows[0]["published_date"] == "2026-06-01"
    assert rows[0]["categories"] == ["AI4SE", "Testing"]
    # Null-heavy second row is normalised to safe defaults.
    assert rows[1]["abstract"] == ""
    assert rows[1]["authors"] == []
    assert rows[1]["published_date"] is None
    assert rows[1]["categories"] == []
    assert rows[1]["summary"] == ""


# ── fetch log ────────────────────────────────────────────────────────────
def test_get_last_successful_fetch_returns_datetime() -> None:
    cursor = MagicMock()
    when = datetime(2026, 9, 1, 12, 0, 0)
    cursor.fetchone.return_value = (when,)
    assert _repo(cursor).get_last_successful_fetch() == when


def test_get_last_successful_fetch_returns_none_when_no_runs() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    assert _repo(cursor).get_last_successful_fetch() is None


def test_start_fetch_run_returns_row_id() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = (42,)
    assert _repo(cursor).start_fetch_run(datetime(2026, 9, 7)) == 42


def test_finish_fetch_run_passes_all_fields() -> None:
    cursor = MagicMock()
    _repo(cursor).finish_fetch_run(42, datetime(2026, 9, 7), 5, "success")
    params = cursor.execute.call_args[0][1]
    assert params[1] == 5
    assert params[2] == "success"
    assert params[3] == 42
