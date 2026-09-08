"""Share-post agent: drafts a short, human first-person post for each approved
paper so the professor can send it to LinkedIn / X from the public site.

The agent reads a paper's stored four-part summary and score and asks a local
model for a two-to-four sentence post written in the professor's own voice -
plain, curious, no marketing hype, at most two tasteful hashtags, and no link
(the website adds the link when he clicks Share). The draft is cached by
:mod:`dipt.share_store` and picked up by the site exporter.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Final

from dipt import share_store
from dipt.agents.llm_client import OllamaClient
from dipt.database.repository import PaperRepository
from dipt.exceptions import LLMError, RepositoryError

logger = logging.getLogger(__name__)

_REQUEST_DELAY_SECONDS: Final[float] = 1.5
_MAX_OUTPUT_TOKENS: Final[int] = 260
_TEMPERATURE: Final[float] = 0.6
_MIN_USABLE_CHARS: Final[int] = 40

_PROMPT_TEMPLATE = """You are helping Professor Tony Gorschek, a Software
Engineering researcher, share a paper he found interesting.

Write the post as HIM, in the first person ("I came across...", "What I found
useful...", "This one caught my eye..."). It will go on LinkedIn and X.

PAPER TITLE:
{title}

WHAT IT IS ABOUT (from an automated summary):
Research problem: {research_problem}
Key findings: {key_findings}
Why it matters in practice: {industrial_implications}

Industrial-relevance score given by our pipeline: {score} out of 10
Topic areas: {categories}

WRITE THE POST:
- Two to four sentences. Warm, plain, genuinely curious - the way a person
  writes, not a press release. No "excited to share", no emoji, no buzzwords.
- Say briefly what the paper looks at and why a practitioner might care.
- Do not invent results. Stay within what is written above.
- Do NOT include any link or URL.
- You may end with at most two lowercase-style hashtags (for example
  #SoftwareEngineering) only if they fit naturally. Zero is fine.

Return only the post text, nothing else."""


@dataclass
class ShareSummary:
    """Outcome counts for a share-post run.

    Attributes:
        generated: Papers that received a usable post.
        failed: Papers for which no usable post could be produced.
        skipped: Papers that already had a post.
        total: Approved papers considered.
    """

    generated: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0


class SharePostAgent:
    """Drafts a shareable post for each approved paper.

    Args:
        client: Configured :class:`OllamaClient`.
        repository: Persistence layer.
        model: Model tag used to write the post.
        prompt_template: Optional override for the module default.
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

    def run(self, limit: int | None = None, *, force: bool = False) -> ShareSummary:
        """Draft posts for approved papers that do not have one yet.

        Args:
            limit: Optional cap on how many papers to draft this run.
            force: When ``True``, redraft even papers that already have a post.

        Returns:
            A :class:`ShareSummary` for the run.
        """
        try:
            papers = self._repo.list_approved_papers()
        except RepositoryError:
            logger.exception("Share agent: could not load approved papers")
            return ShareSummary()

        summary = ShareSummary(total=len(papers))
        todo = [
            p for p in papers
            if force or not share_store.has(p["id"])
        ]
        summary.skipped = len(papers) - len(todo)
        if limit is not None:
            todo = todo[:limit]
        logger.info(
            "Share agent: %d approved paper(s), %d already have a post, "
            "drafting %d",
            summary.total,
            summary.skipped,
            len(todo),
        )

        for index, paper in enumerate(todo, start=1):
            paper_id = paper["id"]
            logger.info(
                "[%d/%d] Drafting share post for paper %d",
                index,
                len(todo),
                paper_id,
            )
            if self.generate_for(paper) is not None:
                summary.generated += 1
            else:
                summary.failed += 1
                logger.warning("No usable share post for paper %d", paper_id)
            time.sleep(_REQUEST_DELAY_SECONDS)

        logger.info(
            "Share posts complete: generated=%d, failed=%d, skipped=%d",
            summary.generated,
            summary.failed,
            summary.skipped,
        )
        return summary

    def generate_for(self, paper: dict) -> str | None:
        """Draft and store one paper's share post.

        Args:
            paper: A row from :meth:`PaperRepository.list_approved_papers`.

        Returns:
            The stored post text, or ``None`` if none could be produced.
        """
        summary = paper.get("summary") or ""
        sections = _split_summary(summary)
        prompt = self._prompt_template.format(
            title=paper["title"],
            research_problem=sections["research_problem"] or "(not available)",
            key_findings=sections["key_findings"] or "(not available)",
            industrial_implications=(
                sections["industrial_implications"] or "(not available)"
            ),
            score=(
                f"{paper['relevance_score']:.1f}"
                if paper.get("relevance_score") is not None
                else "not scored"
            ),
            categories=", ".join(paper.get("categories") or []) or "software engineering",
        )

        try:
            text = self._client.chat(
                self._model,
                prompt,
                temperature=_TEMPERATURE,
                max_tokens=_MAX_OUTPUT_TOKENS,
            )
        except LLMError:
            logger.exception("LLM share-post call failed")
            return None

        if not text or len(text.strip()) < _MIN_USABLE_CHARS:
            return None

        try:
            share_store.set(paper["id"], text)
        except (ValueError, OSError):
            logger.exception("Could not store share post for paper %d", paper["id"])
            return None
        return share_store.get(paper["id"])


_SUMMARY_HEADERS: Final[tuple[tuple[str, str], ...]] = (
    ("research_problem", "Research Problem:"),
    ("methodology", "Methodology:"),
    ("key_findings", "Key Findings:"),
    ("industrial_implications", "Industrial Implications:"),
)


def _split_summary(summary_text: str) -> dict[str, str]:
    """Parse the stored plain-text summary back into its four sections.

    Args:
        summary_text: The headed text produced by ``PaperSummary.to_text``.

    Returns:
        A dict keyed by section name; missing sections map to an empty string.
    """
    sections = {key: "" for key, _ in _SUMMARY_HEADERS}
    if not summary_text:
        return sections
    positions = [
        (key, summary_text.find(header))
        for key, header in _SUMMARY_HEADERS
        if summary_text.find(header) != -1
    ]
    positions.sort(key=lambda pair: pair[1])
    headers = dict(_SUMMARY_HEADERS)
    for order, (key, start) in enumerate(positions):
        text_start = start + len(headers[key])
        end = (
            positions[order + 1][1]
            if order + 1 < len(positions)
            else len(summary_text)
        )
        sections[key] = summary_text[text_start:end].strip()
    return sections
