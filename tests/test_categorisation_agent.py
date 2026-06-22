"""Unit tests for :mod:`dipt.agents.categorisation_agent`."""

from __future__ import annotations

from unittest.mock import MagicMock

from dipt.agents.categorisation_agent import CategorisationAgent
from dipt.models.schemas import Category


def _make_agent() -> CategorisationAgent:
    """Construct an agent with mocked client and repository."""
    return CategorisationAgent(
        client=MagicMock(),
        repository=MagicMock(),
        model="test-model",
    )


def _category_lookup() -> dict[str, Category]:
    """Provide a small known-category lookup for parsing tests."""
    categories = [
        Category(id=1, name="Software Testing", description=""),
        Category(id=2, name="Empirical Software Engineering", description=""),
        Category(id=3, name="AI for Software Engineering (AI4SE)", description=""),
    ]
    return {cat.name.lower(): cat for cat in categories}


def test_parse_response_extracts_valid_categories() -> None:
    """Well-formed XML should yield validated assignments."""
    agent = _make_agent()
    response = """
    <categories>
      <category><name>Software Testing</name><confidence>0.95</confidence></category>
      <category><name>Empirical Software Engineering</name><confidence>0.80</confidence></category>
    </categories>
    """

    assignments = agent._parse_response(response, _category_lookup())

    assert len(assignments) == 2
    assert assignments[0].category_name == "Software Testing"
    assert assignments[0].confidence == 0.95


def test_parse_response_drops_low_confidence() -> None:
    """Assignments below the minimum confidence are discarded."""
    agent = _make_agent()
    response = """
    <categories>
      <category><name>Software Testing</name><confidence>0.30</confidence></category>
    </categories>
    """

    assignments = agent._parse_response(response, _category_lookup())
    assert assignments == []


def test_parse_response_ignores_unknown_category() -> None:
    """Categories not present in the lookup are ignored."""
    agent = _make_agent()
    response = """
    <categories>
      <category><name>Quantum Teleportation</name><confidence>0.99</confidence></category>
    </categories>
    """

    assignments = agent._parse_response(response, _category_lookup())
    assert assignments == []


def test_parse_response_partial_match_discounts_confidence() -> None:
    """A fuzzy name match should resolve but discount the confidence."""
    agent = _make_agent()
    response = """
    <categories>
      <category><name>AI for Software Engineering</name><confidence>1.00</confidence></category>
    </categories>
    """

    assignments = agent._parse_response(response, _category_lookup())

    assert len(assignments) == 1
    assert assignments[0].category_id == 3
    assert assignments[0].confidence < 1.0  # discounted by partial match


def test_parse_response_caps_at_max_categories() -> None:
    """No more than the maximum number of categories should be returned."""
    agent = _make_agent()
    response = """
    <categories>
      <category><name>Software Testing</name><confidence>0.95</confidence></category>
      <category><name>Empirical Software Engineering</name><confidence>0.90</confidence></category>
      <category><name>AI for Software Engineering (AI4SE)</name><confidence>0.85</confidence></category>
    </categories>
    """

    assignments = agent._parse_response(response, _category_lookup())
    assert len(assignments) <= 3


def test_parse_confidence_defaults_when_missing() -> None:
    """A block without a confidence value defaults to 0.5."""
    agent = _make_agent()
    assert agent._parse_confidence("<name>X</name>") == 0.5


def test_parse_confidence_handles_garbage() -> None:
    """A non-numeric confidence value defaults to 0.5."""
    agent = _make_agent()
    assert agent._parse_confidence("<confidence>high</confidence>") == 0.5
