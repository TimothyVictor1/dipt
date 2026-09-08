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

    # ── Summaries ───────────────────────────────────────────────────────
    def save_summary(
        self, paper_id: int, summary_text: str, model_used: str
    ) -> None:
        """Persist (or replace) a paper's summary.

        Each paper has at most one summary; re-running the summarisation stage
        overwrites the previous text and records the model used.

        Args:
            paper_id: The paper being summarised.
            summary_text: The rendered summary to store.
            model_used: Tag of the model that produced the summary.

        Raises:
            RepositoryError: If the write fails.
        """
        sql = """
            INSERT INTO summaries (paper_id, summary_text, model_used)
            VALUES (%s, %s, %s)
            ON CONFLICT (paper_id)
            DO UPDATE SET
                summary_text = EXCLUDED.summary_text,
                model_used = EXCLUDED.model_used,
                generated_at = now()
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (paper_id, summary_text, model_used))
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to save summary for paper {paper_id}: {exc}"
            ) from exc

    def get_summary(self, paper_id: int) -> str | None:
        """Return the stored summary text for a paper, if any.

        Args:
            paper_id: The paper to look up.

        Returns:
            The summary text, or ``None`` if the paper has no summary.

        Raises:
            RepositoryError: If the query fails.
        """
        sql = "SELECT summary_text FROM summaries WHERE paper_id = %s"
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (paper_id,))
                    row = cur.fetchone()
            return row[0] if row else None
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to load summary for paper {paper_id}: {exc}"
            ) from exc

    # ── Scores ──────────────────────────────────────────────────────────
    def save_score(
        self,
        paper_id: int,
        relevance_score: float,
        rationale: str,
        model_used: str,
    ) -> None:
        """Persist (or replace) a paper's industrial-relevance score.

        Args:
            paper_id: The paper being scored.
            relevance_score: Score on the 1 to 10 scale.
            rationale: Written justification for the score.
            model_used: Tag of the model that produced the score.

        Raises:
            RepositoryError: If the write fails.
        """
        sql = """
            INSERT INTO scores (paper_id, relevance_score, score_rationale, model_used)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (paper_id)
            DO UPDATE SET
                relevance_score = EXCLUDED.relevance_score,
                score_rationale = EXCLUDED.score_rationale,
                model_used = EXCLUDED.model_used,
                scored_at = now()
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql, (paper_id, relevance_score, rationale, model_used)
                    )
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to save score for paper {paper_id}: {exc}"
            ) from exc

    def get_score(self, paper_id: int) -> dict | None:
        """Return the stored score for a paper, if any.

        Args:
            paper_id: The paper to look up.

        Returns:
            A dict with keys ``relevance_score`` and ``score_rationale``, or
            ``None`` if the paper has not been scored.

        Raises:
            RepositoryError: If the query fails.
        """
        sql = (
            "SELECT relevance_score, score_rationale FROM scores "
            "WHERE paper_id = %s"
        )
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (paper_id,))
                    row = cur.fetchone()
            if row is None:
                return None
            return {"relevance_score": row[0], "score_rationale": row[1]}
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to load score for paper {paper_id}: {exc}"
            ) from exc

    # ── QA flags ────────────────────────────────────────────────────────
    def save_qa_flag(
        self, paper_id: int, flag_type: str, description: str
    ) -> None:
        """Record an unresolved QA flag for human review.

        Args:
            paper_id: The paper being flagged.
            flag_type: Short machine-readable category of the problem.
            description: Human-readable explanation of the problem.

        Raises:
            RepositoryError: If the write fails.
        """
        sql = """
            INSERT INTO qa_flags (paper_id, flag_type, description)
            VALUES (%s, %s, %s)
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (paper_id, flag_type, description))
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to save QA flag for paper {paper_id}: {exc}"
            ) from exc

    def fetch_papers_for_qa(self, limit: int | None = None) -> list[dict]:
        """Return scored papers that have no outstanding QA flag.

        Papers already carrying an unresolved flag are excluded so a failed
        paper is not reprocessed on every run; a human must clear the flag
        first.

        Args:
            limit: Optional maximum number of rows to return.

        Returns:
            A list of dict rows with keys: ``id``, ``title``, ``abstract``,
            ``status``, ``full_text_path``, ``source``.

        Raises:
            RepositoryError: If the query fails.
        """
        sql = """
            SELECT id, title, abstract, status, full_text_path, source
            FROM papers p
            WHERE p.status = %s
              AND NOT EXISTS (
                  SELECT 1 FROM qa_flags q
                  WHERE q.paper_id = p.id AND q.resolved = FALSE
              )
            ORDER BY id ASC
        """
        params: list = [PaperStatus.SCORED.value]
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
            raise RepositoryError(
                f"Failed to fetch papers for QA: {exc}"
            ) from exc

    def list_open_qa_flags(self) -> list[dict]:
        """Return unresolved QA flags joined to their paper title.

        Returns:
            A list of dict rows with keys: ``id``, ``paper_id``, ``title``,
            ``flag_type``, ``description``, ``flagged_at``.

        Raises:
            RepositoryError: If the query fails.
        """
        sql = """
            SELECT q.id, q.paper_id, p.title, q.flag_type, q.description,
                   q.flagged_at
            FROM qa_flags q
            JOIN papers p ON p.id = q.paper_id
            WHERE q.resolved = FALSE
            ORDER BY q.flagged_at ASC
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except psycopg2.Error as exc:
            raise RepositoryError(f"Failed to list QA flags: {exc}") from exc

    def resolve_qa_flag(self, flag_id: int) -> None:
        """Mark a single QA flag as resolved.

        Args:
            flag_id: Primary key of the flag row.

        Raises:
            RepositoryError: If the update fails.
        """
        sql = "UPDATE qa_flags SET resolved = TRUE WHERE id = %s"
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (flag_id,))
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to resolve QA flag {flag_id}: {exc}"
            ) from exc

    # ── Dashboard reads ─────────────────────────────────────────────────
    def list_papers_overview(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Return a page of papers with their score and category names.

        Args:
            status: Optional exact status to filter by.
            limit: Maximum rows to return.
            offset: Row offset for pagination.

        Returns:
            A list of dict rows with keys: ``id``, ``title``, ``status``,
            ``source``, ``published_date``, ``relevance_score``,
            ``categories`` (comma-separated names or ``None``).

        Raises:
            RepositoryError: If the query fails.
        """
        sql = """
            SELECT
                p.id, p.title, p.status, p.source, p.published_date,
                s.relevance_score,
                (
                    SELECT string_agg(c.name, ', ' ORDER BY c.name)
                    FROM paper_categories pc
                    JOIN categories c ON c.id = pc.category_id
                    WHERE pc.paper_id = p.id
                ) AS categories
            FROM papers p
            LEFT JOIN scores s ON s.paper_id = p.id
        """
        params: list = []
        if status:
            sql += " WHERE p.status = %s"
            params.append(status)
        sql += " ORDER BY s.relevance_score DESC NULLS LAST, p.id DESC"
        sql += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to list papers overview: {exc}"
            ) from exc

    def get_paper_detail(self, paper_id: int) -> dict | None:
        """Return a single paper with its categories, summary, and score.

        Args:
            paper_id: The paper to look up.

        Returns:
            A dict with keys: ``id``, ``title``, ``abstract``, ``authors``,
            ``doi``, ``source``, ``status``, ``published_date``, ``pdf_url``,
            ``categories`` (list of ``(name, confidence)`` tuples), ``summary``
            (text or ``None``), ``score`` (float or ``None``), ``rationale``
            (text or ``None``). Returns ``None`` if the paper does not exist.

        Raises:
            RepositoryError: If the query fails.
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, title, abstract, authors, doi, source,
                               status, published_date, pdf_url
                        FROM papers WHERE id = %s
                        """,
                        (paper_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return None
                    detail = {
                        "id": row[0],
                        "title": row[1],
                        "abstract": row[2],
                        "authors": row[3] or [],
                        "doi": row[4],
                        "source": row[5],
                        "status": row[6],
                        "published_date": row[7],
                        "pdf_url": row[8],
                    }

                    cur.execute(
                        """
                        SELECT c.name, pc.confidence
                        FROM paper_categories pc
                        JOIN categories c ON c.id = pc.category_id
                        WHERE pc.paper_id = %s
                        ORDER BY pc.confidence DESC
                        """,
                        (paper_id,),
                    )
                    detail["categories"] = [
                        (name, conf) for name, conf in cur.fetchall()
                    ]

                    cur.execute(
                        "SELECT summary_text FROM summaries WHERE paper_id = %s",
                        (paper_id,),
                    )
                    summary_row = cur.fetchone()
                    detail["summary"] = summary_row[0] if summary_row else None

                    cur.execute(
                        """
                        SELECT relevance_score, score_rationale
                        FROM scores WHERE paper_id = %s
                        """,
                        (paper_id,),
                    )
                    score_row = cur.fetchone()
                    detail["score"] = score_row[0] if score_row else None
                    detail["rationale"] = score_row[1] if score_row else None
            return detail
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to load paper detail for {paper_id}: {exc}"
            ) from exc

    def list_approved_papers(self) -> list[dict]:
        """Return every approved paper with everything the public site needs.

        Returns:
            A list of dict rows, one per ``approved`` paper, ordered by score
            then recency, with keys: ``id``, ``title``, ``abstract``,
            ``authors``, ``doi``, ``source``, ``published_date``,
            ``relevance_score``, ``score_rationale``, ``summary``,
            ``categories`` (list of category-name strings).

        Raises:
            RepositoryError: If the query fails.
        """
        sql = """
            SELECT
                p.id, p.title, p.abstract, p.authors, p.doi, p.source,
                p.published_date,
                s.relevance_score, s.score_rationale,
                su.summary_text,
                (
                    SELECT array_agg(c.name ORDER BY pc.confidence DESC)
                    FROM paper_categories pc
                    JOIN categories c ON c.id = pc.category_id
                    WHERE pc.paper_id = p.id
                ) AS categories
            FROM papers p
            LEFT JOIN scores s ON s.paper_id = p.id
            LEFT JOIN summaries su ON su.paper_id = p.id
            WHERE p.status = %s
            ORDER BY s.relevance_score DESC NULLS LAST, p.published_date DESC NULLS LAST,
                     p.id DESC
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (PaperStatus.APPROVED.value,))
                    rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "title": row[1],
                    "abstract": row[2] or "",
                    "authors": list(row[3]) if row[3] else [],
                    "doi": row[4],
                    "source": row[5],
                    "published_date": (
                        row[6].isoformat() if row[6] is not None else None
                    ),
                    "relevance_score": row[7],
                    "score_rationale": row[8] or "",
                    "summary": row[9] or "",
                    "categories": list(row[10]) if row[10] else [],
                }
                for row in rows
            ]
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to list approved papers: {exc}"
            ) from exc

    # ── Category administration ─────────────────────────────────────────
    def list_all_categories(self) -> list[dict]:
        """Return every category, active or not, with its paper count.

        Returns:
            A list of dict rows with keys: ``id``, ``name``, ``description``,
            ``is_active``, ``paper_count``.

        Raises:
            RepositoryError: If the query fails.
        """
        sql = """
            SELECT c.id, c.name, COALESCE(c.description, '') AS description,
                   c.is_active,
                   (SELECT COUNT(*) FROM paper_categories pc
                    WHERE pc.category_id = c.id) AS paper_count
            FROM categories c
            ORDER BY c.id ASC
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to list all categories: {exc}"
            ) from exc

    def add_category(self, name: str, description: str = "") -> int:
        """Insert a new category.

        Args:
            name: Unique category name.
            description: Optional description used in LLM prompts.

        Returns:
            The new category's primary key.

        Raises:
            RepositoryError: If the insert fails (including a duplicate name).
        """
        sql = """
            INSERT INTO categories (name, description, is_active)
            VALUES (%s, %s, TRUE)
            RETURNING id
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (name.strip(), description.strip()))
                    return int(cur.fetchone()[0])
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to add category '{name}': {exc}"
            ) from exc

    def update_category(
        self, category_id: int, name: str, description: str
    ) -> None:
        """Update a category's name and description.

        Args:
            category_id: Primary key of the category.
            name: New name.
            description: New description.

        Raises:
            RepositoryError: If the update fails.
        """
        sql = "UPDATE categories SET name = %s, description = %s WHERE id = %s"
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql, (name.strip(), description.strip(), category_id)
                    )
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to update category {category_id}: {exc}"
            ) from exc

    def set_category_active(self, category_id: int, is_active: bool) -> None:
        """Activate or deactivate a category without deleting it.

        Deactivating keeps historical assignments intact but removes the
        category from the list the categorisation agent offers the model.

        Args:
            category_id: Primary key of the category.
            is_active: New active flag.

        Raises:
            RepositoryError: If the update fails.
        """
        sql = "UPDATE categories SET is_active = %s WHERE id = %s"
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (is_active, category_id))
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to set category {category_id} active={is_active}: {exc}"
            ) from exc

    # ── Fetch log (scheduler support table) ────────────────────────────
    _SUPPORT_TABLES: Sequence[str] = ("fetch_log",)

    _SUPPORT_DDL: str = """
        CREATE TABLE IF NOT EXISTS fetch_log (
            id           SERIAL PRIMARY KEY,
            started_at   TIMESTAMP NOT NULL,
            finished_at  TIMESTAMP,
            papers_saved INTEGER NOT NULL DEFAULT 0,
            status       VARCHAR(16) NOT NULL DEFAULT 'running'
        );
    """

    def support_tables_present(self) -> bool:
        """Return whether the ``fetch_log`` scheduler table exists.

        Returns:
            ``True`` if ``fetch_log`` is present.

        Raises:
            RepositoryError: If the catalogue query fails.
        """
        sql = """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = ANY(%s)
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (list(self._SUPPORT_TABLES),))
                    return cur.fetchone()[0] == len(self._SUPPORT_TABLES)
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to check for support tables: {exc}"
            ) from exc

    def ensure_support_tables(self) -> bool:
        """Ensure the ``fetch_log`` scheduler table exists.

        ``fetch_log`` holds one row per pipeline run so the scheduler can fetch
        only papers newer than the last successful run. If it is missing this
        tries to create it, which needs CREATE on schema ``public``; the
        application role may not hold that, in which case the operator applies
        ``migrations/001_support_tables.sql`` once as the database owner. This
        never raises for a privilege problem: an unprivileged start-up just
        loses incremental fetch and run history.

        (Agent prompt overrides do **not** need this table - they live in
        ``config/agent_prompts/`` via :mod:`dipt.prompt_store`.)

        Returns:
            ``True`` if the table is present afterwards, ``False`` otherwise.
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = ANY(%s)
                        """,
                        (list(self._SUPPORT_TABLES),),
                    )
                    if cur.fetchone()[0] == len(self._SUPPORT_TABLES):
                        return True

                    # Only attempt the DDL if this role can actually create in
                    # schema public; otherwise a failed CREATE would surface a
                    # noisy rollback for an entirely expected condition.
                    cur.execute(
                        "SELECT has_schema_privilege("
                        "current_user, 'public', 'CREATE')"
                    )
                    if not cur.fetchone()[0]:
                        logger.warning(
                            "fetch_log is missing and this role cannot create "
                            "it. Apply migrations/001_support_tables.sql as the "
                            "database owner to enable incremental fetch and "
                            "run history."
                        )
                        return False

                    cur.execute(self._SUPPORT_DDL)
            logger.info("Created support table: fetch_log")
            return True
        except psycopg2.Error as exc:
            logger.warning("Could not ensure fetch_log table: %s", exc)
            return False

    # ── Fetch log (scheduler) ──────────────────────────────────────────
    def get_last_successful_fetch(self):
        """Return the ``started_at`` of the most recent successful fetch run.

        Returns:
            A ``datetime`` for the last successful run, or ``None`` if no run
            has succeeded yet.

        Raises:
            RepositoryError: If the query fails.
        """
        sql = """
            SELECT started_at FROM fetch_log
            WHERE status = 'success'
            ORDER BY started_at DESC
            LIMIT 1
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    row = cur.fetchone()
            return row[0] if row else None
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to read last fetch run: {exc}"
            ) from exc

    def start_fetch_run(self, started_at) -> int:
        """Record the start of a pipeline run.

        Args:
            started_at: When the run began.

        Returns:
            The new ``fetch_log`` row id, to be passed to
            :meth:`finish_fetch_run`.

        Raises:
            RepositoryError: If the insert fails.
        """
        sql = """
            INSERT INTO fetch_log (started_at, status)
            VALUES (%s, 'running')
            RETURNING id
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (started_at,))
                    return int(cur.fetchone()[0])
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to record fetch-run start: {exc}"
            ) from exc

    def finish_fetch_run(
        self, run_id: int, finished_at, papers_saved: int, status: str
    ) -> None:
        """Record the outcome of a pipeline run.

        Args:
            run_id: Id returned by :meth:`start_fetch_run`.
            finished_at: When the run ended.
            papers_saved: Number of new papers the fetch stage saved.
            status: ``"success"`` or ``"failed"``.

        Raises:
            RepositoryError: If the update fails.
        """
        sql = """
            UPDATE fetch_log
            SET finished_at = %s, papers_saved = %s, status = %s
            WHERE id = %s
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql, (finished_at, papers_saved, status, run_id)
                    )
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to record fetch-run finish: {exc}"
            ) from exc

    def recent_fetch_runs(self, limit: int = 20) -> list[dict]:
        """Return recent pipeline runs, newest first.

        Args:
            limit: Maximum rows to return.

        Returns:
            A list of dict rows with keys: ``id``, ``started_at``,
            ``finished_at``, ``papers_saved``, ``status``.

        Raises:
            RepositoryError: If the query fails.
        """
        sql = """
            SELECT id, started_at, finished_at, papers_saved, status
            FROM fetch_log
            ORDER BY started_at DESC
            LIMIT %s
        """
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (limit,))
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except psycopg2.Error as exc:
            raise RepositoryError(
                f"Failed to list fetch runs: {exc}"
            ) from exc
