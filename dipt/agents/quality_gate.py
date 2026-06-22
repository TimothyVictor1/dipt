"""LLM-based quality gate that decides whether a paper is SE research.

Every fetched paper passes through :meth:`QualityGate.is_software_engineering`
before being persisted. This is the final, source-agnostic guard against
off-topic papers entering the database.
"""

from __future__ import annotations

import logging

from dipt.agents.llm_client import OllamaClient
from dipt.exceptions import LLMError

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """You are a strict Software Engineering research paper classifier.

A paper IS Software Engineering research if it studies:
- How software is built, tested, maintained, or evolved
- Tools, methods, or processes used by software developers
- Requirements engineering, software architecture, DevOps, CI/CD
- Empirical studies of software development practices
- Code quality, technical debt, refactoring, code smells
- Software testing, debugging, program analysis
- LLMs or AI tools used specifically for software development
- Developer productivity, teams, and collaboration in an SE context
- Software product management, agile, lean methods
- Mining software repositories (GitHub, Stack Overflow, etc.)

A paper is NOT Software Engineering research if it:
- Uses software as a tool but studies another domain
  (medicine, finance, agriculture, physics, education)
- Is about general AI or machine learning not applied to SE
- Is about cybersecurity or network security without an SE focus
- Is about IoT, hardware, or embedded systems without an SE focus
- Is about data science, big data, or analytics in other domains
- Is about information systems in business or management
- Is written in a non-English language
- Has an irrelevant or very generic title with no clear SE topic

Be STRICT. When in doubt, answer NO.

{content}

Is this a Software Engineering research paper? Answer only YES or NO."""


class QualityGate:
    """SE-relevance classifier backed by a local LLM.

    Args:
        client: Configured :class:`OllamaClient`.
        model: Model tag to use for classification.
    """

    def __init__(self, client: OllamaClient, model: str) -> None:
        self._client = client
        self._model = model

    def is_software_engineering(self, title: str, abstract: str = "") -> bool:
        """Classify whether a paper is SE research.

        On LLM failure this returns ``False`` (fail-closed): it is safer to
        reject a borderline paper than to admit an off-topic one.

        Args:
            title: Paper title.
            abstract: Paper abstract; may be empty.

        Returns:
            ``True`` if the model classifies the paper as SE, else ``False``.
        """
        if not title.strip():
            return False

        content = f"Title: {title}"
        if abstract.strip():
            content += f"\nAbstract: {abstract[:600]}"

        prompt = _PROMPT_TEMPLATE.format(content=content)

        try:
            answer = self._client.chat(self._model, prompt)
        except LLMError:
            logger.exception(
                "Quality gate LLM failed; rejecting paper '%s' (fail-closed)",
                title[:60],
            )
            return False

        return answer.strip().upper().startswith("YES")
