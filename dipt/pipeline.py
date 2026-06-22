"""Master pipeline orchestrator and command-line entry point.

This module wires together configuration, the connection pool, the repository,
the LLM client, the sources, and the agents, then exposes a small CLI:

    python -m dipt.pipeline fetch          # run the fetch agent
    python -m dipt.pipeline parse [N]      # run the parser (optional limit N)
    python -m dipt.pipeline categorise [N] # run categorisation (optional limit)
    python -m dipt.pipeline all [N]        # run all three in sequence

It is the only place that performs dependency wiring, keeping the agents and
sources free of global state and therefore unit-testable in isolation.
"""

from __future__ import annotations

import argparse
import logging
import sys

from dipt.agents.categorisation_agent import CategorisationAgent
from dipt.agents.fetch_agent import FetchAgent
from dipt.agents.llm_client import OllamaClient
from dipt.agents.parser_agent import ParserAgent
from dipt.agents.quality_gate import QualityGate
from dipt.config import Settings, get_settings
from dipt.database.connection import ConnectionPool
from dipt.database.repository import PaperRepository
from dipt.exceptions import DIPTError
from dipt.logging_config import configure_logging
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

    def _build_sources(self) -> list[PaperSourceFetcher]:
        """Construct the configured set of paper sources.

        Returns:
            A list of source fetchers.
        """
        return [
            OpenAlexSource(
                contact_email=self._settings.contact_email,
                timeout_seconds=self._settings.http_timeout_seconds,
                days_back=self._settings.fetch_days_back,
            ),
            ArxivSource(
                contact_email=self._settings.contact_email,
                timeout_seconds=self._settings.http_timeout_seconds,
                days_back=self._settings.fetch_days_back,
            ),
        ]

    def fetch(self) -> None:
        """Run the fetch agent across all configured sources."""
        gate = QualityGate(self._llm, self._settings.model_quality_gate)
        agent = FetchAgent(self._build_sources(), gate, self._repo)
        summary = agent.run()
        logger.info("Fetch summary: %d papers saved", summary.total_saved)

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
            self._llm, self._repo, self._settings.model_categorisation
        )
        summary = agent.run(limit=limit)
        logger.info(
            "Categorise summary: categorised=%d, failed=%d",
            summary.categorised,
            summary.failed,
        )

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
        choices=["fetch", "parse", "categorise", "all"],
        help="Pipeline stage to run",
    )
    parser.add_argument(
        "limit",
        nargs="?",
        type=int,
        default=None,
        help="Optional max number of papers (parse/categorise stages)",
    )
    return parser


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
        if args.stage in ("fetch", "all"):
            pipeline.fetch()
        if args.stage in ("parse", "all"):
            pipeline.parse(args.limit)
        if args.stage in ("categorise", "all"):
            pipeline.categorise(args.limit)
    except DIPTError:
        logger.exception("Pipeline stage '%s' failed", args.stage)
        return 1
    finally:
        pipeline.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
