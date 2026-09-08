"""Unit tests for :mod:`dipt.agents.summarisation_agent`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dipt.agents.summarisation_agent import SummarisationAgent
from dipt.exceptions import LLMRequestError, RepositoryError
from dipt.models.schemas import PaperStatus

_GOOD_RESPONSE = """
{
  "research_problem": "Flaky tests waste developer time.",
  "methodology": "Mined 100 repositories and interviewed 12 engineers.",
  "key_findings": "Order dependency causes 40 percent of flakiness.",
  "industrial_implications": "Teams should isolate test state."
}
"""


def _make_agent() -> SummarisationAgent:
    """Construct an agent with a mocked client and repository."""
    return SummarisationAgent(
        client=MagicMock(),
        repository=MagicMock(),
        model="test-model",
    )


def _paper(**overrides) -> dict:
    """Build a representative categorised-paper row dict."""
    paper = {
        "id": 7,
        "title": "An Empirical Study of Flaky Tests",
        "abstract": "We study flaky tests in open-source projects.",
        "status": PaperStatus.CATEGORISED.value,
        "full_text_path": None,
        "source": "arxiv",
    }
    paper.update(overrides)
    return paper


def test_parse_response_extracts_all_sections() -> None:
    """A well-formed JSON object should populate every summary section."""
    result = SummarisationAgent._parse_response(_GOOD_RESPONSE)

    assert result is not None
    assert result.research_problem == "Flaky tests waste developer time."
    assert "Mined 100 repositories" in result.methodology
    assert "Order dependency" in result.key_findings
    assert "isolate test state" in result.industrial_implications


def test_parse_response_returns_none_when_not_json() -> None:
    """A response that is not JSON yields ``None``."""
    assert SummarisationAgent._parse_response("no json here at all") is None


def test_parse_response_returns_none_when_not_an_object() -> None:
    """A JSON array (not an object) yields ``None``."""
    assert SummarisationAgent._parse_response('["a", "b"]') is None


def test_parse_response_accepts_partial_and_extra_keys() -> None:
    """Missing keys become empty; unknown keys are ignored."""
    partial = (
        '{"research_problem": "Only this one is present.", '
        '"notes": "ignored", "methodology": ""}'
    )
    result = SummarisationAgent._parse_response(partial)

    assert result is not None
    assert result.research_problem == "Only this one is present."
    assert result.methodology == ""


def test_parse_response_strips_code_fence() -> None:
    """A fenced ```json block is tolerated despite JSON mode."""
    fenced = (
        "```json\n"
        '{"research_problem": "X", "methodology": "Y", '
        '"key_findings": "Z", "industrial_implications": "W"}\n'
        "```"
    )
    result = SummarisationAgent._parse_response(fenced)

    assert result is not None
    assert result.key_findings == "Z"


def test_parse_response_coerces_non_string_values() -> None:
    """A non-string value is coerced rather than crashing the parse."""
    result = SummarisationAgent._parse_response(
        '{"research_problem": 42, "methodology": "m", '
        '"key_findings": "k", "industrial_implications": "i"}'
    )
    assert result is not None
    assert result.research_problem == "42"


def test_prepare_content_prefers_full_text(tmp_path) -> None:
    """When a full-text file exists it is used regardless of status."""
    text_file = tmp_path / "paper_7.txt"
    text_file.write_text("Full body of the paper about flaky tests.", encoding="utf-8")
    agent = _make_agent()

    content = agent._prepare_content(
        _paper(status=PaperStatus.CATEGORISED.value, full_text_path=str(text_file))
    )

    assert "Full text (excerpt):" in content
    assert "Full body of the paper" in content


def test_prepare_content_falls_back_to_abstract_when_no_path() -> None:
    """Without a text path the abstract is used."""
    agent = _make_agent()
    content = agent._prepare_content(_paper(full_text_path=None))

    assert "Abstract:" in content
    assert "flaky tests in open-source projects" in content


def test_prepare_content_falls_back_when_file_missing() -> None:
    """A recorded path that does not exist falls back to the abstract."""
    agent = _make_agent()
    content = agent._prepare_content(
        _paper(full_text_path="/nonexistent/paper_7.txt")
    )

    assert "Abstract:" in content


def test_summarise_paper_persists_and_returns_summary() -> None:
    """A good LLM response is parsed, saved, and returned."""
    agent = _make_agent()
    agent._client.chat.return_value = _GOOD_RESPONSE

    result = agent.summarise_paper(_paper())

    assert result is not None
    agent._repo.save_summary.assert_called_once()
    saved_args = agent._repo.save_summary.call_args[0]
    assert saved_args[0] == 7
    assert "Research Problem:" in saved_args[1]
    assert saved_args[2] == "test-model"


def test_summarise_paper_returns_none_on_llm_error() -> None:
    """An LLM failure yields ``None`` and writes nothing."""
    agent = _make_agent()
    agent._client.chat.side_effect = LLMRequestError("boom")

    assert agent.summarise_paper(_paper()) is None
    agent._repo.save_summary.assert_not_called()


def test_summarise_paper_returns_none_on_save_failure() -> None:
    """A repository failure while saving yields ``None``."""
    agent = _make_agent()
    agent._client.chat.return_value = _GOOD_RESPONSE
    agent._repo.save_summary.side_effect = RepositoryError("db down")

    assert agent.summarise_paper(_paper()) is None


@patch("dipt.agents.summarisation_agent.time.sleep")
def test_run_advances_status_on_success(_sleep: MagicMock) -> None:
    """A successful summary advances the paper to ``summarised``."""
    agent = _make_agent()
    agent._repo.fetch_papers_by_status.return_value = [_paper()]
    agent._client.chat.return_value = _GOOD_RESPONSE

    summary = agent.run(limit=1)

    assert summary.summarised == 1
    assert summary.failed == 0
    agent._repo.update_status.assert_called_once_with(7, PaperStatus.SUMMARISED)


@patch("dipt.agents.summarisation_agent.time.sleep")
def test_run_counts_failure_without_status_change(_sleep: MagicMock) -> None:
    """A paper with no usable summary is counted as failed and left as-is."""
    agent = _make_agent()
    agent._repo.fetch_papers_by_status.return_value = [_paper()]
    agent._client.chat.return_value = "unparseable"

    summary = agent.run(limit=1)

    assert summary.summarised == 0
    assert summary.failed == 1
    agent._repo.update_status.assert_not_called()
