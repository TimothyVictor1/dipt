"""Master pipeline orchestrator and command-line entry point.

This module wires together configuration, the connection pool, the repository,
the LLM client, the sources, and the agents, then exposes a small CLI:

    python -m dipt.pipeline fetch [N]      # run the fetch agent (optional cap N
                                          #   on new papers saved)
    python -m dipt.pipeline parse [N]      # run the parser (optional limit N)
    python -m dipt.pipeline categorise [N] # run categorisation (optional limit)
    python -m dipt.pipeline summarise [N]  # run summarisation (optional limit)
    python -m dipt.pipeline score [N]      # run scoring (optional limit)
    python -m dipt.pipeline qa [N]         # run the QA agent (optional limit)
    python -m dipt.pipeline all [N]        # run every stage in sequence (batch)
    python -m dipt.pipeline stream [N]     # push each paper through the LLM
                                           # stages one at a time

It is the only place that performs dependency wiring, keeping the agents and
sources free of global state and therefore unit-testable in isolation.

Batch mode (``all``) runs each stage to completion across the whole backlog
before starting the next. Streaming mode (``stream``) takes papers that have
been categorised and carries each one through summarise, score, and QA before
moving to the next paper, so approved papers start appearing much sooner.
"""

from __future__ import annotations

import argparse
import logging
import sys

from dipt.agents.categorisation_agent import CategorisationAgent
from dipt.agents.fetch_agent import FetchAgent
from dipt.agents.llm_client import OllamaClient
from dipt.agents.parser_agent import ParserAgent
from dipt.agents.qa_agent import QAAgent
from dipt.agents.quality_gate import QualityGate
from dipt.agents.scoring_agent import ScoringAgent
from dipt.agents.summarisation_agent import SummarisationAgent
from dipt.config import Settings, get_settings
from dipt.database.connection import ConnectionPool
from dipt.database.repository import PaperRepository
from dipt.exceptions import DIPTError, RepositoryError
from dipt.logging_config import configure_logging
from dipt import prompt_store
from dipt.models.schemas import PaperStatus
from dipt.sources.arxiv import ArxivSource
from dipt.sources.base import PaperSourceFetcher
from dipt.sources.openalex import OpenAlexSource

logger = logging.getLogger(__name__)


class Pipeline:
    """Owns dependency wiring and exposes per-stage entry points.

    Args:
        settings: Validated application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool = ConnectionPool(settings)
        self._repo = PaperRepository(self._pool)
        self._llm = OllamaClient(host=settings.ollama_base_url)
        self._fetch_log_ready = self._repo.ensure_support_tables()

    @staticmethod
    def _prompt(agent: str) -> str | None:
        """Return the admin-edited prompt for an agent, if one is stored.

        Overrides live in ``config/agent_prompts/<agent>.txt`` (see
        :mod:`dipt.prompt_store`). A missing or unreadable file means "use the
        built-in default".

        Args:
            agent: Agent key, e.g. ``"summarisation"``.

        Returns:
            The stored template, or ``None``.
        """
        return prompt_store.get(agent)

    def _build_sources(
        self, days_back: int | None = None
    ) -> list[PaperSourceFetcher]:
        """Construct the configured set of paper sources.

        Args:
            days_back: Optional override for how far back each source looks.
                The scheduler passes a window derived from the last successful
                run so a run only fetches genuinely new papers.

        Returns:
            A list of source fetchers.
        """
        window = (
            days_back if days_back is not None else self._settings.fetch_days_back
        )
        return [
            OpenAlexSource(
                contact_email=self._settings.contact_email,
                timeout_seconds=self._settings.http_timeout_seconds,
                days_back=window,
            ),
            ArxivSource(
                contact_email=self._settings.contact_email,
                timeout_seconds=self._settings.http_timeout_seconds,
                days_back=window,
            ),
        ]

    def _build_summariser(self) -> SummarisationAgent:
        """Construct the summarisation agent."""
        return SummarisationAgent(
            self._llm,
            self._repo,
            self._settings.model_summarisation,
            prompt_template=self._prompt("summarisation"),
        )

    def _build_scorer(self) -> ScoringAgent:
        """Construct the scoring agent."""
        return ScoringAgent(
            self._llm,
            self._repo,
            self._settings.model_scoring,
            prompt_template=self._prompt("scoring"),
        )

    def _build_qa(
        self, summariser: SummarisationAgent, scorer: ScoringAgent
    ) -> QAAgent:
        """Construct the QA agent, sharing the auto-fix collaborators.

        Args:
            summariser: Agent used to regenerate a summary during auto-fix.
            scorer: Agent used to regenerate a score during auto-fix.

        Returns:
            A wired :class:`QAAgent`.
        """
        return QAAgent(
            self._llm,
            self._repo,
            self._settings.model_qa,
            summariser=summariser,
            scorer=scorer,
            prompt_template=self._prompt("qa"),
        )

    def fetch(
        self, days_back: int | None = None, limit: int | None = None
    ) -> int:
        """Run the fetch agent across all configured sources.

        Args:
            days_back: Optional override for the look-back window (used by the
                scheduler to fetch only papers newer than the last run).
            limit: Optional cap on the number of new papers saved this run.

        Returns:
            The number of new papers saved.
        """
        gate = QualityGate(
            self._llm,
            self._settings.model_quality_gate,
            prompt_template=self._prompt("quality_gate"),
        )
        agent = FetchAgent(self._build_sources(days_back), gate, self._repo)
        summary = agent.run(limit=limit)
        logger.info("Fetch summary: %d papers saved", summary.total_saved)
        return summary.total_saved

    def parse(self, limit: int | None) -> None:
        """Run the parser agent.

        Args:
            limit: Optional cap on papers to process.
        """
        agent = ParserAgent(self._repo, self._settings)
        summary = agent.run(limit=limit)
        logger.info(
            "Parse summary: parsed=%d, no_pdf=%d, no_text=%d",
            summary.parsed,
            summary.no_pdf,
            summary.no_text,
        )

    def categorise(self, limit: int | None) -> None:
        """Run the categorisation agent.

        Args:
            limit: Optional cap on papers to process.
        """
        agent = CategorisationAgent(
            self._llm,
            self._repo,
            self._settings.model_categorisation,
            prompt_template=self._prompt("categorisation"),
        )
        summary = agent.run(limit=limit)
        logger.info(
            "Categorise summary: categorised=%d, failed=%d",
            summary.categorised,
            summary.failed,
        )

    def summarise(self, limit: int | None) -> None:
        """Run the summarisation agent.

        Args:
            limit: Optional cap on papers to process.
        """
        summary = self._build_summariser().run(limit=limit)
        logger.info(
            "Summarise summary: summarised=%d, failed=%d",
            summary.summarised,
            summary.failed,
        )

    def score(self, limit: int | None) -> None:
        """Run the scoring agent.

        Args:
            limit: Optional cap on papers to process.
        """
        summary = self._build_scorer().run(limit=limit)
        logger.info(
            "Score summary: scored=%d, failed=%d",
            summary.scored,
            summary.failed,
        )

    def qa(self, limit: int | None) -> None:
        """Run the QA agent.

        Args:
            limit: Optional cap on papers to process.
        """
        summariser = self._build_summariser()
        scorer = self._build_scorer()
        summary = self._build_qa(summariser, scorer).run(limit=limit)
        logger.info(
            "QA summary: passed=%d, auto_fixed=%d, flagged=%d",
            summary.passed,
            summary.auto_fixed,
            summary.flagged,
        )

    def stream(self, limit: int | None) -> None:
        """Carry each categorised paper through summarise, score, and QA.

        Unlike :meth:`summarise` / :meth:`score` / :meth:`qa`, which each drain
        the whole backlog before the next stage begins, this walks one paper at
        a time through all three LLM stages. The trade-off is more model
        switching; the benefit is that fully approved papers appear from the
        first paper onward, which is what the dashboard and public site want.

        Fetch, parse, and categorise remain batch stages: they are network
        bound or cheap, and there is nothing to gain from interleaving them.

        Args:
            limit: Optional cap on papers to carry through.
        """
        summariser = self._build_summariser()
        scorer = self._build_scorer()
        qa_agent = self._build_qa(summariser, scorer)

        try:
            papers = self._repo.fetch_papers_by_status(
                [PaperStatus.CATEGORISED], limit=limit
            )
        except RepositoryError:
            logger.exception("Streaming: could not load categorised papers")
            return

        logger.info("Streaming %d paper(s) through summarise -> score -> qa",
                    len(papers))

        approved = 0
        for index, paper in enumerate(papers, start=1):
            paper_id = paper["id"]
            logger.info("[%d/%d] Streaming paper %d", index, len(papers), paper_id)

            if summariser.summarise_paper(paper) is None:
                logger.warning("Paper %d: summarise failed, leaving as-is", paper_id)
                continue
            self._safe_update(paper_id, PaperStatus.SUMMARISED)

            if scorer.score_paper(paper) is None:
                logger.warning("Paper %d: score failed, leaving as summarised",
                               paper_id)
                continue
            self._safe_update(paper_id, PaperStatus.SCORED)

            outcome = qa_agent.check_paper(paper)
            if outcome in ("passed", "auto_fixed"):
                approved += 1
            logger.info("Paper %d: QA %s", paper_id, outcome)

        logger.info(
            "Streaming complete: %d/%d paper(s) approved", approved, len(papers)
        )

    def _safe_update(self, paper_id: int, status: PaperStatus) -> None:
        """Update a paper's status, logging any repository failure.

        Args:
            paper_id: Paper primary key.
            status: New status.
        """
        try:
            self._repo.update_status(paper_id, status)
        except RepositoryError:
            logger.exception("Failed to update status for paper %d", paper_id)

    @property
    def repository(self) -> PaperRepository:
        """The repository this pipeline is wired to (used by the scheduler)."""
        return self._repo

    @property
    def has_fetch_log(self) -> bool:
        """Whether the ``fetch_log`` table is present (used by the scheduler)."""
        return self._fetch_log_ready

    def run_all(self, limit: int | None = None) -> None:
        """Run every stage once, in lifecycle order (batch mode).

        Args:
            limit: Optional cap applied to every stage, including the number of
                new papers ``fetch`` saves.
        """
        self.fetch(limit=limit)
        self.parse(limit)
        self.categorise(limit)
        self.summarise(limit)
        self.score(limit)
        self.qa(limit)

    def close(self) -> None:
        """Release all owned resources."""
        self._pool.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser.

    Returns:
        A configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(description="DIPT pipeline runner")
    parser.add_argument(
        "stage",
        choices=[
            "fetch",
            "parse",
            "categorise",
            "summarise",
            "score",
            "qa",
            "all",
            "stream",
        ],
        help="Pipeline stage to run",
    )
    parser.add_argument(
        "limit",
        nargs="?",
        type=int,
        default=None,
        help=(
            "Optional cap: papers processed by per-paper stages, or new "
            "papers saved by 'fetch'"
        ),
    )
    return parser


def _run_stage(pipeline: Pipeline, stage: str, limit: int | None) -> None:
    """Dispatch a single CLI stage to the pipeline.

    Args:
        pipeline: The wired pipeline.
        stage: The stage name from the CLI.
        limit: Optional per-stage paper cap.
    """
    if stage == "stream":
        pipeline.stream(limit)
        return
    if stage == "all":
        pipeline.run_all(limit)
        return

    {
        "fetch": lambda: pipeline.fetch(limit=limit),
        "parse": lambda: pipeline.parse(limit),
        "categorise": lambda: pipeline.categorise(limit),
        "summarise": lambda: pipeline.summarise(limit),
        "score": lambda: pipeline.score(limit),
        "qa": lambda: pipeline.qa(limit),
    }[stage]()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv``).

    Returns:
        Process exit code (0 success, 1 failure).
    """
    args = _build_arg_parser().parse_args(argv)

    try:
        settings = get_settings()
    except DIPTError:
        logging.basicConfig(level=logging.ERROR)
        logger.exception("Failed to load configuration")
        return 1

    configure_logging(settings.log_level)
    pipeline = Pipeline(settings)

    try:
        _run_stage(pipeline, args.stage, args.limit)
    except DIPTError:
        logger.exception("Pipeline stage '%s' failed", args.stage)
        return 1
    finally:
        pipeline.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
