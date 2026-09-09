"""Thin, resilient wrapper around the Ollama Python client.

Centralises LLM access so retry logic, timeouts, error translation, and
on-demand model downloads live in one place rather than being duplicated
across every agent.
"""

from __future__ import annotations

import logging
import time
from typing import Final

import ollama

from dipt import model_pull
from dipt.exceptions import LLMRequestError, ModelPullError

logger = logging.getLogger(__name__)

_MAX_RETRIES: Final[int] = 3
_BASE_BACKOFF_SECONDS: Final[float] = 2.0
_MISSING_MODEL_HINTS: Final[tuple[str, ...]] = (
    "not found",
    "try pulling it",
    "no such model",
    "pull the model",
)


class OllamaClient:
    """Resilient client for local Ollama chat inference.

    Args:
        host: Base URL of the Ollama server.
        max_retries: Maximum inference attempts before giving up.
        base_backoff_seconds: Base delay for exponential backoff.
        auto_pull: When ``True`` (default), a chat that fails because the model
            is not downloaded triggers a one-time ``ollama pull`` of that model,
            then the call is retried.
    """

    def __init__(
        self,
        host: str,
        max_retries: int = _MAX_RETRIES,
        base_backoff_seconds: float = _BASE_BACKOFF_SECONDS,
        auto_pull: bool = True,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self._host = host
        self._client = ollama.Client(host=host)
        self._max_retries = max_retries
        self._base_backoff = base_backoff_seconds
        self._auto_pull = auto_pull
        self._pulled: set[str] = set()

    def chat(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        """Run a single-turn chat completion with retries.

        Args:
            model: Ollama model tag (e.g. ``"llama3.1:70b"``). If it is not
                downloaded and ``auto_pull`` is on, it is fetched first.
            prompt: The user prompt.
            temperature: Sampling temperature; low values give stable output.
            max_tokens: Optional hard cap on generated tokens (``num_predict``).
                Left unset, generation runs until the model emits a stop token,
                which a verbose model may never do; callers with a bounded
                expected answer should pass a cap.
            json_mode: When ``True``, ask Ollama to constrain the output to
                valid JSON (``format="json"``). The prompt must still describe
                the JSON shape expected.

        Returns:
            The assistant message content, stripped of surrounding whitespace.

        Raises:
            LLMRequestError: If all retry attempts fail.
        """
        last_error: Exception | None = None
        options: dict[str, float | int] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        fmt = "json" if json_mode else ""

        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    options=options,
                    format=fmt,
                )
                content = response["message"]["content"]
                return content.strip()
            except (ollama.ResponseError, KeyError, ConnectionError) as exc:
                last_error = exc
                if self._try_pull_missing(model, exc):
                    # Downloaded the model just now - retry immediately without
                    # counting this as a failed attempt.
                    continue
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

    def _try_pull_missing(self, model: str, exc: Exception) -> bool:
        """Pull ``model`` if ``exc`` means it is not downloaded yet.

        Args:
            model: The model tag the failed call used.
            exc: The exception the Ollama client raised.

        Returns:
            ``True`` if a pull was performed (so the caller should retry),
            ``False`` otherwise.
        """
        if not self._auto_pull or model in self._pulled:
            return False
        message = str(exc).lower()
        if not any(hint in message for hint in _MISSING_MODEL_HINTS):
            return False

        self._pulled.add(model)
        logger.info(
            "Model '%s' is not downloaded; fetching it now (first use)", model
        )
        try:
            model_pull.pull(self._host, model)
        except ModelPullError:
            logger.exception("Automatic download of model '%s' failed", model)
            return False
        return True
