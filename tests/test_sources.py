"""Unit tests for :mod:`dipt.sources.openalex`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from dipt.models.schemas import PaperSource
from dipt.sources.openalex import (
    OpenAlexSource,
    _reconstruct_abstract,
    _should_skip,
)


def test_reconstruct_abstract_orders_words() -> None:
    """Words should be ordered by their recorded positions."""
    inverted = {"Hello": [0], "world": [1], "again": [2]}
    assert _reconstruct_abstract(inverted) == "Hello world again"


def test_reconstruct_abstract_handles_none() -> None:
    """A missing inverted index should yield an empty string."""
    assert _reconstruct_abstract(None) == ""


def test_should_skip_metadata_titles() -> None:
    """Known metadata titles should be skipped."""
    assert _should_skip("Issue Information") is True
    assert _should_skip("Editorial") is True


def test_should_skip_short_titles() -> None:
    """Very short titles should be skipped."""
    assert _should_skip("Bug") is True


def test_should_not_skip_real_title() -> None:
    """A genuine paper title should not be skipped."""
    assert _should_skip("An Empirical Study of Refactoring Practices") is False


def _make_source() -> OpenAlexSource:
    """Construct a source with test-friendly parameters."""
    return OpenAlexSource(
        contact_email="test@example.com",
        timeout_seconds=5.0,
        days_back=7,
    )


def test_parse_record_produces_valid_record() -> None:
    """A well-formed OpenAlex work should parse into a PaperRecord."""
    source = _make_source()
    raw = {
        "id": "https://openalex.org/W123",
        "title": "An Empirical Study of Code Review",
        "abstract_inverted_index": {"Code": [0], "review": [1]},
        "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
        "doi": "https://doi.org/10.1000/xyz",
        "publication_date": "2026-05-01",
        "primary_location": {"pdf_url": "https://example.com/p.pdf"},
    }

    record = source._parse_record(raw)

    assert record is not None
    assert record.source == PaperSource.OPENALEX
    assert record.doi == "10.1000/xyz"
    assert record.authors == ["Ada Lovelace"]
    assert record.pdf_url == "https://example.com/p.pdf"


def test_parse_record_skips_metadata_title() -> None:
    """Records with metadata titles should be skipped."""
    source = _make_source()
    raw = {"id": "W1", "title": "Issue Information"}
    assert source._parse_record(raw) is None


def test_parse_record_skips_missing_abstract() -> None:
    """Records without an abstract should be skipped."""
    source = _make_source()
    raw = {"id": "W1", "title": "A Real Software Engineering Paper Title"}
    assert source._parse_record(raw) is None


@patch("dipt.sources.openalex.requests.get")
def test_fetch_journal_handles_request_exception(mock_get: MagicMock) -> None:
    """A network error on one journal should be swallowed (empty result)."""
    mock_get.side_effect = requests.RequestException("network down")
    source = _make_source()

    results = list(source._fetch_journal("Test Journal", "0000-0000"))
    assert results == []


@patch("dipt.sources.openalex.requests.get")
def test_fetch_journal_handles_non_200(mock_get: MagicMock) -> None:
    """A non-200 response should be swallowed (empty result)."""
    response = MagicMock()
    response.status_code = 503
    mock_get.return_value = response
    source = _make_source()

    results = list(source._fetch_journal("Test Journal", "0000-0000"))
    assert results == []
