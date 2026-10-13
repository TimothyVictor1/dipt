"""Unit tests for :mod:`dipt.agents.parser_agent`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from dipt.agents.parser_agent import ParserAgent, _clean_text
from dipt.models.schemas import PaperStatus


def test_clean_text_collapses_whitespace() -> None:
    """Excessive blank lines and spaces should be collapsed."""
    raw = "Hello\n\n\n\nworld     here"
    assert _clean_text(raw) == "Hello\n\nworld here"


def _make_agent() -> ParserAgent:
    """Construct a parser agent with a mocked repository and settings."""
    settings = MagicMock()
    settings.http_timeout_seconds = 5.0
    settings.pdf_dir = MagicMock()
    settings.text_dir = MagicMock()
    return ParserAgent(repository=MagicMock(), settings=settings)


def test_process_paper_no_url_marks_no_pdf() -> None:
    """A paper without a PDF URL should be marked no_pdf."""
    agent = _make_agent()
    outcome = agent._process_paper(paper_id=1, pdf_url="")
    assert outcome == "no_pdf"
    agent._repo.update_status.assert_called_with(1, PaperStatus.NO_PDF, None)


@patch.object(ParserAgent, "_download_pdf", return_value=False)
def test_process_paper_failed_download_marks_no_pdf(mock_dl: MagicMock) -> None:
    """A failed download should be marked no_pdf."""
    agent = _make_agent()
    outcome = agent._process_paper(paper_id=2, pdf_url="http://x/p.pdf")
    assert outcome == "no_pdf"
    mock_dl.assert_called_once()


def test_download_pdf_rejects_html_content_type() -> None:
    """A response with an HTML content type should be rejected."""
    agent = _make_agent()
    response = MagicMock()
    response.status_code = 200
    response.headers = {"Content-Type": "text/html"}
    response.content = b"<html></html>"

    with patch.object(agent._session, "get", return_value=response):
        assert agent._download_pdf("http://x", MagicMock()) is False


def test_download_pdf_rejects_non_pdf_magic() -> None:
    """Content that does not start with the PDF magic bytes is rejected."""
    agent = _make_agent()
    response = MagicMock()
    response.status_code = 200
    response.headers = {"Content-Type": "application/octet-stream"}
    response.content = b"NOTPDF...."

    with patch.object(agent._session, "get", return_value=response):
        assert agent._download_pdf("http://x", MagicMock()) is False


def test_download_pdf_handles_request_exception() -> None:
    """A network error during download should return False, not raise."""
    agent = _make_agent()
    with patch.object(
        agent._session, "get", side_effect=requests.RequestException("down")
    ):
        assert agent._download_pdf("http://x", MagicMock()) is False


def test_run_uses_the_pdf_url_from_the_repository_row() -> None:
    """The parser must actually attempt a download when a PDF URL exists.

    Regression test: ``fetch_papers_by_status`` did not select ``pdf_url``, so
    every row reached the agent without one and was silently marked ``no_pdf``
    without a single download being attempted.
    """
    agent = _make_agent()
    agent._repo.fetch_papers_by_status.return_value = [
        {
            "id": 11,
            "title": "A paper",
            "abstract": "",
            "status": PaperStatus.FETCHED.value,
            "full_text_path": None,
            "source": "arxiv",
            "pdf_url": "http://example.test/paper.pdf",
        }
    ]

    with patch.object(ParserAgent, "_download_pdf", return_value=False) as dl:
        summary = agent.run(limit=1)

    dl.assert_called_once()
    assert dl.call_args.args[0] == "http://example.test/paper.pdf"
    assert summary.no_pdf == 1


def test_summary_record_rejects_an_unknown_outcome() -> None:
    """A mistyped outcome must raise rather than silently miscount."""
    from dipt.agents.parser_agent import ParserSummary

    summary = ParserSummary()
    summary.record("parsed")
    assert summary.parsed == 1
    with pytest.raises(ValueError, match="Unknown parser outcome"):
        summary.record("parsedd")
