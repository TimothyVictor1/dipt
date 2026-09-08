"""QA agent: consistency-checks each paper, auto-fixing before escalating.

The agent selects papers that have been scored and asks a local model to check
the stored summary and score for internal consistency and obvious errors. Its
behaviour is deliberately two-stage, a specific requirement from the project
owner:

1. Auto-fix first. If a problem is found, regenerate the affected output (the
   summary or the score) once and re-check.
2. Escalate second. Only if the regenerated output still fails does the agent
   write a row to ``qa_flags`` for human review.

Papers that pass (immediately or after an auto-fix) advance to ``approved``.
Papers that are flagged stay at ``scored`` and are excluded from later runs
until a human resolves the flag.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Final

from dipt.agents.llm_client import OllamaClient
from dipt.agents.scoring_agent import ScoringAgent
from dipt.agents.summarisation_agent import SummarisationAgent
from dipt.database.repository import PaperRepository
from dipt.exceptions import LLMError, RepositoryError
from dipt.models.schemas import PaperStatus, QAReview

logger = logging.getLogger(__name__)

_REQUEST_DELAY_SECONDS: Final[float] = 2.0
_MAX_OUTPUT_TOKENS: Final[int] = 600
_VALID_TARGETS: Final[frozenset[str]] = frozenset({"summary", "score", "none"})


def _as_bool(value: object) -> bool:
    """Coerce a JSON-ish truthiness value to ``bool``.

    Handles real booleans, ``"true"`` / ``"yes"`` / ``"y"`` strings (any case),
    and numbers, so a model that stringifies the verdict is still understood.

    Args:
        value: The raw value from the parsed JSON.

    Returns:
        The boolean interpretation.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"true", "yes", "y", "pass", "passed"}


_PROMPT_TEMPLATE = """You are a meticulous research quality reviewer.

Check the stored outputs for one Software Engineering paper. You are looking for
clear defects, not stylistic preferences.

PAPER TITLE:
{title}

STORED SUMMARY:
{summary}

STORED SCORE: {score}
STORED RATIONALE:
{rationale}

FAIL the review if any of the following is true:
- The summary is empty, truncated mid-sentence, or missing whole sections.
- The summary contradicts itself or is clearly not about the title's topic.
- The score is outside the 1 to 10 range, or missing.
- The rationale is empty, or plainly inconsistent with the score (for example a
  score of 9 with a rationale that describes no practical value).

Return ONLY a JSON object with these keys:
- "passed": true if the review passes, false if it fails
- "target": when it fails, the single output to regenerate, "summary" or
  "score"; when it passes, "none"
- "reason": one sentence explaining the verdict"""


@dataclass
class QASummary:
    """Outcome counts for a QA run.

    Attributes:
        passed: Papers that passed the check on the first attempt.
        auto_fixed: Papers that failed, were regenerated, and then passed.
        flagged: Papers that still failed after an auto-fix and were escalated.
        total: Total papers processed.
    """

    passed: int = 0
    auto_fixed: int = 0
    flagged: int = 0
    total: int = 0


class QAAgent:
    """Consistency-checks scored papers, auto-fixing before escalating.

    Args:
        client: Configured :class:`OllamaClient` for the review model.
        repository: Persistence layer.
        model: Model tag used for the review check.
        summariser: Agent used to regenerate a summary during auto-fix.
        scorer: Agent used to regenerate a score during auto-fix.
    """

    def __init__(
        self,
        client: OllamaClient,
        repository: PaperRepository,
        model: str,
        summariser: SummarisationAgent,
        scorer: ScoringAgent,
        prompt_template: str | None = None,
    ) -> None:
        self._client = client
        self._repo = repository
        self._model = model
        self._summariser = summariser
        self._scorer = scorer
        self._prompt_template = prompt_template or _PROMPT_TEMPLATE

    def run(self, limit: int | None = None) -> QASummary:
        """QA-check all scored papers that have no outstanding flag.

        Args:
            limit: Optional cap on the number of papers to process.

        Returns:
            A :class:`QASummary` for the run.
        """
        papers = self._repo.fetch_papers_for_qa(limit=limit)
        summary = QASummary(total=len(papers))
        logger.info("QA agent: %d papers to process", summary.total)

        for index, paper in enumerate(papers, start=1):
            paper_id = paper["id"]
            logger.info(
                "[%d/%d] QA-checking paper %d", index, summary.total, paper_id
            )

            outcome = self.check_paper(paper)
            setattr(summary, outcome, getattr(summary, outcome) + 1)
            time.sleep(_REQUEST_DELAY_SECONDS)

        logger.info(
            "QA complete: passed=%d, auto_fixed=%d, flagged=%d",
            summary.passed,
            summary.auto_fixed,
            summary.flagged,
        )
        return summary

    def check_paper(self, paper: dict) -> str:
        """Review one paper, auto-fixing once before escalating.

        Args:
            paper: A paper row dict.

        Returns:
            The :class:`QASummary` attribute to increment: ``"passed"``,
            ``"auto_fixed"``, or ``"flagged"``.
        """
        paper_id = paper["id"]

        review = self._review(paper)
        if review is None:
            self._flag(
                paper_id,
                "review_error",
                "The QA review model did not return a usable verdict.",
            )
            return "flagged"

        if review.passed:
            self._safe_update(paper_id, PaperStatus.APPROVED)
            return "passed"

        logger.info(
            "Paper %d failed QA (target=%s): %s",
            paper_id,
            review.target,
            review.reason,
        )

        if not self._auto_fix(paper, review.target):
            self._flag(
                paper_id,
                f"{review.target}_unfixable",
                f"Auto-fix of the {review.target} did not resolve: {review.reason}",
            )
            return "flagged"

        recheck = self._review(paper)
        if recheck is not None and recheck.passed:
            logger.info("Paper %d passed QA after auto-fixing its %s",
                        paper_id, review.target)
            self._safe_update(paper_id, PaperStatus.APPROVED)
            return "auto_fixed"

        reason = recheck.reason if recheck is not None else "recheck returned nothing"
        self._flag(
            paper_id,
            f"{review.target}_still_failing",
            f"Regenerated the {review.target} but QA still failed: {reason}",
        )
        return "flagged"

    def _auto_fix(self, paper: dict, target: str) -> bool:
        """Regenerate the affected output once.

        Args:
            paper: A paper row dict.
            target: Which output to regenerate: ``"summary"`` or ``"score"``.

        Returns:
            ``True`` if regeneration produced and persisted a new output,
            ``False`` otherwise (including an unknown target).
        """
        paper_id = paper["id"]
        if target == "summary":
            logger.info("Auto-fix: regenerating summary for paper %d", paper_id)
            return self._summariser.summarise_paper(paper) is not None
        if target == "score":
            logger.info("Auto-fix: regenerating score for paper %d", paper_id)
            return self._scorer.score_paper(paper) is not None

        logger.warning(
            "Paper %d failed QA but target '%s' is not fixable", paper_id, target
        )
        return False

    def _review(self, paper: dict) -> QAReview | None:
        """Ask the review model to check the stored summary and score.

        Args:
            paper: A paper row dict.

        Returns:
            A :class:`QAReview`, or ``None`` if the check could not be run or
            parsed.
        """
        paper_id = paper["id"]
        summary_text = self._safe_get_summary(paper_id)
        score = self._safe_get_score(paper_id)

        if not summary_text or score is None:
            logger.warning(
                "Paper %d is missing a summary or score; cannot QA-check",
                paper_id,
            )
            return QAReview(
                passed=False,
                target="summary" if not summary_text else "score",
                reason="Stored summary or score is missing.",
            )

        prompt = self._prompt_template.format(
            title=paper["title"],
            summary=summary_text,
            score=score["relevance_score"],
            rationale=score["score_rationale"] or "(none)",
        )

        try:
            response = self._client.chat(
                self._model,
                prompt,
                max_tokens=_MAX_OUTPUT_TOKENS,
                json_mode=True,
            )
        except LLMError:
            logger.exception("LLM QA review call failed")
            return None

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: str) -> QAReview | None:
        """Parse a QA verdict from the review model's JSON response.

        Args:
            response: Raw LLM response text (expected to be a JSON object).

        Returns:
            A :class:`QAReview`, or ``None`` if no verdict can be extracted.
        """
        text = response.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("QA review response was not valid JSON")
            return None
        if not isinstance(data, dict) or "passed" not in data:
            return None

        passed = _as_bool(data["passed"])
        target = str(data.get("target", "none")).strip().lower()
        if target not in _VALID_TARGETS:
            target = "none"
        reason = str(data.get("reason", "") or "").strip()

        if passed:
            target = "none"
        elif target == "none":
            # A failure with no actionable target defaults to re-summarising.
            target = "summary"

        return QAReview(passed=passed, target=target, reason=reason)

    def _safe_get_summary(self, paper_id: int) -> str | None:
        """Load the stored summary text, tolerating repository failure."""
        try:
            return self._repo.get_summary(paper_id)
        except RepositoryError:
            logger.exception("Failed to load summary for paper %d", paper_id)
            return None

    def _safe_get_score(self, paper_id: int) -> dict | None:
        """Load the stored score, tolerating repository failure."""
        try:
            return self._repo.get_score(paper_id)
        except RepositoryError:
            logger.exception("Failed to load score for paper %d", paper_id)
            return None

    def _flag(self, paper_id: int, flag_type: str, description: str) -> None:
        """Record a QA flag for human review, tolerating repository failure.

        Args:
            paper_id: Paper primary key.
            flag_type: Short machine-readable category of the problem.
            description: Human-readable explanation.
        """
        try:
            self._repo.save_qa_flag(paper_id, flag_type, description)
            logger.warning(
                "Paper %d escalated to human review: %s", paper_id, flag_type
            )
        except RepositoryError:
            logger.exception("Failed to save QA flag for paper %d", paper_id)

    def _safe_update(self, paper_id: int, status: PaperStatus) -> None:
        """Update paper status, logging any repository failure."""
        try:
            self._repo.update_status(paper_id, status)
        except RepositoryError:
            logger.exception("Failed to update status for paper %d", paper_id)
