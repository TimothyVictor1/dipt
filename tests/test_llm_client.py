"""Unit tests for :mod:`dipt.agents.llm_client`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import ollama
import pytest

from dipt.agents.llm_client import OllamaClient
from dipt.exceptions import LLMRequestError


def _client(inner: MagicMock) -> OllamaClient:
    """Build an OllamaClient whose underlying ollama.Client is ``inner``."""
    with patch("dipt.agents.llm_client.ollama.Client", return_value=inner):
        return OllamaClient(host="http://x", base_backoff_seconds=0)


def test_chat_returns_stripped_content() -> None:
    inner = MagicMock()
    inner.chat.return_value = {"message": {"content": "  hello  "}}
    assert _client(inner).chat("m", "prompt") == "hello"


def test_chat_passes_max_tokens_and_json_mode() -> None:
    inner = MagicMock()
    inner.chat.return_value = {"message": {"content": "{}"}}
    _client(inner).chat("m", "p", max_tokens=321, json_mode=True)

    kwargs = inner.chat.call_args.kwargs
    assert kwargs["options"]["num_predict"] == 321
    assert kwargs["format"] == "json"


def test_chat_omits_num_predict_when_unset() -> None:
    inner = MagicMock()
    inner.chat.return_value = {"message": {"content": "x"}}
    _client(inner).chat("m", "p")

    kwargs = inner.chat.call_args.kwargs
    assert "num_predict" not in kwargs["options"]
    assert kwargs["format"] == ""


def test_chat_retries_then_succeeds() -> None:
    inner = MagicMock()
    inner.chat.side_effect = [
        ollama.ResponseError("busy"),
        {"message": {"content": "ok"}},
    ]
    assert _client(inner).chat("m", "p") == "ok"
    assert inner.chat.call_count == 2


def test_chat_raises_after_exhausting_retries() -> None:
    inner = MagicMock()
    inner.chat.side_effect = ollama.ResponseError("still busy")
    with pytest.raises(LLMRequestError):
        _client(inner).chat("m", "p")
    assert inner.chat.call_count == 3


def test_constructor_rejects_bad_retry_count() -> None:
    with pytest.raises(ValueError):
        OllamaClient(host="http://x", max_retries=0)
