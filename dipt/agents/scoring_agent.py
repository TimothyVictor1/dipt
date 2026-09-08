"""Scoring agent: rates each paper's industrial relevance from 1 to 10.

The agent selects papers that have been summarised, presents the structured
summary to a local reasoning model acting as a virtual CTO, and asks for a
single industrial-relevance score together with a short written rationale. The
score is persisted and the paper advances to the ``scored`` state.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Final

from dipt.agents.llm_client import OllamaClient
from dipt.database.repository import PaperRepository
from dipt.exceptions import LLMError, RepositoryError
from dipt.models.schemas import PaperScore, PaperStatus

logger = logging.getLogger(__name__)

_REQUEST_DELAY_SECONDS: Final[float] = 2.0
_MIN_SCORE: Final[float] = 1.0
_MAX_SCORE: Final[float] = 10.0
# A reasoning model spends most of its output in a <think> block before the
# short XML answer, so this cap is generous compared with the other agents.
_MAX_OUTPUT_TOKENS: Final[int] = 2500
# Scoring at near-zero temperature makes the model park on one modal number for
# every paper; a little sampling variety restores discrimination across papers.
_TEMPERATURE: Final[float] = 0.35

_THINK_RE: Final[re.Pattern[str]] = re.compile(
    r"<think>.*?</think>", re.DOTALL | re.IGNORECASE
)
_SCORE_RE: Final[re.Pattern[str]] = re.compile(
    r"<score>\s*([0-9]+(?:\.[0-9]+)?)\s*</score>", re.DOTALL | re.IGNORECASE
)
_RATIONALE_RE: Final[re.Pattern[str]] = re.compile(
    r"<rationale>(.*?)</rationale>", re.DOTALL | re.IGNORECASE
)

_PROMPT_TEMPLATE = """You are the Chief Technology Officer of a large software company.

Your job is to decide whether a Software Engineering research paper is worth
your engineering leadership team's time. Judge only industrial relevance: could
the findings change how real teams build, test, or maintain software in the next
two to three years?

PAPER TITLE:
{title}

STRUCTURED SUMMARY:
{summary}

SCORING GUIDE (1 to 10):
- 1 to 2: Purely theoretical or a niche academic contribution; no realistic
  path to industrial use.
- 3 to 4: A little practical relevance, but narrow, very early stage, or would
  be hard for a team to act on.
- 5 to 6: Solid, usable findings for a specific kind of team, with real
  adoption friction. This is the typical score for a competent applied paper.
- 7 to 8: Clear, actionable value for many teams, adoptable within a year.
- 9 to 10: Exceptional; would plausibly change common industry practice.

INSTRUCTIONS:
- Give one integer or one-decimal score from 1 to 10, and use the WHOLE range
  across papers. Most papers land at 4 to 6; a 7 or above must be earned.
- Do not default to a middle-of-the-road number. Commit to a specific score
  and, in your rationale, say why it is not one point higher and not one point
  lower.
- Give a rationale of three to five sentences written for a technical executive.
  Be specific about who benefits and what they would do differently.

Respond ONLY with XML in this exact format, nothing else:

<assessment>
  <score>5</score>
  <rationale>...</rationale>
</assessment>"""


@dataclass
class ScoringSummary:
    """Outcome counts for a scoring run.

    Attributes:
        scored: Papers that received a usable score.
        failed: Papers for which no usable score could be produced.
        total: Total papers processed.
    """

    scored: int = 0
    failed: int = 0
    total: int = 0


class ScoringAgent:
    """Assigns an industrial-relevance score to summarised papers.

    Args:
        client: Configured :class:`OllamaClient`.
        repository: Persistence layer.
        model: Model tag used for scoring (a reasoning model).
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

    def run(self, limit: int | None = None) -> ScoringSummary:
        """Score all papers currently in the ``summarised`` state.

        Args:
            limit: Optional cap on the number of papers to process.

        Returns:
            A :class:`ScoringSummary` for the run.
        """
        papers = self._repo.fetch_papers_by_status(
            [PaperStatus.SUMMARISED], limit=limit
        )
        summary = ScoringSummary(total=len(papers))
        logger.info("Scoring agent: %d papers to process", summary.total)

        for index, paper in enumerate(papers, start=1):
            paper_id = paper["id"]
            logger.info(
                "[%d/%d] Scoring paper %d", index, summary.total, paper_id
            )

            result = self.score_paper(paper)
            if result is not None:
                self._safe_update(paper_id, PaperStatus.SCORED)
                summary.scored += 1
                logger.info(
                    "  -> score %.1f for paper %d", result.score, paper_id
                )
            else:
                summary.failed += 1
                logger.warning("No usable score for paper %d", paper_id)

            time.sleep(_REQUEST_DELAY_SECONDS)

        logger.info(
            "Scoring complete: scored=%d, failed=%d",
            summary.scored,
            summary.failed,
        )
        return summary

    def score_paper(self, paper: dict) -> PaperScore | None:
        """Generate and persist a single paper's score, without advancing it.

        This is the unit of work shared by :meth:`run`, the streaming pipeline,
        and the QA agent's auto-fix path. The caller is responsible for any
        status transition.

        Args:
            paper: A paper row dict.

        Returns:
            The persisted :class:`PaperScore`, or ``None`` if no usable score
            could be generated or saved.
        """
        result = self._generate(paper)
        if result is None:
            return None

        try:
            self._repo.save_score(
                paper["id"], result.score, result.rationale, self._model
            )
        except RepositoryError:
            logger.exception("Failed to save score for paper %d", paper["id"])
            return None
        return result

    def _generate(self, paper: dict) -> PaperScore | None:
        """Call the LLM and parse its response into a score.

        Args:
            paper: A paper row dict.

        Returns:
            A populated :class:`PaperScore`, or ``None`` on failure or an
            unparseable response.
        """
        summary_text = self._repo_summary(paper["id"])
        if not summary_text:
            logger.warning(
                "Paper %d has no stored summary; cannot score", paper["id"]
            )
            return None

        prompt = self._prompt_template.format(
            title=paper["title"], summary=summary_text
        )

        try:
            response = self._client.chat(
                self._model,
                prompt,
                temperature=_TEMPERATURE,
                max_tokens=_MAX_OUTPUT_TOKENS,
            )
        except LLMError:
            logger.exception("LLM scoring call failed")
            return None

        return self._parse_response(response)

    def _repo_summary(self, paper_id: int) -> str | None:
        """Load the stored summary text for a paper, tolerating failure.

        Args:
            paper_id: Paper primary key.

        Returns:
            The summary text, or ``None`` if missing or unreadable.
        """
        try:
            return self._repo.get_summary(paper_id)
        except RepositoryError:
            logger.exception("Failed to load summary for paper %d", paper_id)
            return None

    @staticmethod
    def _parse_response(response: str) -> PaperScore | None:
        """Parse a score and rationale from the model response.

        Any ``<think>`` reasoning block emitted by a reasoning model is removed
        before parsing so it cannot be mistaken for the answer.

        Args:
            response: Raw LLM response text.

        Returns:
            A validated :class:`PaperScore`, or ``None`` if no score in range
            [1, 10] can be extracted.
        """
        cleaned = _THINK_RE.sub("", response)

        score_match = _SCORE_RE.search(cleaned)
        if not score_match:
            return None
        try:
            raw_score = float(score_match.group(1))
        except ValueError:
            return None

        clamped = max(_MIN_SCORE, min(_MAX_SCORE, raw_score))

        rationale_match = _RATIONALE_RE.search(cleaned)
        rationale = (
            rationale_match.group(1).strip() if rationale_match else ""
        )

        return PaperScore(score=clamped, rationale=rationale)

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
