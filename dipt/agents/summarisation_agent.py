"""Summarisation agent: produces a structured summary of each paper.

The agent selects papers that have been categorised, reads their full text (or
falls back to the abstract), and prompts a local model for a four-part summary:
research problem, methodology, key findings, and industrial implications. The
model is asked for a JSON object and Ollama's JSON mode constrains the output,
which is far more robust on local models than parsing free-form or XML text.
The rendered summary is persisted and the paper advances to ``summarised``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dipt.agents.llm_client import OllamaClient
from dipt.database.repository import PaperRepository
from dipt.exceptions import LLMError, RepositoryError
from dipt.models.schemas import PaperStatus, PaperSummary

logger = logging.getLogger(__name__)

# Local 30B-70B models on constrained hardware degrade into repetition loops
# when handed a very long prompt, so the excerpt is deliberately modest.
_MAX_TEXT_CHARS: Final[int] = 9000
_REQUEST_DELAY_SECONDS: Final[float] = 2.0
# Four sections of two to three sentences need roughly 350 to 500 tokens; the
# cap is headroom plus a guard against a model that never stops.
_MAX_OUTPUT_TOKENS: Final[int] = 800

_SECTION_NAMES: Final[tuple[str, ...]] = (
    "research_problem",
    "methodology",
    "key_findings",
    "industrial_implications",
)

_PROMPT_TEMPLATE = """You are an expert Software Engineering research analyst.

Summarise the following Software Engineering paper for a busy industry reader.

PAPER:
{content}

Return ONLY a JSON object with exactly these four string keys, in this order:
- "research_problem": the gap or question the paper addresses
- "methodology": how the work was carried out
- "key_findings": the main results and contributions
- "industrial_implications": why a practitioner should care

Each value must be two to three factual sentences. Do not invent results that
are not in the text. Return the JSON object and nothing else."""


@dataclass
class SummarisationSummary:
    """Outcome counts for a summarisation run.

    Attributes:
        summarised: Papers that received a usable summary.
        failed: Papers for which no usable summary could be produced.
        total: Total papers processed.
    """

    summarised: int = 0
    failed: int = 0
    total: int = 0


class SummarisationAgent:
    """Generates structured summaries for categorised papers.

    Args:
        client: Configured :class:`OllamaClient`.
        repository: Persistence layer.
        model: Model tag used for summarisation.
    """

    def __init__(
        self,
        client: OllamaClient,
        repository: PaperRepository,
        model: str,
        prompt_template: str | None = None,
    ) -> None:
        self._client = client
        self._repo = repository
        self._model = model
        self._prompt_template = prompt_template or _PROMPT_TEMPLATE

    def run(self, limit: int | None = None) -> SummarisationSummary:
        """Summarise all papers currently in the ``categorised`` state.

        Args:
            limit: Optional cap on the number of papers to process.

        Returns:
            A :class:`SummarisationSummary` for the run.
        """
        papers = self._repo.fetch_papers_by_status(
            [PaperStatus.CATEGORISED], limit=limit
        )
        summary = SummarisationSummary(total=len(papers))
        logger.info("Summarisation agent: %d papers to process", summary.total)

        for index, paper in enumerate(papers, start=1):
            paper_id = paper["id"]
            logger.info(
                "[%d/%d] Summarising paper %d", index, summary.total, paper_id
            )

            if self.summarise_paper(paper) is not None:
                self._safe_update(paper_id, PaperStatus.SUMMARISED)
                summary.summarised += 1
            else:
                summary.failed += 1
                logger.warning("No usable summary for paper %d", paper_id)

            time.sleep(_REQUEST_DELAY_SECONDS)

        logger.info(
            "Summarisation complete: summarised=%d, failed=%d",
            summary.summarised,
            summary.failed,
        )
        return summary

    def summarise_paper(self, paper: dict) -> PaperSummary | None:
        """Generate and persist a single paper's summary, without advancing it.

        This is the unit of work shared by :meth:`run`, the streaming pipeline,
        and the QA agent's auto-fix path. The caller is responsible for any
        status transition.

        Args:
            paper: A paper row dict.

        Returns:
            The persisted :class:`PaperSummary`, or ``None`` if no usable
            summary could be generated or saved.
        """
        result = self._generate(paper)
        if result is None:
            return None

        try:
            self._repo.save_summary(paper["id"], result.to_text(), self._model)
        except RepositoryError:
            logger.exception("Failed to save summary for paper %d", paper["id"])
            return None
        return result

    def _generate(self, paper: dict) -> PaperSummary | None:
        """Call the LLM and parse its response into a summary.

        Args:
            paper: A paper row dict.

        Returns:
            A populated :class:`PaperSummary`, or ``None`` on failure or an
            empty response.
        """
        content = self._prepare_content(paper)
        prompt = self._prompt_template.format(content=content)

        try:
            response = self._client.chat(
                self._model,
                prompt,
                max_tokens=_MAX_OUTPUT_TOKENS,
                json_mode=True,
            )
        except LLMError:
            logger.exception("LLM summarisation call failed")
            return None

        return self._parse_response(response)

    def _prepare_content(self, paper: dict) -> str:
        """Build the paper content block for the prompt.

        Prefers the extracted full text (truncated) and falls back to the
        abstract, then to the title alone. The full text is used whenever a
        ``full_text_path`` is recorded, regardless of the paper's current
        status: by the time a paper reaches this agent it has advanced to
        ``categorised``, so gating on ``parsed`` would never read the text.

        Args:
            paper: A paper row dict.

        Returns:
            The content string for the prompt.
        """
        title = paper["title"]
        content = f"Title: {title}\n\n"

        text_path = paper.get("full_text_path")
        if text_path:
            path = Path(text_path)
            if path.exists():
                try:
                    full_text = path.read_text(encoding="utf-8")
                    return content + (
                        f"Full text (excerpt):\n{full_text[:_MAX_TEXT_CHARS]}"
                    )
                except OSError:
                    logger.warning("Could not read text file %s", text_path)

        abstract = paper.get("abstract") or ""
        if abstract:
            return content + f"Abstract:\n{abstract}"
        return content

    @staticmethod
    def _parse_response(response: str) -> PaperSummary | None:
        """Parse the four summary sections from the model's JSON response.

        The response is expected to be a JSON object (Ollama JSON mode). Parsing
        is lenient: unknown keys are ignored, missing keys become empty strings,
        and non-string values are coerced with ``str``. A leading code fence, if
        the model adds one despite JSON mode, is stripped first.

        Args:
            response: Raw LLM response text.

        Returns:
            A :class:`PaperSummary`, or ``None`` if the response is not an
            object or every section is empty.
        """
        text = response.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Summarisation response was not valid JSON")
            return None
        if not isinstance(data, dict):
            return None

        sections = {
            name: str(data.get(name, "") or "").strip()
            for name in _SECTION_NAMES
        }
        result = PaperSummary(**sections)
        if result.is_empty():
            return None
        return result

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
