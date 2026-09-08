"""Demonstrate the QA agent's two required modes on real papers.

Professor Gorschek's requirement: the QA agent must first try to auto-fix a
problem by regenerating the affected output, and only escalate to a
``qa_flags`` row for human review when the auto-fix does not resolve it.

This script exercises both paths against the live database, leaving clear
evidence behind (an ``approved`` paper for the auto-fix path, a ``qa_flags``
row for the escalation path). It only touches the papers you name.

Usage, from the project root:

    python -m scripts.qa_demo --autofix <paper_id>
    python -m scripts.qa_demo --escalate <paper_id>

Each paper must already be at status ``scored`` (or ``approved`` from a previous
run of this script; it will be reset). Run the normal pipeline first:

    python -m dipt.pipeline summarise 4
    python -m dipt.pipeline score 4
"""

from __future__ import annotations

import argparse
import logging
import sys

from dipt.agents.llm_client import OllamaClient
from dipt.agents.qa_agent import QAAgent
from dipt.agents.scoring_agent import ScoringAgent
from dipt.agents.summarisation_agent import SummarisationAgent
from dipt.config import get_settings
from dipt.database.connection import ConnectionPool
from dipt.database.repository import PaperRepository
from dipt.exceptions import DIPTError
from dipt.logging_config import configure_logging
from dipt.models.schemas import PaperStatus

logger = logging.getLogger(__name__)

_BROKEN_SUMMARY = (
    "Research Problem:\nTODO\n\nMethodology:\n\nKey Findings:\n\n"
    "Industrial Implications:"
)


def _row_for(repo: PaperRepository, paper_id: int) -> dict:
    """Return the paper row dict the agents expect, or exit if unusable."""
    for status in (PaperStatus.SCORED, PaperStatus.APPROVED):
        for row in repo.fetch_papers_by_status([status], limit=1000):
            if row["id"] == paper_id:
                return row
    logger.error(
        "Paper %d is not at status 'scored' or 'approved'. Run summarise then "
        "score first.",
        paper_id,
    )
    raise SystemExit(2)


def _reset_to_scored(repo: PaperRepository, paper_id: int) -> None:
    """Put the paper back to ``scored`` and clear any earlier demo flag."""
    repo.update_status(paper_id, PaperStatus.SCORED)


def demo_autofix(repo: PaperRepository, qa: QAAgent, paper_id: int) -> None:
    """Corrupt the stored summary, then show QA regenerate it and approve."""
    _reset_to_scored(repo, paper_id)
    original = repo.get_summary(paper_id)
    repo.save_summary(paper_id, _BROKEN_SUMMARY, "demo-corruption")
    logger.info("Paper %d summary replaced with a truncated stub.", paper_id)

    row = _row_for(repo, paper_id)
    outcome = qa.check_paper(row)

    logger.info("QA outcome: %s", outcome)
    new_summary = repo.get_summary(paper_id)
    if outcome == "auto_fixed" and new_summary and new_summary != _BROKEN_SUMMARY:
        logger.info(
            "AUTO-FIX PATH OK: the summary was regenerated and the paper "
            "advanced to 'approved'. Regenerated summary begins:\n%s",
            new_summary[:300],
        )
    else:
        logger.warning(
            "Auto-fix path did not complete as expected (outcome=%s). The "
            "stored summary may need restoring manually. Original was:\n%s",
            outcome,
            original,
        )


def demo_escalate(
    repo: PaperRepository, settings, paper_id: int
) -> None:
    """Corrupt the summary and make regeneration fail, forcing an escalation.

    The auto-fix summariser is replaced with one whose ``summarise_paper``
    always returns ``None`` (a faithful stand-in for "the model produced
    nothing usable"). The QA agent must then write a ``qa_flags`` row for human
    review instead of approving the paper.
    """
    _reset_to_scored(repo, paper_id)
    repo.save_summary(paper_id, _BROKEN_SUMMARY, "demo-corruption")
    logger.info("Paper %d summary replaced with a truncated stub.", paper_id)

    client = OllamaClient(host=settings.ollama_base_url)

    class _FailingSummariser(SummarisationAgent):
        """A summariser whose regeneration attempts never succeed."""

        def summarise_paper(self, paper: dict):  # noqa: D102 - see class doc
            logger.info(
                "Auto-fix summariser deliberately returning nothing for "
                "paper %d",
                paper["id"],
            )
            return None

    broken_summariser = _FailingSummariser(
        client, repo, settings.model_summarisation
    )
    scorer = ScoringAgent(client, repo, settings.model_scoring)
    qa = QAAgent(
        client,
        repo,
        settings.model_qa,
        summariser=broken_summariser,
        scorer=scorer,
    )

    row = _row_for(repo, paper_id)
    outcome = qa.check_paper(row)
    logger.info("QA outcome: %s", outcome)

    flags = [f for f in repo.list_open_qa_flags() if f["paper_id"] == paper_id]
    if outcome == "flagged" and flags:
        logger.info(
            "ESCALATION PATH OK: auto-fix failed, so QA wrote a qa_flags row "
            "for human review: [%s] %s",
            flags[0]["flag_type"],
            flags[0]["description"],
        )
        logger.info(
            "The paper stays at 'scored' and is now visible in the dashboard "
            "QA queue."
        )
    else:
        logger.warning(
            "Escalation path did not complete as expected (outcome=%s, "
            "flags=%d).",
            outcome,
            len(flags),
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="QA agent dual-mode demo")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--autofix", type=int, metavar="PAPER_ID")
    group.add_argument("--escalate", type=int, metavar="PAPER_ID")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except DIPTError:
        logging.basicConfig(level=logging.ERROR)
        logger.exception("Failed to load configuration")
        return 1

    configure_logging(settings.log_level)
    pool = ConnectionPool(settings)
    repo = PaperRepository(pool)

    try:
        if args.autofix is not None:
            client = OllamaClient(host=settings.ollama_base_url)
            summariser = SummarisationAgent(
                client, repo, settings.model_summarisation
            )
            scorer = ScoringAgent(client, repo, settings.model_scoring)
            qa = QAAgent(
                client,
                repo,
                settings.model_qa,
                summariser=summariser,
                scorer=scorer,
            )
            demo_autofix(repo, qa, args.autofix)
        else:
            demo_escalate(repo, settings, args.escalate)
    except DIPTError:
        logger.exception("QA demo failed")
        return 1
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
