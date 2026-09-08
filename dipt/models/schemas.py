"""Domain models for the DIPT system.

These Pydantic models form the typed boundary between external data (API
responses, database rows) and internal logic. Validating at this boundary
means the rest of the codebase can rely on well-formed, typed objects.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class PaperStatus(str, Enum):
    """Lifecycle status of a paper as it moves through the pipeline."""

    FETCHED = "fetched"
    PARSED = "parsed"
    NO_PDF = "no_pdf"
    NO_TEXT = "no_text"
    CATEGORISED = "categorised"
    SUMMARISED = "summarised"
    SCORED = "scored"
    APPROVED = "approved"
    REJECTED = "rejected"


class PaperSource(str, Enum):
    """Supported upstream paper sources."""

    OPENALEX = "openalex"
    ARXIV = "arxiv"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    DBLP = "dblp"
    CROSSREF = "crossref"
    ZENODO = "zenodo"
    CORE = "core"


class PaperRecord(BaseModel):
    """A normalised paper as produced by any source fetcher.

    All sources convert their raw responses into this common shape before the
    paper enters the pipeline, decoupling downstream logic from source quirks.

    Attributes:
        title: Paper title (must be non-empty after stripping).
        abstract: Abstract text; may be empty for sources without abstracts.
        authors: Ordered list of author display names.
        doi: Normalised DOI without URL prefix, or ``None``.
        source: Which upstream source produced this record.
        source_id: Stable unique identifier within the source.
        pdf_url: Direct PDF URL if available, else empty string.
        published_date: Publication date if known.
    """

    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    source: PaperSource
    source_id: str
    pdf_url: str = ""
    published_date: date | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, title: str) -> str:
        """Reject blank titles early."""
        stripped = title.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped

    @field_validator("source_id")
    @classmethod
    def _source_id_not_blank(cls, source_id: str) -> str:
        """Reject blank source identifiers early."""
        stripped = source_id.strip()
        if not stripped:
            raise ValueError("source_id must not be blank")
        return stripped


class Category(BaseModel):
    """A single SE category as stored in the database.

    Attributes:
        id: Primary key.
        name: Unique category name.
        description: Human-readable description used in LLM prompts.
    """

    id: int
    name: str
    description: str = ""


class CategoryAssignment(BaseModel):
    """A category assigned to a paper with a confidence score.

    Attributes:
        category_id: Foreign key into the categories table.
        category_name: Resolved category name (for logging / display).
        confidence: Confidence in the assignment, in the range [0, 1].
    """

    category_id: int
    category_name: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class PaperSummary(BaseModel):
    """A structured, four-part summary of a paper.

    The summary is broken into the sections an industrial reader cares about,
    so downstream stages (scoring, the dashboard) can rely on a consistent
    shape. The :meth:`to_text` rendering is what gets persisted to the
    ``summaries`` table.

    Attributes:
        research_problem: The problem the paper sets out to address.
        methodology: How the work was carried out.
        key_findings: The main results and contributions.
        industrial_implications: Why a practitioner should care.
    """

    research_problem: str = ""
    methodology: str = ""
    key_findings: str = ""
    industrial_implications: str = ""

    def is_empty(self) -> bool:
        """Return whether every section is blank.

        Returns:
            ``True`` if no section contains any text.
        """
        return not any(
            section.strip()
            for section in (
                self.research_problem,
                self.methodology,
                self.key_findings,
                self.industrial_implications,
            )
        )

    def to_text(self) -> str:
        """Render the summary to the plain-text form stored in the database.

        Returns:
            A headed, human-readable summary string.
        """
        return (
            f"Research Problem:\n{self.research_problem.strip()}\n\n"
            f"Methodology:\n{self.methodology.strip()}\n\n"
            f"Key Findings:\n{self.key_findings.strip()}\n\n"
            f"Industrial Implications:\n{self.industrial_implications.strip()}"
        )


class PaperScore(BaseModel):
    """An industrial-relevance score for a paper.

    Attributes:
        score: Relevance score on a 1 to 10 scale.
        rationale: Written justification for the score.
    """

    score: float = Field(..., ge=1.0, le=10.0)
    rationale: str = ""


class QAReview(BaseModel):
    """The outcome of a quality-assurance check on a paper.

    Attributes:
        passed: Whether the summary and score passed the consistency check.
        target: Which output to regenerate when not passed: ``"summary"``,
            ``"score"``, or ``"none"``.
        reason: Human-readable explanation of the verdict.
    """

    passed: bool
    target: str = "none"
    reason: str = ""
