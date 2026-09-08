"""Unit tests for :mod:`dipt.agents.scoring_agent`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dipt.agents.scoring_agent import ScoringAgent
from dipt.exceptions import LLMRequestError, RepositoryError
from dipt.models.schemas import PaperStatus

_GOOD_RESPONSE = """
<assessment>
  <score>7.5</score>
  <rationale>Test teams could adopt the isolation technique within a quarter.</rationale>
</assessment>
"""

_REASONING_RESPONSE = """
<think>
The paper is about flaky tests. Score should be maybe 8 because it is broadly
useful. Let me settle on 8.
</think>
<assessment>
  <score>8</score>
  <rationale>Broadly useful to any team with a large test suite.</rationale>
</assessment>
"""


def _make_agent() -> ScoringAgent:
    """Construct an agent with a mocked client and repository."""
    return ScoringAgent(
        client=MagicMock(),
        repository=MagicMock(),
        model="test-model",
    )


def _paper(**overrides) -> dict:
    """Build a representative summarised-paper row dict."""
    paper = {
        "id": 11,
        "title": "An Empirical Study of Flaky Tests",
        "abstract": "We study flaky tests.",
        "status": PaperStatus.SUMMARISED.value,
        "full_text_path": None,
        "source": "arxiv",
    }
    paper.update(overrides)
    return paper


def test_parse_response_extracts_score_and_rationale() -> None:
    """Well-formed XML yields a score and rationale."""
    result = ScoringAgent._parse_response(_GOOD_RESPONSE)

    assert result is not None
    assert result.score == 7.5
    assert "isolation technique" in result.rationale


def test_parse_response_strips_reasoning_block() -> None:
    """A leading <think> block must not be mistaken for the answer."""
    result = ScoringAgent._parse_response(_REASONING_RESPONSE)

    assert result is not None
    assert result.score == 8.0
    assert "large test suite" in result.rationale


def test_parse_response_returns_none_without_score() -> None:
    """A response with no <score> tag yields ``None``."""
    assert ScoringAgent._parse_response("<rationale>no score</rationale>") is None


def test_parse_response_clamps_out_of_range_score() -> None:
    """A score above 10 is clamped into range rather than rejected."""
    result = ScoringAgent._parse_response(
        "<score>12</score><rationale>enthusiastic</rationale>"
    )

    assert result is not None
    assert result.score == 10.0


def test_score_paper_returns_none_when_summary_missing() -> None:
    """Without a stored summary the agent cannot score the paper."""
    agent = _make_agent()
    agent._repo.get_summary.return_value = None

    assert agent.score_paper(_paper()) is None
    agent._client.chat.assert_not_called()


def test_score_paper_persists_and_returns_score() -> None:
    """A good response is parsed, saved, and returned."""
    agent = _make_agent()
    agent._repo.get_summary.return_value = "Research Problem:\n..."
    agent._client.chat.return_value = _GOOD_RESPONSE

    result = agent.score_paper(_paper())

    assert result is not None
    saved = agent._repo.save_score.call_args[0]
    assert saved[0] == 11
    assert saved[1] == 7.5
    assert saved[3] == "test-model"


def test_score_paper_returns_none_on_llm_error() -> None:
    """An LLM failure yields ``None`` and writes nothing."""
    agent = _make_agent()
    agent._repo.get_summary.return_value = "Research Problem:\n..."
    agent._client.chat.side_effect = LLMRequestError("boom")

    assert agent.score_paper(_paper()) is None
    agent._repo.save_score.assert_not_called()


def test_score_paper_returns_none_on_save_failure() -> None:
    """A repository failure while saving yields ``None``."""
    agent = _make_agent()
    agent._repo.get_summary.return_value = "Research Problem:\n..."
    agent._client.chat.return_value = _GOOD_RESPONSE
    agent._repo.save_score.side_effect = RepositoryError("db down")

    assert agent.score_paper(_paper()) is None


@patch("dipt.agents.scoring_agent.time.sleep")
def test_run_advances_status_on_success(_sleep: MagicMock) -> None:
    """A successful score advances the paper to ``scored``."""
    agent = _make_agent()
    agent._repo.fetch_papers_by_status.return_value = [_paper()]
    agent._repo.get_summary.return_value = "Research Problem:\n..."
    agent._client.chat.return_value = _GOOD_RESPONSE

    summary = agent.run(limit=1)

    assert summary.scored == 1
    assert summary.failed == 0
    agent._repo.update_status.assert_called_once_with(11, PaperStatus.SCORED)


@patch("dipt.agents.scoring_agent.time.sleep")
def test_run_counts_failure_without_status_change(_sleep: MagicMock) -> None:
    """A paper with no usable score is counted as failed and left as-is."""
    agent = _make_agent()
    agent._repo.fetch_papers_by_status.return_value = [_paper()]
    agent._repo.get_summary.return_value = "Research Problem:\n..."
    agent._client.chat.return_value = "no score here"

    summary = agent.run(limit=1)

    assert summary.scored == 0
    assert summary.failed == 1
    agent._repo.update_status.assert_not_called()
