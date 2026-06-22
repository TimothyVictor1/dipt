"""Categorisation agent: assigns SE categories to papers via a local LLM.

The agent loads the active category list from the database at runtime (so
operator changes take effect immediately), prompts the categorisation model for
an XML-structured answer, validates every returned category against the known
list, and persists the assignments.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dipt.agents.llm_client import OllamaClient
from dipt.database.repository import PaperRepository
from dipt.exceptions import LLMError, RepositoryError
from dipt.models.schemas import Category, CategoryAssignment, PaperStatus

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS: Final[int] = 3000
_MAX_CATEGORIES: Final[int] = 3
_MIN_CONFIDENCE: Final[float] = 0.5
_REQUEST_DELAY_SECONDS: Final[float] = 2.0

_CATEGORY_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"<category>(.*?)</category>", re.DOTALL
)
_NAME_RE: Final[re.Pattern[str]] = re.compile(r"<name>(.*?)</name>", re.DOTALL)
_CONFIDENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"<confidence>(.*?)</confidence>", re.DOTALL
)

_PROMPT_TEMPLATE = """You are an expert Software Engineering researcher and librarian.

Assign the most relevant categories to the following Software Engineering paper.

AVAILABLE CATEGORIES:
{category_list}

PAPER:
{content}

INSTRUCTIONS:
- Assign between 1 and {max_categories} categories that best describe this paper.
- Only use categories from the list above; use the EXACT category name.
- Assign a confidence score between 0.0 and 1.0 for each category.
- Only include a category if its confidence is at least {min_confidence}.
- Be precise: assign a category only if the paper genuinely contributes to it.

Respond ONLY with XML in this exact format, nothing else:

<categories>
  <category>
    <name>EXACT CATEGORY NAME</name>
    <confidence>0.95</confidence>
  </category>
</categories>"""


@dataclass
class CategorisationSummary:
    """Outcome counts for a categorisation run.

    Attributes:
        categorised: Papers that received at least one valid category.
        failed: Papers for which no valid category could be extracted.
        total: Total papers processed.
    """

    categorised: int = 0
    failed: int = 0
    total: int = 0


class CategorisationAgent:
    """Assigns categories to parsed papers using a local LLM.

    Args:
        client: Configured :class:`OllamaClient`.
        repository: Persistence layer.
        model: Model tag used for categorisation.
    """

    def __init__(
        self,
        client: OllamaClient,
        repository: PaperRepository,
        model: str,
    ) -> None:
        self._client = client
        self._repo = repository
        self._model = model

    def run(self, limit: int | None = None) -> CategorisationSummary:
        """Categorise all papers in ``parsed`` or ``no_pdf`` state.

        Args:
            limit: Optional cap on the number of papers to process.

        Returns:
            A :class:`CategorisationSummary` for the run.
        """
        categories = self._repo.load_active_categories()
        category_lookup = {cat.name.lower(): cat for cat in categories}
        category_list_text = self._format_category_list(categories)
        logger.info("Loaded %d active categories", len(categories))

        papers = self._repo.fetch_papers_by_status(
            [PaperStatus.PARSED, PaperStatus.NO_PDF], limit=limit
        )
        summary = CategorisationSummary(total=len(papers))
        logger.info("Categorisation agent: %d papers to process", summary.total)

        for index, paper in enumerate(papers, start=1):
            paper_id = paper["id"]
            logger.info("[%d/%d] Categorising paper %d", index, summary.total, paper_id)

            content = self._prepare_content(paper)
            assignments = self._categorise(content, category_lookup, category_list_text)

            if assignments:
                self._persist(paper_id, assignments)
                summary.categorised += 1
                for assignment in assignments:
                    logger.info(
                        "  -> %s (%.2f)",
                        assignment.category_name,
                        assignment.confidence,
                    )
            else:
                summary.failed += 1
                logger.warning("No valid categories for paper %d", paper_id)

            self._safe_update(paper_id, PaperStatus.CATEGORISED)
            time.sleep(_REQUEST_DELAY_SECONDS)

        logger.info(
            "Categorisation complete: categorised=%d, failed=%d",
            summary.categorised,
            summary.failed,
        )
        return summary

    @staticmethod
    def _format_category_list(categories: list[Category]) -> str:
        """Render the category list for inclusion in the prompt.

        Args:
            categories: Active categories.

        Returns:
            A newline-delimited, numbered category list.
        """
        lines = []
        for position, category in enumerate(categories, start=1):
            line = f"{position}. {category.name}"
            if category.description:
                line += f" — {category.description}"
            lines.append(line)
        return "\n".join(lines)

    def _prepare_content(self, paper: dict) -> str:
        """Build the paper content block for the prompt.

        Prefers the extracted full text (truncated) and falls back to the
        abstract, then to the title alone.

        Args:
            paper: A paper row dict.

        Returns:
            The content string for the prompt.
        """
        title = paper["title"]
        content = f"Title: {title}\n\n"

        text_path = paper.get("full_text_path")
        if paper["status"] == PaperStatus.PARSED.value and text_path:
            path = Path(text_path)
            if path.exists():
                try:
                    full_text = path.read_text(encoding="utf-8")
                    return content + f"Full text (excerpt):\n{full_text[:_MAX_TEXT_CHARS]}"
                except OSError:
                    logger.warning("Could not read text file %s", text_path)

        abstract = paper.get("abstract") or ""
        if abstract:
            return content + f"Abstract:\n{abstract}"
        return content

    def _categorise(
        self,
        content: str,
        category_lookup: dict[str, Category],
        category_list_text: str,
    ) -> list[CategoryAssignment]:
        """Call the LLM and parse its response into assignments.

        Args:
            content: The paper content block.
            category_lookup: Lower-cased name to :class:`Category` mapping.
            category_list_text: The rendered category list for the prompt.

        Returns:
            A validated, de-duplicated list of assignments (possibly empty).
        """
        prompt = _PROMPT_TEMPLATE.format(
            category_list=category_list_text,
            content=content,
            max_categories=_MAX_CATEGORIES,
            min_confidence=_MIN_CONFIDENCE,
        )

        try:
            response = self._client.chat(self._model, prompt)
        except LLMError:
            logger.exception("LLM categorisation call failed")
            return []

        return self._parse_response(response, category_lookup)

    def _parse_response(
        self, response: str, category_lookup: dict[str, Category]
    ) -> list[CategoryAssignment]:
        """Parse XML category blocks, validating names against known categories.

        Args:
            response: Raw LLM response text.
            category_lookup: Lower-cased name to :class:`Category` mapping.

        Returns:
            A validated, de-duplicated list of assignments.
        """
        assignments: dict[int, CategoryAssignment] = {}

        for block in _CATEGORY_BLOCK_RE.findall(response):
            name_match = _NAME_RE.search(block)
            if not name_match:
                continue
            name = name_match.group(1).strip()

            confidence = self._parse_confidence(block)
            if confidence < _MIN_CONFIDENCE:
                continue

            resolved = self._resolve_category(name, category_lookup)
            if resolved is None:
                logger.warning("LLM returned unknown category '%s'", name)
                continue

            category, adjusted_confidence = resolved
            effective = min(confidence * adjusted_confidence, 1.0)

            existing = assignments.get(category.id)
            if existing is None or effective > existing.confidence:
                assignments[category.id] = CategoryAssignment(
                    category_id=category.id,
                    category_name=category.name,
                    confidence=effective,
                )

        ranked = sorted(
            assignments.values(), key=lambda a: a.confidence, reverse=True
        )
        return ranked[:_MAX_CATEGORIES]

    @staticmethod
    def _parse_confidence(block: str) -> float:
        """Extract a confidence value from a category block.

        Args:
            block: The inner text of a ``<category>`` element.

        Returns:
            The parsed confidence, or ``0.5`` if absent or unparseable.
        """
        match = _CONFIDENCE_RE.search(block)
        if not match:
            return 0.5
        try:
            return float(match.group(1).strip())
        except ValueError:
            return 0.5

    @staticmethod
    def _resolve_category(
        name: str, category_lookup: dict[str, Category]
    ) -> tuple[Category, float] | None:
        """Resolve an LLM-returned name to a known category.

        Falls back to a partial (substring) match, slightly discounting the
        confidence to reflect the lower certainty of a fuzzy match.

        Args:
            name: The category name returned by the LLM.
            category_lookup: Lower-cased name to :class:`Category` mapping.

        Returns:
            A ``(category, confidence_multiplier)`` tuple, or ``None`` if the
            name cannot be resolved.
        """
        lowered = name.lower()
        if lowered in category_lookup:
            return category_lookup[lowered], 1.0

        for key, category in category_lookup.items():
            if lowered in key or key in lowered:
                return category, 0.9
        return None

    def _persist(
        self, paper_id: int, assignments: list[CategoryAssignment]
    ) -> None:
        """Persist assignments, logging any repository failure.

        Args:
            paper_id: Paper primary key.
            assignments: Validated assignments to persist.
        """
        try:
            self._repo.save_category_assignments(paper_id, assignments)
        except RepositoryError:
            logger.exception("Failed to save categories for paper %d", paper_id)

    def _safe_update(self, paper_id: int, status: PaperStatus) -> None:
        """Update paper status, logging any repository failure.

        Args:
            paper_id: Paper primary key.
            status: New status.
        """
        try:
            self._repo.update_status(paper_id, status)
        except RepositoryError:
            logger.exception("Failed to update status for paper %d", paper_id)
