"""Unit tests for :mod:`dipt.pipeline` wiring and streaming mode."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dipt import pipeline as pipeline_mod
from dipt.pipeline import Pipeline, _run_stage
from dipt.models.schemas import PaperStatus


@pytest.fixture
def wired_pipeline():
    """A Pipeline with the pool, repository, and LLM client mocked out."""
    with patch.object(pipeline_mod, "ConnectionPool"), patch.object(
        pipeline_mod, "OllamaClient"
    ), patch.object(pipeline_mod, "PaperRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.ensure_support_tables.return_value = False
        p = Pipeline(settings=MagicMock())
        yield p, repo


# ── prompt overrides come from the file store ────────────────────────────
def test_prompt_returns_none_when_no_override_file() -> None:
    with patch.object(pipeline_mod.prompt_store, "get", return_value=None) as g:
        assert Pipeline._prompt("summarisation") is None
    g.assert_called_once_with("summarisation")


def test_prompt_returns_stored_override() -> None:
    with patch.object(
        pipeline_mod.prompt_store, "get", return_value="custom {content}"
    ):
        assert Pipeline._prompt("summarisation") == "custom {content}"


# ── CLI dispatch ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "stage, method",
    [
        ("fetch", "fetch"),
        ("parse", "parse"),
        ("categorise", "categorise"),
        ("summarise", "summarise"),
        ("score", "score"),
        ("qa", "qa"),
        ("stream", "stream"),
    ],
)
def test_run_stage_dispatches_single_stage(stage, method) -> None:
    p = MagicMock()
    _run_stage(p, stage, limit=5)
    getattr(p, method).assert_called_once()


def test_run_stage_all_runs_full_chain() -> None:
    p = MagicMock()
    _run_stage(p, "all", limit=None)
    p.run_all.assert_called_once_with(None)


def test_run_all_invokes_every_stage_in_order() -> None:
    p = MagicMock(spec=Pipeline)
    Pipeline.run_all(p, limit=7)
    p.fetch.assert_called_once()
    p.parse.assert_called_once_with(7)
    p.categorise.assert_called_once_with(7)
    p.summarise.assert_called_once_with(7)
    p.score.assert_called_once_with(7)
    p.qa.assert_called_once_with(7)


# ── streaming mode ───────────────────────────────────────────────────────
def _paper(pid: int) -> dict:
    return {
        "id": pid,
        "title": f"Paper {pid}",
        "abstract": "a",
        "status": PaperStatus.CATEGORISED.value,
        "full_text_path": None,
        "source": "arxiv",
    }


def test_stream_carries_paper_through_all_three_stages(wired_pipeline) -> None:
    p, repo = wired_pipeline
    repo.fetch_papers_by_status.return_value = [_paper(1)]

    summariser, scorer, qa = MagicMock(), MagicMock(), MagicMock()
    summariser.summarise_paper.return_value = object()
    scorer.score_paper.return_value = object()
    qa.check_paper.return_value = "passed"

    with patch.object(p, "_build_summariser", return_value=summariser), \
         patch.object(p, "_build_scorer", return_value=scorer), \
         patch.object(p, "_build_qa", return_value=qa):
        p.stream(limit=1)

    summariser.summarise_paper.assert_called_once()
    scorer.score_paper.assert_called_once()
    qa.check_paper.assert_called_once()
    statuses = [c.args[1] for c in repo.update_status.call_args_list]
    assert PaperStatus.SUMMARISED in statuses
    assert PaperStatus.SCORED in statuses


def test_stream_stops_at_failed_summary(wired_pipeline) -> None:
    p, repo = wired_pipeline
    repo.fetch_papers_by_status.return_value = [_paper(1)]

    summariser, scorer, qa = MagicMock(), MagicMock(), MagicMock()
    summariser.summarise_paper.return_value = None  # failure

    with patch.object(p, "_build_summariser", return_value=summariser), \
         patch.object(p, "_build_scorer", return_value=scorer), \
         patch.object(p, "_build_qa", return_value=qa):
        p.stream(limit=1)

    scorer.score_paper.assert_not_called()
    qa.check_paper.assert_not_called()
    repo.update_status.assert_not_called()
