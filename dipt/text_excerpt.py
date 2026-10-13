"""Build a representative excerpt from an extracted paper.

A research paper puts its contribution at both ends: the problem and the
method near the front, the results, discussion and conclusions near the back.
Taking a leading slice therefore reads the introduction and stops, which is
how a four-part summary ends up inferring "key findings" and "industrial
implications" from text that contains neither.

This module takes a slice from each end instead, so the sections that carry
the findings are always in the prompt. It deliberately does not try to parse
section headings: text extracted from a PDF has inconsistent, often corrupted
headings, and a parser that silently fails would be worse than a positional
split that always works.
"""

from __future__ import annotations

from typing import Final

#: Share of the budget given to the opening. The remainder goes to the end,
#: which is weighted more heavily because results and conclusions live there.
_HEAD_SHARE: Final[float] = 0.42

_ELLIPSIS: Final[str] = "\n\n[... middle of the paper omitted ...]\n\n"


def head_tail_excerpt(text: str, budget: int) -> str:
    """Return up to ``budget`` characters taken from both ends of ``text``.

    Args:
        text: The full extracted paper text.
        budget: Maximum characters to return, excluding the omission marker.

    Returns:
        The whole text when it already fits, otherwise an opening slice and a
        closing slice joined by a marker.

    Raises:
        ValueError: If ``budget`` is not positive.
    """
    if budget <= 0:
        raise ValueError("budget must be positive")

    text = text.strip()
    if len(text) <= budget:
        return text

    head_chars = int(budget * _HEAD_SHARE)
    tail_chars = budget - head_chars
    head = text[:head_chars].rstrip()
    tail = text[-tail_chars:].lstrip()
    return f"{head}{_ELLIPSIS}{tail}"


def coverage(text_length: int, budget: int) -> float:
    """Return the share of a paper an excerpt of ``budget`` covers, 0.0-1.0.

    Args:
        text_length: Length of the full text in characters.
        budget: The excerpt budget in characters.

    Returns:
        The covered fraction, capped at 1.0. Zero-length text counts as fully
        covered, since there is nothing left unread.
    """
    if text_length <= 0:
        return 1.0
    return min(1.0, budget / text_length)
