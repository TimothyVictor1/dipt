"""Data-access layer for the DIPT system.

The :class:`PaperRepository` encapsulates every SQL statement the application
issues. Keeping SQL in one place (rather than scattered through agents) means
the schema can evolve without touching business logic, and every query is
parameterised, eliminating SQL-injection risk.
"""

from __future__ import annotations

import logging
from typing import Sequence

import psycopg2

from dipt.database.connection import ConnectionPool
from dipt.exceptions import RepositoryError
from dipt.models.schemas import (
    Category,
    CategoryAssignment,
    PaperRecord,
    PaperStatus,
)

logger = logging.getLogger(__name__)


class PaperRepository:
    """Repository for all paper- and category-related persistence.

    Args:
        pool: A configured :class:`ConnectionPool`.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # ── Writes: papers ──────────────────────────────────────────────────
    def insert_paper(self, record: PaperRecord) -> bool:
        """Insert a paper, ignoring duplicates by ``source_id``.

        Args:
            record: The normalised paper to insert.

        Returns:
            ``True`` if a new row was inserted, ``False`` if it already existed.

        Raises:
            RepositoryError: If the insert fails for any non-duplicate reason.
        """
        sql = """
            INSERT INTO papers
                (title, abstract, authors, doi, source, source_id,
                 pdf_url, published_date, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO NOTHING
            RETURNING id
        """
        params = (
            record.title,
            record.abstract,
            record.authors,
            record.doi,
            record.source.value,
            record.source_id,
            record.pdf_url,
            record.published_date,
            PaperStatus.FETCHED.value,
        )
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    inserted = cur.fetchone() is not None
            return inserted
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to insert paper '{record.source_id}': {exc}"
            ) from exc

    def update_status(
        self,
        paper_id: int,
        status: PaperStatus,
        full_text_path: str | None = None,
    ) -> None:
        """Update a paper's status and optionally its text-file path.

        Args:
            paper_id: Primary key of the paper.
            status: New lifecycle status.
            full_text_path: Path to the extracted text file, if applicable.

        Raises:
            RepositoryError: If the update fails.
        """
        if full_text_path is not None:
            sql = "UPDATE papers SET status = %s, full_text_path = %s WHERE id = %s"
            params: tuple = (status.value, full_text_path, paper_id)
        else:
            sql = "UPDATE papers SET status = %s WHERE id = %s"
            params = (status.value, paper_id)

        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to update status for paper {paper_id}: {exc}"
            ) from exc

    # ── Reads: papers ───────────────────────────────────────────────────
    def fetch_papers_by_status(
        self,
        statuses: Sequence[PaperStatus],
        limit: int | None = None,
    ) -> list[dict]:
        """Return papers whose status is in ``statuses``.

        Results are ordered to prioritise higher-quality sources first
        (arXiv, OpenAlex), which is useful when a ``limit`` is applied during
        development.

        Args:
            statuses: Statuses to match.
            limit: Optional maximum number of rows to return.

        Returns:
            A list of dict rows with keys: ``id``, ``title``, ``abstract``,
            ``status``, ``full_text_path``, ``source``.

        Raises:
            RepositoryError: If the query fails.
        """
        if not statuses:
            raise ValueError("statuses must not be empty")

        status_values = [s.value for s in statuses]
        sql = """
            SELECT id, title, abstract, status, full_text_path, source
            FROM papers
            WHERE status = ANY(%s)
            ORDER BY
                CASE source
                    WHEN 'arxiv'    THEN 1
                    WHEN 'openalex' THEN 2
                    WHEN 'core'     THEN 3
                    WHEN 'dblp'     THEN 4
                    ELSE 5
                END,
                id ASC
        """
        params: list = [status_values]
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)

        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except psycopg2.Error as exc:
            raise RepositoryError(f"Failed to fetch papers by status: {exc}") from exc

    def count_by_status(self) -> dict[str, int]:
        """Return a mapping of status to paper count.

        Returns:
            Dict mapping each status string to its count.

        Raises:
            RepositoryError: If the query fails.
        """
        sql = "SELECT status, COUNT(*) FROM papers GROUP BY status"
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    return {row[0]: row[1] for row in cur.fetchall()}
        except psycopg2.Error as exc:
            raise RepositoryError(f"Failed to count papers by status: {exc}") from exc

    # ── Categories ──────────────────────────────────────────────────────
    def load_active_categories(self) -> list[Category]:
        """Load all active categories ordered by id.

        Returns:
            A list of :class:`Category` objects.

        Raises:
            RepositoryError: If the query fails.
        """
        sql = """
            SELECT id, name, COALESCE(description, '')
            FROM categories
            WHERE is_active = TRUE
            ORDER BY id ASC
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()
            return [
                Category(id=row[0], name=row[1], description=row[2])
                for row in rows
            ]
        except psycopg2.Error as exc:
            raise RepositoryError(f"Failed to load categories: {exc}") from exc

    def save_category_assignments(
        self,
        paper_id: int,
        assignments: Sequence[CategoryAssignment],
    ) -> int:
        """Persist category assignments for a paper.

        Existing assignments for the same (paper, category) pair have their
        confidence updated.

        Args:
            paper_id: The paper receiving the assignments.
            assignments: Category assignments to persist.

        Returns:
            The number of assignment rows written.

        Raises:
            RepositoryError: If the write fails.
        """
        if not assignments:
            return 0

        sql = """
            INSERT INTO paper_categories (paper_id, category_id, confidence)
            VALUES (%s, %s, %s)
            ON CONFLICT (paper_id, category_id)
            DO UPDATE SET confidence = EXCLUDED.confidence
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    for assignment in assignments:
                        cur.execute(
                            sql,
                            (paper_id, assignment.category_id, assignment.confidence),
                        )
            return len(assignments)
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to save category assignments for paper {paper_id}: {exc}"
            ) from exc
