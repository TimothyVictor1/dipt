"""Thin, resilient wrapper around the Ollama Python client.

Centralises LLM access so retry logic, timeouts, and error translation live in
one place rather than being duplicated across every agent.
"""

from __future__ import annotations

import logging
import time
from typing import Final

import ollama

from dipt.exceptions import LLMRequestError

logger = logging.getLogger(__name__)

_MAX_RETRIES: Final[int] = 3
_BASE_BACKOFF_SECONDS: Final[float] = 2.0


class OllamaClient:
    """Resilient client for local Ollama chat inference.

    Args:
        host: Base URL of the Ollama server.
        max_retries: Maximum inference attempts before giving up.
        base_backoff_seconds: Base delay for exponential backoff.
    """

    def __init__(
        self,
        host: str,
        max_retries: int = _MAX_RETRIES,
        base_backoff_seconds: float = _BASE_BACKOFF_SECONDS,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self._client = ollama.Client(host=host)
        self._max_retries = max_retries
        self._base_backoff = base_backoff_seconds

    def chat(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.1,
    ) -> str:
        """Run a single-turn chat completion with retries.

        Args:
            model: Ollama model tag (e.g. ``"llama3.1:70b"``).
            prompt: The user prompt.
            temperature: Sampling temperature; low values give stable output.

        Returns:
            The assistant message content, stripped of surrounding whitespace.

        Raises:
            LLMRequestError: If all retry attempts fail.
        """
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": temperature},
                )
                content = response["message"]["content"]
                return content.strip()
            except (ollama.ResponseError, KeyError, ConnectionError) as exc:
                last_error = exc
                logger.warning(
                    "LLM attempt %d/%d failed for model '%s': %s",
                    attempt,
                    self._max_retries,
                    model,
                    exc,
                )
                if attempt < self._max_retries:
                    backoff = self._base_backoff * (2 ** (attempt - 1))
                    time.sleep(backoff)

        logger.error("LLM exhausted all retries for model '%s'", model)
        raise LLMRequestError(
            f"LLM inference failed after {self._max_retries} attempts for "
            f"model '{model}'"
        ) from last_error
