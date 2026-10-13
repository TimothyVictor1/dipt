"""Unit tests for :mod:`dipt.text_excerpt`."""

from __future__ import annotations

import pytest

from dipt.text_excerpt import coverage, head_tail_excerpt


def test_short_text_is_returned_whole() -> None:
    """Text inside the budget comes back untouched."""
    assert head_tail_excerpt("a short paper", 100) == "a short paper"


def test_long_text_keeps_both_ends() -> None:
    """The excerpt must contain the start and the end of the paper.

    Regression test: taking a leading slice meant the model never saw the
    results or conclusions, so "key findings" was inferred from the
    introduction.
    """
    text = "START" + ("x" * 50_000) + "CONCLUSION"
    out = head_tail_excerpt(text, 1_000)

    assert out.startswith("START")
    assert out.endswith("CONCLUSION")
    assert "omitted" in out


def test_tail_gets_the_larger_share() -> None:
    """Results and conclusions live at the end, so the tail is weighted."""
    text = "H" * 10_000 + "T" * 10_000
    out = head_tail_excerpt(text, 1_000)
    assert out.count("T") > out.count("H")


def test_budget_is_respected() -> None:
    """Only the omission marker may push the result past the budget."""
    text = "y" * 80_000
    budget = 2_000
    out = head_tail_excerpt(text, budget)
    assert len(out.replace("[... middle of the paper omitted ...]", "")) <= budget + 10


def test_non_positive_budget_is_rejected() -> None:
    """A zero or negative budget is a programming error, not a silent no-op."""
    with pytest.raises(ValueError, match="budget must be positive"):
        head_tail_excerpt("some text", 0)


@pytest.mark.parametrize(
    ("length", "budget", "expected"),
    [(0, 100, 1.0), (50, 100, 1.0), (1_000, 250, 0.25), (24_000, 24_000, 1.0)],
)
def test_coverage_reports_the_read_fraction(
    length: int, budget: int, expected: float
) -> None:
    """Coverage is the share of a paper the excerpt actually reads."""
    assert coverage(length, budget) == pytest.approx(expected)
