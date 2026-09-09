"""Download Ollama models on demand.

The pipeline never requires an operator to run ``ollama pull`` by hand: when a
stage is pointed at a model that is not present, it is fetched automatically -
either by :class:`dipt.agents.llm_client.OllamaClient` right before the call,
or by the dashboard when the model is chosen. Re-pulling a tag also updates it
to the latest published version.
"""

from __future__ import annotations

import logging
from typing import Callable

import ollama

from dipt.exceptions import ModelPullError

logger = logging.getLogger(__name__)

#: Progress callback signature: ``(status, completed_bytes, total_bytes)``.
ProgressFn = Callable[[str, int, int], None]


def _client(base_url: str) -> ollama.Client:
    return ollama.Client(host=base_url)


def list_installed(base_url: str) -> list[str]:
    """Return the model tags currently downloaded, or ``[]`` if Ollama is down.

    Args:
        base_url: Ollama base URL.

    Returns:
        Sorted list of installed model tags.
    """
    try:
        resp = _client(base_url).list()
    except (ollama.ResponseError, ConnectionError, OSError, KeyError):
        return []
    models = resp.get("models", []) if isinstance(resp, dict) else []
    return sorted(
        m.get("name", "") for m in models if isinstance(m, dict) and m.get("name")
    )


def is_installed(base_url: str, tag: str) -> bool:
    """Return whether a model tag is already downloaded.

    A bare name without a ``:tag`` is treated as ``<name>:latest``, matching
    Ollama's own default.

    Args:
        base_url: Ollama base URL.
        tag: The model tag to check.

    Returns:
        ``True`` if the model is present locally.
    """
    tag = (tag or "").strip()
    if not tag:
        return False
    installed = set(list_installed(base_url))
    if tag in installed:
        return True
    if ":" not in tag and f"{tag}:latest" in installed:
        return True
    return False


def pull(base_url: str, tag: str, on_progress: ProgressFn | None = None) -> None:
    """Download (or update) a model, blocking until it is complete.

    Args:
        base_url: Ollama base URL.
        tag: The model tag to pull, e.g. ``"llama3.3"`` or ``"qwen2.5:32b"``.
        on_progress: Optional callback invoked with ``(status, completed,
            total)`` as layers download.

    Raises:
        ModelPullError: If the model cannot be found or downloaded.
    """
    tag = (tag or "").strip()
    if not tag:
        raise ModelPullError("No model name given")

    logger.info("Pulling model '%s' from the Ollama registry", tag)
    try:
        stream = _client(base_url).pull(tag, stream=True)
        last_status = ""
        for update in stream:
            status = str(update.get("status", "")) if isinstance(update, dict) else ""
            completed = int(update.get("completed", 0) or 0)
            total = int(update.get("total", 0) or 0)
            if status and status != last_status:
                logger.info("  pull '%s': %s", tag, status)
                last_status = status
            if on_progress is not None:
                on_progress(status, completed, total)
            if isinstance(update, dict) and update.get("error"):
                raise ModelPullError(f"{tag}: {update['error']}")
    except ollama.ResponseError as exc:
        raise ModelPullError(
            f"Could not download '{tag}': {exc}. Check the name and that this "
            "machine can reach ollama.com."
        ) from exc
    except (ConnectionError, OSError) as exc:
        raise ModelPullError(f"Could not reach Ollama to pull '{tag}': {exc}") from exc

    logger.info("Model '%s' is ready", tag)


def ensure(base_url: str, tag: str) -> bool:
    """Make sure a model is present, downloading it if necessary.

    Args:
        base_url: Ollama base URL.
        tag: The model tag.

    Returns:
        ``True`` if a download was performed, ``False`` if it was already there.

    Raises:
        ModelPullError: If the model is missing and cannot be downloaded.
    """
    if is_installed(base_url, tag):
        return False
    pull(base_url, tag)
    return True
