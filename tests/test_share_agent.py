"""Unit tests for :mod:`dipt.agents.share_agent`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dipt import share_store
from dipt.agents.share_agent import SharePostAgent
from dipt.exceptions import LLMRequestError


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(share_store, "_STORE_DIR", tmp_path / "share_posts")
    return tmp_path


def _agent() -> SharePostAgent:
    return SharePostAgent(
        client=MagicMock(), repository=MagicMock(), model="test-model"
    )


def _paper(pid: int = 942) -> dict:
    return {
        "id": pid,
        "title": "Kerncap: Automated Kernel Extraction for AMD GPUs",
        "relevance_score": 7.0,
        "categories": ["Software Construction", "SE4AI"],
        "summary": (
            "Research Problem:\nReproducing GPU kernel runs is slow.\n\n"
            "Methodology:\nRuntime interception of dispatches.\n\n"
            "Key Findings:\nSix real workloads reproduced faithfully.\n\n"
            "Industrial Implications:\nFaster kernel tuning for AMD teams."
        ),
    }


def test_generate_for_stores_a_cleaned_post() -> None:
    agent = _agent()
    agent._client.chat.return_value = (
        'Post: "I came across Kerncap, a tool that makes reproducing AMD GPU '
        'kernel runs much faster. Handy if you tune kernels. #SoftwareEngineering"'
    )
    out = agent.generate_for(_paper())
    assert out is not None
    assert out.startswith("I came across Kerncap")
    assert share_store.get(942) == out
    # the paper's summary sections were fed into the prompt
    prompt = agent._client.chat.call_args[0][1]
    assert "Reproducing GPU kernel runs is slow." in prompt
    assert "7.0" in prompt


def test_generate_for_returns_none_on_llm_error() -> None:
    agent = _agent()
    agent._client.chat.side_effect = LLMRequestError("boom")
    assert agent.generate_for(_paper()) is None
    assert share_store.has(942) is False


def test_generate_for_returns_none_on_too_short_output() -> None:
    agent = _agent()
    agent._client.chat.return_value = "ok"
    assert agent.generate_for(_paper()) is None


@patch("dipt.agents.share_agent.time.sleep")
def test_run_drafts_only_papers_without_a_post(_sleep) -> None:
    agent = _agent()
    agent._repo.list_approved_papers.return_value = [_paper(1), _paper(2), _paper(3)]
    agent._client.chat.return_value = (
        "I found this one worth a look - it speeds up a slow part of GPU work."
    )
    share_store.set(2, "already has a post from before")

    summary = agent.run()

    assert summary.total == 3
    assert summary.skipped == 1
    assert summary.generated == 2
    assert summary.failed == 0
    assert share_store.get(2) == "already has a post from before"  # untouched


@patch("dipt.agents.share_agent.time.sleep")
def test_run_force_redrafts_everything(_sleep) -> None:
    agent = _agent()
    agent._repo.list_approved_papers.return_value = [_paper(1)]
    share_store.set(1, "old post")
    agent._client.chat.return_value = (
        "A fresh take: this paper tackles a real pain point in kernel tuning."
    )

    summary = agent.run(force=True)

    assert summary.generated == 1
    assert share_store.get(1).startswith("A fresh take")
