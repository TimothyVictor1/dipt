"""Unit tests for :mod:`dipt.agents.fetch_agent`, focused on the save cap."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from dipt.agents.fetch_agent import FetchAgent
from dipt.models.schemas import PaperRecord, PaperSource


def _record(n: int) -> PaperRecord:
    return PaperRecord(
        title=f"An Empirical Study {n}",
        abstract="We study software.",
        source=PaperSource.ARXIV,
        source_id=f"arxiv:{n}",
        published_date=date(2026, 5, 1),
    )


def _source(name: str, count: int) -> MagicMock:
    src = MagicMock()
    src.source_name = name
    src.fetch.return_value = iter([_record(i) for i in range(count)])
    return src


def _gate(accept: bool = True) -> MagicMock:
    gate = MagicMock()
    gate.is_software_engineering.return_value = accept
    return gate


def test_run_without_limit_saves_everything() -> None:
    repo = MagicMock()
    repo.insert_paper.return_value = True
    agent = FetchAgent([_source("arxiv", 5)], _gate(), repo)

    summary = agent.run()

    assert summary.total_saved == 5


def test_run_stops_at_limit_within_a_source() -> None:
    repo = MagicMock()
    repo.insert_paper.return_value = True
    agent = FetchAgent([_source("arxiv", 10)], _gate(), repo)

    summary = agent.run(limit=3)

    assert summary.total_saved == 3
    assert repo.insert_paper.call_count == 3


def test_run_limit_spans_sources_and_skips_later_ones() -> None:
    repo = MagicMock()
    repo.insert_paper.return_value = True
    src_a = _source("arxiv", 2)
    src_b = _source("openalex", 5)
    agent = FetchAgent([src_a, src_b], _gate(), repo)

    summary = agent.run(limit=3)

    assert summary.total_saved == 3
    assert summary.saved["arxiv"] == 2
    assert summary.saved["openalex"] == 1


def test_run_limit_ignores_rejected_and_duplicate_papers() -> None:
    """Only *saved* papers count toward the cap."""
    repo = MagicMock()
    # First call duplicate, rest new.
    repo.insert_paper.side_effect = [False, True, True, True, True]
    agent = FetchAgent([_source("arxiv", 5)], _gate(), repo)

    summary = agent.run(limit=2)

    assert summary.total_saved == 2
    assert summary.duplicates["arxiv"] == 1
