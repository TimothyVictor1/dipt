"""Unit tests for :mod:`dipt.agents.quality_gate`."""

from __future__ import annotations

from unittest.mock import MagicMock

from dipt.agents.quality_gate import QualityGate
from dipt.exceptions import LLMRequestError


def test_accepts_when_model_says_yes() -> None:
    """A 'YES' answer should classify the paper as SE."""
    client = MagicMock()
    client.chat.return_value = "YES"
    gate = QualityGate(client, model="test-model")

    assert gate.is_software_engineering("A study of refactoring", "abstract") is True
    client.chat.assert_called_once()


def test_rejects_when_model_says_no() -> None:
    """A 'NO' answer should classify the paper as non-SE."""
    client = MagicMock()
    client.chat.return_value = "NO"
    gate = QualityGate(client, model="test-model")

    assert gate.is_software_engineering("A study of soil", "abstract") is False


def test_rejects_blank_title_without_calling_llm() -> None:
    """A blank title is rejected before any LLM call is made."""
    client = MagicMock()
    gate = QualityGate(client, model="test-model")

    assert gate.is_software_engineering("   ", "abstract") is False
    client.chat.assert_not_called()


def test_fails_closed_on_llm_error() -> None:
    """If the LLM errors, the gate rejects (fail-closed)."""
    client = MagicMock()
    client.chat.side_effect = LLMRequestError("boom")
    gate = QualityGate(client, model="test-model")

    assert gate.is_software_engineering("Some SE paper", "abstract") is False


def test_case_insensitive_yes() -> None:
    """Answers should be matched case-insensitively."""
    client = MagicMock()
    client.chat.return_value = "yes, this is SE"
    gate = QualityGate(client, model="test-model")

    assert gate.is_software_engineering("Title", "abstract") is True
