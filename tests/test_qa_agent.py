"""Unit tests for :mod:`dipt.agents.qa_agent`.

These cover both required modes: the auto-fix path (regenerate the affected
output and re-check) and the escalation path (write a ``qa_flags`` row when the
auto-fix does not resolve the problem).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dipt.agents.qa_agent import QAAgent
from dipt.exceptions import LLMRequestError
from dipt.models.schemas import PaperScore, PaperStatus, PaperSummary

_PASS_RESPONSE = (
    '{"passed": true, "target": "none", "reason": "All consistent."}'
)
_FAIL_SUMMARY_RESPONSE = (
    '{"passed": false, "target": "summary", "reason": "Summary is truncated."}'
)
_FAIL_SCORE_RESPONSE = (
    '{"passed": false, "target": "score", '
    '"reason": "Score of 9 with weak rationale."}'
)


def _make_agent() -> QAAgent:
    """Construct a QA agent with mocked collaborators."""
    return QAAgent(
        client=MagicMock(),
        repository=MagicMock(),
        model="test-model",
        summariser=MagicMock(),
        scorer=MagicMock(),
    )


def _paper(**overrides) -> dict:
    """Build a representative scored-paper row dict."""
    paper = {
        "id": 21,
        "title": "An Empirical Study of Flaky Tests",
        "abstract": "We study flaky tests.",
        "status": PaperStatus.SCORED.value,
        "full_text_path": None,
        "source": "arxiv",
    }
    paper.update(overrides)
    return paper


def _wire_stored_outputs(agent: QAAgent) -> None:
    """Give the mocked repository a plausible stored summary and score."""
    agent._repo.get_summary.return_value = "Research Problem:\nx\n\nMethodology:\ny"
    agent._repo.get_score.return_value = {
        "relevance_score": 7.0,
        "score_rationale": "Useful to many teams.",
    }


# ── parsing ────────────────────────────────────────────────────────────────
def test_parse_response_pass() -> None:
    """A YES verdict parses as passed with target none."""
    review = QAAgent._parse_response(_PASS_RESPONSE)
    assert review is not None
    assert review.passed is True
    assert review.target == "none"


def test_parse_response_fail_with_target() -> None:
    """A NO verdict keeps the named target."""
    review = QAAgent._parse_response(_FAIL_SCORE_RESPONSE)
    assert review is not None
    assert review.passed is False
    assert review.target == "score"


def test_parse_response_tolerates_code_fence_and_string_bool() -> None:
    """A fenced block and a stringified boolean are both understood."""
    fenced = '```json\n{"passed": "false", "target": "score", "reason": "x"}\n```'
    review = QAAgent._parse_response(fenced)
    assert review is not None
    assert review.passed is False
    assert review.target == "score"


def test_parse_response_none_without_verdict() -> None:
    """A JSON object with no ``passed`` key yields ``None``."""
    assert QAAgent._parse_response('{"reason": "nothing"}') is None


def test_parse_response_none_when_not_json() -> None:
    """A non-JSON response yields ``None``."""
    assert QAAgent._parse_response("the summary looks fine to me") is None


def test_parse_response_fail_without_target_defaults_to_summary() -> None:
    """A failure that names no actionable target defaults to summary."""
    review = QAAgent._parse_response(
        '{"passed": false, "target": "none", "reason": "bad"}'
    )
    assert review is not None
    assert review.passed is False
    assert review.target == "summary"


# ── pass path ──────────────────────────────────────────────────────────────
def test_check_paper_pass_approves() -> None:
    """A first-attempt pass advances the paper to approved."""
    agent = _make_agent()
    _wire_stored_outputs(agent)
    agent._client.chat.return_value = _PASS_RESPONSE

    assert agent.check_paper(_paper()) == "passed"
    agent._repo.update_status.assert_called_once_with(21, PaperStatus.APPROVED)
    agent._repo.save_qa_flag.assert_not_called()


# ── auto-fix path ──────────────────────────────────────────────────────────
def test_check_paper_auto_fix_summary_then_pass() -> None:
    """A failed summary is regenerated once and then passes."""
    agent = _make_agent()
    _wire_stored_outputs(agent)
    agent._client.chat.side_effect = [_FAIL_SUMMARY_RESPONSE, _PASS_RESPONSE]
    agent._summariser.summarise_paper.return_value = PaperSummary(
        research_problem="x", methodology="y", key_findings="z",
        industrial_implications="w",
    )

    assert agent.check_paper(_paper()) == "auto_fixed"
    agent._summariser.summarise_paper.assert_called_once()
    agent._repo.update_status.assert_called_once_with(21, PaperStatus.APPROVED)
    agent._repo.save_qa_flag.assert_not_called()


def test_check_paper_auto_fix_score_then_pass() -> None:
    """A failed score is regenerated once and then passes."""
    agent = _make_agent()
    _wire_stored_outputs(agent)
    agent._client.chat.side_effect = [_FAIL_SCORE_RESPONSE, _PASS_RESPONSE]
    agent._scorer.score_paper.return_value = PaperScore(score=6.0, rationale="ok")

    assert agent.check_paper(_paper()) == "auto_fixed"
    agent._scorer.score_paper.assert_called_once()
    agent._summariser.summarise_paper.assert_not_called()
    agent._repo.update_status.assert_called_once_with(21, PaperStatus.APPROVED)


# ── escalation path ────────────────────────────────────────────────────────
def test_check_paper_flags_when_regeneration_fails() -> None:
    """If regeneration itself fails, the paper is flagged, not approved."""
    agent = _make_agent()
    _wire_stored_outputs(agent)
    agent._client.chat.return_value = _FAIL_SUMMARY_RESPONSE
    agent._summariser.summarise_paper.return_value = None

    assert agent.check_paper(_paper()) == "flagged"
    agent._repo.save_qa_flag.assert_called_once()
    flag_args = agent._repo.save_qa_flag.call_args[0]
    assert flag_args[0] == 21
    agent._repo.update_status.assert_not_called()


def test_check_paper_flags_when_recheck_still_fails() -> None:
    """If the regenerated output still fails QA, the paper is escalated."""
    agent = _make_agent()
    _wire_stored_outputs(agent)
    agent._client.chat.side_effect = [
        _FAIL_SUMMARY_RESPONSE,
        _FAIL_SUMMARY_RESPONSE,
    ]
    agent._summariser.summarise_paper.return_value = PaperSummary(
        research_problem="x", methodology="y", key_findings="z",
        industrial_implications="w",
    )

    assert agent.check_paper(_paper()) == "flagged"
    agent._summariser.summarise_paper.assert_called_once()
    agent._repo.save_qa_flag.assert_called_once()
    agent._repo.update_status.assert_not_called()


def test_check_paper_flags_when_review_unparseable() -> None:
    """A review response with no verdict escalates immediately."""
    agent = _make_agent()
    _wire_stored_outputs(agent)
    agent._client.chat.return_value = "garbage, no xml"

    assert agent.check_paper(_paper()) == "flagged"
    agent._repo.save_qa_flag.assert_called_once()
    agent._summariser.summarise_paper.assert_not_called()


def test_check_paper_llm_error_escalates() -> None:
    """An LLM error during review escalates the paper for human review."""
    agent = _make_agent()
    _wire_stored_outputs(agent)
    agent._client.chat.side_effect = LLMRequestError("boom")

    assert agent.check_paper(_paper()) == "flagged"
    agent._repo.save_qa_flag.assert_called_once()


# ── run loop ───────────────────────────────────────────────────────────────
@patch("dipt.agents.qa_agent.time.sleep")
def test_run_aggregates_outcomes(_sleep: MagicMock) -> None:
    """The run loop tallies passed, auto_fixed, and flagged papers."""
    agent = _make_agent()
    _wire_stored_outputs(agent)
    agent._repo.fetch_papers_for_qa.return_value = [_paper(id=1), _paper(id=2)]
    agent._client.chat.return_value = _PASS_RESPONSE

    summary = agent.run(limit=2)

    assert summary.total == 2
    assert summary.passed == 2
    assert summary.auto_fixed == 0
    assert summary.flagged == 0
